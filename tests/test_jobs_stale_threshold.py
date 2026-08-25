"""tests/test_jobs_stale_threshold.py — dynamic zombie-sweep threshold (v1.0.35.17).

Covers moni.fetch.jobs.get_stale_threshold / _global_max_timeout: the sweep
cutoff must stay ABOVE the largest configured per-source timeout budget so a
legit still-running job (e.g. bestblogs timeout_sec=1800) is never mis-swept as
a zombie — which would otherwise allow a duplicate full-fetch thread to start.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.expanduser("~/dev/moni"))

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.fetch.jobs import STALE_THRESHOLD_SECONDS, get_stale_threshold


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
        INSERT INTO sources (id, name, display_name, source_type, url,
                             fetch_interval_min, is_active, fetch_config,
                             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"src-{name}", name, name, "rss", "https://example.com",
            120, 1, fetch_config, now, now,
        ),
    )
    conn.commit()


def test_floor_when_no_timeout_configured():
    # No sources at all → global_max stays 600 → max(900, 600+300) = 900
    # (migrate() may seed a few sources but none with timeout_sec, so global stays 600)
    assert get_stale_threshold() == STALE_THRESHOLD_SECONDS == 900


def test_scales_with_largest_configured_timeout():
    conn = get_db()
    _seed_source(conn, "tt_bestblogs", json.dumps({"timeout_sec": 1800}))
    _seed_source(conn, "tt_36kr", json.dumps({"timeout_sec": 600}))
    assert get_stale_threshold() == 1800 + 300 == 2100


def test_ignores_below_floor_config():
    conn = get_db()
    _seed_source(conn, "tt_mpaypass", json.dumps({"timeout_sec": 300}))
    # 300+300=600 < 900 floor → stays 900
    assert get_stale_threshold() == STALE_THRESHOLD_SECONDS