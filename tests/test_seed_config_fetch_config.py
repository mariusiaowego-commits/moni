"""Regression tests for fetch_config preservation across seed()."""
from __future__ import annotations

import json

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.db.seed_config import seed
from moni.web.routes.fetch import JOB_TIMEOUT_SECONDS, _job_timeout


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    yield
    close_db()


def test_seed_persists_cn_indie_timeout_budget():
    seed()
    assert _job_timeout("chinese_independent_blogs") == 1200


@pytest.mark.parametrize(
    ("source_name", "expected"),
    [
        ("bestblogs", 2700),
        ("bestblog-podcast", 1800),
        ("bestblog-x", 1800),
        ("bestblog-yt", 1800),
        ("36kr_rss", JOB_TIMEOUT_SECONDS),
    ],
)
def test_fresh_seed_matches_ui_timeout_defaults(source_name, expected):
    seed()
    assert _job_timeout(source_name) == expected


@pytest.mark.parametrize(
    "source_name",
    ["bestblogs", "bestblog-podcast", "bestblog-x", "bestblog-yt"],
)
def test_seed_preserves_existing_custom_fetch_config(source_name):
    conn = get_db()
    seed()
    custom = json.dumps({
        "max_articles_total": 1900,
        "timeout_sec": 1800,
    })
    conn.execute(
        "UPDATE sources SET fetch_config = ? WHERE name = ?",
        (custom, source_name),
    )
    conn.commit()

    seed()

    stored = conn.execute(
        "SELECT fetch_config FROM sources WHERE name = ?",
        (source_name,),
    ).fetchone()["fetch_config"]
    assert json.loads(stored) == json.loads(custom)
    assert _job_timeout(source_name) == 1800
