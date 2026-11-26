"""sprint-35 — X-bot 精选 per-topic 门槛 (thresholds) 单元 + fetcher 接入测试。

覆盖 done-criteria AC-4 / AC-5 / AC-6 / AC-7 / AC-8 + 闸门 G-3。

口径（Mttt 2026-11-11 16:40 GO IMPL）：
- Q1 = A → 默认阈值全 0（不设限）
- Q2 = A → 缺 author_followers / metrics.like **放行 + 计数**（不丢）
"""
from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.fetch.sources.xbot_feed import (
    DEFAULT_THRESHOLDS,
    FetcherXbotFeed,
    evaluate_thresholds,
)

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "xbot_feed"
_SOURCE_NAME = "xbot_feed"
_SOURCE_ID = "src-xbot-feed"

# AC-4 基线：**改动前**（未实现门槛时）同一批 fixture feed 入库的 URL 集合。
#
# 由 /tmp/moni-sprint/capture_baseline_r1.py 在 **main 的 pre-change fetcher**
# （把 main:moni/fetch/sources/xbot_feed.py 临时换进来跑，跑完立即还原）上
# 采集并冻结 → 是真正的"改动前"基线，不是改动后的自我对照。
#
# 语料 = 3 份 fixture (2026-11-03 / 2026-11-02 / **2026-11-11 真实一天 118 条**)
#      + latest.json → 125 raw item，47 条入库。
# 门槛默认全关 → 改动后必须与它**逐条相同**（集合相等，不是数量相等）。
BASELINE_INGESTED_URLS = [
    "https://x.com/ChainEpic/status/2120140330536460398",
    "https://x.com/FinanceYF5/status/2120023543169908750",
    "https://x.com/FinanceYF5/status/2120037771540828663",
    "https://x.com/FinanceYF5/status/2120070920983433649",
    "https://x.com/FinanceYF5/status/2120074461546533026",
    "https://x.com/FinanceYF5/status/2120076098872778838",
    "https://x.com/FinanceYF5/status/2120077952801239509",
    "https://x.com/FinanceYF5/status/2120084535488708922",
    "https://x.com/Gorden_Sun/status/2120003861771686071",
    "https://x.com/Gorden_Sun/status/2120175259303502252",
    "https://x.com/HYhongyu8/status/2120059775677583396",
    "https://x.com/JackZhang2046/status/2120065235927375926",
    "https://x.com/JackZhang2046/status/2120130438455058869",
    "https://x.com/SoonCrush/status/2120107246428537292",
    "https://x.com/bailu_thaifans/status/2119996234417234069",
    "https://x.com/clanhj611/status/2120106567500526050",
    "https://x.com/dotey/status/2120039846118821976",
    "https://x.com/dotey/status/2120044871108956199",
    "https://x.com/dotey/status/2120064710200500574",
    "https://x.com/dotey/status/2120064894376558646",
    "https://x.com/dotey/status/2120176641603244488",
    "https://x.com/dotey/status/2120191790774960630",
    "https://x.com/dotey/status/2120208679005810803",
    "https://x.com/dotey/status/2120213189464105192",
    "https://x.com/dotey/status/2120226054195577277",
    "https://x.com/dotey/status/2120237447963415014",
    "https://x.com/manateelazycat/status/2120066927049703787",
    "https://x.com/manateelazycat/status/2120071865796542835",
    "https://x.com/manateelazycat/status/2120168594919346449",
    "https://x.com/manateelazycat/status/2120170509598167273",
    "https://x.com/manateelazycat/status/2120186076648370617",
    "https://x.com/manateelazycat/status/2120188271678591093",
    "https://x.com/manateelazycat/status/2120189413397504041",
    "https://x.com/maomao17888/status/2120140341873623418",
    "https://x.com/oldposter/status/2117000000000000001",
    "https://x.com/oran_ge/status/2117398931932082403",
    "https://x.com/oran_ge/status/2117414151186502091",
    "https://x.com/oran_ge/status/2120152466121724026",
    "https://x.com/shao__meng/status/2120069038873493989",
    "https://x.com/shao__meng/status/2120117639125348470",
    "https://x.com/shao__meng/status/2120121464880050507",
    "https://x.com/shihuangtimes/status/2117412872158662796",
    "https://x.com/vista8/status/2120066792995561957",
    "https://x.com/vista8/status/2120089651625271344",
    "https://x.com/vista8/status/2120099636149153857",
    "https://x.com/vista8/status/2120120757128929641",
    "https://x.com/xiaochaizong/status/2120074665682993449",
]


# ── fixtures ────────────────────────────────────────────────────────────

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


def _seed_source(conn, fetch_config=None):
    conn.execute("DELETE FROM source_tags WHERE source_id=?", (_SOURCE_ID,))
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


def _seed_tags(conn):
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


def _seeded_fetcher(data_dir, fetch_config=None):
    conn = get_db()
    _seed_tags(conn)
    cfg = {
        "bypass_keyword_filters": True,
        # t119u: 本文件测的是**门槛**语义，不测回复/转推过滤 —— 显式关掉
        # 那两道形态过滤，否则冻结的 BASELINE_INGESTED_URLS 会因为少收
        # 纯回复/纯转推而漂移，掩盖真正要验证的门槛行为。
        "exclude_replies": False,
        "exclude_retweets": False,
        "repo_url": "https://example.invalid/xbot-feed.git",
        "max_days": 30,
    }
    if fetch_config:
        cfg.update(fetch_config)
    _seed_source(conn, fetch_config=json.dumps(cfg, ensure_ascii=False))
    return FetcherXbotFeed()


def _load_fixture(name="2026-11-03.json"):
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _item(**over):
    base = {
        "id": "2117000000000000001",
        "url": "https://x.com/someone/status/2117000000000000001",
        "author_name": "Someone",
        "author_handle": "someone",
        "author_followers": 1000,
        "text": "微信小程序又出新玩法了",
        "created_at": "2026-11-03T00:00:00.000Z",
        "metrics": {"like": 3, "retweet": 0, "impression": 0},
        "topic": "wechat",
    }
    base.update(over)
    return base


# ── AC-5: 门槛判定与边界值（纯函数）─────────────────────────────────────

def test_default_thresholds_are_all_off():
    """Q1=A: 默认门槛全 0 → 不设限。"""
    assert DEFAULT_THRESHOLDS == {
        "wechat": {"min_followers": 0, "min_likes": 0},
        "xcircle": {"min_followers": 0, "min_likes": 0},
    }


def test_zero_threshold_always_passes():
    decision = evaluate_thresholds(_item(author_followers=1, metrics={"like": 0}),
                                   DEFAULT_THRESHOLDS, "wechat")
    assert decision.passed is True
    assert decision.reason is None
    assert decision.missing == ()


def test_below_min_followers_is_dropped():
    decision = evaluate_thresholds(
        _item(author_followers=4999),
        {"wechat": {"min_followers": 5000, "min_likes": 0}}, "wechat")
    assert decision.passed is False
    assert decision.reason == "below_min_followers"


def test_boundary_equal_followers_passes():
    """followers == min_followers → 放行。"""
    decision = evaluate_thresholds(
        _item(author_followers=5000),
        {"wechat": {"min_followers": 5000, "min_likes": 0}}, "wechat")
    assert decision.passed is True
    assert decision.reason is None


def test_below_min_likes_is_dropped():
    decision = evaluate_thresholds(
        _item(metrics={"like": 4}),
        {"wechat": {"min_followers": 0, "min_likes": 5}}, "wechat")
    assert decision.passed is False
    assert decision.reason == "below_min_likes"


def test_boundary_equal_likes_passes():
    decision = evaluate_thresholds(
        _item(metrics={"like": 5}),
        {"wechat": {"min_followers": 0, "min_likes": 5}}, "wechat")
    assert decision.passed is True


def test_zero_like_is_not_missing():
    """like == 0 是合法值（不是缺失）→ min_likes=5 时丢弃。"""
    decision = evaluate_thresholds(
        _item(metrics={"like": 0}),
        {"wechat": {"min_followers": 0, "min_likes": 5}}, "wechat")
    assert decision.passed is False
    assert decision.reason == "below_min_likes"
    assert decision.missing == ()


# ── AC-6: 缺字段处理（Q2=A → 放行 + 计数）──────────────────────────────

def test_missing_followers_fails_open():
    decision = evaluate_thresholds(
        _item(author_followers=None),
        {"wechat": {"min_followers": 5000, "min_likes": 0}}, "wechat")
    assert decision.passed is True
    assert decision.reason is None
    assert "followers" in decision.missing


def test_missing_followers_key_absent_fails_open():
    item = _item()
    item.pop("author_followers")
    decision = evaluate_thresholds(
        item, {"wechat": {"min_followers": 5000, "min_likes": 0}}, "wechat")
    assert decision.passed is True
    assert "followers" in decision.missing


def test_missing_likes_fails_open():
    decision = evaluate_thresholds(
        _item(metrics={}),
        {"wechat": {"min_followers": 0, "min_likes": 5}}, "wechat")
    assert decision.passed is True
    assert "likes" in decision.missing


def test_non_numeric_followers_treated_as_missing():
    """P2-3: 字段存在但类型不对 (如字符串) → 当作缺失 (放行 + 计数)。

    修复前: 非数值既不判缺失也不参与比较 → 静默放行且**不计数**,
    运维看计数是 0 的"门槛生效但没丢东西"假象。
    """
    decision = evaluate_thresholds(
        _item(author_followers="3280"),
        {"wechat": {"min_followers": 5000, "min_likes": 0}}, "wechat")
    assert decision.passed is True
    assert decision.reason is None
    assert "followers" in decision.missing


def test_non_numeric_likes_treated_as_missing():
    decision = evaluate_thresholds(
        _item(metrics={"like": "5"}),
        {"wechat": {"min_followers": 0, "min_likes": 5}}, "wechat")
    assert decision.passed is True
    assert decision.reason is None
    assert "likes" in decision.missing


def test_non_numeric_followers_is_counted_not_dropped(fresh_db, monkeypatch):
    """P2-3 端到端: 非数值粉丝数 → 放行 + 计入 threshold_missing_by。"""
    data_dir = fresh_db
    fetcher = _seeded_fetcher(data_dir, fetch_config={
        "thresholds": {"wechat": {"min_followers": 5000, "min_likes": 0}},
    })
    clone = _make_clone(data_dir)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    payload = _load_fixture("2026-11-03.json")
    payload["items"] = [
        it if it.get("id") != "2117412872158662796"
        else {**it, "author_followers": "3280"}
        for it in payload["items"]
    ]
    stamp = date.today().isoformat()
    override = clone / "feed" / f"{stamp}.json"
    override.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(FetcherXbotFeed, "_read_feed_file",
                        lambda self, path: payload["items"]
                        if path.name == f"{stamp}.json" else [])

    result = fetcher.run()

    assert "wechat:missing_followers" in result.get("threshold_missing_by", {})
    # 非数值 → 放行 → 仍然入库
    urls = [r["url"] for r in get_db().execute(
        "SELECT url FROM articles WHERE source = ?", (_SOURCE_NAME,)).fetchall()]
    assert "https://x.com/shihuangtimes/status/2117412872158662796" in urls


def test_missing_field_not_counted_when_dimension_off():
    """阈值 0 → 该维度不检查 → 连缺失都不计。"""
    decision = evaluate_thresholds(
        _item(author_followers=None, metrics={}),
        DEFAULT_THRESHOLDS, "wechat")
    assert decision.passed is True
    assert decision.missing == ()


# ── AC-7: topic 隔离 ────────────────────────────────────────────────────

def test_wechat_threshold_does_not_affect_xcircle():
    decision = evaluate_thresholds(
        _item(topic="xcircle", author_followers=1),
        {"wechat": {"min_followers": 5000, "min_likes": 5}}, "xcircle")
    assert decision.passed is True


def test_xcircle_threshold_does_not_affect_wechat():
    decision = evaluate_thresholds(
        _item(topic="wechat", author_followers=1),
        {"xcircle": {"min_followers": 5000, "min_likes": 5}}, "wechat")
    assert decision.passed is True


def test_unconfigured_topic_passes():
    decision = evaluate_thresholds(
        _item(topic="zhihu", author_followers=1),
        {"wechat": {"min_followers": 5000, "min_likes": 5}}, "zhihu")
    assert decision.passed is True
    assert decision.reason is None


def test_unknown_topic_passes_with_warning(caplog):
    """上游新增 topic → 放行 + warning（不因门槛丢条目）。"""
    import logging

    with caplog.at_level(logging.WARNING, logger="moni.fetch.sources.xbot_feed"):
        decision = evaluate_thresholds(
            _item(topic="zhihu", author_followers=1),
            {"wechat": {"min_followers": 5000, "min_likes": 5}}, "zhihu")
    assert decision.passed is True
    assert any("zhihu" in r.getMessage() for r in caplog.records)


# ── G-3: fetcher 必须能读到 DB 里的 thresholds（_load_config 白名单）────

def test_fetcher_reads_thresholds_from_db(fresh_db):
    """写进 DB 的 thresholds 必须被 fetcher 读到（旧白名单会静默吃掉）。"""
    conn = get_db()
    _seed_tags(conn)
    _seed_source(conn, fetch_config=json.dumps({
        "bypass_keyword_filters": True,
        "repo_url": "https://example.invalid/xbot-feed.git",
        "max_days": 30,
        "thresholds": {"wechat": {"min_followers": 5000, "min_likes": 5}},
    }, ensure_ascii=False))
    fetcher = FetcherXbotFeed()
    cfg = fetcher._load_config()
    assert cfg["thresholds"] == {"wechat": {"min_followers": 5000, "min_likes": 5}}


def test_fetcher_reads_scope_from_db(fresh_db):
    conn = get_db()
    _seed_tags(conn)
    _seed_source(conn, fetch_config=json.dumps({
        "bypass_keyword_filters": True,
        "repo_url": "https://example.invalid/xbot-feed.git",
        "max_days": 30,
        "scope": {"xcircle_accounts": ["dotey"], "wechat_regex": "微信",
                  "wechat_queries": []},
    }, ensure_ascii=False))
    fetcher = FetcherXbotFeed()
    cfg = fetcher._load_config()
    assert cfg["scope"]["xcircle_accounts"] == ["dotey"]


# ── AC-4: 门槛默认全关 → 入库 URL 集合与基线逐条相同 ────────────────────

def test_thresholds_off_ingests_identical_set(fresh_db, monkeypatch):
    data_dir = fresh_db
    fetcher = _seeded_fetcher(data_dir)
    clone = _make_clone(data_dir)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    result = fetcher.run()

    urls = sorted(r["url"] for r in get_db().execute(
        "SELECT url FROM articles WHERE source = ?", (_SOURCE_NAME,)).fetchall())
    assert urls == BASELINE_INGESTED_URLS
    assert result.get("threshold_dropped", 0) == 0
    assert result.get("threshold_dropped_by", {}) == {}


# ── 门槛生效：按 topic × 原因计数 ────────────────────────────────────────

def test_threshold_drops_item_and_counts_by_topic_and_reason(
        fresh_db, monkeypatch):
    data_dir = fresh_db
    fetcher = _seeded_fetcher(data_dir, fetch_config={
        "thresholds": {"wechat": {"min_followers": 5000, "min_likes": 0}},
    })
    clone = _make_clone(data_dir)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    result = fetcher.run()

    # fixture 里的 wechat item: shihuangtimes followers=3280 (<5000) → 丢
    assert result["threshold_dropped"] >= 1
    assert "wechat:below_min_followers" in result["threshold_dropped_by"]
    # 被丢的条目没入库
    urls = [r["url"] for r in get_db().execute(
        "SELECT url FROM articles WHERE source = ?", (_SOURCE_NAME,)).fetchall()]
    assert "https://x.com/shihuangtimes/status/2117412872158662796" not in urls


def test_threshold_missing_field_is_counted_not_dropped(fresh_db, monkeypatch):
    data_dir = fresh_db
    fetcher = _seeded_fetcher(data_dir, fetch_config={
        "thresholds": {"wechat": {"min_followers": 5000, "min_likes": 0}},
    })
    clone = _make_clone(data_dir)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    # 把一个 wechat item 的 author_followers 去掉 → 应放行 + 计 missing
    payload = _load_fixture()
    payload["items"] = [
        it if it.get("id") != "2117412872158662796"
        else {k: v for k, v in it.items() if k != "author_followers"}
        for it in payload["items"]
    ]
    # 文件名必须带 YYYY-MM-DD (max_days 过滤器按文件名日期挑文件)
    stamp = date.today().isoformat()
    override = clone / "feed" / f"{stamp}.json"
    override.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(FetcherXbotFeed, "_read_feed_file",
                        lambda self, path: payload["items"]
                        if path.name == f"{stamp}.json" else [])

    result = fetcher.run()

    assert "wechat:missing_followers" in result.get("threshold_missing_by", {})


# ── AC-8: 被丢弃的条目不进 state；门槛调松后能重新导入 ──────────────────

def test_threshold_dropped_not_in_state(fresh_db, monkeypatch):
    data_dir = fresh_db
    fetcher = _seeded_fetcher(data_dir, fetch_config={
        "thresholds": {"wechat": {"min_followers": 5000, "min_likes": 0}},
    })
    clone = _make_clone(data_dir)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    fetcher.run()

    state_path = data_dir / ".xbot_feed_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    # shihuangtimes 那条被门槛丢掉 → 不在 imported_ids
    assert "2117412872158662796" not in state["imported_ids"]
    # 通过门槛的条目在 state 里
    assert "2117414151186502091" in state["imported_ids"]


def test_loosened_threshold_reimports_item(fresh_db, monkeypatch):
    """门槛调松后，同一条目能被再次导入。"""
    data_dir = fresh_db
    conn = get_db()
    fetcher = _seeded_fetcher(data_dir, fetch_config={
        "thresholds": {"wechat": {"min_followers": 5000, "min_likes": 0}},
    })
    clone = _make_clone(data_dir)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    fetcher.run()
    urls = [r["url"] for r in conn.execute(
        "SELECT url FROM articles WHERE source = ?", (_SOURCE_NAME,)).fetchall()]
    assert "https://x.com/shihuangtimes/status/2117412872158662796" not in urls

    # 门槛调松 → 重跑
    conn.execute(
        "UPDATE sources SET fetch_config = ? WHERE id = ?",
        (json.dumps({
            "bypass_keyword_filters": True,
            "repo_url": "https://example.invalid/xbot-feed.git",
            "max_days": 30,
            "thresholds": {"wechat": {"min_followers": 0, "min_likes": 0}},
        }, ensure_ascii=False), _SOURCE_ID),
    )
    conn.commit()

    fetcher2 = FetcherXbotFeed()
    fetcher2.run()
    urls2 = [r["url"] for r in conn.execute(
        "SELECT url FROM articles WHERE source = ?", (_SOURCE_NAME,)).fetchall()]
    assert "https://x.com/shihuangtimes/status/2117412872158662796" in urls2
