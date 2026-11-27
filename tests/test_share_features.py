"""Queue 特性快照：哪些开关会真正产出内容。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moni.share.features import describe_features


def _labels(payload: dict, fmt: str = "html_landing") -> list[str]:
    return [f["label"] for f in describe_features(payload, fmt)["features"]]


def test_default_source_keeps_ai_abstract() -> None:
    assert _labels({"include_abstract": True}) == ["AI 摘要"]
    assert _labels({"include_abstract": True, "bb_summary_source": "moni_ai"}) == [
        "AI 摘要"
    ]


def test_bestblogs_source_replaces_ai_abstract() -> None:
    labels = _labels(
        {
            "include_abstract": True,
            "bb_summary_source": "bestblogs",
            "bb_score": True,
        }
    )
    assert labels == ["BestBlogs 摘要", "BB 评分"]


def test_none_source_emits_no_summary_chip() -> None:
    assert _labels({"include_abstract": True, "bb_summary_source": "none"}) == []


def test_both_source_emits_both_summary_chips() -> None:
    assert _labels({"include_abstract": True, "bb_summary_source": "both"}) == [
        "AI 摘要",
        "BestBlogs 摘要",
    ]


def test_plain_text_never_emits_ai_abstract() -> None:
    assert _labels({"include_abstract": True}, fmt="plain_text") == []


def test_generation_approach_leads_and_is_tagged() -> None:
    doc = describe_features(
        {"generation_approach": "baoyu-infographic", "include_fact_check": True},
        "info_graphic_png",
    )
    assert [f["label"] for f in doc["features"]] == ["生图·信息图", "事实核查"]
    assert doc["features"][0]["kind"] == "approach"
