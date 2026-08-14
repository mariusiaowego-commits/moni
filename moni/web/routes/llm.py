"""LLM-powered routes: generate-summary and fact-check."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

# Default model; override with MONI_LLM_MODEL env var
_DEFAULT_MODEL = os.environ.get("MONI_LLM_MODEL", "claude-3-5-sonnet-latest")

from fastapi import APIRouter, HTTPException, status

from moni.db.repository import ArticleRepository
from moni.db.connection import get_db
from moni.llm import ChatClient
from moni.llm.prompts import render_summary
from moni.web.exceptions import BadRequestError, ConflictError, NotFoundError
from moni.web.models import ArticleSummaryRequest

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

    # v1.0.5.24: get article tags for perspective selection
    conn = get_db()
    tag_rows = conn.execute(
        "SELECT tag_name FROM article_tags WHERE article_id = ?",
        (req.article_id,),
    ).fetchall()
    tags = [r["tag_name"] for r in tag_rows]

    prompt = render_summary(article, tags=tags)

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
# POST /llm/fact-check  — removed v1.0.22 (dead code; no frontend callers;
# model resolution ignored llm_settings; use async
# POST /api/articles/{id}/fact-check or /fact-check-plus instead).
# ChatClient shim kept above for /summary compatibility.
# ----------------------------------------------------------------------


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


# ==========================================================================
# v1.0.5.20: Provider / Model / Settings / Usage CRUD
# ==========================================================================


# ── Pydantic request models ─────────────────────────────────────────────

from pydantic import BaseModel, ConfigDict, Field
from typing import Any


class _ExtraForbid(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderCreate(_ExtraForbid):
    name: str = Field(..., min_length=1, max_length=100)
    hermes_provider: str = Field(..., min_length=1, max_length=100)
    notes: str = Field(default="")


class ProviderUpdate(_ExtraForbid):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    hermes_provider: str | None = Field(default=None, min_length=1, max_length=100)
    notes: str | None = None
    is_active: int | None = Field(default=None, ge=0, le=1)


class ModelCreate(_ExtraForbid):
    provider_id: str = Field(..., min_length=1)
    model_id: str = Field(..., min_length=1, max_length=100)
    display_name: str = Field(default="", max_length=100)
    use_for: list[str] = Field(default=["summary"])


class ModelUpdate(_ExtraForbid):
    display_name: str | None = Field(default=None, max_length=100)
    enabled: int | None = Field(default=None, ge=0, le=1)


class SettingsUpdate(_ExtraForbid):
    """Partial update — only provided keys are written."""
    active_summary_model_id: str | None = None
    active_fact_check_model_id: str | None = None
    active_tag_model_id: str | None = None
    temperature_summary: float | None = None
    temperature_fact_check: float | None = None
    max_tokens_summary: int | None = None
    max_tokens_fact_check: int | None = None


# ── Provider CRUD ────────────────────────────────────────────────────────


def _row_to_dict(row) -> dict:
    """Convert sqlite3.Row to plain dict."""
    return dict(row) if row else {}


@router.get("/providers")
def list_providers() -> dict:
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, hermes_provider, is_active, notes, created_at, updated_at "
        "FROM llm_providers ORDER BY name"
    ).fetchall()
    return {"items": [_row_to_dict(r) for r in rows]}


@router.post("/providers", status_code=201)
def create_provider(body: ProviderCreate) -> dict:
    import secrets
    conn = get_db()
    provider_id = f"provider-{secrets.token_hex(8)}"
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO llm_providers (id, name, hermes_provider, is_active, notes, created_at, updated_at) "
        "VALUES (?, ?, ?, 1, ?, ?, ?)",
        (provider_id, body.name, body.hermes_provider, body.notes, now, now),
    )
    conn.commit()
    logger.info("provider created: %s (%s)", provider_id, body.name)
    row = conn.execute("SELECT * FROM llm_providers WHERE id=?", (provider_id,)).fetchone()
    return _row_to_dict(row)


@router.put("/providers/{provider_id}")
def update_provider(provider_id: str, body: ProviderUpdate) -> dict:
    conn = get_db()
    existing = conn.execute("SELECT * FROM llm_providers WHERE id=?", (provider_id,)).fetchone()
    if not existing:
        raise NotFoundError(message=f"Provider {provider_id!r} not found")

    updates = []
    params = []
    for field_name in ("name", "hermes_provider", "notes", "is_active"):
        val = getattr(body, field_name, None)
        if val is not None:
            updates.append(f"{field_name}=?")
            params.append(val)

    if not updates:
        return _row_to_dict(existing)

    updates.append("updated_at=?")
    params.append(datetime.now(timezone.utc).isoformat())
    params.append(provider_id)

    conn.execute(
        f"UPDATE llm_providers SET {', '.join(updates)} WHERE id=?",
        params,
    )
    conn.commit()
    row = conn.execute("SELECT * FROM llm_providers WHERE id=?", (provider_id,)).fetchone()
    return _row_to_dict(row)


@router.delete("/providers/{provider_id}", status_code=204)
def delete_provider(provider_id: str) -> None:
    conn = get_db()
    existing = conn.execute("SELECT * FROM llm_providers WHERE id=?", (provider_id,)).fetchone()
    if not existing:
        raise NotFoundError(message=f"Provider {provider_id!r} not found")

    model_count = conn.execute(
        "SELECT COUNT(*) FROM llm_models WHERE provider_id=?", (provider_id,)
    ).fetchone()[0]
    if model_count > 0:
        raise ConflictError(
            message=f"Provider has {model_count} model(s), delete them first",
            details={"model_count": model_count},
        )

    conn.execute("DELETE FROM llm_providers WHERE id=?", (provider_id,))
    conn.commit()
    logger.info("provider deleted: %s", provider_id)


@router.post("/providers/{provider_id}/test")
def test_provider(provider_id: str) -> dict:
    """Test provider connectivity with a minimal LLM call.

    Uses the provider's first enabled model and a very short prompt
    with max_tokens=1 to minimize token usage (~5-10 tokens total).
    """
    import time as _time
    conn = get_db()
    provider = conn.execute(
        "SELECT * FROM llm_providers WHERE id=?", (provider_id,)
    ).fetchone()
    if not provider:
        raise NotFoundError(message=f"Provider {provider_id!r} not found")

    hermes_provider = provider["hermes_provider"]

    # Find the first enabled model for this provider
    model = conn.execute(
        "SELECT model_id FROM llm_models WHERE provider_id=? AND enabled=1 "
        "ORDER BY created_at LIMIT 1",
        (provider_id,),
    ).fetchone()

    if model:
        model_id = model["model_id"]
    else:
        # No model configured — try common defaults
        defaults = {
            "google": "gemini-2.5-flash-lite",
            "minimax-cn": "MiniMax-M3",
            "xiaomi": "mimo-v2.5-pro",
            "deepseek": "deepseek-chat",
            "openrouter": "google/gemini-2.5-flash-lite",
        }
        model_id = defaults.get(hermes_provider, "")
        if not model_id:
            return {
                "ok": False,
                "provider": hermes_provider,
                "model": "",
                "error": "No enabled model found for this provider",
                "duration_ms": 0,
            }

    # Minimal prompt — just "hi" to test connectivity
    t0 = _time.monotonic()
    try:
        from moni.llm.client import call_llm
        # v1.0.31 (sprint-12 D): 不再传 module kwarg — call_llm 是公共 API,
        # 没有这个参数 (修复前 TypeError → 连通测试永远 ok=False)。
        reply = call_llm(
            hermes_provider, model_id, "hi",
            timeout=15,
        )
        duration_ms = int((_time.monotonic() - t0) * 1000)
        return {
            "ok": True,
            "provider": hermes_provider,
            "model": model_id,
            "reply_preview": reply[:100],
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = int((_time.monotonic() - t0) * 1000)
        return {
            "ok": False,
            "provider": hermes_provider,
            "model": model_id,
            "error": str(exc)[:200],
            "duration_ms": duration_ms,
        }


# ── Model CRUD ───────────────────────────────────────────────────────────


@router.get("/models")
def list_models() -> dict:
    conn = get_db()
    rows = conn.execute(
        """SELECT m.id, m.provider_id, m.model_id, m.display_name,
                  m.use_for, m.enabled, m.created_at,
                  p.name AS provider_name, p.hermes_provider
           FROM llm_models m
           LEFT JOIN llm_providers p ON p.id = m.provider_id
           ORDER BY p.name, m.display_name"""
    ).fetchall()
    return {"items": [_row_to_dict(r) for r in rows]}


@router.post("/models", status_code=201)
def create_model(body: ModelCreate) -> dict:
    import json as _json
    import secrets
    conn = get_db()

    # Validate provider exists
    provider = conn.execute("SELECT id FROM llm_providers WHERE id=?", (body.provider_id,)).fetchone()
    if not provider:
        raise NotFoundError(message=f"Provider {body.provider_id!r} not found")

    model_id = f"model-{body.provider_id}-{body.model_id}"
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO llm_models (id, provider_id, model_id, display_name, use_for, enabled, created_at) "
        "VALUES (?, ?, ?, ?, ?, 1, ?)",
        (model_id, body.provider_id, body.model_id, body.display_name, _json.dumps(body.use_for), now),
    )
    conn.commit()
    logger.info("model created: %s (%s)", model_id, body.model_id)
    row = conn.execute(
        "SELECT m.*, p.name AS provider_name FROM llm_models m "
        "LEFT JOIN llm_providers p ON p.id = m.provider_id WHERE m.id=?",
        (model_id,),
    ).fetchone()
    return _row_to_dict(row)


@router.put("/models/{model_id:path}")
def update_model(model_id: str, body: ModelUpdate) -> dict:
    conn = get_db()
    existing = conn.execute("SELECT * FROM llm_models WHERE id=?", (model_id,)).fetchone()
    if not existing:
        raise NotFoundError(message=f"Model {model_id!r} not found")

    updates = []
    params = []
    for field_name in ("display_name", "enabled"):
        val = getattr(body, field_name, None)
        if val is not None:
            updates.append(f"{field_name}=?")
            params.append(val)

    if not updates:
        return _row_to_dict(existing)

    params.append(model_id)
    conn.execute(
        f"UPDATE llm_models SET {', '.join(updates)} WHERE id=?",
        params,
    )
    conn.commit()
    row = conn.execute("SELECT * FROM llm_models WHERE id=?", (model_id,)).fetchone()
    return _row_to_dict(row)


# ── Settings ─────────────────────────────────────────────────────────────


@router.get("/settings")
def get_settings() -> dict:
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM llm_settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


@router.put("/settings")
def update_settings(body: SettingsUpdate) -> dict:
    conn = get_db()
    data = body.model_dump(exclude_none=True)
    if not data:
        raise BadRequestError(message="No settings to update")

    for key, value in data.items():
        conn.execute(
            "INSERT OR REPLACE INTO llm_settings (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
    conn.commit()
    logger.info("settings updated: %s", list(data.keys()))
    rows = conn.execute("SELECT key, value FROM llm_settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


# ── Usage Statistics ─────────────────────────────────────────────────────


@router.get("/usage")
def get_usage(
    days: int = 30,
    group_by: str = "module",
) -> dict:
    """Aggregate token usage from llm_usage.log.

    Query params:
        days: Number of days to look back (-1 = all time).
        group_by: "module", "model", or "date".
    """
    from moni.llm.usage import aggregate, parse_log

    if group_by not in ("module", "model", "date"):
        raise BadRequestError(
            message=f"group_by must be 'module', 'model', or 'date', got {group_by!r}"
        )

    entries = parse_log()
    return aggregate(entries, days=days, group_by=group_by)
