"""moni.stats.savings — 节省时间公式 (T₀ yaml 配置 + T₁ 实测)。

v1.0.19: moni-B §四.7 W1 量化看板 — 申报 §五 "降本增效 + 效益提质" +2-3 分。

公式 (来自 MOA 拍板 Q2+A):
    savings_minutes = (T0_baseline_minutes_per_task - actual_minutes_per_task) × task_count

口径防夸大三铁律 (from research §二 反夸大 4 原则):
1. T₀ 是 yaml 配置的"人工基准", 不是实测 → display_status 标 "公式估算" 非 "实测"
2. 样本 N < 阈值 → 不展示百分比/节省, 显示 "数据积累中"
3. 不报年化数字, 只报期间内数字 (period=30d 即月内数字)

T₀ 默认值 (from MOA 拍板 Q3+A 保守 30min/篇):
- summary / fact_check: 30 分钟 (Brynjolfsson 新手 +34% 推断 moni 业务人员新场景)
- event_extract / case_extract: 25 分钟 (结构化输出略快)
- predict_tags: 5 分钟 (简单分类)
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from ..db.connection import get_db

logger = logging.getLogger(__name__)


# ── 默认 T₀ 配置 (跟 config/savings_baseline.yaml 同步) ─────────
DEFAULT_T0_MINUTES: dict[str, float] = {
    "summary": 30.0,
    "fact_check": 30.0,
    "event_extract": 25.0,
    "case_extract": 25.0,
    "predict_tags": 5.0,
}

DEFAULT_TARGET_GROWTH: float = 1.2  # 顶部三卡 target = current × 1.2 (保守)


def _load_baseline_yaml() -> dict[str, Any]:
    """读 config/savings_baseline.yaml (失败 fallback 默认值).

    Returns:
        {t0_minutes: {task_type: float}, target_growth: float}
    """
    yaml_path = Path("config/savings_baseline.yaml")
    if not yaml_path.exists():
        return {
            "t0_minutes": dict(DEFAULT_T0_MINUTES),
            "target_growth": DEFAULT_TARGET_GROWTH,
        }
    try:
        with yaml_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        t0 = data.get("t0_minutes") or dict(DEFAULT_T0_MINUTES)
        tg = data.get("target_growth") or DEFAULT_TARGET_GROWTH
        return {"t0_minutes": t0, "target_growth": float(tg)}
    except Exception as e:
        logger.warning("savings_baseline.yaml 解析失败, fallback 默认: %s", e)
        return {
            "t0_minutes": dict(DEFAULT_T0_MINUTES),
            "target_growth": DEFAULT_TARGET_GROWTH,
        }


def compute_savings(period: str = "30d") -> dict[str, Any]:
    """节省时间聚合 (供顶部效率层卡片 + /api/stats/savings).

    Args:
        period: '7d' / '30d' / '90d'

    Returns:
        {
          display_status: 'ok' / '数据积累中',
          display_label: '公式估算' (标非实测, 防评委质疑),
          total_savings_minutes: float,
          by_task_type: [{task_type, count, t0_minutes, actual_minutes, savings_minutes}],
          t0_source: 'config/savings_baseline.yaml' (口径透明),
          config_version: '1.0' (yaml 改版本号便于审计)
        }
    """
    from .repository import N_DEFAULT_THRESHOLD, _period_iso  # 延迟导入避免循环

    baseline = _load_baseline_yaml()
    t0_map = baseline["t0_minutes"]
    since = _period_iso(period)

    # T₁ 实测: 按 task_type 聚合 duration_ms
    rows = get_db().execute(
        """
        SELECT task_type,
               COUNT(*) AS n,
               AVG(duration_ms) AS avg_ms
        FROM llm_jobs
        WHERE started_at >= ? AND status='done' AND duration_ms IS NOT NULL
        GROUP BY task_type
        """,
        (since,),
    ).fetchall()

    by_task_type = []
    total_savings = 0.0
    total_count = 0

    for r in rows:
        task_type = r["task_type"]
        n = r["n"] or 0
        actual_min = (r["avg_ms"] or 0) / 1000.0 / 60.0  # ms → min
        t0_min = float(t0_map.get(task_type, DEFAULT_T0_MINUTES.get(task_type, 30.0)))
        savings_per_task = max(0.0, t0_min - actual_min)
        task_savings = savings_per_task * n
        total_savings += task_savings
        total_count += n
        by_task_type.append(
            {
                "task_type": task_type,
                "count": n,
                "t0_minutes_per_task": round(t0_min, 1),
                "actual_minutes_per_task": round(actual_min, 3),
                "savings_minutes_per_task": round(savings_per_task, 3),
                "savings_minutes_total": round(task_savings, 1),
            }
        )

    # 排序按节省降序, 前 5 上瀑布图
    by_task_type.sort(key=lambda x: x["savings_minutes_total"], reverse=True)

    raw = {
        "total_savings_minutes": round(total_savings, 1),
        "by_task_type": by_task_type,
        "total_count": total_count,
    }

    return {
        "period": period,
        "module": "efficiency",
        "display_status": "数据积累中" if total_count < N_DEFAULT_THRESHOLD else "ok",
        "display_label": "公式估算",  # 防评委误读为实测
        "count": total_count,
        "threshold": N_DEFAULT_THRESHOLD,
        "t0_source": "config/savings_baseline.yaml",
        "t0_config_version": "1.0",
        "raw": raw,
    }