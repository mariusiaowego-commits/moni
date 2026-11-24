"""Dedicated worker for reader_chat LLM jobs (Sprint 47).

The old summary/fact-check worker is single-threaded.  A reader reply can
stream for minutes, so it must never sit in that queue.  This module
claims only ``task_type='reader_chat'`` rows and runs two workers.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any

from moni.db import leases
from moni.db.connection import get_db
from moni.llm.desensitize.audit import desensitize_scope
from moni.llm.reader import (
    build_chunk_prompt,
    build_merge_prompt,
    build_reader_prompt,
    choose_reader_mode,
    chunks_for_question,
    clean_article_text,
    ensure_chunks,
    group_chunk_texts,
    load_chunk_rows,
    outline_chunks,
    run_reader_stream,
)

logger = logging.getLogger(__name__)

_WORKER_THREADS: list[threading.Thread] = []
_WORKER_LOCK = threading.Lock()
_STOP = threading.Event()
_RUNNING_PROCS: dict[str, subprocess.Popen[str]] = {}
_PROC_LOCK = threading.Lock()


def ensure_reader_worker_started() -> None:
    with _WORKER_LOCK:
        if any(t.is_alive() for t in _WORKER_THREADS):
            return
        _STOP.clear()
        _WORKER_THREADS.clear()
        for i in range(2):
            thread = threading.Thread(
                target=_worker_loop,
                name=f"moni-reader-worker-{i + 1}",
                daemon=True,
            )
            thread.start()
            _WORKER_THREADS.append(thread)
        logger.info("[reader] worker threads started")


def stop_reader_worker() -> None:
    _STOP.set()
    with _PROC_LOCK:
        procs = list(_RUNNING_PROCS.values())
    for proc in procs:
        _kill_proc(proc)


def cancel_reader_run(job_id: str) -> bool:
    """Mark a run cancelled and kill its process group if still alive.

    Bumping the fence stops the worker's ``leases.finish`` from writing
    ``done`` over the cancellation.
    """
    conn = get_db()
    now = _now()
    conn.execute(
        "UPDATE llm_jobs SET status = 'cancelled', finished_at = ?, "
        "fence = fence + 1, lease_owner = NULL "
        "WHERE id = ? AND status IN ('pending', 'running')",
        (now, job_id),
    )
    conn.execute(
        "UPDATE reader_messages SET status = 'cancelled', updated_at = ? "
        "WHERE job_id = ? AND status IN ('pending', 'streaming')",
        (now, job_id),
    )
    conn.commit()
    with _PROC_LOCK:
        proc = _RUNNING_PROCS.get(job_id)
    if proc is not None:
        _kill_proc(proc)
        return True
    return False


def _worker_loop() -> None:
    while not _STOP.is_set():
        try:
            handle = leases.acquire(
                "llm_jobs",
                grace_sec=120,
                task_types=("reader_chat",),
            )
            if handle is None:
                _STOP.wait(1.0)
                continue
            _run_claimed(handle)
        except Exception:  # noqa: BLE001 — a bad job must not kill the worker
            logger.exception("[reader] worker loop error")
            _STOP.wait(1.0)


def _run_claimed(handle: leases.LeaseHandle) -> None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM llm_jobs WHERE id = ?", (handle.job_id,)
    ).fetchone()
    if row is None:
        leases.finish(handle, status="failed", extra_fields={"error": "job row vanished"})
        return
    job = dict(row)
    job_id = job["id"]
    article_id = job["article_id"]
    article_row = conn.execute(
        "SELECT * FROM articles WHERE id = ?", (article_id,)
    ).fetchone()
    if article_row is None:
        _finish_failed(handle, job_id, "article not found")
        return
    article = dict(article_row)
    assistant = conn.execute(
        "SELECT * FROM reader_messages WHERE job_id = ? AND role = 'assistant'",
        (job_id,),
    ).fetchone()
    if assistant is None:
        _finish_failed(handle, job_id, "assistant message not found")
        return
    assistant = dict(assistant)
    conversation_id = assistant["conversation_id"]

    user = conn.execute(
        "SELECT * FROM reader_messages WHERE conversation_id = ? AND role = 'user' "
        "AND created_at <= ? ORDER BY created_at DESC LIMIT 1",
        (conversation_id, assistant["created_at"]),
    ).fetchone()
    user = dict(user) if user else {"content": "", "quote_text": None}

    prompt_row = None
    if job.get("prompt_name"):
        prompt_row = conn.execute(
            "SELECT * FROM reader_prompts WHERE name = ?", (job["prompt_name"],)
        ).fetchone()
    prompt = dict(prompt_row) if prompt_row else {"body": "", "scope": "article"}

    history_rows = conn.execute(
        "SELECT role, content FROM reader_messages "
        "WHERE conversation_id = ? AND status = 'done' AND created_at < ? "
        "ORDER BY created_at DESC LIMIT 6",
        (conversation_id, user.get("created_at") or assistant["created_at"]),
    ).fetchall()
    history = list(reversed([dict(r) for r in history_rows]))

    now = _now()
    claimed = conn.execute(
        "UPDATE llm_jobs SET started_at = COALESCE(started_at, ?) "
        "WHERE id = ? AND status = 'running' AND lease_owner = ? AND fence = ?",
        (now, job_id, handle.owner, handle.fence),
    )
    if claimed.rowcount == 0:
        return
    conn.execute(
        "UPDATE reader_messages SET status = 'streaming', content = '', updated_at = ? "
        "WHERE id = ? AND status = 'pending'",
        (now, assistant["id"]),
    )
    conn.commit()

    text = clean_article_text(article.get("raw_content"))
    question = user.get("content") or prompt.get("body") or "请阅读这篇文章。"
    quote = user.get("quote_text") or ""
    prompt_body = prompt.get("body") or ""
    scope = prompt.get("scope") or "article"
    reasoning = assistant.get("reasoning") or "low"
    usage = {"input_tokens": 0, "output_tokens": 0}
    duration_ms = 0
    mode = choose_reader_mode(
        text_len=len(text),
        prompt_body=prompt_body,
        scope=scope,
        quote=quote,
        has_history=bool(history),
    )

    def on_start(proc: subprocess.Popen[str]) -> None:
        with _PROC_LOCK:
            _RUNNING_PROCS[job_id] = proc

    def make_delta(message_id: str):
        state = {"last": 0.0, "buf": ""}

        def flush(force: bool = False) -> None:
            if not state["buf"]:
                return
            now_ts = time.monotonic()
            if not force and len(state["buf"]) < 40 and now_ts - state["last"] < 0.25:
                return
            chunk = state["buf"]
            state["buf"] = ""
            state["last"] = now_ts
            conn.execute(
                "UPDATE reader_messages SET content = content || ?, updated_at = ? "
                "WHERE id = ?",
                (chunk, _now(), message_id),
            )
            conn.commit()

        def on_delta(delta: str) -> None:
            state["buf"] += delta
            flush(force=False)

        on_delta.flush = lambda: flush(force=True)  # type: ignore[attr-defined]
        return on_delta

    stop_heartbeat = threading.Event()

    def _beat() -> None:
        while not stop_heartbeat.wait(20):
            if not leases.heartbeat(handle):
                with _PROC_LOCK:
                    proc = _RUNNING_PROCS.get(job_id)
                if proc is not None:
                    _kill_proc(proc)
                return

    beater = threading.Thread(target=_beat, name=f"reader-heartbeat-{job_id[:8]}", daemon=True)
    beater.start()

    def _cancelled() -> bool:
        row = conn.execute("SELECT status FROM llm_jobs WHERE id = ?", (job_id,)).fetchone()
        return bool(row and row["status"] == "cancelled")

    def _note(message_id: str, content: str) -> None:
        conn.execute(
            "UPDATE reader_messages SET content = ?, updated_at = ? WHERE id = ?",
            (content, _now(), message_id),
        )
        conn.commit()

    delta = None
    try:
        delta = make_delta(assistant["id"])
        with desensitize_scope(task_type="reader", article_id=article_id, job_id=job_id):
            if mode == "map_reduce":
                ensure_chunks(article_id, text)
                rows = load_chunk_rows(article_id)
                groups = group_chunk_texts([row["content"] for row in rows])
                partials: list[str] = []
                for idx, chunk in enumerate(groups, start=1):
                    if _cancelled():
                        break
                    _note(assistant["id"], f"正在分段阅读全文（{idx}/{len(groups)}）…")
                    part = run_reader_stream(
                        build_chunk_prompt(
                            article=article,
                            prompt_body=prompt_body or question,
                            chunk=chunk,
                            index=idx,
                            total=len(groups),
                        ),
                        reasoning=reasoning,
                        on_start=on_start,
                    )
                    partials.append(part.get("text") or "")
                    usage["input_tokens"] += int((part.get("usage") or {}).get("input_tokens") or 0)
                    usage["output_tokens"] += int((part.get("usage") or {}).get("output_tokens") or 0)
                    duration_ms += int(part.get("duration_ms") or 0)
                if not _cancelled():
                    _note(assistant["id"], "")
                    result = run_reader_stream(
                        build_merge_prompt(
                            article=article,
                            prompt_body=prompt_body or question,
                            partials=partials,
                            quote_text=quote,
                        ),
                        reasoning=reasoning,
                        on_start=on_start,
                        on_delta=delta,
                    )
                    delta.flush()
                    usage["input_tokens"] += int((result.get("usage") or {}).get("input_tokens") or 0)
                    usage["output_tokens"] += int((result.get("usage") or {}).get("output_tokens") or 0)
                    duration_ms += int(result.get("duration_ms") or 0)
                else:
                    result = {"text": ""}
            else:
                if mode == "full":
                    context = text
                    chunk_note = ""
                else:
                    ensure_chunks(article_id, text)
                    focus = quote if mode == "selection" else question
                    picked = chunks_for_question(article_id, focus, limit=6)
                    context = "\n\n".join(item["content"] for item in picked)
                    outline = outline_chunks(article_id)
                    if mode == "selection":
                        chunk_note = "长文：这次只附上选中文本所在的相关段落，不是逐字全文。"
                    else:
                        chunk_note = "长文追问：附上大纲、开头、结尾和相关段落，不是逐字全文。"
                    if outline:
                        chunk_note += "\n全文大纲：\n" + outline
                result = run_reader_stream(
                    build_reader_prompt(
                        article=article,
                        question=question,
                        prompt_body=prompt_body,
                        quote_text=quote,
                        history=history,
                        article_text=context,
                        chunk_note=chunk_note,
                    ),
                    reasoning=reasoning,
                    on_start=on_start,
                    on_delta=delta,
                )
                delta.flush()
                usage = result.get("usage") or usage
                duration_ms = result.get("duration_ms") or 0

        if _cancelled():
            leases.finish(
                handle,
                status="cancelled",
                extra_fields={
                    "finished_at": _now(),
                    "duration_ms": duration_ms,
                    "input_tokens": int(usage.get("input_tokens") or 0),
                    "output_tokens": int(usage.get("output_tokens") or 0),
                },
            )
            return

        final_text = (result.get("text") or "").strip() or (
            conn.execute(
                "SELECT content FROM reader_messages WHERE id = ?",
                (assistant["id"],),
            ).fetchone()["content"]
        )
        wrote = leases.finish(
            handle,
            status="done",
            extra_fields={
                "result": final_text,
                "finished_at": _now(),
                "duration_ms": duration_ms,
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
            },
        )
        if not wrote:
            return
        conn.execute(
            "UPDATE reader_messages SET content = ?, status = 'done', error = NULL, "
            "updated_at = ? WHERE id = ?",
            (final_text, _now(), assistant["id"]),
        )
        conn.execute(
            "UPDATE reader_conversations SET unread = 1, updated_at = ? WHERE id = ?",
            (_now(), conversation_id),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        if delta is not None:
            try:
                delta.flush()
            except Exception:
                pass
        if _cancelled():
            leases.finish(
                handle,
                status="cancelled",
                extra_fields={"finished_at": _now(), "error": str(exc)[:300]},
            )
            return
        logger.exception("[reader] job %s failed", job_id)
        _mark_message_failed(conn, assistant["id"], str(exc))
        _finish_failed(handle, job_id, f"{type(exc).__name__}: {exc}")
    finally:
        stop_heartbeat.set()
        beater.join(timeout=1)
        with _PROC_LOCK:
            _RUNNING_PROCS.pop(job_id, None)




def _finish_failed(handle: leases.LeaseHandle, job_id: str, error: str) -> None:
    leases.finish(
        handle,
        status="failed",
        extra_fields={"error": error[:1000], "finished_at": _now()},
    )


def _mark_message_failed(conn, message_id: str, error: str) -> None:
    conn.execute(
        "UPDATE reader_messages SET status = 'failed', error = ?, updated_at = ? "
        "WHERE id = ?",
        (error[:1000], _now(), message_id),
    )
    conn.commit()


def _kill_proc(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
