"""Tests for moni.fetch.keyword_match (v1.0.64).

The three rules under test, and why each one exists:

* pure-ASCII keywords are matched on word boundaries, because ``MA``
  used to hit "MASAJIN" and ``ark`` used to hit "Markdown";
* pure-Han keywords of two characters or fewer need corroboration (a
  title hit, or repetition in the body), because one passing mention
  of ``消费`` in a travel note is not a topic signal;
* everything else keeps plain substring semantics, because Chinese has
  no word separators and ``股票市场`` must still hit ``股票``.

The plural allowance is deliberate: ``AI Agent`` has to keep matching
"AI Agents" (41 stored articles in the 2026-10-25 snapshot).
"""

from __future__ import annotations

import pytest

from moni.fetch.keyword_match import (
    SHORT_CJK_MAX_LEN,
    SHORT_CJK_MIN_BODY_REPEATS,
    SHORT_CJK_SHORT_BODY_CHARS,
    is_pure_ascii,
    is_pure_han,
    keyword_matches,
    keyword_profile,
    matched_keywords,
)


# ── keyword shape classification ────────────────────────────────────────────


@pytest.mark.parametrize("kw", ["MA", "ark", "App", "AI Agent", "CI/CD", "deepseek-v4"])
def test_pure_ascii_detected(kw):
    assert is_pure_ascii(kw) is True


@pytest.mark.parametrize("kw", ["银行", "A股", "生成式AI", ""])
def test_not_pure_ascii(kw):
    assert is_pure_ascii(kw) is False


@pytest.mark.parametrize("kw", ["银行", "支付", "不良", "通胀"])
def test_pure_han_detected(kw):
    assert is_pure_han(kw) is True


@pytest.mark.parametrize("kw", ["A股", "生成式AI", "CI/CD", ""])
def test_mixed_tokens_are_not_pure_han(kw):
    """Mixed tokens fall through to substring — they are already precise."""
    assert is_pure_han(kw) is False


def test_keyword_profile_counts_each_rule():
    profile = keyword_profile(["MA", "银行", "生成式AI", "", "App"])
    assert profile == {
        "ascii_boundary": 2,   # MA, App
        "short_han": 1,        # 银行
        "substring": 1,        # 生成式AI
        "empty": 1,
    }


# ── ASCII boundary rule ─────────────────────────────────────────────────────


def test_ascii_keyword_does_not_match_inside_a_longer_word():
    """The exact bug: 'MA' inside 'MASAJIN' and 'MAX_BOOKS'."""
    assert keyword_matches("MA", title="", body="晚餐是临时找的 MASAJIN 富良野店") is False
    assert keyword_matches("MA", title="", body="const MAX_BOOKS = 20") is False
    assert keyword_matches("MA", title="", body="(ct state)、MAC 地址等") is False
    assert keyword_matches("ark", title="", body="用 Markdown 编辑器写内容") is False
    assert keyword_matches("ark", title="", body="WallaperDarkScrimAlpha") is False


def test_ascii_keyword_matches_as_a_standalone_token():
    assert keyword_matches("MA", title="", body="营销自动化 (MA) 落地") is True
    assert keyword_matches("ark", title="", body="ark-code-latest 配额用尽") is True
    assert keyword_matches("App", title="macOS App 开发笔记", body="") is True


def test_ascii_boundary_respects_cjk_neighbours():
    """CJK characters are boundaries — Chinese text has no spaces."""
    assert keyword_matches("App", title="移动支付App上线", body="") is True
    assert keyword_matches("AI", title="看AI落地", body="") is True


def test_ascii_plural_suffix_still_matches():
    """Losing 'AI Agents' would be a regression, not a fix."""
    assert keyword_matches("AI Agent", title="", body="we ran three AI Agents") is True
    assert keyword_matches("App", title="", body="both Apps shipped") is True
    assert keyword_matches("App", title="", body="Apples are fruit") is False


def test_ascii_keyword_ending_in_digit_gets_no_plural():
    """'deepseek-v4' must not accept 'deepseek-v4s'."""
    assert keyword_matches("deepseek-v4", title="", body="deepseek-v4 上线") is True
    assert keyword_matches("deepseek-v4", title="", body="deepseek-v4s 上线") is False


def test_ascii_keyword_with_slash_is_escaped_not_a_regex():
    """'CI/CD' contains regex metacharacters — it must be literal."""
    assert keyword_matches("CI/CD", title="", body="用 CI/CD 流水线") is True
    assert keyword_matches("CI/CD", title="", body="用 CIXCD 流水线") is False


def test_plural_is_allowed_whenever_the_keyword_ends_in_a_letter():
    """Pin the real behaviour rather than an aspirational one.

    'CI/CD' ends in 'D', so 'CI/CDs' matches.  That is deliberate: the
    plural denotes the same concept, and rejecting it costs recall for no
    accuracy gain.  The guard that actually matters is that regex
    metacharacters stay literal, which the test above covers.
    """
    assert keyword_matches("CI/CD", title="", body="两条 CI/CDs 流水线") is True
    assert keyword_matches("MA", title="", body="MAs 是营销自动化") is True


# ── short-Han corroboration rule ────────────────────────────────────────────


def test_short_han_hits_when_in_title():
    assert keyword_matches("消费", title="消费金融新规落地", body="") is True


def test_short_han_single_passing_mention_in_body_is_not_enough():
    """The exact bug: a passing mention in an unrelated long body fired it."""
    body = "今天去了月饼店，买了两盒。" + "无关内容。" * 200
    assert keyword_matches("消费", title="五仁月饼", body=body) is False


def test_short_han_repeated_in_body_is_enough():
    body = "银行 " * SHORT_CJK_MIN_BODY_REPEATS
    assert keyword_matches("银行", title="", body=body) is True


def test_short_han_threshold_is_one_below_repeat_count():
    """Long enough to require the repeat, one short of it."""
    body = "银行 " * (SHORT_CJK_MIN_BODY_REPEATS - 1) + "填充内容。" * 80
    assert len(body) > SHORT_CJK_SHORT_BODY_CHARS
    assert keyword_matches("银行", title="", body=body) is False


def test_short_body_is_exempt_from_the_repeat_rule():
    """A 60-character market brief can only mention '腾讯' once."""
    body = "恒生科技指数午后跌超2%，腾讯领跌。" + "盘面回顾。" * 10
    assert len(body) <= SHORT_CJK_SHORT_BODY_CHARS
    assert keyword_matches("腾讯", title="盘面速递", body=body) is True


def test_long_body_still_needs_the_repeat():
    """Same single mention, long body — the false positive stays dead."""
    body = "恒生科技指数午后跌超2%，腾讯领跌。" + "盘面回顾。" * 400
    assert len(body) > SHORT_CJK_SHORT_BODY_CHARS
    assert keyword_matches("腾讯", title="盘面速递", body=body) is False


def test_short_body_exemption_does_not_leak_to_ascii():
    """Short bodies get no ASCII leniency — 'MA' in 'MASAJIN' is still wrong."""
    body = "晚餐是临时找的 MASAJIN 富良野店"
    assert len(body) <= SHORT_CJK_SHORT_BODY_CHARS
    assert keyword_matches("MA", title="北海道", body=body) is False


def test_short_body_exemption_does_not_leak_to_title_field():
    """field='title' has no body at all, so the exemption cannot apply."""
    assert keyword_matches("腾讯", title="普通标题", body="腾讯", field="title") is False


def test_long_han_keyword_keeps_substring_semantics():
    """Three-plus characters are specific enough — one hit is enough."""
    assert keyword_matches("生成式AI", title="", body="谈生成式AI 的落地") is True
    assert keyword_matches("商业银行", title="", body="某商业银行公告") is True


def test_short_body_exemption_boundary_is_inclusive():
    """200 / 201 characters decide whether a single body hit counts."""
    def body_of(n: int) -> str:
        return "腾讯" + "填" * (n - 2)

    assert keyword_matches("腾讯", title="", body=body_of(SHORT_CJK_SHORT_BODY_CHARS)) is True
    assert keyword_matches("腾讯", title="", body=body_of(SHORT_CJK_SHORT_BODY_CHARS + 1)) is False


def test_empty_body_never_triggers_the_exemption():
    """A 0-char body is 'short' but contains nothing — must not match."""
    assert keyword_matches("腾讯", title="", body="") is False


def test_short_han_boundary_constant_is_two():
    """Guard the threshold itself: 3-char words must not be gated."""
    assert SHORT_CJK_MAX_LEN == 2


# ── field selection ─────────────────────────────────────────────────────────


def test_title_field_ignores_body():
    """bestblog* feed descriptions are boilerplate — body must not count."""
    assert keyword_matches("微信小店", title="普通标题", body="微信小店上线", field="title") is False
    assert keyword_matches("微信小店", title="微信小店上线", body="", field="title") is True


def test_content_field_ignores_title():
    assert keyword_matches("微信小店", title="微信小店上线", body="无关", field="content") is False
    assert keyword_matches("微信小店", title="无关", body="微信小店上线", field="content") is True


def test_default_field_is_title_plus_content():
    assert keyword_matches("微信小店", title="微信小店上线", body="") is True
    assert keyword_matches("微信小店", title="", body="微信小店上线") is True


def test_short_han_title_rule_applies_under_content_field():
    """Under field='content' the title is out of scope, so no free pass."""
    assert keyword_matches("消费", title="消费金融", body="一句话", field="content") is False


# ── misc ────────────────────────────────────────────────────────────────────


def test_empty_keyword_never_matches():
    assert keyword_matches("", title="任意标题", body="任意正文") is False


def test_none_like_title_and_body_are_tolerated():
    assert keyword_matches("银行", title=None, body=None) is False  # type: ignore[arg-type]
    assert keyword_matches("银行", title="银行", body=None) is True  # type: ignore[arg-type]


def test_matched_keywords_preserves_order():
    kws = ["生成式AI", "MA", "银行", "支付"]
    got = matched_keywords(kws, title="银行 支付", body="生成式AI (MA)")
    assert got == ["生成式AI", "MA", "银行", "支付"]


def test_matched_keywords_drops_non_matching():
    got = matched_keywords(["MA", "银行"], title="银行公告", body="")
    assert got == ["银行"]


def test_case_sensitivity_is_unchanged():
    """'APP' and 'App' are separate deliberate entries in the live tag set."""
    assert keyword_matches("APP", title="", body="app store") is False
    assert keyword_matches("App", title="", body="app store") is False
    assert keyword_matches("App", title="", body="App Store") is True
