"""HTML cleaner for raw article content.

36kr_rss fetcher stores article body as concatenated HTML (multiple articles
in one ``raw_content`` blob, each marked up with ``<h2>`` section headers and
``<p>`` / ``<strong>`` per-story blocks). Feeding that straight into the LLM
summarizer produces noisy, low-quality summaries. This module turns it into
clean plain text before storage or summarization.

Public API
----------
``extract_text(raw_html: str) -> str``
    Strip tags, drop non-content elements, normalise whitespace.
    Returns ``""`` for empty / falsy input.

Design notes
------------
- We use BeautifulSoup with the ``lxml`` parser when available (fast, robust);
  fall back to stdlib ``html.parser`` if lxml is missing. Lxml is the typical
  install on this project (it's already pulled in transitively by other
  dependencies).
- We **keep** the existing text inside ``<strong>``, ``<b>``, ``<em>``,
  ``<i>`` — those are typically sub-headings within a 36kr article and
  contain real content. We just unwrap the tag.
- We **drop** the contents of ``<script>``, ``<style>``, ``<noscript>``,
  ``<nav>``, ``<aside>``, ``<footer>``, ``<header>`` wholesale, because
  those are chrome / metadata that would pollute the summary.
- We **preserve** ``<div>`` content (some 36kr articles use ``<div>``
  paragraphs in addition to ``<p>``); the body text comes through either way.
- Headings (``<h1>``–``<h6>``) and list items (``<li>``) are kept as their
  own paragraphs so the LLM sees the structure.
- Output: paragraphs joined with ``"\n\n"``, intra-paragraph whitespace
  collapsed to single spaces, leading/trailing whitespace stripped.
"""

from __future__ import annotations

import logging
from typing import Iterable

logger = logging.getLogger(__name__)

# Tags whose entire subtree should be discarded (chrome / metadata).
_DROP_TAGS: frozenset[str] = frozenset({
    "script",
    "style",
    "noscript",
    "nav",
    "aside",
    "footer",
    "header",
    "form",
    "iframe",
})

# Tags that introduce a new paragraph boundary in the output.
_BLOCK_TAGS: frozenset[str] = frozenset({
    "p",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "li",
    "div",
    "blockquote",
    "pre",
    "br",
    "tr",
})

_TRY_PARSERS: tuple[str, ...] = ("lxml", "html.parser")


def _select_parser() -> str:
    """Return the best available BS4 parser name.

    Prefer ``lxml`` (fast, lenient). Fall back to stdlib ``html.parser``
    (slower, but always present). This avoids a hard dependency on lxml —
    cleaner still works on a minimal install.
    """
    for name in _TRY_PARSERS:
        try:
            __import__(name)  # cheap probe; bs4 will re-import internally
            return name
        except ImportError:
            continue
    # html.parser is in the stdlib, the loop should always succeed; this
    # is just defensive in case the stdlib itself is stripped.
    return "html.parser"


def _iter_block_tags(soup) -> Iterable:
    """Yield the relevant block-level elements in document order.

    We walk the tree top-down (not just .find_all on each tag) so the
    output keeps the original order when mixed tags interleave, and so
    that a single <p> containing a <strong> + a sibling <p> are emitted
    in the right sequence.
    """
    # Local import: avoid forcing bs4 at module import time (keeds the
    # import cost out of cron / health-check paths that don't clean HTML).
    from bs4 import BeautifulSoup, Tag

    if not isinstance(soup, BeautifulSoup):
        # Defensive: should never happen because we build soup right above.
        return
    body = soup.body or soup
    for descendant in body.descendants:
        if not isinstance(descendant, Tag):
            continue
        if descendant.name in _BLOCK_TAGS:
            yield descendant


def _clean_text(node) -> str:
    """Get text from a BS4 node with whitespace collapsed and stripped.

    Dropped tags have already been ``decompose()``-ed at this point, so
    the only thing we need to do is pull out the text and normalise
    whitespace. Walking ``node.descendants`` would double-count because
    every text node appears under both its parent and every ancestor —
    we use ``get_text()`` which yields each string exactly once.
    """
    import re as _re

    if node is None:
        return ""
    text = node.get_text(separator=" ", strip=True)
    return _re.sub(r"\s+", " ", text).strip()


def extract_text(raw_html: str) -> str:
    """Strip HTML tags from ``raw_html`` and return clean plain text.

    Behaviour:
        * Falsy / non-string input -> ``""``.
        * Tags in ``_DROP_TAGS`` (script / style / nav / aside / footer / …)
          are removed entirely (their contents do not appear in output).
        * Inline formatting tags (strong / b / em / i / a / span) are
          unwrapped — their text content is preserved.
        * Block-level tags (p / h1-6 / li / div / blockquote / pre) each
          contribute one paragraph to the output, separated by blank lines.
        * ``<br>`` is treated as a paragraph break.
        * All whitespace runs (newlines, tabs, multiple spaces) inside a
          paragraph are collapsed to a single space. Leading and trailing
          whitespace per-paragraph and on the final output is stripped.

    Args:
        raw_html: The HTML string to clean. May be empty, ``None``, or not a
            string at all — all are treated as no-op inputs.

    Returns:
        Cleaned plain text. Always a string (never ``None``).
    """
    if not raw_html or not isinstance(raw_html, str):
        return ""

    from bs4 import BeautifulSoup, Comment, Tag

    parser = _select_parser()
    try:
        soup = BeautifulSoup(raw_html, parser)
    except Exception as exc:
        # Last-ditch: if even the parser falls over, return a whitespace-
        # normalised version of the raw input. Better than dropping the
        # article entirely.
        logger.warning("HTML cleaner parser failed (%s) — falling back to text-only", exc)
        return " ".join(raw_html.split())

    # 1. Strip out whole-subtree drop tags.
    for tag in soup.find_all(lambda t: isinstance(t, Tag) and t.name in _DROP_TAGS):
        tag.decompose()

    # 2. Strip comments (BeautifulSoup would otherwise keep their text).
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    # 3. Collect block-level elements in document order and emit each
    #    as one paragraph.
    paragraphs: list[str] = []
    seen: set[int] = set()
    for block in _iter_block_tags(soup):
        if id(block) in seen:
            continue
        seen.add(id(block))
        text = _clean_text(block)
        if text:
            paragraphs.append(text)

    # 4. No block-level tags at all: this is already plain text that happens
    #    to contain angle brackets (``<ENV_ID>``, ``营收 < 100 亿``).  Returning
    #    "" here used to destroy the article on a second cleaning pass —
    #    ``extract_text`` must be idempotent.
    if not paragraphs:
        return _clean_text(soup)

    # 5. Walk through and remove consecutive duplicate paragraphs
    #    (some 36kr entries repeat the headline in <p><strong>...</strong></p>
    #    and then again in the next <p>).
    deduped: list[str] = []
    for para in paragraphs:
        if not deduped or deduped[-1] != para:
            deduped.append(para)

    return "\n\n".join(deduped).strip()


__all__ = ["extract_text"]
