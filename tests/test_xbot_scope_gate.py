"""t119u（2026-11-26 dad 拍板）— X-bot「必须命中业务 tag 才入库」parse() 级契约。

**旧语义作废**：``scope``（wechat_regex / xcircle_accounts）不再是「命中即准入、
与领域词表 OR」的放行通道，也不再合成 ``xbot-scope`` 当业务命中。

新语义（本文件钉死）：

* **唯一放行通道** = 命中「已启用的业务 tag」之一：信源勾选的 ``source_tags``
  与 ``fetch_config.topic_tags`` 映射两个真相源分别可启停，默认都开；
* 对集合本身 **fail-closed**：两边都关 / 已启用 tag 关键词全空 ⇒ 0 准入
  （绝不透传成「全放通」）；
* 工程 tag ``xbot-scope`` **永不算业务命中**；
* ``scope`` 默认**只展示**；仅当 ``scope_fence_enabled=true`` 时作为
  「命中业务 tag **且** 命中范围」的 AND 围栏；
* 三道强闸门（作者屏蔽 / 粉丝点赞门槛 / 跨源 URL 去重）优先且不可绕过；
* 纯回复 / 纯转推默认过滤，可配置关；
* 漏斗计数：``business_tag_dropped`` / ``scope_fence_dropped`` / ``url_deduped``
  / ``reply_dropped`` / ``retweet_dropped``。

测试层次：这里全是 ``parse()`` 级（闸门顺序 + 计数）；``fetch()`` 的
``imported_ids`` 跳过逻辑在 ``test_xbot_scope_admission_ingest.py``，
run() 级端到端在 ``test_xbot_business_tag_gate.py``。
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

SOURCE_ID = "src-xbot-feed"

#: 业务 tag 的关键词表 —— 故意收窄，方便单独观察「命中 / 未命中」。
_TAG_KEYWORDS = {
    "wechat": ["大模型"],
    "miniprogram": ["小程序"],
    "general-tech": ["代码审查"],
    "ai-fintech": ["量化"],
    "x-ai-circle": ["Claude Code", "MCP"],
}

#: fixture 里显式绑到 xbot_feed 的业务 tag（含 070 的建议绑定 x-ai-circle）
_BOUND = ["wechat", "miniprogram", "general-tech", "ai-fintech", "x-ai-circle"]

_REGEX = "skill|agent"

_WECHAT_HIT = "今天用 Claude Code 重构了一遍流水线，顺手接了个 MCP server"
_WECHAT_HIT_WITH_SCOPE = "用 Claude Code 写了个 agent，成本降了一半"
_WECHAT_NO_HIT = "这个岗位要求会 skill 栈和 agent 编排，别的不要求"
_XCIRCLE_NO_HIT = "今天聊点别的，跟行业无关"


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    close_db()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MONI_DATA_DIR", str(data_dir))
    set_db_path(data_dir / "news.db")
    migrate()
    _seed_tags()
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
    # 工程 tag：**故意保持 active 且带词**，用来证明「排除是代码级的」，
    # 而不是靠 migration 071 把它停用（AC-5）。
    conn.execute(
        "INSERT OR REPLACE INTO tag_definitions "
        "(name, description, color, created_at, is_active, keywords) "
        "VALUES (?,?,?,?,1,?)",
        (SCOPE_ADMISSION_TAG, "X-bot 范围准入", "#0EA5E9", "2026-01-01",
         json.dumps(["Claude Code"], ensure_ascii=False)),
    )
    conn.execute("DELETE FROM source_tags WHERE source_id = ?", (SOURCE_ID,))
    for tag in _BOUND:
        conn.execute(
            "INSERT OR IGNORE INTO source_tags (source_id, tag_name, created_at) "
            "VALUES (?,?,?)",
            (SOURCE_ID, tag, "2026-01-01"),
        )
    conn.commit()


_ABSENT = object()


def _fetcher(
    scope=_ABSENT,
    *,
    thresholds=None,
    blacklisted=None,
    topic_tags=None,
    switches: dict | None = None,
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
    for key, value in (switches or {}).items():
        cfg[key] = value
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
    likes: int = 30,
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


# ══ 1. 唯一放行通道：业务 tag ═════════════════════════════════════════

def test_business_tag_hit_admits_and_tags_only_real_tags(fresh_db):
    """AC-1：命中 x-ai-circle（Claude Code / MCP）⇒ 入库，只挂真实业务 tag。"""
    f = _fetcher()
    art = f.parse(_item(_WECHAT_HIT))
    assert art is not None
    assert art["xbot_tags"] == ["x-ai-circle"]
    assert art["xbot_hits"][0]["tag_name"] == "x-ai-circle"
    assert art["xbot_hits"][0]["hit_field"] == "title+content"
    assert SCOPE_ADMISSION_TAG not in art["xbot_tags"]


def test_scope_hit_alone_never_admits(fresh_db):
    """AC-2：scope 明确命中但业务 tag 零命中 ⇒ 必须丢弃（不再救回）。"""
    f = _fetcher(_SCOPE_BOTH)
    assert f.parse(_item(_WECHAT_NO_HIT)) is None
    assert f._business_tag_dropped == 1
    assert f._scope_admitted == 0, "scope-only 救回已退役"
    assert f.parse(
        _item(_XCIRCLE_NO_HIT, topic="xcircle", handle="UberEng")
    ) is None
    assert f._business_tag_dropped == 2


def test_scope_is_display_only_by_default(fresh_db):
    """scope 存量已配也不参与准入（默认只展示）。"""
    f = _fetcher(_SCOPE_BOTH)
    assert f._scope_fence_enabled is False
    art = f.parse(_item(_WECHAT_HIT))
    assert art is not None, "不中 scope 也应入库 —— 默认不吃围栏"
    assert f._scope_fence_dropped == 0


# ══ 2. 两个真相源分别可启停 ════════════════════════════════════════════

def test_source_tags_switch_off_uses_topic_tags_only(fresh_db):
    f = _fetcher(
        switches={"business_tags_from_source_tags": False},
        topic_tags={"wechat": ["wechat"]},
    )
    assert f.parse(_item("这个 小程序 挺好用")) is None, "小程序不在候选里"
    assert f.parse(_item("大模型又降价了", tid="4444444444444444444")) is not None


def test_topic_tags_switch_off_uses_source_tags_only(fresh_db):
    f = _fetcher(switches={"business_tags_from_topic_tags": False})
    assert f.parse(_item("大模型又降价了")) is not None


def test_both_truth_sources_off_is_fail_closed(fresh_db):
    """AC-4：两边都关 ⇒ 0 准入（绝不透传）。"""
    f = _fetcher(switches={
        "business_tags_from_source_tags": False,
        "business_tags_from_topic_tags": False,
    })
    assert f.parse(_item(_WECHAT_HIT)) is None
    assert f._business_tag_dropped == 1
    assert f._business_gate_empty is True


def test_enabled_tags_without_keywords_is_fail_closed(fresh_db):
    """AC-4：已启用 tag 一个关键词都没有 ⇒ 同样 0 准入。"""
    conn = get_db()
    conn.execute(
        "UPDATE tag_definitions SET keywords = '[]' "
        "WHERE name IN ('wechat','miniprogram','general-tech','ai-fintech','x-ai-circle')"
    )
    conn.commit()
    f = _fetcher(switches={"business_tags_from_topic_tags": False})
    assert f.parse(_item(_WECHAT_HIT)) is None
    assert f._business_gate_empty is True
    assert f._business_tag_dropped == 1


# ══ 3. 工程 tag 隔离（AC-5）════════════════════════════════════════════

def test_engineering_tag_never_counts_as_business_tag(fresh_db):
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO source_tags (source_id, tag_name, created_at) "
        "VALUES (?,?,?)",
        (SOURCE_ID, SCOPE_ADMISSION_TAG, "2026-01-01"),
    )
    conn.execute(
        "UPDATE tag_definitions SET keywords = '[]' "
        "WHERE name IN ('wechat','miniprogram','general-tech','ai-fintech','x-ai-circle')"
    )
    conn.commit()
    f = _fetcher(switches={"business_tags_from_topic_tags": False})
    assert SCOPE_ADMISSION_TAG not in f._business_candidate_tags("wechat")
    assert f.parse(_item(_WECHAT_HIT)) is None, (
        "xbot-scope 的词表（Claude Code）不得充业务命中"
    )
    assert f._business_gate_empty is True


# ══ 4. scope 的机制（只在 AND 围栏模式下生效）══════════════════════════

def test_scope_fence_requires_business_hit_and_scope_hit(fresh_db):
    f = _fetcher(_SCOPE_BOTH, switches={"scope_fence_enabled": True})
    assert f.parse(_item(_WECHAT_HIT_WITH_SCOPE)) is not None   # 双命中
    assert f.parse(_item(_WECHAT_HIT, tid="2222222222222222222")) is None
    assert f._scope_fence_dropped == 1
    assert f._business_tag_dropped == 0


def test_scope_regex_is_case_insensitive_under_fence(fresh_db):
    for variant in ("SKILL", "Skill", "aGEnT", "AGENT"):
        f = _fetcher(_SCOPE_BOTH, switches={"scope_fence_enabled": True})
        assert f.parse(_item(f"用 Claude Code 写 {variant} 编排")) is not None


def test_scope_regex_matches_title_or_content_under_fence(fresh_db):
    f = _fetcher(_SCOPE_BOTH, switches={"scope_fence_enabled": True})
    assert f.parse(_item("agent 生态 + Claude Code\n\n第二行")) is not None
    f2 = _fetcher(_SCOPE_BOTH, switches={"scope_fence_enabled": True})
    assert f2.parse(_item("Claude Code 实践\n\n第二行提到 agent")) is not None


@pytest.mark.parametrize("variant", ["ubereng", "@UberEng", "  UberEng  ", "UBERENG"])
def test_xcircle_account_match_is_normalized(fresh_db, variant):
    """账号匹配走 ``normalize_handle()``（strip → 去 @ → lower）。"""
    f = _fetcher(_SCOPE_BOTH, switches={"scope_fence_enabled": True})
    art = f.parse(_item("Claude Code 实践", topic="xcircle", handle=variant))
    assert art is not None, f"{variant!r} 归一后应命中 UberEng"


def test_wechat_regex_does_not_apply_to_xcircle_topic(fresh_db):
    f = _fetcher(_SCOPE_BOTH, switches={"scope_fence_enabled": True})
    assert f.parse(_item(
        "Claude Code 实践", topic="xcircle", handle="random_guy"
    )) is None
    assert f._scope_fence_dropped == 1


def test_xcircle_accounts_do_not_apply_to_wechat_topic(fresh_db):
    scope = {"xcircle_accounts": ["UberEng"], "wechat_regex": "",
             "wechat_queries": []}
    f = _fetcher(scope, switches={"scope_fence_enabled": True})
    assert f.parse(_item("Claude Code 实践", topic="wechat",
                         handle="UberEng")) is None
    assert f._scope_fence_dropped == 1


def test_wechat_queries_never_gate(fresh_db):
    """第三个字段 ``wechat_queries`` 仍仅展示。"""
    scope = {"xcircle_accounts": [], "wechat_regex": "__never__",
             "wechat_queries": ["Claude Code"]}
    f = _fetcher(scope, switches={"scope_fence_enabled": True})
    assert f.parse(_item(_WECHAT_HIT)) is None
    assert f._scope_fence_dropped == 1


def test_invalid_regex_is_inactive_and_does_not_crash(fresh_db):
    scope = {"xcircle_accounts": [], "wechat_regex": "(", "wechat_queries": []}
    f = _fetcher(scope, switches={"scope_fence_enabled": True})
    assert f._scope_wechat_regex is None
    assert f.parse(_item(_WECHAT_HIT)) is None, "围栏开着但维度非法 ⇒ 拦下"


def test_non_dict_scope_is_ignored(fresh_db):
    for bad in ("微信", ["微信"], 3, None):
        f = _fetcher(bad, switches={"scope_fence_enabled": True})
        assert f._scope_gate_active is False


# ══ 5. 三道强闸门不可绕过（AC-8）══════════════════════════════════════

def test_author_block_wins_over_business_tag(fresh_db):
    f = _fetcher(blacklisted=["blockedguy"])
    assert f.parse(_item("Claude Code 很好用", handle="blockedguy")) is None
    assert f._author_blocked == 1


def test_threshold_wins_over_business_tag(fresh_db):
    f = _fetcher(thresholds={"wechat": {"min_followers": 1000}})
    assert f.parse(_item("Claude Code 很好用", followers=10)) is None
    assert f._threshold_drops == {"wechat:below_min_followers": 1}


def test_url_dedupe_wins_over_business_tag_and_is_counted(fresh_db):
    conn = get_db()
    tid = "9999999999999999999"
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at) "
        "VALUES ('dup-row', 't', ?, 'bestblog-x', '2026-11-20T00:00:00+00:00')",
        (f"https://x.com/dotey/status/{tid}",),
    )
    conn.commit()
    f = _fetcher()
    assert f.parse(_item("Claude Code 很好用", tid=tid, handle="dotey")) is None
    assert f._url_deduped == 1, "跨源去重必须可计数（t118u 黑盒问题）"


# ══ 6. 纯回复 / 纯转推（AC-D4）════════════════════════════════════════

def test_reply_and_retweet_filtered_by_default(fresh_db):
    f = _fetcher()
    assert f.parse(_item("@dotey 你说得对，Claude Code 真香")) is None
    assert f._reply_dropped == 1
    assert f.parse(
        _item("RT @dotey: Claude Code 真香", tid="5555555555555555555")
    ) is None
    assert f._retweet_dropped == 1


def test_reply_and_retweet_filters_can_be_disabled(fresh_db):
    f = _fetcher(switches={"exclude_replies": False, "exclude_retweets": False})
    assert f.parse(_item("@dotey 你说得对，Claude Code 真香")) is not None
    f2 = _fetcher(switches={"exclude_replies": False, "exclude_retweets": False})
    assert f2.parse(
        _item("RT @dotey: Claude Code 真香", tid="6666666666666666666")
    ) is not None


# ══ 7. 开关默认值与拍板一致 ════════════════════════════════════════════

def test_switch_defaults_match_decision(fresh_db):
    f = _fetcher()
    assert f._bt_from_source_tags is True
    assert f._bt_from_topic_tags is True
    assert f._scope_fence_enabled is False
    assert f._exclude_replies is True
    assert f._exclude_retweets is True


# ══ 8. pV P0-1：旧的 topic 候选提前拦截已被移除 ═══════════════════════

def test_source_tags_only_admits_even_when_topic_pool_disabled(fresh_db):
    """关掉 topic 真相源 + 清空 ``topic_tags``，**只靠 source_tags** 也要入库。

    旧实现里 ``_topic_candidate_tags()`` 会在闸门之前直接 ``return None``，
    完全无视 ``business_tags_from_source_tags`` / ``business_tags_from_topic_tags``
    两个开关 → 只启用 source_tags 时条目被静默吞掉（还不计 dropped）。
    """
    f = _fetcher(
        topic_tags={},
        switches={
            "business_tags_from_topic_tags": False,
            "business_tags_from_source_tags": True,
        },
    )
    art = f.parse(_item(_WECHAT_HIT))
    assert art is not None, "只启用 source_tags 时不能被旧的 topic 候选逻辑拦掉"
    assert art["xbot_tags"] == ["x-ai-circle"]
    # 候选集合 = source 池（topic 池已关 → 不含 topic 映射带来的 tag）
    assert set(art["xbot_candidate_tags"]) == set(_BOUND)


def test_topic_pool_disabled_and_source_tags_disabled_is_fail_closed(fresh_db):
    """两个真相源都关 ⇒ 0 准入（fail-closed），并且**计入**丢弃计数。"""
    f = _fetcher(
        topic_tags={},
        switches={
            "business_tags_from_topic_tags": False,
            "business_tags_from_source_tags": False,
        },
    )
    assert f.parse(_item(_WECHAT_HIT)) is None
    assert f._business_gate_empty is True
    assert f._business_tag_dropped == 1
