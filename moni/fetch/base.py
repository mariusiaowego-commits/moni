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

    # ── date-range filtering (set by run() before calling fetch()) ──────────

    date_from: str | None = None
    date_to: str | None = None

    # ── progress callback (v1.0.7.6) ────────────────────────────────────────
    # Optional callable invoked from inside fetch() to report progress.
    # Signature: cb(total_feeds=int|None, done_feeds=int|None,
    #               current_feed=str|None, articles_so_far=int|None) -> None
    # Routes set this so they can persist progress into fetch_jobs.

    progress_callback: Any = None

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

    def dedupe_insert(self, articles: list[dict[str, Any]], keyword_hits: list[dict[str, Any]] | None = None) -> int:
        """Insert articles into the DB, skipping duplicates.

        Args:
            articles: Normalised list of article dicts.
            keyword_hits: List of keyword hit dicts from apply_keyword_filters().

        Returns:
            Number of articles actually inserted (i.e. not duplicates).
        """
        from ..db.repository import ArticleRepository

        repo = ArticleRepository()
        return repo.dedupe_insert(articles, keyword_hits or ())

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
        self._dry_run = dry_run

        raw_articles: list[dict[str, Any]] = []
        try:
            raw_articles = self.fetch()
        except Exception as exc:
            logger.error("[%s] fetch() raised %s: %s", source, type(exc).__name__, exc)
            stats = _merge_stats(getattr(self, "_chunk_stats", {}), {
                "source": source,
                "fetched": 0,
                "parsed": 0,
                "inserted": 0,
                "parse_errors": 0,
                "error": f"{type(exc).__name__}: {exc}",
            })
            return stats

        logger.info("[%s] fetched %d raw articles", source, len(raw_articles))
        stats = self._ingest(raw_articles, dry_run=dry_run)

        # Streaming fetchers (e.g. BestBlogs) persist in chunks from inside
        # fetch(); merge their totals so the caller still gets one summary.
        stats = _merge_stats(getattr(self, "_chunk_stats", {}), stats)
        logger.info("[%s] done — inserted %d new articles", source, stats["inserted"])
        return stats

    def ingest_chunk(self, raw_articles: list[dict[str, Any]]) -> dict[str, Any]:
        """Run the shared parse/filter/insert pipeline on one chunk.

        Sources that sweep hundreds of feeds call this periodically so a
        process restart can't discard everything fetched so far.
        """
        stats = self._ingest(raw_articles, dry_run=bool(getattr(self, "_dry_run", False)))
        self._chunk_stats = _merge_stats(getattr(self, "_chunk_stats", {}), stats)
        logger.info(
            "[%s] chunk flushed — fetched %d, inserted %d new articles",
            self.source_name, stats["fetched"], stats["inserted"],
        )
        return stats

    def _ingest(self, raw_articles: list[dict[str, Any]], *, dry_run: bool) -> dict[str, Any]:
        source = self.source_name

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

        # ── HTML cleaner (v1.0.3) ────────────────────────────────────────
        # 36kr_rss 等源 raw_content 是 HTML (<h2>/<p>/<strong>). 清洗成纯文本
        # 让下游 keyword filter 和 LLM 摘要都看到干净内容. 已在 DB 的历史
        # 记录可由 `moni clean-articles` retroactively 跑一次.
        from .cleaner import extract_text
        cleaned_count = 0
        for article in parsed_articles:
            content = article.get("content")
            if isinstance(content, str) and content and "<" in content:
                cleaned = extract_text(content)
                if cleaned != content:
                    article["content"] = cleaned
                    cleaned_count += 1
        if cleaned_count:
            logger.info(
                "[%s] HTML cleaner: %d article(s) cleaned to plain text",
                source, cleaned_count,
            )

        # ── keyword filter (v1.0.2) ──────────────────────────────
        # 应用 source_keyword_filters (黑/白名单). 无 filter 时全保留.
        from .filters import apply_keyword_filters
        filtered_articles, filter_stats, keyword_hits = apply_keyword_filters(
            source, parsed_articles
        )
        if filter_stats["filters_applied"] > 0:
            logger.info(
                "[%s] keyword filter: %d input, %d kept, "
                "%d dropped_blacklist (%d global), %d dropped_whitelist (%d global) (%d filter(s) active, %d global)",
                source,
                filter_stats["input"],
                filter_stats["kept"],
                filter_stats["dropped_blacklist"],
                filter_stats.get("dropped_global_blacklist", 0),
                filter_stats["dropped_whitelist"],
                filter_stats.get("dropped_global_whitelist", 0),
                filter_stats["filters_applied"],
                filter_stats.get("global_filters_applied", 0),
            )

        # ── tag-keyword filter (v1.0.5.4) ────────────────────────────────
        # Apply AFTER source_keyword_filters so that source-level
        # blacklists (e.g. "drop all 36kr articles about 明星/游戏")
        # get a chance to fire first.  Articles that survive then
        # need to hit at least one of the source's tag-keywords to be
        # considered "in scope" for the user's tag taxonomy.  If no
        # tag carries keywords, this step is a pass-through.
        from .tag_filter import apply_tag_keyword_filter

        filtered_articles, tag_stats, tag_keyword_hits = apply_tag_keyword_filter(
            source, filtered_articles
        )
        # v1.0.5.20: 合并 tag_keyword_hits 到 keyword_hits
        keyword_hits.extend(tag_keyword_hits)
        if tag_stats["dropped_tag_filter"] > 0:
            logger.info(
                "[%s] tag-keyword filter: %d kept, "
                "%d dropped (%d tag-keyword(s) in OR set)",
                source,
                tag_stats["kept"],
                tag_stats["dropped_tag_filter"],
                tag_stats["tags_used"],
            )

        inserted = 0
        if filtered_articles and not dry_run:
            try:
                inserted = self.dedupe_insert(filtered_articles, keyword_hits)
            except Exception as exc:
                logger.error("[%s] dedupe_insert() raised %s: %s",
                             source, type(exc).__name__, exc)
        elif dry_run:
            inserted = 0
            logger.info("[%s] dry-run — skipped database insert", source)

        return {
            "source": source,
            "fetched": len(raw_articles),
            "parsed": len(parsed_articles),
            "inserted": inserted,
            "parse_errors": parse_errors,
            "cleaned": cleaned_count,
            "filtered": filter_stats["kept"],
            "dropped_blacklist": filter_stats["dropped_blacklist"],
            "dropped_whitelist": filter_stats["dropped_whitelist"],
            "dropped_global_blacklist": filter_stats.get("dropped_global_blacklist", 0),
            "dropped_global_whitelist": filter_stats.get("dropped_global_whitelist", 0),
            "global_filters_applied": filter_stats.get("global_filters_applied", 0),
            "filters_applied": filter_stats["filters_applied"],
            "dropped_tag_filter": tag_stats["dropped_tag_filter"],
            "tags_used": tag_stats["tags_used"],
            "dropped_articles": tag_stats.get("dropped_articles", []),
            "error": None,
        }


_SUM_STAT_FIELDS = (
    "fetched",
    "parsed",
    "inserted",
    "parse_errors",
    "cleaned",
    "filtered",
    "dropped_blacklist",
    "dropped_whitelist",
    "dropped_global_blacklist",
    "dropped_global_whitelist",
    "dropped_tag_filter",
)

# Counts of distinct filters/tags — identical for every chunk, so take max
# instead of summing (summing would multiply them by the chunk count).
_MAX_STAT_FIELDS = ("filters_applied", "global_filters_applied", "tags_used")


def _merge_stats(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    if not base:
        return dict(extra)
    merged = dict(extra)
    for field in _SUM_STAT_FIELDS:
        merged[field] = base.get(field, 0) + extra.get(field, 0)
    for field in _MAX_STAT_FIELDS:
        merged[field] = max(base.get(field, 0), extra.get(field, 0))
    merged["dropped_articles"] = list(base.get("dropped_articles", [])) + list(
        extra.get("dropped_articles", [])
    )
    merged["error"] = extra.get("error") or base.get("error")
    return merged
