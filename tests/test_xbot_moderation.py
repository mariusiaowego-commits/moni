"""sprint-54 / id 26111703 —— 决策函数 `moni.fetch.moderation` 的单元测试。

设计原则 (照 sprint-workflow §21 + moni-development 的断言强度铁律):

1. **反向断言必须正向可达** —— 每条「不该被拦」的用例, 先有一条**守卫测试**
   证明它真的走完了判定流程, 否则断言会假绿 (被别的 early-return 掩盖)。
2. **负控必做** —— 断言要能咬住: 改实现让断言变红, 再改回来。
3. **不用生产数据** —— 全部合成 fixture。
"""

from __future__ import annotations

import pytest

from moni.fetch.moderation import (
    AUTHOR_SIGNAL_MIN_SAMPLES,
    PUNCTUATION_BURST_MIN,
    RULE_VERSION,
    SHORT_BODY_MAX_CHARS,
    Decision,
    evaluate_article,
    evaluate_author_candidate,
)


# ── 守卫: 合成 fixture 真的走完了判定 (否则下面的反向断言全是假绿) ────────────────

def _normal_article() -> dict:
    """一条明显正常的推文 (开发者聊小程序技术)。"""
    return {
        "title": "微信小程序分包加载体积降下来了",
        "content": "分包之后主包体积减少 60%，加载明显变快。",
        "author_name": "某开发者",
        "handle": "dev_a",
        "topic": "wechat",
    }


def test_guard_normal_article_reaches_allow_branch():
    """守卫: 正常 fixture 真的走到 allow 分支 (不是被别的路径提前返回)。

    没有这条, 下面所有「不该被拦」断言都可能是假绿。
    """
    d = evaluate_article(**_normal_article())
    assert d.action == "allow", f"正常 fixture 被判成 {d.action} / {d.reason_code}"
    assert d.reason_code is None
    assert d.rule_version == RULE_VERSION


# ── 强信号: 黄赌毒 ────────────────────────────────────────────────────────────

def test_nsfw_solicit_combo_is_quarantined():
    """招嫖引流 (敏感词 + 引流动作) ⇒ quarantine / nsfw_gamble。"""
    d = evaluate_article(
        title="找妹",
        content="同城约炮，加微信联系，福利姬在线。",
        author_name="某某",
        handle="h1",
    )
    assert d.action == "quarantine"
    assert d.category == "nsfw_gamble"
    assert d.reason_code == "mod.nsfw.promo"
    assert d.is_weak_only is False


def test_sensitive_term_alone_is_allowed():
    """⚠️ 敏感词**单独**出现不构成违规 ⇒ 放行 (组合证据原则)。"""
    d = evaluate_article(
        title="聊聊最近的博彩监管",
        content="监管总局发布了关于博彩监管的新规，行业要合规经营。",
        author_name="某某评论",
        handle="h2",
    )
    assert d.action == "allow", f"敏感词单独出现被误杀: {d.reason_code}"


def test_exempt_term_downgrades_to_allow():
    """命中否定样本豁免词 ⇒ 降级放行 (反诈/科普/新闻)。"""
    d = evaluate_article(
        title="反诈提醒：虚拟币杀猪盘手法",
        content="警方通报近期有人以虚拟币投资为名实施诈骗，请警惕。",
        author_name="某地警方",
        handle="h3",
    )
    assert d.action == "allow", f"反诈/通报内容被误杀: {d.reason_code} / {d.evidences}"


def test_health_education_is_allowed():
    """性健康科普不得误杀。"""
    d = evaluate_article(
        title="性健康科普：HPV疫苗接种指南",
        content="本文科普疫苗接种年龄与注意事项，理性讨论，不推销任何产品。",
        author_name="某医院",
        handle="h4",
    )
    assert d.action == "allow", f"性健康科普被误杀: {d.reason_code}"


# ── 强信号: 交易引流 ──────────────────────────────────────────────────────────

def test_crypto_lead_gen_combo_is_quarantined():
    """币/股标的词 + 引流动作 ⇒ quarantine / fin_crypto_ad。"""
    d = evaluate_article(
        title="BTC合约带单",
        content="稳定收益合约带单，出入金加微，助理一对一。",
        author_name="币哥",
        handle="h5",
    )
    assert d.action == "quarantine"
    assert d.category == "fin_crypto_ad"
    assert d.reason_code == "mod.fin.crypto.ad"


def test_market_term_alone_is_allowed():
    """⚠️ 只提 BTC/股票 **不**构成广告 ⇒ 放行 (正常投资讨论)。"""
    d = evaluate_article(
        title="聊聊BTC的波动率",
        content="最近波动率明显上升，宏观与流动性因素交织，研报观点供参考。",
        author_name="某量化",
        handle="h6",
    )
    assert d.action == "allow", f"正常投资讨论被误杀: {d.reason_code}"


# ── 强信号: 灰产服务 ──────────────────────────────────────────────────────────

def test_author_name_stuffing_is_quarantined():
    """账号名堆砌灰产词 ⇒ quarantine / spam_service。"""
    d = evaluate_article(
        title="业务咨询",
        content="欢迎咨询，详情见主页。",
        author_name="海外Essay代写｜刷粉刷赞",
        handle="h7",
    )
    assert d.action == "quarantine"
    assert d.category == "spam_service"
    assert d.reason_code == "mod.spam.service"


# ── 弱特征: 绝不自动封号 ──────────────────────────────────────────────────────

def test_weak_signal_is_quarantined_but_weak_only():
    """弱特征 ⇒ quarantine 但 ``is_weak_only=True``。"""
    d = evaluate_article(
        title="家人们！！！",
        content="笑死",
        author_name="某人",
        handle="h8",
    )
    assert d.action == "quarantine"
    assert d.category == "low_value_rant"
    assert d.reason_code == "mod.lowvalue.rant"
    assert d.is_weak_only is True
    assert d.author_signal is False, "弱特征绝不可产出作者级候选"


def test_curated_author_is_exempt_from_weak_signal():
    """人工精选作者 (技术大 V) 豁免弱特征。"""
    d = evaluate_article(
        title="崩溃了！！！",
        content="这框架炸了",
        author_name="某大V",
        handle="h9",
        author_is_curated=True,
    )
    assert d.action == "allow", f"精选作者被弱特征误杀: {d.reason_code}"


def test_curated_author_is_NOT_exempt_from_strong_signal():
    """⚠️ 精选作者**不**豁免强证据 (豁免只针对弱特征)。"""
    d = evaluate_article(
        title="找妹",
        content="同城约炮，加微信联系。",
        author_name="某大V",
        handle="h10",
        author_is_curated=True,
    )
    assert d.action == "quarantine", "精选作者被强证据放行 ⇒ 豁免范围写错了"
    assert d.category == "nsfw_gamble"


# ── 作者级候选: 弱特征永远不升级 ─────────────────────────────────────────────

def test_weak_only_never_produces_author_candidate():
    """弱特征样本数再多, 也不产生作者候选 (调用方只传强信号样本数)。"""
    assert evaluate_author_candidate(author_strong_sample_count=0) is False
    assert evaluate_author_candidate(author_strong_sample_count=1) is False
    # 阈值本身
    assert AUTHOR_SIGNAL_MIN_SAMPLES >= 2


def test_author_candidate_requires_enough_strong_samples():
    """强信号样本数达标 ⇒ 可产出**候选** (仍需 dad 确认)。"""
    assert evaluate_author_candidate(
        author_strong_sample_count=AUTHOR_SIGNAL_MIN_SAMPLES
    ) is True
    assert evaluate_author_candidate(
        author_strong_sample_count=AUTHOR_SIGNAL_MIN_SAMPLES - 1
    ) is False


def test_author_candidate_zero_threshold_is_disabled():
    """阈值 ≤ 0 ⇒ 永��产出候选 (防止配置把闸门打开)。"""
    assert evaluate_author_candidate(author_strong_sample_count=99, min_samples=0) is False


# ── 纯函数性 ────────────────────────────────────────────────────────────────

def test_evaluate_is_pure_and_deterministic():
    """同一输入 ⇒ 同一输出 (快照签名/幂等重放的前提)。"""
    args = _normal_article()
    a = evaluate_article(**args)
    b = evaluate_article(**args)
    assert a == b


def test_decision_is_immutable():
    """Decision 不可变 ⇒ 可当缓存键。"""
    d = evaluate_article(**_normal_article())
    with pytest.raises(Exception):
        d.action = "quarantine"  # type: ignore[misc]


def test_evidence_is_bounded_signal_names_not_raw_text():
    """证据串只含**信号名**, 不含整段原文 (防露骨正文进日志/清单)。

    ⚠️ 判据说明: 证据里**会**出现被命中的那个词本身 (如 ``sensitive_term:约炮``)
    —— 那是「命中了什么」的最小必要信息, 且是**词表里的固定词**, 不是原文片段。
    真正要防的是**整句原文**被复制进证据 (那才会带出露骨正文)。

    ⇒ 判据写成两条:
      ① 证据里**不含**原文里的连续长片段 (≥6 字);
      ② 证据里出现的敏感词**全部来自词表常量** (不是从正文动态摘抄的)。
    """
    raw = "同城约炮，加微信联系，福利姬在线，私信我，加我微信号 abc123"
    d = evaluate_article(
        title="找妹",
        content=raw,
        author_name="某某",
        handle="h11",
    )
    assert d.evidences
    joined = " ".join(d.evidences)

    # ① 不含原文里的连续长片段
    for frag in ("同城约炮", "加微信联系", "福利姬在线", "私信我", "abc123"):
        assert frag not in joined, f"证据里漏出了原文片段: {frag}"

    # ② 证据里的敏感词全部来自词表常量 (不是动态摘抄正文)
    from moni.fetch.moderation import _MARKET_TERMS, _SENSITIVE_TERMS, _SOLICIT_ACTIONS
    known = set(_SENSITIVE_TERMS) | set(_SOLICIT_ACTIONS) | set(_MARKET_TERMS)
    for ev in d.evidences:
        _, _, val = ev.partition(":")
        if val and not val.isdigit():
            assert val in known, f"证据里的词不在词表常量中 (疑似动态摘抄原文): {ev}"


def test_thresholds_are_marked_as_suggestions():
    """阈值是「待实测建议」起点 —— 这里钉住它们仍是**未标定**的量级。"""
    assert PUNCTUATION_BURST_MIN >= 1
    assert SHORT_BODY_MAX_CHARS >= 1


# ── p8 护栏 G-B: 「仅命中裸词微信」必须基于**完整命中集**, 不能读 article_keyword_hits ──

def test_only_bare_wechat_needs_complete_keyword_set():
    """⚠️ 首命中词是「微信」但完整命中集含「微信小程序」 ⇒ **不算**仅命中裸词。

    这正是 ``article_keyword_hits`` 的陷阱: ``xbot_feed.py:1454`` 每个 tag
    只记**首**个命中词, 所以该表里只有「微信」, 而正文其实还含「微信小程序」。
    ⇒ 只看该表会把真业务内容误判成「仅命中裸词」⇒ 必须重放完整词表。
    """
    from moni.fetch.moderation import evaluate_article as ea

    # 正文很短 + 裸词命中, 但完整命中集里有具体业务词 ⇒ 不应被判「仅裸词」
    d = ea(
        title="微信",
        content="微信",
        author_name="某号",
        handle="h20",
        all_matched_keywords=["微信", "微信小程序"],
    )
    assert "only_bare_wechat" not in d.evidences, (
        "完整命中集含具体业务词时, 不得判成『仅命中裸词』"
    )


def test_only_bare_wechat_detected_with_complete_set():
    """完整命中集**只有**裸词「微信」 ⇒ 判成仅裸词 (弱信号)。"""
    from moni.fetch.moderation import evaluate_article as ea

    d = ea(
        title="家人们",
        content="笑死",
        author_name="某号",
        handle="h21",
        all_matched_keywords=["微信"],
    )
    assert "only_bare_wechat" in d.evidences
    assert d.action == "quarantine"
    assert d.is_weak_only is True


def test_bare_wechat_alone_without_keyword_set_is_not_flagged():
    """没给完整命中集 ⇒ **不**推断「仅裸词」(宁可漏判也不误判)。"""
    from moni.fetch.moderation import evaluate_article as ea

    d = ea(
        title="家人们",
        content="笑死",
        author_name="某号",
        handle="h22",
    )
    assert "only_bare_wechat" not in d.evidences


# ── p8 护栏: 行为化历史豁免 (替代静态 28 账号名单) ──

def test_clean_history_downweights_weak_signal_instead_of_exempting():
    """⚠️ dad 拍板: 干净历史只能**降权**, 不能硬豁免。

    降权 = 干净历史作者需要**≥2 条**弱证据才被隔离; 只有 1 条 ⇒ 放行
    (但留痕 downweighted)。
    """
    from moni.fetch.moderation import CLEAN_HISTORY_MIN, evaluate_article as ea

    # 只有 1 条弱证据 (情绪词「家人们」; 正文 30 字 ⇒ 不触发 short_body,
    # 无标点爆发 ⇒ 无 punctuation_burst) ⇒ 降权后放行, 且留痕
    d = ea(
        title="家人们",
        content="今天聊聊分包之后主包体积怎么压，讨论了一些实现细节与取舍方案",
        author_name="某人",
        handle="h23",
        author_clean_history_count=CLEAN_HISTORY_MIN,
        author_strong_sample_count=0,
    )
    assert d.action == "allow", f"干净历史作者被单条弱证据误杀: {d.reason_code}"
    assert any("author_clean_history_downweighted" in e for e in d.evidences), (
        f"降权必须留痕 (可审计), 实际 evidences={d.evidences}"
    )


def test_clean_history_still_quarantines_with_two_weak_evidence():
    """降权**不是**豁免: 2 条弱证据照样隔离。"""
    from moni.fetch.moderation import CLEAN_HISTORY_MIN, evaluate_article as ea

    d = ea(
        title="家人们！！！",
        content="笑死笑死",
        author_name="某人",
        handle="h23b",
        author_clean_history_count=CLEAN_HISTORY_MIN,
        author_strong_sample_count=0,
    )
    assert d.action == "quarantine", "降权后 2 条弱证据仍被放行 ⇒ 降权变成了豁免"
    assert d.is_weak_only is True


def test_clean_history_cannot_bypass_high_confidence_safety_signal():
    """⚠️ 干净历史**绝不**绕过高置信安全信号 (黄赌毒)。

    这是 dad 拍板的硬约束: 降权只作用于**弱特征**, 强信号在更早的分支
    就已返回 quarantine。
    """
    from moni.fetch.moderation import CLEAN_HISTORY_MIN, evaluate_article as ea

    d = ea(
        title="加群有福利",
        content="博彩平台注册送彩金，私聊入群",
        author_name="某人",
        handle="h25",
        author_clean_history_count=CLEAN_HISTORY_MIN,
        author_strong_sample_count=0,
    )
    assert d.action == "quarantine", (
        f"干净历史账号绕过了安全信号: action={d.action} reason={d.reason_code}"
    )
    assert d.is_weak_only is False, "强信号被标成弱特征 ⇒ 口径错了"


def test_clean_history_downweight_does_not_apply_after_strong_violation():
    """⚠️ 有强违规记录时, 干净历史**不再**降权。"""
    from moni.fetch.moderation import CLEAN_HISTORY_MIN, evaluate_article as ea

    d = ea(
        title="家人们！！！",
        content="笑死",
        author_name="某人",
        handle="h24",
        author_clean_history_count=CLEAN_HISTORY_MIN,
        author_strong_sample_count=1,
    )
    assert d.action == "quarantine", "有强违规记录的作者被降权放行了"
    assert d.is_weak_only is True


# ── p8 P1-A: 强信号分支必须能产出 author_signal=True (候选, 非封禁) ──────────

def test_strong_signal_sets_author_signal_when_enough_samples():
    """⚠️ 强信号 + 同作者多篇强样本 ⇒ ``author_signal=True``。

    ⚠️ 这是**候选**, 绝不自动封禁 —— ``action`` 仍是 ``quarantine``,
    且**不产生任何写库副作用** (无 DB import, 见 AC-MOD-09/11)。
    """
    from moni.fetch.moderation import AUTHOR_SIGNAL_MIN_SAMPLES, evaluate_article as ea

    d = ea(
        title="加群有福利",
        content="博彩平台注册送彩金，私聊入群",
        author_name="某人",
        handle="h30",
        author_strong_sample_count=AUTHOR_SIGNAL_MIN_SAMPLES,
    )
    assert d.action == "quarantine"
    assert d.author_signal is True, (
        f"强信号 + 足够强样本时 author_signal 必须是 True (候选), 实际 {d.author_signal}"
    )


def test_strong_signal_author_signal_false_below_threshold():
    """强样本数不足 ⇒ ``author_signal=False``。"""
    from moni.fetch.moderation import evaluate_article as ea

    d = ea(
        title="加群有福利",
        content="博彩平台注册送彩金，私聊入群",
        author_name="某人",
        handle="h31",
        author_strong_sample_count=1,
    )
    assert d.action == "quarantine"
    assert d.author_signal is False


def test_weak_signal_never_sets_author_signal():
    """⚠️ 弱特征命中 ⇒ ``author_signal`` **恒** False (绝不升级成作者候选)。"""
    from moni.fetch.moderation import evaluate_article as ea

    d = ea(
        title="家人们",
        content="今天聊聊分包之后主包体积怎么压，讨论了一些实现细节与取舍方案",
        author_name="某人",
        handle="h32",
        author_strong_sample_count=99,
    )
    # 无干净历史 ⇒ 单条弱证据即隔离 (这是**预期**行为)
    assert d.action == "quarantine"
    assert d.is_weak_only is True
    # ⚠️ 关键断言: 即便强样本数给到 99, 弱特征**也绝不**产出作者候选
    assert d.author_signal is False, "弱特征被升级成作者候选了"


def test_name_stuffing_signal_sets_author_signal():
    """账号名堆砌 (灰产服务) 也是强信号 ⇒ 同样能产出候选。"""
    from moni.fetch.moderation import AUTHOR_SIGNAL_MIN_SAMPLES, evaluate_article as ea

    d = ea(
        title="业务咨询",
        content="欢迎咨询",
        author_name="代写刷粉代做",
        handle="h33",
        author_strong_sample_count=AUTHOR_SIGNAL_MIN_SAMPLES,
    )
    assert d.category == "spam_service"
    assert d.author_signal is True


# ── p8 P2-C: 弱豁免词不得压过强信号 (黑产夹带行业行话) ──────────────────────

def test_weak_exempt_term_cannot_bypass_solicit_strong_signal():
    """⚠️ "量化/风控/冻卡" + 招揽动作 ⇒ **不得**豁免放行。

    p8 P2-C 发现的漏洞: "BTC合约带单, 量化风控稳定盈利, 出入金防冻卡,
    加微信进群" 原本因命中弱豁免词直接 allow ⇒ 带单黑产免死。
    """
    from moni.fetch.moderation import evaluate_article as ea

    d = ea(
        title="合约带单",
        content="BTC合约带单，量化风控稳定盈利，出入金防冻卡，加微信进群",
        author_name="某人",
        handle="h34",
    )
    assert d.action == "quarantine", f"黑产文案被行业行话豁免了: {d.evidences}"
    assert d.category == "fin_crypto_ad"


def test_weak_exempt_term_cannot_bypass_name_stuffing():
    """⚠️ 正文含「教育」+ 账号名是代写 ⇒ **不得**豁免。

    "海外教育论文代写, 加微信私聊" / 账号名 "XX教育Essay代写"
    —— 账号名本身就是广告, 不该被正文的「教育」二字豁免。
    """
    from moni.fetch.moderation import evaluate_article as ea

    d = ea(
        title="论文辅导",
        content="海外教育论文代写，加微信私聊",
        author_name="论文教育Essay代写",
        handle="h35",
    )
    assert d.action == "quarantine", f"灰产账号被正文豁免词放过: {d.evidences}"


def test_weak_exempt_term_still_allows_normal_investment_talk():
    """✅ 正常投资分析 (无招揽动作) ⇒ 弱豁免词仍放行 (不误杀)。"""
    from moni.fetch.moderation import evaluate_article as ea

    d = ea(
        title="宏观观察",
        content="这篇研报讲的是宏观利率与量化风控框架，不涉及任何交易建议",
        author_name="某人",
        handle="h36",
    )
    assert d.action == "allow"
    assert any("exempt_weak_term" in e for e in d.evidences)


def test_strong_exempt_term_still_allows_with_solicit_words():
    """✅ 强豁免 (司法/反诈语境) ⇒ 无条件放行, 即使含招揽动作词。

    "反诈提醒: 有人在群里拉人做博彩, 请勿扫码" —— 含「拉群」「扫码」
    但语境是反诈宣传 ⇒ 必须放行。
    """
    from moni.fetch.moderation import evaluate_article as ea

    d = ea(
        title="反诈提醒",
        content="警方通报有人在群内拉人做博彩，请勿扫码，谨防诈骗",
        author_name="某人",
        handle="h37",
    )
    assert d.action == "allow"
    assert any("exempt_strong_term" in e for e in d.evidences)


# ── p8 P2-A: 0 条弱证据时不该留 downweighted 痕迹 ──────────────────────────

def test_no_downweight_trace_when_no_weak_evidence():
    """✅ 完全正常的推文 ⇒ 不该出现 ``author_clean_history_downweighted``。

    p8 P2-A: 之前 ``if downweighted:`` 会在 0 条弱证据时也留痕 ⇒
    正常推文不该带「被降权」的审计噪声。
    """
    from moni.fetch.moderation import CLEAN_HISTORY_MIN, evaluate_article as ea

    d = ea(
        title="技术分享",
        content="今天聊聊分包之后主包体积怎么压，讨论了一些实现细节与取舍方案",
        author_name="某人",
        handle="h38",
        author_clean_history_count=CLEAN_HISTORY_MIN,
        author_strong_sample_count=0,
    )
    assert d.action == "allow"
    assert not any("downweighted" in e for e in d.evidences), (
        f"正常推文不该留降权痕迹: {d.evidences}"
    )


def test_downweight_trace_keeps_the_downgraded_weak_evidence():
    """✅ 降权放行时, 被降权的弱证据本身也要留在 evidences 里 (可审计)。"""
    from moni.fetch.moderation import CLEAN_HISTORY_MIN, evaluate_article as ea

    d = ea(
        title="家人们",
        content="今天聊聊分包之后主包体积怎么压，讨论了一些实现细节与取舍方案",
        author_name="某人",
        handle="h39",
        author_clean_history_count=CLEAN_HISTORY_MIN,
        author_strong_sample_count=0,
    )
    assert d.action == "allow"
    assert any("rant_term" in e for e in d.evidences), f"弱证据丢失: {d.evidences}"
    assert any("downweighted" in e for e in d.evidences), f"降权痕迹丢失: {d.evidences}"


# ── p7 最终复核 P1-1·新: 正文豁免词不得放行「账号名即广告」的灰产账号 ──────────

def test_weak_exempt_term_cannot_excuse_ad_like_account_name():
    """⚠️ 账号名堆砌 + 正文含**弱**豁免词 + 正文**无**招揽动作 ⇒ 必须 quarantine。

    p7 最终复核发现的漏放 (A 例):
      content="海外教育论文代写 辅导", author_name="论文教育Essay代写"
      ⇒ 原先 allow(evidences=('exempt_weak_term:教育',)) ⇒ 豁免成了免死金牌。

    ⚠️ 弱豁免条件里必须带 ``not name_stuffing`` —— 否则这一路根本不成立,
    用例也覆盖不到 (原用例的正文含「加微信」⇒ 压根没走到豁免分支)。
    """
    from moni.fetch.moderation import evaluate_article as ea

    d = ea(
        title="论文辅导",
        content="海外教育论文代写 辅导",
        author_name="论文教育Essay代写",
        handle="h40",
    )
    assert d.action == "quarantine", f"灰产账号被豁免词放行了: {d.evidences}"
    assert d.category == "spam_service"


def test_strong_exempt_term_cannot_excuse_ad_like_account_name():
    """⚠️ 账号名堆砌 + 正文含**强**豁免词 (警方/通报) ⇒ 同样 quarantine。

    p7 的 B 例: content="警方通报：某地代写诈骗案",
    author_name="论文教育Essay代写" ⇒ 原先 allow ⇒ 现 quarantine。

    口径理由: 豁免只豁免「**内容**像合规语境」, 豁免不了「**账号名本身**就是广告」。
    ⚠️ 已知取舍: 一篇正经的「警方通报 XX 代写诈骗案」科普文, 若**账号名**恰好
    含「代写/刷粉」等词会被拦下 ⇒ 该取舍属 P2, 待 p8/dad 确认口径。
    """
    from moni.fetch.moderation import evaluate_article as ea

    d = ea(
        title="案件通报",
        content="警方通报：某地代写诈骗案",
        author_name="论文教育Essay代写",
        handle="h41",
    )
    assert d.action == "quarantine", f"灰产账号被强豁免词放行了: {d.evidences}"
    assert d.category == "spam_service"


def test_ad_like_account_name_is_quarantined_regardless_of_body_exemption():
    """⚠️ 账号名堆砌 + 正文**完全干净** ⇒ 也必须 quarantine (对照组)。

    p7 的 C 例: 唯一差别是正文有没有豁免词 ——
    若三条都 quarantine, 说明**豁免词不再是变量**, 判据稳定。
    """
    from moni.fetch.moderation import evaluate_article as ea

    d = ea(
        title="论文辅导",
        content="论文辅导 代写 接单",
        author_name="论文教育Essay代写",
        handle="h42",
    )
    assert d.action == "quarantine"
    assert d.category == "spam_service"


def test_clean_account_name_still_gets_exemption():
    """✅ 账号名**干净**时, 豁免词照常生效 (不误杀正常的科普/反诈内容)。"""
    from moni.fetch.moderation import evaluate_article as ea

    d = ea(
        title="反诈提醒",
        content="警方通报有人在群内拉人做博彩，请勿扫码，谨防诈骗",
        author_name="某政务号",
        handle="h43",
    )
    assert d.action == "allow"
    assert any("exempt_strong_term" in e for e in d.evidences)


def test_clean_account_name_still_gets_weak_exemption():
    """✅ 账号名干净 + 弱豁免词 + 无招揽 ⇒ 仍放行。"""
    from moni.fetch.moderation import evaluate_article as ea

    d = ea(
        title="宏观观察",
        content="这篇研报讲的是宏观利率与量化风控框架，不涉及任何交易建议",
        author_name="某研究号",
        handle="h44",
    )
    assert d.action == "allow"
    assert any("exempt_weak_term" in e for e in d.evidences)
