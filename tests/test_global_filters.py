"""tests/test_global_filters.py — Tests for v1.0.40 Global Keyword Filters (Sprint-20 PR2).

Verifies:
1. Migration 033: table exists and schema_version = 33
2. Backend API CRUD: GET, POST, PUT, DELETE /api/config/global-filters + 400 validations
3. Pipeline: Global Blacklist drop (reason='global_blacklist', tag_name='[global]')
4. Pipeline: Global Whitelist drop (reason='no_global_whitelist_match')
5. Pipeline: Union of Global + Source blacklists
6. Pipeline: Intersection of Global + Source whitelists
7. Pipeline: Independent match_field semantics
8. Regression: Pure pass-through when no global filters exist
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import get_db
from moni.db.migrate import current_version, migrate
from moni.fetch.filters import apply_keyword_filters
from moni.web.server import create_app


def _seed_source(conn, name):
    """INSERT the source into sources if missing (id 'src-<name>'), so source_keyword_filters can always be attached."""
    src_id = "src-" + name
    # Remove any pre-existing row so we can re-seed freely
    conn.execute("DELETE FROM source_tags WHERE source_id=?", (src_id,))
    conn.execute("DELETE FROM sources WHERE id=? OR name=?", (src_id, name))
    conn.commit()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, notes, fetch_config, created_at, updated_at) "
        "VALUES (?, ?, ?, 'json', ?, ?, ?, ?, ?, '2026-01-01', '2026-01-01')",
        (src_id, name, name, "https://example.com/" + name,
         360, 1, "test", None),
    )
    conn.commit()
    return src_id


@pytest.fixture(autouse=True)
def _setup_test_db():
    """Ensure migration 033 is applied and clean up global_keyword_filters + source_keyword_filters after each test."""
    migrate()
    conn = get_db()
    conn.execute("DELETE FROM global_keyword_filters")
    conn.execute("DELETE FROM source_keyword_filters WHERE notes='test'")
    conn.commit()
    yield
    conn = get_db()
    conn.execute("DELETE FROM global_keyword_filters")
    conn.execute("DELETE FROM source_keyword_filters WHERE notes='test'")
    conn.commit()


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app, base_url="http://127.0.0.1:8900")


# ── 1. Migration 033 Verification ──────────────────────────────────────────


def test_migration_033_schema_version():
    conn = get_db()
    v = current_version(conn)
    assert v >= 33, f"schema_version should be >= 33, got {v}"

    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='global_keyword_filters'"
    ).fetchone()
    assert row is not None, "global_keyword_filters table should exist"


# ── 2. Backend API CRUD ───────────────────────────────────────────────────


def test_api_global_filters_crud(client: TestClient):
    # 1. List initially empty
    r = client.get("/api/config/global-filters")
    assert r.status_code == 200
    assert r.json() == []

    # 2. Create global blacklist filter
    payload = {
        "mode": "blacklist",
        "match_field": "title+content",
        "keywords": ["博彩", "赌博", "诈骗"],
        "is_active": True,
        "notes": "涉诈全局黑名单",
    }
    r = client.post("/api/config/global-filters", json=payload)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["id"] > 0
    assert created["mode"] == "blacklist"
    assert created["keywords"] == ["博彩", "赌博", "诈骗"]
    filter_id = created["id"]

    # 3. Get single filter
    r = client.get(f"/api/config/global-filters/{filter_id}")
    assert r.status_code == 200
    assert r.json()["id"] == filter_id

    # 4. Update filter
    update_payload = {
        "keywords": ["博彩", "赌博", "诈骗", "外挂"],
        "notes": "更新备注",
    }
    r = client.put(f"/api/config/global-filters/{filter_id}", json=update_payload)
    assert r.status_code == 200
    updated = r.json()
    assert "外挂" in updated["keywords"]
    assert updated["notes"] == "更新备注"

    # 5. List with item
    r = client.get("/api/config/global-filters")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["id"] == filter_id

    # 6. Delete filter
    r = client.delete(f"/api/config/global-filters/{filter_id}")
    assert r.status_code == 204

    # 7. Get after delete -> 404
    r = client.get(f"/api/config/global-filters/{filter_id}")
    assert r.status_code == 404


def test_api_global_filters_validation_hard_guard(client: TestClient):
    # Empty keywords on create -> 400
    r = client.post(
        "/api/config/global-filters",
        json={"mode": "whitelist", "match_field": "title", "keywords": []},
    )
    assert r.status_code == 400
    assert "keywords" in r.text

    # Invalid mode -> 400
    r = client.post(
        "/api/config/global-filters",
        json={"mode": "invalid_mode", "match_field": "title", "keywords": ["AI"]},
    )
    assert r.status_code == 400

    # Invalid match_field -> 400
    r = client.post(
        "/api/config/global-filters",
        json={"mode": "blacklist", "match_field": "unknown_field", "keywords": ["AI"]},
    )
    assert r.status_code == 400


# ── 3. Pipeline: Global Blacklist ─────────────────────────────────────────


def test_pipeline_global_blacklist_drop():
    conn = get_db()
    # Insert global blacklist: "垃圾内容"
    conn.execute(
        """
        INSERT INTO global_keyword_filters
            (mode, match_field, keywords, is_active, notes, created_at, updated_at)
        VALUES ('blacklist', 'title+content', '[\"垃圾内容\"]', 1, 'test', 'now', 'now')
        """
    )
    conn.commit()

    articles = [
        {"source": "36kr_rss", "url": "http://example.com/1", "title": "优质金融科技报告", "content": "内容详实"},
        {"source": "36kr_rss", "url": "http://example.com/2", "title": "包含垃圾内容的文章", "content": "测试"},
    ]

    kept, stats, hits = apply_keyword_filters("36kr_rss", articles)
    assert len(kept) == 1
    assert kept[0]["url"] == "http://example.com/1"
    assert stats["dropped_global_blacklist"] == 1
    assert stats["dropped_blacklist"] >= 1

    # Check keyword hit
    hit_global = [h for h in hits if h["keyword"] == "垃圾内容"]
    assert len(hit_global) == 1
    assert hit_global[0]["tag_name"] == "[global]"
    assert hit_global[0]["filter_id"] is None

    # Check basket
    basket_row = conn.execute(
        "SELECT dropped_reason, dropped_by FROM basket WHERE url = 'http://example.com/2'"
    ).fetchone()
    assert basket_row is not None
    assert basket_row[0] == "global_blacklist"
    assert basket_row[1] == "垃圾内容"


# ── 4. Pipeline: Global Whitelist ─────────────────────────────────────────


def test_pipeline_global_whitelist_drop():
    conn = get_db()
    # Insert global whitelist: "金融", "银行"
    conn.execute(
        """
        INSERT INTO global_keyword_filters
            (mode, match_field, keywords, is_active, notes, created_at, updated_at)
        VALUES ('whitelist', 'title+content', '[\"金融\", \"银行\"]', 1, 'test', 'now', 'now')
        """
    )
    conn.commit()

    articles = [
        {"source": "mpaypass", "url": "http://example.com/w1", "title": "银行数字化转型", "content": "正文"},
        {"source": "mpaypass", "url": "http://example.com/w2", "title": "娱乐八卦新闻", "content": "完全无关"},
    ]

    kept, stats, hits = apply_keyword_filters("mpaypass", articles)
    assert len(kept) == 1
    assert kept[0]["url"] == "http://example.com/w1"
    assert stats["dropped_global_whitelist"] == 1
    assert stats["dropped_whitelist"] >= 1

    # Check basket reason
    basket_row = conn.execute(
        "SELECT dropped_reason, dropped_by FROM basket WHERE url = 'http://example.com/w2'"
    ).fetchone()
    assert basket_row is not None
    assert basket_row[0] == "global_whitelist"
    assert basket_row[1] == "no_global_whitelist_match"


# ── 5. Pipeline: Blacklist Union (Global + Source) ───────────────────────


def test_pipeline_blacklist_union():
    conn = get_db()
    # 1. Global blacklist: "全局违禁"
    conn.execute(
        """
        INSERT INTO global_keyword_filters
            (mode, match_field, keywords, is_active, notes, created_at, updated_at)
        VALUES ('blacklist', 'title+content', '[\"全局违禁\"]', 1, 'test', 'now', 'now')
        """
    )
    # 2. Source blacklist on 36kr_rss: "源级违禁"
    src_id = _seed_source(conn, "36kr_rss")
    conn.execute(
        """
        INSERT INTO source_keyword_filters
            (source_id, mode, match_field, keywords, is_active, notes, created_at, updated_at)
        VALUES (?, 'blacklist', 'title+content', '[\"源级违禁\"]', 1, 'test', 'now', 'now')
        """,
        (src_id,),
    )
    conn.commit()

    try:
        articles = [
            {"source": "36kr_rss", "url": "http://example.com/clean", "title": "正常科技文", "content": "ok"},
            {"source": "36kr_rss", "url": "http://example.com/hit_g", "title": "包含全局违禁词", "content": "bad"},
            {"source": "36kr_rss", "url": "http://example.com/hit_s", "title": "包含源级违禁词", "content": "bad"},
        ]
        kept, stats, _ = apply_keyword_filters("36kr_rss", articles)
        assert len(kept) == 1
        assert kept[0]["url"] == "http://example.com/clean"
        assert stats["dropped_global_blacklist"] == 1
        assert stats["dropped_blacklist"] == 2
    finally:
        conn.execute("DELETE FROM source_keyword_filters WHERE notes = 'test'")
        conn.commit()


# ── 6. Pipeline: Whitelist Intersection (Global + Source) ─────────────────


def test_pipeline_whitelist_intersection():
    conn = get_db()
    # Global whitelist: "科技"
    conn.execute(
        """
        INSERT INTO global_keyword_filters
            (mode, match_field, keywords, is_active, notes, created_at, updated_at)
        VALUES ('whitelist', 'title+content', '[\"科技\"]', 1, 'test', 'now', 'now')
        """
    )
    # Source whitelist on qbitai: "AI"
    src_id = _seed_source(conn, "qbitai")
    conn.execute(
        """
        INSERT INTO source_keyword_filters
            (source_id, mode, match_field, keywords, is_active, notes, created_at, updated_at)
        VALUES (?, 'whitelist', 'title+content', '[\"AI\"]', 1, 'test', 'now', 'now')
        """,
        (src_id,),
    )
    conn.commit()

    try:
        articles = [
            {"source": "qbitai", "url": "http://example.com/both", "title": "前沿科技与AI大模型", "content": "good"},
            {"source": "qbitai", "url": "http://example.com/only_g", "title": "前沿科技发展", "content": "no ai keyword"},
            {"source": "qbitai", "url": "http://example.com/only_s", "title": "智能AI创新应用", "content": "no tech keyword"},
        ]
        kept, stats, _ = apply_keyword_filters("qbitai", articles)
        # Only article with both passes
        assert len(kept) == 1
        assert kept[0]["url"] == "http://example.com/both"
    finally:
        conn.execute("DELETE FROM source_keyword_filters WHERE notes = 'test'")
        conn.commit()


# ── 7. Pipeline: Independent match_field Semantics ────────────────────────


def test_pipeline_independent_match_field():
    conn = get_db()
    # Global blacklist matches ONLY title: "炒股"
    conn.execute(
        """
        INSERT INTO global_keyword_filters
            (mode, match_field, keywords, is_active, notes, created_at, updated_at)
        VALUES ('blacklist', 'title', '[\"炒股\"]', 1, 'test', 'now', 'now')
        """
    )
    conn.commit()

    articles = [
        # "炒股" in content only -> should NOT be dropped by title-only filter
        {"source": "36kr_rss", "url": "http://example.com/c1", "title": "证券行业观察", "content": "炒股技巧分析"},
        # "炒股" in title -> dropped
        {"source": "36kr_rss", "url": "http://example.com/c2", "title": "今日炒股必看", "content": "证券行业观察"},
    ]

    kept, stats, hits = apply_keyword_filters("36kr_rss", articles)
    assert len(kept) == 1
    assert kept[0]["url"] == "http://example.com/c1"
    assert stats["dropped_global_blacklist"] == 1


# ── 8. Regression: Pass-through with No Global Filters ───────────────────


def test_pipeline_no_global_filters_passthrough():
    articles = [
        {"source": "unknown_src", "url": "http://example.com/u1", "title": "任选文章", "content": "内容"},
    ]
    kept, stats, hits = apply_keyword_filters("unknown_src", articles)
    assert len(kept) == 1
    assert stats["dropped_global_blacklist"] == 0
    assert stats["dropped_global_whitelist"] == 0


# ── 9. Inactive Global Filter Ignored ──────────────────────────────────────


def test_pipeline_inactive_global_filter_ignored():
    conn = get_db()
    # Insert inactive global blacklist: "已停用词"
    conn.execute(
        """
        INSERT INTO global_keyword_filters
            (mode, match_field, keywords, is_active, notes, created_at, updated_at)
        VALUES ('blacklist', 'title+content', '[\"已停用词\"]', 0, 'test', 'now', 'now')
        """
    )
    # Insert inactive global whitelist: "必须包含词"
    conn.execute(
        """
        INSERT INTO global_keyword_filters
            (mode, match_field, keywords, is_active, notes, created_at, updated_at)
        VALUES ('whitelist', 'title+content', '[\"必须包含词\"]', 0, 'test', 'now', 'now')
        """
    )
    conn.commit()

    articles = [
        {"source": "mpaypass", "url": "http://example.com/in1", "title": "包含已停用词的文章", "content": "内容"},
        {"source": "mpaypass", "url": "http://example.com/in2", "title": "不含必须包含词的文章", "content": "内容"},
    ]

    kept, stats, _ = apply_keyword_filters("mpaypass", articles)
    # Both articles should be kept because filters are is_active = 0
    assert len(kept) == 2
    assert stats["global_filters_applied"] == 0
    assert stats["dropped_global_blacklist"] == 0
    assert stats["dropped_global_whitelist"] == 0


# ── 10. Multiple Global Filters of Same Mode (OR Union) ───────────────────


def test_pipeline_multiple_global_filters_or_union():
    conn = get_db()
    # Insert two active global blacklist filters
    conn.execute(
        """
        INSERT INTO global_keyword_filters
            (mode, match_field, keywords, is_active, notes, created_at, updated_at)
        VALUES ('blacklist', 'title+content', '[\"违禁A\"]', 1, 'rule 1', 'now', 'now')
        """
    )
    conn.execute(
        """
        INSERT INTO global_keyword_filters
            (mode, match_field, keywords, is_active, notes, created_at, updated_at)
        VALUES ('blacklist', 'title+content', '[\"违禁B\"]', 1, 'rule 2', 'now', 'now')
        """
    )
    conn.commit()

    articles = [
        {"source": "36kr_rss", "url": "http://example.com/m1", "title": "命中违禁A", "content": "ok"},
        {"source": "36kr_rss", "url": "http://example.com/m2", "title": "命中违禁B", "content": "ok"},
        {"source": "36kr_rss", "url": "http://example.com/m3", "title": "安全干净文章", "content": "ok"},
    ]

    kept, stats, hits = apply_keyword_filters("36kr_rss", articles)
    assert len(kept) == 1
    assert kept[0]["url"] == "http://example.com/m3"
    assert stats["dropped_global_blacklist"] == 2
    assert stats["global_filters_applied"] == 2


# ── 11. Fetch Log & BaseFetcher Metrics Logging ───────────────────────────


def test_fetch_log_and_base_fetcher_metrics():
    from moni.cron.run_history import CronRunHistory
    from moni.fetch.base import BaseFetcher

    class DummyFetcher(BaseFetcher):
        source_name = "test_src"

        def fetch(self):
            return [
                {"source": "test_src", "url": "http://example.com/k1", "title": "正常科技文", "content": "ok"},
                {"source": "test_src", "url": "http://example.com/k2", "title": "包含拦截词的文章", "content": "bad"},
            ]

        def parse(self, raw_items):
            return raw_items

    history = CronRunHistory()
    cron_run_id = history.log_run("test_job")

    conn = get_db()
    # Add active global blacklist
    conn.execute(
        """
        INSERT INTO global_keyword_filters
            (mode, match_field, keywords, is_active, notes, created_at, updated_at)
        VALUES ('blacklist', 'title+content', '[\"拦截词\"]', 1, 'test', 'now', 'now')
        """
    )
    conn.commit()

    fetcher = DummyFetcher()

    result = fetcher.run(dry_run=True)
    assert result["dropped_global_blacklist"] == 1
    assert result["global_filters_applied"] == 1
    assert result["filtered"] == 1

    # Log to fetch_log
    history = CronRunHistory()
    history.log_fetch(
        cron_run_rowid=cron_run_id,
        source="test_src",
        fetched=result["fetched"],
        parsed=result["parsed"],
        inserted=result["inserted"],
        parse_errors=result["parse_errors"],
        cleaned=result["cleaned"],
        filtered=result["filtered"],
        dropped_blacklist=result["dropped_blacklist"],
        dropped_whitelist=result["dropped_whitelist"],
        dropped_global_blacklist=result["dropped_global_blacklist"],
        dropped_global_whitelist=result["dropped_global_whitelist"],
        dropped_tag_filter=result["dropped_tag_filter"],
        filters_applied=result["filters_applied"],
        global_filters_applied=result["global_filters_applied"],
        tags_used=result["tags_used"],
        deduped=0,
    )

    log_row = conn.execute(
        "SELECT dropped_global_blacklist, dropped_global_whitelist, global_filters_applied FROM fetch_log WHERE cron_run_rowid = ?",
        (cron_run_id,),
    ).fetchone()
    assert log_row is not None
    assert log_row[0] == 1  # dropped_global_blacklist
    assert log_row[1] == 0  # dropped_global_whitelist
    assert log_row[2] == 1  # global_filters_applied
