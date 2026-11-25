"""On-demand general business-value scoring API.

Scoring is explicit: nothing here runs unless the user asks for it.
``POST`` queues one article and returns immediately; the worker scores it
in the background and the client polls ``GET`` until the status leaves
``queued``/``running``.

The score itself comes from the same harness the share pipeline uses
(``moni.scoring.bv_pipeline.score_general_bv``); this module only owns
the queue and the read shape.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from fastapi import APIRouter, Query

from moni.db.connection import get_db
from moni.scoring import bv_runner, store
from moni.scoring.bv_pipeline import content_hash, short_llm_error
from moni.scoring.snapshot import from_row
from moni.web.exceptions import NotFoundError
from moni.web.models import BusinessValueQueued, BusinessValueState

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/articles", tags=["articles"])

_runner: bv_runner.GeneralBvRunner | None = None
_runner_lock = threading.Lock()
#: Serialises claim vs. stale recovery, for the same reason the direction
#: score route needs it: a recovery pass must not fail a row another
#: request just queued.
_enqueue_lock = threading.Lock()


def _llm_call(article_id: str):
    """Adapter for the harness' ``llm(prompt, timeout) -> (text, code, error)``.

    Outbound text goes through the same desensitisation scope the share
    pipeline uses, so the two paths cannot disagree about what leaves the
    machine.
    """

    def call(prompt: str, timeout: float) -> tuple[str, int, str]:
        from moni.llm.client import call_llm, resolve_active_model
        from moni.llm.desensitize.audit import desensitize_scope
        from moni.llm.runner import read_model_mode

        if read_model_mode() == "profile":
            provider, model = "", ""
        else:
            provider, model = resolve_active_model()
        try:
            with desensitize_scope(
                task_type="business_value", article_id=article_id, json_output=True
            ):
                out = call_llm(
                    provider, model, prompt, timeout=max(15, int(round(timeout)))
                )
        except Exception as exc:  # noqa: BLE001 - provider failures are data here
            log.warning("general bv llm failed for %s: %s", article_id, exc)
            return "", 1, short_llm_error(str(exc))
        if not isinstance(out, str):
            return "", 1, "模型返回了非文本内容"
        return out, 0, ""

    return call


def _ensure_runner() -> bv_runner.GeneralBvRunner:
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = bv_runner.GeneralBvRunner(_llm_call)
        return _runner


def _row_or_404(article_id: str):
    conn = get_db()
    row = bv_runner.load_row(conn, article_id)
    if row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")
    return row


def _state(row: Any) -> dict[str, Any]:
    raw = row["business_value_breakdown_json"] if "business_value_breakdown_json" in row.keys() else None
    breakdown = store.public_breakdown(json.loads(raw)) if raw else None
    stored_hash = row["business_value_hash"]
    current_hash = content_hash(str(row["raw_content"] or ""))
    return {
        "article_id": row["id"],
        "status": row["business_value_status"],
        "score": row["business_value_score"],
        "note": row["business_value_note"] or "",
        "error": row["business_value_error"],
        "stale": bool(stored_hash) and stored_hash != current_hash,
        "breakdown": breakdown,
    }


def _state_row(conn, article_id: str):
    return conn.execute(
        "SELECT id, raw_content, business_value_status, business_value_score, "
        "business_value_note, business_value_error, business_value_hash, "
        "business_value_breakdown_json FROM articles WHERE id=?",
        (article_id,),
    ).fetchone()


@router.get("/{article_id}/business-value", response_model=BusinessValueState)
def get_business_value(article_id: str) -> BusinessValueState:
    _row_or_404(article_id)
    conn = get_db()
    row = _state_row(conn, article_id)
    return BusinessValueState(**_state(row))


@router.post(
    "/{article_id}/business-value",
    response_model=BusinessValueQueued,
    status_code=202,
)
def start_business_value(
    article_id: str,
    force: bool = Query(False, description="true = 正文没变也重打"),
) -> BusinessValueQueued:
    """Queue one article.  A fresh score is returned as ``cached``."""
    row = _row_or_404(article_id)
    conn = get_db()
    runner = _ensure_runner()
    with _enqueue_lock:
        bv_runner.recover_stale(conn, live_ids=runner.live_ids)
        row = bv_runner.load_row(conn, article_id)
        if row is None:
            raise NotFoundError(message=f"Article {article_id!r} not found")
        if bv_runner.is_inflight(row) and article_id in runner.live_ids:
            return BusinessValueQueued(
                article_id=article_id,
                status=row["business_value_status"],
                running=True,
                reason="这篇正在打分",
            )
        snap = from_row(row)
        if not force and not store.needs_bv_rescore(row, snap):
            return BusinessValueQueued(
                article_id=article_id,
                status=row["business_value_status"],
                cached=True,
                reason="正文没变，沿用已有分数",
            )
        if not bv_runner.mark_queued(conn, article_id):
            return BusinessValueQueued(
                article_id=article_id,
                status=bv_runner.STATUS_QUEUED,
                running=True,
                reason="这篇正在打分",
            )
        runner.submit([article_id])
    return BusinessValueQueued(
        article_id=article_id, status=bv_runner.STATUS_QUEUED, queued=True
    )
