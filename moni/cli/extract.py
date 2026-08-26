"""moni.cli.extract — 批量提取案例/事件 CLI (v1.0.35.19).

竞赛成果数据支撑 (P1): 打通案例库/行业日历真实数据批量填充流程。
- 选文: 从未处理过 (无 cases/events 行) 的文章, 前置过滤 raw_content 长度
- provider/model 缺省走 resolve_active_model (llm_settings → ark)
- 串行执行 + 每篇落库 (pipeline 内部事务) + SIGINT 可中断 + 进度输出
- dry-run 出候选报告 (篇数/信源分布/预计时长), 不调用 LLM

用法:
    moni extract cases [--source NAME] [--limit N] [--dry-run]
                       [--include-skipped] [--retry-failed] [--after-id ID]
    moni extract events [同参数]
"""

from __future__ import annotations

import signal
import sys
import time
from collections import Counter
from typing import Any

from moni.db.connection import get_db

#: 每篇 LLM 调用粗算时长 (秒) — dry-run 预估用
_EST_SECONDS_PER_ARTICLE = 10.0

#: 进度打印间隔 (篇)
_PROGRESS_EVERY = 10


def _candidate_rows(
    kind: str,
    *,
    source: str | None = None,
    min_content_len: int = 100,
    after_id: str | None = None,
    include_skipped: bool = False,
    retry_failed: bool = False,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """选文 (正常 = 从未处理; +skipped / +failed 附加重跑).

    kind 只接受受控白名单 ('cases' / 'events'), 防止 SQL 注入。
    """
    conn = get_db()
    if kind == "cases":
        table, alias, status_col = "cases c", "c.article_id", "c.extraction_status"
    else:
        table, alias, status_col = "events e", "e.article_id", "e.extraction_status"

    rows: list[Any] = []

    # 1. 从未处理过的文章 (LEFT JOIN 无行)
    sql = f"""
        SELECT a.id, a.title, a.raw_content, a.source, a.published_at, a.fetched_at
        FROM articles a
        LEFT JOIN {table} ON {alias} = a.id
        WHERE {alias} IS NULL
          AND a.raw_content IS NOT NULL
          AND length(a.raw_content) >= ?
    """
    params: list[Any] = [min_content_len]
    if source:
        sql += " AND a.source = ?"
        params.append(source)
    if after_id:
        sql += " AND a.id > ?"
        params.append(after_id)
    sql += " ORDER BY a.fetched_at DESC"
    rows.extend(conn.execute(sql, params).fetchall())

    # 2. 附加重跑池 (skipped / failed)
    extra_statuses: list[str] = []
    if include_skipped:
        extra_statuses.append("skipped")
    if retry_failed:
        extra_statuses.append("failed")
    for st in extra_statuses:
        sql2 = f"""
            SELECT a.id, a.title, a.raw_content, a.source, a.published_at, a.fetched_at
            FROM articles a
            JOIN {table} ON {alias} = a.id
            WHERE {status_col} = ?
              AND a.raw_content IS NOT NULL
              AND length(a.raw_content) >= ?
        """
        p2: list[Any] = [st, min_content_len]
        if source:
            sql2 += " AND a.source = ?"
            p2.append(source)
        sql2 += " ORDER BY a.fetched_at DESC"
        rows.extend(conn.execute(sql2, p2).fetchall())

    if limit:
        rows = rows[:limit]
    return [dict(r) for r in rows]


def _print_dry_run(kind: str, rows: list[dict[str, Any]]) -> None:
    """dry-run 候选报告 (不调用 LLM)."""
    by_source = Counter(r["source"] for r in rows)
    est_sec = len(rows) * _EST_SECONDS_PER_ARTICLE
    print(f"[dry-run] {kind} 提取候选: {len(rows)} 篇")
    for s, c in by_source.most_common():
        print(f"  {s}: {c}")
    print(f"  预计 LLM 调用: {len(rows)} 次 (串行)")
    print(f"  粗算时长: {est_sec / 60:.0f} 分钟 ({est_sec / 3600:.1f} 小时)")


def run_extract(kind: str, args: Any) -> int:
    """批量提取入口 (main.py cmd_extract 调用)."""
    if kind not in ("cases", "events"):
        print(f"未知提取类型: {kind}", file=sys.stderr)
        return 2

    rows = _candidate_rows(
        kind,
        source=args.source,
        min_content_len=args.min_content_len,
        after_id=args.after_id,
        include_skipped=args.include_skipped,
        retry_failed=args.retry_failed,
        limit=args.limit,
    )

    if args.dry_run:
        _print_dry_run(kind, rows)
        return 0

    if not rows:
        print(f"[extract] {kind}: 没有待提取的文章 (前置过滤后)")
        return 0

    # 提取函数按 kind 选择
    if kind == "cases":
        from moni.cases.pipeline import extract_cases_for_article
        fn = extract_cases_for_article
        count_field = "case_count"
    else:
        from moni.events.pipeline import extract_events_for_article
        fn = extract_events_for_article
        count_field = "event_count"

    stats: dict[str, int] = {"total": len(rows)}
    total_extracted = 0
    t0 = time.time()
    interrupted = {"flag": False}

    def _sigint(_sig, _frame):  # noqa: ANN001
        interrupted["flag"] = True
        print("\n[SIGINT] 收到中断, 处理完当前篇后停止…", flush=True)

    signal.signal(signal.SIGINT, _sigint)

    for i, art in enumerate(rows, 1):
        if interrupted["flag"]:
            print("[SIGINT] 已停止", flush=True)
            break
        try:
            res = fn(art)
        except Exception as exc:  # 单篇兜底, 不影响后续
            res = {"status": "failed", count_field: 0, "error": str(exc)}
        status = res.get("status", "failed")
        stats[status] = stats.get(status, 0) + 1
        total_extracted += int(res.get(count_field) or 0)

        if i % _PROGRESS_EVERY == 0 or i == len(rows) or interrupted["flag"]:
            el = time.time() - t0
            print(
                f"  进度 {i}/{len(rows)} | 完成={stats.get('completed', 0)} "
                f"无案例={stats.get('no_cases', 0)} 失败={stats.get('failed', 0)} "
                f"已提取{count_field}={total_extracted} | {el:.0f}s",
                flush=True,
            )

    el = time.time() - t0
    print(
        f"[extract] {kind} 完成: 处理 {stats.get('total', 0)} 篇, "
        f"{el / 60:.1f} 分钟, 提取 {total_extracted} 个{count_field.replace('count', '')}"
    )
    return 0
