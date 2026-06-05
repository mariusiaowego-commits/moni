"""routes/config_sources — CRUD for sources + source_tags (v1.0.2).

Sources are the canonical replacement for config/sources.yaml. Each source
can be associated with one or more tag_definitions via the source_tags
M:N table.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from moni.db.connection import get_db
from moni.web.exceptions import ConflictError, NotFoundError
from moni.web.models import (
    Source,
    SourceCreate,
    SourceUpdate,
    SourceWithTags,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config/sources", tags=["config"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_source(r) -> Source:
    return Source(
        id=r["id"],
        name=r["name"],
        display_name=r["display_name"],
        source_type=r["source_type"],
        url=r["url"],
        fetch_interval_min=r["fetch_interval_min"],
        is_active=bool(r["is_active"]),
        notes=r["notes"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


@router.get("", response_model=list[SourceWithTags])
def list_sources() -> list[SourceWithTags]:
    """List all sources with their associated tag names."""
    conn = get_db()
    sources = conn.execute(
        """
        SELECT id, name, display_name, source_type, url,
               fetch_interval_min, is_active, notes, created_at, updated_at
        FROM sources
        ORDER BY name
        """
    ).fetchall()
    if not sources:
        return []

    # Fetch all source_tags in one query (N+1 avoidance)
    tag_rows = conn.execute(
        "SELECT source_id, tag_name FROM source_tags"
    ).fetchall()
    tags_by_source: dict[str, list[str]] = {}
    for t in tag_rows:
        tags_by_source.setdefault(t["source_id"], []).append(t["tag_name"])

    result = []
    for r in sources:
        s = _row_to_source(r).model_dump()
        s["tags"] = sorted(tags_by_source.get(r["id"], []))
        result.append(SourceWithTags(**s))
    return result


@router.post("", response_model=SourceWithTags, status_code=201)
def create_source(body: SourceCreate) -> SourceWithTags:
    """Create a new source. Returns 409 if id or name already exists."""
    conn = get_db()
    existing = conn.execute(
        "SELECT 1 FROM sources WHERE id = ? OR name = ?", (body.id, body.name)
    ).fetchone()
    if existing:
        raise ConflictError(
            message=f"Source id={body.id!r} or name={body.name!r} already exists",
            details={"id": body.id, "name": body.name},
        )
    now = _now()
    conn.execute(
        """
        INSERT INTO sources
            (id, name, display_name, source_type, url,
             fetch_interval_min, is_active, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (body.id, body.name, body.display_name, body.source_type, body.url,
         body.fetch_interval_min, 1 if body.is_active else 0, body.notes,
         now, now),
    )
    conn.commit()
    return SourceWithTags(
        id=body.id, name=body.name, display_name=body.display_name,
        source_type=body.source_type, url=body.url,
        fetch_interval_min=body.fetch_interval_min,
        is_active=body.is_active, notes=body.notes,
        created_at=now, updated_at=now, tags=[],
    )


@router.get("/{source_id}", response_model=SourceWithTags)
def get_source(source_id: str) -> SourceWithTags:
    """Get a single source with its tag names."""
    conn = get_db()
    row = conn.execute(
        """
        SELECT id, name, display_name, source_type, url,
               fetch_interval_min, is_active, notes, created_at, updated_at
        FROM sources WHERE id = ?
        """,
        (source_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Source {source_id!r} not found")
    tag_rows = conn.execute(
        "SELECT tag_name FROM source_tags WHERE source_id = ? ORDER BY tag_name",
        (source_id,),
    ).fetchall()
    s = _row_to_source(row).model_dump()
    s["tags"] = [t["tag_name"] for t in tag_rows]
    return SourceWithTags(**s)


@router.put("/{source_id}", response_model=SourceWithTags)
def update_source(source_id: str, body: SourceUpdate) -> SourceWithTags:
    """Update mutable fields of a source. Returns 404 if not found."""
    conn = get_db()
    row = conn.execute(
        """
        SELECT id, name, display_name, source_type, url,
               fetch_interval_min, is_active, notes, created_at, updated_at
        FROM sources WHERE id = ?
        """,
        (source_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Source {source_id!r} not found")

    fields = {**dict(row)}
    if body.display_name is not None:
        fields["display_name"] = body.display_name
    if body.url is not None:
        fields["url"] = body.url
    if body.fetch_interval_min is not None:
        fields["fetch_interval_min"] = body.fetch_interval_min
    if body.is_active is not None:
        fields["is_active"] = 1 if body.is_active else 0
    if body.notes is not None:
        fields["notes"] = body.notes
    fields["updated_at"] = _now()

    conn.execute(
        """
        UPDATE sources SET
            display_name = ?, url = ?, fetch_interval_min = ?,
            is_active = ?, notes = ?, updated_at = ?
        WHERE id = ?
        """,
        (fields["display_name"], fields["url"], fields["fetch_interval_min"],
         fields["is_active"], fields["notes"], fields["updated_at"], source_id),
    )
    conn.commit()
    tag_rows = conn.execute(
        "SELECT tag_name FROM source_tags WHERE source_id = ? ORDER BY tag_name",
        (source_id,),
    ).fetchall()
    s = {**fields, "is_active": bool(fields["is_active"])}
    s["tags"] = [t["tag_name"] for t in tag_rows]
    return SourceWithTags(**s)


@router.delete("/{source_id}", status_code=204)
def delete_source(source_id: str) -> None:
    """Delete a source. CASCADE removes source_tags and source_keyword_filters."""
    conn = get_db()
    row = conn.execute("SELECT 1 FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise NotFoundError(message=f"Source {source_id!r} not found")
    conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.commit()


@router.put("/{source_id}/tags", response_model=SourceWithTags)
def set_source_tags(source_id: str, tag_names: list[str]) -> SourceWithTags:
    """Replace the tag list for a source (PUT semantics: full replacement).

    Tags not in the request body are removed; new tags are added.
    Tags must already exist in tag_definitions (FK).
    """
    conn = get_db()
    row = conn.execute("SELECT 1 FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise NotFoundError(message=f"Source {source_id!r} not found")

    # Validate all tag names exist
    if tag_names:
        placeholders = ",".join("?" for _ in tag_names)
        valid = conn.execute(
            f"SELECT name FROM tag_definitions WHERE name IN ({placeholders})",
            tag_names,
        ).fetchall()
        valid_names = {v["name"] for v in valid}
        missing = set(tag_names) - valid_names
        if missing:
            from moni.web.exceptions import BadRequestError
            raise BadRequestError(
                message=f"Unknown tag names: {sorted(missing)}",
                details={"missing": sorted(missing)},
            )

    now = _now()
    conn.execute("DELETE FROM source_tags WHERE source_id = ?", (source_id,))
    for tag_name in tag_names:
        conn.execute(
            "INSERT INTO source_tags (source_id, tag_name, created_at) VALUES (?, ?, ?)",
            (source_id, tag_name, now),
        )
    conn.execute("UPDATE sources SET updated_at = ? WHERE id = ?", (now, source_id))
    conn.commit()

    # Return updated source
    return get_source(source_id)
