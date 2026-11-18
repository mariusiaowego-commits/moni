"""Preview / apply / audit behaviour of ``moni.cleaning.service``.

The contract the page relies on:

* ``preview`` records a run but never touches an article body;
* ``apply`` is idempotent — a second run reports ``changed=0``;
* a body that would clean to empty is skipped, not written;
* orphan rows (a source that no longer exists) are archived to the basket,
  not destroyed, so the user can recover them.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from moni.cleaning.service import (
    apply_cleaning_run,
    audit,
    list_runs,
    load_run,
    overview,
    preview,
    report_markdown,
)
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.db"
    set_db_path(path)
    migrate()
    _seed_sources()
    yield path
    close_db()
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod

    conn_mod._CON = None  # type: ignore[attr-defined]
    conn_mod._DB_PATH = None  # type: ignore[attr-defined]
    mig_mod._CON = None  # type: ignore[attr-defined]


def _seed_sources() -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    for name, url in (("qbitai", "https://qbitai.com"), ("mpaypass", "https://mpaypass.com")):
        conn.execute(
            """
            INSERT INTO sources (id, name, display_name, source_type, url,
                                 fetch_interval_min, is_active, created_at, updated_at)
            VALUES (?, ?, ?, 'rss', ?, 30, 1, ?, ?)
            """,
            (f"src-{name}", name, name, url, now, now),
        )
    conn.commit()


def _add(article_id: str, source: str, body: str) -> None:
    conn = get_db()
    conn.execute(
        """
        INSERT INTO articles (id, title, url, source, fetched_at, raw_content)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            article_id, f"标题 {article_id}", f"https://e.com/{article_id}",
            source, datetime.now(timezone.utc).isoformat(), body,
        ),
    )
    conn.commit()


def _body(db, article_id: str) -> str:
    with sqlite3.connect(db) as c:
        return c.execute(
            "SELECT raw_content FROM articles WHERE id = ?", (article_id,)
        ).fetchone()[0]


class TestPreview:
    def test_preview_never_writes_a_body(self, db):
        _add("a1", "qbitai", "正文。点击关注公众号「x」了解更多")
        run = preview()
        assert run["mode"] == "preview"
        assert run["changed"] == 1
        assert _body(db, "a1") == "正文。点击关注公众号「x」了解更多"
        assert run["details"]["samples"][0]["after"] == "正文。"

    def test_preview_is_recorded_in_history(self, db):
        preview()
        assert len(list_runs()) == 1
        assert load_run(list_runs()[0]["id"])["mode"] == "preview"


class TestApply:
    def test_apply_writes_and_is_idempotent(self, db):
        _add("a1", "qbitai", "<p>正文</p>  点击关注公众号「x」了解更多")
        first = apply_cleaning_run()
        assert first["changed"] == 1
        assert _body(db, "a1") == "正文"

        second = apply_cleaning_run()
        assert second["changed"] == 0
        assert second["backfilled"] == 0
        assert _body(db, "a1") == "正文"

    def test_apply_fills_status_for_unchanged_rows(self, db):
        _add("a1", "qbitai", "字" * 300)
        run = apply_cleaning_run()
        assert run["changed"] == 0
        assert run["backfilled"] == 1
        with sqlite3.connect(db) as c:
            state, chars = c.execute(
                "SELECT content_state, content_chars FROM articles WHERE id = 'a1'"
            ).fetchone()
        assert state == "ok"
        assert chars == 300

    def test_guard_skips_body_that_would_run_empty(self, db):
        """250 chars of pure footer must not be rewritten into nothing."""
        body = "本文来源：某某公众号\n" * 25
        assert len(body) >= 200
        _add("a1", "qbitai", body)
        run = apply_cleaning_run()
        assert run["emptied"] == 1
        assert run["changed"] == 0
        assert _body(db, "a1") == body

    def test_derived_text_is_cleared(self, db):
        _add("a1", "qbitai", "正文。点击关注公众号「x」了解更多")
        conn = get_db()
        conn.execute(
            "UPDATE articles SET summary = 's', ai_summary = 'a', has_ai_summary = 1,"
            " brief_summary = 'b', brief_body_md = 'c' WHERE id = 'a1'"
        )
        conn.commit()
        apply_cleaning_run()
        with sqlite3.connect(db) as c:
            row = c.execute(
                "SELECT summary, ai_summary, has_ai_summary, brief_summary, brief_body_md"
                " FROM articles WHERE id = 'a1'"
            ).fetchone()
        summary, ai_summary, has_ai, brief_summary, brief_body = row
        assert (summary, ai_summary, has_ai, brief_summary) == (None, None, 0, None)
        # Layout is rebuilt from the cleaned body, not left stale.
        assert "正文。" in brief_body
        assert "点击关注" not in brief_body

    def test_backfill_keeps_derived_text(self, db):
        _add("a1", "qbitai", "字" * 300)
        conn = get_db()
        conn.execute(
            "UPDATE articles SET summary = 's', ai_summary = 'a', has_ai_summary = 1,"
            " brief_summary = 'b', brief_body_md = 'c' WHERE id = 'a1'"
        )
        conn.commit()
        run = apply_cleaning_run()
        assert run["changed"] == 0
        assert run["backfilled"] == 1
        with sqlite3.connect(db) as c:
            row = c.execute(
                "SELECT summary, ai_summary, has_ai_summary, brief_summary, brief_body_md"
                " FROM articles WHERE id = 'a1'"
            ).fetchone()
        assert row == ("s", "a", 1, "b", "c")

    def test_backfill_null_body_row_stamps_cleaner(self, db):
        conn = get_db()
        conn.execute(
            """
            INSERT INTO articles (id, title, url, source, fetched_at, raw_content)
            VALUES ('n1', '标题 n1', 'https://e.com/n1', 'qbitai', ?, NULL)
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.commit()
        run = apply_cleaning_run()
        assert run["backfilled"] >= 1
        with sqlite3.connect(db) as c:
            cleaner = c.execute(
                "SELECT content_cleaner FROM articles WHERE id = 'n1'"
            ).fetchone()[0]
        assert cleaner == "clean_v1"


class TestOrphanRows:
    def test_orphan_source_rows_are_archived_not_deleted(self, db):
        _add("t1", "testsrc", "article 0")
        run = apply_cleaning_run()
        assert run["archived_test_rows"] == 1
        with sqlite3.connect(db) as c:
            assert c.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0
            row = c.execute(
                "SELECT title, source, dropped_reason, dropped_by FROM basket"
            ).fetchone()
        assert row == ("标题 t1", "testsrc", "test_source", "orphan_source")

    def test_preview_only_counts_orphans(self, db):
        _add("t1", "testsrc", "article 0")
        run = preview()
        assert run["details"]["test_rows"] == 1
        assert run["archived_test_rows"] == 0
        with sqlite3.connect(db) as c:
            assert c.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1


class TestOverview:
    def test_states_and_pending(self, db):
        _add("a1", "qbitai", "字" * 300)
        _add("a2", "qbitai", "短")
        _add("a3", "qbitai", "")
        apply_cleaning_run()

        data = overview()
        assert data["total"] == 3
        assert data["states"] == {"ok": 1, "short": 1, "empty": 1}
        assert data["pending"] == 0
        assert data["test_rows"] == 0

    def test_pending_counts_rows_on_an_old_cleaner(self, db):
        _add("a1", "qbitai", "字" * 300)
        apply_cleaning_run()
        conn = get_db()
        conn.execute("UPDATE articles SET content_cleaner = 'clean_v0'")
        conn.commit()
        assert overview()["pending"] == 1

    def test_source_contract_decides_issue_count(self, db):
        _add("a1", "qbitai", "")
        apply_cleaning_run()
        rows = {r["source"]: r for r in audit()["sources"]}
        assert rows["qbitai"]["contract"] == "snippet"
        assert rows["qbitai"]["issues"] == 0

        conn = get_db()
        conn.execute("UPDATE sources SET content_contract = 'full_text' WHERE name = 'qbitai'")
        conn.commit()
        rows = {r["source"]: r for r in audit()["sources"]}
        assert rows["qbitai"]["issues"] == 1


class TestReport:
    def test_markdown_contains_the_numbers(self, db):
        _add("a1", "qbitai", "正文。点击关注公众号「x」了解更多")
        run = apply_cleaning_run()
        md = report_markdown(run)
        assert "数据清洗报告" in md
        assert "扫描：1 篇" in md
        assert "文本变化：1 篇" in md
        assert "公众号尾巴" in md
        assert "幂等" in md

    def test_samples_carry_before_and_after(self, db):
        _add("a1", "qbitai", "正文。点击关注公众号「x」了解更多")
        run = apply_cleaning_run()
        sample = run["details"]["samples"][0]
        assert sample["before"].startswith("正文。点击关注")
        assert sample["after"] == "正文。"
        assert json.loads(json.dumps(sample))["id"] == "a1"
