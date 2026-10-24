"""routes/config_sources — CRUD for sources + source_tags (v1.0.2).

Sources are the canonical replacement for config/sources.yaml. Each source
can be associated with one or more tag_definitions via the source_tags
M:N table.
"""
from __future__ import annotations

import json as _json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from moni.db.connection import get_db
from moni.web.exceptions import ConflictError, NotFoundError
from moni.web.models import (
    FetchConfigResponse,
    Source,
    SourceCreate,
    SourceUpdate,
    SourceWithTags,
)
from moni.web.redact import is_masked_url, sanitize_source_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config/sources", tags=["config"])
# Known fetch_config defaults per source (for UI pre-fill + merge)
# v1.0.12: include xwatch so empty/partial rows still show documented keys.
_FETCH_CONFIG_DEFAULTS: dict[str, dict] = {
    "bestblogs": {
        "max_feeds": 375,
        "max_articles_per_feed": 5,
        "max_articles_total": 500,
        "request_delay": 0.5,
        "timeout_sec": 1800,
    },
    "bestblog-podcast": {
        "max_feeds": 60,
        "max_articles_per_feed": 5,
        "max_articles_total": 100,
        "request_delay": 0.5,
        "timeout_sec": 1800,
    },
    "bestblog-x": {
        "max_feeds": 160,
        "max_articles_per_feed": 5,
        "max_articles_total": 200,
        "request_delay": 0.5,
        "timeout_sec": 1800,
    },
    "bestblog-yt": {
        "max_feeds": 20,
        "max_articles_per_feed": 5,
        "max_articles_total": 50,
        "request_delay": 0.5,
        "timeout_sec": 1800,
    },
    "xwatch": {
        "following_enabled": True,
        "following_max_posts": 100,
        "following_max_scrolls": 40,
        "following_scroll_pause": 1.8,
        "following_skip_syndication": True,
        "following_stop_chrome": True,
        "accounts": [],
        "accounts_per_run": 5,
        "max_results_per_account": 5,
        "account_rss": {},
        "account_ddg_fallback": True,
        "queries": [],
        "queries_per_run": 1,
        "max_results_per_query": 10,
        "request_delay": 8.0,
        "search_timeout": 20,
        "syndication_timeout": 20,
        "ddg_circuit_minutes": 60.0,
        "skip_existing_urls": True,
    },
    "follow_builders": {
        "bypass_keyword_filters": True,
        "timeout_sec": 60,
        "http_timeout": 15.0,
        "skip_existing_urls": True,
    },
    "chinese_independent_blogs": {
        "max_feeds": 80,
        "max_articles_per_feed": 3,
        "max_articles_total": 240,
        "request_delay": 0.35,
        "http_timeout": 12.0,
        "min_feeds_before_failure_rate_check": 10,
        "max_failure_rate": 0.7,
        "timeout_sec": 1200,
    },
}

_RETIRED_FETCH_CONFIG_KEYS = {"consecutive_failure_limit"}



def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_source(r) -> Source:
    fc = None
    if r["fetch_config"]:
        try:
            fc = _json.loads(r["fetch_config"])
        except (_json.JSONDecodeError, TypeError):
            fc = None
    return Source(
        id=r["id"],
        name=r["name"],
        display_name=r["display_name"],
        source_type=r["source_type"],
        # v1.0.42 (audit P1-10): never echo the credential embedded in the
        # WeChat aggregator URLs.  Out-bound only — the stored value is the
        # fetch input and must stay intact.  (`or ""` only for typing: a NULL
        # url used to raise a ValidationError here anyway.)
        url=sanitize_source_url(r["url"]) or "",
        fetch_interval_min=r["fetch_interval_min"],
        is_active=bool(r["is_active"]),
        notes=r["notes"],
        fetch_config=fc,
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
               fetch_interval_min, is_active, notes, fetch_config,
               created_at, updated_at
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
             fetch_interval_min, is_active, notes, fetch_config,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (body.id, body.name, body.display_name, body.source_type, body.url,
         body.fetch_interval_min, 1 if body.is_active else 0, body.notes,
         _json.dumps(body.fetch_config, ensure_ascii=False) if body.fetch_config else None,
         now, now),
    )
    conn.commit()
    return SourceWithTags(
        id=body.id, name=body.name, display_name=body.display_name,
        source_type=body.source_type,
        # v1.0.42 (P1-10): sanitize the echo as well (idempotent).
        url=sanitize_source_url(body.url) or "",
        fetch_interval_min=body.fetch_interval_min,
        is_active=body.is_active, notes=body.notes,
        fetch_config=body.fetch_config,
        created_at=now, updated_at=now, tags=[],
    )


@router.get("/{source_id}", response_model=SourceWithTags)
def get_source(source_id: str) -> SourceWithTags:
    """Get a single source with its tag names."""
    conn = get_db()
    row = conn.execute(
        """
        SELECT id, name, display_name, source_type, url,
               fetch_interval_min, is_active, notes, fetch_config,
               created_at, updated_at
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
               fetch_interval_min, is_active, notes, fetch_config,
               created_at, updated_at
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
        # v1.0.42 (P1-10): a masked value means "the client echoed what we
        # gave it" → keep the stored credential, never write "…" into the DB
        # (it is the fetch input; a masked value would break RSS pulling).
        if not is_masked_url(body.url):
            fields["url"] = body.url
    if body.fetch_interval_min is not None:
        fields["fetch_interval_min"] = body.fetch_interval_min
    if body.is_active is not None:
        fields["is_active"] = 1 if body.is_active else 0
    if body.notes is not None:
        fields["notes"] = body.notes
    if body.fetch_config is not None:
        fields["fetch_config"] = _json.dumps(body.fetch_config, ensure_ascii=False)
    fields["updated_at"] = _now()

    conn.execute(
        """
        UPDATE sources SET
            display_name = ?, url = ?, fetch_interval_min = ?,
            is_active = ?, notes = ?, fetch_config = ?, updated_at = ?
        WHERE id = ?
        """,
        (fields["display_name"], fields["url"], fields["fetch_interval_min"],
         fields["is_active"], fields["notes"], fields.get("fetch_config"),
         fields["updated_at"], source_id),
    )
    conn.commit()
    tag_rows = conn.execute(
        "SELECT tag_name FROM source_tags WHERE source_id = ? ORDER BY tag_name",
        (source_id,),
    ).fetchall()
    s = {**fields, "is_active": bool(fields["is_active"])}
    # v1.0.42 (P1-10): the response must not carry the stored credential.
    s["url"] = sanitize_source_url(s.get("url")) or ""
    # Parse fetch_config JSON string back to dict for the response model
    fc_raw = s.get("fetch_config")
    if isinstance(fc_raw, str) and fc_raw:
        try:
            s["fetch_config"] = _json.loads(fc_raw)
        except (_json.JSONDecodeError, TypeError):
            s["fetch_config"] = None
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


@router.get("/{source_id}/fetch_config", response_model=FetchConfigResponse)
def get_fetch_config(source_id: str) -> FetchConfigResponse:
    """Get the fetch_config for a source, merged with type defaults."""
    conn = get_db()
    row = conn.execute(
        "SELECT name, fetch_config FROM sources WHERE id = ?",
        (source_id,),
   ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Source {source_id!r} not found")

    user_cfg: dict = {}
    if row["fetch_config"]:
        try:
            user_cfg = _json.loads(row["fetch_config"])
        except (_json.JSONDecodeError, TypeError):
            pass

    source_name = row["name"]
    defaults = _FETCH_CONFIG_DEFAULTS.get(source_name, {})
    # Only remove keys that were explicitly retired. A schema whitelist is
    # unsafe here: fetch_config also carries runtime keys and documented
    # aliases (timeout_sec, bypass_tag_filter) that older source schemas do
    # not list.
    user_cfg = {
        k: v for k, v in user_cfg.items() if k not in _RETIRED_FETCH_CONFIG_KEYS
    }
    if "bypass_tag_filter" in user_cfg:
        if "bypass_keyword_filters" not in user_cfg:
            user_cfg["bypass_keyword_filters"] = user_cfg["bypass_tag_filter"]
        user_cfg.pop("bypass_tag_filter")
    merged = {**defaults, **user_cfg}

    return FetchConfigResponse(
        source_id=source_id,
        source_name=source_name,
        fetch_config=merged,
        has_custom=bool(user_cfg),
        defaults=defaults,
    )


@router.put("/{source_id}/fetch_config", response_model=FetchConfigResponse)
def update_fetch_config(source_id: str, body: dict) -> FetchConfigResponse:
    """Update fetch_config for a source. Pass the full config dict."""
    conn = get_db()
    row = conn.execute(
        "SELECT name FROM sources WHERE id = ?",
        (source_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Source {source_id!r} not found")

    now = _now()
    conn.execute(
        "UPDATE sources SET fetch_config = ?, updated_at = ? WHERE id = ?",
        (_json.dumps(body, ensure_ascii=False), now, source_id),
    )
    conn.commit()

    # Return merged config
    return get_fetch_config(source_id)
