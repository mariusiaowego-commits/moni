"""Tests for moni.db — schema + migrate."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary DB and clean up after."""
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    yield db_path
    close_db()
    # Reset module-level state for next test
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod
    conn_mod._CON = None
    conn_mod._DB_PATH = None
    mig_mod._CON = None


class TestSchema:
    """Verify schema is applied correctly."""

    def test_all_tables_exist(self, tmp_db):
        migrate()
        conn = get_db()
        tables = [
            "articles",
            "tag_definitions",
            "article_tags",
            "share_tasks",
            "schema_version",
        ]
        for t in tables:
            row = conn.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{t}'"
            ).fetchone()
            assert row is not None, f"table {t} not found"

    def test_articles_columns(self, tmp_db):
        migrate()
        conn = get_db()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(articles)")]
        expected = ["id", "title", "url", "source", "publisher",
                    "published_at", "fetched_at", "raw_content",
                    "summary", "summary_at",
                    "fact_check_conclusion", "fact_check_method",
                    "fact_checked_at", "fact_check_note"]
        for c in expected:
            assert c in cols, f"column {c} missing from articles"

    def test_fts5_virtual_table_exists(self, tmp_db):
        migrate()
        conn = get_db()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='articles_fts'"
        ).fetchone()
        assert row is not None

    def test_indexes_exist(self, tmp_db):
        migrate()
        conn = get_db()
        indexes = [
            "idx_articles_fetched_at",
            "idx_articles_published_at",
            "idx_articles_source",
            "idx_article_tags_tag",
            "idx_share_tasks_status",
        ]
        for idx in indexes:
            row = conn.execute(
                f"SELECT name FROM sqlite_master WHERE type='index' AND name='{idx}'"
            ).fetchone()
            assert row is not None, f"index {idx} not found"


class TestMigrate:
    """Test migration behaviour."""

    def test_idempotent(self, tmp_db):
        migrate()
        migrate()  # must not fail
        conn = get_db()
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        assert version == 12  # current schema version (v1.0.5.23: +bestblogs fetch_config)

    def test_foreign_keys_enabled(self, tmp_db):
        migrate()
        conn = get_db()
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1

    def test_schema_version_recorded(self, tmp_db):
        migrate()
        conn = get_db()
        row = conn.execute("SELECT version, applied_at FROM schema_version").fetchone()
        assert row["version"] == 12  # current schema version (v1.0.5.23)
        assert row["applied_at"] is not None


class TestCRUD:
    """Basic CRUD sanity checks (no FTS5 triggers yet — those need F-Fetch)."""

    def test_insert_article(self, tmp_db):
        migrate()
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO articles (id, title, url, source, fetched_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("test1", "Test Title", "https://example.com/1", "test_src", now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM articles WHERE id='test1'").fetchone()
        assert row["title"] == "Test Title"

    def test_unique_source_url(self, tmp_db):
        migrate()
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO articles (id, title, url, source, fetched_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("test2", "Title", "https://ex.com/x", "src", now),
        )
        with pytest.raises(Exception):  # UNIQUE(source, url) violation
            conn.execute(
                """INSERT INTO articles (id, title, url, source, fetched_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("test3", "Title", "https://ex.com/x", "src", now),
            )

    def test_tag_definitions_crud(self, tmp_db):
        migrate()
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO tag_definitions (name, description, color, created_at) VALUES (?, ?, ?, ?)",
            ("AI", "大模型", "#9B59B6", now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM tag_definitions WHERE name='AI'"
        ).fetchone()
        assert row["color"] == "#9B59B6"

    def test_article_tags_cascade_delete(self, tmp_db):
        migrate()
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO articles (id, title, url, source, fetched_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("art1", "T", "https://x.com/1", "s", now),
        )
        conn.execute(
            "INSERT INTO tag_definitions (name, description, color, created_at) VALUES (?, ?, ?, ?)",
            ("Tag1", "desc", "#fff", now),
        )
        conn.execute(
            "INSERT INTO article_tags (article_id, tag_name, predicted, created_at) VALUES (?, ?, ?, ?)",
            ("art1", "Tag1", 1, now),
        )
        conn.commit()
        # Delete article → tags should cascade
        conn.execute("DELETE FROM articles WHERE id='art1'")
        conn.commit()
        remaining = conn.execute("SELECT * FROM article_tags WHERE article_id='art1'").fetchall()
        assert len(remaining) == 0
