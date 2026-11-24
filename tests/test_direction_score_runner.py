"""Runner tests for direction scoring (Sprint 67, S3)."""
from __future__ import annotations

import json

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.scoring import direction_score, direction_store, rubric
from moni.scoring.direction_score_runner import DirectionScoreRunner, ScoreRequest
from tests.test_scoring_bv import BODY, make_llm
from tests.test_scoring_direction import _payload


def _scores(value: int) -> dict:
    return {
        key: {"score": value, "evidence_ids": ["e1", "e2", "e3"], "reason": f"{key} 有事实"}
        for key in rubric.KEYS
    }


@pytest.fixture
def conn(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    connection = get_db()
    for index in range(3):
        connection.execute(
            "INSERT INTO articles (id, title, url, source, publisher, published_at, "
            "fetched_at, raw_content) VALUES (?,?,?,?,?,?,?,?)",
            (
                f"a{index + 1}",
                f"标题{index}",
                f"https://e.com/{index}",
                "s",
                "p",
                "2024-05-10",
                "2024-05-11",
                BODY,
            ),
        )
    connection.commit()
    yield connection
    close_db()


def _direction(conn) -> tuple[int, int, object]:
    direction_id, _ = direction_store.create_direction(conn, "微信生态", "微信里的动作")
    research, _ = direction_store.start_research(conn, direction_id)
    direction_store.finish_research_success(
        conn,
        direction_id=direction_id,
        research_version=research["research_version"],
        raw_research=_payload(),
        model="test",
    )
    _, _, rubric_obj, reason = direction_score.resolve_rubric(conn, direction_id)
    assert reason is None
    return direction_id, research["research_version"], rubric_obj


def _queue(conn, direction_id, version, rubric_obj, article_ids):
    for article_id in article_ids:
        body_hash = direction_score.article_hashes(conn, [article_id])[article_id]
        assert direction_score.mark_queued(
            conn,
            article_id=article_id,
            direction_id=direction_id,
            research_version=version,
            body_hash=body_hash,
            rubric_id=rubric_obj.id,
        )


def test_runner_scores_every_request_in_order(conn):
    direction_id, version, rubric_obj = _direction(conn)
    ids = ["a1", "a2", "a3"]
    _queue(conn, direction_id, version, rubric_obj, ids)

    runner = DirectionScoreRunner(lambda: make_llm(scores=_scores(6)))
    accepted = runner.submit(
        [
            ScoreRequest(
                direction_id=direction_id,
                research_version=version,
                rubric=rubric_obj,
                article_id=article_id,
            )
            for article_id in ids
        ]
    )
    assert len(accepted) == 3
    assert runner.wait_idle(timeout=30)

    rows = conn.execute(
        "SELECT article_id, status, score FROM article_bv_direction "
        "ORDER BY scored_at"
    ).fetchall()
    assert [row["article_id"] for row in rows] == ids
    assert all(row["status"] == "scored" for row in rows)
    assert all(row["score"] == 60 for row in rows)


def test_runner_skips_duplicate_submissions(conn):
    direction_id, version, rubric_obj = _direction(conn)
    _queue(conn, direction_id, version, rubric_obj, ["a1"])
    request = ScoreRequest(
        direction_id=direction_id,
        research_version=version,
        rubric=rubric_obj,
        article_id="a1",
    )
    runner = DirectionScoreRunner(lambda: make_llm(scores=_scores(6)))
    assert len(runner.submit([request])) == 1
    assert runner.submit([request]) == []
    assert runner.wait_idle(timeout=30)


def test_runner_records_failure_without_raising(conn):
    direction_id, version, rubric_obj = _direction(conn)
    _queue(conn, direction_id, version, rubric_obj, ["a1"])

    def broken_llm(prompt: str, timeout: float):
        raise RuntimeError("模型炸了")

    runner = DirectionScoreRunner(lambda: broken_llm)
    runner.submit(
        [
            ScoreRequest(
                direction_id=direction_id,
                research_version=version,
                rubric=rubric_obj,
                article_id="a1",
            )
        ]
    )
    assert runner.wait_idle(timeout=30)
    row = conn.execute(
        "SELECT status, error FROM article_bv_direction WHERE article_id='a1'"
    ).fetchone()
    assert row["status"] == "failed"
    assert "模型炸了" in row["error"]


def test_live_keys_cover_pending_and_active(conn):
    direction_id, version, rubric_obj = _direction(conn)
    _queue(conn, direction_id, version, rubric_obj, ["a1", "a2"])
    runner = DirectionScoreRunner(lambda: make_llm(scores=_scores(6)))
    assert runner.live_keys == set()
    runner.submit(
        [
            ScoreRequest(
                direction_id=direction_id,
                research_version=version,
                rubric=rubric_obj,
                article_id=article_id,
            )
            for article_id in ("a1", "a2")
        ]
    )
    keys = runner.live_keys
    assert ("a1", direction_id, version) in keys
    assert runner.wait_idle(timeout=30)
    assert runner.live_keys == set()
