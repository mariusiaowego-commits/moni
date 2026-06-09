"""Shared Pydantic schemas for the web API."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Article schemas
# ---------------------------------------------------------------------------


class TagRef(BaseModel):
    """Lightweight tag reference (v1.0.5.12).

    Surfaces to the frontend so the article-list / share / CRUD views
    can render a tag chip using the colour the user picked in
    /config > Tags.  The colour is the literal value from
    ``tag_definitions.color`` (hex string) and may be ``None`` if the
    row in ``tag_definitions`` is missing a colour (we then fall
    back to a neutral CSS default).

    v1.0.5.14: added ``display_name`` so the chip can render the
    human-friendly Chinese / unicode string the user typed in
    /config.  The frontend falls back to ``name`` when ``display_name``
    is NULL (e.g. legacy rows that haven't been re-seeded yet).
    """
    name: str
    display_name: str | None = None
    color: str | None = None


class Article(BaseModel):
    """Full article record."""
    id: str
    source: str  # source name (TEXT) — was wrongly typed as int source_id
    # v1.0.5.12: tag list now carries {name, color} so the UI can
    # render each chip with the colour the user picked in /config.
    # Old shape was list[str] (just names).  Test_api + app.js updated.
    tags: list[TagRef] = []
    title: str
    url: str
    author: str | None = None
    published_at: datetime | None = None
    content: str | None = None
    summary: str | None = None
    is_deleted: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ArticleCreate(BaseModel):
    """Schema for creating a new article (internal).

    v1.0.5.10: ``source_id`` is actually the source **name** (TEXT),
    not the row id.  ``articles.source`` is TEXT referencing
    ``sources.name`` — there is no FK to ``sources.id`` in the schema.
    """
    # v1.0.5.10: was `source_id: int` — but the schema uses TEXT name.
    # The historical model was wrong; fixed to match the actual schema.
    source_id: str
    title: str
    url: str
    author: str | None = None
    published_at: datetime | None = None
    content: str | None = None


class ArticleUpdate(BaseModel):
    """Schema for updating an existing article."""

    title: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    content: str | None = None
    summary: str | None = None


class ArticleSummaryRequest(BaseModel):
    """Request LLM to generate a summary for an article."""

    article_id: str


class FactCheckRequest(BaseModel):
    """Request fact-check analysis for an article."""

    article_id: str


# ---------------------------------------------------------------------------
# Delete / confirm-token flow
# ---------------------------------------------------------------------------


class DeletePreviewResponse(BaseModel):
    """Response for the delete-preview (confirm-token) endpoint."""

    token: str
    article_title: str
    expires_at: datetime


# ---------------------------------------------------------------------------
# CronJob schemas
# ---------------------------------------------------------------------------


class CronJob(BaseModel):
    """A scheduled cron job definition."""

    id: int
    name: str
    schedule: str  # cron expression, e.g. "0 * * * *"
    source_id: int | None = None
    is_active: bool = True
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CronJobCreate(BaseModel):
    """Schema for creating a new cron job."""

    name: str
    schedule: str
    source_id: int | None = None
    is_active: bool = True


class CronRun(BaseModel):
    """A single execution instance of a cron job."""

    id: int
    cron_job_id: int
    started_at: datetime
    finished_at: datetime | None = None
    status: str = Field(default="running")  # running | success | failed
    articles_fetched: int = 0
    error_message: str | None = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# FetchSource schemas
# ---------------------------------------------------------------------------


class FetchSource(BaseModel):
    """A web source that can be crawled."""

    id: int
    name: str
    base_url: str
    crawler_type: str  # e.g. "rss", "html", "api"
    is_active: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FetchSourceUpdate(BaseModel):
    """Schema for updating a FetchSource."""

    name: str | None = None
    base_url: str | None = None
    crawler_type: str | None = None
    is_active: bool | None = None
    config: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Health-check response."""

    status: str = "ok"
    version: str
    db_connected: bool


# ---------------------------------------------------------------------------
# ShareTask schemas
# ---------------------------------------------------------------------------


class ShareTask(BaseModel):
    """A share/export task in the v1.0 manifest-queue pipeline.

    v1.0.5.14: the model was previously misaligned with the
    ``share_tasks`` DB schema (it was returning stub fields like
    ``article_id=0, token=id, view_count=0`` — leftovers from a
    never-shipped public-link design).  The real table has 9
    columns (id, format, article_ids, output_path, status, error,
    created_at, started_at, finished_at, extra) and the manifest
    queue is driven by ``status`` (pending/running/done/failed/
    cancelled).  Frontend needs ``id``, ``article_count``,
    ``format``, ``status``, ``created_at`` to render the queue
    table — that's what we return.
    """
    id: str
    format: str
    article_count: int = 0
    # v1.0.5.15: surface output_path + output_url so the frontend queue
    # table can show a "打开" link for done tasks.  v1.0.5.14 trimmed
    # these off the public response model (they were in the DB but not
    # the API), so the user had no way to reach the artifact.  The
    # frontend uses output_url (browser-safe web URL); output_path is
    # the absolute filesystem path for users who want to copy/open it
    # in Finder.
    output_path: str | None = None
    output_url: str | None = None
    status: str = "pending"
    error: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ShareTaskCreate(BaseModel):
    """Schema for creating a share task (legacy, article_id-based)."""

    article_id: int
    expires_in_hours: int = 24


# ---------------------------------------------------------------------------
# Share v1.0 — manifest-pipeline schema
# ---------------------------------------------------------------------------


class ShareTaskCreateV1(BaseModel):
    """Request body for POST /api/share (v1.0 manifest pipeline)."""

    format: str = Field(..., description="Format name, e.g. 'markdown', 'pdf_bound'")
    version: str = Field(default="1.0", description="Manifest schema version")
    payload: dict[str, Any] = Field(..., description="Format-specific parameters")


class ShareTaskResponseV1(BaseModel):
    """Response body for POST /api/share on success."""

    share_id: str
    status: str = "pending"


class ShareTaskListResponse(BaseModel):
    """Paginated list of share tasks."""

    items: list[ShareTask]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Config: tag_definitions (v1.0.2)
# ---------------------------------------------------------------------------


class Tag(BaseModel):
    """业务 tag 定义 (用于 source ↔ tag / article ↔ tag).

    v1.0.5.4: optional ``keywords`` is the OR-set used by
    ``moni.fetch.tag_filter`` to decide which articles from the
    bound sources are kept at fetch time.  When the field is empty
    the tag still works for classification / display, it just does
    NOT constrain fetched content.

    v1.0.5.14: ``display_name`` is the human-friendly UI name
    (Chinese / any string).  ``name`` stays as the immutable
    system primary key + FK reference for source_tags / article_tags
    — users edit ``display_name`` to "rename" a tag from the UI.
    The mirrors sources.name / sources.display_name pattern.
    """
    name: str
    display_name: str | None = None
    description: str | None = None
    color: str | None = None
    is_active: bool = True
    created_at: str
    keywords: list[str] = []


class TagCreate(BaseModel):
    name: str
    display_name: str | None = None
    description: str | None = None
    color: str | None = None
    keywords: list[str] = []


class TagUpdate(BaseModel):
    # v1.0.5.14 (display_name mode): `name` is intentionally NOT in
    # this model — it stays as the immutable primary key.  To "rename"
    # a tag from the UI, the user edits `display_name`, which is a
    # plain column update with no FK cascade required.  The previous
    # v1.0.5.14 attempt to allow renaming `name` directly was rolled
    # back because the FK chain on tag_definitions.name has no
    # ON UPDATE CASCADE, so any rename transaction had to manually
    # PRAGMA foreign_keys = OFF and reorder UPDATEs — fragile and
    # rejected by the user in favour of the cleaner display_name
    # pattern (mirrors sources.display_name).
    display_name: str | None = None
    description: str | None = None
    color: str | None = None
    is_active: bool | None = None
    keywords: list[str] | None = None


# ---------------------------------------------------------------------------
# Config: sources (v1.0.2)
# ---------------------------------------------------------------------------


class Source(BaseModel):
    """抓取源配置 (替代 config/sources.yaml 主用)."""
    id: str
    name: str
    display_name: str | None = None
    source_type: str
    url: str
    fetch_interval_min: int
    is_active: bool
    notes: str | None = None
    created_at: str
    updated_at: str


class SourceCreate(BaseModel):
    id: str
    name: str
    display_name: str | None = None
    source_type: str
    url: str
    fetch_interval_min: int = 30
    is_active: bool = True
    notes: str | None = None


class SourceUpdate(BaseModel):
    display_name: str | None = None
    url: str | None = None
    fetch_interval_min: int | None = None
    is_active: bool | None = None
    notes: str | None = None


class SourceWithTags(Source):
    """Source + 关联的 tag_name 列表."""
    tags: list[str] = []


# ---------------------------------------------------------------------------
# Config: source_keyword_filters (v1.0.2)
# ---------------------------------------------------------------------------


class KeywordFilter(BaseModel):
    """源关键词白/黑名单 filter."""
    id: int
    source_id: str
    mode: str                       # 'whitelist' | 'blacklist'
    match_field: str = "title+content"
    keywords: list[str] = []
    is_active: bool = True
    notes: str | None = None
    created_at: str
    updated_at: str


class KeywordFilterCreate(BaseModel):
    source_id: str
    mode: str
    match_field: str = "title+content"
    keywords: list[str] = []
    is_active: bool = True
    notes: str | None = None


class KeywordFilterUpdate(BaseModel):
    mode: str | None = None
    match_field: str | None = None
    keywords: list[str] | None = None
    is_active: bool | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# Standard ErrorResponse
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """Canonical error shape returned by all API errors."""

    code: str
    message: str
    details: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Re-export for convenience
# ---------------------------------------------------------------------------

__all__ = [
    "Article",
    "ArticleCreate",
    "ArticleUpdate",
    "ArticleSummaryRequest",
    "FactCheckRequest",
    "DeletePreviewResponse",
    "CronJob",
    "CronJobCreate",
    "CronRun",
    "FetchSource",
    "FetchSourceUpdate",
    "HealthResponse",
    "ShareTask",
    "ShareTaskCreate",
    "ShareTaskCreateV1",
    "ShareTaskResponseV1",
    "ShareTaskListResponse",
    "ErrorResponse",
    # v1.0.2 config
    "Tag", "TagCreate", "TagUpdate",
    "Source", "SourceCreate", "SourceUpdate", "SourceWithTags",
    "KeywordFilter", "KeywordFilterCreate", "KeywordFilterUpdate",
]
