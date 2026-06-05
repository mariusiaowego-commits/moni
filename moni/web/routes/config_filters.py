"""routes/config_filters — CRUD for source_keyword_filters (v1.0.2).

Filters are source-scoped keyword lists that gate the fetch pipeline.
mode = 'whitelist' (only keep if match) or 'blacklist' (drop if match).
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
    KeywordFilter,
    KeywordFilterCreate,
    KeywordFilterUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config/filters", tags=["config"])

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


def _row_to_filter(r) -> KeywordFilter:
    """Convert a sqlite row to a KeywordFilter Pydantic model.

    keywords is stored as a JSON string; decode safely.
    """
    try:
        keywords = json.loads(r["keywords"]) if r["keywords"] else []
    except (TypeError, json.JSONDecodeError):
        logger.warning("[config_filters] row id=%d has invalid JSON keywords", r["id"])
        keywords = []
    if not isinstance(keywords, list):
        keywords = []
    return KeywordFilter(
        id=r["id"],
        source_id=r["source_id"],
        mode=r["mode"],
        match_field=r["match_field"],
        keywords=[str(k) for k in keywords],
        is_active=bool(r["is_active"]),
        notes=r["notes"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


@router.get("", response_model=list[KeywordFilter])
def list_filters(
    source_id: str | None = Query(None, description="Filter by source id"),
    include_inactive: bool = Query(False, description="Include inactive filters"),
) -> list[KeywordFilter]:
    """List keyword filters. Optional ?source_id=... filter."""
    conn = get_db()
    where = []
    args: list = []
    if source_id:
        where.append("source_id = ?")
        args.append(source_id)
    if not include_inactive:
        where.append("is_active = 1")
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"""
        SELECT id, source_id, mode, match_field, keywords,
               is_active, notes, created_at, updated_at
        FROM source_keyword_filters
        {where_sql}
        ORDER BY source_id, mode, id
        """,
        args,
    ).fetchall()
    return [_row_to_filter(r) for r in rows]


@router.post("", response_model=KeywordFilter, status_code=201)
def create_filter(body: KeywordFilterCreate) -> KeywordFilter:
    """Create a new keyword filter. Returns 400 if mode/match_field invalid.

    Multiple filters per (source_id, mode) are allowed (no uniqueness constraint).
    """
    _validate_mode(body.mode)
    _validate_match_field(body.match_field)
    if not body.keywords:
        raise BadRequestError(message="keywords must be a non-empty list")
    conn = get_db()
    # Verify source exists
    src_row = conn.execute("SELECT 1 FROM sources WHERE id = ?", (body.source_id,)).fetchone()
    if src_row is None:
        raise BadRequestError(
            message=f"source_id {body.source_id!r} does not exist",
            details={"source_id": body.source_id},
        )
    now = _now()
    keywords_json = json.dumps(body.keywords, ensure_ascii=False)
    cur = conn.execute(
        """
        INSERT INTO source_keyword_filters
            (source_id, mode, match_field, keywords,
             is_active, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (body.source_id, body.mode, body.match_field, keywords_json,
         1 if body.is_active else 0, body.notes, now, now),
    )
    conn.commit()
    return KeywordFilter(
        id=cur.lastrowid,
        source_id=body.source_id,
        mode=body.mode,
        match_field=body.match_field,
        keywords=list(body.keywords),
        is_active=body.is_active,
        notes=body.notes,
        created_at=now,
        updated_at=now,
    )


@router.get("/{filter_id}", response_model=KeywordFilter)
def get_filter(filter_id: int) -> KeywordFilter:
    conn = get_db()
    row = conn.execute(
        """
        SELECT id, source_id, mode, match_field, keywords,
               is_active, notes, created_at, updated_at
        FROM source_keyword_filters WHERE id = ?
        """,
        (filter_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Filter id={filter_id} not found")
    return _row_to_filter(row)


@router.put("/{filter_id}", response_model=KeywordFilter)
def update_filter(filter_id: int, body: KeywordFilterUpdate) -> KeywordFilter:
    conn = get_db()
    row = conn.execute(
        """
        SELECT id, source_id, mode, match_field, keywords,
               is_active, notes, created_at, updated_at
        FROM source_keyword_filters WHERE id = ?
        """,
        (filter_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Filter id={filter_id} not found")

    fields = dict(row)
    if body.mode is not None:
        _validate_mode(body.mode)
        fields["mode"] = body.mode
    if body.match_field is not None:
        _validate_match_field(body.match_field)
        fields["match_field"] = body.match_field
    if body.keywords is not None:
        if not body.keywords:
            raise BadRequestError(message="keywords must be a non-empty list")
        fields["keywords"] = json.dumps(body.keywords, ensure_ascii=False)
    if body.is_active is not None:
        fields["is_active"] = 1 if body.is_active else 0
    if body.notes is not None:
        fields["notes"] = body.notes
    fields["updated_at"] = _now()

    conn.execute(
        """
        UPDATE source_keyword_filters SET
            mode = ?, match_field = ?, keywords = ?,
            is_active = ?, notes = ?, updated_at = ?
        WHERE id = ?
        """,
        (fields["mode"], fields["match_field"], fields["keywords"],
         fields["is_active"], fields["notes"], fields["updated_at"],
         filter_id),
    )
    conn.commit()
    return _row_to_filter(fields)


@router.delete("/{filter_id}", status_code=204)
def delete_filter(filter_id: int) -> None:
    conn = get_db()
    row = conn.execute("SELECT 1 FROM source_keyword_filters WHERE id = ?", (filter_id,)).fetchone()
    if row is None:
        raise NotFoundError(message=f"Filter id={filter_id} not found")
    conn.execute("DELETE FROM source_keyword_filters WHERE id = ?", (filter_id,))
    conn.commit()
