"""v1.0.104 Gate 3 语义契约。

钉住的失败模式：
1. 历史背景证据（旧文）把近期新闻整篇拖成 90d_demote；
2. 91+ 把【待核实】错改成【存疑】（旧≠矛盾）；
3. Gate 3 降级清空 summary，导致分享预检死循环；
4. how 文案自相矛盾（一边写最新来源 13 天，一边写超过 90 天）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "moni" / "llm" / "jobs.py"
PLUS = ROOT / "moni" / "web" / "routes" / "fact_check_plus.py"
INDEX = ROOT / "moni" / "web" / "static" / "index.html"
READER_JS = ROOT / "moni" / "web" / "static" / "js" / "reader.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_timeliness_uses_newest_evidence_when_no_verified_rows():
    """失败模式 1：没有【已核实】证据时，看最新一条，不看全批最旧一条。"""
    src = _read(JOBS)
    assert "newest_days = [e[\"days_old\"]" in src, (
        "when no evidence row is 【已核实】, Gate 3 must use the newest evidence age; "
        "otherwise a historical background citation drags a fresh news item into 90d_demote"
    )
    assert "worst_days = min(newest_days)" in src, (
        "the fallback must be the newest evidence age, not the worst (oldest)"
    )


def test_90_day_demotion_changes_verified_to_unverified_only():
    """失败模式 2：旧不等于矛盾；91+ 不把【待核实】改成【存疑】。"""
    src = _read(PLUS)
    m = src.find("def apply_timeliness(")
    assert m >= 0
    body = src[m : src.find("def ", m + 1)]
    assert 'with_tag("【待核实】", conclusion)' in body
    assert 'with_tag("【存疑】", conclusion)' not in body, (
        "apply_timeliness must not convert 待核实/无矛盾 claims into 存疑; "
        "timeliness is a separate signal, not a contradiction finding"
    )


def test_gate3_does_not_blank_summary():
    """失败模式 3：降级不许清空人话结论，分享预检依赖 summary 非空。"""
    src = _read(JOBS)
    assert "summary = \"\"" not in src
    assert "if gate2_demoted or timeliness == \"90d_demote\":" not in src, (
        "Gate 2/3 must not blank fact_check_summary; enrichment.py treats it as "
        "'missing summary' and repeatedly enqueues the same article"
    )


def test_how_text_reports_newest_and_oldest_without_contradiction():
    """失败模式 4：how 文案必须与实际聚合值一致。"""
    src = _read(JOBS)
    assert "最新来源距今" in src
    assert "最新来源超过 90 天" not in src, (
        "_build_how must not claim the newest source is >90 days when the newest "
        "row is fresh; the old text contradicted itself in the same sentence"
    )


def test_how_warn_text_matches_verified_evidence_semantics():
    """审计 P2：30d_warn 时聚合值可能是【已核实】证据的 max，而不是全批 max。

    文案不得再说“最新来源已过 30 天”，否则同一句里会一边写最新来源 13 天、
    一边写过期 30 天。
    """
    src = _read(JOBS)
    assert '"30d_warn": "含 30–90 天旧证"' in src
    assert "最新来源已过 30 天" not in src


def test_reader_renders_timeliness_badge():
    """UI 必须区分时效降级与模型判定，而不是伪装成存疑。"""
    html = _read(INDEX)
    js = _read(READER_JS)
    assert "readerTimelinessLabel" in html and "x-text=\"readerTimelinessLabel\"" in html, (
        "fact tab must bind the timeliness badge label"
    )
    assert "时效降级 · 含超 90 天旧证" in js
    assert "含 30–90 天旧证" in js


def test_gate2_summary_rewrite_is_limited_to_verified_originals():
    """Gate 2 降级说明留在 summary，且不得把本来就准确的人话结论一并抹掉。"""
    src = _read(JOBS)
    assert "门禁降级：权威源不足" in src
    assert "summary = _clip_sentence" in src
