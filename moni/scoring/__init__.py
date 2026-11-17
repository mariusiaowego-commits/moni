"""Scoring core: Pin quality (local) + business-value harness (model-assisted).

Public surface used by ``moni.share`` and ``moni.web``:

    from moni.scoring import (
        ArticleSnapshot, from_row, score_quality, reading_heuristic,
        score_general_bv, ensure_quality, needs_quality_refresh,
        recompute_quality_if_pinned, needs_bv_rescore, write_bv,
    )
"""
from .bv_pipeline import BvResult, content_hash, score_general_bv
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
    "PIN_FORMULA",
    "QualityResult",
    "RUBRIC_ID",
    "RUBRIC_V1",
    "Rubric",
    "WEIGHT_TOTAL",
    "content_hash",
    "ensure_quality",
    "from_row",
    "is_quality_current",
    "needs_bv_rescore",
    "needs_quality_refresh",
    "quality_fingerprint",
    "reading_heuristic",
    "recompute_quality_if_pinned",
    "score_general_bv",
    "score_quality",
    "write_bv",
    "write_quality",
]
