"""Deterministic content cleaning for every ingest path.

One module owns "what a clean article body looks like":

``normalize``  strip artifacts (frontmatter / HTML / entities / zero-width /
               replacement chars / wechat CTAs) and return plain text.
``assess``     classify the result as ``ok`` / ``short`` / ``empty``.
``apply_cleaning``  mutate an ingest dict with the cleaned text and the
               ``content_*`` columns the repository persists.

Why a separate module: the same body arrives through fetch, manual create,
basket rescue and full-text import.  Before this module each path cleaned
differently (or not at all), and ``extract_text`` was not idempotent, so a
second cleaning pass could empty a plain-text body that merely contained
``<ENV_ID>`` or ``营收 < 100 亿``.

Nothing here touches the DB, calls a model, or decides whether to keep an
article.  ``assess`` describes; the caller gates.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

CLEANER_ID = "clean_v1"
SHORT_CHARS = 200
#: A re-fetched body must be this many chars longer to replace the stored one.
UPGRADE_MIN_CHARS = 200

#: A YAML header bigger than this (or over half the document) is a broken
#: dump, not a header — bestblogs wrote one article's whole body into
#: ``title: "…"`` with literal ``\n`` escapes.  Stripping it would delete the
#: article, so the body is left alone and flagged instead.
_FRONTMATTER_MAX_CHARS = 1000
_FRONTMATTER_MAX_LINES = 40
_YAML_KEY_RE = re.compile(r"^[A-Za-z_][\w-]*\s*:")


def _key_of(line: str) -> str:
    return line.strip().split(":", 1)[0].strip()

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)
_REPLACEMENT_CHAR = "\ufffd"
_HTML_TAGS = (
    "a|abbr|article|aside|audio|b|blockquote|br|button|center|code|dd|div|dl|dt|"
    "em|figcaption|figure|font|footer|form|h[1-6]|head|header|hr|i|iframe|img|"
    "input|label|li|link|main|meta|nav|ol|option|p|pre|section|select|small|"
    "source|span|strong|sub|sup|style|table|tbody|td|textarea|tfoot|th|thead|"
    "tr|u|ul|video"
)
#: Only real HTML tag names with plausible attribute syntax.  A loose
#: ``<[A-Za-z/]…>`` also eats code (``i < customerNum; i++``,
#: ``当 a<b 且 b>c 时``) and XML samples, which mangles technical posts far
#: worse than leaving a stray tag in place.  The body deliberately excludes
#: CJK and comparison prose; ``content`` is just a variable name.
_ATTR = r"""(?:\s+[A-Za-z_:][-\w:.]*(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)"""
_PSEUDO_TAG_RE = re.compile(
    rf"<\s*/?\s*(?:{_HTML_TAGS})\b{_ATTR}{{0,8}}\s*/?>",
    re.I,
)
_ANY_TAG_RE = re.compile(r"<[^>\n]{0,200}>")
_BLOCK_TAG_RE = re.compile(
    r"<\s*/?\s*(?:p|div|h[1-6]|li|ul|ol|br|section|article|tr|td|blockquote|pre)\b",
    re.I,
)
_NON_TEXT_RE = re.compile(r"[^\W_]", re.UNICODE)
#: ``extract_text`` is a *block* extractor: it keeps only what it recognises as
#: blocks and silently drops the rest.  Feed it prose carrying inline tags —
#: ``<h1>404 Not Found</h1>``, Java generics, a ``<script>`` sample in a
#: security post — and the article dies (measured: 8,110 chars → 13; 5,617 →
#: 3,554; 76,803 → one paragraph missing).  So it only runs on bodies that are
#: actually markup (tag characters ≥ 5 % of the body); anything else is
#: stripped in place, which cannot remove text.
_HTML_MIN_BLOCK_TAGS = 8
_HTML_MIN_TAG_SHARE = 0.05
_HTML_KEEP_RATIO = 0.8
_BLOCK_BOUNDARY_RE = re.compile(
    rf"<\s*/?\s*(?:p|div|br|h[1-6]|li|ul|ol|tr|td|th|section|article|"
    rf"blockquote|pre|figure|header|footer)\b{_ATTR}{{0,8}}\s*/?>",
    re.I,
)

# Three deliberate shapes, calibrated against the production bodies (5,330
# rows) rather than guessed:
#
#   _FOOTER_START     line begins with a byline / footer marker → drop line
#   _FOOTER_CONTAINS  footer boilerplate that may follow a short lead-in
#                     ("本文转载目的在于知识分享，版权归原作者…") → drop line
#   _CTA_TAIL         wechat CTA glued to the tail of a line → cut the tail
#
# Never truncate mid-paragraph: huxiu bodies arrive as one 7k-char line, and
# phrases like 商务合作 / 版权归属 / 扫描二维码 occur *inside* real sentences
# (``东方枢纽国际商务合作区``, ``乘客在车站扫描二维码后…``).  An unanchored
# ``.*$`` rule deletes the rest of such an article.
_FOOTER_LINE_MAX = 150
_FOOTER_START = (
    "免责声明",
    "商务合作",
    "投稿邮箱",
    "本文来源",
    "本文来自",
    "本文版权归",
    "原标题",
    "点击上方",
    "戳「",
    "分享到朋友圈",
    "喜欢这篇文章",
)
_FOOTER_BYLINE_RE = re.compile(
    r"^(编辑|责编|校对)\s*(?:[|｜丨:：]\s*\S{1,12}|[：:]\s*\S{1,12})$"
)
#: Attribution boilerplate, but only in its footer *shape*: ``版权归属、内容合规性``
#: is a real sentence in a compliance article and must survive.
_FOOTER_CONTAINS_RE = re.compile(
    r"版权归[^。！？\n]{0,20}所有|转载请注明|未经授权禁止转载|转载目的在于|如有侵权"
)
_CTA_TAIL = (
    "点击阅读原文",
    "点击「在看」",
    "点击在看",
    "点击关注",
    "长按识别",
    "扫描二维码",
    "扫码关注",
    "分享到朋友圈",
    "阅读原文",
    "喜欢这篇文章",
)
_CTA_TAIL_CHARS = 30
#: A CTA tail never carries sentence punctuation.  Without this guard
#: ``乘客在车站扫描二维码后，可直接购买车票。`` loses its second half.
_SENTENCE_PUNCT = "。！？；，、：!?,;:"
#: Cutting inside ``长按扫描二维码 查阅报告全文`` leaves a "长按" stub behind.
_STUB_SUFFIXES = (
    "更多内容，请",
    "长按扫描",
    "长按",
    "扫描",
    "识别",
    "即可",
    "立即",
    "如需",
    "关注",
    "请",
)


@dataclass(frozen=True)
class Cleaned:
    text: str
    flags: tuple[str, ...] = ()
    changed: bool = False


@dataclass(frozen=True)
class ContentVerdict:
    state: str
    chars: int
    reasons: tuple[str, ...] = ()


def clean_boilerplate(text: str) -> str:
    """Drop footer lines and CTA tails.  Shared by source parsers."""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and len(stripped) <= _FOOTER_LINE_MAX and (
            stripped.startswith(_FOOTER_START)
            or bool(_FOOTER_BYLINE_RE.match(stripped))
            or _FOOTER_CONTAINS_RE.search(stripped)
        ):
            continue
        kept.append(_cut_cta_tail(line))
    return "\n".join(kept)


def _is_markup(text: str) -> bool:
    """True when the body is HTML, not prose that happens to contain ``<``."""
    tags = _ANY_TAG_RE.findall(text)
    if len(_BLOCK_TAG_RE.findall(text)) < _HTML_MIN_BLOCK_TAGS:
        return False
    return sum(len(t) for t in tags) >= _HTML_MIN_TAG_SHARE * len(text)


def _strip_tags_in_place(text: str) -> str:
    """Remove tags without re-flowing: block boundaries become newlines.

    Deleting tags outright glues paragraphs together (``一段</p><p>二段``),
    so block-level tags turn into line breaks while inline tags vanish.
    """
    text = _BLOCK_BOUNDARY_RE.sub("\n", text)
    return _PSEUDO_TAG_RE.sub("", text)


def _strip_frontmatter(text: str) -> tuple[str, str]:
    """Return ``(body, flag)``.

    ``flag`` is ``frontmatter`` when a YAML header was removed,
    ``frontmatter_embedded`` when it looks like YAML but is too big to be a
    header (the body was dumped into a field), else ``""``.
    """
    stripped = text.lstrip("\ufeff")
    if not stripped.startswith("---"):
        return text, ""
    lines = stripped.split("\n")
    if lines[0].strip() != "---":
        return text, ""
    for i in range(1, min(len(lines), _FRONTMATTER_MAX_LINES)):
        if lines[i].strip() != "---":
            continue
        header = lines[1:i]
        keyed = [
            ln for ln in header if ln.strip() and not ln.strip().startswith("#")
        ]
        if not all(_YAML_KEY_RE.match(ln) for ln in keyed):
            return text, ""
        # ``Q:`` / ``A:`` / ``Note:`` are common prose openers, so a block made
        # only of those is a question, not a header.  Alongside real keys it is
        # metadata and gets stripped with the rest.
        if keyed and all(
            _key_of(ln) in ("Q", "A", "Note") for ln in keyed
        ):
            return text, ""
        block_chars = sum(len(ln) for ln in header)
        dumped = block_chars > _FRONTMATTER_MAX_CHARS or (
            len(stripped) > 600 and block_chars > len(stripped) * 0.5
        )
        if dumped:
            return text, "frontmatter_embedded"
        return "\n".join(lines[i + 1:]), "frontmatter"
    return text, ""


def _cut_cta_tail(line: str) -> str:
    """Cut ``点击关注公众号…`` when it only trails the line."""
    cut = len(line)
    for phrase in _CTA_TAIL:
        idx = line.find(phrase)
        while idx != -1:
            tail = line[idx + len(phrase):]
            if (
                len(tail) <= _CTA_TAIL_CHARS
                and not any(ch in tail for ch in _SENTENCE_PUNCT)
            ):
                cut = min(cut, idx)
                break
            idx = line.find(phrase, idx + 1)
    if cut == len(line):
        return line
    return _drop_stub(line[:cut])


def _drop_stub(prefix: str) -> str:
    """Remove a dangling ``长按`` / ``请`` left where the CTA used to start."""
    text = prefix.rstrip(" \t，、：:")
    for _ in range(3):
        for stub in _STUB_SUFFIXES:
            if text.endswith(stub) and len(text) > len(stub):
                text = text[: -len(stub)].rstrip(" \t，、：:")
                break
            if text == stub:
                return ""
        else:
            break
    return text


def _collapse(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_FENCE_BLOCK_RE = re.compile(r"```.*?```", re.S)


def _protect_fences(text: str) -> tuple[str, dict[str, str]]:
    """Hide whole Markdown code fences, delimiters included.

    Hiding only the fence *bodies* used to lose the `` ``` `` markers and let
    ``_collapse`` reflow the sample, so a second pass saw the ``<div>`` inside
    a code block as live markup and deleted it.  The block is now swapped out
    verbatim, before any whitespace collapsing runs.
    """
    protected: dict[str, str] = {}
    if "```" not in text:
        return text, protected

    def _hide(match: re.Match[str]) -> str:
        ph = f"\x00F{len(protected)}\x00"
        protected[ph] = match.group(0)
        return ph

    return _FENCE_BLOCK_RE.sub(_hide, text), protected


def _restore_fences(text: str, protected: dict[str, str]) -> str:
    for ph, val in protected.items():
        text = text.replace(ph, val)
    return text


def normalize(raw: str, *, title: str = "") -> Cleaned:
    """Return plain text with deterministic artifacts removed.

    Idempotent: ``normalize(normalize(x).text).text == normalize(x).text``.
    ``title`` is accepted for callers that want to pass context; the current
    rules do not depend on it.
    """
    if not isinstance(raw, str) or not raw:
        return Cleaned("")

    flags: list[str] = []
    # Protect before collapsing: code samples must survive whitespace reflow.
    text, protected = _protect_fences(raw)
    text = _collapse(text)

    text, fm_flag = _strip_frontmatter(text)
    if fm_flag:
        flags.append(fm_flag)

    # Loop until stable: feeds double-escape (`&amp;lt;` → `&lt;` → `<`), and
    # a single pass would leave the second layer for the *next* run to strip —
    # which is exactly how a "second cleaning empties the body" bug hides.
    for _ in range(10):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
        if "entities" not in flags:
            flags.append("entities")

    if "<" in text and _is_markup(text):
        from .cleaner import extract_text

        stripped = extract_text(text)
        plain = _ANY_TAG_RE.sub("", text).strip()
        block_ok = not plain or len(stripped) >= _HTML_KEEP_RATIO * len(plain)
        if stripped != text and block_ok:
            text = stripped
            flags.append("html")
        else:
            text = _strip_tags_in_place(text)
            if "html" not in flags:
                flags.append("html")
    elif "<" in text:
        without_tags = _strip_tags_in_place(text)
        if without_tags != text:
            text = without_tags
            flags.append("html")

    if any(ch in text for ch in "\u200b\u200c\u200d\ufeff\u2060"):
        text = text.translate(_ZERO_WIDTH)
        flags.append("zero_width")

    if _REPLACEMENT_CHAR in text:
        text = text.replace(_REPLACEMENT_CHAR, "")
        flags.append("replacement")

    without_cta = clean_boilerplate(text)
    if without_cta != text:
        text = without_cta
        flags.append("boilerplate")

    text = _collapse(text)
    text = _restore_fences(text, protected)
    return Cleaned(
        text=text,
        flags=tuple(dict.fromkeys(flags)),
        changed=text != raw,
    )


def assess(cleaned: Cleaned) -> ContentVerdict:
    """Classify the cleaned body.  Never decides whether to keep it."""
    chars = len(cleaned.text)
    if not _NON_TEXT_RE.search(cleaned.text):
        return ContentVerdict("empty", chars, ("no_text",))
    if chars < SHORT_CHARS:
        return ContentVerdict("short", chars, ("below_200",))
    return ContentVerdict("ok", chars)


def apply_cleaning(art: dict) -> tuple[Cleaned, ContentVerdict]:
    """Clean ``art`` in place and fill the ``content_*`` column values.

    ``raw_content`` is dropped: leaving a dirty copy behind lets the
    repository's ``content or raw_content`` fallback write the artifact back.
    """
    body = art.get("content")
    if not isinstance(body, str) or not body:
        body = art.get("raw_content") or ""

    cleaned = normalize(body, title=str(art.get("title") or ""))
    verdict = assess(cleaned)

    art["content"] = cleaned.text
    art.pop("raw_content", None)
    art["content_state"] = verdict.state
    art["content_chars"] = verdict.chars
    art["content_reasons"] = json.dumps(
        list(dict.fromkeys(list(cleaned.flags) + list(verdict.reasons))),
        ensure_ascii=False,
    )
    art["content_cleaner"] = CLEANER_ID
    art["content_cleaned_at"] = datetime.now(timezone.utc).isoformat()
    return cleaned, verdict


def clean_for_write(art: dict, old_text: str = "") -> Cleaned | None:
    """Clean a replacement body, but refuse to empty substantial prose.

    ``old_text`` is only needed for paths replacing an existing body (manual
    edit, full-text import, basket rescue).  New inserts pass ``""``.
    """
    cleaned, verdict = apply_cleaning(art)
    if verdict.state == "empty" and len(old_text.strip()) >= SHORT_CHARS:
        return None
    return cleaned


__all__ = [
    "CLEANER_ID",
    "SHORT_CHARS",
    "UPGRADE_MIN_CHARS",
    "Cleaned",
    "ContentVerdict",
    "apply_cleaning",
    "assess",
    "clean_for_write",
    "clean_boilerplate",
    "normalize",
]
