"""moni.fetch — pluggable news-source fetch pipeline.

Public API::

    from moni.fetch import fetch_all, run

    # Fetch and persist all enabled sources due for fetching
    results = fetch_all()

    # Run a specific fetcher by source name (must be imported explicitly)
    result = run("36kr_rss")

The ``sources`` sub-package holds individual fetcher implementations.

``config/sources.yaml`` defines all known sources and their intervals.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import _ensure_sources_loaded, _FETCHER_REGISTRY, BaseFetcher, list_fetchers
from .scheduler import Scheduler

# Import-time source discovery — after BaseFetcher is defined to avoid circular import
try:
    from . import sources  # noqa: F401
except Exception as exc:
    import logging
    logging.getLogger(__name__).warning("moni.fetch.sources auto-import failed: %s", exc)

logger = logging.getLogger(__name__)


def run(source_name: str, force: bool = False) -> dict[str, Any] | None:
    """Instantiate and run the fetcher registered for ``source_name``.

    Args:
        source_name: Unique source identifier as defined in ``config/sources.yaml``.
        force: If True, bypass the scheduler's ``should_fetch()`` check.
               Use when a user explicitly requests a specific source.

    Returns:
        Run result dict (same shape as ``BaseFetcher.run()``), or ``None`` if
        ``source_name`` has no registered fetcher.
    """
    _ensure_sources_loaded()
    if source_name not in _FETCHER_REGISTRY:
        logger.error("No fetcher registered for source %r", source_name)
        return None

    scheduler = Scheduler()
    if not force and not scheduler.should_fetch(source_name):
        logger.info("Source %r not due", source_name)
        return None

    fetcher_cls = _FETCHER_REGISTRY[source_name]
    fetcher = fetcher_cls()
    result = fetcher.run()
    if force or source_name in scheduler.last_run:
        scheduler.mark_fetched(source_name)
    return result


def fetch_all() -> list[dict[str, Any]]:
    """Fetch all enabled sources that are due for fetching.

    Reads ``config/sources.yaml``, checks ``should_fetch()`` for each source,
    runs eligible fetchers, and records last-run timestamps.

    Returns:
        List of run result dicts (one per fetcher that ran).
        Sources skipped because they are not due are absent from the list.
    """
    # Ensure all source modules have been imported and self-registered
    _ensure_sources_loaded()

    scheduler = Scheduler()
    results: list[dict[str, Any]] = []

    for cfg in scheduler.enabled_sources():
        name = cfg["source_name"]
        if not scheduler.should_fetch(name):
            logger.debug("Skipping %r — not due", name)
            continue

        if name not in _FETCHER_REGISTRY:
            logger.warning("No fetcher registered for %r — skipping", name)
            continue

        fetcher_cls = _FETCHER_REGISTRY[name]
        fetcher = fetcher_cls()
        try:
            result = fetcher.run()
        except Exception as exc:
            logger.error("[%s] Unhandled exception: %s", name, exc)
            result = {
                "source": name,
                "fetched": 0,
                "parsed": 0,
                "inserted": 0,
                "parse_errors": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }

        scheduler.mark_fetched(name)
        results.append(result)

    return results
