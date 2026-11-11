"""Regression tests for migration 037 (cn-indie reuses existing business tags).

v1.0.60 shipped a dedicated ``independent-blog`` tag with an empty keyword
list.  That made the tag gate a pass-through AND left every ingested article
untagged (``dedupe_insert`` only writes tags that were hit).  037 drops the
one-off tag and rebinds the source to the existing business tags.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, _run_sql_file, migrate
from moni.db.seed_config import seed

SOURCE_ID = "src-chinese-independent-blogs"
SOURCE_NAME = "chinese_independent_blogs"
EXPECTED_TAGS = {
    "APP", "ai-fintech", "banking", "general-tech", "miniprogram",
    "model-providers", "payment", "regulation", "wechat", "workbuddy",
}
MIGRATION_037 = (
    Path(__file__).resolve().parents[1]
    / "moni" / "db" / "migrations" / "037_cn_indie_existing_tags.sql"
)


@pytest.fixture
def v36_db(tmp_path):
    """Seeded v36 database with the legacy keyword-less tag still present."""
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    seed()  # real source row so the legacy source_tags FK resolves
    conn = get_db()
    # Roll the marker back so migrate() runs 037 again on the legacy state.
    conn.execute("DELETE FROM schema_version WHERE version >= 37")
    conn.execute(
        "DELETE FROM source_tags WHERE source_id = ?", (SOURCE_ID,)
    )
    conn.execute(
        "INSERT OR IGNORE INTO tag_definitions "
        "(name, display_name, description, color, created_at, is_active) "
        "VALUES ('independent-blog', '独立博客', 'legacy', '#0F766E', "
        "'2026-10-24', 1)"
    )
    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) "
        "VALUES (?, 'independent-blog', '2026-10-24')",
        (SOURCE_ID,),
    )
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at) "
        "VALUES ('cn037a', 'T', 'https://example.com/cn037a', ?, '2026-10-24')",
        (SOURCE_NAME,),
    )
    conn.execute(
        "INSERT INTO article_tags (article_id, tag_name, predicted, created_at) "
        "VALUES ('cn037a', 'independent-blog', 0, '2026-10-24')"
    )
    conn.commit()
    yield conn
    close_db()


def test_037_runs_after_036(v36_db):
    migrate()
    assert get_db().execute(
        "SELECT MAX(version) FROM schema_version"
    ).fetchone()[0] == _CURRENT_VERSION == 40  # v1.0.70 bumped it to 040


def test_037_binds_source_to_existing_tags(v36_db):
    migrate()
    rows = get_db().execute(
        "SELECT tag_name FROM source_tags WHERE source_id = ?", (SOURCE_ID,)
    ).fetchall()
    assert {r["tag_name"] for r in rows} == EXPECTED_TAGS


def test_037_removes_legacy_tag_and_its_links(v36_db):
    migrate()
    conn = get_db()
    assert conn.execute(
        "SELECT COUNT(*) FROM tag_definitions WHERE name = 'independent-blog'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM article_tags WHERE tag_name = 'independent-blog'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM source_tags WHERE tag_name = 'independent-blog'"
    ).fetchone()[0] == 0
    # 037 only re-tags; it must never delete ingested content.
    assert conn.execute(
        "SELECT COUNT(*) FROM articles WHERE id = 'cn037a'"
    ).fetchone()[0] == 1


def test_037_is_idempotent(v36_db):
    migrate()
    migrate()  # schema already current -> early return
    _run_sql_file(get_db(), MIGRATION_037)
    assert get_db().execute(
        "SELECT COUNT(*) FROM source_tags WHERE source_id = ?", (SOURCE_ID,)
    ).fetchone()[0] == len(EXPECTED_TAGS)


def test_037_tolerates_unseeded_database(tmp_path):
    """A DB without the source or those tags must not fail the migration."""
    close_db()
    set_db_path(tmp_path / "bare.db")
    migrate()
    conn = get_db()
    conn.execute("DELETE FROM schema_version WHERE version >= 37")
    conn.execute("DELETE FROM source_tags")
    conn.execute("DELETE FROM sources WHERE id = ?", (SOURCE_ID,))
    conn.commit()
    migrate()
    assert get_db().execute(
        "SELECT MAX(version) FROM schema_version"
    ).fetchone()[0] == _CURRENT_VERSION
    close_db()


def test_037_cleanup_logged(v36_db):
    migrate()
    assert get_db().execute(
        "SELECT COUNT(*) FROM schema_cleanup_log "
        "WHERE migration = '037_cn_indie_existing_tags'"
    ).fetchone()[0] == 1


def test_037_does_not_log_success_when_bindings_are_missing(v36_db):
    """Without the ten tags the gate would silently fall back to pass-through.

    The migration must not report success in that case, otherwise a
    half-provisioned database looks migrated while every article passes.
    """
    conn = get_db()
    conn.execute("DELETE FROM source_tags")
    conn.execute("UPDATE tag_definitions SET is_active = 0")
    conn.execute("DELETE FROM tag_definitions WHERE name != 'banking'")
    conn.commit()

    _run_sql_file(conn, MIGRATION_037)

    assert conn.execute(
        "SELECT COUNT(*) FROM schema_cleanup_log "
        "WHERE migration = '037_cn_indie_existing_tags'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM source_tags WHERE source_id = ?", (SOURCE_ID,)
    ).fetchone()[0] < 10


def test_037_clears_legacy_tag_from_keyword_hits(v36_db):
    """article_keyword_hits.tag_name has no FK, so it needs an explicit delete."""
    conn = get_db()
    conn.execute(
        "INSERT INTO article_keyword_hits "
        "(article_id, keyword, filter_id, hit_field, tag_name, created_at) "
        "VALUES ('cn037a', '(bypass)', NULL, 'bypass', 'independent-blog', '2026-10-24')"
    )
    conn.commit()

    migrate()

    assert get_db().execute(
        "SELECT COUNT(*) FROM article_keyword_hits "
        "WHERE tag_name = 'independent-blog'"
    ).fetchone()[0] == 0


def test_seed_after_037_keeps_user_keywords(v36_db):
    """seed() must not blank keywords that were configured through /config."""
    conn = get_db()
    conn.execute(
        "UPDATE tag_definitions SET keywords = ? WHERE name = 'APP'",
        (json.dumps(["APP"], ensure_ascii=False),),
    )
    conn.commit()
    migrate()
    seed()
    got = get_db().execute(
        "SELECT keywords FROM tag_definitions WHERE name = 'APP'"
    ).fetchone()["keywords"]
    assert json.loads(got) == ["APP"]
