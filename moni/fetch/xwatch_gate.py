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

from typing import Any

from .keyword_match import keyword_matches
from .tag_filter import _load_tag_keywords_for_source

SOURCE_NAME = "xwatch"


def load_gate_keywords(source: str = SOURCE_NAME) -> dict[str, list[str]]:
    """Bound tags with keywords for ``source`` (empty dict = no gate)."""
    return _load_tag_keywords_for_source(source)


def gate_article(
    art: dict[str, Any],
    tag_keywords: dict[str, list[str]],
    *,
    title_field: str | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Apply the gate to one article.

    Returns ``(matched_tag_names, keyword_hits)``.  An empty tag list means
    the article should not be stored.
    """
    title = (art.get("title") or "").strip()
    content = (art.get("content") or "").strip()
    hit_field = title_field or "title+content"

    matched_tags: list[str] = []
    keyword_hits: list[dict[str, Any]] = []
    for tag_name, kws in tag_keywords.items():
        for kw in kws:
            if keyword_matches(kw, title=title, body=content):
                matched_tags.append(tag_name)
                keyword_hits.append({
                    "article_url": art.get("url", ""),
                    "keyword": kw,
                    "tag_name": tag_name,
                    "filter_id": None,
                    "hit_field": hit_field,
                })
    return matched_tags, keyword_hits


def describe_gate(tag_keywords: dict[str, list[str]]) -> str:
    """Human-readable summary for the SKIP message."""
    sample = [w for ws in tag_keywords.values() for w in ws][:5]
    return (
        f"未命中 {SOURCE_NAME} 绑定 tag 的关键词 "
        f"(expected one of {sample}...)"
    )
