"""The xwatch tag gate, in one place (v1.0.64).

Why this module exists
----------------------
xwatch has three ingestion paths and they all have to agree:

* ``FetcherXwatch`` — the scheduled batch scrape;
* ``moni xwatch ingest <id>`` — one tweet by id;
* ``moni xwatch following --ingest`` — the Following timeline.

Before v1.0.64 the tag gate was copy-pasted into the two CLI paths while
the batch path used ``apply_tag_keyword_filter``.  The v1.0.64 audit found
that fixing the match rules in two of the three places left
``xwatch following --ingest`` accepting exactly the "MASAJIN"/"Markdown"
articles the other two had just started rejecting: a tweet dropped by the
batch scrape could still be written by the CLI.

Sharing one function is the only version of this that stays fixed.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from .keyword_match import keyword_matches
from .tag_filter import _load_tag_keywords_for_source

SOURCE_NAME = "xwatch"


def load_gate_keywords(source: str = SOURCE_NAME) -> dict[str, list[str]]:
    """Bound tags with keywords for ``source`` (empty dict = no gate)."""
    return _load_tag_keywords_for_source(source)


class GateResult(NamedTuple):
    """Outcome of one gate evaluation.

    ``allowed`` exists because "no keywords configured" is a *pass-through*
    in ``apply_tag_keyword_filter`` (backwards compatibility: a tag system
    with no keywords must change nothing), whereas an empty
    ``matched_tags`` would read as "reject".  Collapsing those two into one
    signal is exactly how the three xwatch paths would drift apart again.
    """

    allowed: bool
    matched_tags: list[str]
    keyword_hits: list[dict[str, Any]]


def gate_article(
    art: dict[str, Any],
    tag_keywords: dict[str, list[str]],
    *,
    title_field: str | None = None,
) -> GateResult:
    """Apply the gate to one article.

    Mirrors ``apply_tag_keyword_filter`` exactly:

    * no bound tags carrying keywords → pass through (``allowed=True``);
    * otherwise keep only when at least one keyword matches.
    """
    if not tag_keywords:
        return GateResult(True, [], [])

    title = (art.get("title") or "").strip()
    content = (art.get("content") or "").strip()
    hit_field = title_field or "title+content"

    # Ordered set: one entry per tag that fired, not per keyword.
    matched_tags: dict[str, None] = {}
    keyword_hits: list[dict[str, Any]] = []
    for tag_name, kws in tag_keywords.items():
        for kw in kws:
            if keyword_matches(kw, title=title, body=content):
                matched_tags[tag_name] = None
                keyword_hits.append({
                    "article_url": art.get("url", ""),
                    "keyword": kw,
                    "tag_name": tag_name,
                    "filter_id": None,
                    "hit_field": hit_field,
                })
    tags = list(matched_tags)
    return GateResult(bool(tags), tags, keyword_hits)


def describe_gate(tag_keywords: dict[str, list[str]]) -> str:
    """Human-readable summary for the SKIP message."""
    sample = [w for ws in tag_keywords.values() for w in ws][:5]
    return (
        f"未命中 {SOURCE_NAME} 绑定 tag 的关键词 "
        f"(expected one of {sample}...)"
    )
