"""Tests for the reader perspective and the clarification round (sprint-74).

The reader perspective is what a direction's articles are judged *from*.
One sentence is not enough for the model to rewrite ten scoring
questions, so the perspective is clarified first: the model asks up to
five questions, the user answers them, and the answers become part of
the research input.
"""
from __future__ import annotations

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.scoring import direction_store
from moni.scoring.direction import (
    CLARIFY_MAX_QUESTIONS,
    DIRECTION_PROMPT_ID,
    DIRECTION_PROMPT_ID_V2,
    STATUS_EXPIRED,
    build_clarify_prompt,
    build_research_prompt,
    parse_clarification,
    reader_reject_reason,
    research_prompt_id,
)
from tests.test_scoring_direction import _payload

PERSPECTIVE = "银行新媒体渠道从业者，想知道哪些内容能变成公众号选题"


@pytest.fixture
def db(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    yield conn
    close_db()


def _direction(db, label="微信生态", brief="微信里的银行动作", perspective=""):
    direction_id, error = direction_store.create_direction(
        db, label, brief, "", perspective
    )
    assert error is None and direction_id is not None
    return direction_id


def _clarify(db, direction_id, questions):
    """Run one clarification round to ``passed`` with the given questions."""
    running, error = direction_store.start_clarification(db, direction_id)
    assert error is None and running is not None
    version = int(running["clarification_version"])
    ok, error = direction_store.finish_clarification_success(
        db,
        direction_id=direction_id,
        clarification_version=version,
        raw_clarification={"sufficient": not questions, "questions": list(questions)},
    )
    assert ok, error
    return version


# ── validation ─────────────────────────────────────────────────────────


def test_reader_perspective_is_optional_but_bounded():
    assert reader_reject_reason("") is None
    assert reader_reject_reason("银行新媒体渠道从业者") is None
    error = reader_reject_reason("长" * 201)
    assert error and "200" in error


def test_prompt_id_follows_the_perspective():
    assert research_prompt_id("") == DIRECTION_PROMPT_ID
    assert research_prompt_id("   ") == DIRECTION_PROMPT_ID
    assert research_prompt_id(PERSPECTIVE) == DIRECTION_PROMPT_ID_V2


# ── prompts ────────────────────────────────────────────────────────────


def test_research_prompt_without_perspective_is_unchanged():
    prompt = build_research_prompt("微信生态", "说明")
    assert "读者视角" not in prompt
    assert "问题一律从银行或支付机构角度问" in prompt
    assert "问题一律从上面「读者视角」" not in prompt


def test_research_prompt_with_perspective_asks_from_that_point_of_view():
    prompt = build_research_prompt(
        "微信生态",
        "说明",
        "",
        PERSPECTIVE,
        "Q1: 更在意获客还是合规？\nA1: 两者都要，先获客",
    )
    assert "<读者视角>" in prompt
    assert PERSPECTIVE in prompt
    assert "<澄清问答>" in prompt
    assert "问题一律从上面「读者视角」里那个人的立场问" in prompt
    assert "问题一律从银行或支付机构角度问" not in prompt


def test_clarify_prompt_carries_the_perspective_and_a_question_budget():
    prompt = build_clarify_prompt("微信生态", "说明", PERSPECTIVE)
    assert PERSPECTIVE in prompt
    assert f"最多 {CLARIFY_MAX_QUESTIONS} 个" in prompt
    assert "sufficient" in prompt


# ── clarification parsing ──────────────────────────────────────────────


def test_parse_clarification_accepts_a_clear_perspective():
    parsed, error = parse_clarification('{"sufficient": true, "questions": []}')
    assert error is None
    assert parsed is not None
    assert parsed.sufficient is True
    assert parsed.questions == ()


def test_parse_clarification_accepts_questions():
    parsed, error = parse_clarification(
        '{"sufficient": false, "questions": ["更在意获客还是合规？", "他要多长的内容？"]}'
    )
    assert error is None
    assert parsed is not None
    assert parsed.questions == ("更在意获客还是合规？", "他要多长的内容？")


def test_parse_clarification_rejects_broken_payloads():
    parsed, error = parse_clarification("not json")
    assert parsed is None and error

    parsed, error = parse_clarification('{"sufficient": "yes", "questions": []}')
    assert parsed is None and error and "sufficient" in error

    parsed, error = parse_clarification('{"sufficient": true, "questions": ["问句"]}')
    assert parsed is None and error

    parsed, error = parse_clarification('{"sufficient": false, "questions": []}')
    assert parsed is None and error

    parsed, error = parse_clarification(
        '{"sufficient": false, "questions": ["问句"], "questions": []}'
    )
    assert parsed is None and error


def test_parse_clarification_rejects_too_many_or_too_long_questions():
    many = "、".join(f"问题{i}" for i in range(CLARIFY_MAX_QUESTIONS + 1))
    parsed, error = parse_clarification(
        '{"sufficient": false, "questions": [' + ",".join(f'"{q}"' for q in many.split("、")) + "]}"
    )
    assert parsed is None and error and str(CLARIFY_MAX_QUESTIONS) in error

    long_question = "问" * 61
    parsed, error = parse_clarification(
        '{"sufficient": false, "questions": ["' + long_question + '"]}'
    )
    assert parsed is None and error and "60" in error


# ── store: perspective travels with the direction ─────────────────────


def test_direction_keeps_the_reader_perspective(db):
    direction_id = _direction(db, perspective=PERSPECTIVE)
    row = direction_store.get_direction(db, direction_id)
    assert row is not None
    assert row["reader_perspective"] == PERSPECTIVE

    updated, error = direction_store.update_direction(
        db, direction_id, reader_perspective="换成投行从业者"
    )
    assert error is None
    assert updated is not None
    assert updated["reader_perspective"] == "换成投行从业者"


def test_changing_the_perspective_expires_the_passed_research(db):
    direction_id = _direction(db)
    running, _ = direction_store.start_research(db, direction_id)
    ok, _ = direction_store.finish_research_success(
        db,
        direction_id=direction_id,
        research_version=int(running["research_version"]),
        raw_research=_payload(),
        model="test",
    )
    assert ok
    assert direction_store.direction_usable(db, direction_store.get_direction(db, direction_id))[0]

    direction_store.update_direction(db, direction_id, reader_perspective=PERSPECTIVE)
    usable, reason = direction_store.direction_usable(
        db, direction_store.get_direction(db, direction_id)
    )
    assert usable is None
    assert reason


# ── store: the clarification gate ──────────────────────────────────────


def test_research_is_blocked_until_the_perspective_is_clarified(db):
    direction_id = _direction(db, perspective=PERSPECTIVE)
    running, error = direction_store.start_research(db, direction_id)
    assert running is None
    assert error and "澄清" in error


def test_research_is_blocked_while_questions_are_unanswered(db):
    direction_id = _direction(db, perspective=PERSPECTIVE)
    _clarify(db, direction_id, ["更在意获客还是合规？", "他要多长的内容？"])
    running, error = direction_store.start_research(db, direction_id)
    assert running is None
    assert error and "2" in error


def test_clarified_direction_can_be_researched(db):
    direction_id = _direction(db, perspective=PERSPECTIVE)
    _clarify(db, direction_id, ["更在意获客还是合规？"])
    saved, error = direction_store.save_clarification_answers(
        db, direction_id, ["两者都要，先获客"]
    )
    assert error is None and saved is not None

    running, error = direction_store.start_research(db, direction_id)
    assert error is None and running is not None
    assert running["perspective_snapshot"] == PERSPECTIVE
    assert "更在意获客还是合规" in running["clarification_snapshot"]
    assert running["prompt_id"] == DIRECTION_PROMPT_ID_V2

    ok, error = direction_store.finish_research_success(
        db,
        direction_id=direction_id,
        research_version=int(running["research_version"]),
        raw_research=_payload(),
        model="test",
    )
    assert ok, error
    direction = direction_store.get_direction(db, direction_id)
    research, reason = direction_store.direction_usable(db, direction)
    assert research is not None, reason
    assert research.reader_perspective == PERSPECTIVE


def test_changing_an_answer_expires_the_research(db):
    direction_id = _direction(db, perspective=PERSPECTIVE)
    _clarify(db, direction_id, ["更在意获客还是合规？"])
    direction_store.save_clarification_answers(db, direction_id, ["先获客"])
    running, _ = direction_store.start_research(db, direction_id)
    direction_store.finish_research_success(
        db,
        direction_id=direction_id,
        research_version=int(running["research_version"]),
        raw_research=_payload(),
        model="test",
    )

    saved, error = direction_store.save_clarification_answers(
        db, direction_id, ["先合规"]
    )
    assert error is None and saved is not None
    rows = direction_store.latest_research_rows(db, direction_id)
    assert rows[0]["status"] == STATUS_EXPIRED
    usable, reason = direction_store.direction_usable(
        db, direction_store.get_direction(db, direction_id)
    )
    assert usable is None
    # The row is expired outright, so the gate reports that first.
    assert reason and ("澄清" in reason or "expired" in reason)


def test_a_clarification_for_an_older_perspective_does_not_count(db):
    direction_id = _direction(db, perspective=PERSPECTIVE)
    _clarify(db, direction_id, ["更在意获客还是合规？"])
    direction_store.save_clarification_answers(db, direction_id, ["先获客"])

    direction_store.update_direction(db, direction_id, reader_perspective="投行从业者")
    running, error = direction_store.start_research(db, direction_id)
    assert running is None
    assert error and "澄清" in error
