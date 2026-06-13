"""Tests for moni.fetch.tag_filter (v1.0.5.4).

The filter builds the union of keywords from every tag bound to a
source and drops articles whose title+content does not hit any of
those substrings.  Tags with no keywords are skipped.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.fetch.tag_filter import apply_tag_keyword_filter


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "news.db"
    set_db_path(db_path)
    migrate()
    conn = get_db()
    # Source + tag definitions
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, 'html', ?, 30, 1, '2026-01-01', '2026-01-01')",
        ("src-mp", "mpaypass", "移动支付网", "https://www.mpaypass.com.cn/"),
    )
    conn.execute(
        "INSERT INTO tag_definitions (name, description, color, created_at, is_active) "
        "VALUES ('banking', '银行', '#2563EB', '2026-01-01', 1)"
    )
    conn.execute(
        "INSERT INTO tag_definitions (name, description, color, created_at, is_active, keywords) "
        "VALUES ('payment', '支付', '#16A34A', '2026-01-01', 1, ?)",
        (json.dumps(["支付", "跨境支付", "收单"], ensure_ascii=False),)
    )
    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) "
        "VALUES ('src-mp', 'banking', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) "
        "VALUES ('src-mp', 'payment', '2026-01-01')"
    )
    # v1.0.5.24: wechat tag for bestblogs title-only test
    conn.execute(
        "INSERT INTO tag_definitions (name, description, color, created_at, is_active, keywords) "
        "VALUES ('wechat', '微信生态', '#7C3AED', '2026-01-01', 1, ?)",
        (json.dumps(["腾讯", "微信", "小程序", "公众号"], ensure_ascii=False),)
    )
    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) "
        "VALUES ('src-bestblogs', 'wechat', '2026-01-01')"
    )
    conn.commit()
    yield "mpaypass"
    close_db()


def test_no_keywords_means_pass_through(fresh_db):
    """If no bound tag has keywords, every article is kept.

    We use a fresh fixture variant: the 'fresh_db' fixture already
    binds a 'payment' tag with keywords, so we monkeypatch the
    loader to simulate "no keywords bound" by stubbing the helper.
    """
    from moni.fetch import tag_filter
    original = tag_filter._load_tag_keywords_for_source
    tag_filter._load_tag_keywords_for_source = lambda src: {}
    try:
        arts = [
            {"title": "Anything goes", "content": "blah"},
            {"title": "Foo", "content": "bar"},
        ]
        kept, stats, keyword_hits = apply_tag_keyword_filter(fresh_db, arts)
        assert len(kept) == 2
        assert stats["dropped_tag_filter"] == 0
        assert stats["tags_used"] == 0
    finally:
        tag_filter._load_tag_keywords_for_source = original


def test_kept_when_any_tag_keyword_hits(fresh_db):
    """Article hits the 'payment' tag's keyword '支付' — kept."""
    arts = [
        {"title": "工行收紧支付", "content": "5月1日, 工商银行发布通知, 加强支付合规管理。"},
        {"title": "无关新闻", "content": "这是一篇完全无关的文章。"},
    ]
    kept, stats, keyword_hits = apply_tag_keyword_filter(fresh_db, arts)
    assert len(kept) == 1
    assert kept[0]["title"] == "工行收紧支付"
    assert stats["dropped_tag_filter"] == 1
    assert stats["tags_used"] == 3  # 3 keywords from 'payment'


def test_drop_when_no_keyword_hits(fresh_db):
    """Article doesn't hit any keyword — dropped."""
    arts = [
        {"title": "纯旅游", "content": "介绍一些旅游景点。"},
    ]
    kept, stats, keyword_hits = apply_tag_keyword_filter(fresh_db, arts)
    assert kept == []
    assert stats["dropped_tag_filter"] == 1


def test_empty_title_content_drops(fresh_db):
    """Empty article — dropped (no match possible)."""
    kept, stats, keyword_hits = apply_tag_keyword_filter(fresh_db, [{"title": "", "content": ""}])
    assert kept == []
    assert stats["dropped_tag_filter"] == 1


def test_invalid_json_keywords_in_db_is_skipped(fresh_db):
    """A tag with malformed JSON keywords should not crash the filter."""
    conn = get_db()
    # Replace the payment tag's keywords with garbage
    conn.execute("UPDATE tag_definitions SET keywords = 'not json' WHERE name = 'payment'")
    conn.commit()
    arts = [{"title": "支付新闻", "content": "支付"}]
    kept, stats, keyword_hits = apply_tag_keyword_filter(fresh_db, arts)
    # Garbage keywords are skipped → no keyword set → pass-through
    assert len(kept) == 1
    assert stats["tags_used"] == 0


def test_bestblogs_matches_title_only(fresh_db):
    """v1.0.5.24: bestblogs source only matches title, not content."""
    # Title contains keyword → kept
    arts = [{"title": "腾讯混元AI Infra进化", "content": "无关内容"}]
    kept, stats, _ = apply_tag_keyword_filter("bestblogs", arts)
    assert len(kept) == 1

    # Title does NOT contain keyword but content does → dropped
    arts2 = [{"title": "普通新闻标题", "content": "腾讯混元AI Infra进化"}]
    kept2, stats2, _ = apply_tag_keyword_filter("bestblogs", arts2)
    assert len(kept2) == 0
    assert stats2["dropped_tag_filter"] == 1


def test_non_bestblogs_matches_content(fresh_db):
    """Non-bestblogs sources still match title+content."""
    # Content contains keyword → kept (for non-bestblogs sources)
    arts = [{"title": "普通标题", "content": "支付新闻"}]
    kept, stats, _ = apply_tag_keyword_filter("mpaypass", arts)
    assert len(kept) == 1
