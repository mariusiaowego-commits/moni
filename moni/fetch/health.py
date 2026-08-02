"""Health checks for fetch sources.

Runs HEAD/GET requests against configured feed_url values and reports
availability, response code, and latency.
"""

from __future__ import annotations

import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "sources.yaml"


@dataclass
class HealthResult:
    source: str
    url: str
    ok: bool
    status_code: int | None
    latency_ms: float | None
    error: str | None


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


def check_all_sequential(sources: list[dict] | None = None) -> list[HealthResult]:
    """Run health checks for all enabled sources sequentially.

    v1.0.11: skip sources with no real ``feed_url`` entirely (return empty
    result list) instead of emitting a "no URL configured" stub.  xwatch
    intentionally has an empty feed_url — its health is better measured
    by the cron run log than by an HTTP probe of a URL that doesn't exist.
    """
    if sources is None:
        sources = _load_sources()

    results: list[HealthResult] = []
    for cfg in sources:
        feed_url = (cfg.get("feed_url") or "").strip()
        if not feed_url:
            # Silent skip — no health check for sources without a real
            # feed URL (wechat_rss_a/b tokens live in url, xwatch has none).
            continue
        name = cfg.get("source_name", "?")
        results.append(async_check(name, feed_url))

    return results


def _mono_ms() -> float:
    """Monotonic time in milliseconds (cross-platform)."""
    import time
    return time.monotonic() * 1000
