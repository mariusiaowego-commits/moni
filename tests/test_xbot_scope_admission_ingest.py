"""sprint-55 / v1.0.131 — scope 准入的**端到端**（run() 级）验收。

与 ``test_xbot_scope_gate.py`` 的分工: 那边测 ``parse()`` 的闸门顺序与计数；
这里测 ``run()`` 全链路（含 ``fetch()`` 的 ``imported_ids`` 跳过、dedupe_insert、
state 落盘、结果 dict 的漏斗字段）。

口径（pV 交叉审修正后）:
* AC-13 二次幂等: 第二次 ``run()`` 里 ``fetch()`` 就按 ``imported_ids`` 跳过了这批
  条目 ⇒ ``scope_admitted`` 必须是 **0**（不是「仍计数但 inserted=0」）。
* ``scope_admitted`` 只计 **scope-only 救回**；``scope_matched_by`` 记 scope
  字段的总命中（含与领域词表双命中的）。
* 默认（库里没有显式存储 scope）→ 入库 URL 集合必须与 sprint-35 冻结的
  **改动前基线**逐条相同（集合相等，不是数量相等）。
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


def _baseline_urls() -> list[str]:
    """sprint-35 冻结的「门槛/scope 都未生效时」入库 URL 基线。

    从 ``test_xbot_thresholds`` 直接 import（同一 tests 目录），而不是
    在本轮代码上重算 —— 自我对照是零信息量的假绿。
    """
    try:
        from test_xbot_thresholds import BASELINE_INGESTED_URLS  # type: ignore
        return list(BASELINE_INGESTED_URLS)
    except Exception:  # pragma: no cover - 兜底: 从源码里取字面量
        import ast
        src = (_FIXTURE_DIR.parents[1] / "test_xbot_thresholds.py").read_text(
            encoding="utf-8")
        for node in ast.parse(src).body:
            if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "BASELINE_INGESTED_URLS" for t in node.targets
            ):
                return list(ast.literal_eval(node.value))
        raise


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
    for tag, kws in (("wechat", ["微信", "小程序"]), ("miniprogram", ["小程序"]),
                     ("general-tech", ["AI", "模型", "RSI"]),
                     ("ai-fintech", ["AI", "A股", "投顾", "量化"])):
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
        "bypass_keyword_filters": True,
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


def _scope_tag_urls() -> list[str]:
    return sorted(r["url"] for r in get_db().execute(
        "SELECT a.url FROM articles a JOIN article_tags t ON t.article_id = a.id "
        "WHERE a.source = ? AND t.tag_name = ?", (_SOURCE_NAME, SCOPE_ADMISSION_TAG)
    ).fetchall())


# ── 默认关闭: 与改动前基线逐条相同 ──────────────────────────────────────

def test_scope_absent_ingests_identical_pre_change_set(fresh_db, monkeypatch):
    """库里没有显式存储 scope → 与 sprint-35 冻结基线**集合相等**。"""
    fetcher = _prepare(fresh_db)
    clone = _make_clone(fresh_db)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    result = fetcher.run()

    assert _ingested_urls() == _baseline_urls()
    assert result["scope_gate_active"] is False
    assert result["scope_admitted"] == 0
    assert result["scope_admitted_by"] == {}
    assert _scope_tag_urls() == []


# ── 显式存储后才生效: 只多收 scope 命中的 ────────────────────────────────

def test_scope_stored_admits_scope_only_items(fresh_db, monkeypatch):
    scope = {
        "xcircle_accounts": ["dotey"],
        "wechat_regex": "微信|小程序",
        "wechat_queries": [],
    }
    fetcher = _prepare(fresh_db, {"scope": scope})
    clone = _make_clone(fresh_db)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    result = fetcher.run()

    urls = _ingested_urls()
    baseline = _baseline_urls()
    assert result["scope_gate_active"] is True
    assert set(urls) > set(baseline), "scope 生效后必须严格多收（OR 准入）"
    assert result["scope_admitted"] == len(urls) - len(baseline)
    assert result["scope_admitted"] > 0
    # 多收的每一条都必须挂 scope 专用 tag，且不是靠领域词表进来的
    rescued = sorted(set(urls) - set(baseline))
    assert _scope_tag_urls() == rescued
    # 漏斗口径: parsed = 基线 + scope-only 救回（不重复计数）
    assert result["parsed"] == result["inserted"] == len(urls)
    assert result["scope_admitted"] == result["parsed"] - len(baseline)


def test_scope_admitted_hits_carry_scope_hit_field(fresh_db, monkeypatch):
    scope = {"xcircle_accounts": ["dotey"], "wechat_regex": "", "wechat_queries": []}
    fetcher = _prepare(fresh_db, {"scope": scope})
    clone = _make_clone(fresh_db)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)
    fetcher.run()

    rows = get_db().execute(
        "SELECT h.hit_field, h.keyword FROM article_keyword_hits h "
        "JOIN articles a ON a.id = h.article_id "
        "WHERE a.source = ? AND h.tag_name = ? LIMIT 5",
        (_SOURCE_NAME, SCOPE_ADMISSION_TAG)).fetchall()
    assert rows, "scope 救回的条目必须留痕（article_keyword_hits, tag_name=xbot-scope）"
    for row in rows:
        assert row["hit_field"] == "scope"
        assert row["keyword"].startswith("account:")


# ── 二次幂等 (pV 修正: 第二次 scope_admitted 必须是 0) ────────────────────

def test_second_run_is_idempotent_and_scope_admitted_zero(fresh_db, monkeypatch):
    scope = {"xcircle_accounts": ["dotey"], "wechat_regex": "微信", "wechat_queries": []}
    fetcher = _prepare(fresh_db, {"scope": scope})
    clone = _make_clone(fresh_db)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    first = fetcher.run()
    assert first["inserted"] > 0
    first_admitted = first["scope_admitted"]
    assert first_admitted > 0

    # 同一批语料再跑一轮: 已准入的 id 进了 state → fetch() 不会再送进 parse;
    # 只有「被闸门丢掉、没进 state」的那批还会回来（这是 sprint-35 的既有语义:
    # 改关键词/改 scope 后它们必须还能被重新评估）。
    second = fetcher.run()
    assert second["inserted"] == 0
    assert second["fetched"] == first["fetched"] - first["inserted"], (
        "第二次 fetch() 必须按 imported_ids 跳过所有已准入条目"
        "（剩下的只能是当时被闸门丢掉的）"
    )
    assert second["scope_admitted"] == 0, (
        "第二次 run 里没有任何**新**条目靠 scope 进来 → scope_admitted 必须为 0；"
        "若不为 0 说明计数被记在了错误层次"
    )
    assert len(_ingested_urls()) == first["inserted"]


def test_scope_removed_keeps_imported_articles(fresh_db, monkeypatch):
    """回滚 scope 不会删已入库内容（只影响下一轮准入）。"""
    scope = {"xcircle_accounts": ["dotey"], "wechat_regex": "微信", "wechat_queries": []}
    fetcher = _prepare(fresh_db, {"scope": scope})
    clone = _make_clone(fresh_db)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)
    fetcher.run()
    before = _ingested_urls()
    assert before

    # 清掉存储的 scope（模拟回滚），再跑一轮
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
    assert result2["scope_gate_active"] is False
    assert result2["scope_admitted"] == 0
    assert _ingested_urls() == before, "回滚 scope 不得删掉已入库文章"


# ── 前置闸门在 run() 级也不松 ───────────────────────────────────────────

def test_author_block_still_drops_even_with_scope(fresh_db, monkeypatch):
    scope = {"xcircle_accounts": ["dotey"], "wechat_regex": "微信", "wechat_queries": []}
    fetcher = _prepare(fresh_db, {
        "scope": scope,
        "blacklisted_handles": ["dotey"],
    })
    clone = _make_clone(fresh_db)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    result = fetcher.run()

    assert result["author_blocked"] >= 1
    urls = _ingested_urls()
    assert not any("/dotey/" in u for u in urls), "被屏蔽作者的条目不得因 scope 命中入库"


def test_scope_admitted_items_are_not_counted_as_tag_filter_drops(fresh_db, monkeypatch):
    """scope 救回的条目走的是 tag 专用 hit，不该出现 tag 闸门丢弃计数串味。"""
    scope = {"xcircle_accounts": ["dotey"], "wechat_regex": "微信", "wechat_queries": []}
    fetcher = _prepare(fresh_db, {"scope": scope})
    clone = _make_clone(fresh_db)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)
    result = fetcher.run()

    assert result["dropped_tag_filter"] == 0
    assert result["dropped_blacklist"] == 0
    assert result["parse_errors"] == 0
