"""Tests for FetcherXWatch — fetcher / parse / dedupe end-to-end with mocks.

Strategy:
- All network calls (search_tweet_urls + fetch_tweet) are patched via
  ``monkeypatch`` at the module path used inside FetcherXWatch.
- DB tests use a tmp file + migrate() per AGENTS.md "Test 隔离陷阱" so we
  don't pollute the dev database.
- tag_filter runs because source_tags("src-xwatch", "wechat" /
  "miniprogram") are seeded by the test fixture (real DB seed-config
  doesn't apply here — that runs against the dev DB, not tmp DB).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.expanduser("~/dev/moni"))

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.fetch.sources.xwatch import FetcherXWatch, _DEFAULTS

FIX = Path(__file__).parent / "fixtures"


# ── fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Per-test isolated DB file — avoids contaminating dev news.db."""
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


def _seed_xwatch_source(conn, *, fetch_config_json: str | None = None) -> None:
    """Insert the xwatch source row + its tag bindings.

    We seed both the source row AND source_tags / tag_definitions so the
    tag_keyword filter (which calls ``apply_tag_keyword_filter`` in
    BaseFetcher.run) has the keywords it needs to mark articles as
    "kept".
    """
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO sources (id, name, display_name, source_type, url,
                             fetch_interval_min, is_active, fetch_config,
                             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "src-xwatch", "xwatch", "X 关键词监控", "x", "",
            30, 1, fetch_config_json, now, now,
        ),
    )

    # tag_definitions: 6 existing + 'wechat' + 'miniprogram' (with keywords)
    tag_rows = [
        ("banking",      "银行",   "银行 tags",        "#2563EB", "[]"),
        ("payment",      "支付",   "支付 tags",        "#16A34A", "[]"),
        ("ai-fintech",   "AI×金融","AI×金融",         "#DC2626", "[]"),
        ("regulation",   "监管",   "监管",             "#EA580C", "[]"),
        ("general-tech", "通用科技","通用科技",        "#6B7280", "[]"),
        ("wechat",       "微信生态","微信公众号/小程序","#7C3AED",
         json.dumps(["微信小程序", "服务通知", "订阅消息"], ensure_ascii=False)),
        ("miniprogram",  "小程序", "小程序关键词",     "#10B981",
         json.dumps(["微信小程序", "订阅消息", "服务通知", "搜一搜"], ensure_ascii=False)),
    ]
    for name, display, desc, color, kw in tag_rows:
        conn.execute(
            """INSERT INTO tag_definitions
               (name, display_name, description, color, keywords, created_at, is_active)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (name, display, desc, color, kw, now),
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
def fetcher() -> FetcherXWatch:
    return FetcherXWatch()


# ── _load_config ────────────────────────────────────────────────────────


class TestLoadConfig:
    def test_defaults_when_fetch_config_missing(self, fetcher):
        """No fetch_config row → defaults are returned, queries non-empty."""
        _seed_xwatch_source(get_db(), fetch_config_json=None)
        cfg = fetcher._load_config()
        assert cfg["queries"] == _DEFAULTS["queries"]
        assert cfg["max_results_per_query"] == _DEFAULTS["max_results_per_query"]
        assert cfg["request_delay"] == _DEFAULTS["request_delay"]

    def test_fetch_config_overrides_subset(self, fetcher):
        user = json.dumps({
            "queries": ["a", "b"],
            "request_delay": 0.1,
        })
        _seed_xwatch_source(get_db(), fetch_config_json=user)
        cfg = fetcher._load_config()
        # Overridden
        assert cfg["queries"] == ["a", "b"]
        assert cfg["request_delay"] == 0.1
        # Other keys stay default
        assert cfg["max_results_per_query"] == _DEFAULTS["max_results_per_query"]


# ── fetch() (network-mocked) ─────────────────────────────────────────────


class TestFetchDiscoveryAndDedupe:
    def test_fetch_two_queries_with_overlap(self, fetcher, monkeypatch):
        """Two queries both find the same URL → only one raw article (seen_ids)."""
        _seed_xwatch_source(get_db())
        url = "https://x.com/wxapp/status/1111111111111111111"
        monkeypatch.setattr(
            "moni.fetch.sources.xwatch.search_tweet_urls",
            MagicMock(return_value=[url]),
        )
        monkeypatch.setattr(
            "moni.fetch.sources.xwatch.fetch_tweet",
            MagicMock(return_value={
                "id_str": "1111111111111111111",
                "text": "微信小程序 触达 体验流畅",
                "user": {"screen_name": "wxapp"},
                "created_at": "Wed Oct 22 21:30:00 +0000 2026",
            }),
        )

        # 2 queries, both find same URL → dedup to 1 unique tweet id.
        monkeypatch.setattr(
            "moni.fetch.sources.xwatch._DEFAULTS",
            {
                **_DEFAULTS,
                "queries": ["q1", "q2"],
                "request_delay": 0,
            },
        )
        raws = fetcher.fetch()
        assert len(raws) == 1
        assert raws[0]["url"] == url
        assert raws[0]["data"]["id_str"] == "1111111111111111111"

    def test_one_query_returns_empty_does_not_break_next(self, fetcher, monkeypatch):
        """If discovery returns [] for one query, the next query still runs."""
        _seed_xwatch_source(get_db())
        call_log: list[str] = []

        def fake_discovery(query, *, max_results=20, timeout=20):
            call_log.append(query)
            if query == "broken":
                return []
            return [f"https://x.com/u/status/{query_id(query)}"]

        def query_id(q: str) -> str:
            return str(1000 + len(call_log))

        def fake_fetch_tweet(tid, *, timeout=20):
            return {
                "id_str": tid,
                "text": f"content {tid}",
                "user": {"screen_name": "u"},
                "created_at": "Wed Oct 22 21:30:00 +0000 2026",
            }

        monkeypatch.setattr("moni.fetch.sources.xwatch.search_tweet_urls", fake_discovery)
        monkeypatch.setattr("moni.fetch.sources.xwatch.fetch_tweet", fake_fetch_tweet)
        monkeypatch.setattr(
            "moni.fetch.sources.xwatch._DEFAULTS",
            {**_DEFAULTS, "queries": ["broken", "ok1", "ok2"], "request_delay": 0},
        )

        raws = fetcher.fetch()
        assert len(raws) == 2
        assert call_log == ["broken", "ok1", "ok2"]

    def test_syndication_none_is_kept_as_raw_but_filtered_in_parse(
        self, fetcher, monkeypatch,
    ):
        """When syndication returns None, the raw stays in fetch() but parse()
        returns None — the run() pipeline drops it gracefully."""
        _seed_xwatch_source(get_db())
        monkeypatch.setattr(
            "moni.fetch.sources.xwatch.search_tweet_urls",
            MagicMock(return_value=[
                "https://x.com/u/status/111",
                "https://x.com/u/status/222",
            ]),
        )
        # First tweet → None (deleted/withheld), second → valid.
        def fake_fetch(tid, *, timeout=20):
            if tid == "111":
                return None
            return {
                "id_str": tid,
                "text": "小程序 服务通知 体验不错",
                "user": {"screen_name": "u"},
                "created_at": "Wed Oct 22 21:30:00 +0000 2026",
            }
        monkeypatch.setattr("moni.fetch.sources.xwatch.fetch_tweet", fake_fetch)
        monkeypatch.setattr(
            "moni.fetch.sources.xwatch._DEFAULTS",
            {**_DEFAULTS, "queries": ["q"], "request_delay": 0},
        )

        raws = fetcher.fetch()
        assert len(raws) == 2  # both keep in raw list
        parsed = [fetcher.parse(r) for r in raws]
        assert parsed[0] is None  # None data → None article
        assert parsed[1] is not None

    def test_progress_callback_invoked(self, fetcher, monkeypatch):
        _seed_xwatch_source(get_db())
        monkeypatch.setattr(
            "moni.fetch.sources.xwatch.search_tweet_urls",
            MagicMock(return_value=[]),
        )
        monkeypatch.setattr(
            "moni.fetch.sources.xwatch._DEFAULTS",
            {**_DEFAULTS, "queries": ["a", "b", "c"], "request_delay": 0},
        )
        calls: list[dict] = []
        fetcher.progress_callback = lambda **kw: calls.append(kw)

        fetcher.fetch()

        # First call: total=3, done=0. Then 3 more (one per query), done=1,2,3
        assert calls[0]["total_feeds"] == 3
        assert calls[0]["done_feeds"] == 0
        assert calls[-1]["done_feeds"] == 3
        assert calls[-1]["current_feed"] == "c"

    def test_empty_queries_returns_empty(self, fetcher):
        _seed_xwatch_source(get_db())
        fetcher._load_config = MagicMock(return_value={**_DEFAULTS, "queries": []})
        assert fetcher.fetch() == []

    def test_invalid_queries_type_returns_empty(self, fetcher):
        _seed_xwatch_source(get_db())
        fetcher._load_config = MagicMock(return_value={
            **_DEFAULTS,
            "queries": "not a list",
        })
        assert fetcher.fetch() == []


# ── parse() ──────────────────────────────────────────────────────────────


class TestParse:
    def test_canonical_article_shape(self, fetcher, monkeypatch):
        _seed_xwatch_source(get_db())
        data = {
            "id_str": "2083475663496044761",
            # Mirror the spec's example URL text + a 微信小程序 keyword hit
            # so the assert below exercises the parse() contract directly.
            "text": (
                "UX很流畅的感觉。但是我觉得最核心需求是要高触达，及时提醒还方便。"
                "如果用户不小心删了微信小程序，应该引导客户主动搜索进入。"
            ),
            "user": {"screen_name": "xiaoyangX026"},
            "created_at": "Sun Dec 15 10:42:18 +0000 2024",
        }
        art = fetcher.parse({"data": data, "url": "https://x.com/xiaoyangX026/status/2083475663496044761"})
        assert art is not None
        assert art["title"].startswith("UX很流畅")
        assert art["url"] == "https://x.com/xiaoyangX026/status/2083475663496044761"
        assert art["source"] == "xwatch"
        assert art["published_at"].endswith("+00:00")
        assert "微信小程序" in art["content"]
        assert art["publisher"] == "@xiaoyangX026"

    def test_parse_with_none_data_returns_none(self, fetcher):
        assert fetcher.parse({"data": None, "url": "..."}) is None


# ── end-to-end (mocked network + temp DB) ────────────────────────────────


class TestEndToEnd:
    def test_run_inserts_article_and_tags(self, fetcher, monkeypatch):
        """End-to-end: fetch → parse → tag-filter (wechat/miniprogram) → dedupe_insert."""
        _seed_xwatch_source(get_db())

        # Two queries return two distinct tweets about 微信小程序 / 服务通知 / 触达.
        url_a = "https://x.com/wxapp/status/1111111111111111111"
        url_b = "https://x.com/svcdev/status/2222222222222222222"
        monkeypatch.setattr(
            "moni.fetch.sources.xwatch.search_tweet_urls",
            MagicMock(side_effect=lambda q, **_kw: [url_a] if q == "q1" else [url_b]),
        )

        def fake_fetch(tid, *, timeout=20):
            return {
                "id_str": tid,
                "text": "微信小程序 服务通知 触达 体验调查",
                "user": {"screen_name": "wxapp" if tid.startswith("1") else "svcdev"},
                "created_at": "Wed Oct 22 21:30:00 +0000 2026",
            }
        monkeypatch.setattr("moni.fetch.sources.xwatch.fetch_tweet", fake_fetch)
        monkeypatch.setattr(
            "moni.fetch.sources.xwatch._DEFAULTS",
            {**_DEFAULTS, "queries": ["q1", "q2"], "request_delay": 0},
        )

        summary = fetcher.run(dry_run=False)
        assert summary["inserted"] >= 1
        assert summary["source"] == "xwatch"

        # Verify article_tags picked up wechat and/or miniprogram (keyword_hits).
        conn = get_db()
        rows = conn.execute(
            """SELECT a.title, a.publisher, t.tag_name FROM articles a
               JOIN article_tags t ON t.article_id = a.id
               WHERE a.source = 'xwatch'"""
        ).fetchall()
        assert rows, "xwatch articles should have at least one tag binding"
        tag_names = {r["tag_name"] for r in rows}
        assert {"wechat", "miniprogram"} & tag_names, (
            f"expected wechat/miniprogram tag hit, got {tag_names!r}"
        )

    def test_run_dedupes_second_invocation(self, fetcher, monkeypatch):
        """Re-running with the same URLs → inserted=0 on second call."""
        _seed_xwatch_source(get_db())
        url = "https://x.com/wxapp/status/3333333333333333333"
        monkeypatch.setattr(
            "moni.fetch.sources.xwatch.search_tweet_urls",
            MagicMock(return_value=[url]),
        )
        monkeypatch.setattr(
            "moni.fetch.sources.xwatch.fetch_tweet",
            MagicMock(return_value={
                "id_str": "3333333333333333333",
                "text": "微信小程序 触达 体验讨论",
                "user": {"screen_name": "wxapp"},
                "created_at": "Wed Oct 22 21:30:00 +0000 2026",
            }),
        )
        monkeypatch.setattr(
            "moni.fetch.sources.xwatch._DEFAULTS",
            {**_DEFAULTS, "queries": ["q"], "request_delay": 0},
        )

        s1 = fetcher.run(dry_run=False)
        assert s1["inserted"] >= 1
        s2 = fetcher.run(dry_run=False)
        assert s2["inserted"] == 0  # dedupe_insert OR IGNORE


class TestRegistration:
    def test_xwatch_fetcher_registered(self):
        from moni.fetch.base import list_fetchers
        fetchers = list_fetchers()
        assert "xwatch" in fetchers
        assert fetchers["xwatch"] is FetcherXWatch
