"""Unit tests for direction scoring state (Sprint 67, S3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.scoring import direction_score, direction_store
from moni.scoring.bv_pipeline import BvResult
from tests.test_scoring_direction import _payload

BODY = (
    "正文句子一：2024 年 5 月 10 日，人民银行发布《非银行支付机构监督管理条例实施细则》，"
    "要求支付机构在 2024 年 12 月 31 日前完成备案整改，涉及系统改造预算约 3000 万元。"
    "正文句子二：招商银行 2024 年 3 月上线企业微信代发权益，面向代发客户发放立减金。"
    "正文句子三：收单机构需改造商户准入系统与清算接口，满足新的报备要求。"
)


@pytest.fixture
def conn(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    connection = get_db()
    connection.execute(
        "INSERT INTO articles (id, title, url, source, publisher, published_at, "
        "fetched_at, raw_content) VALUES (?,?,?,?,?,?,?,?)",
        ("a1", "标题", "https://e.com/1", "s", "p", "2024-05-10", "2024-05-11", BODY),
    )
    connection.commit()
    yield connection
    close_db()


def _direction(conn, *, label="微信生态", brief="微信里的银行动作", enabled=1):
    direction_id, error = direction_store.create_direction(conn, label, brief)
    assert error is None
    if not enabled:
        direction_store.update_direction(conn, direction_id, enabled=False)
    return direction_id


def _research(conn, direction_id):
    research, error = direction_store.start_research(conn, direction_id)
    assert error is None
    direction_store.finish_research_success(
        conn,
        direction_id=direction_id,
        research_version=research["research_version"],
        raw_research=_payload(),
        model="test",
    )
    return research["research_version"]


def test_resolve_reports_missing_disabled_and_unresearched(conn):
    direction, research, reason = direction_score.resolve_direction(conn, 999)
    assert direction is None and research is None
    assert reason == "方向不存在"

    direction_id = _direction(conn, enabled=0)
    direction, research, reason = direction_score.resolve_direction(conn, direction_id)
    assert direction is not None and research is None
    assert "停用" in reason

    direction_id = _direction(conn, label="短视频")
    direction, research, reason = direction_score.resolve_direction(conn, direction_id)
    assert research is None
    assert "研究版本" in reason


def test_resolve_rubric_builds_rubric_after_research(conn):
    direction_id = _direction(conn)
    version = _research(conn, direction_id)
    direction, research, rubric, reason = direction_score.resolve_rubric(
        conn, direction_id
    )
    assert reason is None
    assert research.version == version
    assert rubric.id == f"bv_direction_v{version}"
    assert len(rubric.dimensions) == 10


def test_queued_running_and_terminal_states(conn):
    direction_id = _direction(conn)
    version = _research(conn, direction_id)
    body_hash = direction_score.article_hashes(conn, ["a1"])["a1"]

    assert direction_score.mark_queued(
        conn,
        article_id="a1",
        direction_id=direction_id,
        research_version=version,
        body_hash=body_hash,
        rubric_id="bv_direction_v1",
    )
    # A second claim must not steal the row.
    assert not direction_score.mark_queued(
        conn,
        article_id="a1",
        direction_id=direction_id,
        research_version=version,
        body_hash=body_hash,
        rubric_id="bv_direction_v1",
    )
    row = conn.execute(
        "SELECT status FROM article_bv_direction WHERE article_id='a1'"
    ).fetchone()
    assert row["status"] == direction_score.STATUS_QUEUED

    assert direction_score.mark_running(
        conn, article_id="a1", direction_id=direction_id, research_version=version
    )
    assert not direction_score.mark_running(
        conn, article_id="a1", direction_id=direction_id, research_version=version
    )

    outcome = direction_score.write_score(
        conn,
        article_id="a1",
        direction_id=direction_id,
        research_version=version,
        result=BvResult(status="ready", total=72, note="好", content_hash=body_hash),
        rubric_id="bv_direction_v1",
    )
    assert outcome["written"] is True
    assert outcome["status"] == direction_score.STATUS_SCORED
    row = conn.execute(
        "SELECT status, score FROM article_bv_direction WHERE article_id='a1'"
    ).fetchone()
    assert row["status"] == "scored" and row["score"] == 72


def test_write_score_refuses_non_running_row(conn):
    direction_id = _direction(conn)
    version = _research(conn, direction_id)
    body_hash = direction_score.article_hashes(conn, ["a1"])["a1"]
    direction_score.mark_queued(
        conn,
        article_id="a1",
        direction_id=direction_id,
        research_version=version,
        body_hash=body_hash,
        rubric_id="bv_direction_v1",
    )
    outcome = direction_score.write_score(
        conn,
        article_id="a1",
        direction_id=direction_id,
        research_version=version,
        result=BvResult(status="ready", total=10, content_hash=body_hash),
        rubric_id="bv_direction_v1",
    )
    assert outcome["written"] is False
    row = conn.execute(
        "SELECT status FROM article_bv_direction WHERE article_id='a1'"
    ).fetchone()
    assert row["status"] == direction_score.STATUS_QUEUED


def test_status_mapping_covers_every_harness_result():
    assert direction_score.map_status(BvResult(status="ready")) == "scored"
    assert direction_score.map_status(BvResult(status="needs_review")) == "needs_review"
    assert direction_score.map_status(BvResult(status="insufficient")) == "insufficient"
    assert direction_score.map_status(BvResult(status="budget_skipped")) == "failed"
    assert direction_score.map_status(BvResult(status="failed")) == "failed"


def test_stale_flag_follows_body_change(conn):
    direction_id = _direction(conn)
    version = _research(conn, direction_id)
    body_hash = direction_score.article_hashes(conn, ["a1"])["a1"]
    direction_score.mark_queued(
        conn,
        article_id="a1",
        direction_id=direction_id,
        research_version=version,
        body_hash=body_hash,
        rubric_id="bv_direction_v1",
    )
    direction_score.mark_running(
        conn, article_id="a1", direction_id=direction_id, research_version=version
    )
    direction_score.write_score(
        conn,
        article_id="a1",
        direction_id=direction_id,
        research_version=version,
        result=BvResult(status="ready", total=60, content_hash=body_hash),
        rubric_id="bv_direction_v1",
    )
    scores = direction_score.load_scores(
        conn, direction_id=direction_id, research_version=version, article_ids=["a1"]
    )
    assert scores["a1"]["stale"] is False

    conn.execute("UPDATE articles SET raw_content=? WHERE id='a1'", (BODY + "补充",))
    conn.commit()
    scores = direction_score.load_scores(
        conn, direction_id=direction_id, research_version=version, article_ids=["a1"]
    )
    assert scores["a1"]["stale"] is True


def test_breakdown_is_trimmed_to_public_fields(conn):
    direction_id = _direction(conn)
    version = _research(conn, direction_id)
    body_hash = direction_score.article_hashes(conn, ["a1"])["a1"]
    direction_score.mark_queued(
        conn,
        article_id="a1",
        direction_id=direction_id,
        research_version=version,
        body_hash=body_hash,
        rubric_id="bv_direction_v1",
    )
    direction_score.mark_running(
        conn, article_id="a1", direction_id=direction_id, research_version=version
    )
    breakdown = {
        "rubric": "bv_direction_v1",
        "total": 70,
        "evidence": [{"id": "e1", "quote": "不该外泄"}],
        "dimensions": [
            {
                "key": "rule_delta",
                "label": "规则变化",
                "weight": 12,
                "score": 7,
                "reason": "有文号",
                "evidence_ids": ["e1"],
            }
        ],
    }
    direction_score.write_score(
        conn,
        article_id="a1",
        direction_id=direction_id,
        research_version=version,
        result=BvResult(
            status="ready", total=70, content_hash=body_hash, breakdown=breakdown
        ),
        rubric_id="bv_direction_v1",
    )
    scores = direction_score.load_scores(
        conn, direction_id=direction_id, research_version=version, article_ids=["a1"]
    )
    payload = scores["a1"]["breakdown"]
    assert "evidence" not in payload
    assert payload["dimensions"][0] == {
        "key": "rule_delta",
        "label": "规则变化",
        "weight": 12,
        "score": 7,
        "reason": "有文号",
    }


def test_recover_stale_fails_dead_running_and_orphan_queued(conn):
    direction_id = _direction(conn)
    version = _research(conn, direction_id)
    body_hash = direction_score.article_hashes(conn, ["a1"])["a1"]
    conn.execute(
        "INSERT INTO article_bv_direction (article_id, direction_id, research_version, "
        "status, content_hash, rubric_id, scored_at) VALUES (?,?,?,?,?,?,?)",
        ("a1", direction_id, version, "running", body_hash, "bv_direction_v1",
         (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()),
    )
    conn.execute(
        "INSERT INTO article_bv_direction (article_id, direction_id, research_version, "
        "status, content_hash, rubric_id, scored_at) VALUES (?,?,?,?,?,?,?)",
        ("a1", direction_id, version + 1, "queued", body_hash, "bv_direction_v1",
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    # The queued row is live, so it must survive.
    dead = direction_score.recover_stale(
        conn, live_keys=[("a1", direction_id, version + 1)]
    )
    assert dead == 1
    rows = {
        (row["research_version"], row["status"])
        for row in conn.execute(
            "SELECT research_version, status FROM article_bv_direction"
        ).fetchall()
    }
    assert (version, "failed") in rows
    assert (version + 1, "queued") in rows

    dead = direction_score.recover_stale(conn, live_keys=[])
    assert dead == 1
    row = conn.execute(
        "SELECT status, error FROM article_bv_direction WHERE research_version=?",
        (version + 1,),
    ).fetchone()
    assert row["status"] == "failed"
    assert "队列" in row["error"]
