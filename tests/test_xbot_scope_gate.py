"""sprint-55 / v1.0.131 — X-bot scope **准入闸门** (wechat_regex / xcircle_accounts)。

这些用例钉死已批准的语义（dad 2026-11-25 12:33 GO）:

* ``wechat_regex`` 只对 ``topic == "wechat"``，匹配 **title + content**，大小写不敏感；
* ``xcircle_accounts`` 只对 ``topic == "xcircle"``，按 ``normalize_handle()`` 精确命中；
* scope 命中与领域关键词表是 **OR**（命中即准入），不是 AND；
* 作者屏蔽 / 粉丝门槛 / 跨源 URL 去重 **优先级更高**，scope 命中不能绕过；
* 只读 **moni 显式存储**的 ``sources.fetch_config.scope``，读不到 = 不参与准入
  （不是「全放通」，也不是读 ``_effective_scope`` 的兜底/上游值）；
* scope-only 命中合成到专用 tag（``xbot-scope``），**不冒充**领域关键词命中；
* 漏斗口径: ``scope_admitted`` 只计 **scope-only 救回**；双命中不重复计入。

测试层次说明: 这里全是 ``parse()`` 级（纯闸门顺序 + 计数），``fetch()`` 的
``imported_ids`` 跳过逻辑在 ``test_xbot_scope_admission_ingest.py`` 里测。
"""
from __future__ import annotations

import json

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.fetch.sources.xbot_feed import (
    DEFAULT_FETCH_CONFIG,
    SCOPE_ADMISSION_TAG,
    FetcherXbotFeed,
)

#: 候选 tag 的关键词表 —— **故意收窄**，让"普通文本"不命中领域词表，
#: 这样 scope 命中/未命中的差异才能被单独观察。
_TAG_KEYWORDS = {
    "wechat": ["大模型"],
    "miniprogram": ["小程序"],
    "general-tech": ["代码审查"],
    "ai-fintech": ["量化"],
}

_WECHAT_TEXT_NO_TAG = "这个岗位要求会 skill 栈和 agent 编排，别的不要求"
_WECHAT_TEXT_TAG_HIT = "大模型推理成本又降了"
_XCIRCLE_TEXT_NO_TAG = "今天聊点别的，跟行业无关"

_REGEX = "skill|agent"


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    close_db()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MONI_DATA_DIR", str(data_dir))
    set_db_path(data_dir / "news.db")
    migrate()
    _seed_tags()
    _seed_scope_tag()
    yield data_dir
    close_db()


def _seed_tags() -> None:
    conn = get_db()
    for name, kws in _TAG_KEYWORDS.items():
        conn.execute(
            "INSERT OR REPLACE INTO tag_definitions "
            "(name, description, color, created_at, is_active, keywords) "
            "VALUES (?,?,?,?,1,?)",
            (name, name, "#000000", "2026-01-01",
             json.dumps(kws, ensure_ascii=False)),
        )
    conn.commit()


def _seed_scope_tag() -> None:
    """``xbot-scope`` 由 migration 061 建好；夹具里显式兜底一次。"""
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO tag_definitions "
        "(name, description, color, created_at, is_active, keywords) "
        "VALUES (?,?,?,?,1,?)",
        (SCOPE_ADMISSION_TAG, "X-bot 范围准入", "#0EA5E9", "2026-01-01", "[]"),
    )
    conn.commit()


_ABSENT = object()


def _fetcher(
    scope=_ABSENT,
    *,
    thresholds=None,
    blacklisted=None,
    topic_tags=None,
) -> FetcherXbotFeed:
    cfg = dict(DEFAULT_FETCH_CONFIG)
    if scope is not _ABSENT:
        cfg["scope"] = scope
    if thresholds is not None:
        cfg["thresholds"] = thresholds
    if blacklisted is not None:
        cfg["blacklisted_handles"] = blacklisted
    if topic_tags is not None:
        cfg["topic_tags"] = topic_tags
    f = FetcherXbotFeed()
    f._prepare(cfg)
    return f


def _item(
    text: str,
    *,
    topic: str = "wechat",
    handle: str = "someone",
    tid: str = "1111111111111111111",
    followers: int = 50000,
    likes: int = 3,
) -> dict:
    return {
        "id": tid,
        "url": f"https://x.com/{handle}/status/{tid}",
        "author_handle": handle,
        "author_name": handle,
        "author_followers": followers,
        "text": text,
        "metrics": {"like": likes},
        "topic": topic,
        "created_at": "2026-11-20T01:00:00.000Z",
    }


_SCOPE_BOTH = {
    "xcircle_accounts": ["UberEng", "dotey"],
    "wechat_regex": _REGEX,
    "wechat_queries": [],
}


# ── 守卫: fixture 真的走完 parse() (否则下面所有"未被拦"断言都是假绿) ──

def test_guard_scope_only_fixture_reaches_end_of_parse(fresh_db):
    f = _fetcher(_SCOPE_BOTH)
    out = f.parse(_item(_WECHAT_TEXT_NO_TAG))
    assert out is not None, (
        "守卫失败: scope-only fixture 被别的 early-return 拦掉了 —— "
        "下面所有「scope 命中即准入」的断言都会假绿"
    )


# ── wechat 正则 ────────────────────────────────────────────────────────

def test_wechat_regex_hit_admits_without_tag_keyword(fresh_db):
    f = _fetcher(_SCOPE_BOTH)
    out = f.parse(_item(_WECHAT_TEXT_NO_TAG))
    assert out is not None, "正则命中应准入（OR 语义），不得被领域词表零命中吞掉"
    assert out["xbot_tags"] == [SCOPE_ADMISSION_TAG]
    assert out["xbot_hits"][0]["hit_field"] == "scope"
    assert out["xbot_hits"][0]["keyword"].startswith("regex:")
    assert out["xbot_hits"][0]["tag_name"] == SCOPE_ADMISSION_TAG
    assert f._scope_admitted == 1
    assert f._scope_admitted_by == {"wechat_regex": 1}
    assert f._scope_matched_by == {"wechat_regex": 1}


def test_wechat_regex_is_case_insensitive(fresh_db):
    """UI 已承诺「大小写不敏感的正则」(index.html placeholder)。"""
    for variant in ("SKILL", "Skill", "aGEnT", "AGENT"):
        f = _fetcher(_SCOPE_BOTH)
        out = f.parse(_item(f"岗位上写着 {variant} 要求"))
        assert out is not None, f"大小写变体 {variant!r} 应命中"


def test_wechat_regex_miss_still_dropped(fresh_db):
    f = _fetcher(_SCOPE_BOTH)
    out = f.parse(_item("今天天气不错，出去走走"))
    assert out is None
    assert f._scope_admitted == 0
    assert f._scope_matched_by == {}


def test_wechat_regex_matches_title_or_content(fresh_db):
    """标题（正文首行）命中即可，正文尾部命中亦可。"""
    f = _fetcher(_SCOPE_BOTH)
    assert f.parse(_item("agent 生态观察\n\n第二行随便写")) is not None
    f2 = _fetcher(_SCOPE_BOTH)
    assert f2.parse(_item("第一行无关\n\n第二行提到 agent")) is not None


# ── xcircle 固定账号 ──────────────────────────────────────────────────

def test_xcircle_account_hit_admits_without_tag_keyword(fresh_db):
    f = _fetcher(_SCOPE_BOTH)
    out = f.parse(_item(_XCIRCLE_TEXT_NO_TAG, topic="xcircle", handle="UberEng"))
    assert out is not None, "名单内作者应准入"
    assert out["xbot_tags"] == [SCOPE_ADMISSION_TAG]
    assert out["xbot_hits"][0]["hit_field"] == "scope"
    assert out["xbot_hits"][0]["keyword"].startswith("account:")
    assert f._scope_admitted == 1
    assert f._scope_admitted_by == {"xcircle_accounts": 1}


@pytest.mark.parametrize("variant", ["ubereng", "@UberEng", "  UberEng  ", "UBERENG"])
def test_xcircle_account_match_is_normalized(fresh_db, variant):
    """匹配走 ``normalize_handle()``（strip → 去 @ → lower），与写入侧同一套。"""
    f = _fetcher(_SCOPE_BOTH)
    out = f.parse(_item(_XCIRCLE_TEXT_NO_TAG, topic="xcircle", handle=variant))
    assert out is not None, f"{variant!r} 归一后应命中 UberEng"


def test_xcircle_account_miss_still_dropped(fresh_db):
    f = _fetcher(_SCOPE_BOTH)
    out = f.parse(_item(_XCIRCLE_TEXT_NO_TAG, topic="xcircle", handle="random_guy"))
    assert out is None
    assert f._scope_admitted == 0


def test_xcircle_new_accounts_from_approved_list_admit(fresh_db):
    """已批准的 4 个新账号：上游当前 0 条 → 只能用构造 fixture 证明「有数据即准入」。

    不声称生产已抓到（见 implement-notes 的上游边界）。
    """
    scope = {
        "xcircle_accounts": ["UberEng", "ShopifyEng", "AirbnbEng", "Meta_Engineers"],
        "wechat_regex": "",
        "wechat_queries": [],
    }
    for handle in ("UberEng", "ShopifyEng", "AirbnbEng", "Meta_Engineers"):
        f = _fetcher(scope)
        assert f.parse(_item("构造样本，不含任何领域关键词", topic="xcircle",
                             handle=handle)) is not None, handle


# ── topic 隔离 ─────────────────────────────────────────────────────────

def test_wechat_regex_does_not_admit_xcircle_topic(fresh_db):
    """wechat 正则对 xcircle 条目完全不参与判定（否则会误伤 286 条/池）。"""
    f = _fetcher(_SCOPE_BOTH)
    out = f.parse(_item("微信生态观察", topic="xcircle", handle="random_guy"))
    assert out is None, "wechat 正则不得作用于 xcircle topic"
    assert f._scope_matched_by == {}


def test_xcircle_accounts_do_not_admit_wechat_topic(fresh_db):
    f = _fetcher(_SCOPE_BOTH)
    out = f.parse(_item("今天天气不错", topic="wechat", handle="UberEng"))
    assert out is None, "账号名单不得作用于 wechat topic"
    assert f._scope_matched_by == {}


# ── 未配置 / 空值 / 非法值 ─────────────────────────────────────────────

def test_scope_absent_means_no_admission(fresh_db):
    """库里有没显式存储 scope → 闸门不参与（默认行为零变化）。"""
    f = _fetcher()
    assert f._scope_gate_active is False
    assert f.parse(_item(_WECHAT_TEXT_NO_TAG)) is None
    f2 = _fetcher()
    assert f2.parse(_item(_XCIRCLE_TEXT_NO_TAG, topic="xcircle", handle="UberEng")) is None


def test_empty_scope_fields_do_not_admit_everything(fresh_db):
    """空正则 / 空名单 = 该维度不参与，**不是**全放通。"""
    f = _fetcher({"xcircle_accounts": [], "wechat_regex": "", "wechat_queries": []})
    assert f._scope_gate_active is False
    assert f.parse(_item(_WECHAT_TEXT_NO_TAG)) is None
    f2 = _fetcher({"xcircle_accounts": [], "wechat_regex": "   ", "wechat_queries": []})
    assert f2._scope_gate_active is False
    assert f2.parse(_item(_XCIRCLE_TEXT_NO_TAG, topic="xcircle", handle="UberEng")) is None


def test_wechat_queries_never_gate(fresh_db):
    """第三个字段 ``wechat_queries`` 仍仅展示，不参与准入。"""
    f = _fetcher({"xcircle_accounts": [], "wechat_regex": "__never__",
                  "wechat_queries": ["skill", "agent"]})
    assert f._scope_gate_active is True  # 正则可编译 → 生效，但下面的文本不命中
    assert f.parse(_item(_WECHAT_TEXT_NO_TAG)) is None, (
        "wechat_queries 不得参与准入 —— 命中的应是正则/账号，不是查询词"
    )


def test_invalid_regex_is_inactive_and_does_not_crash(fresh_db):
    f = _fetcher({"xcircle_accounts": [], "wechat_regex": "(", "wechat_queries": []})
    assert f._scope_wechat_regex is None, "非法正则必须降级为「该维度不参与」"
    assert f.parse(_item(_WECHAT_TEXT_NO_TAG)) is None


def test_non_dict_scope_is_ignored(fresh_db):
    for bad in ("微信", ["微信"], 3, None):
        f = _fetcher(bad)
        assert f._scope_gate_active is False


# ── 前置闸门仍优先 ─────────────────────────────────────────────────────

def test_author_block_wins_over_scope(fresh_db):
    f = _fetcher(_SCOPE_BOTH, blacklisted=["blockedguy"])
    out = f.parse(_item(_XCIRCLE_TEXT_NO_TAG, topic="xcircle", handle="blockedguy"))
    assert out is None, "作者屏蔽是强优先，scope 命中不能绕过"
    assert f._author_blocked == 1
    assert f._scope_admitted == 0
    assert f._scope_matched_by == {}, "被屏蔽的条目不应进入 scope 判定"


def test_threshold_wins_over_scope(fresh_db):
    f = _fetcher(
        _SCOPE_BOTH,
        thresholds={"wechat": {"min_followers": 1000, "min_likes": 0},
                    "xcircle": {"min_followers": 0, "min_likes": 0}},
    )
    out = f.parse(_item(_WECHAT_TEXT_NO_TAG, followers=10))
    assert out is None, "门槛默认不绕过，scope 命中也要先过门槛"
    assert f._threshold_drops == {"wechat:below_min_followers": 1}
    assert f._scope_admitted == 0


def test_url_dedupe_wins_over_scope(fresh_db):
    conn = get_db()
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at) "
        "VALUES (?,?,?,?,?)",
        ("existing-1", "已在库", "https://x.com/UberEng/status/2222222222222222222",
         "bestblog-x", "2026-11-20T00:00:00+00:00"),
    )
    conn.commit()
    f = _fetcher(_SCOPE_BOTH)
    out = f.parse(_item(_XCIRCLE_TEXT_NO_TAG, topic="xcircle", handle="UberEng",
                        tid="2222222222222222222"))
    assert out is None, "跨源 URL 去重优先于 scope 准入"
    assert f._scope_admitted == 0


# ── 与领域词表的关系（OR，且不重复计数） ───────────────────────────────

def test_tag_hit_still_admits_without_scope(fresh_db):
    """老路径不受影响：领域词表命中就准入，且不记 scope 计数。"""
    f = _fetcher(_SCOPE_BOTH)
    out = f.parse(_item(_WECHAT_TEXT_TAG_HIT))
    assert out is not None
    assert out["xbot_tags"] == ["wechat"]
    assert f._scope_admitted == 0
    assert f._scope_matched_by == {}, "文本不含 skill/agent → scope 不应记命中"


def test_double_hit_counts_once_and_keeps_real_tag(fresh_db):
    """同时命中词表与 scope → parsed 只加 1；tag 仍是真实业务 tag。"""
    f = _fetcher(_SCOPE_BOTH)
    out = f.parse(_item("大模型 与 agent 编排的事情"))
    assert out is not None
    assert out["xbot_tags"] == ["wechat"], "双命中时不该挂 scope 专用 tag"
    assert f._scope_admitted == 0, "scope_admitted 只计 scope-only 救回"
    assert f._scope_matched_by.get("wechat_regex") == 1, (
        "scope 匹配总数仍要记录（用于漏斗归因），但不计入 scope_admitted"
    )


def test_scope_hits_never_claim_keyword_field(fresh_db):
    f = _fetcher(_SCOPE_BOTH)
    out = f.parse(_item(_WECHAT_TEXT_NO_TAG))
    assert out is not None
    for hit in out["xbot_hits"]:
        assert hit["hit_field"] == "scope"
        assert hit["keyword"] != "skill" and hit["keyword"] != "agent", (
            "scope 命中不得冒充领域关键词命中（keyword 要带 regex:/account: 前缀）"
        )


def test_candidates_unchanged_when_scope_admits(fresh_db):
    """scope 只影响准入，不改变候选 tag 名单。"""
    f = _fetcher(_SCOPE_BOTH)
    out = f.parse(_item(_WECHAT_TEXT_NO_TAG))
    assert out is not None
    assert out["xbot_candidate_tags"] == ["wechat", "miniprogram"]
