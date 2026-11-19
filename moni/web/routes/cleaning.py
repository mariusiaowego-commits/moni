"""Content cleaning API (sprint-55).

Preview → apply is the only path that writes article bodies.  Every apply is
recorded in ``cleaning_runs`` and exports a Markdown report for outside use.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Query
from fastapi.responses import PlainTextResponse

from moni.cleaning.service import (
    CONTRACTS,
    apply_cleaning_run,
    audit,
    list_runs,
    load_run,
    overview,
    preview,
    report_markdown,
)
from moni.db.connection import get_db
from moni.web.exceptions import BadRequestError, NotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cleaning", tags=["cleaning"])


def _get_run(run_id: str) -> dict[str, Any]:
    try:
        return load_run(run_id)
    except KeyError:
        raise NotFoundError(message=f"Cleaning run {run_id!r} not found")


@router.get("/overview")
def get_overview() -> dict[str, Any]:
    return overview()


@router.post("/preview")
def post_preview() -> dict[str, Any]:
    """Scan every body and report what would change.  Writes no article."""
    return preview()


@router.post("/apply")
def post_apply() -> dict[str, Any]:
    """Re-scan and write.  Idempotent: a second call reports changed=0."""
    return apply_cleaning_run()


@router.get("/runs")
def get_runs(limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
    return list_runs(limit)


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return _get_run(run_id)


@router.get("/runs/{run_id}/report.md", response_class=PlainTextResponse)
def get_report(run_id: str) -> PlainTextResponse:
    run = _get_run(run_id)
    return PlainTextResponse(
        report_markdown(run),
        media_type="text/markdown; charset=utf-8",
    )


@router.get("/sources")
def list_sources() -> list[dict[str, Any]]:
    return audit()["sources"]


@router.put("/sources/{name}/contract")
def set_contract(name: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Set how a source is expected to deliver content.

    ``title_only`` is the compliance escape hatch: empty bodies are expected
    there and must not be reported as a fetch failure.
    """
    contract = str(body.get("contract") or "")
    if contract not in CONTRACTS:
        raise BadRequestError(
            message=f"Unknown contract {contract!r}",
            details={"allowed": list(CONTRACTS)},
        )
    conn = get_db()
    row = conn.execute("SELECT name FROM sources WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise NotFoundError(message=f"Source {name!r} not found")
    conn.execute(
        "UPDATE sources SET content_contract = ?, updated_at = ? WHERE name = ?",
        (contract, datetime.now(timezone.utc).isoformat(), name),
    )
    conn.commit()
    return {"source": name, "contract": contract}
