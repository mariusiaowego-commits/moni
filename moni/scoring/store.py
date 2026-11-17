"""Persistence for scoring results.  The only module here that writes SQL."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from . import heuristic
from .bv_pipeline import BvResult
from .heuristic import QualityResult
from .snapshot import ArticleSnapshot, from_row

log = logging.getLogger(__name__)

QUALITY_FIELDS = (
    "quality_score",
    "quality_status",
    "quality_error",
    "quality_formula",
    "quality_breakdown_json",
    "quality_input_hash",
    "quality_score_at",
)

BV_FIELDS = (
    "business_value_score",
    "business_value_note",
    "business_value_status",
    "business_value_error",
    "business_value_breakdown_json",
    "business_value_hash",
    "business_value_rubric",
    "business_value_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_quality(conn: sqlite3.Connection, snap: ArticleSnapshot, result: QualityResult) -> None:
    conn.execute(
        "UPDATE articles SET quality_score=?, quality_status=?, quality_error=?, "
        "quality_formula=?, quality_breakdown_json=?, quality_input_hash=?, "
        "quality_score_at=? WHERE id=?",
        (
            result.score,
            result.status,
            result.error,
            result.formula,
            json.dumps(result.breakdown, ensure_ascii=False) if result.breakdown else None,
            result.input_hash,
            _now(),
            snap.id,
        ),
    )


def write_quality_failure(conn: sqlite3.Connection, article_id: str, error: str) -> None:
    conn.execute(
        "UPDATE articles SET quality_status=?, quality_error=?, quality_formula=?, "
        "quality_score_at=? WHERE id=?",
        ("failed", error[:500], heuristic.PIN_FORMULA, _now(), article_id),
    )


def ensure_quality(conn: sqlite3.Connection, article_id: str) -> QualityResult | None:
    """Score and persist quality for one article.  Never raises on DB errors."""
    try:
        row = conn.execute(
            "SELECT id, title, raw_content, publisher, published_at, source, "
            "ai_summary, fact_check_conclusion, url FROM articles WHERE id=?",
            (article_id,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        log.warning("scoring: quality read failed for %s: %s", article_id, exc)
        return None
    if row is None:
        return None
    snap = from_row(row)
    try:
        result = heuristic.score_quality(snap)
        write_quality(conn, snap, result)
        conn.commit()
        return result
    except (sqlite3.OperationalError, ValueError) as exc:
        log.warning("scoring: quality write failed for %s: %s", article_id, exc)
        try:
            write_quality_failure(conn, article_id, str(exc))
            conn.commit()
        except sqlite3.OperationalError:
            pass
        return None


def needs_quality_refresh(row: Mapping[str, Any] | sqlite3.Row) -> bool:
    try:
        formula = row["quality_formula"]
        status = row["quality_status"]
        has_breakdown = bool(row["quality_breakdown_json"])
        stored_hash = row["quality_input_hash"]
    except (KeyError, IndexError, TypeError):
        return True
    if formula != heuristic.PIN_FORMULA or status != "ready" or not has_breakdown:
        return True
    snap = from_row(row)
    return stored_hash != heuristic.quality_fingerprint(snap)


def recompute_quality_if_pinned(conn: sqlite3.Connection, article_id: str) -> None:
    """Call after any write to raw_content / publisher / published_at."""
    try:
        pinned = conn.execute(
            "SELECT 1 FROM article_pins WHERE article_id=?", (article_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        return
    if not pinned:
        return
    ensure_quality(conn, article_id)


def write_bv(conn: sqlite3.Connection, snap: ArticleSnapshot, result: BvResult) -> None:
    note = result.note or None
    conn.execute(
        "UPDATE articles SET business_value_score=?, business_value_note=?, "
        "business_value_status=?, business_value_error=?, business_value_breakdown_json=?, "
        "business_value_hash=?, business_value_rubric=?, business_value_at=? WHERE id=?",
        (
            result.total,
            note,
            result.status,
            result.error,
            json.dumps(result.breakdown, ensure_ascii=False) if result.breakdown else None,
            result.content_hash,
            (result.breakdown or {}).get("rubric"),
            _now(),
            snap.id,
        ),
    )


def needs_bv_rescore(row: Mapping[str, Any] | sqlite3.Row, snap: ArticleSnapshot) -> bool:
    """Cache-hit test: status + rubric + body hash must all agree."""
    try:
        status = row["business_value_status"]
        stored_hash = row["business_value_hash"]
    except (KeyError, IndexError, TypeError):
        return True
    try:
        stored_rubric = row["business_value_rubric"]
    except (KeyError, IndexError, TypeError):
        stored_rubric = None
    if status in (None, "", "failed", "budget_skipped", "pending"):
        return True
    from .bv_pipeline import content_hash
    from .rubric import RUBRIC_V1

    if stored_rubric and stored_rubric != RUBRIC_V1.id:
        return True
    return stored_hash != content_hash(snap.body)
