"""``moni/fetch/keyword_match`` 的 scope 正则引擎（sprint-55 / v1.0.131）。

``regex_matches()`` 是「用户写的正则怎么匹配」的**唯一实现**（AGENTS.md 铁律 1）:
``keyword_matches()``（字面关键词）与它语义不同，一行未改。
"""
from __future__ import annotations

import re

import pytest

from moni.fetch.keyword_match import (
    SCOPE_REGEX_MAX_TEXT_CHARS,
    compile_regex,
    keyword_matches,
    regex_matches,
)


def test_empty_and_none_pattern_never_match():
    """空 / None ⇒ 该维度不参与（**不是**匹配一切）。"""
    assert regex_matches(None, text="任何文本") is False
    assert regex_matches("", text="任何文本") is False
    assert regex_matches("   ", text="任何文本") is False


def test_non_string_pattern_never_matches():
    for bad in (3, ["微信"], {"a": 1}, True):
        assert regex_matches(bad, text="微信") is False  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["(", "[a-", "a{2,1}", "(?P<n>", "*"])
def test_invalid_regex_does_not_raise_and_never_matches(bad):
    assert compile_regex(bad) is None
    assert regex_matches(bad, text="任何文本") is False


def test_case_insensitive_by_default():
    assert regex_matches("skill|agent", text="需要一个 AGENT 来编排") is True
    assert regex_matches("skill|agent", text="SKILL 栈") is True
    assert regex_matches("skill|agent", text="sKiLl") is True


def test_case_sensitive_when_asked():
    assert regex_matches("skill", text="SKILL", ignore_case=False) is False
    assert regex_matches("SKILL", text="SKILL", ignore_case=False) is True


def test_plain_substring_semantics_for_cjk():
    assert regex_matches("微信|小程序", text="这是微信生态的讨论") is True
    assert regex_matches("微信|小程序", text="讨论的是别的") is False


def test_compiled_pattern_is_accepted_and_reused():
    rx = compile_regex("agent")
    assert isinstance(rx, re.Pattern)
    assert regex_matches(rx, text="an agent") is True
    assert regex_matches(rx, text="nothing") is False


def test_long_text_is_truncated_before_matching():
    """超过上限的部分不参与匹配（防灾难回溯 / 无语义的长正文）。"""
    body = "x" * (SCOPE_REGEX_MAX_TEXT_CHARS + 50) + "agent"
    assert regex_matches("agent", text=body) is False, (
        "第 2000 字符之后的命中不该算命中（截断是硬约束）"
    )
    inside = "agent" + "x" * 10
    assert regex_matches("agent", text=inside) is True


def test_keyword_matches_semantics_unchanged():
    """回归护栏: 字面关键词引擎的行为不能因为新增正则引擎而变化。"""
    # ASCII 词边界规则仍在
    assert keyword_matches("MA", title="Markdown", body="") is False
    assert keyword_matches("MA", title="MA 投放", body="") is True
    # 短中文需求配信度规则仍在
    assert keyword_matches("微信", title="标题", body="微信") is True
    long_body = "无关" * 200 + "微信"
    assert keyword_matches("微信", title="标题", body=long_body) is False
