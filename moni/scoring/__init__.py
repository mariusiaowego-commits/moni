"""Scoring core: Pin quality (local) + business-value harness (model-assisted).

Public surface used by ``moni.share`` and ``moni.web``:

    from moni.scoring import (
        ArticleSnapshot, from_row, score_quality, reading_heuristic,
        score_general_bv, ensure_quality, needs_quality_refresh,
        recompute_quality_if_pinned, needs_bv_rescore, write_bv,
    )
"""
from .bv_pipeline import BvResult, content_hash, score_general_bv
from .direction import (
    DIRECTION_PROMPT_ID,
    DirectionResearch,
    brief_reject_reason,
    build_research_prompt,
    label_reject_reason,
    parse_research,
    research_to_rubric,
    usable_research,
)
from .heuristic import (
    PIN_FORMULA,
    QualityResult,
    is_quality_current,
    quality_fingerprint,
    reading_heuristic,
    score_quality,
)
from .rubric import RUBRIC_ID, RUBRIC_V1, DIMENSIONS, WEIGHT_TOTAL, Rubric
from .snapshot import ArticleSnapshot, from_row
from .store import (
    bv_inflight_fresh,
    ensure_quality,
    needs_bv_rescore,
    needs_quality_refresh,
    recompute_quality_if_pinned,
    write_bv,
    write_quality,
)

__all__ = [
    "ArticleSnapshot",
    "BvResult",
    "DIMENSIONS",
    "DIRECTION_PROMPT_ID",
    "DirectionResearch",
    "PIN_FORMULA",
    "QualityResult",
    "RUBRIC_ID",
    "RUBRIC_V1",
    "Rubric",
    "WEIGHT_TOTAL",
    "content_hash",
    "brief_reject_reason",
    "build_research_prompt",
    "bv_inflight_fresh",
    "ensure_quality",
    "from_row",
    "is_quality_current",
    "label_reject_reason",
    "needs_bv_rescore",
    "needs_quality_refresh",
    "parse_research",
    "quality_fingerprint",
    "reading_heuristic",
    "research_to_rubric",
    "recompute_quality_if_pinned",
    "score_general_bv",
    "score_quality",
    "usable_research",
    "write_bv",
    "write_quality",
]
