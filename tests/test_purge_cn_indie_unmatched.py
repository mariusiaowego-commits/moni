"""Tests for the one-off cn-indie cleanup script.

This is the only code path in the project that hard-deletes articles, so the
safety properties matter more than the happy path: it must never touch another
source, must refuse to run as a pass-through, and must be idempotent.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.db.seed_config import seed as seed_config

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts" / "purge_cn_indie_unmatched.py"
)
SOURCE = "chinese_independent_blogs"


@pytest.fixture
def purge_module():
    spec = importlib.util.spec_from_file_location("purge_cn_indie", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seeded(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    seed_config()
    conn = get_db()
    # One deterministic keyword set so the fixture does not depend on the
    # user-managed live keywords in tag_definitions.
    conn.execute(
        "UPDATE tag_definitions SET keywords = ? WHERE name = 'general-tech'",
        (json.dumps(["Kubernetes"], ensure_ascii=False),),
    )
    for tag in ("APP", "ai-fintech", "banking", "miniprogram",
                "model-providers", "payment", "regulation", "wechat",
                "workbuddy"):
        conn.execute(
            "UPDATE tag_definitions SET keywords = NULL WHERE name = ?", (tag,)
        )
    rows = [
        ("cn-keep", "Kubernetes 运维笔记", "https://example.com/keep"),
        ("cn-drop", "2024 北海道游记", "https://example.com/drop"),
    ]
    for aid, title, url in rows:
        conn.execute(
            "INSERT INTO articles (id, title, url, source, fetched_at) "
            "VALUES (?, ?, ?, ?, '2026-10-24')",
            (aid, title, url, SOURCE),
        )
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at) "
        "VALUES ('other-1', 'Kubernetes 别的源', 'https://example.com/other', "
        "'mpaypass', '2026-10-24')"
    )
    # Give the to-be-deleted row children so the cascade assertions below can
    # tell "cascade worked" apart from "there was nothing to cascade".
    conn.execute(
        "INSERT INTO article_tags (article_id, tag_name, predicted, created_at) "
        "VALUES ('cn-drop', 'banking', 0, '2026-10-24')"
    )
    conn.execute(
        "INSERT INTO article_keyword_hits "
        "(article_id, keyword, filter_id, hit_field, tag_name, created_at) "
        "VALUES ('cn-drop', '资本', NULL, 'title+content', 'banking', '2026-10-24')"
    )
    conn.commit()
    yield conn
    close_db()


def _run(module, db_path, monkeypatch, argv):
    monkeypatch.setattr("sys.argv", ["purge", "--db", str(db_path), *argv])
    return module.main()


def test_dry_run_writes_nothing(purge_module, seeded, tmp_path, monkeypatch):
    before = seeded.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    assert _run(purge_module, tmp_path / "news.db", monkeypatch, []) == 0
    assert seeded.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == before


def test_apply_deletes_only_unmatched_articles_of_this_source(
    purge_module, seeded, tmp_path, monkeypatch
):
    assert _run(purge_module, tmp_path / "news.db", monkeypatch, ["--apply"]) == 0
    remaining = {
        r["id"]
        for r in seeded.execute("SELECT id FROM articles").fetchall()
    }
    assert "cn-keep" in remaining
    assert "cn-drop" not in remaining
    # Other sources are never touched, even when they match no keyword.
    assert "other-1" in remaining
    # Deleting the article must cascade to its children; this only holds
    # because the script opens the connection with PRAGMA foreign_keys = ON.
    assert seeded.execute(
        "SELECT COUNT(*) FROM article_tags WHERE article_id = 'cn-drop'"
    ).fetchone()[0] == 0
    assert seeded.execute(
        "SELECT COUNT(*) FROM article_keyword_hits WHERE article_id = 'cn-drop'"
    ).fetchone()[0] == 0


def test_apply_writes_tags_and_hits_for_kept_articles(
    purge_module, seeded, tmp_path, monkeypatch
):
    assert _run(purge_module, tmp_path / "news.db", monkeypatch, ["--apply"]) == 0
    tags = {
        r["tag_name"]
        for r in seeded.execute(
            "SELECT tag_name FROM article_tags WHERE article_id = 'cn-keep'"
        ).fetchall()
    }
    assert tags == {"general-tech"}
    hits = {
        r["keyword"]
        for r in seeded.execute(
            "SELECT keyword FROM article_keyword_hits WHERE article_id = 'cn-keep'"
        ).fetchall()
    }
    assert hits == {"Kubernetes"}


def test_apply_is_idempotent(purge_module, seeded, tmp_path, monkeypatch):
    _run(purge_module, tmp_path / "news.db", monkeypatch, ["--apply"])
    count = seeded.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    assert _run(purge_module, tmp_path / "news.db", monkeypatch, ["--apply"]) == 0
    assert seeded.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == count


def test_refuses_to_run_when_no_bound_tag_has_keywords(
    purge_module, seeded, tmp_path, monkeypatch
):
    """Guard against re-introducing the pass-through: exit 2, delete nothing."""
    seeded.execute("UPDATE tag_definitions SET keywords = NULL")
    seeded.commit()
    before = seeded.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    assert _run(purge_module, tmp_path / "news.db", monkeypatch, ["--apply"]) == 2
    assert seeded.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == before


def test_kept_rows_keep_their_summary_data(purge_module, seeded, tmp_path, monkeypatch):
    """Only unmatched rows are removed; LLM output on kept rows survives."""
    seeded.execute(
        "UPDATE articles SET summary = 'llm summary' WHERE id = 'cn-keep'"
    )
    seeded.commit()
    _run(purge_module, tmp_path / "news.db", monkeypatch, ["--apply"])
    assert seeded.execute(
        "SELECT summary FROM articles WHERE id = 'cn-keep'"
    ).fetchone()["summary"] == "llm summary"
