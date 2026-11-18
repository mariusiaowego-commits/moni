"""moni.fetch.content.assess — the ok / short / empty classification.

The boundary matters because ``short`` feeds the source health report and
``empty`` suppresses the LLM call; an off-by-one here would either hide
title-only articles or flag 200-char briefs as broken.
"""

from __future__ import annotations

import pytest

from moni.fetch.content import SHORT_CHARS, Cleaned, assess


def verdict(text: str):
    return assess(Cleaned(text))


class TestBoundaries:
    def test_empty_string(self):
        got = verdict("")
        assert got.state == "empty"
        assert got.chars == 0
        assert "no_text" in got.reasons

    def test_whitespace_only_is_empty(self):
        assert verdict("   \n\n\t ").state == "empty"

    def test_punctuation_only_is_empty(self):
        """``……`` and ``---`` carry no text — 55 rows in prod looked like this."""
        assert verdict("……").state == "empty"
        assert verdict("---").state == "empty"

    def test_one_char_is_short(self):
        got = verdict("字")
        assert got.state == "short"
        assert got.chars == 1
        assert "below_200" in got.reasons

    @pytest.mark.parametrize("n", [SHORT_CHARS - 1])
    def test_just_below_threshold(self, n):
        assert verdict("字" * n).state == "short"

    @pytest.mark.parametrize("n", [SHORT_CHARS, SHORT_CHARS + 1])
    def test_at_and_above_threshold(self, n):
        assert verdict("字" * n).state == "ok"

    def test_latin_text_counts(self):
        assert verdict("a" * SHORT_CHARS).state == "ok"

    def test_underscore_only_is_empty(self):
        assert verdict("___").state == "empty"
