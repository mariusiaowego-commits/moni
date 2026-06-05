"""routes/config_tags — CRUD for tag_definitions (v1.0.2).

Tags are referenced by source_tags (M:N with sources) and article_tags (M:N
with articles). Deleting a tag cascades via FK ON DELETE CASCADE.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from moni.db.connection import get_db
from moni.web.exceptions import ConflictError, NotFoundError
from moni.web.models import Tag, TagCreate, TagUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config/tags", tags=["config"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("", response_model=list[Tag])
def list_tags() -> list[Tag]:
    """List all tag definitions (including inactive)."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT name, description, color, is_active, created_at
        FROM tag_definitions
        ORDER BY name
        """
    ).fetchall()
    return [Tag(**dict(r)) for r in rows]


@router.post("", response_model=Tag, status_code=201)
def create_tag(body: TagCreate) -> Tag:
    """Create a new tag definition. Returns 409 if name exists."""
    conn = get_db()
    existing = conn.execute(
        "SELECT 1 FROM tag_definitions WHERE name = ?", (body.name,)
    ).fetchone()
    if existing:
        raise ConflictError(
            message=f"Tag {body.name!r} already exists",
            details={"name": body.name},
        )
    now = _now()
    conn.execute(
        """
        INSERT INTO tag_definitions (name, description, color, created_at, is_active)
        VALUES (?, ?, ?, ?, 1)
        """,
        (body.name, body.description, body.color, now),
    )
    conn.commit()
    return Tag(
        name=body.name,
        description=body.description,
        color=body.color,
        is_active=True,
        created_at=now,
    )


@router.put("/{name}", response_model=Tag)
def update_tag(name: str, body: TagUpdate) -> Tag:
    """Update an existing tag. Returns 404 if not found."""
    conn = get_db()
    row = conn.execute(
        "SELECT name, description, color, is_active, created_at FROM tag_definitions WHERE name = ?",
        (name,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Tag {name!r} not found")

    fields = {**dict(row)}
    if body.description is not None:
        fields["description"] = body.description
    if body.color is not None:
        fields["color"] = body.color
    if body.is_active is not None:
        fields["is_active"] = 1 if body.is_active else 0

    conn.execute(
        """
        UPDATE tag_definitions
        SET description = ?, color = ?, is_active = ?
        WHERE name = ?
        """,
        (fields["description"], fields["color"], fields["is_active"], name),
    )
    conn.commit()
    return Tag(**fields)


@router.delete("/{name}", status_code=204)
def delete_tag(name: str) -> None:
    """Delete a tag. CASCADE removes source_tags and article_tags references."""
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM tag_definitions WHERE name = ?", (name,)
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Tag {name!r} not found")
    conn.execute("DELETE FROM tag_definitions WHERE name = ?", (name,))
    conn.commit()
