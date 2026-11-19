"""Rule behaviour of ``evaluate_labels()`` (sprint-58).

Each test names a concrete false-positive class measured on the production
corpus; a rule that stops catching it gets caught here.
"""

from __future__ import annotations

from moni.fetch.moderation import LABEL_RULE_VERSION, evaluate_labels


def _cats(title: str = "", content: str = "") -> set[str]:
    return {label.category for label in evaluate_labels(title=title, content=content)}


def test_bare_words_do_not_trigger():
    """原稿的 100% 误杀根因：裸词「加」「qq」「二维码」「翻倍」。"""
    text = (
        "参加活动的人数增加了三倍，算力翻倍，营收翻倍。"
        "请到 qq.com 下载客户端，跨境二维码支付已上线。"
    )
    assert _cats(content=text) == set()


def test_contact_and_promise_in_same_window_is_pure_ad():
    text = "加微信领取资料，免费领取内部渠道名额，今天最后一天。"
    labels = evaluate_labels(content=text)
    assert [lb.category for lb in labels] == ["pure_ad"]
    assert labels[0].severity == "strong"
    assert labels[0].confidence == 0.8
    assert any(ev.startswith("contact:") for ev in labels[0].evidences)


def test_contact_and_promise_far_apart_do_not_trigger():
    filler = "正常内容。" * 60
    text = "加微信" + filler + "稳赚"
    assert "pure_ad" not in _cats(content=text)


def test_contact_without_promise_does_not_trigger():
    text = "有问题可以加微信联系我们的客服团队，工作日 9 点到 18 点。"
    assert _cats(content=text) == set()


def test_nsfw_cluster_routes_to_s_category():
    text = "加微信，博彩平台内部渠道，日入三百。"
    labels = evaluate_labels(content=text)
    assert labels and labels[0].category == "nsfw_gamble"
    assert labels[0].severity == "strong"


def test_market_cluster_routes_to_crypto_ad():
    text = "加微信，BTC 合约带单，收益翻倍。"
    labels = evaluate_labels(content=text)
    assert labels and labels[0].category == "fin_crypto_ad"


def test_gray_cluster_routes_to_spam_service():
    text = "代写论文，加微信私聊。"
    labels = evaluate_labels(content=text)
    assert labels and labels[0].category == "spam_service"


def test_anti_fraud_context_is_exempt():
    text = "警方通报：诈骗团伙以加微信稳赚为饵，已查处。"
    assert _cats(content=text) == set()


def test_real_credential_beats_exemption():
    """强豁免的唯一例外：正文里有真实引流凭证时，规则照常判定。"""
    text = "警方通报：涉案账号微信号：abc12345，加微信稳赚，请勿添加。"
    assert "pure_ad" in _cats(content=text)
    # 只有凭证、没有招揽动作时仍然不产出强标签。
    assert _cats(content="警方通报：涉案账号微信号：abc12345 请勿添加。") == set()


def test_bait_only_scans_body_not_title():
    """新闻标题里的"单日涨粉"不是求关注。"""
    assert _cats(title="野人先生单日涨粉近3万", content="门店排队三小时。") == set()
    assert "engagement_bait" in _cats(content="求涨粉，互关，评论区见。")


def test_bait_exempt_for_long_and_podcast_bodies():
    long_body = "本期节目我们聊了 AI。" + "内容。" * 400
    assert "engagement_bait" not in _cats(content="求涨粉" + long_body)
    assert "engagement_bait" not in _cats(content="本期节目：求涨粉") 


def test_bait_ignores_mutual_relation_word():
    """「相互关联」不是「互关」。"""
    assert "engagement_bait" not in _cats(content="两件事相互关联，需要一起看。")


def test_rant_needs_three_conditions():
    assert "low_value_rant" in _cats(content="卧槽！！！这也太离谱了吧")
    assert "low_value_rant" not in _cats(content="卧槽！这也太离谱了吧")
    assert "low_value_rant" not in _cats(content="卧槽！！！" + "正文。" * 300)
    assert "low_value_rant" not in _cats(content="正常内容！！！")


def test_soft_exempt_suppresses_weak_only():
    text = "风险提示：卧槽！！！这类骗局太离谱"
    assert "low_value_rant" not in _cats(content=text)


def test_rule_version_is_stable():
    assert LABEL_RULE_VERSION == "labels-v1"
