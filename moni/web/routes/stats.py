"""moni.web.routes.stats — W1 量化看板 API。

v1.0.19: moni-B §四.7 路线图 W1 申报包装。
- GET /api/stats/overview?period=7d|30d|90d — 顶部三卡 + 6 类数字汇总
- GET /api/stats/timeseries?metric=&period=&granularity= — 趋势数据 (供 SVG 趋势图)
- GET /api/stats/llm?period= — LLM 任务按 task_type 分桶
- GET /api/stats/savings?period= — 节省时间明细 (读 T₀ yaml + 实测 T₁)

所有端点共用 moni/stats/{repository,savings}.py 聚合层。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from moni.stats.repository import (
    fetch_layer_stats,
    llm_layer_stats,
    extract_layer_stats,
    usage_layer_stats,
    overview,
    timeseries,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/overview")
def get_overview(
    period: str = Query("30d", description="时间窗口: 7d / 30d / 90d"),
) -> dict[str, Any]:
    """顶部三卡 + 6 类数字汇总 (供 /stats 页面 hero + 5 模块卡片)."""
    if period not in ("7d", "30d", "90d"):
        period = "30d"
    return overview(period)


@router.get("/timeseries")
def get_timeseries(
    metric: str = Query(
        ...,
        description="指标: fetch / llm / events / cases / pins / share",
    ),
    period: str = Query("30d", description="时间窗口: 7d / 30d / 90d"),
    granularity: str = Query(
        "daily", description="粒度: daily / weekly / monthly"
    ),
) -> dict[str, Any]:
    """时间序列数据 (供 SVG 趋势图).

    Args:
        metric: 'fetch' / 'llm' / 'events' / 'cases' / 'pins' / 'share'
        period: '7d' / '30d' / '90d'
        granularity: 'daily' / 'weekly' / 'monthly'
    """
    if period not in ("7d", "30d", "90d"):
        period = "30d"
    if granularity not in ("daily", "weekly", "monthly"):
        granularity = "daily"
    return timeseries(metric, period, granularity)


@router.get("/llm")
def get_llm_stats(
    period: str = Query("30d", description="时间窗口: 7d / 30d / 90d"),
) -> dict[str, Any]:
    """LLM 任务按 task_type 分桶 (供 LLM 模块卡片 + 节省瀑布图数据)."""
    if period not in ("7d", "30d", "90d"):
        period = "30d"
    return llm_layer_stats(period)


@router.get("/savings")
def get_savings(
    period: str = Query("30d", description="时间窗口: 7d / 30d / 90d"),
) -> dict[str, Any]:
    """节省时间明细 (供效率层卡片 + 节省瀑布图).

    返回 display_label='公式估算' 防评委误读为实测, t0_source 标明 yaml 路径便于审计.
    """
    if period not in ("7d", "30d", "90d"):
        period = "30d"
    from moni.stats.savings import compute_savings  # 延迟导入避免循环
    return compute_savings(period)


# (可选) 单模块端点 — 供前端按需拉取避免一次拉全
@router.get("/fetch")
def get_fetch_stats(
    period: str = Query("30d", description="时间窗口: 7d / 30d / 90d"),
) -> dict[str, Any]:
    if period not in ("7d", "30d", "90d"):
        period = "30d"
    return fetch_layer_stats(period)


@router.get("/extract")
def get_extract_stats(
    period: str = Query("30d", description="时间窗口: 7d / 30d / 90d"),
) -> dict[str, Any]:
    if period not in ("7d", "30d", "90d"):
        period = "30d"
    return extract_layer_stats(period)


@router.get("/usage")
def get_usage_stats(
    period: str = Query("30d", description="时间窗口: 7d / 30d / 90d"),
) -> dict[str, Any]:
    if period not in ("7d", "30d", "90d"):
        period = "30d"
    return usage_layer_stats(period)