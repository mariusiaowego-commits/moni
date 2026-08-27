"""moni.stats.repository — W1 量化看板 SQL 聚合层。

v1.0.19: moni-B §四.7 路线图 W1 申报包装。
聚合 7 张表 (articles / cron_runs / llm_jobs / share_tasks / article_pins / events / cases)。
所有聚合函数返回 dict, 包含 raw_count + display 字段 (供前端直接渲染)。

数据稀疏应对 (MOA 拍板 Q6+补丁 2026-08-09):
- 默认阈值 N_DEFAULT_THRESHOLD = 10
- 抽取层 (events/cases) 阈值 N_EXTRACT_THRESHOLD = 5
- N < 阈值 → display_status = "数据积累中", 不展示百分比/节省
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db.connection import get_db

logger = logging.getLogger(__name__)

# ── MOA 拍板常量 ────────────────────────────────────────────────
N_DEFAULT_THRESHOLD = 10  # 抓取/LLM/使用/效率模块阈值
N_EXTRACT_THRESHOLD = 5   # 抽取层 events/cases 阈值 (避免稀疏期两模块同时砍掉)


# ── 工具函数 ─────────────────────────────────────────────────────

def _period_start(period: str) -> datetime | None:
    """根据 period (7d/30d/90d/all) 返回起始时间 (UTC).

    Args:
        period: '7d' / '30d' / '90d' / 'all' (其他值 fallback 30d)

    Returns:
        UTC datetime, or None when period='all' (no time filter)
    """
    if period == "all":
        return None
    now = datetime.now(timezone.utc)
    days = {"7d": 7, "30d": 30, "90d": 90}.get(period, 30)
    return now - timedelta(days=days)


def _period_iso(period: str) -> str | None:
    start = _period_start(period)
    return start.isoformat() if start is not None else None


def _sparse_response(count: int, threshold: int, *, raw: dict | None = None) -> dict[str, Any]:
    """N < 阈值时返回稀疏响应 (不展示百分比/节省).

    Args:
        count: 实际样本数
        threshold: 阈值
        raw: 真实聚合数据 (展示给运营, 不展示给评委)
    """
    return {
        "count": count,
        "threshold": threshold,
        "display_status": "数据积累中" if count < threshold else "ok",
        "raw": raw or {},
    }


# ── 抓取层 (cron_runs) ──────────────────────────────────────────

def fetch_layer_stats(period: str = "30d") -> dict[str, Any]:
    """抓取层 5 数字: cron_total / cron_success / new_articles / by_source / success_rate.

    Schema: cron_runs.triggered_at, cron_runs.finished_at, cron_runs.success, cron_runs.new_articles,
            cron_runs.sources_run, cron_runs.error.
    """
    conn = get_db()
    since = _period_iso(period)

    # 总览
    row = conn.execute(
        """
        SELECT COUNT(*) AS cron_total,
               SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS cron_success,
               SUM(new_articles) AS new_articles_total
        FROM cron_runs
        WHERE triggered_at >= ?
        """,
        (since,),
    ).fetchone()

    cron_total = row["cron_total"] or 0
    cron_success = row["cron_success"] or 0
    new_total = row["new_articles_total"] or 0
    success_rate = (cron_success / cron_total) if cron_total > 0 else 0.0

    # 按源拆分
    by_source_rows = conn.execute(
        """
        SELECT s.name AS source_name,
               COUNT(cr.id) AS cron_n,
               SUM(CASE WHEN cr.success=1 THEN 1 ELSE 0 END) AS cron_success_n,
               COALESCE(SUM(cr.new_articles), 0) AS new_n
        FROM cron_runs cr
        LEFT JOIN sources s ON cr.sources_run LIKE '%' || s.name || '%'
        WHERE cr.triggered_at >= ?
        GROUP BY s.name
        ORDER BY cron_n DESC
        LIMIT 20
        """,
        (since,),
    ).fetchall()

    by_source = [
        {
            "source": r["source_name"] or "(unknown)",
            "cron_count": r["cron_n"] or 0,
            "success_count": r["cron_success_n"] or 0,
            "new_articles": r["new_n"] or 0,
        }
        for r in by_source_rows
    ]

    raw = {
        "cron_total": cron_total,
        "cron_success": cron_success,
        "new_articles_total": new_total,
        "success_rate": round(success_rate, 4),
        "by_source": by_source,
    }

    return {
        "period": period,
        "module": "fetch",
        **_sparse_response(cron_total, N_DEFAULT_THRESHOLD, raw=raw),
    }


# ── LLM 层 (llm_jobs) ───────────────────────────────────────────

def llm_layer_stats(period: str = "30d") -> dict[str, Any]:
    """LLM 层 6 数字: task_count / success_count / retry_count / avg_duration_ms / by_task_type / success_rate.

    Schema: llm_jobs.task_type, llm_jobs.status, llm_jobs.duration_ms, llm_jobs.attempts,
            llm_jobs.started_at.
    task_type 枚举 (实际数据): summary / fact_check / event_extract / case_extract / predict_tags.
    """
    conn = get_db()
    since = _period_iso(period)

    # 总览
    row = conn.execute(
        """
        SELECT COUNT(*) AS task_total,
               SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS task_success,
               SUM(CASE WHEN attempts > 1 THEN 1 ELSE 0 END) AS task_retry,
               AVG(duration_ms) AS avg_duration_ms
        FROM llm_jobs
        WHERE started_at >= ?
        """,
        (since,),
    ).fetchone()

    task_total = row["task_total"] or 0
    task_success = row["task_success"] or 0
    task_retry = row["task_retry"] or 0
    avg_duration = row["avg_duration_ms"] or 0
    success_rate = (task_success / task_total) if task_total > 0 else 0.0

    # 按 task_type 拆分
    by_type_rows = conn.execute(
        """
        SELECT task_type,
               COUNT(*) AS n,
               SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS success_n,
               AVG(duration_ms) AS avg_ms
        FROM llm_jobs
        WHERE started_at >= ?
        GROUP BY task_type
        ORDER BY n DESC
        """,
        (since,),
    ).fetchall()

    by_task_type = [
        {
            "task_type": r["task_type"],
            "count": r["n"] or 0,
            "success_count": r["success_n"] or 0,
            "avg_duration_ms": round(r["avg_ms"] or 0, 1),
        }
        for r in by_type_rows
    ]

    raw = {
        "task_total": task_total,
        "task_success": task_success,
        "task_retry": task_retry,
        "avg_duration_ms": round(avg_duration, 1),
        "success_rate": round(success_rate, 4),
        "by_task_type": by_task_type,
    }

    return {
        "period": period,
        "module": "llm",
        **_sparse_response(task_total, N_DEFAULT_THRESHOLD, raw=raw),
    }


# ── LLM 用量 (llm_jobs token/cost, v1.0.32) ───────────────────

def llm_usage_layer_stats(period: str = "30d") -> dict[str, Any]:
    """Q4 LLM 用量统计: 按 model 聚合 token/cost。

    Schema: llm_jobs.model, llm_jobs.started_at, llm_jobs.input_tokens,
            llm_jobs.output_tokens, llm_jobs.cost_usd (migration 029).

    Returns:
        {
          period, period_start,
          totals: {calls, input_tokens, output_tokens, cost_usd},
          by_model: [{model, calls, input_tokens, output_tokens, cost_usd}, ...],
          module: 'llm_usage',
        }

    period: '7d' / '30d' / '90d' (其他值 fallback 30d).
    """
    conn = get_db()
    since = _period_iso(period)

    if since:
        where = "WHERE started_at >= ?"
        params: tuple = (since,)
    else:
        where = ""
        params = ()

    totals_row = conn.execute(
        f"""
        SELECT COUNT(*) AS calls,
               COALESCE(SUM(input_tokens), 0)  AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(cost_usd), 0)      AS cost_usd
        FROM llm_jobs
        {where}
        """,
        params,
    ).fetchone()

    by_model_rows = conn.execute(
        f"""
        SELECT COALESCE(model, '<unknown>') AS model,
               COUNT(*) AS calls,
               COALESCE(SUM(input_tokens), 0)  AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(cost_usd), 0)      AS cost_usd
        FROM llm_jobs
        {where}
        GROUP BY COALESCE(model, '<unknown>')
        ORDER BY calls DESC
        """,
        params,
    ).fetchall()

    totals = {
        "calls": int(totals_row["calls"] or 0),
        "input_tokens": int(totals_row["input_tokens"] or 0),
        "output_tokens": int(totals_row["output_tokens"] or 0),
        "cost_usd": round(float(totals_row["cost_usd"] or 0.0), 6),
    }
    by_model = [
        {
            "model": r["model"],
            "calls": int(r["calls"] or 0),
            "input_tokens": int(r["input_tokens"] or 0),
            "output_tokens": int(r["output_tokens"] or 0),
            "cost_usd": round(float(r["cost_usd"] or 0.0), 6),
        }
        for r in by_model_rows
    ]

    return {
        "period": period,
        "period_start": since,
        "totals": totals,
        "by_model": by_model,
        "module": "llm_usage",
    }


# ── 抽取层 (events / cases) — N_EXTRACT_THRESHOLD ──────────────

def extract_layer_stats(period: str = "30d") -> dict[str, Any]:
    """抽取层 4 数字: events_total / cases_total / events_pending / cases_pending.

    Schema: events.extracted_at, events.review_status; cases.extracted_at, cases.review_status.
    走 N_EXTRACT_THRESHOLD (5) 避免 cases=1/events=9 同时砍掉 (DA 关键贡献 2026-08-09)。
    """
    conn = get_db()
    since = _period_iso(period)

    events_total = conn.execute(
        "SELECT COUNT(*) FROM events WHERE COALESCE(extracted_at, created_at) >= ?",
        (since,),
    ).fetchone()[0] or 0
    cases_total = conn.execute(
        "SELECT COUNT(*) FROM cases WHERE COALESCE(extracted_at, created_at) >= ?",
        (since,),
    ).fetchone()[0] or 0
    events_pending = conn.execute(
        "SELECT COUNT(*) FROM events WHERE COALESCE(extracted_at, created_at) >= ? AND review_status='pending'",
        (since,),
    ).fetchone()[0] or 0
    cases_pending = conn.execute(
        "SELECT COUNT(*) FROM cases WHERE COALESCE(extracted_at, created_at) >= ? AND review_status='pending'",
        (since,),
    ).fetchone()[0] or 0

    raw = {
        "events_total": events_total,
        "cases_total": cases_total,
        "events_pending": events_pending,
        "cases_pending": cases_pending,
        "events_display": _sparse_response(events_total, N_EXTRACT_THRESHOLD)["display_status"],
        "cases_display": _sparse_response(cases_total, N_EXTRACT_THRESHOLD)["display_status"],
    }

    # 模块整体阈值 = 两子模块中较小者, 单独展示给前端
    min_count = min(events_total, cases_total)
    return {
        "period": period,
        "module": "extract",
        "count": min_count,
        "threshold": N_EXTRACT_THRESHOLD,
        "display_status": "数据积累中" if min_count < N_EXTRACT_THRESHOLD else "ok",
        "raw": raw,
    }


# ── 使用层 (article_pins / share_tasks) ────────────────────────

def usage_layer_stats(period: str = "30d") -> dict[str, Any]:
    """使用层 4 数字: pin_count / share_count / brief_count / brief_format_breakdown.

    Schema: article_pins.pinned_at; share_tasks.created_at, share_tasks.format, share_tasks.status.
    """
    conn = get_db()
    since = _period_iso(period)

    pin_count = conn.execute(
        "SELECT COUNT(*) FROM article_pins WHERE pinned_at >= ?", (since,)
    ).fetchone()[0] or 0

    share_count = conn.execute(
        "SELECT COUNT(*) FROM share_tasks WHERE created_at >= ?", (since,)
    ).fetchone()[0] or 0

    brief_rows = conn.execute(
        """
        SELECT format, COUNT(*) AS n
        FROM share_tasks
        WHERE created_at >= ? AND status='done'
        GROUP BY format
        """,
        (since,),
    ).fetchall()
    format_breakdown = {r["format"]: r["n"] or 0 for r in brief_rows}

    raw = {
        "pin_count": pin_count,
        "share_count": share_count,
        "brief_count": sum(format_breakdown.values()),
        "brief_format_breakdown": format_breakdown,
    }

    return {
        "period": period,
        "module": "usage",
        **_sparse_response(pin_count + share_count, N_DEFAULT_THRESHOLD, raw=raw),
    }


# ── 事实核查 method 分布 (v1.0.25) ───────────────────────────────

def fact_check_method_layer_stats(period: str = "30d") -> dict[str, Any]:
    """聚合 articles.fact_check_method × 时间窗 (v1.0.25).

    Args:
        period: '7d' / '30d' / '90d' / 'all'

    Returns:
        {
          period, period_start, total_articles_with_fact_check,
          by_method: [{method, article_count, percentage}, ...],
          by_state:  [{state, article_count, percentage}, ...]  # 5 档全有
          unfact_checked_total, module
        }

    Notes:
        - 仅统计 has_fact_check=1 的文章 (饼图内)
        - NULL method → 标为 '<NULL>'
        - unfact_checked_total = has_fact_check=0 全库 (不限 period, 供对照)
    """
    conn = get_db()
    period_start = _period_iso(period)  # None when period='all'

    if period_start:
        rows = conn.execute(
            """
            SELECT
                COALESCE(fact_check_method, '<NULL>') AS method,
                COUNT(*) AS article_count
            FROM articles
            WHERE has_fact_check = 1
              AND fact_checked_at IS NOT NULL
              AND fact_checked_at >= ?
            GROUP BY fact_check_method
            ORDER BY article_count DESC
            """,
            (period_start,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT
                COALESCE(fact_check_method, '<NULL>') AS method,
                COUNT(*) AS article_count
            FROM articles
            WHERE has_fact_check = 1
            GROUP BY fact_check_method
            ORDER BY article_count DESC
            """
        ).fetchall()

    total = sum(int(r["article_count"] or 0) for r in rows)
    by_method = [
        {
            "method": r["method"],
            "article_count": int(r["article_count"] or 0),
            "percentage": (
                round(100.0 * int(r["article_count"] or 0) / total, 1) if total else 0.0
            ),
        }
        for r in rows
    ]

    unfact = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE COALESCE(has_fact_check, 0) = 0"
    ).fetchone()[0] or 0

    _STATES = ("draft", "verified", "published", "deprecated", "archived")
    counts: dict[str, int] = {s: 0 for s in _STATES}
    try:
        if period_start:
            state_rows = conn.execute(
                """
                SELECT
                    COALESCE(fact_check_state, 'draft') AS state,
                    COUNT(*) AS article_count
                FROM articles
                WHERE has_fact_check = 1
                  AND fact_checked_at IS NOT NULL
                  AND fact_checked_at >= ?
                GROUP BY fact_check_state
                """,
                (period_start,),
            ).fetchall()
        else:
            state_rows = conn.execute(
                """
                SELECT
                    COALESCE(fact_check_state, 'draft') AS state,
                    COUNT(*) AS article_count
                FROM articles
                WHERE has_fact_check = 1
                GROUP BY fact_check_state
                """
            ).fetchall()
        for r in state_rows:
            key = r["state"] if r["state"] in counts else r["state"]
            counts[key] = int(r["article_count"] or 0)
    except Exception:
        # Pre-migration 028: fact_check_state column may be missing.
        pass

    state_total = sum(counts.get(s, 0) for s in _STATES)
    by_state = [
        {
            "state": s,
            "article_count": counts.get(s, 0),
            "percentage": (
                round(100.0 * counts.get(s, 0) / state_total, 1) if state_total else 0.0
            ),
        }
        for s in _STATES
    ]

    return {
        "period": period,
        "period_start": period_start,
        "total_articles_with_fact_check": total,
        "by_method": by_method,
        "by_state": by_state,
        "unfact_checked_total": int(unfact),
        "module": "fact_check_method",
    }


# ── 时间序列 (按 metric + period + granularity) ────────────────

# metric → SQL 配置
_TIMESERIES_QUERIES: dict[str, dict[str, str]] = {
    "fetch": {
        "time_col": "triggered_at",
        "table": "cron_runs",
        "count_expr": "COUNT(*)",
        "extra_col": "SUM(CASE WHEN success=1 THEN 1 ELSE 0 END)",
    },
    "llm": {
        "time_col": "started_at",
        "table": "llm_jobs",
        "count_expr": "COUNT(*)",
        "extra_col": "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END)",
    },
    "events": {
        "time_col": "extracted_at",
        "table": "events",
        "count_expr": "COUNT(*)",
        "extra_col": "SUM(CASE WHEN review_status='confirmed' THEN 1 ELSE 0 END)",
    },
    "cases": {
        "time_col": "extracted_at",
        "table": "cases",
        "count_expr": "COUNT(*)",
        "extra_col": "SUM(CASE WHEN review_status='confirmed' THEN 1 ELSE 0 END)",
    },
    "pins": {
        "time_col": "pinned_at",
        "table": "article_pins",
        "count_expr": "COUNT(*)",
        "extra_col": "0",
    },
    "share": {
        "time_col": "created_at",
        "table": "share_tasks",
        "count_expr": "COUNT(*)",
        "extra_col": "SUM(CASE WHEN status='done' THEN 1 ELSE 0 END)",
    },
}


def _granularity_format(granularity: str) -> str:
    """SQLite strftime format by granularity."""
    return {"daily": "%Y-%m-%d", "weekly": "%Y-W%W", "monthly": "%Y-%m"}.get(
        granularity, "%Y-%m-%d"
    )


def timeseries(
    metric: str,
    period: str = "30d",
    granularity: str = "daily",
) -> dict[str, Any]:
    """时间序列聚合 (供前端 SVG 趋势图).

    Args:
        metric: 'fetch' / 'llm' / 'events' / 'cases' / 'pins' / 'share'
        period: '7d' / '30d' / '90d'
        granularity: 'daily' / 'weekly' / 'monthly'

    Returns:
        {metric, period, granularity, points: [{bucket, count, success_n}], display_status}
    """
    if metric not in _TIMESERIES_QUERIES:
        return {
            "metric": metric,
            "period": period,
            "granularity": granularity,
            "points": [],
            "display_status": "invalid_metric",
        }

    q = _TIMESERIES_QUERIES[metric]
    fmt = _granularity_format(granularity)
    since = _period_iso(period)

    rows = get_db().execute(
        f"""
        SELECT strftime(?, {q['time_col']}) AS bucket,
               {q['count_expr']} AS cnt,
               {q['extra_col']} AS extra
        FROM {q['table']}
        WHERE {q['time_col']} >= ?
        GROUP BY bucket
        ORDER BY bucket ASC
        """,
        (fmt, since),
    ).fetchall()

    points = [
        {"bucket": r["bucket"], "count": r["cnt"] or 0, "success_count": r["extra"] or 0}
        for r in rows
    ]
    total = sum(p["count"] for p in points)

    threshold = N_EXTRACT_THRESHOLD if metric in ("events", "cases") else N_DEFAULT_THRESHOLD
    return {
        "metric": metric,
        "period": period,
        "granularity": granularity,
        "points": points,
        "total": total,
        "threshold": threshold,
        "display_status": "数据积累中" if total < threshold else "ok",
    }


# ── 顶部三卡 (基线/当前/目标) + 节省时间 ────────────────────────

def overview(period: str = "30d") -> dict[str, Any]:
    """顶部 3 卡 + 6 类数字汇总 (供 /api/stats/overview).

    三卡定义 (moni-B §五 评分标准):
    - baseline: 历史基线 (本实现: 当前期间上一周期对比)
    - current: 当前期间真实数据
    - target: 目标值 (本实现: 配置 savings_baseline.yaml 里 target)

    Returns:
        {period, cards: {baseline, current, target}, modules: {fetch, llm, extract, usage, efficiency}}
    """
    from .savings import compute_savings  # 延迟导入避免循环

    fetch = fetch_layer_stats(period)
    llm = llm_layer_stats(period)
    extract = extract_layer_stats(period)
    usage = usage_layer_stats(period)
    savings = compute_savings(period)

    # 当前 vs 基线 (上一周期: 7d→前7d, 30d→前30d, 90d→前90d)
    prev_period = {"7d": "7d", "30d": "30d", "90d": "90d"}.get(period, "30d")  # 同长度
    fetch_prev = fetch_layer_stats(prev_period)["raw"]
    llm_prev = llm_layer_stats(prev_period)["raw"]

    baseline = {
        "fetch_total": fetch_prev.get("cron_total", 0),
        "llm_total": llm_prev.get("task_total", 0),
        "new_articles": fetch_prev.get("new_articles_total", 0),
    }
    current = {
        "fetch_total": fetch["raw"].get("cron_total", 0),
        "llm_total": llm["raw"].get("task_total", 0),
        "new_articles": fetch["raw"].get("new_articles_total", 0),
    }
    # 目标 = 当前 × 1.2 (保守 +20%) — 由 yaml 配置覆盖 (savings_baseline.yaml target 段)
    target = {
        "fetch_total": int(current["fetch_total"] * 1.2) + 1,
        "llm_total": int(current["llm_total"] * 1.2) + 1,
        "new_articles": int(current["new_articles"] * 1.2) + 1,
    }

    return {
        "period": period,
        "cards": {"baseline": baseline, "current": current, "target": target},
        "modules": {
            "fetch": fetch,
            "llm": llm,
            "extract": extract,
            "usage": usage,
            "efficiency": savings,
        },
    }


# ── 成果指标 (竞赛申报) ────────────────────────────────────────────────
# v1.0.35.19: 领导要求的成果数据 (追踪同业数/高价值案例/同业分布/榜单)。

# 拆分算法独立到 .split_institutions (2026-08-26 dad review: 让 repository
# 保持聚合职责, 不与 normalize 耦合). 测试仍可从 repository 导入 (重导出).
from .split_institutions import (
    split_institutions as _split_institutions,
)


def distinct_institutions_only(rows: list[tuple[str | None, str | None]]) -> list[str]:
    """保持向后兼容的薄包装 — 直接走 split_institutions 模块的 distinct_institutions."""
    from .split_institutions import distinct_institutions
    return distinct_institutions(rows)


def _institution_distribution(rows: list[tuple[str | None, str | None]], top_n: int = 5) -> list[dict[str, Any]]:
    """保留供独立调用 (不计入 cases_dashboard, 但 dashboard_stats 现已不用).

    dad 8-26 review: 同业分布"没意义", 删除.
    保留函数以兼容可能存在的外部调用, 但内部 dashboard 已切换 distinct_institutions.
    """
    from collections import Counter
    from .split_institutions import distinct_institutions

    counter: Counter[str] = Counter()
    for subject, counterparty in rows:
        for name in _split_institutions(subject):
            counter[name] += 1
        for name in _split_institutions(counterparty):
            counter[name] += 1

    distinct = distinct_institutions(rows)
    top_names = set(distinct[:top_n])
    top_dist = [
        {"name": name, "count": counter[name], "pct": round(counter[name] / max(counter.total(), 1) * 100, 1)}
        for name in distinct[:top_n]
    ]
    others = sum(c for n, c in counter.items() if n not in top_names)
    if others > 0:
        top_dist.append({"name": "其他", "count": others, "pct": round(others / max(counter.total(), 1) * 100, 1)})
    return top_dist


def cases_dashboard_stats(period: str = "all") -> dict[str, Any]:
    """竞赛成果指标 (领导口径, 申报书 §五 数据支撑).

    period: 'all' (默认, 累计) / '7d' / '30d' / '90d'。
    成果数字是累计口径, 前端成果模块默认 all, 不受 toolbar 7/30/90 影响
    (grok F7)。排除 __processing__ 占位行。

    Returns:
        {period, total_cases, total_institutions, high_value_cases,
         verified_cases, published_cases, pending_review,
         institution_distribution (TOP5+其他), high_value_ranking,
         action_distribution, by_type}
    """
    conn = get_db()
    where = "subject != '__processing__'"
    start: str | None = None
    if period != "all":
        start = _period_iso(period)
        if start:
            where += " AND COALESCE(extracted_at, created_at) >= ?"

    params: tuple[Any, ...] = (start,) if start else ()

    total_cases = conn.execute(
        f"SELECT COUNT(*) FROM cases WHERE {where}", params
    ).fetchone()[0]
    high_value_cases = conn.execute(
        f"SELECT COUNT(*) FROM cases WHERE {where} AND reference_value = 'high'", params
    ).fetchone()[0]
    verified_cases = conn.execute(
        f"SELECT COUNT(*) FROM cases WHERE {where} AND review_status = 'confirmed'", params
    ).fetchone()[0]
    published_cases = conn.execute(
        f"SELECT COUNT(*) FROM cases WHERE {where} AND case_lifecycle = 'published'", params
    ).fetchone()[0]
    pending_review = conn.execute(
        f"SELECT COUNT(*) FROM cases WHERE {where} AND review_status = 'pending'", params
    ).fetchone()[0]

    # 同业机构数: 走"主体+对手方拆分后 DISTINCT" 算法 (agy 评审盲点1 + grok F4 共识).
    # SQL 拆分方案 (dad 拍板 8-26), 不建 case_institutions 关联表. v2 方案附录A 留演进.
    from moni.stats.split_institutions import distinct_institutions
    inst_rows = conn.execute(
        f"SELECT subject, counterparty FROM cases WHERE {where}", params
    ).fetchall()
    distinct_insts = distinct_institutions([(r["subject"], r["counterparty"]) for r in inst_rows])
    total_institutions = len(distinct_insts)

    display_label = "待核验" if pending_review > 0 else "已核验"

    # 高价值案例榜单 (TOP10, 加 summary/article_title/url 让前端能渲染内容预览;
    # 排序: 高价值优先 → 置信度高 → 最新 (dad 8-26 review: extracted_at desc 默认
    # 等同"最新"没意义, 必须按 reference_value + confidence 重排)
    # v1.0.37: keywords (case_keywords) + 主地理 (case_geography is_primary)
    rank_sql = """
        SELECT c.case_id, c.subject, c.counterparty, c.case_action,
               c.reference_value, c.extraction_confidence, c.extracted_at,
               c.summary, c.evidence_snippet,
               a.title AS article_title, a.url AS article_url,
               a.source AS article_source,
               (SELECT GROUP_CONCAT(at.tag_name, ',')
                FROM article_tags at WHERE at.article_id = c.article_id) AS tags,
               (SELECT json_group_array(ck.keyword)
                FROM case_keywords ck WHERE ck.case_id = c.case_id) AS keywords,
               g.country_code, g.country_name, g.region
        FROM cases c
        JOIN articles a ON a.id = c.article_id
        LEFT JOIN case_geography g ON g.id = (
            SELECT g2.id FROM case_geography g2
            WHERE g2.case_id = c.case_id
            ORDER BY g2.is_primary DESC, g2.id
            LIMIT 1
        )
        WHERE c.subject != '__processing__' AND c.reference_value = 'high'
        ORDER BY
            CASE c.reference_value WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
            c.extraction_confidence DESC,
            c.extracted_at DESC
        LIMIT 10
    """
    high_value_ranking = []
    for r in conn.execute(rank_sql).fetchall():
        row = dict(r)
        row["keywords"] = _parse_keyword_list(row.get("keywords"))
        high_value_ranking.append(row)

    keyword_options = [
        str(r[0]) for r in conn.execute(
            "SELECT DISTINCT keyword FROM case_keywords "
            "WHERE keyword IS NOT NULL AND keyword != '' "
            "ORDER BY keyword"
        ).fetchall()
    ]

    return {
        "period": period,
        "display_label": display_label,
        "total_cases": total_cases,
        "total_institutions": total_institutions,
        "high_value_cases": high_value_cases,
        "verified_cases": verified_cases,
        "published_cases": published_cases,
        "pending_review": pending_review,
        "fetch_total": _fetch_total(),
        "high_value_ranking": high_value_ranking,
        "keyword_options": keyword_options,
        "geo_distribution": _geo_distribution(conn),
    }


def _parse_keyword_list(raw: Any) -> list[str]:
    """json_group_array / 逗号串 → 去空 trim 列表."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw).strip()
    if not s:
        return []
    if s[0] == "[":
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [p.strip() for p in s.split(",") if p.strip()]


def _geo_distribution(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """案例地理聚合: by_region + by_country (COUNT DISTINCT case_id)."""
    by_region = [
        {"region": r["region"], "count": int(r["count"])}
        for r in conn.execute(
            "SELECT region, COUNT(DISTINCT case_id) AS count "
            "FROM case_geography GROUP BY region ORDER BY count DESC, region"
        ).fetchall()
    ]
    by_country = [
        {
            "country_code": r["country_code"],
            "country_name": r["country_name"],
            "region": r["region"],
            "count": int(r["count"]),
        }
        for r in conn.execute(
            "SELECT country_code, country_name, region, "
            "COUNT(DISTINCT case_id) AS count "
            "FROM case_geography GROUP BY country_code "
            "ORDER BY count DESC, country_code"
        ).fetchall()
    ]
    return {"by_region": by_region, "by_country": by_country}


def row_hits_excluded(row: dict[str, Any], excluded: dict[str, Any] | None) -> bool:
    """双维精确反选: 关键词 Set / country_code / region 任一命中 → True.

    与前端 stats.js _rowHitsExcluded 合同一口径 (sprint-19 PR #136).
    关键词拆分 trim 后大小写不敏感精确匹配, 不做 substring.
    """
    if not excluded:
        return False
    keywords = excluded.get("keywords") or []
    countries = excluded.get("countries") or []
    regions = excluded.get("regions") or []
    if not keywords and not countries and not regions:
        return False

    kws = {k.lower() for k in _parse_keyword_list(row.get("keywords"))}
    if any(str(e).strip().lower() in kws for e in keywords if str(e).strip()):
        return True

    code = str(row.get("country_code") or "").strip().upper()
    if code and any(str(c).strip().upper() == code for c in countries):
        return True

    region = str(row.get("region") or "").strip().upper()
    if region and any(str(r).strip().upper() == region for r in regions):
        return True
    return False


def _fetch_total() -> int:
    """累计抓取文章数 (cron_runs.new_articles 全量求和, 与 period 解耦)."""
    conn = get_db()
    row = conn.execute("SELECT COALESCE(SUM(new_articles), 0) FROM cron_runs").fetchone()
    return int(row[0] or 0)


def data_funnel_stats() -> dict[str, Any]:
    """数据漏斗 4 数字链路 (dad 8-26 review): 累计入库 → 抽取事件 → 案例 → 简报.

    不用漏斗图形, 只展示数据 (better-layout 评审共识: 数字靠空间分组不靠分隔线).
    各数字全量累计, 不绑 period, 与成果指标一致 (默认 all 累计口径).

    Returns:
        {articles_total, events_total, cases_total, share_tasks_done}
    """
    conn = get_db()
    return {
        "articles_total": conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
        "events_total": conn.execute(
            "SELECT COUNT(*) FROM events WHERE subject != '__processing__'"
        ).fetchone()[0],
        "cases_total": conn.execute(
            "SELECT COUNT(*) FROM cases WHERE subject != '__processing__'"
        ).fetchone()[0],
        "share_tasks_done": conn.execute(
            "SELECT COUNT(*) FROM share_tasks WHERE status = 'done'"
        ).fetchone()[0],
    }
