"""Integration tests: the v1.0.64 match rules wired into every gate.

`test_keyword_match.py` covers the rule engine in isolation.  These tests
cover the three call sites that must not drift from each other:

* ``apply_tag_keyword_filter`` — the ingest gate for every source;
* ``apply_keyword_filters`` — global + source black/whitelists;
* ``moni xwatch ingest`` — the manual one-tweet path.

The last one matters most: a tweet that batch scraping would reject must
not become insertable just because it came in through the CLI.
"""

from __future__ import annotations

import json

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "news.db"
    set_db_path(db_path)
    migrate()
    conn = get_db()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, 'html', ?, 30, 1, '2026-01-01', '2026-01-01')",
        ("src-t", "testsrc", "测试源", "https://example.com/"),
    )
    conn.execute(
        "INSERT INTO tag_definitions (name, description, color, created_at, is_active, keywords) "
        "VALUES ('general-tech', '通用科技', '#6B7280', '2026-01-01', 1, ?)",
        (json.dumps(["MA", "CI/CD"], ensure_ascii=False),),
    )
    conn.execute(
        "INSERT INTO tag_definitions (name, description, color, created_at, is_active, keywords) "
        "VALUES ('banking', '银行', '#2563EB', '2026-01-01', 1, ?)",
        (json.dumps(["银行", "消费"], ensure_ascii=False),),
    )
    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) "
        "VALUES ('src-t', 'general-tech', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) "
        "VALUES ('src-t', 'banking', '2026-01-01')"
    )
    conn.commit()
    yield "testsrc"
    close_db()


# ── tag gate ────────────────────────────────────────────────────────────────


def test_tag_gate_rejects_ascii_hit_inside_a_longer_word(fresh_db):
    """'MA' inside 'MASAJIN' no longer opens the gate."""
    from moni.fetch.tag_filter import apply_tag_keyword_filter

    arts = [{"title": "2024 北海道 Part 3", "content": "晚餐是临时找的 MASAJIN 富良野店。" * 20}]
    kept, stats, _ = apply_tag_keyword_filter(fresh_db, arts)
    assert kept == []
    assert stats["dropped_tag_filter"] == 1


def test_tag_gate_accepts_real_ascii_token(fresh_db):
    from moni.fetch.tag_filter import apply_tag_keyword_filter

    arts = [{"title": "营销自动化 (MA) 落地实践", "content": "正文。" * 100}]
    kept, stats, _ = apply_tag_keyword_filter(fresh_db, arts)
    assert len(kept) == 1
    assert stats["dropped_tag_filter"] == 0


def test_tag_gate_rejects_one_passing_short_cjk_in_long_body(fresh_db):
    """A single '消费' in a long unrelated body is not a topic signal."""
    from moni.fetch.tag_filter import apply_tag_keyword_filter

    arts = [{"title": "五仁月饼", "content": "顺便提了一句消费。" + "正文内容。" * 400}]
    kept, stats, _ = apply_tag_keyword_filter(fresh_db, arts)
    assert kept == []
    assert stats["dropped_tag_filter"] == 1


def test_tag_gate_accepts_short_cjk_in_title(fresh_db):
    from moni.fetch.tag_filter import apply_tag_keyword_filter

    arts = [{"title": "消费金融新规落地", "content": "正文内容。" * 400}]
    kept, _, _ = apply_tag_keyword_filter(fresh_db, arts)
    assert len(kept) == 1


def test_tag_gate_records_only_the_keywords_that_actually_matched(fresh_db):
    """keyword_hits feeds article_keyword_hits — it must not over-report."""
    from moni.fetch.tag_filter import apply_tag_keyword_filter

    arts = [{"title": "银行公告", "content": "MASAJIN 晚餐。" * 100}]
    _, _, hits = apply_tag_keyword_filter(fresh_db, arts)
    kws = {h["keyword"] for h in hits}
    assert "银行" in kws
    assert "MA" not in kws


def test_tag_gate_hit_field_reflects_the_scope(fresh_db):
    from moni.fetch.tag_filter import apply_tag_keyword_filter

    arts = [{"title": "营销自动化 (MA)", "content": "正文"}]
    _, _, hits = apply_tag_keyword_filter(fresh_db, arts)
    assert {h["hit_field"] for h in hits} == {"title+content"}


def test_bestblog_title_only_still_ignores_body(fresh_db):
    """The v1.0.5.24/7.5 boilerplate guard must survive the rewrite."""
    from moni.fetch.tag_filter import apply_tag_keyword_filter

    conn = get_db()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, created_at, updated_at) "
        "VALUES ('src-bb', 'bestblog-yt', 'BB-YouTube', 'rss', 'https://example.com/f', "
        "60, 1, '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) "
        "VALUES ('src-bb', 'banking', '2026-01-01')"
    )
    conn.commit()

    arts = [{"title": "444. 在编织的一针一线中", "content": "关注公众号 银行"}]
    kept, stats, hits = apply_tag_keyword_filter("bestblog-yt", arts)
    assert kept == []
    assert stats["dropped_tag_filter"] == 1
    assert [h["hit_field"] for h in hits] == []


# ── global / source black-white lists ───────────────────────────────────────


def _add_source_filter(conn, *, mode, keywords, match_field="title+content", source_id="src-t"):
    conn.execute(
        "INSERT INTO source_keyword_filters "
        "(source_id, mode, match_field, keywords, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 1, '2026-01-01', '2026-01-01')",
        (source_id, mode, match_field, json.dumps(keywords, ensure_ascii=False)),
    )
    conn.commit()


def test_blacklist_no_longer_fires_on_a_word_fragment(fresh_db):
    """A blacklisted 'Tesla' must not kill an article about 'Tesla-like'."""
    from moni.fetch.filters import apply_keyword_filters

    conn = get_db()
    _add_source_filter(conn, mode="blacklist", keywords=["RAG"])
    kept, stats, _ = apply_keyword_filters(
        fresh_db, [{"title": "GraphRAG 本体工程", "content": "正文。" * 100}]
    )
    assert len(kept) == 1
    assert stats["dropped_blacklist"] == 0


def test_blacklist_still_fires_on_the_real_token(fresh_db):
    from moni.fetch.filters import apply_keyword_filters

    conn = get_db()
    _add_source_filter(conn, mode="blacklist", keywords=["RAG"])
    kept, stats, _ = apply_keyword_filters(
        fresh_db, [{"title": "RAG 检索增强实战", "content": "正文。" * 100}]
    )
    assert kept == []
    assert stats["dropped_blacklist"] == 1


def test_whitelist_uses_the_same_rules(fresh_db):
    """Whitelist must not become a loophole the blacklist just closed."""
    from moni.fetch.filters import apply_keyword_filters

    conn = get_db()
    _add_source_filter(conn, mode="whitelist", keywords=["MA"])
    kept, stats, _ = apply_keyword_filters(
        fresh_db, [{"title": "北海道 MASAJIN", "content": "正文。" * 100}]
    )
    assert kept == []
    assert stats["dropped_whitelist"] == 1


def test_match_field_title_is_respected(fresh_db):
    from moni.fetch.filters import apply_keyword_filters

    conn = get_db()
    _add_source_filter(conn, mode="blacklist", keywords=["消费"], match_field="title")
    kept, stats, _ = apply_keyword_filters(
        fresh_db, [{"title": "普通标题", "content": "消费 " * 100}]
    )
    assert len(kept) == 1
    assert stats["dropped_blacklist"] == 0


def test_bypass_short_circuit_is_untouched(fresh_db):
    """follow_builders-style bypass must still skip every gate."""
    from moni.fetch.filters import apply_keyword_filters

    conn = get_db()
    conn.execute(
        "UPDATE sources SET fetch_config = ? WHERE name = ?",
        (json.dumps({"bypass_keyword_filters": True}), fresh_db),
    )
    _add_source_filter(conn, mode="blacklist", keywords=["anything"])
    kept, stats, _ = apply_keyword_filters(
        fresh_db, [{"title": "任意标题", "content": "任意正文"}]
    )
    assert len(kept) == 1
    assert stats["dropped_blacklist"] == 0


# ── CLI: xwatch ingest ──────────────────────────────────────────────────────


def _fake_tweet(text: str) -> dict:
    return {
        "text": text,
        "id_str": "1900000000000000000",
        "user": {"screen_name": "someone", "name": "Someone"},
        "created_at": "Sat Oct 25 00:00:00 +0000 2026",
    }


def test_xwatch_ingest_gate_matches_the_batch_gate(fresh_db, monkeypatch):
    """The CLI path must reject exactly what the batch gate rejects.

    Runs WITHOUT ``dry_run`` on purpose: dry_run returns before touching
    the DB, so it cannot tell "gated out" from "gated in, not written".
    An earlier draft made that mistake and the mutation survived.
    """
    from moni.cli import main as cli_main
    from moni.fetch import xwatch_syndication

    conn = get_db()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, created_at, updated_at) "
        "VALUES ('src-x', 'xwatch', 'X', 'x', 'https://x.com/', 30, 1, "
        "'2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) "
        "VALUES ('src-x', 'general-tech', '2026-01-01')"
    )
    conn.commit()

    monkeypatch.setattr(
        xwatch_syndication, "fetch_tweet", lambda tid: _fake_tweet("晚餐是 MASAJIN 富良野店")
    )
    monkeypatch.setattr(
        xwatch_syndication,
        "tweet_to_article",
        lambda data, fallback_url=None: {
            "title": "北海道游记",
            "content": "晚餐是 MASAJIN 富良野店",
            "url": "https://x.com/someone/status/1",
            "publisher": "someone",
            "published_at": "2026-10-25",
            "fetched_at": "2026-10-25",
        },
    )

    code = cli_main.cmd_xwatch_ingest("https://x.com/someone/status/1")
    assert code == 0
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0


def test_xwatch_ingest_accepts_a_real_hit(fresh_db, monkeypatch):
    """The control case: a genuine token hit still inserts."""
    from moni.cli import main as cli_main
    from moni.fetch import xwatch_syndication

    conn = get_db()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, created_at, updated_at) "
        "VALUES ('src-x', 'xwatch', 'X', 'x', 'https://x.com/', 30, 1, "
        "'2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) "
        "VALUES ('src-x', 'general-tech', '2026-01-01')"
    )
    conn.commit()

    monkeypatch.setattr(
        xwatch_syndication, "fetch_tweet", lambda tid: _fake_tweet("CI/CD 流水线")
    )
    monkeypatch.setattr(
        xwatch_syndication,
        "tweet_to_article",
        lambda data, fallback_url=None: {
            "title": "用 CI/CD 做发布",
            "content": "CI/CD 流水线实践",
            "url": "https://x.com/someone/status/2",
            "source": "xwatch",
            "publisher": "someone",
            "published_at": "2026-10-25",
            "fetched_at": "2026-10-25",
        },
    )

    code = cli_main.cmd_xwatch_ingest("https://x.com/someone/status/2")
    assert code == 0
    # cmd_xwatch_ingest closes the connection on success — reopen to verify.
    assert get_db().execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1
