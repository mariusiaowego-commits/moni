"""Immutable article snapshot handed to the scoring functions.

Scoring code never touches ``sqlite3.Row`` or the DB directly: the caller
normalises a row once and everything downstream is a pure function of this
dataclass.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


@dataclass(frozen=True)
class ArticleSnapshot:
    id: str
    title: str = ""
    raw_content: str = ""
    publisher: str = ""
    published_at: str = ""
    source: str = ""
    ai_summary: str = ""
    fact_check_conclusion: str = ""
    url: str = ""

    @property
    def body(self) -> str:
        return self.raw_content.strip()

    @property
    def body_len(self) -> int:
        return len(self.body)


def from_row(row: Mapping[str, Any] | Any) -> ArticleSnapshot:
    """Build a snapshot from a sqlite3.Row, a dict, or any mapping.

    Missing keys degrade to empty strings so pre-migration rows still score.
    """

    def get(key: str) -> str:
        try:
            return _text(row[key])
        except (KeyError, IndexError, TypeError):
            return ""

    return ArticleSnapshot(
        id=get("id"),
        title=get("title"),
        raw_content=get("raw_content"),
        publisher=get("publisher"),
        published_at=get("published_at"),
        source=get("source"),
        ai_summary=get("ai_summary"),
        fact_check_conclusion=get("fact_check_conclusion"),
        url=get("url"),
    )
