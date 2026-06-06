"""routes/fetch — fetch source listing, config update, manual run and health.

Note: there is no ``fetch_sources`` DB table.  Source configs live in
``config/sources.yaml`` and are managed by ``moni.fetch.scheduler.Scheduler``.
This module lists sources from that YAML config and runs fetchers via
``moni.fetch.run(source_name)``.

v1.0.5: the route URLs accept BOTH the DB-style source id
(``src-36kr-rss``, what the SPA sends) and the YAML-style source name
(``36kr_rss``, what the CLI uses).  The frontend SPA loaded the source
list from ``/api/config/sources`` (DB id) but then POSTed to
``/api/fetch/sources/{id}/run`` and ``/ping``, where the backend was
looking up by ``source_name`` — so the user got 404 "Fetch source
'src-36kr-rss' not found" and 405 "Method Not Allowed" on /ping.
This module now resolves either form.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter

from moni.db.connection import get_db
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


def _resolve_source_name(ref: str) -> str | None:
    """Resolve a route param to the YAML ``source_name``.

    Accepts:
      * YAML ``source_name`` directly (``36kr_rss``, ``mpaypass`` …)
      * DB source ``id`` (``src-36kr-rss``, ``src-mpaypass`` …), looked
        up via ``sources.name`` in the DB

    Returns the YAML ``source_name`` that ``moni.fetch.run`` expects, or
    None if neither form matches.
    """
    yaml_sources = _load_sources()
    yaml_names = {cfg.get("source_name", "") for cfg in yaml_sources}
    if ref in yaml_names:
        return ref
    # Try the DB mapping: src-36kr-rss -> 36kr_rss (sources.name)
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT name FROM sources WHERE id = ?", (ref,)
        ).fetchone()
        if row and row["name"] in yaml_names:
            return row["name"]
    except Exception:
        # DB unavailable — fall through
        pass
    return None


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

    v1.0.5: ``name`` can be either the YAML ``source_name`` (e.g.
    ``36kr_rss``) or the DB source id (e.g. ``src-36kr-rss``) — see
    ``_resolve_source_name``.

    Calls ``moni.fetch.run(source_name)`` which checks the scheduler's
    should_fetch() gate before running.
    """
    resolved = _resolve_source_name(name)
    if resolved is None:
        raise NotFoundError(message=f"Fetch source {name!r} not found")

    result: dict[str, Any] | None = run_fetcher(resolved)

    if result is None:
        return {
            "source": resolved,
            "status": "skipped",
            "message": "Source not due for fetching",
        }

    return result


@router.post("/sources/{name}/health", response_model=HealthResponse)
def check_source_health(name: str) -> HealthResponse:
    """Check the health / availability of a named fetch source.

    v1.0.5: ``name`` can be either the YAML ``source_name`` or the DB
    source id (see ``_resolve_source_name``).
    """
    resolved = _resolve_source_name(name)
    if resolved is None:
        raise NotFoundError(message=f"Fetch source {name!r} not found")

    sources = _load_sources()
    cfg = next((s for s in sources if s.get("source_name") == resolved), None)

    db_connected = True
    try:
        conn = get_db()
        conn.execute("SELECT 1").fetchone()
    except Exception:
        db_connected = False

    source_ok = False
    base_url = (cfg or {}).get("base_url", "")
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


# v1.0.5 alias: the SPA POSTs to ``/api/fetch/sources/{name}/ping`` (the
# intuitive verb), but the canonical name on this module is ``/health``.
# Wire the alias to the same handler so both work without touching the
# frontend.  Define it AFTER the canonical route so FastAPI registers
# the alias after the primary path (avoids matching surprises).
@router.post("/sources/{name}/ping", response_model=HealthResponse)
def ping_source_health(name: str) -> HealthResponse:
    """Alias of ``/sources/{name}/health`` for the SPA's ``pingSource()``."""
    return check_source_health(name)
