"""moni.share.formats — v1.0 format schemas and validation.

There are 11 supported share/export formats, each with its own
Pydantic model and a shared validation entry point.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Format registry
# ---------------------------------------------------------------------------


class FormatEnum(str, Enum):
    """All supported share/export formats."""

    PLAIN_TEXT = "plain_text"
    MARKDOWN = "markdown"
    HTML_LANDING = "html_landing"
    INFO_GRAPHIC_PNG = "info_graphic_png"
    ARCHITECTURE_SVG = "architecture_svg"
    KNOWLEDGE_COMIC = "knowledge_comic"
    CONCEPT_VIDEO = "concept_video"
    ASCII_ART = "ascii_art"
    ASCII_VIDEO = "ascii_video"
    PDF_BOUND = "pdf_bound"
    PPT_SLIDES = "ppt_slides"


VALID_FORMATS: set[str] = {f.value for f in FormatEnum}


# ---------------------------------------------------------------------------
# Shared model config with extra='forbid'
# ---------------------------------------------------------------------------

_extra_forbid = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Per-format payload schemas (v1.0)
# ---------------------------------------------------------------------------


class PlainTextPayload(BaseModel):
    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    font_name: str = Field(default="Courier New", description="Monospace font")
    font_size: int = Field(default=12, ge=8, le=24)
    line_height: float = Field(default=1.5, ge=1.0, le=3.0)
    page_width: int = Field(default=80, ge=40, le=200, description="Characters per line")


class MarkdownPayload(BaseModel):
    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    include_toc: bool = Field(default=True, description="Generate table of contents")
    include_metadata: bool = Field(default=True, description="Show title/author/date headers")
    code_theme: str = Field(default="github-dark", description="Highlight.js theme name")


class HtmlLandingPayload(BaseModel):
    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    title: str = Field(..., description="Page title")
    description: str = Field(default="", description="Meta description")
    theme: str = Field(default="light", description="'light' | 'dark' | 'auto'")
    include_share_buttons: bool = Field(default=True)
    logo_url: str | None = Field(default=None)


class InfoGraphicPngPayload(BaseModel):
    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    width: int = Field(default=1200, ge=600, le=3840)
    height: int = Field(default=630, ge=300, le=2160)
    layout: str = Field(default="grid", description="'grid' | 'timeline' | 'comparison'")
    color_scheme: str = Field(default="vibrant", description="Colour palette name")


class ArchitectureSvgPayload(BaseModel):
    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    diagram_type: str = Field(
        default="flowchart", description="'flowchart' | 'hierarchical' | 'timeline'"
    )
    direction: str = Field(default="LR", description="'LR' | 'RL' | 'TB' | 'BT'")
    node_style: str = Field(default="rounded", description="'rounded' | 'square' | 'diamond'")


class KnowledgeComicPayload(BaseModel):
    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    art_style: str = Field(
        default="manga",
        description="'manga' | 'western' | 'chibi' | 'sketch'",
    )
    panel_count: int = Field(default=6, ge=2, le=24)
    caption_mode: str = Field(
        default="speech_bubble", description="'speech_bubble' | 'narration' | 'mixed'"
    )


class ConceptVideoPayload(BaseModel):
    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    duration_secs: int = Field(default=60, ge=10, le=300)
    resolution: str = Field(default="1080p", description="'720p' | '1080p' | '4k'")
    voice: str = Field(default="female_narrator", description="TTS voice name")
    music: str | None = Field(default=None, description="Background music cue")


class AsciiArtPayload(BaseModel):
    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    charset: str = Field(default="standard", description="'standard' | 'detailed' | 'blocks'")
    width: int = Field(default=120, ge=40, le=240)
    style: str = Field(default="photo", description="'photo' | 'portrait' | 'landscape'")


class AsciiVideoPayload(BaseModel):
    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    resolution: str = Field(default="1080p", description="'720p' | '1080p' | '4k'")
    fps: int = Field(default=12, ge=4, le=30)
    charset: str = Field(default="standard", description="'standard' | 'detailed' | 'blocks'")


class PdfBoundPayload(BaseModel):
    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    page_size: str = Field(default="A4", description="'A4' | 'Letter' | 'Legal'")
    font_family: str = Field(default="Noto Serif", description="Body font family")
    font_size: int = Field(default=11, ge=8, le=16)
    margin_mm: float = Field(default=20.0, ge=5.0, le=50.0)
    include_cover: bool = Field(default=True)


class PptSlidesPayload(BaseModel):
    model_config = _extra_forbid

    article_ids: list[str] = Field(..., description="Articles to include")
    title: str = Field(..., description="Presentation title")
    theme: str = Field(default="corporate", description="Theme name")
    slide_count: int = Field(default=10, ge=3, le=100)
    layout: str = Field(default="title_content", description="Master layout name")


# ---------------------------------------------------------------------------
# Format → model mapping
# ---------------------------------------------------------------------------

_FORMAT_MODELS: dict[str, type[BaseModel]] = {
    FormatEnum.PLAIN_TEXT.value: PlainTextPayload,
    FormatEnum.MARKDOWN.value: MarkdownPayload,
    FormatEnum.HTML_LANDING.value: HtmlLandingPayload,
    FormatEnum.INFO_GRAPHIC_PNG.value: InfoGraphicPngPayload,
    FormatEnum.ARCHITECTURE_SVG.value: ArchitectureSvgPayload,
    FormatEnum.KNOWLEDGE_COMIC.value: KnowledgeComicPayload,
    FormatEnum.CONCEPT_VIDEO.value: ConceptVideoPayload,
    FormatEnum.ASCII_ART.value: AsciiArtPayload,
    FormatEnum.ASCII_VIDEO.value: AsciiVideoPayload,
    FormatEnum.PDF_BOUND.value: PdfBoundPayload,
    FormatEnum.PPT_SLIDES.value: PptSlidesPayload,
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
        default_factory=lambda: datetime.utcnow().isoformat() + "Z",
        description="ISO8601 timestamp",
    )
    payload: dict[str, Any] = Field(..., description="Format-specific parameters")


def validate_manifest(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Validate a raw manifest dict against v1.0 schemas.

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
