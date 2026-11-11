"""Regression tests for migration 036 (legacy 36kr source id)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, _run_sql_file, migrate
from moni.db.seed_config import seed


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def v35_db(tmp_path):
    """Real v35 database, then step back so migrate() runs 036 for real.

    Everything from 36 upward is removed so this fixture keeps working as new
    migrations are added (037+); the assertions below are about 036 itself.
    """
    close_db()
    set_db_path(tmp_path / "test.db")
    migrate()
    conn = get_db()
    conn.execute("DELETE FROM schema_version WHERE version >= 36")
    conn.commit()
    yield conn
    close_db()


def _insert_legacy_source(*, tags=("ai-fintech",), with_filter=True) -> int | None:
    conn = get_db()
    now = _now()
    for tag in tags:
        conn.execute(
            "INSERT OR IGNORE INTO tag_definitions "
            "(name, description, color, created_at) VALUES (?, ?, ?, ?)",
            (tag, "test tag", "#fff", now),
        )
    conn.execute(
        """
        INSERT INTO sources
            (id, name, display_name, source_type, url,
             fetch_interval_min, is_active, notes, fetch_config,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("src-36kr_rss", "36kr_rss", "36氪 RSS", "rss",
         "https://36kr.com/feed", 5, 1, "legacy", None, now, now),
    )
    for tag in tags:
        conn.execute(
            "INSERT INTO source_tags (source_id, tag_name, created_at) "
            "VALUES (?, ?, ?)",
            ("src-36kr_rss", tag, now),
        )
    filter_id = None
    if with_filter:
        cur = conn.execute(
            """
            INSERT INTO source_keyword_filters
                (source_id, mode, match_field, keywords, is_active, notes,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("src-36kr_rss", "blacklist", "title+content",
             '["游戏"]', 1, "legacy filter", now, now),
        )
        filter_id = cur.lastrowid
    conn.commit()
    return filter_id


def _insert_article_and_hit(filter_id: int) -> None:
    conn = get_db()
    now = _now()
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("a036", "T", "https://example.com/a036", "36kr_rss", now),
    )
    conn.execute(
        """
        INSERT INTO article_keyword_hits
            (article_id, keyword, filter_id, hit_field, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("a036", "游戏", filter_id, "title+content", now),
    )
    conn.commit()


def test_migrate_normalizes_id_and_moves_relations(v35_db):
    filter_id = _insert_legacy_source()
    assert filter_id is not None
    _insert_article_and_hit(filter_id)

    migrate()  # real entry point, v35 -> v36

    conn = get_db()
    assert conn.execute(
        "SELECT COUNT(*) FROM sources WHERE id = 'src-36kr_rss'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT name FROM sources WHERE id = 'src-36kr-rss'"
    ).fetchone()["name"] == "36kr_rss"
    assert conn.execute(
        "SELECT tag_name FROM source_tags WHERE source_id = 'src-36kr-rss'"
    ).fetchone()["tag_name"] == "ai-fintech"
    moved_filter = conn.execute(
        "SELECT id FROM source_keyword_filters WHERE source_id = 'src-36kr-rss'"
    ).fetchone()
    assert moved_filter["id"] == filter_id
    assert conn.execute(
        "SELECT filter_id FROM article_keyword_hits WHERE article_id = 'a036'"
    ).fetchone()["filter_id"] == filter_id
    assert conn.execute(
        "SELECT MAX(version) FROM schema_version"
    ).fetchone()[0] == _CURRENT_VERSION


def test_seed_succeeds_after_migration(v35_db):
    _insert_legacy_source()
    migrate()
    seed()

    conn = get_db()
    assert conn.execute(
        "SELECT COUNT(*) FROM sources WHERE name = '36kr_rss'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM sources WHERE id = 'src-chinese-independent-blogs'"
    ).fetchone()[0] == 1


def test_merges_children_when_canonical_already_exists(v35_db):
    conn = get_db()
    now = _now()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, created_at, updated_at) "
        "VALUES ('src-36kr-rss', '36kr_rss_canonical', 'canonical', 'rss', "
        "'https://36kr.com/feed', 5, 1, ?, ?)",
        (now, now),
    )
    conn.commit()
    _insert_legacy_source(tags=("ai-fintech", "banking"))

    migrate()

    conn = get_db()
    assert conn.execute(
        "SELECT COUNT(*) FROM sources WHERE id = 'src-36kr_rss'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM source_tags WHERE source_id = 'src-36kr-rss'"
    ).fetchone()[0] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM source_keyword_filters "
        "WHERE source_id = 'src-36kr-rss'"
    ).fetchone()[0] == 1


def test_canonical_only_database_is_unchanged(v35_db):
    seed()
    before = {
        row["id"] for row in get_db().execute("SELECT id FROM sources").fetchall()
    }
    migrate()
    after = {
        row["id"] for row in get_db().execute("SELECT id FROM sources").fetchall()
    }
    assert after == before
    assert get_db().execute(
        "SELECT COUNT(*) FROM schema_cleanup_log "
        "WHERE migration = '036_normalize_36kr_source_id'"
    ).fetchone()[0] == 0


def test_cleanup_log_records_real_normalization(v35_db):
    _insert_legacy_source()
    migrate()
    assert get_db().execute(
        "SELECT COUNT(*) FROM schema_cleanup_log "
        "WHERE migration = '036_normalize_36kr_source_id'"
    ).fetchone()[0] == 1


def test_version_constant_is_current():
    # 036 is a historical migration; the constant tracks the latest file.
    assert _CURRENT_VERSION == 40


def test_failed_migration_rolls_back_and_connection_is_usable(v35_db, tmp_path):
    _insert_legacy_source()
    broken = tmp_path / "broken.sql"
    broken.write_text(
        """
BEGIN IMMEDIATE;
UPDATE sources SET display_name = 'changed' WHERE id = 'src-36kr_rss';
SELECT * FROM table_that_does_not_exist;
COMMIT;
""",
        encoding="utf-8",
    )

    with pytest.raises(Exception):
        _run_sql_file(get_db(), broken)

    conn = get_db()
    assert conn.in_transaction is False
    # The failed transaction must not leave partial writes behind.
    assert conn.execute(
        "SELECT display_name FROM sources WHERE id = 'src-36kr_rss'"
    ).fetchone()["display_name"] == "36氪 RSS"
