"""Deterministic HTML brief: full body + collapsible original."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.expanduser("~/dev/moni"))

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.share.brief_html import render_brief_html, write_brief_html


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "bh.db"
    set_db_path(db_path)
    migrate()
    yield db_path
    close_db()
    import moni.db.connection as conn_mod

    conn_mod._CON = None
    conn_mod._DB_PATH = None


def test_html_contains_body_and_fold(fresh_db: Path, tmp_path: Path):
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    body = (
        "---\ntitle: x\n---\n"
        "这是完整正文段落一。\n\n"
        "## 小节标题\n\n"
        "这是段落二，**加粗**与[链接](https://example.com/a)。\n\n"
        "- 列表甲\n- 列表乙\n"
    )
    conn.execute(
        """
        INSERT INTO articles
        (id, title, url, source, publisher, published_at, fetched_at, raw_content,
         ai_summary, brief_summary, business_value_score, business_value_note,
         quality_score, fact_check_conclusion)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "h1",
            "测试标题",
            "https://example.com/x",
            "bestblogs",
            "极客公园",
            now,
            now,
            body,
            "长摘要不该优先",
            "短摘要两句内。",
            80,
            "对我方有对标价值",
            70,
            "无需核查",
        ),
    )
    conn.commit()

    html_doc = render_brief_html(str(fresh_db), ["h1"], title="同业简报 — test")
    assert "查看全文（离线可读）" in html_doc
    assert "orig-toggle" in html_doc
    assert "orig-scroll" in html_doc
    assert "max-height: 320px" in html_doc
    assert "这是完整正文段落一" in html_doc
    assert "<h3>" in html_doc or "<h2>" in html_doc  # ## → h3 in body
    assert "<strong>加粗</strong>" in html_doc
    assert "<ul>" in html_doc and "列表甲" in html_doc
    assert "短摘要两句内" in html_doc
    assert "极客公园" in html_doc
    assert "gsap" in html_doc.lower()
    assert 'aria-expanded="false"' in html_doc
    assert "from moni by TQ" in html_doc
    assert "Curated by moni" in html_doc
    # frontmatter stripped
    assert "title: x" not in html_doc

    out = tmp_path / "out.html"
    write_brief_html(str(fresh_db), ["h1"], out)
    assert out.is_file()
    assert out.stat().st_size > 500


def test_body_to_html_plain_paragraphs():
    from moni.share.brief_html import body_to_html

    h = body_to_html("第一段。\n\n第二段内容。")
    assert "<p>" in h
    assert "第一段" in h and "第二段" in h
