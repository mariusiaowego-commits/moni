"""Tests for BestBlogs fetcher — date filtering, boilerplate cleaning, title-only matching."""

from __future__ import annotations

import pytest

from moni.fetch.sources.bestblogs import _clean_description, _in_date_range


# ── _in_date_range ────────────────────────────────────────────────────────


class TestInDateRange:
    def test_within_range(self):
        assert _in_date_range("2026-06-12T10:00:00+08:00", "2026-06-10", "2026-06-13")

    def test_before_range(self):
        assert not _in_date_range("2026-06-09T10:00:00+08:00", "2026-06-10", None)

    def test_after_range(self):
        assert not _in_date_range("2026-06-14T10:00:00+08:00", None, "2026-06-13")

    def test_no_pub_date_keeps_article(self):
        assert _in_date_range(None, "2026-06-10", "2026-06-13")

    def test_empty_pub_date_keeps_article(self):
        assert _in_date_range("", "2026-06-10", "2026-06-13")

    def test_unparseable_date_keeps_article(self):
        assert _in_date_range("not-a-date", "2026-06-10", "2026-06-13")

    def test_no_filters_keeps_article(self):
        assert _in_date_range("2026-06-12T10:00:00+08:00", None, None)

    def test_only_from(self):
        assert _in_date_range("2026-06-12T10:00:00+08:00", "2026-06-10", None)
        assert not _in_date_range("2026-06-09T10:00:00+08:00", "2026-06-10", None)

    def test_only_to(self):
        assert _in_date_range("2026-06-12T10:00:00+08:00", None, "2026-06-13")
        assert not _in_date_range("2026-06-14T10:00:00+08:00", None, "2026-06-13")

    def test_boundary_inclusive(self):
        assert _in_date_range("2026-06-10T00:00:00+00:00", "2026-06-10", None)
        assert _in_date_range("2026-06-13T23:59:59+00:00", None, "2026-06-13")


# ── _clean_description ────────────────────────────────────────────────────


class TestCleanDescription:
    def test_removes_click_follow(self):
        text = "AI泡沫已经在破了。点击关注公众号「腾讯技术工程」了解更多"
        cleaned = _clean_description(text)
        assert "点击关注" not in cleaned
        assert "AI泡沫" in cleaned

    def test_removes_long_press(self):
        text = "深度分析长按识别二维码关注我们"
        cleaned = _clean_description(text)
        assert "长按识别" not in cleaned

    def test_removes_share_to_moments(self):
        text = "重要观点。分享到朋友圈让更多人看到"
        cleaned = _clean_description(text)
        assert "分享到朋友圈" not in cleaned

    def test_removes_read_original(self):
        text = "摘要内容。点击阅读原文查看完整版"
        cleaned = _clean_description(text)
        assert "点击阅读原文" not in cleaned

    def test_preserves_real_content(self):
        text = "腾讯混元AI Infra进化，HPC-Ops推理核心算子全面升级。"
        cleaned = _clean_description(text)
        assert cleaned == text

    def test_empty_string(self):
        assert _clean_description("") == ""

    def test_only_boilerplate(self):
        text = "点击关注公众号「xxx」了解更多"
        cleaned = _clean_description(text)
        assert len(cleaned) < 5  # essentially empty
