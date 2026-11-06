"""Batch fetch worker bookkeeping (v1.0.67).

An aborted batch used to leave ``cron_runs`` marked success with
``sources_run`` counting every enabled source. These tests pin the abort
path and the normal path of ``_run_batch_job``.
"""
from __future__ import annotations

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.fetch import base as fetch_base
from moni.fetch.base import BaseFetcher
from moni.fetch.jobs import FetchJobStore
from moni.web.routes import fetch as fetch_routes


class _OkFetcher(BaseFetcher):
    source_name = "ok_a"

    def fetch(self):
        return [{"url": "http://example.com/a", "title": "A"}]

    def parse(self, raw):
        return {
            "title": raw["title"],
            "url": raw["url"],
            "content": "fake content",
            "published_at": None,
            "source": self.source_name,
        }


class _OkFetcher2(_OkFetcher):
    source_name = "ok_b"

    def fetch(self):
        return [{"url": "http://example.com/b", "title": "B"}]


@pytest.fixture
def fresh_db(tmp_path):
    set_db_path(tmp_path / "news.db")
    migrate()
    yield
    close_db()


def _queue(store: FetchJobStore, batch_id: str, names: list[str]) -> dict[str, str]:
    job_ids: dict[str, str] = {}
    for name in names:
        job_id = store.create(name, batch_id=batch_id, status="pending")
        store.update_progress(job_id, current_feed="(排队中)")
        job_ids[name] = job_id
    return job_ids


def _jobs_by_source() -> dict[str, dict]:
    rows = get_db().execute(
        "SELECT source, status, error FROM fetch_jobs"
    ).fetchall()
    return {row["source"]: dict(row) for row in rows}


def test_aborted_batch_is_not_recorded_as_success(fresh_db, monkeypatch):
    monkeypatch.setitem(fetch_base._FETCHER_REGISTRY, "ok_a", _OkFetcher)
    monkeypatch.setitem(fetch_base._FETCHER_REGISTRY, "ok_b", _OkFetcher2)

    calls = {"n": 0}
    real_start = fetch_routes._start_heartbeat

    def flaky_start(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("heartbeat exploded")
        return real_start(*args, **kwargs)

    monkeypatch.setattr(fetch_routes, "_start_heartbeat", flaky_start)

    store = FetchJobStore()
    batch_id = "batch-abort"
    job_ids = _queue(store, batch_id, ["ok_a", "ok_b"])
    enabled = [{"source_name": "ok_a"}, {"source_name": "ok_b"}]

    fetch_routes._run_batch_job(
        batch_id, enabled, None, "2026-11-06T00:00:00+08:00", job_ids
    )

    cron = get_db().execute(
        "SELECT success, sources_run, error, finished_at FROM cron_runs"
    ).fetchone()
    assert cron["success"] == 0
    assert cron["sources_run"] == 2
    assert "批次中断" in cron["error"]
    assert cron["finished_at"]

    jobs = _jobs_by_source()
    assert jobs["ok_a"]["status"] == "done"
    assert jobs["ok_b"]["status"] == "failed"
    assert "心跳线程启动失败" in jobs["ok_b"]["error"]


def test_successful_batch_closes_every_row(fresh_db, monkeypatch):
    monkeypatch.setitem(fetch_base._FETCHER_REGISTRY, "ok_a", _OkFetcher)
    monkeypatch.setitem(fetch_base._FETCHER_REGISTRY, "ok_b", _OkFetcher2)

    store = FetchJobStore()
    batch_id = "batch-ok"
    job_ids = _queue(store, batch_id, ["ok_a", "ok_b"])
    enabled = [{"source_name": "ok_a"}, {"source_name": "ok_b"}]

    fetch_routes._run_batch_job(
        batch_id, enabled, None, "2026-11-06T00:00:00+08:00", job_ids
    )

    cron = get_db().execute(
        "SELECT success, sources_run, error FROM cron_runs"
    ).fetchone()
    assert cron["success"] == 1
    assert cron["sources_run"] == 2
    assert cron["error"] is None

    jobs = _jobs_by_source()
    assert jobs["ok_a"]["status"] == "done"
    assert jobs["ok_b"]["status"] == "done"
