"""routes/reader — article reader conversations, prompts and SSE (Sprint 47)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from moni.db.connection import get_db
from moni.llm.reader_worker import cancel_reader_run, ensure_reader_worker_started
from moni.web.exceptions import BadRequestError, NotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reader", tags=["reader"])

_REASONING_LEVELS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}

_DEFAULT_PROMPTS = [
    ("rp-summary", "总结要点", "用 5 条 bullet 总结这篇文章最值得关注的观点。每条先给结论，再给一句原文依据。", 10),
    ("rp-structure", "结构拆解", "把文章的论证结构拆成：问题、证据、结论、隐含假设。指出哪一环最薄弱。", 20),
    ("rp-facts", "事实清单", "列出文章里的关键事实、数据、案例，并逐条说明它们分别证明了什么。没有出处的数字单独标出来。", 30),
    ("rp-verify", "待验证点", "这篇文章有哪些值得怀疑或需要验证的地方？按重要性排序，每条说明验证方法。", 40),
    ("rp-oppose", "反方观点", "站在反方立场，指出这篇文章最可能被挑战的 5 个点。只基于文中内容，不引入外部事实。", 50),
    ("rp-oneline", "一句话解释", "用一句话解释这篇文章的核心观点，再用三句话展开。不要复述标题。", 60),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


class PromptCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    body: str = Field(min_length=1, max_length=4000)
    scope: str = "article"


class PromptUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    body: str | None = Field(default=None, min_length=1, max_length=4000)
    scope: str | None = None


class ConversationCreate(BaseModel):
    article_id: str


class MessageCreate(BaseModel):
    text: str = Field(default="", max_length=8000)
    quote_text: str | None = Field(default=None, max_length=4000)
    prompt_id: str | None = None
    prompt_name: str | None = None
    reasoning: str = "low"


def _prompt_row(row: Any) -> dict[str, Any]:
    item = _row(row)
    if not item:
        return item
    item["is_builtin"] = bool(item.get("is_builtin"))
    return item


@router.get("/prompts")
def list_prompts() -> list[dict[str, Any]]:
    rows = get_db().execute(
        "SELECT * FROM reader_prompts ORDER BY sort_order ASC, updated_at DESC"
    ).fetchall()
    return [_prompt_row(r) for r in rows]


@router.post("/prompts")
def create_prompt(body: PromptCreate) -> dict[str, Any]:
    conn = get_db()
    now = _now()
    prompt_id = uuid.uuid4().hex
    try:
        conn.execute(
            "INSERT INTO reader_prompts (id, name, body, scope, is_builtin, sort_order, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 0, 100, ?, ?)",
            (prompt_id, body.name.strip(), body.body.strip(), body.scope, now, now),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — surface duplicate names as 400
        conn.rollback()
        raise BadRequestError(message=f"prompt name already exists: {body.name}") from exc
    return _prompt_row(conn.execute("SELECT * FROM reader_prompts WHERE id = ?", (prompt_id,)).fetchone())


@router.patch("/prompts/{prompt_id}")
def update_prompt(prompt_id: str, body: PromptUpdate) -> dict[str, Any]:
    conn = get_db()
    current = conn.execute("SELECT * FROM reader_prompts WHERE id = ?", (prompt_id,)).fetchone()
    if current is None:
        raise NotFoundError(message=f"prompt {prompt_id!r} not found")
    name = (body.name or current["name"]).strip()
    value = (body.body or current["body"]).strip()
    scope = body.scope or current["scope"]
    conn.execute(
        "UPDATE reader_prompts SET name = ?, body = ?, scope = ?, updated_at = ? WHERE id = ?",
        (name, value, scope, _now(), prompt_id),
    )
    conn.commit()
    return _prompt_row(conn.execute("SELECT * FROM reader_prompts WHERE id = ?", (prompt_id,)).fetchone())


@router.delete("/prompts/{prompt_id}")
def delete_prompt(prompt_id: str) -> dict[str, str]:
    conn = get_db()
    row = conn.execute("SELECT * FROM reader_prompts WHERE id = ?", (prompt_id,)).fetchone()
    if row is None:
        raise NotFoundError(message=f"prompt {prompt_id!r} not found")
    if row["is_builtin"]:
        raise BadRequestError(message="built-in prompts are editable but not deletable")
    conn.execute("DELETE FROM reader_prompts WHERE id = ?", (prompt_id,))
    conn.commit()
    return {"deleted": prompt_id}


@router.post("/prompts/restore")
def restore_prompts() -> list[dict[str, Any]]:
    conn = get_db()
    now = _now()
    for prompt_id, name, body, order in _DEFAULT_PROMPTS:
        conn.execute(
            "INSERT INTO reader_prompts (id, name, body, scope, is_builtin, sort_order, created_at, updated_at) "
            "VALUES (?, ?, ?, 'article', 1, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "body = excluded.body, is_builtin = 1, sort_order = excluded.sort_order, "
            "updated_at = excluded.updated_at "
            "WHERE reader_prompts.is_builtin = 1",
            (prompt_id, name, body, order, now, now),
        )
    conn.commit()
    return list_prompts()


def _latest_conversation(conn, article_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM reader_conversations WHERE article_id = ? "
        "ORDER BY updated_at DESC LIMIT 1",
        (article_id,),
    ).fetchone()
    return _row(row) if row else None


def _conversation_payload(conn, conversation: dict[str, Any]) -> dict[str, Any]:
    messages = conn.execute(
        "SELECT * FROM reader_messages WHERE conversation_id = ? ORDER BY created_at ASC",
        (conversation["id"],),
    ).fetchall()
    return {
        "conversation": conversation,
        "messages": [_row(m) for m in messages],
    }


@router.get("/conversations")
def get_conversation(article_id: str) -> dict[str, Any]:
    conn = get_db()
    conversation = _latest_conversation(conn, article_id)
    if conversation is None:
        return {"conversation": None, "messages": []}
    return _conversation_payload(conn, conversation)


@router.post("/conversations")
def create_conversation(body: ConversationCreate) -> dict[str, Any]:
    conn = get_db()
    article = conn.execute(
        "SELECT id, title FROM articles WHERE id = ?", (body.article_id,)
    ).fetchone()
    if article is None:
        raise NotFoundError(message=f"article {body.article_id!r} not found")
    conversation = _latest_conversation(conn, body.article_id)
    if conversation is None:
        now = _now()
        conversation = {
            "id": uuid.uuid4().hex,
            "article_id": body.article_id,
            "title": article["title"],
            "unread": 0,
            "created_at": now,
            "updated_at": now,
        }
        conn.execute(
            "INSERT INTO reader_conversations (id, article_id, title, unread, created_at, updated_at) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (conversation["id"], conversation["article_id"], conversation["title"], now, now),
        )
        conn.commit()
    return _conversation_payload(conn, conversation)


@router.get("/conversations/{conversation_id}")
def get_conversation_by_id(conversation_id: str) -> dict[str, Any]:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM reader_conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"conversation {conversation_id!r} not found")
    return _conversation_payload(conn, _row(row))


@router.post("/conversations/{conversation_id}/messages", status_code=202)
def create_message(conversation_id: str, body: MessageCreate) -> dict[str, Any]:
    conn = get_db()
    conversation = conn.execute(
        "SELECT * FROM reader_conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    if conversation is None:
        raise NotFoundError(message=f"conversation {conversation_id!r} not found")
    if not body.text.strip() and not body.prompt_id and not body.prompt_name:
        raise BadRequestError(message="text or prompt is required")

    prompt = None
    if body.prompt_id:
        prompt = conn.execute(
            "SELECT * FROM reader_prompts WHERE id = ?", (body.prompt_id,)
        ).fetchone()
    elif body.prompt_name:
        prompt = conn.execute(
            "SELECT * FROM reader_prompts WHERE name = ?", (body.prompt_name,)
        ).fetchone()
    if (body.prompt_id or body.prompt_name) and prompt is None:
        raise NotFoundError(message="prompt not found")
    prompt = _row(prompt)
    prompt_name = prompt.get("name") if prompt else None
    prompt_body = prompt.get("body") if prompt else ""
    user_text = body.text.strip() or prompt_body
    reasoning = body.reasoning if body.reasoning in _REASONING_LEVELS else "low"

    now = _now()
    user_id = uuid.uuid4().hex
    assistant_id = uuid.uuid4().hex
    job_id = uuid.uuid4().hex
    # llm_jobs first: reader_messages.job_id is a foreign key and
    # foreign_keys=ON, so the assistant row cannot land before the job.
    conn.execute(
        "INSERT INTO llm_jobs "
        "(id, task_type, article_id, provider, model, status, attempts, created_at, "
        " conversation_id, prompt_name) "
        "VALUES (?, 'reader_chat', ?, 'profile', '', 'pending', 0, ?, ?, ?)",
        (job_id, conversation["article_id"], now, conversation_id, prompt_name),
    )
    conn.execute(
        "INSERT INTO reader_messages "
        "(id, conversation_id, role, content, quote_text, status, created_at, updated_at) "
        "VALUES (?, ?, 'user', ?, ?, 'done', ?, ?)",
        (user_id, conversation_id, user_text, (body.quote_text or "").strip() or None, now, now),
    )
    conn.execute(
        "INSERT INTO reader_messages "
        "(id, conversation_id, role, content, quote_text, job_id, reasoning, status, created_at, updated_at) "
        "VALUES (?, ?, 'assistant', '', NULL, ?, ?, 'pending', ?, ?)",
        (assistant_id, conversation_id, job_id, reasoning, now, now),
    )
    conn.execute(
        "UPDATE reader_conversations SET updated_at = ? WHERE id = ?",
        (now, conversation_id),
    )
    conn.commit()
    ensure_reader_worker_started()
    return {
        "job_id": job_id,
        "user_message_id": user_id,
        "assistant_message_id": assistant_id,
        "status": "pending",
    }


@router.post("/conversations/{conversation_id}/read")
def mark_read(conversation_id: str) -> dict[str, Any]:
    conn = get_db()
    conn.execute(
        "UPDATE reader_conversations SET unread = 0 WHERE id = ?", (conversation_id,)
    )
    conn.commit()
    return {"conversation_id": conversation_id, "unread": 0}


@router.get("/unread-count")
def unread_count() -> dict[str, int]:
    row = get_db().execute(
        "SELECT COUNT(*) AS n FROM reader_conversations WHERE unread = 1"
    ).fetchone()
    return {"count": int(row["n"] or 0)}


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str) -> StreamingResponse:
    conn = get_db()
    job = conn.execute("SELECT * FROM llm_jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise NotFoundError(message=f"job {job_id!r} not found")
    message = conn.execute(
        "SELECT * FROM reader_messages WHERE job_id = ?", (job_id,)
    ).fetchone()
    if message is None:
        raise NotFoundError(message=f"reader message for job {job_id!r} not found")

    async def events() -> AsyncIterator[str]:
        sent = False
        prev = ""
        last_ping = time.monotonic()
        while True:
            row = get_db().execute(
                "SELECT content, status, error FROM reader_messages WHERE id = ?",
                (message["id"],),
            ).fetchone()
            content = row["content"] or ""
            status = row["status"]
            # Progress text is replaced, not appended. A shrink or a
            # non-prefix rewrite must reset the client instead of gluing
            # the next phase onto the previous one.
            if not sent or not content.startswith(prev):
                yield _sse("snapshot", {"content": content, "status": status})
                sent = True
                prev = content
            elif len(content) > len(prev):
                yield _sse("delta", {"text": content[len(prev):]})
                prev = content
            if status in {"done", "failed", "cancelled"}:
                final = get_db().execute(
                    "SELECT input_tokens, output_tokens, duration_ms, error "
                    "FROM llm_jobs WHERE id = ?",
                    (job_id,),
                ).fetchone()
                if status == "done":
                    yield _sse(
                        "done",
                        {
                            "message_id": message["id"],
                            "input_tokens": int(final["input_tokens"] or 0),
                            "output_tokens": int(final["output_tokens"] or 0),
                            "duration_ms": int(final["duration_ms"] or 0),
                        },
                    )
                else:
                    yield _sse(
                        "error",
                        {
                            "message_id": message["id"],
                            "status": status,
                            "message": row["error"] or final["error"] or status,
                        },
                    )
                return
            if time.monotonic() - last_ping > 15:
                last_ping = time.monotonic()
                yield _sse("ping", {"t": int(time.time())})
            await asyncio.sleep(0.15)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    job = get_db().execute(
        "SELECT id, task_type FROM llm_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if job is None:
        raise NotFoundError(message=f"job {job_id!r} not found")
    if job["task_type"] != "reader_chat":
        raise BadRequestError(message="not a reader job")
    cancelled = cancel_reader_run(job_id)
    return {"job_id": job_id, "cancelled": True, "process_killed": cancelled}
