"""moni.fetch.run — CLI entry point for the fetch command.

python3 -m moni fetch [--source NAME] [--dry-run] [--health]

Usage::

    python3 -m moni fetch                      # fetch all enabled sources
    python3 -m moni fetch --source 36kr        # fetch only matching source(s)
    python3 -m moni fetch --dry-run             # fetch + parse, skip DB insert
    python3 -m moni fetch --health              # run health checks only, exit
    python3 -m moni fetch --source 36kr --dry-run  # combine flags
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from ..db.connection import close_db
from . import fetch_all, run as run_fetcher
from .base import _ensure_sources_loaded
from .scheduler import Scheduler
from .source_gate import split_fetchable

logger = logging.getLogger(__name__)


# ── argument parser ────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moni fetch",
        description="Fetch articles from one or all news sources.",
    )
    parser.add_argument(
        "--source",
        dest="source_name",
        metavar="NAME",
        help=(
            "Source name prefix to match (e.g. '36kr' matches '36kr_rss'). "
            "Omit to fetch all enabled sources."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse articles but skip database insertion.",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Run health checks only and print results; no fetch is performed.",
    )
    return parser


# ── dry-run support ─────────────────────────────────────────────────────────────

def _prefix_match(name: str, prefix: str) -> bool:
    """Return True if name starts with prefix (case-insensitive)."""
    return name.lower().startswith(prefix.lower())


def _filter_sources(sources: list[dict], prefix: str) -> list[dict]:
    """Return source configs whose source_name starts with prefix."""
    return [cfg for cfg in sources if _prefix_match(cfg.get("source_name", ""), prefix)]


def _collect_results(
    source_names: list[str],
    dry_run: bool = False,
    force_targeted: bool = False,
) -> tuple[list[dict], int, int]:
    """Run fetchers for source_names, respecting scheduler.should_fetch().

    When ``force_targeted`` is True the caller passed --source on the CLI,
    so the per-source scheduler gate is bypassed (the user is explicitly
    asking for this source right now).  When False (the bulk path, no
    --source), the gate is honoured so a 5-minute source is not hammered.

    Returns (results, total_fetched, total_skipped).
    """
    _ensure_sources_loaded()
    scheduler = Scheduler()
    results: list[dict] = []
    total_fetched = 0
    total_skipped = 0

    for name in source_names:
        # Check scheduler gate (skip if not due), unless it's a targeted
        # fetch (caller passed --source) where we always run and let the
        # fetcher itself decide.  ``force_targeted`` is True when the user
        # passed --source; the bulk path (no --source) still honours the
        # gate so we don't hammer a 5-min source every minute.
        if not force_targeted and not scheduler.should_fetch(name):
            logger.info("[%s] skipped — not due for fetching", name)
            total_skipped += 1
            continue

        result = run_fetcher(name, force=True, dry_run=dry_run)
        if result is None:
            logger.warning("[%s] no fetcher registered or fetch failed", name)
            total_skipped += 1
            continue

        results.append(result)
        total_fetched += result.get("fetched", 0)

    return results, total_fetched, total_skipped


# ── main ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --health takes priority: run checks and exit immediately
    if args.health:
        from .health import check_all_sequential
        results = check_all_sequential()
        if not results:
            print("No sources configured.")
            return 0

        for r in results:
            status_icon = "✓" if r.ok else "✗"
            url = r.url
            status_code = r.status_code
            latency_ms = r.latency_ms
            error = r.error or ""
            source = r.source
            print(f"  {status_icon} [{source}] {url}  status={status_code}  latency={latency_ms}ms"
                  + (f"  ERROR: {error}" if error else ""))
            # v1.0.12: print xwatch multi-probe detail compactly
            if getattr(r, "detail", None) and source == "xwatch":
                d = r.detail
                synd = d.get("syndication") or {}
                ddg = d.get("ddg") or {}
                pas = d.get("passive") or {}
                print(
                    f"      synd={'ok' if synd.get('ok') else 'FAIL'}"
                    f"({synd.get('latency_ms')}ms)"
                    f"  ddg={ddg.get('status', '?')}"
                    f"(urls={ddg.get('urls_found', '-')})"
                    f"  accounts={pas.get('accounts', '-')}"
                    f"  queries={pas.get('queries', '-')}"
                    f"  arts={pas.get('article_count', '-')}"
                    f"  48h={pas.get('articles_last_48h', '-')}"
                )
        return 0

    # Normal fetch path
    scheduler = Scheduler()
    all_sources = list(scheduler.enabled_sources())

    if args.source_name:
        matched = _filter_sources(all_sources, args.source_name)
        if not matched:
            print(f"  ERROR: no source matches prefix {args.source_name!r}")
            return 1
        source_names = [cfg["source_name"] for cfg in matched]
        source_label = f"source(s) matching {args.source_name!r}"
    else:
        # 批量路径排除 batch_enabled: false 的源 (bank_feed 会拉起 agent,
        # 不跟着全量抓取跑)。显式 --source 时才包含。
        batch_cfgs, skipped_sources = split_fetchable(
            list(scheduler.batch_sources())
        )
        for source_name, reason in skipped_sources:
            print(
                f"  SKIP {source_name} — "
                + ("信源已停用" if reason == "inactive" else "信源已隐藏")
            )
        source_names = [cfg["source_name"] for cfg in batch_cfgs]
        source_label = "all batch-enabled sources"

    if not source_names:
        print("  No enabled sources found.")
        return 0

    print(f"Fetching {source_label} ...")
    t0 = time.monotonic()

    results, total_fetched, total_skipped = _collect_results(
        source_names, dry_run=args.dry_run, force_targeted=bool(args.source_name)
    )

    elapsed = time.monotonic() - t0

    # Print per-source summary
    for result in results:
        err = f"  ERROR: {result['error']}" if result.get("error") else ""
        warn = f"  WARN: {result['warning']}" if result.get("warning") else ""
        dry_tag = " [dry-run]" if args.dry_run else ""
        print(
            f"  [{result.get('source')}]{dry_tag}  "
            f"fetched={result.get('fetched', 0)}  parsed={result.get('parsed', 0)}  "
            f"inserted={result.get('inserted', 0)}  "
            f"parse_errors={result.get('parse_errors', 0)}"
            + (f"  {err}" if err else "")
            + warn
        )

    n_sources_ran = len(results)
    n_inserted = sum(r.get("inserted", 0) for r in results)

    if args.dry_run:
        print(
            f"\nSummary: {total_fetched} articles fetched from {n_sources_ran} source(s), "
            f"{total_skipped} skipped. (dry-run — no DB inserts)"
        )
    else:
        print(
            f"\nSummary: {n_inserted} new article(s) inserted from {n_sources_ran} source(s), "
            f"{total_skipped} skipped, {total_fetched} total fetched. "
            f"Elapsed: {elapsed:.1f}s."
        )

    failed = [r for r in results if r.get("error")]
    if failed:
        # 退出码是唯一能让 shell / cron / CI 看见失败的信号。
        names = ", ".join(str(r.get("source")) for r in failed[:5])
        print(f"\nFAILED: {len(failed)} source(s) — {names}")
        return 1

    partial = [r for r in results if r.get("warning")]
    if partial:
        names = ", ".join(str(r.get("source")) for r in partial[:5])
        print(f"\nPARTIAL: {len(partial)} source(s) — {names}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
