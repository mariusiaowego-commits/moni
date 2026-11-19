"""Re-fetch body upgrade rules in ``ArticleRepository.dedupe_insert``.

Failure modes pinned:

* a title-only row (empty body) never gains the body a later fetch delivers;
* a fuller re-fetch silently overwrites a body the user imported by hand;
* ``brief_body_md`` is treated as "a human edited this" — it is filled for
  every new row, so it must not act as an edit lock.
"""

from __future__ import annotations

import sqlite3

import pytest

from moni.db.connection import close_db, set_db_path
from moni.db.migrate import migrate
from moni.db.repository import ArticleRepository


@pytest.fixture
def tmp_db(tmp_path):
    set_db_path(tmp_path / "test.db")
    migrate()
    yield tmp_path / "test.db"
    close_db()
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod

    conn_mod._CON = None  # type: ignore[attr-defined]
    conn_mod._DB_PATH = None  # type: ignore[attr-defined]
    mig_mod._CON = None  # type: ignore[attr-defined]


def _insert(url: str, content: str) -> None:
    ArticleRepository().dedupe_insert([{
        "title": "t",
        "url": url,
        "source": "qbitai",
        "content": content,
    }])


def _row(db, url: str):
    with sqlite3.connect(db) as c:
        return c.execute(
            "SELECT raw_content, content_state, content_chars, summary, brief_body_md "
            "FROM articles WHERE url = ?",
            (url,),
        ).fetchone()


class TestUpgrade:
    def test_empty_body_is_replaced(self, tmp_db):
        _insert("https://e.com/a", "")
        assert not _row(tmp_db, "https://e.com/a")[0]

        _insert("https://e.com/a", "正文" * 40)
        body, state, chars, *_ = _row(tmp_db, "https://e.com/a")
        assert len(body) == 80
        assert state == "short"
        assert chars == 80

    def test_slightly_longer_body_does_not_replace(self, tmp_db):
        _insert("https://e.com/b", "字" * 300)
        _insert("https://e.com/b", "字" * 499)
        assert len(_row(tmp_db, "https://e.com/b")[0]) == 300

    def test_200_chars_longer_replaces(self, tmp_db):
        _insert("https://e.com/c", "字" * 300)
        _insert("https://e.com/c", "字" * 500)
        assert len(_row(tmp_db, "https://e.com/c")[0]) == 500

    def test_shorter_refetch_never_wins(self, tmp_db):
        _insert("https://e.com/d", "字" * 500)
        _insert("https://e.com/d", "短")
        assert len(_row(tmp_db, "https://e.com/d")[0]) == 500

    def test_brief_body_is_not_an_edit_lock(self, tmp_db):
        """Every new row gets ``brief_body_md``; it must not block the upgrade."""
        _insert("https://e.com/e", "")
        with sqlite3.connect(tmp_db) as c:
            c.execute(
                "UPDATE articles SET brief_body_md = 'x' WHERE url = ?",
                ("https://e.com/e",),
            )
            c.commit()

        _insert("https://e.com/e", "正文" * 60)
        assert len(_row(tmp_db, "https://e.com/e")[0]) == 120

    def test_upgrade_clears_derived_text(self, tmp_db):
        _insert("https://e.com/f", "")
        with sqlite3.connect(tmp_db) as c:
            c.execute(
                "UPDATE articles SET summary = '旧摘要', ai_summary = '旧AI摘要',"
                " has_ai_summary = 1 WHERE url = ?",
                ("https://e.com/f",),
            )
            c.commit()

        _insert("https://e.com/f", "正文" * 60)
        with sqlite3.connect(tmp_db) as c:
            row = c.execute(
                "SELECT summary, ai_summary, has_ai_summary FROM articles WHERE url = ?",
                ("https://e.com/f",),
            ).fetchone()
        assert row == (None, None, 0)

    def test_stored_body_is_cleaned_on_insert(self, tmp_db):
        _insert("https://e.com/g", "<p>正文</p>点击关注公众号「x」了解更多")
        body = _row(tmp_db, "https://e.com/g")[0]
        assert "<p>" not in body
        assert "点击关注" not in body
