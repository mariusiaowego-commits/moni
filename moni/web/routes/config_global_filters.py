"""routes/config_global_filters — CRUD for global_keyword_filters (v1.0.40 / sprint-20 PR2).

Global filters are cross-source keyword rules that gate the fetch pipeline.
mode = 'whitelist' (must match to keep) or 'blacklist' (drop if match).
match_field = 'title' | 'content' | 'title+content'.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from moni.db.connection import get_db
from moni.web.exceptions import BadRequestError, NotFoundError
from moni.web.models import (
    GlobalKeywordFilter,
    GlobalKeywordFilterCreate,
    GlobalKeywordFilterUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config/global-filters", tags=["config"])

VALID_MODES = {"whitelist", "blacklist"}
VALID_MATCH_FIELDS = {"title", "content", "title+content"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_mode(mode: str) -> None:
    if mode not in VALID_MODES:
        raise BadRequestError(
            message=f"Invalid mode {mode!r}; must be one of {sorted(VALID_MODES)}",
            details={"mode": mode},
        )


def _validate_match_field(mf: str) -> None:
    if mf not in VALID_MATCH_FIELDS:
        raise BadRequestError(
            message=f"Invalid match_field {mf!r}; must be one of {sorted(VALID_MATCH_FIELDS)}",
            details={"match_field": mf},
        )


def _row_to_global_filter(r) -> GlobalKeywordFilter:
    """Convert a sqlite row to a GlobalKeywordFilter Pydantic model."""
    try:
        keywords = json.loads(r["keywords"]) if r["keywords"] else []
    except (TypeError, json.JSONDecodeError):
        logger.warning("[config_global_filters] row id=%d has invalid JSON keywords", r["id"])
        keywords = []
    if not isinstance(keywords, list):
        keywords = []
    return GlobalKeywordFilter(
        id=r["id"],
        mode=r["mode"],
        match_field=r["match_field"],
        keywords=[str(k) for k in keywords],
        is_active=bool(r["is_active"]),
        notes=r["notes"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


@router.get("", response_model=list[GlobalKeywordFilter])
def list_global_filters(
    include_inactive: bool = Query(False, description="Include inactive global filters"),
) -> list[GlobalKeywordFilter]:
    """List global keyword filters."""
    conn = get_db()
    where_sql = "" if include_inactive else "WHERE is_active = 1"
    rows = conn.execute(
        f"""
        SELECT id, mode, match_field, keywords,
               is_active, notes, created_at, updated_at
        FROM global_keyword_filters
        {where_sql}
        ORDER BY mode, id
        """
    ).fetchall()
    return [_row_to_global_filter(r) for r in rows]


@router.post("", response_model=GlobalKeywordFilter, status_code=201)
def create_global_filter(body: GlobalKeywordFilterCreate) -> GlobalKeywordFilter:
    """Create a new global keyword filter. Returns 400 if mode/match_field invalid or keywords empty."""
    _validate_mode(body.mode)
    _validate_match_field(body.match_field)
    clean_kws = [str(k).strip() for k in body.keywords if str(k).strip()]
    if not clean_kws:
        raise BadRequestError(message="keywords must be a non-empty list")
    conn = get_db()
    now = _now()
    keywords_json = json.dumps(clean_kws, ensure_ascii=False)
    cur = conn.execute(
        """
        INSERT INTO global_keyword_filters
            (mode, match_field, keywords,
             is_active, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (body.mode, body.match_field, keywords_json,
         1 if body.is_active else 0, body.notes, now, now),
    )
    conn.commit()
    return GlobalKeywordFilter(
        id=cur.lastrowid,
        mode=body.mode,
        match_field=body.match_field,
        keywords=clean_kws,
        is_active=body.is_active,
        notes=body.notes,
        created_at=now,
        updated_at=now,
    )


@router.get("/{filter_id}", response_model=GlobalKeywordFilter)
def get_global_filter(filter_id: int) -> GlobalKeywordFilter:
    conn = get_db()
    row = conn.execute(
        """
        SELECT id, mode, match_field, keywords,
               is_active, notes, created_at, updated_at
        FROM global_keyword_filters WHERE id = ?
        """,
        (filter_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Global filter id={filter_id} not found")
    return _row_to_global_filter(row)


@router.put("/{filter_id}", response_model=GlobalKeywordFilter)
def update_global_filter(filter_id: int, body: GlobalKeywordFilterUpdate) -> GlobalKeywordFilter:
    conn = get_db()
    row = conn.execute(
        """
        SELECT id, mode, match_field, keywords,
               is_active, notes, created_at, updated_at
        FROM global_keyword_filters WHERE id = ?
        """,
        (filter_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Global filter id={filter_id} not found")

    fields = dict(row)
    if body.mode is not None:
        _validate_mode(body.mode)
        fields["mode"] = body.mode
    if body.match_field is not None:
        _validate_match_field(body.match_field)
        fields["match_field"] = body.match_field
    if body.keywords is not None:
        clean_kws = [str(k).strip() for k in body.keywords if str(k).strip()]
        if not clean_kws:
            raise BadRequestError(message="keywords must be a non-empty list")
        fields["keywords"] = json.dumps(clean_kws, ensure_ascii=False)
    if body.is_active is not None:
        fields["is_active"] = 1 if body.is_active else 0
    if body.notes is not None:
        fields["notes"] = body.notes
    fields["updated_at"] = _now()

    conn.execute(
        """
        UPDATE global_keyword_filters SET
            mode = ?, match_field = ?, keywords = ?,
            is_active = ?, notes = ?, updated_at = ?
        WHERE id = ?
        """,
        (fields["mode"], fields["match_field"], fields["keywords"],
         fields["is_active"], fields["notes"], fields["updated_at"],
         filter_id),
    )
    conn.commit()
    return _row_to_global_filter(fields)


@router.delete("/{filter_id}", status_code=204)
def delete_global_filter(filter_id: int) -> None:
    conn = get_db()
    row = conn.execute("SELECT 1 FROM global_keyword_filters WHERE id = ?", (filter_id,)).fetchone()
    if row is None:
        raise NotFoundError(message=f"Global filter id={filter_id} not found")
    conn.execute("DELETE FROM global_keyword_filters WHERE id = ?", (filter_id,))
    conn.commit()
