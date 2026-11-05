"""Lease heartbeat thread for fetch jobs (v1.0.67).

A single transient SQLite error used to stop the heartbeat for good, so a
live job looked dead 90s later. These tests pin the retry/exit behaviour.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from moni.db import leases
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.fetch.jobs import FetchJobStore
from moni.web.routes.fetch import _start_heartbeat


@pytest.fixture
def fresh_db(tmp_path):
    set_db_path(tmp_path / "news.db")
    migrate()
    yield
    close_db()


def _wait_for(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _pending_lease() -> tuple[FetchJobStore, str, leases.LeaseHandle]:
    store = FetchJobStore()
    job_id = store.create("bestblogs", status="pending")
    handle = store.adopt(job_id)
    assert handle is not None
    return store, job_id, handle


def test_heartbeat_keeps_renewing(fresh_db):
    store, job_id, handle = _pending_lease()
    before = store.get_raw(job_id)["lease_heartbeat_at"]

    stop, thread = _start_heartbeat(handle, "bestblogs", job_id, interval=0.01)
    try:
        assert _wait_for(
            lambda: store.get_raw(job_id)["lease_heartbeat_at"] != before
        )
        assert thread.is_alive()
    finally:
        stop.set()
        thread.join(timeout=2.0)


def test_heartbeat_survives_transient_errors(fresh_db, monkeypatch):
    _, job_id, handle = _pending_lease()
    real_heartbeat = leases.heartbeat
    calls = {"n": 0}

    def flaky(h):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise sqlite3.OperationalError("database is locked")
        return real_heartbeat(h)

    monkeypatch.setattr("moni.web.routes.fetch.leases.heartbeat", flaky)
    stop, thread = _start_heartbeat(
        handle, "bestblogs", job_id, interval=0.01, max_failures=3
    )
    try:
        assert _wait_for(lambda: calls["n"] >= 4)
        assert thread.is_alive()
    finally:
        stop.set()
        thread.join(timeout=2.0)


def test_heartbeat_exits_when_lease_is_lost(fresh_db):
    _, job_id, handle = _pending_lease()
    stop, thread = _start_heartbeat(handle, "bestblogs", job_id, interval=0.01)
    try:
        conn = get_db()
        conn.execute(
            "UPDATE fetch_jobs SET fence = fence + 1 WHERE id = ?", (job_id,)
        )
        conn.commit()
        assert _wait_for(lambda: not thread.is_alive())
    finally:
        stop.set()
        thread.join(timeout=2.0)
