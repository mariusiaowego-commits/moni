"""sprint-35 — X-bot 精选 scope / thresholds API 测试。

覆盖 done-criteria AC-1 / AC-2 / AC-3 / AC-12 / AC-13 + 闸门 G-2 / G-4。

口径（Mttt 2026-11-11 16:40 GO IMPL）：
- Q1 = A → 门槛默认全 0
- Q3 = A → scope 在现有 fetch_config 弹窗内的 X-bot 专属区块展示
- scope **可编辑并保存**（moni 侧副本），但**不参与入库闸门**（不改上游搜索）
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.web.server import create_app

_SOURCE_NAME = "xbot_feed"
_SOURCE_ID = "src-xbot-feed"
_OTHER_SOURCE_ID = "src-36kr"
_OTHER_SOURCE_NAME = "36kr_rss"

# 上游 28 个 xcircle 账号（brief 快照 2026-11-11）
XCIRCLE_ACCOUNTS = [
    "dotey", "Gorden_Sun", "lxfater", "op7418", "oran_ge", "vista8",
    "imxiaohu", "FinanceYF5", "ruanyf", "onevcat", "fenng", "yihong0618",
    "waylybaye", "cloudwu", "wshuyi", "shao__meng", "Psyche_Bastion",
    "hi_xiaopeng", "javacruiser", "manateelazycat", "nekocode", "soasme",
    "leveaal", "excalith", "turingou", "orangexiao", "liang2min", "huangyunmin",
]

WECHAT_REGEX = "微信|WeChat|Weixin|小程序|公众号|视频号|mini program"


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


@pytest.fixture
def client(fresh_db):
    app = create_app()
    with TestClient(app) as c:
        yield c


def _seed_xbot(conn, fetch_config=None):
    conn.execute("DELETE FROM sources WHERE id=? OR name=?", (_SOURCE_ID, _SOURCE_NAME))
    conn.commit()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, notes, fetch_config, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (_SOURCE_ID, _SOURCE_NAME, "X-bot 精选", "json",
         "https://github.com/mariusiaowego-commits/xbot-feed.git", 60, 1, "test",
         fetch_config, "2026-01-01", "2026-01-01"),
    )
    conn.commit()


def _seed_other(conn):
    conn.execute("DELETE FROM sources WHERE id=?", (_OTHER_SOURCE_ID,))
    conn.commit()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, notes, fetch_config, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (_OTHER_SOURCE_ID, _OTHER_SOURCE_NAME, "36氪", "rss", "https://36kr.com", 30,
         1, "test", None, "2026-01-01", "2026-01-01"),
    )
    conn.commit()


def _write_upstream_feed(data_dir: Path, scope=None):
    """在隔离数据目录里造一份上游 feed JSON（可选带顶层 scope 键）。"""
    clone = data_dir / "xbot-feed"
    feed = clone / "feed"
    feed.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": date.today().isoformat(),
        "generated_at": "2026-11-11T08:31:42+08:00",
        "counts_by_topic": {"wechat": 3, "xcircle": 5},
        "items": [],
    }
    if scope is not None:
        payload["scope"] = scope
    stamp = date.today().isoformat()
    (feed / f"{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return clone


def _stored_fetch_config(conn):
    row = conn.execute(
        "SELECT fetch_config FROM sources WHERE id = ?", (_SOURCE_ID,)).fetchone()
    return row["fetch_config"]


def _sha_of_stored(conn):
    import hashlib
    return hashlib.sha256((_stored_fetch_config(conn) or "").encode()).hexdigest()


# ── AC-1: GET /scope ────────────────────────────────────────────────────

def test_get_scope_returns_fallback_mode(client, fresh_db):
    conn = get_db()
    _seed_xbot(conn)
    resp = client.get(f"/api/config/sources/{_SOURCE_ID}/scope")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source_id"] == _SOURCE_ID
    assert data["source_name"] == _SOURCE_NAME
    assert data["scope_source"] == "moni_fallback"
    assert data["upstream_scope_available"] is False
    assert len(data["scope"]["xcircle_accounts"]) == 28
    assert "微信" in data["scope"]["wechat_regex"]
    assert "mini program" in data["scope"]["wechat_regex"]
    assert data["scope"]["wechat_queries"] == []
    assert data["thresholds"] == {
        "wechat": {"min_followers": 0, "min_likes": 0},
        "xcircle": {"min_followers": 0, "min_likes": 0},
    }
    assert data["scope_gates_ingest"] is False


def test_get_scope_upstream_mode(client, fresh_db):
    """上游 feed JSON 顶层有 scope 键 → scope_source == upstream。"""
    conn = get_db()
    _seed_xbot(conn)
    _write_upstream_feed(fresh_db, scope={
        "xcircle_accounts": ["aaa", "bbb"],
        "wechat_regex": "上游正则",
        "wechat_queries": ["微信小程序 支付"],
        "thresholds": {"wechat": {"min_followers": 5000, "min_likes": 5}},
    })
    resp = client.get(f"/api/config/sources/{_SOURCE_ID}/scope")
    assert resp.status_code == 200
    data = resp.json()
    assert data["scope_source"] == "upstream"
    assert data["upstream_scope_available"] is True
    assert data["scope"]["xcircle_accounts"] == ["aaa", "bbb"]
    assert data["scope"]["wechat_queries"] == ["微信小程序 支付"]


def test_get_scope_not_found(client):
    resp = client.get("/api/config/sources/nope-does-not-exist/scope")
    assert resp.status_code == 404


def test_get_scope_rejects_non_xbot_source(client):
    conn = get_db()
    _seed_other(conn)
    resp = client.get(f"/api/config/sources/{_OTHER_SOURCE_ID}/scope")
    assert resp.status_code == 400
    assert "xbot_feed" in resp.text


# ── G-4: fetch_config 响应里的 thresholds 默认全 0，且 web 默认值表不变 ──

def test_fetch_config_response_has_default_thresholds(client):
    conn = get_db()
    _seed_xbot(conn)
    resp = client.get(f"/api/config/sources/{_SOURCE_ID}/fetch_config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fetch_config"]["thresholds"] == {
        "wechat": {"min_followers": 0, "min_likes": 0},
        "xcircle": {"min_followers": 0, "min_likes": 0},
    }
    # G-4：web 层的默认值表仍必须 == DEFAULT_FETCH_CONFIG（不含新键）
    from moni.fetch.sources.xbot_feed import DEFAULT_FETCH_CONFIG
    from moni.web.routes.config_sources import _FETCH_CONFIG_DEFAULTS
    assert _FETCH_CONFIG_DEFAULTS[_SOURCE_NAME] == DEFAULT_FETCH_CONFIG
    assert "thresholds" not in _FETCH_CONFIG_DEFAULTS[_SOURCE_NAME]


def test_other_source_merged_has_no_xbot_keys(client):
    """其它信源的 merged 不含 xbot 专属键（AC-10.3）。"""
    conn = get_db()
    _seed_other(conn)
    resp = client.get(f"/api/config/sources/{_OTHER_SOURCE_ID}/fetch_config")
    assert resp.status_code == 200
    merged = resp.json()["fetch_config"]
    assert "thresholds" not in merged
    assert "scope" not in merged


# ── AC-2: PUT 非法门槛 → 400 且不写库 ───────────────────────────────────

@pytest.mark.parametrize("bad", [
    {"wechat": {"min_followers": -1, "min_likes": 0}},
    {"wechat": {"min_followers": "5", "min_likes": 0}},
    {"wechat": {"min_followers": 1.5, "min_likes": 0}},
    {"wechat": {"min_followers": True, "min_likes": 0}},
    {"zhihu": {"min_followers": 10, "min_likes": 0}},
    {"wechat": "not-a-dict"},
])
def test_put_rejects_invalid_thresholds_and_does_not_write_db(client, bad):
    conn = get_db()
    _seed_xbot(conn)
    before = _sha_of_stored(conn)
    resp = client.put(
        f"/api/config/sources/{_SOURCE_ID}/fetch_config",
        json={"thresholds": bad},
    )
    assert resp.status_code == 400, resp.text
    assert _sha_of_stored(conn) == before


def test_put_rejects_thresholds_not_a_dict(client):
    conn = get_db()
    _seed_xbot(conn)
    before = _sha_of_stored(conn)
    resp = client.put(
        f"/api/config/sources/{_SOURCE_ID}/fetch_config",
        json={"thresholds": "nope"},
    )
    assert resp.status_code == 400
    assert _sha_of_stored(conn) == before


# ── AC-3: PUT 合法门槛 → 200，回读一致，其它键不被抹掉 ──────────────────

def test_update_preserves_existing_keys(client):
    conn = get_db()
    original = {
        "repo_url": "https://github.com/mariusiaowego-commits/xbot-feed.git",
        "clone_dir": "",
        "bypass_keyword_filters": True,
        "blacklisted_handles": ["jkghzone"],
        "topic_tags": {"wechat": ["wechat"], "xcircle": ["general-tech"]},
        "max_days": 30,
        "git_timeout": 120,
        "skip_existing_urls": True,
    }
    _seed_xbot(conn, fetch_config=json.dumps(original, ensure_ascii=False))
    resp = client.put(
        f"/api/config/sources/{_SOURCE_ID}/fetch_config",
        json={"thresholds": {"wechat": {"min_followers": 5000, "min_likes": 5},
                             "xcircle": {"min_followers": 0, "min_likes": 0}}},
    )
    assert resp.status_code == 200, resp.text

    stored = json.loads(_stored_fetch_config(conn))
    # 新门槛写进去了
    assert stored["thresholds"] == {
        "wechat": {"min_followers": 5000, "min_likes": 5},
        "xcircle": {"min_followers": 0, "min_likes": 0},
    }
    # 原有 8 个键一个都没被抹掉
    for key in ("repo_url", "clone_dir", "bypass_keyword_filters",
                "blacklisted_handles", "topic_tags", "max_days",
                "git_timeout", "skip_existing_urls"):
        assert key in stored, f"{key} was wiped"
        assert stored[key] == original[key], f"{key} was rewritten"

    # GET 回读逐字一致
    got = client.get(f"/api/config/sources/{_SOURCE_ID}/fetch_config").json()
    assert got["fetch_config"]["thresholds"] == stored["thresholds"]


def test_non_xbot_source_keeps_whole_body_replace_semantics(client):
    """P1-4: 校验只作用于 xbot_feed; 其它信源的 thresholds/scope 键按普通键透传。

    修复前: 校验块在 xbot 分支**之外** → bestblogs 收到
    ``{"scope": {"whatever": ["a"]}}`` 会 400, 破坏了 G-2
    ("其它信源维持现状整体替换 body")。
    """
    conn = get_db()
    _seed_other(conn)
    resp = client.put(
        f"/api/config/sources/{_OTHER_SOURCE_ID}/fetch_config",
        json={"scope": {"whatever": ["a"]}})
    assert resp.status_code == 200, resp.text
    stored = json.loads(
        conn.execute("SELECT fetch_config FROM sources WHERE id = ?",
                     (_OTHER_SOURCE_ID,)).fetchone()["fetch_config"])
    # 整体替换: 只有 body 里的键, 没有被合并进来
    assert stored == {"scope": {"whatever": ["a"]}}


def test_non_xbot_source_accepts_invalid_thresholds_shape(client):
    """P1-4: 其它信源的 thresholds 键不做 xbot 形状校验 (照常透传)。"""
    conn = get_db()
    _seed_other(conn)
    resp = client.put(
        f"/api/config/sources/{_OTHER_SOURCE_ID}/fetch_config",
        json={"thresholds": {"whatever": {"min_followers": "not-a-number"}}})
    assert resp.status_code == 200, resp.text


def test_put_empty_body_clears_for_non_xbot_source(client):
    """G-2: 其它信源仍是"整体替换"语义 → PUT {} 清空（既有行为不变）。"""
    conn = get_db()
    _seed_other(conn)
    conn.execute(
        "UPDATE sources SET fetch_config = ? WHERE id = ?",
        (json.dumps({"max_feeds": 50}), _OTHER_SOURCE_ID))
    conn.commit()
    resp = client.put(f"/api/config/sources/{_OTHER_SOURCE_ID}/fetch_config", json={})
    assert resp.status_code == 200
    assert resp.json()["has_custom"] is False


# ── AC-12: scope 可编辑并保存；非法 scope → 400 不写库 ──────────────────

def test_put_scope_saves_and_reads_back(client):
    conn = get_db()
    _seed_xbot(conn)
    scope = {
        "xcircle_accounts": ["aaa", "bbb"],
        "wechat_regex": "自定义正则",
        "wechat_queries": ["查询一"],
    }
    resp = client.put(
        f"/api/config/sources/{_SOURCE_ID}/fetch_config", json={"scope": scope})
    assert resp.status_code == 200, resp.text
    stored = json.loads(_stored_fetch_config(conn))
    assert stored["scope"] == scope

    got = client.get(f"/api/config/sources/{_SOURCE_ID}/scope").json()
    # P1-1 (option b): 三态 —— 上游没导出 + moni 侧有副本 → moni_stored
    assert got["scope_source"] == "moni_stored"
    assert got["scope_edited"] is True
    assert got["scope"]["xcircle_accounts"] == ["aaa", "bbb"]


def test_scope_source_is_monI_stored_after_user_edit(client):
    """P1-1: 用户编辑并保存 scope → scope_source == "moni_stored"。"""
    conn = get_db()
    _seed_xbot(conn)
    resp = client.put(
        f"/api/config/sources/{_SOURCE_ID}/fetch_config",
        json={"scope": {"xcircle_accounts": ["a"], "wechat_regex": "R",
                        "wechat_queries": []}})
    assert resp.status_code == 200
    got = client.get(f"/api/config/sources/{_SOURCE_ID}/scope").json()
    assert got["scope_source"] == "moni_stored"
    assert got["scope_edited"] is True
    assert got["upstream_scope_available"] is False


def test_scope_source_is_monI_fallback_without_user_edit(client):
    """P1-1: 没有用户副本 + 上游没导出 → moni_fallback (兜底快照)。"""
    conn = get_db()
    _seed_xbot(conn)
    got = client.get(f"/api/config/sources/{_SOURCE_ID}/scope").json()
    assert got["scope_source"] == "moni_fallback"
    assert got["scope_edited"] is False


def test_scope_source_is_upstream_when_feed_exports_it(client, fresh_db):
    """P1-1: 上游导出 scope → scope_source == "upstream" (优先于 moni 副本)。"""
    conn = get_db()
    _seed_xbot(conn)
    client.put(
        f"/api/config/sources/{_SOURCE_ID}/fetch_config",
        json={"scope": {"xcircle_accounts": ["moni-side"], "wechat_regex": "R",
                        "wechat_queries": []}})
    _write_upstream_feed(fresh_db, scope={
        "xcircle_accounts": ["upstream-side"],
        "wechat_regex": "上游正则",
        "wechat_queries": [],
    })
    got = client.get(f"/api/config/sources/{_SOURCE_ID}/scope").json()
    assert got["scope_source"] == "upstream"
    assert got["upstream_scope_available"] is True
    assert got["scope"]["xcircle_accounts"] == ["upstream-side"]


@pytest.mark.parametrize("bad", [
    "not-a-dict",
    {"xcircle_accounts": "not-a-list", "wechat_regex": "x", "wechat_queries": []},
    {"xcircle_accounts": [1, 2], "wechat_regex": "x", "wechat_queries": []},
    {"xcircle_accounts": [], "wechat_regex": 123, "wechat_queries": []},
])
def test_put_rejects_invalid_scope_and_does_not_write_db(client, bad):
    conn = get_db()
    _seed_xbot(conn)
    before = _sha_of_stored(conn)
    resp = client.put(
        f"/api/config/sources/{_SOURCE_ID}/fetch_config", json={"scope": bad})
    assert resp.status_code == 400, resp.text
    assert _sha_of_stored(conn) == before


def test_scope_does_not_gate_ingest():
    """scope 是记录与展示用的副本 → 没有任何 ingest 路径读它。"""
    import inspect

    from moni.fetch.sources import xbot_feed
    src = inspect.getsource(xbot_feed)
    # parse() 里不能用 cfg["scope"] 做闸门
    parse_src = src.split("def parse(", 1)[1].split("\n    def ", 1)[0]
    assert "scope" not in parse_src
    # 判定用的只有 thresholds
    assert "evaluate_thresholds" in parse_src


def test_scope_edit_does_not_change_ingested_url_set():
    """AC-12 **行为**证据: 改 scope 前后入库 URL 集合完全相同。

    静态断言 (``"scope" not in parse_src``) 只证明"代码没读它"；这条用
    真实抓取跑两遍 (scope 不同) → 证明"改了也没用"。
    """
    import shutil
    from datetime import date, timedelta

    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    fixture_dir = Path(__file__).parent / "fixtures" / "xbot_feed"

    def _run(scope_value):
        close_db()
        data_dir = Path(tempfile.mkdtemp(prefix="moni-scope-ac12-"))
        os.environ["MONI_DATA_DIR"] = str(data_dir)
        set_db_path(data_dir / "news.db")
        migrate()
        conn = get_db()
        conn.execute("DELETE FROM source_tags WHERE source_id = ?",
                     ("src-xbot-feed",))
        conn.execute("DELETE FROM sources WHERE id = ? OR name = ?",
                     ("src-xbot-feed", "xbot_feed"))
        conn.commit()
        conn.execute(
            "INSERT INTO sources (id, name, display_name, source_type, url, "
            "fetch_interval_min, is_active, notes, fetch_config, created_at, "
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("src-xbot-feed", "xbot_feed", "X-bot 精选", "json",
             "https://github.com/mariusiaowego-commits/xbot-feed.git", 60, 1, "t",
             json.dumps({
                 "bypass_keyword_filters": True,
                 "repo_url": "https://example.invalid/xbot-feed.git",
                 "max_days": 30,
                 "scope": scope_value,
             }, ensure_ascii=False), "2026-01-01", "2026-01-01"),
        )
        for tag, kws in (("wechat", ["微信", "小程序"]),
                         ("miniprogram", ["小程序"]),
                         ("general-tech", ["AI", "模型", "RSI"]),
                         ("ai-fintech", ["AI", "A股", "投顾", "量化"])):
            conn.execute(
                "INSERT INTO tag_definitions (name, description, color, created_at, "
                "is_active, keywords) VALUES (?,?,?,?,1,?)",
                (tag, tag, "#000000", "2026-01-01",
                 json.dumps(kws, ensure_ascii=False)),
            )
            conn.execute(
                "INSERT OR IGNORE INTO source_tags (source_id, tag_name, "
                "created_at) VALUES (?,?,'2026-01-01')", ("src-xbot-feed", tag))
        conn.commit()

        clone = data_dir / "xbot-feed"
        feed = clone / "feed"
        feed.mkdir(parents=True, exist_ok=True)
        today = date.today()
        for name, off in (("2026-11-03.json", 0), ("2026-11-02.json", 1)):
            shutil.copy2(fixture_dir / name,
                         feed / f"{(today - timedelta(days=off)).isoformat()}.json")
        shutil.copy2(fixture_dir / "latest.json", feed / "latest.json")

        fetcher = FetcherXbotFeed()
        fetcher._sync_clone = lambda cfg: clone  # type: ignore[assignment]
        fetcher.run()
        urls = sorted(r["url"] for r in conn.execute(
            "SELECT url FROM articles WHERE source = ?", ("xbot_feed",)).fetchall())
        close_db()
        shutil.rmtree(data_dir, ignore_errors=True)
        return urls

    scope_a = {"xcircle_accounts": ["dotey"], "wechat_regex": "微信",
               "wechat_queries": []}
    scope_b = {"xcircle_accounts": ["someone-else", "another"], "wechat_regex": "完全不同的正则",
               "wechat_queries": ["查询一", "查询二"]}

    urls_a = _run(scope_a)
    urls_b = _run(scope_b)

    assert urls_a, "基线跑出 0 条 → 断言无意义"
    assert urls_a == urls_b, "改 scope 改变了入库集合 → scope 参与了闸门"


# ── AC-13: migrate-only 升级不覆盖 thresholds / scope ───────────────────
#
# 口径 (sprint-35 实施时修订, 见 PLAN §7):
#   warden G-7 + R-8 明确: **不改全局 seed 语义**, 是否给 thresholds 单独
#   做"seed 不覆盖"保护由 dad 另拍 → 本 sprint **不动 moni/db/seed_config.py**。
#   那么可验证的保证是: **升级路径 (migrate-only) 不碰 sources 行** →
#   dad 配的 thresholds / scope 在升级后原样保留。

def test_migrate_only_upgrade_preserves_thresholds_and_scope(fresh_db):
    """migrate-only 升级 (python3 -m moni.db.migrate) 不覆盖用户配置。"""
    from moni.db.migrate import migrate as run_migrate

    conn = get_db()
    _seed_xbot(conn, fetch_config=json.dumps({
        "repo_url": "https://github.com/mariusiaowego-commits/xbot-feed.git",
        "thresholds": {"wechat": {"min_followers": 5000, "min_likes": 5}},
        "scope": {"xcircle_accounts": ["zzz"], "wechat_regex": "R",
                  "wechat_queries": []},
    }, ensure_ascii=False))

    before = _stored_fetch_config(conn)
    run_migrate()
    after = _stored_fetch_config(conn)

    assert after == before
    stored = json.loads(after)
    assert stored["thresholds"]["wechat"]["min_followers"] == 5000
    assert stored["scope"]["xcircle_accounts"] == ["zzz"]


def test_seed_config_module_has_no_xbot_specific_branch():
    """G-7: 本 sprint 不改全局 seed 语义 (是否加保护由 dad 另拍)。

    P2-5: 原断言是"整份源码不含 threshold/scope 子串" —— 过宽 (将来
    任何相关注释或无关键名都会误伤)。改成**结构性**断言: 只看 seed 里
    真正写库的 fetch_config **载荷** —— 它必须仍然是 ``DEFAULT_FETCH_CONFIG``
    (不含 thresholds / scope 两个新键), 而不是 xbot 特判拼出来的。
    """
    import inspect

    from moni.db import seed_config
    from moni.fetch.sources.xbot_feed import DEFAULT_FETCH_CONFIG

    src = inspect.getsource(seed_config)
    # seed 写进库的 fetch_config 载荷 = DEFAULT_FETCH_CONFIG (原样)
    assert "XBOT_FEED_DEFAULT_FETCH_CONFIG" in src
    # 那份默认值里**不含**新键 → seed 没加 xbot 特判
    assert "thresholds" not in DEFAULT_FETCH_CONFIG
    assert "scope" not in DEFAULT_FETCH_CONFIG

    # 结构性检查: seed 里构造 fetch_config 载荷的那一段, 不得出现
    # 新键名 (只允许在**注释/文档字符串**里提到, 不允许在代码里使用)。
    tree = ast.parse(inspect.getsource(seed_config))
    code_strings = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    # 源码里出现的字符串常量 (含 docstring) 不含这两个键名
    for literal in code_strings:
        assert "thresholds" not in literal, (
            f"seed_config 里出现了 thresholds 字面量: {literal!r}"
        )
        assert '"scope"' not in literal and "'scope'" not in literal, (
            f"seed_config 里出现了 scope 字面量: {literal!r}"
        )
