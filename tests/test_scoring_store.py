"""Cache-hit rules for the two score families (design §4.4, §6.5)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from moni.scoring import (
    bv_inflight_fresh,
    content_hash,
    heuristic,
    needs_bv_rescore,
    needs_quality_refresh,
    quality_fingerprint,
)
from moni.scoring.rubric import RUBRIC_V1
from moni.scoring.snapshot import ArticleSnapshot


def _snap(body: str = "正文内容足够长，用于哈希测试。") -> ArticleSnapshot:
    return ArticleSnapshot(id="a1", raw_content=body, publisher="某报", published_at="2026-01-01")


def test_quality_refresh_only_when_formula_or_fingerprint_drift():
    snap = _snap()
    row = {
        "quality_formula": heuristic.PIN_FORMULA,
        "quality_status": "ready",
        "quality_breakdown_json": "{}",
        "quality_input_hash": quality_fingerprint(snap),
        "raw_content": snap.raw_content,
        "publisher": snap.publisher,
        "published_at": snap.published_at,
    }
    assert needs_quality_refresh(row) is False
    assert needs_quality_refresh({**row, "quality_formula": None}) is True
    assert needs_quality_refresh({**row, "quality_input_hash": "old"}) is True
    assert needs_quality_refresh({**row, "quality_status": "failed"}) is True


def test_bv_rescore_rejects_stale_rubric_and_hash():
    snap = _snap()
    row = {
        "business_value_status": "ready",
        "business_value_hash": content_hash(snap.body),
        "business_value_rubric": RUBRIC_V1.id,
    }
    assert needs_bv_rescore(row, snap) is False
    assert needs_bv_rescore({**row, "business_value_rubric": "bv_rubric_old"}, snap) is True
    assert needs_bv_rescore({**row, "business_value_hash": "old"}, snap) is True
    assert needs_bv_rescore({**row, "business_value_status": "budget_skipped"}, snap) is True


def test_bv_rescore_waits_for_a_live_job_and_takes_over_a_dead_one():
    snap = _snap()
    now = datetime.now(timezone.utc)
    base = {
        "business_value_status": "queued",
        "business_value_hash": content_hash(snap.body),
        "business_value_at": now.isoformat(),
    }
    assert bv_inflight_fresh(base) is True
    assert needs_bv_rescore(base, snap) is False

    dead = {**base, "business_value_at": (now - timedelta(seconds=900)).isoformat()}
    assert bv_inflight_fresh(dead) is False
    assert needs_bv_rescore(dead, snap) is True

    # A queued row without a timestamp belongs to a dead process.
    assert bv_inflight_fresh({**base, "business_value_at": None}) is False
    assert needs_bv_rescore({**base, "business_value_at": None}, snap) is True
    assert bv_inflight_fresh({**base, "business_value_status": "ready"}) is False
