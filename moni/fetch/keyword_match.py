"""Keyword matching rules shared by the fetch-time gates (v1.0.64).

Why this module exists
----------------------
Every gate in the fetcher used to match keywords with a bare
``keyword in text`` substring test.  That is fine for long Chinese
phrases, but it produces false positives for two classes of keyword
that the live tag set actually contains:

* **short ASCII tokens** — ``MA`` matched "MASAJIN", ``ark`` matched
  "Markdown" / "DarkScrim", ``App`` matched "AppleLogger".  A CJK
  document rarely has word separators, so a Latin substring has no
  natural boundary and hits the inside of unrelated words.
* **very short CJK words** — ``消费`` / ``银行`` / ``支付`` are real
  words, but they also appear once in passing inside a long article
  about something else (a travel note mentioning a restaurant, a
  book review mentioning a payment link).  One incidental occurrence
  in a 5,000-character body is not evidence that the article is about
  that topic.

The rules below keep substring semantics (Chinese has no spaces, so
``股票市场`` must still match ``股票``) and only tighten the two shapes
that are provably noisy:

===============  ==========================================  ==============
keyword shape    match rule                                  reason
===============  ==========================================  ==============
pure ASCII       word boundary on either alnum edge,          kills "MA"
                 optional plural suffix (``s`` / ``es``)     inside "MASAJIN"
pure Han, <= 2   hit the title, or repeat in the body at      kills one
characters       least ``SHORT_CJK_MIN_BODY_REPEATS`` times  passing mention
anything else    plain substring (unchanged)                  back-compat
===============  ==========================================  ==============

The plural suffix exists because ``AI Agent`` must still match
"AI Agents" (41 articles in the 2026-10-25 snapshot) and ``App`` must
still match "Apps" — losing those would be a regression, not a fix.

The repeat rule has one exemption: a body shorter than
``SHORT_CJK_SHORT_BODY_CHARS`` cannot physically repeat much, so a
single mention counts there.  A 60-character market brief saying
"腾讯" once is a legitimate hit; a 6,000-character travel diary saying
"消费" once is not.  Without the exemption the rule throws away short
but on-topic items (22 in the 2026-10-25 snapshot, measured).

Everything is case-sensitive, exactly as before: the tag sets use
``APP``/``App``/``MiniMax``/``minimax`` as separate deliberate
entries, so folding case here would change which tags fire.

Not covered on purpose
----------------------
* ``moni/cases/normalize.py`` and ``moni/events/normalize.py`` —
  closed-vocabulary classifiers over short Chinese words; substring
  is the intended semantics there.
* ``moni/wechat_clip.py`` — a manual title drop-list, not a content
  gate.
"""

from __future__ import annotations

import logging
import re
import time
from functools import lru_cache
from typing import Iterable, Literal, Sequence

logger = logging.getLogger(__name__)

# A pure-Han keyword at or below this length needs corroboration —
# one passing mention in a long body is not a topic signal.
SHORT_CJK_MAX_LEN = 2
# ...and "corroboration" means either a title hit or this many
# occurrences in the body.
SHORT_CJK_MIN_BODY_REPEATS = 2
# Bodies at or below this length are exempt from the repeat rule: there
# simply is not room to repeat.  Short briefs are exactly the shape the
# rule was NOT meant to punish.
SHORT_CJK_SHORT_BODY_CHARS = 200

_HAN_RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF))


def _is_word_char(ch: str) -> bool:
    """True for the characters that make an ASCII token continue."""
    return ch.isascii() and (ch.isalnum() or ch == "_")


def is_pure_ascii(keyword: str) -> bool:
    """Every character is < 128."""
    return bool(keyword) and all(ord(ch) < 128 for ch in keyword)


def is_pure_han(keyword: str) -> bool:
    """Every character is a Han ideograph.

    Deliberately strict: mixed tokens such as ``A股`` or ``OAuth2.0``
    fall through to plain substring matching, which is already precise
    for them.
    """
    if not keyword:
        return False
    for ch in keyword:
        code = ord(ch)
        if not any(lo <= code <= hi for lo, hi in _HAN_RANGES):
            return False
    return True


@lru_cache(maxsize=8192)
def _ascii_pattern(keyword: str) -> re.Pattern[str]:
    """Compile the boundary-aware pattern for one ASCII keyword."""
    left = r"(?<![A-Za-z0-9_])" if _is_word_char(keyword[0]) else ""
    right = r"(?![A-Za-z0-9_])" if _is_word_char(keyword[-1]) else ""
    # Only allow the plural suffix when the keyword itself ends in a
    # Latin letter — "CI/CD" or "deepseek-v4" must not get one.
    plural = r"(?:s|es)?" if keyword[-1].isascii() and keyword[-1].isalpha() else ""
    return re.compile(left + re.escape(keyword) + plural + right)


MatchField = Literal["title", "content", "title+content"]


def keyword_matches(
    keyword: str,
    *,
    title: str,
    body: str,
    field: MatchField = "title+content",
) -> bool:
    """Does ``keyword`` match this article under the v1.0.64 rules?

    ``field`` mirrors the ``source_keyword_filters.match_field``
    vocabulary.  ``"title"`` is also what the ``bestblogs``-family tag
    gate uses (their feed descriptions are boilerplate soup), so the
    three values cover every caller.
    """
    if not keyword:
        return False
    title = title or ""
    body = body or ""

    if field == "title":
        haystack, title_ok, body_ok = title, title, ""
    elif field == "content":
        haystack, title_ok, body_ok = body, "", body
    else:
        haystack, title_ok, body_ok = title + "\n" + body, title, body

    if is_pure_ascii(keyword):
        return _ascii_pattern(keyword).search(haystack) is not None

    if is_pure_han(keyword) and len(keyword) <= SHORT_CJK_MAX_LEN:
        if keyword in title_ok:
            return True
        if len(body_ok) <= SHORT_CJK_SHORT_BODY_CHARS:
            return keyword in body_ok
        return body_ok.count(keyword) >= SHORT_CJK_MIN_BODY_REPEATS

    return keyword in haystack


def matched_keywords(
    keywords: Iterable[str],
    *,
    title: str,
    body: str,
    field: MatchField = "title+content",
) -> list[str]:
    """Return every keyword from ``keywords`` that matches, order preserved."""
    return [
        kw
        for kw in keywords
        if kw
        and keyword_matches(kw, title=title, body=body, field=field)
    ]


def keyword_profile(keywords: Sequence[str]) -> dict[str, int]:
    """Count keywords by the rule that governs them (for `keyword-audit`)."""
    profile = {"ascii_boundary": 0, "short_han": 0, "substring": 0, "empty": 0}
    for kw in keywords:
        if not kw:
            profile["empty"] += 1
        elif is_pure_ascii(kw):
            profile["ascii_boundary"] += 1
        elif is_pure_han(kw) and len(kw) <= SHORT_CJK_MAX_LEN:
            profile["short_han"] += 1
        else:
            profile["substring"] += 1
    return profile


# ── scope 正则 (sprint-55 / v1.0.131) ────────────────────────────────────
#
# X-bot 精选的 ``fetch_config.scope.wechat_regex`` 从 v1.0.131 起是 moni 的
# 入库准入门（不再只是展示副本）。它和 ``keyword_matches()`` 是**两种语义**：
#
#   * ``keyword_matches()`` —— 字面关键词 + 词边界 / 短中文配信度规则；
#   * ``regex_matches()``   —— 用户写的**真正则**（大小写不敏感）。
#
# 之所以仍然放在这个模块里: AGENTS.md 铁律 1 要求「匹配规则只有一份实现」。
# 开两个引擎各自演进 = 同一个概念在不同入口有不同语义。这里只是把
# 「正则怎么匹配」这一件事收敛成唯一实现，``keyword_matches()`` 一行未改。

#: 正则匹配前把文本截断到这个长度 —— 无上限的正则扫超长正文是回溯风险面。
SCOPE_REGEX_MAX_TEXT_CHARS = 2000

#: 单条正则匹配的健康阈值 (毫秒)。超过只记 warning (不改变结果)。
SCOPE_REGEX_SLOW_MS = 50


def compile_regex(
    pattern: object, *, ignore_case: bool = True
) -> re.Pattern[str] | None:
    """把用户写的正则编译成 ``re.Pattern``; 非法 / 空 → ``None``。

    ``None`` 的语义是「这个维度不参与匹配」—— **不是**「匹配一切」。
    保存侧 (``PUT /fetch_config``) 用 ``re.error`` 拒非法模式; 这里再兜一次,
    防止历史脏数据 / 手工改库让整轮抓取炸掉。
    """
    if not isinstance(pattern, str):
        return None
    text = pattern.strip()
    if not text:
        return None
    flags = re.IGNORECASE if ignore_case else 0
    try:
        return re.compile(text, flags)
    except re.error as exc:
        logger.warning("[keyword-match] invalid regex %r dropped: %s", text, exc)
        return None


def regex_matches(
    pattern: str | re.Pattern[str] | None,
    *,
    text: str,
    ignore_case: bool = True,
) -> bool:
    """``pattern`` 是否命中 ``text``（大小写不敏感，UTF-8 子串语义的正则）。

    * 空 / 非法 pattern → ``False``（该维度不参与，绝不放通）；
    * 匹配前按 :data:`SCOPE_REGEX_MAX_TEXT_CHARS` 截断文本；
    * 单次匹配超过 :data:`SCOPE_REGEX_SLOW_MS` 记 warning（便于发现灾难回溯
      型模式），**不改变返回值** —— 抓取不该因为一条正则慢就静默丢内容，
      也不该无限期阻塞。
    """
    if pattern is None:
        return False
    if isinstance(pattern, str):
        rx = compile_regex(pattern, ignore_case=ignore_case)
    elif isinstance(pattern, re.Pattern):
        rx = pattern
    else:
        # 非 str / 非编译结果（int / list / dict …）→ 该维度不参与，绝不匹配一切
        return False
    if rx is None:
        return False
    haystack = text or ""
    if len(haystack) > SCOPE_REGEX_MAX_TEXT_CHARS:
        haystack = haystack[:SCOPE_REGEX_MAX_TEXT_CHARS]
    started = time.perf_counter()
    try:
        hit = rx.search(haystack) is not None
    except Exception as exc:  # noqa: BLE001 — 正则运行期异常不得炸整轮抓取
        logger.warning("[keyword-match] regex %r raised %s — treated as no match",
                       rx.pattern, exc)
        return False
    elapsed_ms = (time.perf_counter() - started) * 1000
    if elapsed_ms > SCOPE_REGEX_SLOW_MS:
        logger.warning(
            "[keyword-match] slow regex %r took %.1fms (> %.0fms) on %d chars",
            rx.pattern, elapsed_ms, SCOPE_REGEX_SLOW_MS, len(haystack),
        )
    return hit

