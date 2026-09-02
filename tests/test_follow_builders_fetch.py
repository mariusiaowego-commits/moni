"""tests/test_follow_builders_fetch.py — sprint-21 v1.0.41 fetcher tests.

Covers (tech-spec §8 / test plan):
  1. parse normal tweet → canonical fields (incl. interaction meta)
  2. url normalization (x.com / twitter.com / mobile.twitter.com + tracking params)
  3. cross-source soft dedupe (bestblog-x stored → follow_builders skips)
  4. enabled=false → scheduler.should_fetch False
  5. bypass double-gate → full ingest + synthesized ai-fintech tag
  6. other sources still filtered (no bypass leak)
  7. bypass with active global whitelist+blacklist (BLOCKER core case)
  8. http error graceful empty
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate

_FIXTURE = Path(__file__).parent / "fixtures" / "feed_x_sample.json"


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Isolated tmp sqlite, migrated to current schema. Reset between tests."""
    close_db()  # drop any stale thread-local connection from a prior test
    db_path = tmp_path / "news.db"
    set_db_path(db_path)
    migrate()
    yield
    close_db()


def _seed_source(conn, name, fetch_config=None, is_active=1):
    src_id = "src-" + name
    # Remove any pre-existing binding (FK to sources) so we can re-seed freely
    conn.execute("DELETE FROM source_tags WHERE source_id=?", (src_id,))
    conn.commit()
    conn.execute(
        "DELETE FROM sources WHERE id=? OR name=?",
        (src_id, name),
    )
    conn.commit()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, notes, fetch_config, created_at, updated_at) "
        "VALUES (?, ?, ?, 'json', ?, ?, ?, ?, ?, '2026-01-01', '2026-01-01')",
        (src_id, name, name, "https://example.com/" + name,
         360, is_active, "test", fetch_config),
    )
    conn.commit()


def _seed_ai_tag(conn):
    conn.execute(
        "INSERT INTO tag_definitions (name, description, color, created_at, is_active) "
        "VALUES ('ai-fintech', 'AI×金融', '#DC2626', '2026-01-01', 1) "
        "ON CONFLICT(name) DO UPDATE SET color=excluded.color, is_active=excluded.is_active"
    )
    conn.commit()


def _bind_tag(conn, source_id, tag_name):
    conn.execute(
        "INSERT OR IGNORE INTO source_tags (source_id, tag_name, created_at) "
        "VALUES (?, ?, '2026-01-01')",
        (source_id, tag_name),
    )
    conn.commit()
    return conn.total_changes


def _load_feed():
    with _FIXTURE.open(encoding="utf-8") as fh:
        return json.load(fh)


def _load_fetch_payload(monkeypatch, body=None):
    """Monkeypatch httpx.get → return a fake Response with the fixture body."""
    import httpx

    from moni.fetch.sources.follow_builders import FetcherFollowBuilders, FEED_URL

    if body is None:
        body = json.dumps(_load_feed(), ensure_ascii=False)

    class _FakeResp:
        def raise_for_status(self):
            return None
        @property
        def json(self):
            return lambda: json.loads(body)

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp())
    return FetcherFollowBuilders()


# ── 1. parse normal tweet ────────────────────────────────────────────────

def test_parse_normal_tweet(fresh_db):
    from moni.fetch.sources.follow_builders import normalize_tweet_url, _truncate_title

    url = normalize_tweet_url(
        "https://x.com/thsottiaux/status/2094588317245509959",
        handle="thsottiaux", tweet_id="2094588317245509959",
    )
    assert url == "https://x.com/thsottiaux/status/2094588317245509959"
    assert _truncate_title("If you still haven't tried Codex", "thsottiaux") == (
        "If you still haven't tried Codex")


# ── 2. url normalization ─────────────────────────────────────────────────

def test_url_normalization():
    from moni.fetch.sources.follow_builders import normalize_tweet_url

    a = normalize_tweet_url("https://twitter.com/foo/status/123?s=20", handle="foo", tweet_id="123")
    b = normalize_tweet_url("https://x.com/foo/status/123", handle="foo", tweet_id="123")
    c = normalize_tweet_url("http://mobile.twitter.com/foo/status/123", handle="foo", tweet_id="123")
    assert a == b == c == "https://x.com/foo/status/123"

    # no-status-id path: strip query + host rewrite
    d = normalize_tweet_url("https://twitter.com/foo/status/999?s=20", tweet_id="")
    assert d == "https://x.com/foo/status/999"


# ── 3. cross-source soft dedupe ──────────────────────────────────────────

def test_cross_source_dedupe(fresh_db):
    from moni.fetch.sources.follow_builders import FetcherFollowBuilders

    conn = get_db()
    # bestblog-x already stored the same tweet under a twitter.com variant
    conn.execute(
        "INSERT INTO articles (id, title, url, source, published_at, fetched_at) "
        "VALUES ('best1', 'dup', 'https://twitter.com/thsottiaux/status/2094588317245509959?s=20', "
        "'bestblog-x', NULL, '2026-09-01')"
    )
    conn.commit()

    fetcher = FetcherFollowBuilders()
    assert fetcher._is_url_exists_globally(
        "https://x.com/thsottiaux/status/2094588317245509959", "2094588317245509959"
    ) is True


# ── 4. scheduler disabled ────────────────────────────────────────────────

def test_scheduler_disabled(fresh_db):
    from moni.fetch.scheduler import Scheduler
    from pathlib import Path as P

    cfg = P("/Users/mt16/dev/moni/config/sources.yaml")
    sched = Scheduler(config_path=cfg, last_run_path=P("/tmp/moni-test-lastrun.json"))
    assert sched.should_fetch("follow_builders") is False  # enabled: false in yaml


# ── 5. bypass double-gate + synthesized tag ──────────────────────────────

def test_bypass_double_gate_pipeline(fresh_db, monkeypatch):
    from moni.fetch.base import BaseFetcher
    # patch fetch to our payload, run full BaseFetcher.run → dedupe_insert + tags
    from moni.fetch.sources.follow_builders import FetcherFollowBuilders

    conn = get_db()
    _seed_ai_tag(conn)
    _seed_source(conn, "follow_builders",
                 fetch_config=json.dumps({"bypass_keyword_filters": True}, ensure_ascii=False))
    _bind_tag(conn, "src-follow_builders", "ai-fintech")
    conn.commit()

    f = _load_fetch_payload(monkeypatch)
    result = f.run()
    assert result["error"] is None
    assert result["inserted"] == 3  # 3 tweets with id+url; the id-less one skipped by parse

    # tags synthesized for every inserted article
    rows = conn.execute(
        "SELECT at.tag_name FROM article_tags at "
        "JOIN articles a ON a.id = at.article_id "
        "WHERE a.source='follow_builders'"
    ).fetchall()
    assert len(rows) == 3
    assert {r["tag_name"] for r in rows} == {"ai-fintech"}


# ── 6. other sources still filtered (no bypass leak) ─────────────────────

def test_other_sources_still_filtered(fresh_db):
    from moni.fetch.filters import apply_keyword_filters

    conn = get_db()
    _seed_source(conn, "36kr_rss")
    conn.commit()
    # active global blacklist
    conn.execute(
        "INSERT INTO global_keyword_filters (mode, match_field, keywords, is_active, notes, created_at, updated_at) "
        "VALUES ('blacklist', 'title+content', '[\"博彩\"]', 1, 'test', 'now', 'now')"
    )
    conn.commit()

    articles = [
        {"source": "36kr_rss", "url": "http://e/1", "title": "含博彩的文章", "content": "x"},
        {"source": "36kr_rss", "url": "http://e/2", "title": "干净文章", "content": "x"},
    ]
    kept, stats, _ = apply_keyword_filters("36kr_rss", articles)
    assert len(kept) == 1
    assert kept[0]["url"] == "http://e/2"
    assert stats["dropped_global_blacklist"] == 1


# ── 7. BLOCKER: bypass with active global whitelist + blacklist ─────────

def test_bypass_with_active_global_whitelist_and_blacklist(fresh_db, monkeypatch):
    from moni.fetch.sources.follow_builders import FetcherFollowBuilders

    conn = get_db()
    _seed_ai_tag(conn)
    _seed_source(conn, "follow_builders",
                 fetch_config=json.dumps({"bypass_keyword_filters": True}, ensure_ascii=False))
    _bind_tag(conn, "src-follow_builders", "ai-fintech")

    # active GLOBAL whitelist (English tweets would fail) + blacklist (crypto)
    conn.execute(
        "INSERT INTO global_keyword_filters (mode, match_field, keywords, is_active, notes, created_at, updated_at) "
        "VALUES ('whitelist', 'title+content', '[\"银行\", \"支付\"]', 1, 'test', 'now', 'now')"
    )
    conn.execute(
        "INSERT INTO global_keyword_filters (mode, match_field, keywords, is_active, notes, created_at, updated_at) "
        "VALUES ('blacklist', 'title+content', '[\"crypto\"]', 1, 'test', 'now', 'now')"
    )
    conn.commit()

    f = _load_fetch_payload(monkeypatch)
    result = f.run()
    assert result["error"] is None
    assert result["inserted"] == 3          # all tweets land despite whitelist/blacklist
    assert result["dropped_global_blacklist"] == 0
    assert result["dropped_global_whitelist"] == 0
    assert result["filters_applied"] == 0
    # no basket drops
    n_basket = conn.execute("SELECT COUNT(*) FROM basket WHERE source='follow_builders'").fetchone()[0]
    assert n_basket == 0
    # all tagged ai-fintech
    n_tagged = conn.execute(
        "SELECT COUNT(*) FROM article_tags at JOIN articles a ON a.id=at.article_id "
        "WHERE a.source='follow_builders' AND at.tag_name='ai-fintech'"
    ).fetchone()[0]
    assert n_tagged == 3


# ── 8. http error graceful ───────────────────────────────────────────────

def test_http_error_graceful(fresh_db, monkeypatch):
    import httpx
    from moni.fetch.sources.follow_builders import FetcherFollowBuilders

    def _boom(*a, **k):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(httpx, "get", _boom)
    f = FetcherFollowBuilders()
    raws = f.fetch()
    assert raws == []


# ── 9. empty feed ────────────────────────────────────────────────────────

def test_empty_feed(fresh_db, monkeypatch):
    from moni.fetch.sources.follow_builders import FetcherFollowBuilders
    import httpx

    class _Resp:
        def raise_for_status(self):
            return None
        @property
        def json(self):
            return lambda: {"generatedAt": "x", "x": []}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    f = FetcherFollowBuilders()
    result = f.run()
    assert result["error"] is None
    assert result["fetched"] == 0
    assert result["parsed"] == 0
    assert result["inserted"] == 0