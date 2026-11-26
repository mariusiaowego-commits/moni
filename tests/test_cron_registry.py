"""Tests for the cron registry (sprint-69): parse/format days, next run,
YAML seeding, revision bump, run queue and the /api/cron endpoints."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from moni.cron import registry
from moni.cron.scheduler import CST
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.web.server import create_app


@pytest.fixture
def db(tmp_path):
    set_db_path(tmp_path / "test_cron_registry.db")
    migrate()
    yield get_db()
    close_db()
    import moni.db.connection as conn_mod

    conn_mod._CON = None
    conn_mod._DB_PATH = None


@pytest.fixture
def client(db) -> TestClient:
    return TestClient(create_app())


class TestDayParsing:
    def test_ranges_and_lists(self):
        assert registry.parse_days("mon-fri") == [0, 1, 2, 3, 4]
        assert registry.parse_days("mon,wed,fri") == [0, 2, 4]
        assert registry.parse_days("*") == list(range(7))
        assert registry.parse_days("sat-sun") == [5, 6]

    def test_wrap_around_range(self):
        assert registry.parse_days("fri-mon") == [0, 4, 5, 6]

    def test_invalid(self):
        for bad in ("", "funday", "mon-", "-fri"):
            with pytest.raises(registry.CronRegistryError):
                registry.parse_days(bad)

    def test_format_roundtrip(self):
        assert registry.format_days([0, 1, 2, 3, 4]) == "mon-fri"
        assert registry.format_days(list(range(7))) == "*"
        assert registry.format_days([5, 6]) == "sat,sun"
        assert registry.format_days([0, 2]) == "mon,wed"


class TestNextRun:
    def test_same_day_when_time_ahead(self):
        now = datetime(2026, 11, 23, 8, 0, tzinfo=CST)  # Monday
        nxt = registry.next_run_at(9, 10, "mon-fri", now=now)
        assert nxt == datetime(2026, 11, 23, 9, 10, tzinfo=CST)

    def test_rolls_to_next_weekday(self):
        now = datetime(2026, 11, 27, 10, 0, tzinfo=CST)  # Friday after 09:10
        nxt = registry.next_run_at(9, 10, "mon-fri", now=now)
        assert nxt == datetime(2026, 11, 30, 9, 10, tzinfo=CST)  # Monday

    def test_disabled_day_not_selected(self):
        now = datetime(2026, 11, 28, 8, 0, tzinfo=CST)  # Saturday
        nxt = registry.next_run_at(9, 10, "mon-fri", now=now)
        assert nxt.weekday() == 0


class TestSeeding:
    def test_migration_seeds_seven_jobs(self, db):
        jobs = registry.list_jobs(db)
        assert [j["id"] for j in jobs] == [
            "extract_events_morning",
            "extract_events_am",
            "extract_events_noon",
            "extract_events_evening",
            "fetch_all_sources",
            "extract_cases_noon_post",
            "extract_cases_demo",
        ] or sorted(j["id"] for j in jobs) == sorted([
            "fetch_all_sources",
            "extract_events_morning",
            "extract_events_am",
            "extract_events_noon",
            "extract_events_evening",
            "extract_cases_noon_post",
            "extract_cases_demo",
        ])
        assert len(jobs) == 7

    def test_seed_from_yaml_is_idempotent(self, db, tmp_path):
        path = tmp_path / "schedule.yaml"
        path.write_text(
            "jobs:\n"
            "  - id: extra_job\n"
            "    name: 额外任务\n"
            "    trigger_type: cron\n"
            "    hour: 6\n"
            "    minute: 30\n"
            "    day_of_week: mon\n"
            "    func_path: moni.cron.jobs:extract_events_pending\n",
            encoding="utf-8",
        )
        assert registry.seed_from_yaml(db, path) == 0  # table already seeded
        assert registry.get_job(db, "extra_job") is None
        assert registry.seed_from_yaml(db, path, force=True) == 1


class TestUpdateJob:
    def test_update_bumps_revision_and_persists(self, db):
        before = registry.get_revision(db)
        updated = registry.update_job(
            db, "fetch_all_sources", {"hour": 10, "minute": 5, "day_of_week": "mon,wed"}
        )
        assert (updated["hour"], updated["minute"], updated["day_of_week"]) == (10, 5, "mon,wed")
        assert registry.get_revision(db) == before + 1

    def test_invalid_hour_rejected(self, db):
        with pytest.raises(registry.CronRegistryError):
            registry.update_job(db, "fetch_all_sources", {"hour": 24})

    def test_toggle_inactive(self, db):
        registry.update_job(db, "fetch_all_sources", {"is_active": False})
        assert registry.get_job(db, "fetch_all_sources")["is_active"] is False
        assert "fetch_all_sources" not in [j["id"] for j in registry.list_jobs(db, active_only=True)]


class TestRunQueue:
    def test_enqueue_and_duplicate(self, db):
        rid = registry.enqueue_run(db, "fetch_all_sources")
        assert rid > 0
        with pytest.raises(registry.AlreadyQueued):
            registry.enqueue_run(db, "fetch_all_sources")

    def test_unknown_job(self, db):
        with pytest.raises(registry.CronRegistryError):
            registry.enqueue_run(db, "nope")

    def test_claim_and_finish(self, db):
        rid = registry.enqueue_run(db, "fetch_all_sources")
        claimed = registry.claim_pending_requests(db)
        assert [c["id"] for c in claimed] == [rid]
        assert registry.claim_pending_requests(db) == []  # already claimed
        registry.finish_request(db, rid, error="boom")
        row = db.execute("SELECT status, error FROM cron_run_requests WHERE id = ?", (rid,)).fetchone()
        assert (row["status"], row["error"]) == ("failed", "boom")

    def test_stale_claim_released_at_startup(self, db):
        rid = registry.enqueue_run(db, "fetch_all_sources")
        registry.claim_pending_requests(db)
        with pytest.raises(registry.AlreadyQueued):
            registry.enqueue_run(db, "fetch_all_sources")

        assert registry.release_stale_claims(db, reason="scheduler restart") == [rid]

        row = db.execute(
            "SELECT status, error FROM cron_run_requests WHERE id = ?", (rid,)
        ).fetchone()
        assert (row["status"], row["error"]) == ("failed", "scheduler restart")
        assert registry.release_stale_claims(db, reason="scheduler restart") == []
        assert registry.enqueue_run(db, "fetch_all_sources") > rid


class TestCronApi:
    def test_get_jobs_shape(self, client):
        data = client.get("/api/cron/jobs").json()
        assert len(data) == 7
        job = next(j for j in data if j["id"] == "fetch_all_sources")
        assert job["schedule"] == {"hour": 9, "minute": 10, "day_of_week": "mon-fri"}
        assert job["func_path"] == "moni.cron.jobs:fetch_all_sources"
        assert job["next_run_at"] is not None

    def test_put_updates_and_bumps_revision(self, client, db):
        before = registry.get_revision(db)
        r = client.put("/api/cron/jobs/extract_events_evening", json={"hour": 19, "minute": 30})
        assert r.status_code == 200
        assert r.json()["schedule"]["hour"] == 19
        assert registry.get_revision(db) == before + 1

    def test_put_validation_and_404(self, client):
        assert client.put("/api/cron/jobs/fetch_all_sources", json={"hour": 99}).status_code == 422
        assert client.put("/api/cron/jobs/nope", json={"hour": 9}).status_code == 404

    def test_run_now_and_conflict(self, client):
        r = client.post("/api/cron/jobs/fetch_all_sources/run")
        assert r.status_code == 202
        assert r.json()["status"] == "queued"
        assert client.post("/api/cron/jobs/fetch_all_sources/run").status_code == 409
        assert client.post("/api/cron/jobs/nope/run").status_code == 404

    def test_status_reports_heartbeat(self, client, db):
        assert client.get("/api/cron/status").json()["running"] is False
        registry.write_heartbeat(db)
        data = client.get("/api/cron/status").json()
        assert data["running"] is True
        assert data["job_count"] == 7
        assert data["revision"] >= 1

    def test_runs_filter_by_job_id(self, client, db):
        now = datetime.now(CST).isoformat()
        db.execute(
            "INSERT INTO cron_runs (job_id, triggered_at, finished_at, success) VALUES (?,?,?,1)",
            ("fetch_all_sources", now, now),
        )
        db.execute(
            "INSERT INTO cron_runs (job_id, triggered_at, finished_at, success) VALUES (?,?,?,1)",
            ("extract_events_pending", now, now),
        )
        db.commit()
        data = client.get("/api/cron/runs", params={"job_id": "fetch_all_sources"}).json()
        assert [row["job_id"] for row in data] == ["fetch_all_sources"]

    def test_in_progress_run_reports_running(self, client, db):
        now = datetime.now(CST).isoformat()
        db.execute(
            "INSERT INTO cron_runs (job_id, triggered_at, finished_at, success, extra) "
            "VALUES (?,?,?,0,?)",
            ("fetch_all_sources", now, now, '{"in_progress": true}'),
        )
        db.commit()
        row = client.get("/api/cron/runs").json()[0]
        assert row["status"] == "running"
        assert row["success"] is False
        assert row["duration"] is None

    def test_jobs_report_running_state(self, client, db):
        """The job list must say which job is running right now."""
        now = datetime.now(CST).isoformat()
        db.execute(
            "INSERT INTO cron_runs (job_id, triggered_at, finished_at, success, extra) "
            "VALUES (?,?,?,0,?)",
            ("fetch_all_sources", now, now, '{"in_progress": true}'),
        )
        db.commit()
        jobs = {j["id"]: j for j in client.get("/api/cron/jobs").json()}
        assert jobs["fetch_all_sources"]["is_running"] is True
        assert jobs["fetch_all_sources"]["last_run_at"] is not None
        assert all(
            job["is_running"] is False
            for job_id, job in jobs.items()
            if job_id != "fetch_all_sources"
        )

    def test_jobs_running_state_ignores_malformed_extra(self, client, db):
        now = datetime.now(CST).isoformat()
        db.execute(
            "INSERT INTO cron_runs (job_id, triggered_at, finished_at, success, extra) "
            "VALUES (?,?,?,1,?)",
            ("fetch_all_sources", now, now, "not-json"),
        )
        db.commit()
        jobs = {j["id"]: j for j in client.get("/api/cron/jobs").json()}
        assert jobs["fetch_all_sources"]["is_running"] is False

    def test_legacy_post_is_gone(self, client):
        assert client.post("/api/cron/jobs", json={"name": "x", "schedule": "*"}).status_code == 410
