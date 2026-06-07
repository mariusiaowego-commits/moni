"""Unit tests for v1.0.5.7 title-prefix publisher extraction.

The fetcher's author fallback chain is::

    <dc:creator>  →  <author>  →  _extract_publisher_from_title(title)

where the regex peels off a leading ``[公众号名]`` from the title.
These tests cover the happy paths, the boundary cases, and the
unhappy paths (titles that don't follow the prefix convention).
"""
from __future__ import annotations

import os
import sys

import pytest


WORKTREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKTREE not in sys.path:
    sys.path.insert(0, WORKTREE)


from moni.fetch.sources.wechat_rss import _extract_publisher_from_title  # noqa: E402


# ── happy paths ─────────────────────────────────────────────────────────────


class TestPublisherFromTitle:
    def test_chinese_account_name(self):
        assert _extract_publisher_from_title(
            "[未央科技weiyangx] 国债入池"
        ) == "未央科技weiyangx"

    def test_short_account_name(self):
        assert _extract_publisher_from_title(
            "[财金吃瓜党] 网点行长工具"
        ) == "财金吃瓜党"

    def test_english_account_name(self):
        assert _extract_publisher_from_title(
            "[TechCrunch] Some article title"
        ) == "TechCrunch"

    def test_full_width_brackets(self):
        """Some items use full-width brackets (e.g. pasted from QQ)."""
        assert _extract_publisher_from_title(
            "［未央科技weiyangx］ 国债入池"
        ) == "未央科技weiyangx"

    def test_no_separator_between_bracket_and_title(self):
        """Title may run directly into the bracket with no separator."""
        assert _extract_publisher_from_title(
            "[foo]bar"
        ) == "foo"

    def test_with_leading_whitespace(self):
        """Some items have a stray space before the opening bracket."""
        assert _extract_publisher_from_title(
            " [未央科技weiyangx] 标题"
        ) == "未央科技weiyangx"


# ── unhappy paths (regex must NOT match) ────────────────────────────────────


class TestPublisherFromTitleRejects:
    def test_no_brackets(self):
        assert _extract_publisher_from_title("just a plain title") == ""

    def test_only_opening_bracket(self):
        assert _extract_publisher_from_title("[unclosed bracket") == ""

    def test_empty_string(self):
        assert _extract_publisher_from_title("") == ""

    def test_empty_brackets(self):
        """``[]`` has no name — should reject."""
        assert _extract_publisher_from_title("[] nothing here") == ""

    def test_unreasonable_length_rejected(self):
        """A 60-char name is almost certainly not a public-account name."""
        long_name = "x" * 60
        assert _extract_publisher_from_title(f"[{long_name}] stuff") == ""

    def test_title_is_just_emoji_no_brackets(self):
        assert _extract_publisher_from_title("🚀 月度报告") == ""


# ── regression: titles from waytomaster production data ────────────────────


class TestRealWechatTitles:
    """Verbatim titles from production waytomaster fetches.

    These come from the v1.0.5.6 verification run.  If waytomaster
    ever changes their title format, the fetcher will start
    returning empty publishers and these tests will catch it.
    """

    REAL_TITLES = [
        ("[未央科技weiyangx] 国债入池：养老金竞争从\"开户战\"升级到\"资产配置战\"",
         "未央科技weiyangx"),
        ("[未央科技weiyangx] 金融行业算力进化史：从「别宕机」到「缺GPU」",
         "未央科技weiyangx"),
        ("[未央科技weiyangx] 解读首部\u201c十五五\u201d国家专项规划——《城市更新\u201c十五五\u201d规划》",
         "未央科技weiyangx"),
        ("[未央科技weiyangx] 国际证监会组织发布《资本市场AI应用监管工具包》",
         "未央科技weiyangx"),
        ("[财金吃瓜党] 网点行长工具：零售营销管理「54321工作清单」",
         "财金吃瓜党"),
    ]

    @pytest.mark.parametrize("title,expected", REAL_TITLES)
    def test_real_title_extracts_publisher(self, title, expected):
        assert _extract_publisher_from_title(title) == expected
