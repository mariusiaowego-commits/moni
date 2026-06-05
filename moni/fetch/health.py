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
    """Run health checks for all enabled sources sequentially."""
    if sources is None:
        sources = _load_sources()

    results: list[HealthResult] = []
    for cfg in sources:
        name = cfg.get("source_name", "?")
        url = cfg.get("feed_url") or cfg.get("api_url") or cfg.get("base_url", "")
        if not url:
            results.append(HealthResult(
                source=name,
                url="",
                ok=False,
                status_code=None,
                latency_ms=None,
                error="no URL configured",
            ))
            continue

        results.append(async_check(name, url))

    return results


def _mono_ms() -> float:
    """Monotonic time in milliseconds (cross-platform)."""
    import time
    return time.monotonic() * 1000
