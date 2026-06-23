"""Tests for async fetch job tracking (v1.0.7.6).

Covers:
- FetchJobStore: CRUD, progress updates, terminal states
- /api/fetch/jobs/{id} endpoint: status query
- /api/fetch/jobs endpoint: list with optional status filter
- /api/fetch/sources/{name}/run: returns immediately with job_id
- /api/fetch/sources/{name}/run-sync: legacy synchronous endpoint still works
- 409 Conflict when source is already being fetched
"""
from __future__ import annotations

import time
from pathlib import Path
from threading import Thread

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.fetch.jobs import FetchJobStore


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "news.db"
    set_db_path(db_path)
    migrate()
    yield db_path
    close_db()


# ── FetchJobStore unit tests ───────────────────────────────────────────────


class TestFetchJobStore:
    def test_create_returns_uuid_and_running_status(self, fresh_db):
        store = FetchJobStore()
        job_id = store.create("36kr_rss", total_feeds=10)
        assert isinstance(job_id, str) and len(job_id) >= 32
        job = store.get(job_id)
        assert job is not None
        assert job["source"] == "36kr_rss"
        assert job["status"] == "running"
        assert job["total_feeds"] == 10
        assert job["done_feeds"] == 0
        assert job["articles_so_far"] == 0

    def test_update_progress_persists_counters(self, fresh_db):
        store = FetchJobStore()
        job_id = store.create("bestblog-yt")
        store.update_progress(
            job_id,
            done_feeds=18,
            current_feed="AI Engineer",
            articles_so_far=45,
            total_feeds=124,
        )
        job = store.get(job_id)
        assert job["done_feeds"] == 18
        assert job["current_feed"] == "AI Engineer"
        assert job["articles_so_far"] == 45
        assert job["total_feeds"] == 124

    def test_update_progress_increments_elapsed(self, fresh_db):
        store = FetchJobStore()
        job_id = store.create("36kr_rss")
        time.sleep(1.1)  # ensure elapsed_seconds >= 1
        store.update_progress(job_id, done_feeds=1)
        job = store.get(job_id)
        assert job["elapsed_seconds"] >= 1

    def test_mark_done_persists_result(self, fresh_db):
        store = FetchJobStore()
        job_id = store.create("36kr_rss")
        result = {"source": "36kr_rss", "fetched": 30, "inserted": 5}
        store.mark_done(job_id, result)
        job = store.get(job_id)
        assert job["status"] == "done"
        assert job["finished_at"] is not None
        assert job["result"] == result
        assert "result_json" not in job  # should be parsed into result

    def test_mark_failed_persists_error(self, fresh_db):
        store = FetchJobStore()
        job_id = store.create("bestblog-yt")
        store.mark_failed(job_id, "ConnectionError: upstream timed out")
        job = store.get(job_id)
        assert job["status"] == "failed"
        assert "ConnectionError" in job["error"]
        assert job["finished_at"] is not None

    def test_mark_timeout_persists_with_default_message(self, fresh_db):
        store = FetchJobStore()
        job_id = store.create("36kr_rss")
        store.mark_timeout(job_id)
        job = store.get(job_id)
        assert job["status"] == "timeout"
        assert "timeout" in job["error"].lower()

    def test_has_running_true_only_for_running_status(self, fresh_db):
        store = FetchJobStore()
        assert store.has_running() is False
        job_id = store.create("36kr_rss")
        assert store.has_running() is True
        assert store.has_running("36kr_rss") is True
        assert store.has_running("mpaypass") is False  # different source
        store.mark_done(job_id, {"fetched": 1})
        assert store.has_running() is False

    def test_list_recent_orders_newest_first(self, fresh_db):
        store = FetchJobStore()
        id1 = store.create("36kr_rss")
        time.sleep(0.01)
        id2 = store.create("mpaypass")
        time.sleep(0.01)
        id3 = store.create("bestblog-yt")
        recent = store.list_recent(limit=10)
        ids = [r["id"] for r in recent]
        assert ids[0] == id3  # newest first
        assert ids[-1] == id1

    def test_list_recent_filters_by_status(self, fresh_db):
        store = FetchJobStore()
        id1 = store.create("36kr_rss")
        store.mark_done(id1, {"fetched": 1})
        store.create("mpaypass")  # still running
        running = store.list_recent(limit=10, status="running")
        assert len(running) == 1
        assert running[0]["source"] == "mpaypass"
        done = store.list_recent(limit=10, status="done")
        assert len(done) == 1
        assert done[0]["source"] == "36kr_rss"


# ── HTTP endpoint tests ────────────────────────────────────────────────────


@pytest.fixture
def client(fresh_db, monkeypatch):
    """FastAPI TestClient with fresh DB."""
    from fastapi.testclient import TestClient
    from moni.web.server import create_app
    return TestClient(create_app())


class TestFetchJobsEndpoints:
    def test_run_returns_immediately_with_job_id(self, client, monkeypatch):
        """POST /api/fetch/sources/{name}/run must return immediately,
        not block for the full fetch duration."""
        # Patch moni.fetch.jobs.FetchJobStore._run_fetch_job to sleep 5s
        # so we can confirm the HTTP response is fast.
        import moni.web.routes.fetch as fetch_route
        original = fetch_route._run_fetch_job

        def slow_worker(*args, **kwargs):
            time.sleep(5)

        monkeypatch.setattr(fetch_route, "_run_fetch_job", slow_worker)

        start = time.monotonic()
        r = client.post("/api/fetch/sources/36kr_rss/run")
        elapsed = time.monotonic() - start

        # Should return well under the 5s sleep — within 1s.
        assert elapsed < 1.5, f"took {elapsed}s, should be near-instant"
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "running"
        assert "job_id" in data
        assert data["source"] == "36kr_rss"

    def test_get_job_returns_full_state(self, client):
        r = client.post("/api/fetch/sources/36kr_rss/run")
        job_id = r.json()["job_id"]
        g = client.get(f"/api/fetch/jobs/{job_id}")
        assert g.status_code == 200
        job = g.json()
        assert job["id"] == job_id
        assert job["source"] == "36kr_rss"
        assert job["status"] in ("running", "done", "failed", "timeout")

    def test_get_job_404_for_unknown(self, client):
        r = client.get("/api/fetch/jobs/does-not-exist")
        assert r.status_code == 404

    def test_list_jobs_returns_recent(self, client):
        client.post("/api/fetch/sources/36kr_rss/run")
        client.post("/api/fetch/sources/mpaypass/run")
        r = client.get("/api/fetch/jobs?limit=10")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 2
        assert "jobs" in data
        assert all("source" in j for j in data["jobs"])

    def test_list_jobs_filters_by_running_status(self, client):
        # First source: keep running
        client.post("/api/fetch/sources/36kr_rss/run")
        r = client.get("/api/fetch/jobs?status=running")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 1
        assert all(j["status"] == "running" for j in data["jobs"])

    def test_409_when_source_already_fetching(self, client, monkeypatch):
        """If a job for the same source is already running, second call
        returns 409 with running_job_id, not 200."""
        import moni.web.routes.fetch as fetch_route
        monkeypatch.setattr(
            fetch_route, "_run_fetch_job",
            lambda *a, **kw: time.sleep(2),
        )
        first = client.post("/api/fetch/sources/36kr_rss/run")
        assert first.status_code == 200
        # Second call while first is still running → 409
        second = client.post("/api/fetch/sources/36kr_rss/run")
        assert second.status_code == 409
        detail = second.json().get("detail", {})
        assert detail.get("error") == "fetch_in_progress"
        assert "running_job_id" in detail
        # Let the daemon thread finish before fixture teardown.
        import threading
        for t in threading.enumerate():
            if t is not threading.current_thread() and t.name.startswith("fetch-36kr_rss"):
                t.join(timeout=2.5)

    def test_run_sync_legacy_endpoint_still_blocks(self, client):
        """v1.0.7.6: /run-sync kept for CLI smoke tests — still blocks
        until done (preserves old behaviour)."""
        r = client.post("/api/fetch/sources/36kr_rss/run-sync")
        # 36kr_rss is fast (< 30s), should complete normally
        assert r.status_code == 200
        body = r.json()
        # Either done result or skipped
        assert body.get("source") == "36kr_rss"
        assert body.get("status") in ("done", "skipped") or "fetched" in body


# ── background-thread integration test ─────────────────────────────────────


def test_background_thread_writes_terminal_status(tmp_path, monkeypatch):
    """Confirm the worker thread actually runs the fetcher and persists
    terminal status into fetch_jobs (not just a stub)."""
    from moni.fetch.base import _FETCHER_REGISTRY

    # Stub fetcher that just marks the job done quickly.
    class StubFetcher:
        source_name = "stub-source"
        date_from = None
        date_to = None
        progress_callback = None

        def run(self):
            # Simulate 2 progress ticks then done.
            if self.progress_callback:
                self.progress_callback(total_feeds=2, done_feeds=0)
                self.progress_callback(done_feeds=1, current_feed="feed-1", articles_so_far=3)
                self.progress_callback(done_feeds=2, current_feed="feed-2", articles_so_far=7)
            return {"source": "stub-source", "fetched": 7, "parsed": 7, "inserted": 7}

    _FETCHER_REGISTRY["stub-source"] = StubFetcher

    # Set up DB + write a temporary sources.yaml so _resolve_source_name
    # can find stub-source.
    db_path = tmp_path / "news.db"
    set_db_path(db_path)
    migrate()

    sources_yaml = tmp_path / "sources.yaml"
    sources_yaml.write_text(
        "sources:\n"
        "- source_name: stub-source\n"
        "  type: rss\n"
        "  feed_url: http://stub.local/feed\n"
        "  fetch_interval_min: 5\n"
        "  enabled: true\n"
        "  crawler_type: rss\n",
        encoding="utf-8",
    )

    import moni.web.routes.fetch as fetch_route
    monkeypatch.setattr(fetch_route, "_CONFIG_PATH", sources_yaml)

    from fastapi.testclient import TestClient
    from moni.web.server import create_app

    c = TestClient(create_app())
    r = c.post("/api/fetch/sources/stub-source/run")
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    # Poll until done (worker is daemon, may take a moment)
    deadline = time.monotonic() + 5
    final = None
    while time.monotonic() < deadline:
        g = c.get(f"/api/fetch/jobs/{job_id}").json()
        if g["status"] in ("done", "failed", "timeout"):
            final = g
            break
        time.sleep(0.1)

    # Wait for the daemon thread to finish writing before tearing down DB,
    # otherwise it crashes with "no such table" warnings.
    import threading
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.name.startswith("fetch-stub-source"):
            t.join(timeout=2)

    close_db()
    assert final is not None, "job did not reach terminal state"
    assert final["status"] == "done"
    assert final["total_feeds"] == 2
    assert final["done_feeds"] == 2
    assert final["current_feed"] is None  # cleared on done
    assert final["articles_so_far"] == 7
    assert final["result"]["inserted"] == 7

    # cleanup registry so it doesn't leak into other tests
    _FETCHER_REGISTRY.pop("stub-source", None)


# ── v1.0.7.6 zombie sweep tests ────────────────────────────────────────────


class TestZombieSweep:
    """v1.0.7.6: jobs whose daemon thread died must not block new fetches.

    When a daemon thread crashes (e.g. SQLite contention during shutdown
    leaves a job in 'running' state forever), the next request must
    sweep those jobs to 'stale' so the user can retry.
    """

    def test_mark_stale_sets_status_and_error(self, fresh_db):
        store = FetchJobStore()
        job_id = store.create("36kr_rss")
        store.mark_stale(job_id)
        job = store.get(job_id)
        assert job["status"] == "stale"
        assert "Aborted" in job["error"]
        assert "daemon thread presumed dead" in job["error"]

    def test_sweep_zombies_only_targets_running(self, fresh_db):
        from datetime import datetime, timedelta, timezone
        store = FetchJobStore()
        old_job = store.create("36kr_rss")
        # Backdate started_at to 3h ago.
        old_start = (
            datetime.now(timezone.utc) - timedelta(hours=3)
        ).isoformat()
        get_db = __import__("moni.db.connection", fromlist=["get_db"]).get_db
        get_db().execute(
            "UPDATE fetch_jobs SET started_at = ? WHERE id = ?",
            (old_start, old_job),
        )
        get_db().commit()

        new_job = store.create("mpaypass")  # fresh, just now

        swept = store.sweep_zombies()
        assert swept == 1
        assert store.get(old_job)["status"] == "stale"
        assert store.get(new_job)["status"] == "running"

    def test_sweep_zombies_no_op_for_recent_jobs(self, fresh_db):
        store = FetchJobStore()
        store.create("36kr_rss")  # just started, way under threshold
        swept = store.sweep_zombies()
        assert swept == 0

    def test_has_running_sweeps_stale_before_check(self, fresh_db):
        """has_running() must call sweep_zombies() first so dead daemons
        don't lock out new fetches."""
        from datetime import datetime, timedelta, timezone
        store = FetchJobStore()
        old_job = store.create("36kr_rss")
        old_start = (
            datetime.now(timezone.utc) - timedelta(hours=3)
        ).isoformat()
        from moni.db.connection import get_db
        get_db().execute(
            "UPDATE fetch_jobs SET started_at = ? WHERE id = ?",
            (old_start, old_job),
        )
        get_db().commit()

        # Without sweep: would be True (job is 'running')
        assert store.has_running("36kr_rss") is False  # sweep clears it first
        # Stale, so a new fetch would be allowed
        assert store.has_running("36kr_rss") is False
