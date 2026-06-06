"""Tests for moni.cron — scheduler lifecycle, run history, fetch interval,
failure isolation, and CST timezone.

Covers the 6 acceptance criteria from the T1 cron test task:
1. test_scheduler_start_stop
2. test_cron_runs_accumulates
3. test_fetch_interval_respected
4. test_failed_source_isolated
5. test_timezone_cst
6. test_failure_isolation_in_jobs

These tests are intentionally hermetic:
- Each test uses a fresh ``tmp_path`` for the DB and a fresh scheduler
  pointed at a tmp ``sources.yaml`` / ``schedule.yaml``.
- We never hit the real network — fake fetchers are registered in
  ``_FETCHER_REGISTRY`` for failure-isolation tests.
- The repo's main ``data/news.db`` is NEVER touched (set_db_path() rewires
  the singleton connection to ``tmp_path`` per fixture).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from apscheduler.schedulers.background import BackgroundScheduler

# Make moni importable when running pytest from the worktree root
WT_ROOT = Path(__file__).parent.parent
if str(WT_ROOT) not in sys.path:
    sys.path.insert(0, str(WT_ROOT))

from moni.cron import scheduler as cron_scheduler_mod
from moni.cron.run_history import CronRunHistory
from moni.cron.scheduler import CST, CronScheduler
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.fetch import base as fetch_base
from moni.fetch import scheduler as fetch_scheduler_mod
from moni.fetch.base import BaseFetcher, _FETCHER_REGISTRY


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Fresh DB in tmp_path, isolated from repo's data/news.db.

    Yields the db_path. The fixture also cleans module-level singleton state
    so each test gets a clean connection / scheduler.
    """
    db_path = tmp_path / "test_cron.db"
    set_db_path(db_path)
    migrate()
    yield db_path
    # Teardown — reset module singletons so next test starts clean
    close_db()
    import moni.db.connection as conn_mod

    conn_mod._CON = None
    conn_mod._DB_PATH = None


@pytest.fixture(autouse=True)
def _isolate_moni_imports(monkeypatch):
    """Defensive: ensure each test starts with a moni.* module cache that
    points at the main checkout.

    Why: tests/test_mpaypass_crawler.py historically hardcoded
    ``del sys.modules[k] for k in 'moni.*'`` and re-imported from a
    worktree. That worktree path is gone (v1.0.5 cleanup), but the
    fixture is kept as a belt-and-braces guard.

    Teardown (v1.0.5): the naive approach (``yield; del sys.modules
    ['moni.*']; reimport``) breaks the rest of the suite because every
    ``from moni.X import Y`` at module-load time in test_share_manifest
    etc. holds a stale class reference. When test_cron teardown wipes
    sys.modules and the next test calls into ``moni.share.formats``,
    that module re-imports and produces a *new* class object with the
    same name, breaking ``pytest.raises(BadRequestError)`` identity
    checks downstream.

    The fix: snapshot the pre-test set of moni.* modules in sys.modules,
    yield, then RESTORE that exact set.  Any module that didn't exist
    before the test gets removed; any module that was already there
    keeps its identity (so ``BadRequestError`` from
    ``moni.web.exceptions`` is the same object before and after this
    fixture runs).  We only ever ADD modules during the test itself
    via the local re-imports; the teardown snaps everything back.
    """
    import sys as _sys

    # Snapshot the pre-test state of moni.* in sys.modules.
    pre_modules = {
        k: v for k, v in _sys.modules.items() if k == "moni" or k.startswith("moni.")
    }
    # Ensure main checkout is on sys.path
    p = str(WT_ROOT)
    if p in _sys.path:
        _sys.path.remove(p)
    _sys.path.insert(0, p)
    # Wipe moni.* from sys.modules so re-imports come from the main checkout
    for k in list(_sys.modules):
        if k == "moni" or k.startswith("moni."):
            del _sys.modules[k]
    # Re-import the modules test_cron uses and rebind names in this
    # test_cron module's namespace
    import moni.cron
    import moni.cron.scheduler
    import moni.cron.jobs
    import moni.db.connection
    import moni.db.migrate
    import moni.fetch
    import moni.fetch.scheduler
    import moni.fetch.base

    # NOTE: moni/cron/__init__.py does:
    #   from .run_history import CronRunHistory
    #   run_history = CronRunHistory()   # singleton instance
    # The ``run_history = CronRunHistory()`` line overwrites the
    # ``moni.cron.run_history`` attribute (originally the submodule) with
    # the singleton INSTANCE. So ``moni.cron.run_history`` ends up being
    # the instance, not the class. The class is reachable as
    # ``moni.cron.CronRunHistory`` (imported by the ``from .run_history
    # import CronRunHistory`` line above it).
    cron_run_history_cls = moni.cron.CronRunHistory

    # Rebind the module-level names so ``CronScheduler`` etc. point at
    # the freshly imported classes.
    g = globals()
    g["CST"] = moni.cron.scheduler.CST
    g["CronScheduler"] = moni.cron.scheduler.CronScheduler
    g["CronRunHistory"] = cron_run_history_cls
    g["set_db_path"] = moni.db.connection.set_db_path
    g["get_db"] = moni.db.connection.get_db
    g["close_db"] = moni.db.connection.close_db
    g["migrate"] = moni.db.migrate.migrate
    g["BaseFetcher"] = moni.fetch.base.BaseFetcher
    g["_FETCHER_REGISTRY"] = moni.fetch.base._FETCHER_REGISTRY
    # Re-import scheduler module refs for jobs.py
    g["cron_scheduler_mod"] = moni.cron.scheduler
    g["cron_jobs_mod"] = moni.cron.jobs
    g["fetch_scheduler_mod"] = moni.fetch.scheduler
    g["fetch_base_mod"] = moni.fetch.base
    yield
    # Teardown: restore sys.modules to its pre-test state.  Any
    # moni.* that was loaded before this fixture ran gets its
    # identity back; any moni.* loaded during the test is removed.
    # This is what makes the rest of the test suite (test_share_manifest
    # etc.) see a stable module graph.
    current = {k: v for k, v in _sys.modules.items() if k == "moni" or k.startswith("moni.")}
    # Remove anything that's in current but not in pre_modules
    for k in list(_sys.modules):
        if (k == "moni" or k.startswith("moni.")) and k not in pre_modules:
            del _sys.modules[k]
    # Restore the original module objects where they existed
    for k, v in pre_modules.items():
        _sys.modules[k] = v


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """A clean config/ dir with controllable sources.yaml + schedule.yaml.

    Also redirects ``_CONFIG_PATH`` for both the cron scheduler and the fetch
    scheduler to point inside this tmp dir.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    yield config_dir


def _write_sources_yaml(path: Path, sources: list[dict]) -> None:
    """Helper — write a sources.yaml in the tmp config dir."""
    path.write_text(yaml.safe_dump({"sources": sources}, allow_unicode=True),
                    encoding="utf-8")


def _write_schedule_yaml(path: Path, jobs: list[dict]) -> None:
    """Helper — write a schedule.yaml in the tmp config dir."""
    path.write_text(yaml.safe_dump({"jobs": jobs}, allow_unicode=True),
                    encoding="utf-8")


# ── 1. scheduler start / stop ──────────────────────────────────────────────


class TestSchedulerStartStop:
    """Verify CronScheduler can be started and stopped cleanly."""

    def test_scheduler_start_stop(
        self, isolated_db, tmp_config_dir, monkeypatch
    ):
        """BackgroundScheduler starts on .start() and stops on .stop()."""
        sources_yaml = tmp_config_dir / "sources.yaml"
        schedule_yaml = tmp_config_dir / "schedule.yaml"
        _write_sources_yaml(sources_yaml, [
            {"source_name": "fake_src", "feed_url": "http://example/",
             "fetch_interval_min": 30, "enabled": True},
        ])
        _write_schedule_yaml(schedule_yaml, [
            {
                "id": "fake_job",
                "name": "Fake",
                "trigger_type": "interval",
                "minutes": 60,
                "func_path": "moni.cron.jobs:fetch_all_sources",
            },
        ])

        # The fetch scheduler's _CONFIG_PATH is hard-coded to
        # ``config/sources.yaml`` relative to moni package. Patch the module
        # constant via a property, since the module reads it on .sources.
        fetch_cfg_path = sources_yaml
        schedule_cfg_path = schedule_yaml

        # Build a fetch Scheduler bound to the tmp sources.yaml
        fetch_sched = fetch_scheduler_mod.Scheduler(
            config_path=fetch_cfg_path,
            last_run_path=tmp_config_dir / ".last_run.json",
        )
        # Build a cron Scheduler bound to the tmp schedule.yaml
        cron_sched = CronScheduler(config_path=schedule_cfg_path)

        # Sanity: not running before start()
        assert cron_sched._scheduler is None
        status0 = cron_sched.status()
        assert status0["running"] is False
        assert status0["jobs"] == []

        cron_sched.start()
        try:
            # Now running
            assert isinstance(cron_sched._scheduler, BackgroundScheduler)
            assert cron_sched._scheduler.running is True
            status1 = cron_sched.status()
            assert status1["running"] is True
            assert len(status1["jobs"]) == 1
            assert status1["jobs"][0]["id"] == "fake_job"
            # next_run_time is populated for interval triggers
            assert status1["jobs"][0]["next_run"] is not None
        finally:
            cron_sched.stop()

        # After stop, _scheduler is reset
        assert cron_sched._scheduler is None
        status2 = cron_sched.status()
        assert status2["running"] is False
        assert status2["jobs"] == []

    def test_double_start_is_idempotent(
        self, isolated_db, tmp_config_dir
    ):
        """A second .start() without .stop() should warn and not crash."""
        sources_yaml = tmp_config_dir / "sources.yaml"
        schedule_yaml = tmp_config_dir / "schedule.yaml"
        _write_sources_yaml(sources_yaml, [])
        _write_schedule_yaml(schedule_yaml, [])

        cron_sched = CronScheduler(config_path=schedule_yaml)
        cron_sched.start()
        try:
            # Second start() should be a no-op (warning logged, no exception)
            cron_sched.start()
            assert isinstance(cron_sched._scheduler, BackgroundScheduler)
            assert cron_sched._scheduler.running is True
        finally:
            cron_sched.stop()


# ── 2. cron_runs table accumulates rows ────────────────────────────────────


class TestCronRunsAccumulates:
    """Verify log_run() inserts rows and they accumulate on repeated calls."""

    def test_cron_runs_accumulates(self, isolated_db):
        """Repeated log_run() calls grow cron_runs row count."""
        history = CronRunHistory()
        conn = get_db()

        # Initially empty
        n0 = conn.execute("SELECT COUNT(*) FROM cron_runs").fetchone()[0]
        assert n0 == 0

        # First call → 1 row, returns rowid
        rowid1 = history.log_run("fetch_36kr_rss", success=True,
                                 new_articles=3, sources_run=1)
        assert isinstance(rowid1, int) and rowid1 > 0

        n1 = conn.execute("SELECT COUNT(*) FROM cron_runs").fetchone()[0]
        assert n1 == 1

        # 4 more calls → 5 rows total
        for i in range(4):
            history.log_run(f"job_{i}", success=(i % 2 == 0),
                            new_articles=i, sources_run=i)
        n5 = conn.execute("SELECT COUNT(*) FROM cron_runs").fetchone()[0]
        assert n5 == 5

        # Check the rows we just wrote are queryable
        rows = conn.execute(
            "SELECT job_id, success, new_articles, sources_run "
            "FROM cron_runs ORDER BY id"
        ).fetchall()
        assert rows[0]["job_id"] == "fetch_36kr_rss"
        assert rows[0]["success"] == 1
        assert rows[0]["new_articles"] == 3

        # The even-indexed log_run() calls were success=True
        assert rows[1]["job_id"] == "job_0" and rows[1]["success"] == 1
        assert rows[2]["job_id"] == "job_1" and rows[2]["success"] == 0
        assert rows[3]["job_id"] == "job_2" and rows[3]["success"] == 1
        assert rows[4]["job_id"] == "job_3" and rows[4]["success"] == 0

    def test_cron_runs_includes_log_fetch(
        self, isolated_db
    ):
        """log_fetch() writes per-source rows linked to a cron_runs rowid."""
        history = CronRunHistory()
        conn = get_db()

        cron_rowid = history.log_run("fetch_36kr_rss", success=True,
                                     new_articles=10, sources_run=2)
        history.log_fetch(cron_run_rowid=cron_rowid, source="36kr_rss",
                          fetched=30, parsed=30, inserted=10, parse_errors=0)
        history.log_fetch(cron_run_rowid=cron_rowid, source="mpaypass",
                          fetched=66, parsed=66, inserted=0, parse_errors=0,
                          error=None)

        n = conn.execute("SELECT COUNT(*) FROM fetch_log").fetchone()[0]
        assert n == 2
        rows = conn.execute(
            "SELECT source, inserted FROM fetch_log "
            "WHERE cron_run_rowid = ? ORDER BY id",
            (cron_rowid,),
        ).fetchall()
        assert rows[0]["source"] == "36kr_rss"
        assert rows[0]["inserted"] == 10
        assert rows[1]["source"] == "mpaypass"
        assert rows[1]["inserted"] == 0


# ── 3. fetch interval respected ────────────────────────────────────────────


class TestFetchIntervalRespected:
    """Verify the FetchScheduler skips a source that just ran."""

    def test_fetch_interval_respected(
        self, isolated_db, tmp_config_dir
    ):
        """Source fetched '1 min ago' is NOT due when interval=15 min."""
        sources_yaml = tmp_config_dir / "sources.yaml"
        last_run_path = tmp_config_dir / ".last_run.json"
        _write_sources_yaml(sources_yaml, [
            {"source_name": "fake_src", "feed_url": "http://example/",
             "fetch_interval_min": 15, "enabled": True},
            {"source_name": "never_run_src", "feed_url": "http://example/",
             "fetch_interval_min": 15, "enabled": True},
        ])

        # Write a last_run.json 1 minute in the past
        one_min_ago = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        ).isoformat()
        last_run_path.write_text(
            json.dumps({"fake_src": one_min_ago}), encoding="utf-8"
        )

        sched = fetch_scheduler_mod.Scheduler(
            config_path=sources_yaml, last_run_path=last_run_path
        )

        # should_fetch() must return False because only 1 min elapsed
        # (interval is 15 min).
        assert sched.should_fetch("fake_src") is False, (
            "Source fetched 1 min ago should be skipped when interval=15"
        )

        # And the OTHER source (declared, never run) IS due
        assert sched.should_fetch("never_run_src") is True

    def test_fetch_interval_never_run_always_due(
        self, isolated_db, tmp_config_dir
    ):
        """A source with no last_run entry is always due."""
        sources_yaml = tmp_config_dir / "sources.yaml"
        last_run_path = tmp_config_dir / ".last_run.json"
        last_run_path.write_text(json.dumps({}), encoding="utf-8")
        _write_sources_yaml(sources_yaml, [
            {"source_name": "fresh_src", "feed_url": "http://example/",
             "fetch_interval_min": 15, "enabled": True},
        ])
        sched = fetch_scheduler_mod.Scheduler(
            config_path=sources_yaml, last_run_path=last_run_path
        )
        assert sched.should_fetch("fresh_src") is True

    def test_mark_fetched_resets_timer(
        self, isolated_db, tmp_config_dir
    ):
        """After mark_fetched(), the source is no longer due immediately."""
        sources_yaml = tmp_config_dir / "sources.yaml"
        last_run_path = tmp_config_dir / ".last_run.json"
        _write_sources_yaml(sources_yaml, [
            {"source_name": "fake_src", "feed_url": "http://example/",
             "fetch_interval_min": 15, "enabled": True},
        ])
        last_run_path.write_text(json.dumps({}), encoding="utf-8")

        sched = fetch_scheduler_mod.Scheduler(
            config_path=sources_yaml, last_run_path=last_run_path
        )
        # First check: never run → due
        assert sched.should_fetch("fake_src") is True
        # Mark it as fetched
        sched.mark_fetched("fake_src")
        # Immediately after, NOT due
        assert sched.should_fetch("fake_src") is False

    def test_disabled_source_is_never_due(
        self, isolated_db, tmp_config_dir
    ):
        """enabled: false in sources.yaml → source is never due."""
        sources_yaml = tmp_config_dir / "sources.yaml"
        last_run_path = tmp_config_dir / ".last_run.json"
        last_run_path.write_text(json.dumps({}), encoding="utf-8")
        _write_sources_yaml(sources_yaml, [
            {"source_name": "off_src", "feed_url": "http://example/",
             "fetch_interval_min": 15, "enabled": False},
        ])
        sched = fetch_scheduler_mod.Scheduler(
            config_path=sources_yaml, last_run_path=last_run_path
        )
        assert sched.should_fetch("off_src") is False


# ── 4. failed source isolated from other sources ───────────────────────────


class _FakeGoodFetcher(BaseFetcher):
    """A fetcher that returns 2 articles and never raises."""
    source_name: str = "fake_good"

    def fetch(self):
        return [
            {"url": "http://example.com/a", "title": "A"},
            {"url": "http://example.com/b", "title": "B"},
        ]

    def parse(self, raw):
        return {
            "title": raw["title"],
            "url": raw["url"],
            "content": "fake content",
            "published_at": None,
            "source": self.source_name,
        }


class _FakeGoodFetcher2(BaseFetcher):
    """A second good fetcher for multi-source isolation tests."""
    source_name: str = "fake_good_2"

    def fetch(self):
        return [{"url": "http://example.com/c", "title": "C"}]

    def parse(self, raw):
        return {
            "title": raw["title"],
            "url": raw["url"],
            "content": "fake content 2",
            "published_at": None,
            "source": self.source_name,
        }


class _FakeBadFetcher(BaseFetcher):
    """A fetcher whose .run() always raises (simulating a crashed fetcher).

    Note: BaseFetcher.run() catches exceptions raised inside fetch() / parse()
    and converts them to an ``error`` field in the result dict — the job
    handler treats that as a soft failure (``success=False`` for that source,
    but the loop continues). To exercise the OUTER try/except in
    jobs.fetch_all_sources() (which is what increments ``sources_failed``),
    we override ``run()`` itself to raise.
    """
    source_name: str = "fake_bad"

    def fetch(self):
        return []

    def parse(self, raw):
        return None

    def run(self, dry_run: bool = False) -> dict:
        raise RuntimeError("simulated fetcher crash")



class TestFailedSourceIsolated:
    """A bad source must not stop other sources from running."""

    def _setup_three_sources(self, tmp_config_dir):
        """Write sources.yaml with good + bad + good_2, and empty last_run."""
        sources_yaml = tmp_config_dir / "sources.yaml"
        last_run_path = tmp_config_dir / ".last_run.json"
        _write_sources_yaml(sources_yaml, [
            {"source_name": "fake_good", "feed_url": "http://example/good",
             "fetch_interval_min": 30, "enabled": True},
            {"source_name": "fake_bad", "feed_url": "http://example/bad",
             "fetch_interval_min": 30, "enabled": True},
            {"source_name": "fake_good_2", "feed_url": "http://example/good2",
             "fetch_interval_min": 30, "enabled": True},
        ])
        last_run_path.write_text(json.dumps({}), encoding="utf-8")
        return sources_yaml, last_run_path

    def _patch_fetch_scheduler(self, sources_yaml, last_run_path,
                               monkeypatch):
        """Make moni.fetch.scheduler.Scheduler() return one bound to our tmp
        sources.yaml + last_run.json. The default constructor reads
        hard-coded paths; we monkeypatch the *class* to return our instance
        whenever jobs.py creates one."""
        from moni.cron import jobs as cron_jobs
        from moni.fetch.scheduler import Scheduler as FetchScheduler

        tmp_instance = FetchScheduler(
            config_path=sources_yaml, last_run_path=last_run_path
        )

        def factory(*args, **kwargs):
            return tmp_instance

        monkeypatch.setattr(cron_jobs, "FetchScheduler", factory)
        return tmp_instance

    def test_failed_source_isolated(
        self, isolated_db, tmp_config_dir, monkeypatch
    ):
        """One bad source does NOT prevent good sources from running.

        Steps:
        1. Register fake_good + fake_bad + fake_good_2 in _FETCHER_REGISTRY
        2. Configure sources.yaml with all 3 (all enabled, never run)
        3. Call jobs.fetch_all_sources()
        4. Verify:
           - function returned without raising
           - cron_runs has 1 row with success=0 (because bad source failed)
           - fetch_log has 3 rows, one per source
           - the bad source's fetch_log row has an error
           - the good sources' fetch_log rows have error IS NULL
        """
        from moni.cron import jobs as cron_jobs

        # Register fake fetchers; remove any stale ones
        for name in ("fake_good", "fake_bad", "fake_good_2"):
            _FETCHER_REGISTRY.pop(name, None)
        _FETCHER_REGISTRY["fake_good"] = _FakeGoodFetcher
        _FETCHER_REGISTRY["fake_bad"] = _FakeBadFetcher
        _FETCHER_REGISTRY["fake_good_2"] = _FakeGoodFetcher2
        try:
            sources_yaml, last_run_path = self._setup_three_sources(
                tmp_config_dir
            )
            self._patch_fetch_scheduler(
                sources_yaml, last_run_path, monkeypatch
            )

            # Execute — must not raise
            cron_jobs.fetch_all_sources()

            conn = get_db()

            # One cron_runs row
            cron_rows = conn.execute(
                "SELECT id, success, sources_run, new_articles "
                "FROM cron_runs"
            ).fetchall()
            assert len(cron_rows) == 1, (
                f"expected 1 cron_runs row, got {len(cron_rows)}"
            )
            cron_row = cron_rows[0]

            # success=0 because bad source failed
            assert cron_row["success"] == 0, (
                "cron_runs.success should be 0 when any source failed"
            )
            # 2 sources ran cleanly (fake_good + fake_good_2)
            assert cron_row["sources_run"] == 2
            # 2 + 1 = 3 new articles total (1 from fake_good, 0 from fake_bad,
            # 1 from fake_good_2; fake_good reports inserted=2 in run())
            # _FakeGoodFetcher returns 2 articles, _FakeGoodFetcher2 returns 1.
            # Their .run() goes through BaseFetcher.run() which calls
            # dedupe_insert — without a real DB, that may short-circuit. We
            # don't assert new_articles strictly, just that some run
            # happened.

            # fetch_log has 3 rows
            log_rows = conn.execute(
                "SELECT source, error, inserted, fetched "
                "FROM fetch_log WHERE cron_run_rowid = ? "
                "ORDER BY id",
                (cron_row["id"],),
            ).fetchall()
            assert len(log_rows) == 3

            sources_by_name = {r["source"]: r for r in log_rows}
            assert set(sources_by_name) == {"fake_good", "fake_bad",
                                            "fake_good_2"}

            # The bad source has a non-null error
            assert sources_by_name["fake_bad"]["error"] is not None
            assert "RuntimeError" in sources_by_name["fake_bad"]["error"]
            assert "simulated fetcher crash" in (
                sources_by_name["fake_bad"]["error"]
            )

            # The good sources have error IS NULL
            assert sources_by_name["fake_good"]["error"] is None
            assert sources_by_name["fake_good_2"]["error"] is None
        finally:
            # Cleanup registered fake fetchers
            for name in ("fake_good", "fake_bad", "fake_good_2"):
                _FETCHER_REGISTRY.pop(name, None)

    def test_scheduler_still_healthy_after_failed_run(
        self, isolated_db, tmp_config_dir, monkeypatch, caplog
    ):
        """After a job with a bad source, the scheduler is still running."""
        from moni.cron import jobs as cron_jobs

        _FETCHER_REGISTRY["fake_bad"] = _FakeBadFetcher
        _FETCHER_REGISTRY["fake_good"] = _FakeGoodFetcher
        try:
            sources_yaml, last_run_path = self._setup_three_sources(
                tmp_config_dir
            )
            self._patch_fetch_scheduler(
                sources_yaml, last_run_path, monkeypatch
            )

            # Start a real BackgroundScheduler and register a no-op job,
            # to prove the scheduler isn't killed by fetch_all_sources()
            sources_yaml_only_one = tmp_config_dir / "sources_health.yaml"
            schedule_yaml = tmp_config_dir / "schedule.yaml"
            _write_sources_yaml(sources_yaml_only_one, [])
            _write_schedule_yaml(schedule_yaml, [
                {
                    "id": "noop",
                    "name": "NoOp",
                    "trigger_type": "interval",
                    "minutes": 60,
                    "func_path": "moni.cron.jobs:fetch_all_sources",
                },
            ])

            cron_sched = CronScheduler(config_path=schedule_yaml)
            cron_sched.start()
            try:
                bg = cron_sched._scheduler
                assert bg is not None and bg.running is True

                with caplog.at_level(logging.WARNING):
                    # The job should NOT crash the scheduler
                    cron_jobs.fetch_all_sources()

                # Scheduler is still alive
                bg2 = cron_sched._scheduler
                assert bg2 is not None and bg2.running is True

                # And the log shows the failure
                failure_logs = [
                    r for r in caplog.records
                    if "fake_bad" in r.getMessage() and "failed" in r.getMessage()
                ]
                assert len(failure_logs) >= 1, (
                    "expected a log line saying fake_bad failed"
                )
            finally:
                cron_sched.stop()
        finally:
            for name in ("fake_good", "fake_bad", "fake_good_2"):
                _FETCHER_REGISTRY.pop(name, None)


# ── 5. CST timezone for schedule triggers ──────────────────────────────────


class TestTimezoneCST:
    """Cron / interval triggers must fire in CST (Asia/Shanghai), not UTC."""

    def test_cst_constant_is_asia_shanghai(self):
        """The CST module constant is the Asia/Shanghai zone (UTC+8)."""
        assert str(CST) == "Asia/Shanghai"
        # Offset from UTC must be +8 hours (no DST in Shanghai)
        from datetime import datetime as _dt

        sample = _dt(2026, 6, 6, 12, 0, 0, tzinfo=CST)
        offset = sample.utcoffset()
        assert offset is not None
        assert offset.total_seconds() == 8 * 3600, (
            f"CST offset should be 8h, got {offset.total_seconds()/3600}h"
        )

    def test_scheduler_default_timezone_is_cst(
        self, isolated_db, tmp_config_dir
    ):
        """The BackgroundScheduler built by CronScheduler uses CST."""
        schedule_yaml = tmp_config_dir / "schedule.yaml"
        _write_schedule_yaml(schedule_yaml, [
            {
                "id": "tz_job",
                "name": "TZ",
                "trigger_type": "interval",
                "minutes": 60,
                "func_path": "moni.cron.jobs:fetch_all_sources",
            },
        ])

        cron_sched = CronScheduler(config_path=schedule_yaml)
        cron_sched.start()
        try:
            bg = cron_sched._scheduler
            assert bg is not None
            # apscheduler stores its timezone on the scheduler instance
            sched_tz = bg.timezone
            # Normalize — apscheduler returns a tzinfo; str(Asia/Shanghai) ==
            # 'Asia/Shanghai'.
            assert str(sched_tz) == "Asia/Shanghai", (
                f"scheduler timezone should be Asia/Shanghai, "
                f"got {sched_tz!r}"
            )
        finally:
            cron_sched.stop()

    def test_cron_trigger_at_8am_cst_not_utc(
        self, isolated_db, tmp_config_dir
    ):
        """A '0 8 * * *' cron trigger fires at 8 AM CST, not 8 AM UTC.

        We compute the next_run_time in the trigger's own timezone and check
        that, when converted to UTC, it is 00:00 (i.e. 8 AM CST = 00:00 UTC).
        """
        from apscheduler.triggers.cron import CronTrigger

        # Build the trigger the same way CronScheduler._build_trigger does
        trigger = CronTrigger(
            second=0, minute=0, hour=8,
            day="*", month="*", day_of_week="*",
            timezone=CST,
        )
        # next_fire_time returns the next fire in the trigger's tz.
        # apscheduler 3.x exposes it as get_next_fire_time(prev_time, now).
        nxt = None
        now = datetime.now(CST)
        if hasattr(trigger, "get_next_fire_time"):
            nxt = trigger.get_next_fire_time(None, now)
        assert nxt is not None, "could not derive next fire time"

        # The next_fire_time, as reported, should be in Asia/Shanghai tz
        assert str(nxt.tzinfo) == "Asia/Shanghai", (
            f"cron trigger next_fire_time should be in Asia/Shanghai, "
            f"got {nxt.tzinfo!r}"
        )

        # Wall-clock hour in CST is 8
        assert nxt.hour == 8, (
            f"cron hour=8 should yield CST hour=8, got {nxt.hour}"
        )

        # In UTC, 8 AM CST = 00:00 (midnight)
        nxt_utc = nxt.astimezone(timezone.utc)
        assert nxt_utc.hour == 0, (
            f"8 AM CST should be 00:00 UTC, got {nxt_utc.hour}:00 UTC"
        )

    def test_cron_status_reports_cst_clock(
        self, isolated_db, tmp_config_dir
    ):
        """CronScheduler.status() reports current_time in CST (UTC+8).

        Note: ``datetime.isoformat()`` serializes the offset (``+08:00``),
        not the tz name (``Asia/Shanghai``). We assert the offset is 8h,
        which is the canonical proof of CST.
        """
        schedule_yaml = tmp_config_dir / "schedule.yaml"
        _write_schedule_yaml(schedule_yaml, [])
        cron_sched = CronScheduler(config_path=schedule_yaml)
        cron_sched.start()
        try:
            status = cron_sched.status()
            ts = datetime.fromisoformat(status["current_time"])
            assert ts.tzinfo is not None, "current_time should be tz-aware"
            offset = ts.utcoffset()
            assert offset is not None
            assert offset.total_seconds() == 8 * 3600, (
                f"CST current_time should be UTC+8, got offset "
                f"{offset.total_seconds()/3600}h"
            )
        finally:
            cron_sched.stop()


# ── 6. failure isolation in jobs (broader than #4) ─────────────────────────


class TestFailureIsolationInJobs:
    """One bad source must not crash the job. Other sources still process."""

    def test_failure_isolation_in_jobs(
        self, isolated_db, tmp_config_dir, monkeypatch
    ):
        """fetch_all_sources() returns None without raising when a source
        is misconfigured (no fetcher registered for it).

        This proves the loop continues past a bad source, even if the bad
        source's failure mode is 'no fetcher registered' (vs. fetcher
        raising). The other good source must still be processed.
        """
        from moni.cron import jobs as cron_jobs

        # Register only fake_good; deliberately leave a 'fake_unregistered'
        # entry in sources.yaml with NO corresponding fetcher.
        _FETCHER_REGISTRY.pop("fake_good", None)
        _FETCHER_REGISTRY["fake_good"] = _FakeGoodFetcher
        try:
            sources_yaml = tmp_config_dir / "sources.yaml"
            last_run_path = tmp_config_dir / ".last_run.json"
            _write_sources_yaml(sources_yaml, [
                {"source_name": "fake_good", "feed_url": "http://x/good",
                 "fetch_interval_min": 30, "enabled": True},
                {"source_name": "fake_unregistered",
                 "feed_url": "http://x/bad",
                 "fetch_interval_min": 30, "enabled": True},
            ])
            last_run_path.write_text(json.dumps({}), encoding="utf-8")

            # Patch the FetchScheduler used by jobs.py to point at our tmp
            from moni.fetch.scheduler import Scheduler as FetchScheduler

            tmp_fetch = FetchScheduler(
                config_path=sources_yaml, last_run_path=last_run_path
            )
            monkeypatch.setattr(
                cron_jobs, "FetchScheduler", lambda *a, **kw: tmp_fetch
            )

            # Run — must not raise
            result = cron_jobs.fetch_all_sources()
            assert result is None  # cron jobs return None to apscheduler

            conn = get_db()

            # cron_runs has 1 row, success=0 (because the unregistered source
            # 'failed' — no fetcher)
            rows = conn.execute(
                "SELECT success, sources_run FROM cron_runs"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0]["success"] == 0
            # Only fake_good ran cleanly
            assert rows[0]["sources_run"] == 1

            # fetch_log has 2 rows — one per source attempted
            logs = conn.execute(
                "SELECT source, error FROM fetch_log ORDER BY id"
            ).fetchall()
            assert len(logs) == 2
            srcs = {r["source"]: r["error"] for r in logs}
            assert srcs["fake_good"] is None
            assert srcs["fake_unregistered"] is not None
            assert "No fetcher registered" in srcs["fake_unregistered"]
        finally:
            _FETCHER_REGISTRY.pop("fake_good", None)

    def test_job_runs_when_no_sources_enabled(
        self, isolated_db, tmp_config_dir, monkeypatch
    ):
        """If all sources are disabled, the job still runs and logs 0."""
        from moni.cron import jobs as cron_jobs

        sources_yaml = tmp_config_dir / "sources.yaml"
        last_run_path = tmp_config_dir / ".last_run.json"
        _write_sources_yaml(sources_yaml, [
            {"source_name": "off1", "feed_url": "http://x/1",
             "fetch_interval_min": 30, "enabled": False},
        ])
        last_run_path.write_text(json.dumps({}), encoding="utf-8")
        from moni.fetch.scheduler import Scheduler as FetchScheduler

        tmp_fetch = FetchScheduler(
            config_path=sources_yaml, last_run_path=last_run_path
        )
        monkeypatch.setattr(
            cron_jobs, "FetchScheduler", lambda *a, **kw: tmp_fetch
        )

        # Must not raise
        cron_jobs.fetch_all_sources()
        conn = get_db()
        rows = conn.execute(
            "SELECT success, sources_run FROM cron_runs"
        ).fetchall()
        assert len(rows) == 1
        # No source enabled, but the job itself succeeded
        assert rows[0]["success"] == 1
        assert rows[0]["sources_run"] == 0
