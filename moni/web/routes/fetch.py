"""routes/fetch — fetch source listing, config update, manual run and health.

Note: there is no ``fetch_sources`` DB table.  Source configs live in
``config/sources.yaml`` and are managed by ``moni.fetch.scheduler.Scheduler``.
This module lists sources from that YAML config and runs fetchers via
``moni.fetch.run(source_name)``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter

from moni.fetch import run as run_fetcher
from moni.web.exceptions import NotFoundError
from moni.web.models import FetchSource, FetchSourceUpdate, HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fetch", tags=["fetch"])

# Path to sources config — same resolution as moni.fetch.scheduler
_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "sources.yaml"


def _load_sources() -> list[dict[str, Any]]:
    """Load source configs from config/sources.yaml."""
    if not _CONFIG_PATH.exists():
        return []
    with _CONFIG_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data.get("sources", []) if isinstance(data, dict) else []


def _row_to_fetch_source(name: str, cfg: dict[str, Any]) -> FetchSource:
    now = datetime.now(timezone.utc).isoformat()
    return FetchSource(
        id=0,
        name=name,
        base_url=cfg.get("base_url", ""),
        crawler_type=cfg.get("crawler_type", "unknown"),
        is_active=cfg.get("enabled", True),
        config=cfg,
        created_at=now,
        updated_at=now,
    )


@router.get("/sources", response_model=list[FetchSource])
def list_fetch_sources() -> list[FetchSource]:
    """List all fetch sources defined in config/sources.yaml."""
    sources = _load_sources()
    return [_row_to_fetch_source(cfg.get("source_name", ""), cfg) for cfg in sources]


@router.put("/sources", response_model=list[FetchSource])
def update_fetch_sources(body: FetchSourceUpdate) -> list[FetchSource]:
    """Update mutable fields on sources in config/sources.yaml (batch patch).

    Only non-None fields are applied.  Changes are written back to the YAML file.
    """
    config_path = _CONFIG_PATH
    if not config_path.exists():
        return []

    with config_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    sources: list[dict[str, Any]] = data.get("sources", [])
    for cfg in sources:
        if body.name is not None:
            cfg["source_name"] = body.name
        if body.base_url is not None:
            cfg["base_url"] = body.base_url
        if body.crawler_type is not None:
            cfg["crawler_type"] = body.crawler_type
        if body.is_active is not None:
            cfg["enabled"] = body.is_active
        if body.config is not None:
            cfg.update(body.config)

    data["sources"] = sources
    with config_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)

    return [_row_to_fetch_source(cfg.get("source_name", ""), cfg) for cfg in sources]


@router.post("/sources/{name}/run")
def trigger_fetch_run(name: str) -> dict[str, Any]:
    """Trigger an immediate fetch for the named source.

    Calls ``moni.fetch.run(source_name)`` which checks the scheduler's
    should_fetch() gate before running.
    """
    sources = _load_sources()
    if not any(cfg.get("source_name") == name for cfg in sources):
        raise NotFoundError(message=f"Fetch source {name!r} not found")

    result: dict[str, Any] | None = run_fetcher(name)

    if result is None:
        return {
            "source": name,
            "status": "skipped",
            "message": "Source not due for fetching",
        }

    return result


@router.post("/sources/{name}/health", response_model=HealthResponse)
def check_source_health(name: str) -> HealthResponse:
    """Check the health / availability of a named fetch source."""
    sources = _load_sources()
    cfg = next((s for s in sources if s.get("source_name") == name), None)

    if cfg is None:
        raise NotFoundError(message=f"Fetch source {name!r} not found")

    from moni.db.connection import get_db

    db_connected = True
    try:
        conn = get_db()
        conn.execute("SELECT 1").fetchone()
    except Exception:
        db_connected = False

    source_ok = False
    base_url = cfg.get("base_url", "")
    if base_url:
        try:
            import urllib.request

            req = urllib.request.Request(
                base_url,
                method="HEAD",
                headers={"User-Agent": "moni/1.0"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                source_ok = 200 <= resp.status < 400
        except Exception:
            source_ok = False

    return HealthResponse(
        status="ok" if (source_ok and db_connected) else "degraded",
        version="1.0.0",
        db_connected=db_connected,
    )
