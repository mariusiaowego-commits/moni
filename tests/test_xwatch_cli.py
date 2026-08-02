"""Tests for `moni xwatch ingest` CLI — happy path, tag-miss, dedupe, dry-run.

These exercise the CLI handler directly (no subprocess) so the suite
stays fast and free of network dependencies.  All four fixtures live
under tests/fixtures/.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.expanduser("~/dev/moni"))

from moni.cli.main import cmd_xwatch_ingest
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Per-test DB file — see AGENTS.md "Test 隔离陷阱"."""
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


def _seed_xwatch_source(conn, *, with_keywords: bool = True) -> None:
    """Insert xwatch source + bound tags (wechat, miniprogram) with keywords."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO sources (id, name, display_name, source_type, url,
                                fetch_interval_min, is_active, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
        ("src-xwatch", "xwatch", "X 关键词监控", "x", "", 30, now, now),
    )

    tag_kw = (
        json.dumps(["微信小程序", "服务通知", "订阅消息"], ensure_ascii=False)
        if with_keywords
        else None
    )
    for name, kw in [("wechat", tag_kw), ("miniprogram", tag_kw)]:
        conn.execute(
            """INSERT INTO tag_definitions
               (name, display_name, description, color, keywords, created_at, is_active)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (name, name, name, "#000", kw, now),
        )

    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) VALUES (?, ?, ?)",
        ("src-xwatch", "wechat", now),
    )
    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) VALUES (?, ?, ?)",
        ("src-xwatch", "miniprogram", now),
    )
    conn.commit()


@pytest.fixture
def patch_syndication(monkeypatch):
    """Patch the syndication call inside cmd_xwatch_ingest. Returns a callable
    that lets each test dictate which tweet (or failure) the CLI sees.

    cmd_xwatch_ingest does a lazy ``from ..fetch.xwatch_syndication import
    fetch_tweet`` inside its body, so we patch at the source module path
    (``moni.fetch.xwatch_syndication.fetch_tweet``) to make the local
    ``fetch_tweet`` binding pick up our fake.
    """
    holder: dict = {"return_value": None, "side_effect": None}

    def fake_fetch(tid, *, timeout=20):
        if holder["side_effect"]:
            raise holder["side_effect"]
        return holder["return_value"]

    monkeypatch.setattr("moni.fetch.xwatch_syndication.fetch_tweet", fake_fetch)
    return holder


# ── invalid input ───────────────────────────────────────────────────────


class TestInvalidInput:
    def test_unparseable_url_returns_1(self, capsys, patch_syndication):
        """Garbage input → exit 1 + ERROR message; no DB write."""
        patch_syndication["return_value"] = None
        rc = cmd_xwatch_ingest("not a url at all")
        out = capsys.readouterr().out
        assert rc == 1
        assert "ERROR" in out

    def test_syndication_failure_returns_1(self, capsys, patch_syndication):
        """Tweet id parses but syndication returns None → exit 1, no DB write."""
        patch_syndication["return_value"] = None
        rc = cmd_xwatch_ingest("2083475663496044761")
        out = capsys.readouterr().out
        assert rc == 1
        assert "ERROR" in out
        # DB empty
        assert get_db().execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0


# ── happy path: ingest persists + tags applied ──────────────────────────


class TestHappyPath:
    def test_ingest_writes_article_and_tags(self, capsys, patch_syndication):
        _seed_xwatch_source(get_db())
        sample = json.loads((FIX / "tweet_reply_sample.json").read_text(encoding="utf-8"))
        patch_syndication["return_value"] = sample

        rc = cmd_xwatch_ingest(
            "https://x.com/xiaoyangX026/status/2083475663496044761",
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "inserted=1" in out
        assert "2083475663496044761" in out

        # Verify the article row
        row = get_db().execute(
            "SELECT url, publisher, source FROM articles WHERE source='xwatch'"
        ).fetchone()
        assert row is not None
        assert row["source"] == "xwatch"
        assert row["publisher"] == "@xiaoyangX026"

        # At least one tag binding should exist (wechat or miniprogram).
        tag_rows = get_db().execute(
            """SELECT t.tag_name FROM articles a
               JOIN article_tags t ON t.article_id=a.id
               WHERE a.source='xwatch'"""
        ).fetchall()
        tag_names = {r["tag_name"] for r in tag_rows}
        assert {"wechat", "miniprogram"} & tag_names

    def test_ingest_dedupes_on_repeat_run(self, capsys, patch_syndication):
        """Second identical ingest → inserted=0 (INSERT OR IGNORE)."""
        _seed_xwatch_source(get_db())
        sample = json.loads((FIX / "tweet_reply_sample.json").read_text(encoding="utf-8"))
        patch_syndication["return_value"] = sample

        rc1 = cmd_xwatch_ingest("https://x.com/xiaoyangX026/status/2083475663496044761")
        rc2 = cmd_xwatch_ingest("https://x.com/xiaoyangX026/status/2083475663496044761")

        out1 = capsys.readouterr().out
        assert "inserted=1" in out1
        # Second call still rc=0, but inserted=0
        assert rc2 == 0
        # Only one row in DB
        n = get_db().execute(
            "SELECT COUNT(*) FROM articles WHERE source='xwatch'",
        ).fetchone()[0]
        assert n == 1


# ── tag-miss path: no DB write, exit 0, SKIP notice ────────────────────


class TestTagMiss:
    def test_no_tag_hit_skips_persist(self, capsys, patch_syndication, monkeypatch, tmp_path):
        """Tweet text has no wechat/miniprogram keyword → SKIP notice, no DB row."""
        _seed_xwatch_source(get_db())
        # Tweet with no relevant keywords
        patch_syndication["return_value"] = {
            "id_str": "8888888888888888888",
            "text": "Completely unrelated: I love coffee.",
            "user": {"screen_name": "coffee_lover"},
            "created_at": "Wed Oct 22 21:30:00 +0000 2026",
        }

        rc = cmd_xwatch_ingest("https://x.com/coffee_lover/status/8888888888888888888")
        out = capsys.readouterr().out

        assert rc == 0  # exit 0 = nothing went wrong, just skipped
        assert "SKIP" in out
        n = get_db().execute(
            "SELECT COUNT(*) FROM articles WHERE source='xwatch'",
        ).fetchone()[0]
        assert n == 0


# ── dry-run: no DB write regardless of tag outcome ──────────────────────


class TestDryRun:
    def test_dry_run_writes_nothing(self, capsys, patch_syndication):
        """--dry-run: prints intent, no article inserted."""
        _seed_xwatch_source(get_db())
        sample = json.loads((FIX / "tweet_reply_sample.json").read_text(encoding="utf-8"))
        patch_syndication["return_value"] = sample

        rc = cmd_xwatch_ingest(
            "https://x.com/xiaoyangX026/status/2083475663496044761",
            dry_run=True,
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "[dry-run]" in out
        assert "would insert" in out
        n = get_db().execute(
            "SELECT COUNT(*) FROM articles WHERE source='xwatch'",
        ).fetchone()[0]
        assert n == 0
