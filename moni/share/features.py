"""Snapshot the capabilities of a share task for the queue's 特性 column.

The snapshot is written once, at enqueue time, into ``share_tasks.extra``.
Historical rows (extra IS NULL) fall back to their queue file and finally to
"配置未知" — we never guess what an old task did.
"""
from __future__ import annotations

from typing import Any

APPROACH_LABELS = {
    "baoyu-infographic": "生图·信息图",
    "ljg-card": "生图·卡片",
    "kami": "生图·排版",
    "direct": "生图·直出",
}

_OPTION_KEYS = (
    "include_abstract",
    "include_fact_check",
    "include_business_value",
    "include_user_context",
    "include_quality_score",
    "bb_enhance",
    "bb_summary_source",
    "bb_score",
    "bb_main_points",
    "bb_key_quotes",
    "bb_tags",
)


def _feature(key: str, label: str, kind: str = "basic") -> dict[str, str]:
    return {"key": key, "label": label, "kind": kind}


def describe_features(
    payload: dict[str, Any],
    format_name: str,
    *,
    title: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Return the ``extra`` document stored on a share task.

    Order matters: the queue shows the first two, so the most distinctive
    capability (PNG generation approach, then user contexts) leads.
    """
    features: list[dict[str, str]] = []

    approach = payload.get("generation_approach")
    if approach:
        features.append(
            _feature(
                "generation_approach",
                APPROACH_LABELS.get(str(approach), f"生图·{approach}"),
                "approach",
            )
        )
    if payload.get("include_user_context"):
        features.append(_feature("user_context", "用户上下文"))
    if payload.get("include_fact_check"):
        features.append(_feature("fact_check", "事实核查"))
    # plain_text is a no-LLM dump, and a BestBlogs/off summary source means
    # the worker never writes an AI abstract either.
    summary_source = payload.get("bb_summary_source") or "moni_ai"
    if (
        format_name != "plain_text"
        and payload.get("include_abstract")
        and summary_source in ("moni_ai", "both")
    ):
        features.append(_feature("ai_abstract", "AI 摘要"))
    if payload.get("include_business_value"):
        features.append(_feature("business_value", "业务价值"))
    if payload.get("include_quality_score"):
        features.append(_feature("quality_score", "质量评分"))
    if payload.get("bb_summary_source") in ("bestblogs", "both"):
        features.append(_feature("bb_summary", "BestBlogs 摘要"))
    if payload.get("bb_score"):
        features.append(_feature("bb_score", "BB 评分"))
    if payload.get("bb_main_points"):
        features.append(_feature("bb_main_points", "要点列表"))
    if payload.get("bb_key_quotes"):
        features.append(_feature("bb_key_quotes", "金句"))
    if payload.get("bb_tags"):
        features.append(_feature("bb_tags", "专业标签"))

    return {
        "title": (title or payload.get("title") or "").strip() or None,
        "generation_approach": approach,
        "options": {key: payload.get(key) for key in _OPTION_KEYS if key in payload},
        "features": features,
        "created_by": created_by,
    }
