"""LLM-powered routes: generate-summary and fact-check."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

# Default model; override with MONI_LLM_MODEL env var
_DEFAULT_MODEL = os.environ.get("MONI_LLM_MODEL", "claude-3-5-sonnet-latest")

from fastapi import APIRouter, HTTPException, status

from moni.db.repository import ArticleRepository
from moni.llm import ChatClient
from moni.llm.prompts import render_fact_check, render_summary
from moni.web.exceptions import BadRequestError, NotFoundError
from moni.web.models import ArticleSummaryRequest, FactCheckRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/llm", tags=["llm"])

_repo = ArticleRepository()
_client: ChatClient | None = None


def _get_client() -> ChatClient:
    global _client
    if _client is None:
        _client = ChatClient.get_instance()
    return _client


# ----------------------------------------------------------------------
# POST /llm/summary  (item 7)
# ----------------------------------------------------------------------


@router.post("/summary")
def generate_summary(req: ArticleSummaryRequest) -> dict:
    """Generate a summary for an article via LLM.

    Writes the result back to ``articles.summary`` and
    ``articles.summary_at`` in the database.
    """
    article = _repo.get_by_id(req.article_id)
    if article is None:
        raise NotFoundError(message=f"Article {req.article_id!r} not found")

    raw = article.get("raw_content") or article.get("content") or ""
    if not raw.strip():
        raise BadRequestError(message="Article has no content to summarise")

    prompt = render_summary(article)

    try:
        client = _get_client()
        summary_text = client.chat(prompt, model=_DEFAULT_MODEL, max_tokens=512)
    except Exception as exc:
        logger.error("LLM summary call failed for %s: %s", req.article_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM call failed: {exc}",
        )

    now = datetime.now(timezone.utc).isoformat()
    _repo.update_summary(req.article_id, summary_text.strip(), now)

    logger.info("Summary generated for article %s", req.article_id)
    return {"article_id": req.article_id, "summary": summary_text.strip(), "at": now}


# ----------------------------------------------------------------------
# POST /llm/fact-check  (item 8)
# ----------------------------------------------------------------------


@router.post("/fact-check")
def fact_check(req: FactCheckRequest) -> dict:
    """Run LLM fact-check on an article.

    Writes the result back to ``articles.fact_check_*`` fields.
    """
    article = _repo.get_by_id(req.article_id)
    if article is None:
        raise NotFoundError(message=f"Article {req.article_id!r} not found")

    raw = article.get("raw_content") or article.get("content") or ""
    if not raw.strip():
        raise BadRequestError(message="Article has no content to fact-check")

    prompt = render_fact_check(article)

    try:
        client = _get_client()
        result_text = client.chat(prompt, model=_DEFAULT_MODEL, max_tokens=1024)
    except Exception as exc:
        logger.error("LLM fact-check call failed for %s: %s", req.article_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM call failed: {exc}",
        )

    now = datetime.now(timezone.utc).isoformat()
    _repo.update_fact_check(
        req.article_id,
        conclusion=result_text.strip(),
        method="llm_inference",
        checked_at=now,
    )

    logger.info("Fact-check completed for article %s", req.article_id)
    return {"article_id": req.article_id, "result": result_text.strip(), "at": now}


# ----------------------------------------------------------------------
# GET /llm/token-log  (item 12 — inspect logs)
# ----------------------------------------------------------------------


@router.get("/token-log")
def get_token_log(limit: int = 50) -> dict:
    """Return the most recent token-usage log entries.

    Each entry contains: ts, model, prompt_tokens, completion_tokens,
    total_tokens, cost_usd, provider.
    """
    from pathlib import Path

    log_path = Path("data") / "llm_usage.log"
    if not log_path.exists():
        return {"entries": [], "total": 0}

    lines: list[dict] = []
    with log_path.open(encoding="utf-8") as fh:
        # Read last `limit` lines
        all_lines = fh.readlines()
        for line in all_lines[-limit:]:
            line = line.strip()
            if line:
                import json
                try:
                    lines.append(json.loads(line))
                except Exception:
                    pass

    return {"entries": lines, "total": len(lines)}
