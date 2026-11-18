"""Unit tests for moni.fetch.cleaner — HTML → plain text conversion.

The cleaner is the v1.0.3 fix for 36kr_rss (and any other future source)
storing concatenated HTML in articles.raw_content. These tests cover the
expected contract:

* Dropped tags (script / style / nav / aside / footer) lose their content.
* Block tags (p / h1-6 / li / div) each emit a paragraph.
* Whitespace is collapsed and paragraphs are joined with blank lines.
* Falsy / non-string input → empty string (no exceptions).
* Real 36kr_rss-style input (multiple articles in one blob) round-trips
  to a readable plain-text blob.
* Integration: ArticleRepository.clean_all_html() rewrites raw_content
  for in-DB articles that contain markup and is idempotent.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from moni.db.connection import close_db, set_db_path
from moni.db.migrate import migrate
from moni.db.repository import ArticleRepository
from moni.fetch.cleaner import extract_text


# ── pure unit tests on extract_text() ───────────────────────────────────────


class TestExtractTextHappyPath:
    """The basic contract: HTML in, plain text out."""

    def test_simple_title_and_body(self):
        """Canonical case from the task body spec."""
        html = "<h2>Title</h2><p>body</p><script>x</script>"
        result = extract_text(html)
        assert "Title" in result
        assert "body" in result
        assert "<" not in result
        assert ">" not in result
        assert "x" not in result  # script content dropped

    def test_empty_string_returns_empty(self):
        assert extract_text("") == ""

    def test_none_returns_empty(self):
        assert extract_text(None) == ""  # type: ignore[arg-type]

    def test_non_string_returns_empty(self):
        assert extract_text(123) == ""  # type: ignore[arg-type]
        assert extract_text(["a", "b"]) == ""  # type: ignore[arg-type]

    def test_paragraphs_separated_by_blank_line(self):
        html = "<p>first paragraph</p><p>second paragraph</p>"
        result = extract_text(html)
        # Paragraphs should be separated by a blank line.
        assert "first paragraph" in result
        assert "second paragraph" in result
        assert "first paragraph\n\nsecond paragraph" in result

    def test_whitespace_collapsed(self):
        """Multiple spaces, tabs, and newlines inside a paragraph become one space."""
        html = "<p>  hello\tworld\n\nfoo  </p>"
        result = extract_text(html)
        assert "hello world foo" in result
        # No double-spaces, no tabs, no leading/trailing whitespace on the
        # paragraph itself.
        assert "  " not in result
        assert "\t" not in result

    def test_inline_formatting_unwrapped(self):
        """<strong>, <b>, <em>, <i>, <a>, <span> keep their text."""
        html = "<p><strong>headline</strong> and <em>emphasis</em></p>"
        result = extract_text(html)
        assert "headline and emphasis" in result
        assert "<" not in result


class TestExtractTextDroppedTags:
    """Tags whose entire subtree is discarded."""

    @pytest.mark.parametrize("tag", [
        "script", "style", "noscript", "nav", "aside", "footer", "header",
    ])
    def test_chrome_tags_dropped(self, tag):
        html = f"<p>keep me</p><{tag}>drop me</{tag}><p>keep me too</p>"
        result = extract_text(html)
        assert "keep me" in result
        assert "drop me" not in result

    def test_nested_script_in_p_dropped(self):
        """Even when a <script> is nested inside another element, its content
        should not appear in the output."""
        html = "<div><p>safe<p><script>evil()</script></p></div>"
        result = extract_text(html)
        assert "safe" in result
        assert "evil" not in result

    def test_iframe_dropped(self):
        html = '<p>before</p><iframe src="x">frame content</iframe><p>after</p>'
        result = extract_text(html)
        assert "before" in result
        assert "after" in result
        assert "frame content" not in result


class TestExtractTextBlockTags:
    """Block-level tags contribute paragraphs."""

    def test_headings_are_paragraphs(self):
        html = "<h1>Title</h1><h2>Subtitle</h2><p>body</p>"
        result = extract_text(html)
        assert "Title" in result
        assert "Subtitle" in result
        assert "body" in result
        # Three logical paragraphs → at least two separators.
        assert result.count("\n\n") >= 2

    def test_list_items_are_paragraphs(self):
        html = "<ul><li>one</li><li>two</li><li>three</li></ul>"
        result = extract_text(html)
        assert "one" in result
        assert "two" in result
        assert "three" in result
        # All three items present as separate paragraphs.
        for item in ("one", "two", "three"):
            assert item in result.split("\n\n")

    def test_blockquote_preserved(self):
        html = "<blockquote>quoted text</blockquote>"
        result = extract_text(html)
        assert "quoted text" in result

    def test_div_content_preserved(self):
        """<div> paragraphs (some 36kr articles use them) survive."""
        html = "<div>div paragraph</div>"
        result = extract_text(html)
        assert "div paragraph" in result


class TestExtractTextReal36kr:
    """End-to-end with a realistic 36kr-style multi-article HTML blob."""

    REAL_HTML = """
<html><body>
<article>
<h2>大公司：</h2>
<p><strong>首关15亿元，英诺科创三期基金持续聚焦早期科技投资</strong></p>
<p>36氪获悉，6月5日，英诺基金宣布旗下英诺科创三期基金完成首轮关账，
规模15亿元。该基金聚焦早期科技投资，重点布局前沿科技及人工智能领域。</p>
<p><strong>比特币"金库"公司市值蒸发620亿美元</strong></p>
<p>比特币本周下跌约14%，至四个月低点附近。</p>
</article>
<aside class="related">related: foo</aside>
<footer>copyright 36kr</footer>
</body></html>
"""

    def test_no_html_tags_remain(self):
        result = extract_text(self.REAL_HTML)
        assert "<" not in result
        assert ">" not in result

    def test_real_content_preserved(self):
        result = extract_text(self.REAL_HTML)
        # Headlines and body text both come through.
        assert "首关15亿元" in result
        assert "英诺科创三期基金" in result
        assert "比特币" in result

    def test_aside_and_footer_dropped(self):
        result = extract_text(self.REAL_HTML)
        assert "related: foo" not in result
        assert "copyright 36kr" not in result

    def test_strong_unwrapped(self):
        """Headlines wrapped in <strong> appear without tags."""
        result = extract_text(self.REAL_HTML)
        # If <strong> were still there we'd see literal "<strong>" or its
        # effect of breaking the text — neither should be present.
        assert "<strong>" not in result
        # And the headline text should be in the output, not dropped.
        assert "首关15亿元" in result


# ── integration: clean_all_html() on the repository ────────────────────────


@pytest.fixture
def tmp_db(tmp_path):
    """Fresh DB for the integration test."""
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    yield db_path
    close_db()
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod
    conn_mod._CON = None  # type: ignore[attr-defined]
    conn_mod._DB_PATH = None  # type: ignore[attr-defined]
    mig_mod._CON = None  # type: ignore[attr-defined]


class TestCleanAllHtml:
    @staticmethod
    def _insert_legacy(db, rows):
        """Write rows the way pre-clean_v1 code did: markup stored verbatim.

        ``dedupe_insert`` now cleans at ingest, so this legacy backfill path is
        exercised with rows inserted straight into the table.
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(db) as c:
            for i, (title, url, source, content) in enumerate(rows):
                c.execute(
                    """
                    INSERT INTO articles
                        (id, title, url, source, fetched_at, raw_content)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (f"legacy-{i}", title, url, source, now, content),
                )
            c.commit()

    def test_writes_cleaned_text_to_db(self, tmp_db):
        migrate()
        repo = ArticleRepository()

        # Insert a few legacy rows — some with HTML, some without.
        self._insert_legacy(tmp_db, [
            ("html one", "https://example.com/1", "36kr_rss",
             "<h2>X</h2><p>body one</p>"),
            ("html two", "https://example.com/2", "36kr_rss",
             "<p>body <strong>two</strong></p>"),
            ("plain three", "https://example.com/3", "mpaypass",
             "already clean text"),
        ])

        # Confirm at least two rows have HTML markup in raw_content.
        with sqlite3.connect(tmp_db) as c:
            html_rows = c.execute(
                "SELECT COUNT(*) FROM articles WHERE raw_content LIKE '%<%'"
            ).fetchone()[0]
        assert html_rows == 2

        # Run the retroactive cleaner.
        stats = repo.clean_all_html()
        assert stats["scanned"] == 2  # only the 36kr ones
        assert stats["updated"] == 2
        assert stats["unchanged"] == 0

        # Verify the cleaned text is in the DB.
        with sqlite3.connect(tmp_db) as c:
            row = c.execute(
                "SELECT raw_content FROM articles WHERE title = 'html one'"
            ).fetchone()
        assert "<" not in row[0]
        assert "X" in row[0]
        assert "body one" in row[0]

    def test_idempotent(self, tmp_db):
        """Re-running on already-cleaned content is a no-op."""
        migrate()
        repo = ArticleRepository()
        self._insert_legacy(tmp_db, [
            ("html", "https://example.com/x", "36kr_rss", "<p>body</p>"),
        ])
        first = repo.clean_all_html()
        assert first["updated"] == 1
        # Second run: the LIKE '%<%' filter excludes already-cleaned
        # rows (no '<' left in "body"), so scanned drops to 0 and
        # nothing is updated. That's the correct idempotent behaviour —
        # we don't want to re-scan 100k clean rows every time.
        second = repo.clean_all_html()
        assert second["scanned"] == 0
        assert second["updated"] == 0
        assert second["unchanged"] == 0
