"""Claim / recovery semantics for the on-demand BV runner (v1.0.130)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.scoring import bv_runner
from tests.test_scoring_bv import BODY


@pytest.fixture
def conn(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    connection = get_db()
    connection.execute(
        "INSERT INTO articles (id, title, url, source, publisher, published_at, "
        "fetched_at, raw_content) VALUES (?,?,?,?,?,?,?,?)",
        ("a1", "t", "https://e.com/1", "s", "p", "2024-05-10", "2024-05-11", BODY),
    )
    connection.commit()
    yield connection
    close_db()


def _age(conn, seconds: int) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    conn.execute("UPDATE articles SET business_value_at=? WHERE id='a1'", (stamp,))
    conn.commit()


def _status(conn) -> str | None:
    return conn.execute(
        "SELECT business_value_status FROM articles WHERE id='a1'"
    ).fetchone()[0]


def test_mark_queued_claims_a_fresh_row(conn):
    assert bv_runner.mark_queued(conn, "a1") is True
    assert _status(conn) == "queued"


def test_mark_queued_refuses_a_live_row(conn):
    assert bv_runner.mark_queued(conn, "a1") is True
    assert bv_runner.mark_queued(conn, "a1") is False


def test_mark_queued_reclaims_an_abandoned_row(conn):
    bv_runner.mark_queued(conn, "a1")
    _age(conn, bv_runner.STALE_AFTER_SEC + 60)
    assert bv_runner.mark_queued(conn, "a1") is True


def test_mark_running_only_moves_queued(conn):
    bv_runner.mark_queued(conn, "a1")
    assert bv_runner.mark_running(conn, "a1") is True
    assert _status(conn) == "running"
    assert bv_runner.mark_running(conn, "a1") is False


def test_recover_stale_skips_ids_the_runner_still_owns(conn):
    bv_runner.mark_queued(conn, "a1")
    _age(conn, bv_runner.STALE_AFTER_SEC + 60)
    assert bv_runner.recover_stale(conn, live_ids={"a1"}) == 0
    assert _status(conn) == "queued"

    assert bv_runner.recover_stale(conn, live_ids=set()) == 1
    assert _status(conn) == "failed"
    error = conn.execute(
        "SELECT business_value_error FROM articles WHERE id='a1'"
    ).fetchone()[0]
    assert "中断" in error


def test_is_inflight_covers_both_states(conn):
    row = bv_runner.load_row(conn, "a1")
    assert bv_runner.is_inflight(row) is False
    bv_runner.mark_queued(conn, "a1")
    assert bv_runner.is_inflight(bv_runner.load_row(conn, "a1")) is True
    bv_runner.mark_running(conn, "a1")
    assert bv_runner.is_inflight(bv_runner.load_row(conn, "a1")) is True
