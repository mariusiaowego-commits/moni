"""Unit tests for moni.scoring.heuristic (design doc §5, §9)."""
from __future__ import annotations

from moni.scoring import heuristic
from moni.scoring.snapshot import ArticleSnapshot


def _rich_body() -> str:
    para = "2024 年 3 月，某行上线 2 项功能、投放 3 亿元。\n\n"
    return para * 5 + "正文段落内容。" * 900


def _snap(**kw) -> ArticleSnapshot:
    base = dict(id="a1", title="T", raw_content="", publisher="", published_at="")
    base.update(kw)
    return ArticleSnapshot(**base)


def test_thin_body_scores_three_one():
    snap = _snap(raw_content="字" * 1000)
    result = heuristic.score_quality(snap)
    assert result.score == 3.1  # 2.5 base + 0.6
    assert result.status == "ready"
    assert result.formula == heuristic.PIN_FORMULA
    assert result.breakdown["base"] + sum(
        i["delta"] for i in result.breakdown["items"]
    ) == result.score


def test_full_house_is_five():
    snap = _snap(raw_content=_rich_body(), publisher="某行", published_at="2024-12-31")
    result = heuristic.score_quality(snap)
    assert result.score == heuristic.PIN_MAX == 5.0
    assert result.breakdown["items"][1]["delta"] == 0.6
    assert result.breakdown["scale"] == "0-5"


def test_score_never_exceeds_two_decimals_or_five():
    snap = _snap(raw_content=_rich_body(), publisher="某行", published_at="2024-12-31")
    result = heuristic.score_quality(snap)
    assert result.score is not None
    assert round(result.score, 2) == result.score
    assert 0.0 <= result.score <= 5.0


def test_substance_caps_at_point_six():
    body = "1 2 3 4 5 6\n\n" + "段。\n\n" * 5
    snap = _snap(raw_content=body * 20)
    result = heuristic.score_quality(snap)
    substance = next(i for i in result.breakdown["items"] if i["key"] == "substance")
    assert substance["delta"] <= 0.6


def test_fingerprint_ignores_title_and_summary():
    a = _snap(raw_content="正文" * 300, ai_summary="摘要A", title="标题A")
    b = _snap(raw_content="正文" * 300, ai_summary="摘要B", title="标题B")
    assert heuristic.quality_fingerprint(a) == heuristic.quality_fingerprint(b)


def test_fingerprint_moves_with_body():
    a = _snap(raw_content="正文" * 300)
    b = _snap(raw_content="正文" * 301)
    assert heuristic.quality_fingerprint(a) != heuristic.quality_fingerprint(b)


def test_reading_heuristic_ignores_pin_only_factors():
    body = _rich_body()
    snap = _snap(
        raw_content=body,
        ai_summary="摘要",
        fact_check_conclusion="已核查",
        publisher="某行",
        published_at="2024-12-31",
        source="rss",
    )
    assert len(body.strip()) >= 5000
    assert heuristic.score_quality(snap).score == 5.0
    assert heuristic.reading_heuristic(snap) == 93  # 45+25+8+7+5+3


def test_thin_article_floor():
    result = heuristic.score_quality(_snap(raw_content="短"))
    assert result.score == 1.4  # 2.5 base - 1.1, clamped at 0 lower bound


def test_paragraph_counting_needs_blank_lines():
    assert heuristic.count_paragraphs("a\n\nb\n\nc") == 3
    assert heuristic.count_paragraphs("a\nb\nc") == 1


def test_quality_current_requires_formula_and_hash():
    snap = _snap(raw_content="正文" * 300)
    good = heuristic.score_quality(snap)
    assert heuristic.is_quality_current(
        good.formula, good.input_hash, "ready", "{}", snap
    )
    assert not heuristic.is_quality_current(
        None, good.input_hash, "ready", "{}", snap
    )
    assert not heuristic.is_quality_current(
        good.formula, "deadbeef", "ready", "{}", snap
    )
