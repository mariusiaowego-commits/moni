"""tests/test_fetch_timeout_config.py — per-source timeout_sec budget (v1.0.35.17).

Covers moni.web.routes.fetch._job_timeout: the per-source job timeout added so
heavy sources (bestblogs full 375-feed sweep ~880s) no longer get falsely marked
'timeout after 10min' when they run past the global JOB_TIMEOUT_SECONDS (600).
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.expanduser("~/dev/moni"))

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.web.routes.fetch import JOB_TIMEOUT_SECONDS, _job_timeout


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Reset the database to a temp file before every test."""
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    migrate()
    yield
    close_db()
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod
    conn_mod._CON = None
    conn_mod._DB_PATH = None
    mig_mod._CON = None


def _seed_source(conn, name: str, fetch_config: str | None):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO sources (id, name, display_name, source_type, url,
                             fetch_interval_min, is_active, fetch_config,
                             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"src-{name}", name, name, "rss",
            f"https://example.com/{name}.xml", 120, 1, fetch_config,
            now, now,
        ),
    )
    conn.commit()


def test_reads_timeout_sec_from_config():
    conn = get_db()
    _seed_source(conn, "bestblogs", json.dumps({"max_articles_total": 1900, "timeout_sec": 1800}))
    assert _job_timeout("bestblogs") == 1800


def test_fallback_to_global_default_when_key_absent():
    conn = get_db()
    _seed_source(conn, "bestblogs", json.dumps({"max_articles_total": 1900}))
    assert _job_timeout("bestblogs") == JOB_TIMEOUT_SECONDS == 600


def test_fallback_to_global_default_when_no_config():
    conn = get_db()
    _seed_source(conn, "36kr_rss", None)
    assert _job_timeout("36kr_rss") == JOB_TIMEOUT_SECONDS


def test_invalid_timeout_sec_falls_back():
    conn = get_db()
    for bad in ("1800", "abc", -10, 0, None):
        _seed_source(conn, f"src-{type(bad).__name__}", json.dumps({"timeout_sec": bad}))
        assert _job_timeout(f"src-{type(bad).__name__}") == JOB_TIMEOUT_SECONDS


def test_unknown_source_falls_back():
    assert _job_timeout("does_not_exist") == JOB_TIMEOUT_SECONDS