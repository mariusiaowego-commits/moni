"""Health checks for fetch sources.

Runs HEAD/GET requests against configured feed_url values and reports
availability, response code, and latency.

v1.0.12: xwatch has no feed_url — uses a dedicated dual-probe check
(syndication + DDG + passive DB metrics) instead of silent skip.
"""

from __future__ import annotations

import logging
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "sources.yaml"

# Well-known public tweet (Jack's first tweet id=20) — stable syndication probe.
_SYND_PROBE_TWEET_ID = "20"


@dataclass
class HealthResult:
    source: str
    url: str
    ok: bool
    status_code: int | None
    latency_ms: float | None
    error: str | None
    # v1.0.12 optional detail blob (xwatch multi-probe, etc.)
    detail: dict[str, Any] = field(default_factory=dict)


def _load_sources() -> list[dict]:
    if not _CONFIG_PATH.exists():
        return []
    with _CONFIG_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data.get("sources", []) if isinstance(data, dict) else []


def async_check(source: str, url: str, timeout: float = 8.0) -> HealthResult:
    """Perform a HEAD request against ``url`` and return a HealthResult."""
    try:
        req = urllib.request.Request(
            url,
            method="HEAD",
            headers={"User-Agent": "moni-healthcheck/1.0"},
        )
        t0 = _mono_ms()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed_ms = _mono_ms() - t0
            return HealthResult(
                source=source,
                url=url,
                ok=200 <= resp.status < 400,
                status_code=resp.status,
                latency_ms=round(elapsed_ms, 1),
                error=None,
            )
    except Exception as exc:
        return HealthResult(
            source=source,
            url=url,
            ok=False,
            status_code=None,
            latency_ms=None,
            error=str(exc),
        )


def check_xwatch(*, probe_ddg: bool = True, probe_synd: bool = True) -> HealthResult:
    """xwatch-specific health: syndication + optional DDG + passive metrics.

    Status semantics
    ----------------
    * ``ok=True``  — syndication works (content path healthy).  DDG may
      still be degraded; that is reported in ``detail.ddg`` without
      failing the whole check, because accounts/RSS can produce without DDG.
    * ``ok=False`` — syndication is down (cannot fetch tweet body).
    """
    detail: dict[str, Any] = {
        "channels": [
            "following+cdp",
            "accounts+rss",
            "queries+ddg",
            "manual_ingest",
        ],
    }
    # Channel C readiness: Profile 6 / 9222
    try:
        from .xwatch_following import X_CHROME_PORT, _port_alive
        detail["following_chrome"] = {
            "port": X_CHROME_PORT,
            "alive": _port_alive(),
        }
    except Exception as exc:
        detail["following_chrome"] = {"error": str(exc)}
    synd_ok = False
    synd_latency: float | None = None
    synd_error: str | None = None

    if probe_synd:
        t0 = time.monotonic()
        try:
            from .xwatch_syndication import fetch_tweet
            data = fetch_tweet(_SYND_PROBE_TWEET_ID, timeout=12)
            synd_latency = round((time.monotonic() - t0) * 1000, 1)
            synd_ok = bool(data and (data.get("id_str") or data.get("text")))
            if not synd_ok:
                synd_error = "syndication returned empty/None"
        except Exception as exc:
            synd_latency = round((time.monotonic() - t0) * 1000, 1)
            synd_error = str(exc)
            synd_ok = False
        detail["syndication"] = {
            "ok": synd_ok,
            "latency_ms": synd_latency,
            "probe_id": _SYND_PROBE_TWEET_ID,
            "error": synd_error,
        }

    ddg_info: dict[str, Any] = {}
    if probe_ddg:
        try:
            from .xwatch_discovery import (
                ddg_circuit_status,
                reset_ddg_circuit,  # noqa: F401 — not used here
                search_tweet_urls,
            )
            circ = ddg_circuit_status()
            ddg_info["circuit"] = circ
            if circ.get("open"):
                ddg_info["ok"] = False
                ddg_info["status"] = "circuit_open"
                ddg_info["note"] = (
                    f"circuit open, {circ.get('remaining_sec')}s remaining — "
                    "accounts/RSS channel still available"
                )
            else:
                t0 = time.monotonic()
                # Tiny probe; may open circuit on anomaly — acceptable for health.
                urls = search_tweet_urls(
                    "微信小程序",
                    max_results=3,
                    timeout=12,
                    circuit_minutes=30.0,
                )
                latency = round((time.monotonic() - t0) * 1000, 1)
                circ_after = ddg_circuit_status()
                ddg_info["circuit"] = circ_after
                ddg_info["latency_ms"] = latency
                ddg_info["urls_found"] = len(urls)
                ddg_info["ok"] = len(urls) > 0
                ddg_info["status"] = (
                    "ok" if urls else circ_after.get("last_status", "empty")
                )
        except Exception as exc:
            ddg_info = {"ok": False, "status": "error", "error": str(exc)}
        detail["ddg"] = ddg_info

    # Passive: config + recent insert activity
    passive: dict[str, Any] = {}
    try:
        from ..db.connection import get_db
        conn = get_db()
        row = conn.execute(
            "SELECT fetch_config, is_active FROM sources WHERE name = 'xwatch'",
        ).fetchone()
        if row:
            import json
            cfg = {}
            if row["fetch_config"]:
                try:
                    cfg = json.loads(row["fetch_config"]) or {}
                except Exception:
                    cfg = {}
            passive["is_active"] = bool(row["is_active"])
            passive["accounts"] = len(cfg.get("accounts") or [])
            passive["queries"] = len(cfg.get("queries") or [])
        else:
            passive["is_active"] = False
            passive["error"] = "xwatch source row missing — run moni db seed-config"

        art = conn.execute(
            """
            SELECT COUNT(*) AS n,
                   MAX(fetched_at) AS last_fetch
            FROM articles WHERE source = 'xwatch'
            """,
        ).fetchone()
        passive["article_count"] = int(art["n"]) if art else 0
        passive["last_fetched_at"] = art["last_fetch"] if art else None

        # Articles in last 48h
        recent = conn.execute(
            """
            SELECT COUNT(*) AS n FROM articles
            WHERE source = 'xwatch'
              AND fetched_at >= datetime('now', '-48 hours')
            """,
        ).fetchone()
        passive["articles_last_48h"] = int(recent["n"]) if recent else 0

        job = conn.execute(
            """
            SELECT status, articles_so_far, started_at, finished_at, error,
                   substr(result_json, 1, 200) AS result_preview
            FROM fetch_jobs WHERE source = 'xwatch'
            ORDER BY started_at DESC LIMIT 1
            """,
        ).fetchone()
        if job:
            passive["last_job"] = {
                "status": job["status"],
                "articles_so_far": job["articles_so_far"],
                "started_at": job["started_at"],
                "finished_at": job["finished_at"],
                "error": job["error"],
            }
    except Exception as exc:
        passive["error"] = str(exc)
    detail["passive"] = passive

    # Overall: syndication is the content path — must be ok.
    # Warn in error string if DDG/passive look bad.
    warnings: list[str] = []
    if not synd_ok:
        err = synd_error or "syndication probe failed"
    else:
        err = None
        if ddg_info and not ddg_info.get("ok"):
            warnings.append(
                f"ddg={ddg_info.get('status', 'degraded')}"
            )
        if passive.get("articles_last_48h", 0) == 0 and passive.get("article_count", 0) >= 0:
            # Only warn if source is active and we expected inserts
            if passive.get("is_active", True):
                warnings.append("no inserts in 48h")
        if warnings:
            err = "ok with warnings: " + ", ".join(warnings)

    return HealthResult(
        source="xwatch",
        url="syndication+ddg+accounts",
        ok=synd_ok,
        status_code=200 if synd_ok else None,
        latency_ms=synd_latency,
        error=err,
        detail=detail,
    )


def check_all_sequential(sources: list[dict] | None = None) -> list[HealthResult]:
    """Run health checks for all enabled sources sequentially.

    v1.0.11: skip sources with no real ``feed_url`` (except xwatch).
    v1.0.12: xwatch gets ``check_xwatch()`` instead of silent skip.
    wechat_rss_a/b still skip (token URLs live in DB ``url``, not feed_url).
    """
    if sources is None:
        sources = _load_sources()

    results: list[HealthResult] = []
    for cfg in sources:
        name = cfg.get("source_name", "?")
        feed_url = (cfg.get("feed_url") or "").strip()

        if name == "xwatch" or cfg.get("type") == "x":
            results.append(check_xwatch())
            continue

        if not feed_url:
            # Silent skip — wechat_rss etc.
            continue
        results.append(async_check(name, feed_url))

    return results


def _mono_ms() -> float:
    """Monotonic time in milliseconds (cross-platform)."""
    return time.monotonic() * 1000
