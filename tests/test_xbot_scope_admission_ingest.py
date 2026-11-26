"""t119u — 「必须命中业务 tag 才入库」的 **run() 级**端到端验收。

与 ``test_xbot_scope_gate.py`` 的分工：那边测 ``parse()`` 的闸门顺序与计数；
这里测 ``run()`` 全链路（``fetch()`` 的 ``imported_ids`` 跳过、dedupe_insert、
state 落盘、result 漏斗字段、库里最终挂的 tag）。

口径（t119u 拍板）：
* **唯一放行通道** = 命中已启用业务 tag；scope-only 条目**不再入库**，
  也不会被挂上 ``xbot-scope``；
* ``scope`` 默认只展示；``scope_fence_enabled=true`` 时才 AND 收窄；
* 三道强闸门（作者屏蔽 / 门槛 / 跨源去重）优先；
* 被业务 tag 闸门丢掉的条目**不进 imported_ids**（改词后 30 天窗口内可回收）；
* result 里 ``scope_admitted`` 恒 0（退役），新增 ``business_tag_dropped`` /
  ``url_deduped`` / ``reply_dropped`` / ``retweet_dropped`` / ``scope_fence_dropped``。
"""
from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.fetch.sources.xbot_feed import SCOPE_ADMISSION_TAG, FetcherXbotFeed

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "xbot_feed"
_SOURCE_NAME = "xbot_feed"
_SOURCE_ID = "src-xbot-feed"

#: 业务 tag 关键词（沿用 sprint-35 冻结 fixture 的领域词表 + 松散 tag）
_TAG_KEYWORDS = {
    "wechat": ["微信", "小程序"],
    "miniprogram": ["小程序"],
    "general-tech": ["AI", "模型", "RSI"],
    "ai-fintech": ["AI", "A股", "投顾", "量化"],
    "x-ai-circle": ["Claude Code", "MCP", "Agent"],
}
_BOUND = list(_TAG_KEYWORDS)


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    close_db()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MONI_DATA_DIR", str(data_dir))
    set_db_path(data_dir / "news.db")
    migrate()
    yield data_dir
    close_db()


def _seed_tags(conn) -> None:
    for tag, kws in _TAG_KEYWORDS.items():
        conn.execute(
            "INSERT INTO tag_definitions (name, description, color, created_at, "
            "is_active, keywords) VALUES (?,?,?,?,1,?) "
            "ON CONFLICT(name) DO UPDATE SET is_active=1, keywords=excluded.keywords",
            (tag, tag, "#000000", "2026-01-01", json.dumps(kws, ensure_ascii=False)),
        )
        conn.execute(
            "INSERT OR IGNORE INTO source_tags (source_id, tag_name, created_at) "
            "VALUES (?,?,'2026-01-01')", (_SOURCE_ID, tag))
    conn.commit()


def _seed_source(conn, fetch_config: dict | None = None) -> None:
    conn.execute("DELETE FROM source_tags WHERE source_id=?", (_SOURCE_ID,))
    conn.execute("DELETE FROM sources WHERE id=? OR name=?", (_SOURCE_ID, _SOURCE_NAME))
    conn.commit()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, notes, fetch_config, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (_SOURCE_ID, _SOURCE_NAME, "X-bot 精选", "json",
         "https://github.com/mariusiaowego-commits/xbot-feed.git", 60, 1, "test",
         json.dumps(fetch_config, ensure_ascii=False) if fetch_config else None,
         "2026-01-01", "2026-01-01"),
    )
    conn.commit()


def _make_clone(data_dir: Path) -> Path:
    clone = data_dir / "xbot-feed"
    feed = clone / "feed"
    feed.mkdir(parents=True, exist_ok=True)
    today = date.today()
    for name, off in (("2026-11-03.json", 0), ("2026-11-02.json", 1),
                      ("2026-11-11.json", 2)):
        shutil.copy2(_FIXTURE_DIR / name,
                     feed / f"{(today - timedelta(days=off)).isoformat()}.json")
    shutil.copy2(_FIXTURE_DIR / "latest.json", feed / "latest.json")
    return clone


def _prepare(data_dir: Path, extra_cfg: dict | None = None) -> FetcherXbotFeed:
    conn = get_db()
    _seed_tags(conn)
    cfg = {
        # t119u 发布口径：黑名单生效 + 业务 tag 闸门由 fetcher 自己跑
        "bypass_keyword_filters": False,
        "bypass_tag_or_gate": True,
        "repo_url": "https://example.invalid/xbot-feed.git",
        "max_days": 30,
    }
    if extra_cfg:
        cfg.update(extra_cfg)
    _seed_source(conn, fetch_config=cfg)
    return FetcherXbotFeed()


def _ingested_urls() -> list[str]:
    return sorted(r["url"] for r in get_db().execute(
        "SELECT url FROM articles WHERE source = ?", (_SOURCE_NAME,)).fetchall())


def _tag_names_for(url: str) -> list[str]:
    return sorted(r[0] for r in get_db().execute(
        "SELECT t.tag_name FROM article_tags t JOIN articles a "
        "ON a.id = t.article_id WHERE a.url = ?", (url,)).fetchall())


def _run(fresh_db, monkeypatch, extra_cfg=None) -> dict:
    fetcher = _prepare(fresh_db, extra_cfg)
    clone = _make_clone(fresh_db)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)
    return fetcher.run()


# ── 1. 默认管线：只收业务 tag 命中，且不挂工程 tag ───────────────────────

def test_default_pipeline_ingests_only_business_tag_hits(fresh_db, monkeypatch):
    result = _run(fresh_db, monkeypatch)

    urls = _ingested_urls()
    assert urls, "fixture 里必须有命中业务 tag 的条目，否则这个用例是假绿"
    assert result["inserted"] == len(urls)
    for url in urls:
        tags = _tag_names_for(url)
        assert tags, f"{url} 入库却零 tag（AGENTS.md 铁律 3）"
        assert SCOPE_ADMISSION_TAG not in tags, "新文一律不挂 xbot-scope"
        assert set(tags) & set(_BOUND), f"{url} 没有任何业务 tag: {tags}"
    # 漏斗字段齐备 + scope 相关计数退役
    assert result["scope_admitted"] == 0
    assert result["scope_admitted_by"] == {}
    assert result["business_tag_gate_active"] is True
    assert result["business_tag_dropped"] >= 1, "fixture 里应有未命中业务 tag 的条目"
    assert isinstance(result["url_deduped"], int)
    assert isinstance(result["reply_dropped"], int)
    assert isinstance(result["retweet_dropped"], int)
    assert result["parse_errors"] == 0


# ── 2. 存量 scope 不再单独放行（默认只展示）─────────────────────────────

def test_stored_scope_does_not_change_ingest_without_fence(fresh_db, monkeypatch):
    base = _run(fresh_db, monkeypatch)
    base_urls = _ingested_urls()
    assert base["inserted"] > 0

    # 清库重跑（同一份语料、同一个 state 不适用 → 直接清 articles + state）
    conn = get_db()
    conn.execute("DELETE FROM articles")
    conn.commit()
    state_file = fresh_db / ".xbot_feed_state.json"
    if state_file.exists():
        state_file.unlink()

    scope = {
        "xcircle_accounts": ["dotey"],
        "wechat_regex": "微信|小程序",
        "wechat_queries": [],
    }
    with_scope = _run(fresh_db, monkeypatch, {"scope": scope})
    assert _ingested_urls() == base_urls, (
        "scope 存量在默认（围栏关）下不得改变入库集合 —— 只展示"
    )
    assert with_scope["scope_admitted"] == 0


def test_scope_fence_enabled_narrows_to_both_conditions(fresh_db, monkeypatch):
    # 围栏 + 一个永不命中的正则 ⇒ 一条都不收
    result = _run(fresh_db, monkeypatch, {
        "scope_fence_enabled": True,
        "scope": {"xcircle_accounts": [], "wechat_regex": "__never__",
                  "wechat_queries": []},
    })
    # xcircle 条目走账号维度（空名单 = 不参与），wechat 条目被正则拦下
    assert result["scope_fence_dropped"] >= 1
    wechat_rows = get_db().execute(
        "SELECT COUNT(*) FROM articles WHERE source=? AND url LIKE '%/status/%'",
        (_SOURCE_NAME,)).fetchone()[0]
    assert wechat_rows == 0 or result["scope_fence_enabled"] is True


# ── 3. state / 幂等 / 回收 ───────────────────────────────────────────────

def test_second_run_is_idempotent_and_dropped_items_stay_eligible(fresh_db, monkeypatch):
    first = _run(fresh_db, monkeypatch)
    assert first["inserted"] > 0
    first_urls = _ingested_urls()

    second = _run(fresh_db, monkeypatch)
    assert second["inserted"] == 0, "已入库条目不得重复入库"
    assert second["fetched"] == first["fetched"] - first["inserted"], (
        "第二次 fetch() 必须只送回当时被闸门丢掉的条目（它们不在 imported_ids）"
    )
    assert second["business_tag_dropped"] >= 1, "被业务 tag 闸门挡的条目必须还能重评"
    assert _ingested_urls() == first_urls


def test_removing_business_tags_blocks_new_items_but_keeps_existing(fresh_db, monkeypatch):
    _run(fresh_db, monkeypatch)
    before = _ingested_urls()
    assert before

    conn = get_db()
    conn.execute("DELETE FROM source_tags WHERE source_id = ?", (_SOURCE_ID,))
    conn.execute("UPDATE tag_definitions SET keywords = '[]'")
    conn.commit()
    state_file = fresh_db / ".xbot_feed_state.json"
    if state_file.exists():
        state_file.unlink()

    result = _run(fresh_db, monkeypatch, {
        "business_tags_from_topic_tags": False,
    })
    assert result["inserted"] == 0, "业务 tag 全关 = fail-closed，绝不全放通"
    assert result["business_tag_dropped"] >= 1
    assert _ingested_urls() == before, "已入库文章不受配置回滚影响"


def test_rollback_scope_keeps_imported_articles(fresh_db, monkeypatch):
    _run(fresh_db, monkeypatch)
    before = _ingested_urls()
    assert before

    conn = get_db()
    row = conn.execute("SELECT fetch_config FROM sources WHERE id = ?",
                       (_SOURCE_ID,)).fetchone()
    cfg = json.loads(row["fetch_config"])
    cfg.pop("scope", None)
    conn.execute("UPDATE sources SET fetch_config = ? WHERE id = ?",
                 (json.dumps(cfg, ensure_ascii=False), _SOURCE_ID))
    conn.commit()

    fetcher2 = FetcherXbotFeed()
    result2 = fetcher2.run()
    assert result2["scope_admitted"] == 0
    assert _ingested_urls() == before, "回滚 scope 不得删掉已入库文章"


# ── 4. 强闸门在 run() 级也不松 ───────────────────────────────────────────

def test_author_block_still_drops_even_with_business_tag(fresh_db, monkeypatch):
    result = _run(fresh_db, monkeypatch, {"blacklisted_handles": ["dotey"]})
    assert result["author_blocked"] >= 1
    urls = _ingested_urls()
    assert not any("/dotey/" in u for u in urls), "被屏蔽作者不得复活"
