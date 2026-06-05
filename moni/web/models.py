"""Shared Pydantic schemas for the web API."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Article schemas
# ---------------------------------------------------------------------------


class Article(BaseModel):
    """Full article record."""

    id: str
    source_id: int
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
    """Schema for creating a new article (internal)."""

    source_id: int
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
    """A share/export task (e.g. to generate a shareable link)."""

    id: str
    article_id: int = 0
    token: str
    expires_at: datetime
    view_count: int = 0
    created_at: datetime

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
]
