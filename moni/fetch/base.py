"""Base fetcher — abstract foundation for all source fetchers.

Subclassing checklist
---------------------
Every concrete fetcher must:

1. Define ``source_name: str = "my_source"`` as a class attribute.

2. Implement::

       def fetch(self) -> list[dict]:
           ...  # return raw article dicts from the source

3. Implement ``parse(self, raw: dict) -> dict | None``::

       def parse(self, raw: dict) -> dict | None:
           # return {"title", "url", "source", "published_at", "content"}
           # or None to skip the article

4. Call ``self._register()`` during init if you want self-registration, OR
   explicitly call ``register_fetcher(self.__class__)`` after class definition.

The ``run()`` method executes the full pipeline:
    fetch() → parse() → dedupe_insert()

Parse errors are swallowed per-article (log warning + skip), never aborting
the entire fetch run.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Global registry: source_name (str) → fetcher class
_FETCHER_REGISTRY: dict[str, type] = {}


def register_fetcher(cls: type) -> type:
    """Register a BaseFetcher subclass so it can be found by source_name."""
    _FETCHER_REGISTRY[cls.source_name] = cls
    return cls


def list_fetchers() -> dict[str, type]:
    """Return a copy of the current fetcher registry."""
    _ensure_sources_loaded()
    return _FETCHER_REGISTRY.copy()


def _ensure_sources_loaded() -> None:
    """Lazily import source modules so they self-register.

    Called on first access to the registry to avoid a circular import:
    base.py → sources/__init__.py → sources/kr_rss.py → base.py
    """
    if _FETCHER_REGISTRY:
        return  # already populated
    try:
        # noqa: F401 — side-effect only: runs @register_fetcher on each module
        from . import sources as _sources  # noqa: F401,F811
    except Exception as exc:
        logger.warning("moni.fetch.sources auto-import failed: %s", exc)


class BaseFetcher(ABC):
    """Abstract fetcher for a single news source.

    The ``run()`` method executes the full pipeline:
        fetch() → parse() → dedupe_insert()
    """

    # ── identity (subclasses MUST define source_name as a class attribute) ───

    source_name: str = ""

    def _register(self) -> None:
        """Register this class in the global fetcher registry."""
        _FETCHER_REGISTRY[self.source_name] = type(self)

    # ── abstract fetch/parse ──────────────────────────────────────────────────

    @abstractmethod
    def fetch(self) -> list[dict[str, Any]]:
        """Return raw articles from the source.

        Returns:
            List of raw article dicts. Format is source-specific;
            ``parse()`` normalises them.
        """
        ...  # pragma: no cover

    @abstractmethod
    def parse(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Normalise a single raw article into the canonical schema.

        Canonical schema: {title, url, source, published_at, content}

        Args:
            raw: Source-specific raw article dict.

        Returns:
            Normalised dict, or ``None`` to signal the article should be skipped.
        """
        ...  # pragma: no cover

    # ── dedupe ─────────────────────────────────────────────────────────────

    def dedupe_insert(self, articles: list[dict[str, Any]]) -> int:
        """Insert articles into the DB, skipping duplicates.

        Args:
            articles: Normalised list of article dicts.

        Returns:
            Number of articles actually inserted (i.e. not duplicates).
        """
        from ..db.repository import ArticleRepository

        repo = ArticleRepository()
        return repo.dedupe_insert(articles)

    # ── run ─────────────────────────────────────────────────────────────────

    def run(self, dry_run: bool = False) -> dict[str, Any]:
        """Execute the full fetch pipeline.

        Fetch → Parse → Dedupe-insert (unless dry_run=True).
        Parse failures are logged per-article and do not stop the run.

        Args:
            dry_run: If True, skip the database insert step.

        Returns:
            Summary dict with keys: source, fetched, parsed, inserted, parse_errors.
        """
        source = self.source_name
        logger.info("[%s] starting fetch", source)

        raw_articles: list[dict[str, Any]] = []
        try:
            raw_articles = self.fetch()
        except Exception as exc:
            logger.error("[%s] fetch() raised %s: %s", source, type(exc).__name__, exc)
            return {
                "source": source,
                "fetched": 0,
                "parsed": 0,
                "inserted": 0,
                "parse_errors": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }

        logger.info("[%s] fetched %d raw articles", source, len(raw_articles))

        parsed_articles: list[dict[str, Any]] = []
        parse_errors = 0

        for raw in raw_articles:
            try:
                normalised = self.parse(raw)
                if normalised is not None:
                    normalised.setdefault("source", source)
                    normalised.setdefault("published_at", None)
                    parsed_articles.append(normalised)
            except Exception as exc:
                parse_errors += 1
                logger.warning(
                    "[%s] parse() error for article %s: %s — skipping",
                    source,
                    raw.get("url", raw.get("title", "<unknown>")),
                    exc,
                )

        logger.info("[%s] parsed %d/%d articles (%d parse errors)",
                    source, len(parsed_articles), len(raw_articles), parse_errors)

        inserted = 0
        if parsed_articles and not dry_run:
            try:
                inserted = self.dedupe_insert(parsed_articles)
            except Exception as exc:
                logger.error("[%s] dedupe_insert() raised %s: %s",
                             source, type(exc).__name__, exc)
        elif dry_run:
            inserted = 0
            logger.info("[%s] dry-run — skipped database insert", source)

        logger.info("[%s] done — inserted %d new articles", source, inserted)

        return {
            "source": source,
            "fetched": len(raw_articles),
            "parsed": len(parsed_articles),
            "inserted": inserted,
            "parse_errors": parse_errors,
            "error": None,
        }
