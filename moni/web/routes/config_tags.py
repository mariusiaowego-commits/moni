"""routes/config_tags — CRUD for tag_definitions (v1.0.2).

Tags are referenced by source_tags (M:N with sources) and article_tags (M:N
with articles). Deleting a tag cascades via FK ON DELETE CASCADE.

v1.0.5.4: each tag can carry an optional list of ``keywords`` (substring
phrases).  The fetcher's ``moni.fetch.tag_filter`` builds the union of
keywords across every tag a source is bound to, and uses that as the
OR-set for the "keep / drop" decision.  See that module for the
behaviour.  Storage is JSON in the ``keywords`` column on
``tag_definitions`` (added in migration 006).
"""
from __future__ import annotations

import json
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


def _encode_keywords(kws: list[str] | None) -> str | None:
    """Serialize a keywords list to the JSON stored in the DB.

    Returns ``None`` for an empty list so the column stays NULL (and
    ``apply_tag_keyword_filter`` treats NULL as "no constraint" — see
    that module).
    """
    if not kws:
        return None
    # Drop empty strings and de-dupe while preserving order.
    seen: set[str] = set()
    cleaned: list[str] = []
    for kw in kws:
        if not isinstance(kw, str):
            continue
        s = kw.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
    if not cleaned:
        return None
    return json.dumps(cleaned, ensure_ascii=False)


def _decode_keywords(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        arr = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(arr, list):
        return []
    return [s for s in arr if isinstance(s, str)]


@router.get("", response_model=list[Tag])
def list_tags() -> list[Tag]:
    """List all tag definitions (including inactive)."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT name, description, color, is_active, created_at, keywords
        FROM tag_definitions
        ORDER BY name
        """
    ).fetchall()
    out: list[Tag] = []
    for r in rows:
        d = dict(r)
        d["keywords"] = _decode_keywords(d.get("keywords"))
        out.append(Tag(**d))
    return out


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
        INSERT INTO tag_definitions
            (name, description, color, created_at, is_active, keywords)
        VALUES (?, ?, ?, ?, 1, ?)
        """,
        (body.name, body.description, body.color, now, _encode_keywords(body.keywords)),
    )
    conn.commit()
    return Tag(
        name=body.name,
        description=body.description,
        color=body.color,
        is_active=True,
        created_at=now,
        keywords=_decode_keywords(_encode_keywords(body.keywords)),
    )


@router.put("/{name}", response_model=Tag)
def update_tag(name: str, body: TagUpdate) -> Tag:
    """Update an existing tag. Returns 404 if not found."""
    conn = get_db()
    row = conn.execute(
        "SELECT name, description, color, is_active, created_at, keywords "
        "FROM tag_definitions WHERE name = ?",
        (name,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Tag {name!r} not found")

    fields = {**dict(row)}
    fields["keywords"] = _decode_keywords(fields.get("keywords"))
    if body.description is not None:
        fields["description"] = body.description
    if body.color is not None:
        fields["color"] = body.color
    if body.is_active is not None:
        fields["is_active"] = 1 if body.is_active else 0
    if body.keywords is not None:
        # Body sent the field explicitly.  An empty list is a valid
        # request to clear the keywords (returning the tag to
        # "display only, no fetch-time filter").
        fields["keywords"] = body.keywords

    encoded_kw = _encode_keywords(fields["keywords"])
    conn.execute(
        """
        UPDATE tag_definitions
        SET description = ?, color = ?, is_active = ?, keywords = ?
        WHERE name = ?
        """,
        (fields["description"], fields["color"], fields["is_active"], encoded_kw, name),
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
