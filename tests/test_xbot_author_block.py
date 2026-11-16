"""sprint-38 — X-bot 精选「屏蔽作者」入库期拦截与计数 测试。

覆盖 done-criteria AC-4 / AC-5 / AC-7 / AC-8 / AC-10 / AC-19。

口径（dad GO IMPL 2026-11-12 15:04 + warden DoD v0.3.0）：
- 存储 = 方案 B：新表 ``source_author_blocks``（来源列存 ``source_name``，AC-15）
- 入库期命中名单 → ``parse()`` 返回 None，不入库
- 屏蔽丢弃**独立计数** ``author_blocked``（标量 int），**不并入** threshold_dropped
- 内置 5 个黑名单号 + 存量 fetch_config 自加号行为不变（AC-10 回归）

测试写法约束（避免假绿）：
``parse()`` 有 6 条 early-return None 路径（缺 id/url / 黑名单 / 未知 topic /
门槛 / 跨源去重 / 关键词零命中）。所以「未被屏蔽 → parse() 不为 None」这条断言
必须让 item **真的走完全程**：用已知 topic（wechat/xcircle）、正文命中一个
active tag 的关键词、URL 唯一。helper ``_kept_item()`` 已保证这些前提，
且 ``test_fixture_item_reaches_the_end_of_parse`` 显式守住它。
"""
from __future__ import annotations

import json

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.fetch.sources.xbot_feed import (
    DEFAULT_BLACKLISTED_HANDLES,
    FetcherXbotFeed,
    normalize_handle,
    normalize_handles,
)

_SOURCE_NAME = "xbot_feed"
_SOURCE_ID = "src-xbot-feed"

#: 让 item 走完 parse() 的正文关键词 —— 必须命中一个 active tag。
_TEXT = "微信小程序又出新玩法了"
_TOPIC = "wechat"


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


def _seed_tags(conn):
    """种下 active tag + 源绑定，让关键词闸门能命中（否则 parse 提前返回 None）。"""
    for tag, kws in (("wechat", ["微信", "小程序"]),
                     ("miniprogram", ["小程序"]),
                     ("general-tech", ["AI", "模型"]),
                     ("ai-fintech", ["AI", "A股", "量化"])):
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


def _seed_source(conn, fetch_config=None):
    conn.execute(
        "DELETE FROM sources WHERE id=? OR name=?", (_SOURCE_ID, _SOURCE_NAME)
    )
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


def _block(conn, handle, source_name=_SOURCE_NAME, display_name=None):
    """往新表塞一条屏蔽记录（AC-15：来源列存 source_name，不是 sources.id）。"""
    conn.execute(
        "INSERT INTO source_author_blocks "
        "(source_name, handle, display_name, blocked_at, notes) "
        "VALUES (?,?,?,datetime('now'),?)",
        (source_name, handle, display_name, "test"),
    )
    conn.commit()


def _cfg(**over):
    cfg = {
        "bypass_keyword_filters": True,
        "repo_url": "https://example.invalid/xbot-feed.git",
        "max_days": 30,
    }
    cfg.update(over)
    return json.dumps(cfg, ensure_ascii=False)


def _fetcher(conn, **cfg_over):
    _seed_tags(conn)
    _seed_source(conn, fetch_config=_cfg(**cfg_over))
    f = FetcherXbotFeed()
    f._prepare(f._load_config())
    return f


def _kept_item(**over):
    """一个**能走完 parse() 全程**的 raw item。

    前提：已知 topic + 正文命中 active tag 关键词 + id/url 唯一。
    """
    base = {
        "id": "2117000000000000001",
        "url": "https://x.com/someone/status/2117000000000000001",
        "author_name": "Someone",
        "author_handle": "someone",
        "author_followers": 1000,
        "text": _TEXT,
        "created_at": "2026-11-03T00:00:00.000Z",
        "metrics": {"like": 3, "retweet": 0, "impression": 0},
        "topic": _TOPIC,
    }
    base.update(over)
    return base


# ── 防假绿：守住 fixture 本身 ───────────────────────────────────────────

def test_fixture_item_reaches_the_end_of_parse(fresh_db):
    """守卫：``_kept_item()`` 真的走完 parse()（否则下面所有「未被屏蔽」断言都假绿）。"""
    f = _fetcher(get_db())
    out = f.parse(_kept_item())
    assert out is not None, "fixture item 被别的 early-return 拦掉了 —— 断言会假绿"
    assert out["publisher"] == "Someone (@someone)"


# ── AC-4 / AC-5：入库期拦截 + handle 匹配规则 ────────────────────────────

def test_parse_returns_none_for_blocked_handle(fresh_db):
    """AC-4：名单内 handle → parse() 返回 None（不入库）。"""
    conn = get_db()
    _block(conn, "jiamemexyz")
    f = _fetcher(conn)
    assert f.parse(_kept_item(author_handle="jiamemexyz")) is None


def test_parse_returns_none_for_blocked_handle_case_insensitive(fresh_db):
    """AC-5：大小写不敏感。

    注意 case 变体必须**真的**是同一个 handle 的变体：
    ``jiamemexyz`` → ``JiaMeMexyz``（不是 ``JiaMeXyz``，那是另一个串）。
    """
    conn = get_db()
    _block(conn, "jiamemexyz")
    f = _fetcher(conn)
    assert f.parse(_kept_item(author_handle="JiaMeMexyz")) is None
    assert f.parse(_kept_item(author_handle="JIAMEMEXYZ")) is None
    assert f.parse(_kept_item(author_handle="JiaMeMexyz")) is None


def test_parse_returns_none_for_blocked_handle_with_at_prefix(fresh_db):
    """AC-5：名单存 @handle 或裸 handle 都要能拦住。"""
    conn = get_db()
    _block(conn, "@jiamemexyz")
    f = _fetcher(conn)
    assert f.parse(_kept_item(author_handle="jiamemexyz")) is None


def test_parse_does_not_match_substring_handle(fresh_db):
    """AC-5：精确等值 —— jiamemexyz 不该拦住 jiamemexyz2 / xjiamemexyz。"""
    conn = get_db()
    _block(conn, "jiamemexyz")
    f = _fetcher(conn)
    assert f.parse(_kept_item(author_handle="jiamemexyz2")) is not None
    assert f.parse(_kept_item(author_handle="xjiamemexyz")) is not None


def test_parse_does_not_match_on_display_name(fresh_db):
    """AC-5：display name 不参与匹配（只认 handle）。"""
    conn = get_db()
    _block(conn, "jiamemexyz")
    f = _fetcher(conn)
    assert f.parse(_kept_item(author_handle="someone", author_name="阿拾")) is not None


def test_parse_ignores_empty_handle(fresh_db):
    """AC-5：空 handle 不该误伤。"""
    conn = get_db()
    _block(conn, "jiamemexyz")
    f = _fetcher(conn)
    assert f.parse(_kept_item(author_handle="")) is not None


def test_parse_blocks_only_for_matching_source(fresh_db):
    """AC-7：名单按 source_name 隔离 —— 别的源同名不拦。"""
    conn = get_db()
    _block(conn, "someone", source_name="36kr_rss")
    f = _fetcher(conn)
    assert f.parse(_kept_item(author_handle="someone")) is not None


# ── AC-10：内置 5 个号 + 存量 fetch_config 自加号，行为不能被破坏 ────────

@pytest.mark.parametrize("handle", list(DEFAULT_BLACKLISTED_HANDLES))
def test_builtin_default_handles_still_blocked(fresh_db, handle):
    """AC-10：内置 5 个黑名单号仍被拦。"""
    f = _fetcher(get_db())
    assert f.parse(_kept_item(author_handle=handle)) is None


def test_user_added_handle_in_legacy_fetch_config_still_blocked(fresh_db):
    """AC-10：存量用户写在 fetch_config.blacklisted_handles 里的号仍被拦
    （方案 B 是**并集**，不是替换）。"""
    f = _fetcher(get_db(), blacklisted_handles=["legacy_guy"])
    assert f.parse(_kept_item(author_handle="legacy_guy")) is None


def test_builtin_handles_and_db_handles_are_unioned(fresh_db):
    """AC-10：新表与内置/存量名单是**并集** —— 两边都拦，普通作者放行。"""
    conn = get_db()
    _block(conn, "db_guy")
    f = _fetcher(conn, blacklisted_handles=["legacy_guy"])
    assert f.parse(_kept_item(author_handle="db_guy")) is None
    assert f.parse(_kept_item(author_handle="legacy_guy")) is None
    assert f.parse(_kept_item(author_handle="someone")) is not None


# ── AC-8 / AC-19：独立计数 ─────────────────────────────────────────────

def test_block_drop_count_is_scalar_int(fresh_db):
    """AC-19：计数是标量 int（不是 dict）。"""
    conn = get_db()
    _block(conn, "jiamemexyz")
    f = _fetcher(conn)
    f.parse(_kept_item(author_handle="jiamemexyz"))
    assert isinstance(f._author_blocked, int)
    assert not isinstance(f._author_blocked, dict)
    assert f._author_blocked == 1


def test_block_drop_not_counted_in_threshold_dropped(fresh_db):
    """AC-8：屏蔽丢弃不进 threshold_dropped / threshold_missing。"""
    conn = get_db()
    _block(conn, "jiamemexyz")
    f = _fetcher(conn)
    f.parse(_kept_item(author_handle="jiamemexyz", topic="wechat"))
    assert f._author_blocked == 1
    assert not f._threshold_drops
    assert not f._threshold_missing


def test_block_drop_counted_per_raw_entry_across_topics(fresh_db):
    """AC-19.3：同一 handle 出现在两个 topic → 逐条计数 = 2（不是 1，也不是 dict 分裂）。"""
    conn = get_db()
    _block(conn, "jiamemexyz")
    f = _fetcher(conn)
    f.parse(_kept_item(author_handle="jiamemexyz", topic="wechat",
                       id="1", url="https://x.com/a/status/1"))
    f.parse(_kept_item(author_handle="jiamemexyz", topic="xcircle",
                       id="2", url="https://x.com/a/status/2"))
    assert f._author_blocked == 2
    assert isinstance(f._author_blocked, int)


def test_author_blocked_count_reset_each_round(fresh_db):
    """AC-19.4：计数每轮抓取重置（生命周期同门槛计数）。"""
    conn = get_db()
    _block(conn, "jiamemexyz")
    f = _fetcher(conn)
    f.parse(_kept_item(author_handle="jiamemexyz"))
    assert f._author_blocked == 1
    f._prepare(f._load_config())          # 新一轮
    assert f._author_blocked == 0


def test_threshold_and_block_counts_are_independent(fresh_db):
    """AC-8：同一轮里两类丢弃互不干扰（4 类 item）。"""
    conn = get_db()
    _block(conn, "blocked_guy")
    f = _fetcher(conn, thresholds={"wechat": {"min_followers": 5000,
                                              "min_likes": 50}})
    # ① 名单内作者 → author_blocked
    f.parse(_kept_item(author_handle="blocked_guy", topic="wechat"))
    # ② 粉丝不足 → threshold_dropped
    f.parse(_kept_item(author_handle="low_fan", topic="wechat",
                       id="3", url="https://x.com/a/status/3",
                       author_followers=10, metrics={"like": 100}))
    # ③ 点赞不足 → threshold_dropped
    f.parse(_kept_item(author_handle="low_like", topic="wechat",
                       id="4", url="https://x.com/a/status/4",
                       author_followers=100000, metrics={"like": 1}))
    # ④ 缺字段 → threshold_missing（**只缺 followers**，likes 给足 —— 缺字段是
    #    **按字段**计数的，缺两个字段就是 2 条 missing 记录，见 sprint-35 语义）
    f.parse(_kept_item(author_handle="no_metrics", topic="wechat",
                       id="5", url="https://x.com/a/status/5",
                       author_followers=None, metrics={"like": 100}))
    assert f._author_blocked == 1
    assert sum(f._threshold_drops.values()) == 2
    assert sum(f._threshold_missing.values()) == 1
    assert f._threshold_missing.get("wechat:missing_followers") == 1


# ── AC-3：新表不受 seed 覆盖 ───────────────────────────────────────────

def test_blocklist_survives_seed_config(fresh_db):
    """AC-3：跑完 seed() 之后名单仍在（方案 B 相对方案 A 的核心优势）。"""
    from moni.db.seed_config import seed

    conn = get_db()
    _block(conn, "jiamemexyz")
    seed()
    rows = conn.execute(
        "SELECT handle FROM source_author_blocks WHERE source_name=?",
        (_SOURCE_NAME,),
    ).fetchall()
    assert [r["handle"] for r in rows] == ["jiamemexyz"]


# ── warden 审计 P2-3：handle 归一必须全仓只有一份实现 ──────────────────

def test_handle_normalization_has_single_source_of_truth():
    """写入侧 (author_blocks.py) 与匹配侧 (xbot_feed.py) 必须走同一套归一。

    两处各写一份时, 将来改一处漏一处 → dad 输 ``@JiaMeXyz`` 写进名单,
    抓取时却归一成别的形式 → 「名单里明明有, 作者却还在入库」的**静默失效**。
    """
    from moni.web.routes.author_blocks import _normalize_handle as write_side

    # 写入侧必须**委托**给 xbot_feed 的唯一实现 (不是各写一份)。
    assert write_side is not None
    for raw in ("@JiaMeXyz", "jiamemexyz", "  @JiaMeXyz  ", "JIAMEXYZ", None, "", "@"):
        assert write_side(raw) == normalize_handle(raw), (
            f"写入侧与匹配侧对 {raw!r} 的归一结果不一致 —— 静默失效风险"
        )
        # 匹配侧对同一个输入必须给出同一个 (或空) 规范形式。
        matched = FetcherXbotFeed._normalize_handles([raw])
        expected = normalize_handle(raw)
        assert matched == ({expected} if expected else set())

    # 匹配侧 (静态方法) 必须与模块级唯一实现一致。
    assert FetcherXbotFeed._normalize_handles(["@A", "b", " @C "]) == normalize_handles(["@A", "b", " @C "])
    assert FetcherXbotFeed._normalize_handles(["@A", "b", " @C "]) == {"a", "b", "c"}


def test_normalize_handles_accepts_common_shapes():
    """归一函数对 None / 单 str / 各种容器都稳健, 空串被丢掉。"""
    assert normalize_handles(None) == set()
    assert normalize_handles("@Solo") == {"solo"}
    assert normalize_handles(["@A", "", "  ", "b"]) == {"a", "b"}
    assert normalize_handles(("x", "Y")) == {"x", "y"}
    assert normalize_handles({"z"}) == {"z"}
    assert normalize_handles(123) == set()
    assert normalize_handle(None) == ""
