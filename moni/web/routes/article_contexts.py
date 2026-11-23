"""routes/article_contexts — user-curated context snapshots (Sprint 70).

A context is a frozen copy of an assistant answer (plus the question that
produced it), attached to the article rather than the conversation.  Deleting
the conversation must leave the snapshot intact, so ``source_message_id`` is
informational only and has no FK.

Endpoints
---------
* ``GET    /api/articles/{article_id}/contexts``
* ``POST   /api/articles/{article_id}/contexts``
* ``DELETE /api/articles/{article_id}/contexts/{context_id}``
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from moni.db.connection import get_db
from moni.web.exceptions import BadRequestError, NotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["article-contexts"])

_KINDS = {"chat", "note", "quote", "summary", "fact_check"}
_TITLE_CHARS = 40


class ContextCreate(BaseModel):
    message_id: str = Field(min_length=1, max_length=64)
    kind: str = Field(default="chat", max_length=20)
    title: str | None = Field(default=None, max_length=120)


class ContextOut(BaseModel):
    id: str
    article_id: str
    kind: str
    title: str | None
    body: str
    source_message_id: str | None
    source_question: str | None
    quoted_text: str | None
    created_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _context_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    return {key: item.get(key) for key in ContextOut.model_fields}


def _default_title(body: str) -> str:
    first = next((line.strip() for line in body.splitlines() if line.strip()), "")
    return first[: _TITLE_CHARS - 1] + "…" if len(first) > _TITLE_CHARS else first


def _require_article(conn: Any, article_id: str) -> None:
    row = conn.execute("SELECT id FROM articles WHERE id = ?", (article_id,)).fetchone()
    if row is None:
        raise NotFoundError(message=f"article {article_id!r} not found")


@router.get("/article-contexts/summary")
def context_summary(article_ids: str = "") -> dict[str, Any]:
    """Counts per article, for the share dialog's "N 条" badge."""
    conn = get_db()
    ids = [x.strip() for x in article_ids.split(",") if x.strip()]
    sql = "SELECT article_id, COUNT(*) AS n FROM article_contexts"
    params: list[Any] = []
    if ids:
        sql += f" WHERE article_id IN ({','.join('?' * len(ids))})"
        params = ids
    sql += " GROUP BY article_id"
    counts = {
        str(row["article_id"]): int(row["n"])
        for row in conn.execute(sql, params).fetchall()
    }
    pinned_total = conn.execute(
        "SELECT COUNT(*) FROM article_contexts c "
        "JOIN article_pins p ON p.article_id = c.article_id"
    ).fetchone()[0]
    return {
        "counts": counts,
        "total": sum(counts.values()),
        "pinned_total": int(pinned_total),
    }


@router.get("/articles/{article_id}/contexts")
def list_contexts(article_id: str) -> dict[str, Any]:
    conn = get_db()
    _require_article(conn, article_id)
    rows = conn.execute(
        "SELECT * FROM article_contexts WHERE article_id = ? "
        "ORDER BY sort_order ASC, created_at ASC",
        (article_id,),
    ).fetchall()
    contexts = [_context_row(row) for row in rows]
    return {"article_id": article_id, "count": len(contexts), "contexts": contexts}


@router.post("/articles/{article_id}/contexts", status_code=201)
def create_context(
    article_id: str, body: ContextCreate, response: Response
) -> dict[str, Any]:
    conn = get_db()
    _require_article(conn, article_id)
    kind = body.kind if body.kind in _KINDS else "chat"

    message = conn.execute(
        "SELECT m.*, c.article_id AS conversation_article_id "
        "FROM reader_messages m "
        "JOIN reader_conversations c ON c.id = m.conversation_id "
        "WHERE m.id = ?",
        (body.message_id,),
    ).fetchone()
    if message is None:
        raise NotFoundError(message=f"message {body.message_id!r} not found")
    if str(message["conversation_article_id"]) != article_id:
        raise BadRequestError(message="message belongs to a different article")
    if message["role"] != "assistant":
        raise BadRequestError(message="only assistant answers can be saved")
    if message["status"] != "done":
        raise BadRequestError(message="answer is not finished yet")
    content = str(message["content"] or "").strip()
    if not content:
        raise BadRequestError(message="answer is empty")

    existing = conn.execute(
        "SELECT * FROM article_contexts "
        "WHERE article_id = ? AND source_message_id = ?",
        (article_id, body.message_id),
    ).fetchone()
    if existing is not None:
        response.status_code = 200
        return {"created": False, "context": _context_row(existing)}

    question = conn.execute(
        "SELECT content, quote_text FROM reader_messages "
        "WHERE conversation_id = ? AND role = 'user' AND created_at <= ? "
        "ORDER BY created_at DESC LIMIT 1",
        (message["conversation_id"], message["created_at"]),
    ).fetchone()
    next_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 10 FROM article_contexts "
        "WHERE article_id = ?",
        (article_id,),
    ).fetchone()[0]
    now = _now()
    context_id = uuid.uuid4().hex
    title = (body.title or "").strip() or _default_title(content)
    try:
        conn.execute(
            "INSERT INTO article_contexts "
            "(id, article_id, kind, title, body, source_message_id, source_question, "
            " quoted_text, sort_order, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                context_id,
                article_id,
                kind,
                title,
                content,
                body.message_id,
                (question["content"] if question else None),
                (question["quote_text"] if question else None),
                next_order,
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError:
        # Concurrent double-click: the unique index wins the race.  Re-read
        # the winner so the endpoint stays idempotent instead of returning 500.
        conn.rollback()
        existing = conn.execute(
            "SELECT * FROM article_contexts "
            "WHERE article_id = ? AND source_message_id = ?",
            (article_id, body.message_id),
        ).fetchone()
        if existing is None:
            raise
        response.status_code = 200
        return {"created": False, "context": _context_row(existing)}
    conn.commit()
    row = conn.execute(
        "SELECT * FROM article_contexts WHERE id = ?", (context_id,)
    ).fetchone()
    return {"created": True, "context": _context_row(row)}


@router.delete("/articles/{article_id}/contexts/{context_id}")
def delete_context(article_id: str, context_id: str) -> dict[str, Any]:
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM article_contexts WHERE id = ? AND article_id = ?",
        (context_id, article_id),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"context {context_id!r} not found")
    conn.execute("DELETE FROM article_contexts WHERE id = ?", (context_id,))
    conn.commit()
    return {"deleted": True, "context_id": context_id}
