"""moni.share.formats — v1.0.5.18 format schemas and validation.

There are 6 supported share/export formats in v1.0.5.18 (down from
10 in v1.0.5.17 and 11 originally).  Each format has its own
Pydantic model with a shared validation entry point.  All 6 payloads
share 4 common "user options" fields — include_abstract /
include_fact_check / include_business_value / brief / provider / model — so the worker can honour
the user's choices consistently across formats.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Format registry (v1.0.5.18: 11 → 6)
# ---------------------------------------------------------------------------


class FormatEnum(str, Enum):
    """All supported share/export formats (v1.0.5.21).

    v1.0.5.18 dropped 6 formats (architecture_svg, knowledge_comic,
    ascii_art, ascii_video, pdf_bound, ppt_slides) — they were
    defined but never had a real worker implementation wired up.
    Two new formats were added: rich_text (a content-rich Markdown
    variant) and report_png (a one-image content report, with
    four generation-approach choices).
    v1.0.5.21: added pdf format.
    """

    PLAIN_TEXT = "plain_text"
    MARKDOWN = "markdown"
    HTML_LANDING = "html_landing"
    INFO_GRAPHIC_PNG = "info_graphic_png"
    RICH_TEXT = "rich_text"          # 🆕 v1.0.5.18
    REPORT_PNG = "report_png"        # 🆕 v1.0.5.18
    PDF = "pdf"                      # 🆕 v1.0.5.21


VALID_FORMATS: set[str] = {f.value for f in FormatEnum}


# ---------------------------------------------------------------------------
# Shared model config + 4 user-options fields
# ---------------------------------------------------------------------------

# v1.0.5.18: extra="forbid" stays (so the API rejects typos) but we
# allow None for the 4 user-options fields below.
_extra_forbid = ConfigDict(extra="forbid")


# v1.0.5.18 PRD §2: these 4 fields are SHARED across all 6 payloads.
# We avoid Pydantic mixin complexity by simply repeating the
# field declarations — it's 4 lines, more readable than the
# alternative (and a docstring is right next to each use site).
_USER_OPTIONS_FIELDS = """
    include_abstract: bool = Field(default=True, description=(
        "Include AI-generated summary (articles.ai_summary) per article"
    ))
    include_fact_check: bool = Field(default=False, description=(
        "Include fact-check analysis per article (slower; uses LLM)"
    ))
    provider: str | None = Field(default=None, description=(
        "Hermes provider override (e.g. 'google', 'openrouter'). "
        "Default profile's provider is used if None."
    ))
    model: str | None = Field(default=None, description=(
        "Hermes model override (e.g. 'gemini-2.5-pro'). "
        "Default profile's model is used if None."
    ))
    bb_enhance: bool = Field(default=False, description=(
        "Enable BestBlogs API metadata fetch (score/summary/mainPoints/keyQuotes/tags)"
    ))
    bb_summary_source: str | None = Field(default=None, description=(
        "Summary source: 'moni_ai' | 'bestblogs' | 'both' | 'none'"
    ))
    bb_score: bool = Field(default=False, description="Include BestBlogs score")
    bb_main_points: bool = Field(default=False, description="Include BestBlogs main points")
    bb_key_quotes: bool = Field(default=False, description="Include BestBlogs key quotes")
    bb_tags: bool = Field(default=False, description="Include BestBlogs tags")
"""


# ---------------------------------------------------------------------------
# Per-format payload schemas (v1.0.5.18: 6 formats)
# ---------------------------------------------------------------------------


class PlainTextPayload(BaseModel):
    """Plain-text export.  Worker writes directly without LLM call
    (per PRD §9 decision: plain_text is a no-LLM pass for speed).

    v1.0.5.18: include_abstract / include_fact_check / provider /
    model added for consistency with the other 5 payloads, even
    though the worker doesn't use them (the no-LLM pass just dumps
    the article content to a .txt file).  The fields are accepted
    by the API for cross-payload uniformity — the Pydantic model
    serves as a single source of truth for what every manifest may
    carry."""

    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    font_name: str = Field(default="Courier New", description="Monospace font")
    font_size: int = Field(default=12, ge=8, le=24)
    line_height: float = Field(default=1.5, ge=1.0, le=3.0)
    page_width: int = Field(default=80, ge=40, le=200, description="Characters per line")
    include_abstract: bool = Field(default=True)
    include_fact_check: bool = Field(default=False)
    include_business_value: bool = Field(default=False, description="Per-article business value score+note")
    include_user_context: bool = Field(default=False, description="Include user-curated article contexts as a separate section")
    include_quality_score: bool = Field(default=False, description="Quality score when bb_score missing")
    brief: bool = Field(default=False, description="One-click brief mode (sort by BV, rollup layout)")
    bb_enhance: bool = Field(default=False)
    bb_summary_source: str | None = Field(default=None)
    bb_score: bool = Field(default=False)
    bb_main_points: bool = Field(default=False)
    bb_key_quotes: bool = Field(default=False)
    bb_tags: bool = Field(default=False)
    provider: str | None = Field(default=None)
    model: str | None = Field(default=None)


class MarkdownPayload(BaseModel):
    """Standard Markdown export.  Joined via prompts/format-markdown.md."""

    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    include_toc: bool = Field(default=True, description="Generate table of contents")
    include_metadata: bool = Field(default=True, description="Show title/author/date headers")
    code_theme: str = Field(default="github-dark", description="Highlight.js theme name")
    include_abstract: bool = Field(default=True)
    include_fact_check: bool = Field(default=False)
    include_business_value: bool = Field(default=False, description="Per-article business value score+note")
    include_user_context: bool = Field(default=False, description="Include user-curated article contexts as a separate section")
    include_quality_score: bool = Field(default=False, description="Quality score when bb_score missing")
    brief: bool = Field(default=False, description="One-click brief mode (sort by BV, rollup layout)")
    bb_enhance: bool = Field(default=False)
    bb_summary_source: str | None = Field(default=None)
    bb_score: bool = Field(default=False)
    bb_main_points: bool = Field(default=False)
    bb_key_quotes: bool = Field(default=False)
    bb_tags: bool = Field(default=False)
    provider: str | None = Field(default=None)
    model: str | None = Field(default=None)


class HtmlLandingPayload(BaseModel):
    """HTML landing page.  Joined via prompts/format-html.md."""

    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    title: str = Field(..., description="Page title")
    description: str = Field(default="", description="Meta description")
    theme: str = Field(default="light", description="'light' | 'dark' | 'auto'")
    include_share_buttons: bool = Field(default=True)
    logo_url: str | None = Field(default=None)
    include_abstract: bool = Field(default=True)
    include_fact_check: bool = Field(default=False)
    include_business_value: bool = Field(default=False, description="Per-article business value score+note")
    include_user_context: bool = Field(default=False, description="Include user-curated article contexts as a separate section")
    include_quality_score: bool = Field(default=False, description="Quality score when bb_score missing")
    brief: bool = Field(default=False, description="One-click brief mode (sort by BV, rollup layout)")
    bb_enhance: bool = Field(default=False)
    bb_summary_source: str | None = Field(default=None)
    bb_score: bool = Field(default=False)
    bb_main_points: bool = Field(default=False)
    bb_key_quotes: bool = Field(default=False)
    bb_tags: bool = Field(default=False)
    provider: str | None = Field(default=None)
    model: str | None = Field(default=None)


class InfoGraphicPngPayload(BaseModel):
    """Information-graphic PNG.  Worker uses baoyu-infographic skill."""

    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    width: int = Field(default=1200, ge=600, le=3840)
    height: int = Field(default=630, ge=300, le=2160)
    layout: str = Field(default="grid", description="'grid' | 'timeline' | 'comparison'")
    color_scheme: str = Field(default="vibrant", description="Colour palette name")
    include_abstract: bool = Field(default=True)
    include_fact_check: bool = Field(default=False)
    include_business_value: bool = Field(default=False, description="Per-article business value score+note")
    include_user_context: bool = Field(default=False, description="Include user-curated article contexts as a separate section")
    include_quality_score: bool = Field(default=False, description="Quality score when bb_score missing")
    brief: bool = Field(default=False, description="One-click brief mode (sort by BV, rollup layout)")
    bb_enhance: bool = Field(default=False)
    bb_summary_source: str | None = Field(default=None)
    bb_score: bool = Field(default=False)
    bb_main_points: bool = Field(default=False)
    bb_key_quotes: bool = Field(default=False)
    bb_tags: bool = Field(default=False)
    provider: str | None = Field(default=None)
    model: str | None = Field(default=None)
    generation_approach: str = Field(default="baoyu-infographic", description="baoyu-infographic / ljg-card / kami / direct")
    title: str = Field(default="同业追踪日报", description="信息图总标题")


class RichTextPayload(BaseModel):
    """Content-rich Markdown variant (🆕 v1.0.5.18).

    Like MarkdownPayload but with explicit emphasis on the user's
    content-options (abstract + fact-check) and a richer
    cross-article layout (per PRD §1: "format-rich-text" prompt).
    """

    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    include_abstract: bool = Field(default=True)
    include_fact_check: bool = Field(default=False)
    include_business_value: bool = Field(default=False, description="Per-article business value score+note")
    include_user_context: bool = Field(default=False, description="Include user-curated article contexts as a separate section")
    include_quality_score: bool = Field(default=False, description="Quality score when bb_score missing")
    brief: bool = Field(default=False, description="One-click brief mode (sort by BV, rollup layout)")
    bb_enhance: bool = Field(default=False)
    bb_summary_source: str | None = Field(default=None)
    bb_score: bool = Field(default=False)
    bb_main_points: bool = Field(default=False)
    bb_key_quotes: bool = Field(default=False)
    bb_tags: bool = Field(default=False)
    provider: str | None = Field(default=None)
    model: str | None = Field(default=None)


class PdfPayload(BaseModel):
    """PDF 日报格式 (🆕 v1.0.5.21).

    生成一份 PDF 格式的日报，展示文章的核心内容。
    排版风格参考 BestBlogs.dev 日报格式。
    """

    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    title: str = Field(default="同业追踪日报", description="报告总标题")
    include_abstract: bool = Field(default=True)
    include_fact_check: bool = Field(default=False)
    include_business_value: bool = Field(default=False, description="Per-article business value score+note")
    include_user_context: bool = Field(default=False, description="Include user-curated article contexts as a separate section")
    include_quality_score: bool = Field(default=False, description="Quality score when bb_score missing")
    brief: bool = Field(default=False, description="One-click brief mode (sort by BV, rollup layout)")
    bb_enhance: bool = Field(default=False)
    bb_summary_source: str | None = Field(default=None)
    bb_score: bool = Field(default=False)
    bb_main_points: bool = Field(default=False)
    bb_key_quotes: bool = Field(default=False)
    bb_tags: bool = Field(default=False)
    provider: str | None = Field(default=None)
    model: str | None = Field(default=None)


class ReportPngPayload(BaseModel):
    """One-image content report (🆕 v1.0.5.18).

    The user picks a generation approach at queue time; the worker
    uses the corresponding skill (or none for direct).  See
    prompts/format-report-png.md for the joined prompt.
    """

    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    include_abstract: bool = Field(default=True)
    include_fact_check: bool = Field(default=False)
    include_business_value: bool = Field(default=False, description="Per-article business value score+note")
    include_user_context: bool = Field(default=False, description="Include user-curated article contexts as a separate section")
    include_quality_score: bool = Field(default=False, description="Quality score when bb_score missing")
    brief: bool = Field(default=False, description="One-click brief mode (sort by BV, rollup layout)")
    bb_enhance: bool = Field(default=False)
    bb_summary_source: str | None = Field(default=None)
    bb_score: bool = Field(default=False)
    bb_main_points: bool = Field(default=False)
    bb_key_quotes: bool = Field(default=False)
    bb_tags: bool = Field(default=False)
    provider: str | None = Field(default=None)
    model: str | None = Field(default=None)
    generation_approach: str = Field(
        default="baoyu-infographic",
        description=(
            "'baoyu-infographic' | 'ljg-card' | 'kami' | 'direct' — see "
            "PRD §4 for the skill mapping.  Default: baoyu-infographic."
        ),
    )
    aspect_ratio: str = Field(
        default="portrait",
        description="'landscape' | 'square' | 'portrait'",
    )


# ---------------------------------------------------------------------------
# v1.0.5.18: stale Payload classes kept commented for one release as
# breadcrumbs in case anyone wants to wire them up later.  Same
# pattern as the now-removed ConceptVideoPayload.  These are
# intentionally NOT registered in _FORMAT_MODELS so they're
# rejected at the API.
# ---------------------------------------------------------------------------
#
# class ArchitectureSvgPayload(BaseModel):
#     article_ids: list[str]
#     diagram_type: str  # 'flowchart' | 'hierarchical' | 'timeline'
#     direction: str     # 'LR' | 'RL' | 'TB' | 'BT'
#     node_style: str    # 'rounded' | 'square' | 'diamond'
#
# class KnowledgeComicPayload(BaseModel):
#     article_ids: list[str]
#     art_style: str     # 'manga' | 'western' | 'chibi' | 'sketch'
#     panel_count: int   # 2-24
#     caption_mode: str  # 'speech_bubble' | 'narration' | 'mixed'
#
# class AsciiArtPayload(BaseModel):
#     article_ids: list[str]
#     charset: str       # 'standard' | 'detailed' | 'blocks'
#     width: int         # 40-240
#     style: str         # 'photo' | 'portrait' | 'landscape'
#
# class AsciiVideoPayload(BaseModel):
#     article_ids: list[str]
#     resolution: str    # '720p' | '1080p' | '4k'
#     fps: int           # 4-30
#     charset: str       # 'standard' | 'detailed' | 'blocks'
#
# class PdfBoundPayload(BaseModel):
#     article_ids: list[str]
#     page_size: str     # 'A4' | 'Letter' | 'Legal'
#     font_family: str
#     font_size: int     # 8-16
#     margin_mm: float   # 5-50
#     include_cover: bool
#
# class PptSlidesPayload(BaseModel):
#     article_ids: list[str]
#     title: str         # required
#     theme: str
#     slide_count: int   # 3-100
#     layout: str        # master layout name


# ---------------------------------------------------------------------------
# Format → model mapping
# ---------------------------------------------------------------------------

_FORMAT_MODELS: dict[str, type[BaseModel]] = {
    FormatEnum.PLAIN_TEXT.value: PlainTextPayload,
    FormatEnum.MARKDOWN.value: MarkdownPayload,
    FormatEnum.HTML_LANDING.value: HtmlLandingPayload,
    FormatEnum.INFO_GRAPHIC_PNG.value: InfoGraphicPngPayload,
    FormatEnum.RICH_TEXT.value: RichTextPayload,           # 🆕
    FormatEnum.REPORT_PNG.value: ReportPngPayload,         # 🆕
    FormatEnum.PDF.value: PdfPayload,                      # 🆕 v1.0.5.21
}


# ---------------------------------------------------------------------------
# Validation entry point
# ---------------------------------------------------------------------------


class ManifestPayload(BaseModel):
    """Unvalidated top-level manifest payload as received from the API."""

    model_config = _extra_forbid

    format: str = Field(..., description="Format name, e.g. 'markdown'")
    version: str = Field(default="1.0", description="Manifest schema version")
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        description="ISO8601 timestamp",
    )
    payload: dict[str, Any] = Field(..., description="Format-specific parameters")


def validate_manifest(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Validate a raw manifest dict against v1.0.5.18 schemas.

    Args:
        data: Raw dict from request body.

    Returns:
        A 2-tuple of (format, validated_payload_dict).

    Raises:
        BadRequestError: if format is unknown, version is not "1.0",
            or payload fails validation.
    """
    from moni.web.exceptions import BadRequestError

    # Top-level structural check
    try:
        manifest = ManifestPayload.model_validate(data)
    except Exception as exc:
        raise BadRequestError(
            message="Invalid manifest structure",
            details={"error": str(exc)},
        )

    if manifest.version != "1.0":
        raise BadRequestError(
            message=f"Unsupported manifest version {manifest.version!r}, expected '1.0'",
            details={"supported_versions": ["1.0"]},
        )

    format_name = manifest.format
    if format_name not in VALID_FORMATS:
        raise BadRequestError(
            message=f"Unknown format {format_name!r}",
            details={
                "format": format_name,
                "supported_formats": sorted(VALID_FORMATS),
            },
        )

    # Delegate to the format-specific model
    model_cls = _FORMAT_MODELS[format_name]
    try:
        validated = model_cls.model_validate(manifest.payload)
    except Exception as exc:
        raise BadRequestError(
            message=f"Validation failed for format {format_name!r}",
            details={"error": str(exc), "format": format_name},
        )

    # Return plain dict for clean JSON serialisation
    return format_name, validated.model_dump()
