"""Shared Pydantic schemas for the web API."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Article schemas
# ---------------------------------------------------------------------------


class ArticleLabel(BaseModel):
    """One content label attached to an article (sprint-58).

    Only the compact trio is exposed in the article list; ``evidences``
    stays server-side so a S 类 sample cannot leak through the list API.
    """

    category: str
    severity: str
    review_state: str


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
    # v1.0.5.20: 该 tag 下命中的关键词
    keyword_hits: list[str] = []


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
    is_fresh: bool = False  # v1.0.7.0: 是否是该信源最新一次抓取的文章
    # v1.0.8: pin status.  is_pinned is true iff a row exists in
    # article_pins for this article.  pinned_at is the ISO datetime
    # of the pin (or null if not pinned).  Both come from a LEFT
    # JOIN in the listing query.
    is_pinned: bool = False
    pinned_at: datetime | None = None
    # Sprint 47: true when the article's latest reader conversation has an
    # unread assistant reply.  Computed by the list query; False for
    # articles that never entered the AI panel.
    reader_unread: bool = False
    # Sprint 50: true when the article has any reader AI conversation.
    has_reader_chat: bool = False
    # v1.0.101: 详情页要能显示两档核查结果（此前接口根本没返回这些字段）。
    fact_check_conclusion: str | None = None
    fact_check_method: str | None = None
    fact_checked_at: datetime | None = None
    has_fact_check: int = 0
    has_web_fact_check: int = 0
    fact_check_summary: str | None = None
    fact_check_thesis: str | None = None
    fact_check_how: str | None = None
    fact_check_timeliness: str | None = None
    # sprint-58: 内容分级角标（只给 category/severity/review_state）
    labels: list[ArticleLabel] = []
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


class ImportFulltextRequest(BaseModel):
    """Import full-text content from a local .md file."""

    file_path: str


class BulkDeleteRequest(BaseModel):
    """Delete up to 200 articles in one request (Sprint 47)."""

    ids: list[str]


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
    """A scheduled cron job from the ``cron_jobs`` registry (migration 056)."""

    id: str
    name: str
    func_path: str = ""
    trigger_type: str = "cron"
    schedule: dict[str, Any] = Field(default_factory=dict)
    func_kwargs: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    # True while the job's latest run is still in flight (cron_runs.extra
    # carries {"in_progress": true} until the run finalises its row).
    is_running: bool = False
    last_run_at: datetime | str | None = None
    next_run_at: datetime | str | None = None
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None


class CronJobUpdate(BaseModel):
    """Partial update for one job; omitted fields stay unchanged."""

    hour: int | None = None
    minute: int | None = None
    day_of_week: str | None = None
    func_kwargs: dict[str, Any] | None = None
    is_active: bool | None = None


class CronRun(BaseModel):
    """A single execution instance of a cron job.

    Schema mirrors ``cron_runs`` rows plus SPA-friendly aliases:
    - DB: job_id / triggered_at / success / new_articles
    - legacy SPA: cron_job_id / started_at / status / articles_fetched
    """

    id: int
    # Real APScheduler job id (e.g. "nightly_fetch"); preferred by the SPA.
    job_id: str = ""
    # Legacy integer placeholder (no cron_jobs table); keep for old clients.
    cron_job_id: int = 0
    triggered_at: datetime | str | None = None
    started_at: datetime | str | None = None
    finished_at: datetime | str | None = None
    # "success" | "failed" | "running"
    status: str = Field(default="running")
    # True when status == "success" (SPA historically checked run.success)
    success: bool = False
    new_articles: int = 0
    articles_fetched: int = 0
    duration: float | None = None  # seconds
    log_url: str | None = None
    error_message: str | None = None
    error: str | None = None
    # Per-run counters (feeds_scanned / candidates_new / budget_capped /
    # tag_chain ...).  Populated from ``cron_runs.extra``.
    extra: dict[str, Any] | None = None

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


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Health-check response.

    v1.0.12: optional ``detail`` for multi-probe sources (xwatch).
    Frontend may ignore unknown fields; SPA ping only uses HTTP status.
    """

    status: str = "ok"
    version: str
    db_connected: bool
    detail: dict | None = None
    message: str | None = None


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
    # v1.0.5.17: 3 progress-tracking fields.  Written by the worker
    # at each stage transition (queued → fetching → building_prompt
    # → invoking_llm → writing_artifact).  The frontend reads them
    # to show a progress bar in the queue table + a 5-stage stepper
    # in the task detail modal.  progress_message is a short
    # human-readable string the worker updates in real time
    # ("正在从 articles 表读 raw_content...", etc.).  Optional
    # fields default to None so the model still validates rows
    # that pre-date v1.0.5.17 (migrated with a one-shot backfill).
    progress_stage: str | None = None
    progress_pct: int | None = None
    progress_message: str | None = None
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


class BriefCreateRequest(BaseModel):
    """POST /api/share/brief — one-click brief from all current pins."""

    format: str = Field(default="markdown", description="Output format")
    title: str | None = Field(
        default=None,
        description="Optional title; auto-filled for formats that require it",
    )
    options: dict[str, Any] | None = Field(
        default=None,
        description="Override defaults: include_abstract/fact_check/business_value/...",
    )
    # v1.0.14: skip active-duplicate guard after user confirms in UI
    force: bool = Field(
        default=False,
        description="If true, enqueue even when a pending/running task already has the same pin set",
    )


class BriefCreateResponse(BaseModel):
    """Response for one-click brief create."""

    share_id: str
    status: str = "pending"
    article_count: int = 0
    worker: dict[str, Any] = Field(default_factory=dict)


class ShareTaskEstimateResponse(BaseModel):
    """Response body for POST /api/share/estimate.

    v1.0.5.16: pre-flight estimate for the confirm-modal.  No DB
    write, no share_task row created.  All values are best-effort
    educated guesses (token counts by char/4, duration by last-5
    same-format share_tasks average), accurate enough for the user
    to decide whether to proceed.

    cost_usd_est is intentionally omitted — moni has no pricing
    table (provider rates change monthly), so a guessed cost would
    be misleading.  Tokens + seconds are the durable numbers.
    """
    # The format this estimate is for.  Echoed from the request
    # so the frontend can render "正在预估 markdown..." without
    # needing to remember what the user clicked.  Optional because
    # older callers may not set it; default None so the schema
    # still validates.
    format: str | None = None
    # Resolved model info (read from llm_settings → llm_models → llm_providers)
    model_name: str               # e.g. "MiniMax M3"
    model_id: str                 # e.g. "seed-model-...-MiniMax-M3"
    provider_name: str            # e.g. "minimax-cn (默认)"
    # Input sizing — what we'll feed to the LLM
    articles_count: int
    total_chars: int
    input_tokens_est: int         # chars / 2.5 (rough CJK-mixed heuristic)
    # Output sizing — format-dependent multiplier on input
    output_tokens_est: int
    # Wall-clock estimate from the last 5 done share_tasks of the same format
    duration_sec_est: int
    duration_sec_basis: int       # how many past tasks we averaged
    # v1.0.101: 预检 —— 入队后富化会先补齐这些东西，提前告诉用户要等多久。
    articles_missing_summary: int = 0
    articles_missing_web_fact_check: int = 0
    # Where the user can find the output once done (path is illustrative —
    # the real id is allocated at write time)
    artifact_path_template: str   # e.g. "/artifacts/<id>-html_landing.html"


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
    # 停用与隐藏都会让信源退出定时/批量抓取（moni/fetch/source_gate.py）：
    # is_active=0 是「数据面停用」，is_hidden=1 是「隐藏且不再抓取」。
    # 手动单源触发仍放行。
    is_hidden: bool = False
    notes: str | None = None
    fetch_config: dict[str, Any] | None = None
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
    is_hidden: bool = False
    notes: str | None = None
    fetch_config: dict[str, Any] | None = None


class SourceUpdate(BaseModel):
    display_name: str | None = None
    url: str | None = None
    fetch_interval_min: int | None = None
    is_active: bool | None = None
    is_hidden: bool | None = None
    notes: str | None = None
    fetch_config: dict[str, Any] | None = None


class SourceWithTags(Source):
    """Source + 关联的 tag_name 列表."""
    tags: list[str] = []


class FetchConfigResponse(BaseModel):
    """Response for GET /api/config/sources/{id}/fetch_config."""
    source_id: str
    source_name: str
    fetch_config: dict[str, Any]
    has_custom: bool
    defaults: dict[str, Any]


class ScopeResponse(BaseModel):
    """Response for GET /api/config/sources/{id}/scope (v1.0.71 sprint-35).

    ``scope`` 是**展示用**的信源范围; ``scope_source`` 说明它来自哪里
    (三态, 优先级从上到下):

    * ``upstream`` —— 上游 feed JSON 顶层导出了 scope (权威, 只读);
    * ``moni_stored`` —— moni 侧存着用户编辑过的副本 (可编辑, 仅记录与展示);
    * ``moni_fallback`` —— 都没有 → 用兜底快照 (moni 侧兜底, 2026-11-11)。

    ``scope_edited`` 是给 UI 用的独立布尔 (与 ``scope_source`` 同步)。

    ``scope_gates_ingest`` 恒为 false: scope **不参与入库闸门** ——
    编辑它不会改变 X-bot 的搜索范围。
    """
    source_id: str
    source_name: str
    scope: dict[str, Any]
    scope_source: str
    upstream_scope_available: bool
    scope_edited: bool
    thresholds: dict[str, Any]
    scope_gates_ingest: bool
    stacking_note: str


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
# Config: global_keyword_filters (v1.0.40 / sprint-20 PR2)
# ---------------------------------------------------------------------------


class GlobalKeywordFilter(BaseModel):
    """全局关键词白/黑名单 filter (跨源生效)."""
    id: int
    mode: str                       # 'whitelist' | 'blacklist'
    match_field: str = "title+content"
    keywords: list[str] = []
    is_active: bool = True
    notes: str | None = None
    created_at: str
    updated_at: str


class GlobalKeywordFilterCreate(BaseModel):
    mode: str
    match_field: str = "title+content"
    keywords: list[str] = []
    is_active: bool = True
    notes: str | None = None


class GlobalKeywordFilterUpdate(BaseModel):
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
# Basket schemas (v1.0.6.9)
# ---------------------------------------------------------------------------


class BasketItem(BaseModel):
    """菜篮子中的单条被过滤文章."""
    id: str
    title: str
    url: str
    source: str
    publisher: str | None = None
    published_at: str | None = None
    fetched_at: str
    raw_content: str | None = None
    dropped_reason: str  # 'blacklist' | 'whitelist' | 'tag_filter'
    dropped_by: str | None = None
    rescued_at: str | None = None
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class BasketListResponse(BaseModel):
    """分页的 basket 列表响应."""
    items: list[BasketItem]
    total: int
    page: int
    page_size: int


class BasketRescueRequest(BaseModel):
    """rescue 请求: 选择要打的 tag."""
    tags: list[str] = []  # tag names to assign


# ---------------------------------------------------------------------------
# Re-export for convenience
# ---------------------------------------------------------------------------





__all__ = [
    "Direction",
    "DirectionCreate",
    "DirectionResearchAction",
    "DirectionResearchSummary",
    "DirectionUpdate",
    "Article",
    "ArticleCreate",
    "ArticleUpdate",
    "ArticleSummaryRequest",
    "FactCheckRequest",
    "DeletePreviewResponse",
    "CronJob",
    "CronJobUpdate",
    "CronRun",
    "FetchSource",
    "HealthResponse",
    "ShareTask",
    "ShareTaskCreate",
    "ShareTaskCreateV1",
    "ShareTaskResponseV1",
    "BriefCreateRequest",
    "BriefCreateResponse",
    "ShareTaskListResponse",
    "ErrorResponse",
    # v1.0.2 config
    "Tag", "TagCreate", "TagUpdate",
    "Source", "SourceCreate", "SourceUpdate", "SourceWithTags",
    "KeywordFilter", "KeywordFilterCreate", "KeywordFilterUpdate",
    "GlobalKeywordFilter", "GlobalKeywordFilterCreate", "GlobalKeywordFilterUpdate",
]


# ---------------------------------------------------------------------------
# Business-direction schemas (S2)
# ---------------------------------------------------------------------------


class DirectionCreate(BaseModel):
    label: str = Field(min_length=1, max_length=6)
    brief: str = Field(min_length=1, max_length=200)

    @field_validator("label", "brief", mode="before")
    @classmethod
    def _strip(cls, value):
        # Strip before length checks, otherwise " 微信生态 " fails the
        # length rule while "   " fails a different one further down.
        return value.strip() if isinstance(value, str) else value


class DirectionUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=6)
    brief: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None

    @field_validator("label", "brief", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value


class DirectionResearchAction(BaseModel):
    """Response of POST /api/directions/{id}/research."""

    direction_id: int
    research_version: int
    status: str


class DirectionResearchSummary(BaseModel):
    """Latest research row as shown in the config panel."""

    research_id: int
    direction_id: int
    research_version: int
    status: str
    scope: str | None = None
    out_of_scope: str | None = None
    error: str | None = None
    prompt_id: str | None = None
    model: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class Direction(BaseModel):
    id: int
    label: str
    brief: str
    enabled: bool
    created_at: str
    updated_at: str
    research: DirectionResearchAction | None = None
    usable: bool = False
    usable_error: str | None = None


class DirectionScoreBatch(BaseModel):
    """POST body for one direction-scoring round.

    The length is checked by the route, not by the model: a too-large
    batch must answer 400 with a readable reason, not a 422 validation
    dump.
    """

    article_ids: list[str]


class DirectionScoreItem(BaseModel):
    score: int | None = None
    note: str = ""
    status: str
    error: str | None = None
    stale: bool = False
    scored_at: str | None = None
    breakdown: dict | None = None


class DirectionScoresResponse(BaseModel):
    direction_id: int
    research_version: int
    rubric_id: str
    scores: dict[str, DirectionScoreItem]


class DirectionScoreQueued(BaseModel):
    direction_id: int
    research_version: int
    rubric_id: str
    queued: list[str]
    cached: list[str]
    rejected: list[dict]
