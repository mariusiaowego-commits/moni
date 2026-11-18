"""Local, model-free scores.

``pin_heuristic_v3`` answers "is this draft complete enough to work with?" on
a 0–5 scale (one decimal).  Every term is a multiple of 0.1, so a second
decimal would always be zero.  The five-point form exists so the Pin column can
never be confused with the 0–100 business-value score or BestBlogs ``bb_score``.
``reading_heuristic`` is the legacy completeness/attention score used by the
brief card when no BestBlogs score exists.  They share the length tiers but
must not call each other: the Pin column and the brief card answer different
questions.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .snapshot import ArticleSnapshot

PIN_FORMULA = "pin_heuristic_v3"
PIN_MAX = 5.0
PIN_BASE = 2.5
THIN_CHARS = 200
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_PARA_SPLIT_RE = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class QualityResult:
    score: float | None
    status: str | None
    formula: str = PIN_FORMULA
    breakdown: dict | None = None
    error: str | None = None
    input_hash: str = ""


def _length_tier(n: int) -> tuple[int, float]:
    """Length tier as (0–100 reading points, 0–5 Pin points)."""
    if n >= 5000:
        return 25, 1.4
    if n >= 2000:
        return 18, 1.0
    if n >= 800:
        return 10, 0.6
    if n >= THIN_CHARS:
        return 4, 0.2
    return -20, -1.1


def _length_delta(n: int) -> int:
    """Reading scale: shared with the brief card (0–100)."""
    return _length_tier(n)[0]


def count_numbers(text: str) -> int:
    return len(_NUMBER_RE.findall(text))


def count_paragraphs(text: str) -> int:
    return sum(1 for part in _PARA_SPLIT_RE.split(text) if part.strip())


def score_quality(snap: ArticleSnapshot) -> QualityResult:
    body = snap.body
    n = len(body)
    numbers = count_numbers(body)
    paragraphs = count_paragraphs(body)
    number_pts = 0.4 if numbers >= 3 else (0.2 if numbers >= 1 else 0.0)
    para_pts = 0.2 if paragraphs >= 4 else 0.0
    substance_pts = round(min(0.6, number_pts + para_pts), 1)
    publisher_pts = 0.3 if snap.publisher.strip() else 0.0
    published_pts = 0.2 if snap.published_at.strip() else 0.0
    length_pts = _length_tier(n)[1]

    total = PIN_BASE + length_pts + substance_pts + publisher_pts + published_pts
    total = round(max(0.0, min(PIN_MAX, total)), 1)

    detail_parts = []
    if number_pts:
        detail_parts.append(f"数字 {numbers} 处 +{number_pts}")
    if para_pts:
        detail_parts.append(f"分段 {paragraphs} 段 +{para_pts}")
    breakdown = {
        "formula": PIN_FORMULA,
        "scale": "0-5",
        "base": PIN_BASE,
        "total": total,
        "items": [
            {
                "key": "length",
                "label": "正文长度",
                "hit": length_pts > 0,
                "detail": f"{n} 字",
                "delta": round(length_pts, 1),
            },
            {
                "key": "substance",
                "label": "数字与结构",
                "hit": substance_pts > 0,
                "detail": "，".join(detail_parts),
                "delta": round(substance_pts, 1),
            },
            {
                "key": "publisher",
                "label": "发布方",
                "hit": publisher_pts > 0,
                "detail": "有发布方" if publisher_pts else "",
                "delta": round(publisher_pts, 1),
            },
            {
                "key": "published_at",
                "label": "发布时间",
                "hit": published_pts > 0,
                "detail": "有发布时间" if published_pts else "",
                "delta": round(published_pts, 1),
            },
        ],
    }
    return QualityResult(
        score=total,
        status="ready",
        breakdown=breakdown,
        input_hash=quality_fingerprint(snap),
    )


def quality_fingerprint(snap: ArticleSnapshot) -> str:
    """Hash of everything the formula reads.  Titles/summaries must not move it."""
    payload = "\n".join(
        [PIN_FORMULA, snap.body, snap.publisher, snap.published_at]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def reading_heuristic(snap: ArticleSnapshot) -> int:
    """Brief-card reading score: completeness + summary/fact-check presence.

    Deliberately ignores ``bb_score`` and any stored ``quality_score`` — the
    caller decides whether a BestBlogs score takes precedence.
    """
    score = 45 + _length_delta(snap.body_len)
    if snap.ai_summary.strip():
        score += 8
    if snap.fact_check_conclusion.strip():
        score += 7
    if snap.publisher.strip():
        score += 5
    if snap.published_at.strip():
        score += 3
    src = snap.source
    if src.startswith("bestblog"):
        score += 2
    elif src in ("mpaypass", "wechat_rss_a", "wechat_rss_b"):
        score += 1
    return max(0, min(100, score))


def is_quality_current(
    formula: str | None,
    input_hash: str | None,
    status: str | None,
    breakdown: str | None,
    snap: ArticleSnapshot,
) -> bool:
    """True when the stored row was produced by this formula for this body."""
    if formula != PIN_FORMULA:
        return False
    if status != "ready":
        return False
    if not breakdown:
        return False
    return input_hash == quality_fingerprint(snap)
