"""Article reader prompt assembly + hermes streaming transport (Sprint 47).

The reader panel deliberately runs stateless one-shot calls:

    hermes chat -q <prompt> --oneshot --format stream-json \
        --ignore-rules -t "" --reasoning <level> --source tool \
        --max-turns 1

The `moni` profile routes through the hermes gateway, which refuses
`--run-budget` ("Unsupported gateway CLI options", rc=2) and points at
`agent.run_budget_seconds` in config.yaml instead.  The wall-clock budget is
therefore enforced here: a watchdog thread kills the process group at
`timeout`.

Measured on this machine: the default moni profile costs ~21.5k input
tokens for a one-word reply; `--ignore-rules -t ""` drops that to
~2.1k.  One-shot sessions are not persisted (session count stays flat,
and the printed `local-*` session id is absent from state.db), so the
transcript lives in reader_messages instead of hermes session files.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from moni.db.connection import get_db
from moni.llm.desensitize import StreamRestorer
from moni.llm.desensitize.audit import (
    current_desensitize_scope,
    finalize_inbound,
    prepare_outbound,
    record_event,
)

logger = logging.getLogger(__name__)

HERMES_PROFILE = os.environ.get("MONI_READER_PROFILE", "moni")
READER_SYSTEM_PATH = Path(__file__).parent / "prompts" / "reader_system.md"

#: Articles up to this size are sent whole.  Above it we build a chunk
#: index; a free-form question gets the most relevant chunks, while a
#: whole-article prompt runs a bounded map-reduce.
MAX_FULL_CHARS = 20_000
CHUNK_CHARS = 6_000
MAX_CHUNKS = 8
MAX_HISTORY_MESSAGES = 6
MAX_HISTORY_CHARS = 6_000
MAX_QUOTE_CHARS = 2_000
MAX_PROMPT_CHARS = 8_000

_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.S)
_BANK_MARKER_RE = re.compile(r"\n?\[bank:[^\]]+\]\s*$", re.I)
_STATUS_FOOTER_RE = re.compile(r"\n{2,}[^\n]{0,80}·\s*\d+%\s*·[^\n]*\s*$")


def load_reader_system() -> str:
    try:
        return READER_SYSTEM_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return "你是文章阅读助手。只基于给定正文回答，不编造，不调用工具。"


def clean_article_text(text: str | None) -> str:
    """Remove YAML frontmatter and moni's internal bank footer from context."""
    value = (text or "").strip()
    value = _FRONTMATTER_RE.sub("", value)
    value = _BANK_MARKER_RE.sub("", value)
    return value.strip()


def strip_status_footer(text: str) -> str:
    """Hermes appends `model · N% · cwd` to the final result; deltas are clean."""
    return _STATUS_FOOTER_RE.sub("", (text or "").rstrip()).strip()


def chunk_article(text: str, *, max_chars: int = CHUNK_CHARS) -> list[str]:
    """Split on headings/blank lines, then pack paragraphs up to max_chars."""
    blocks = re.split(r"\n{2,}", text.strip())
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if len(block) > max_chars:
            if current:
                chunks.append("\n\n".join(current))
                current, size = [], 0
            for i in range(0, len(block), max_chars):
                chunks.append(block[i : i + max_chars])
            continue
        if size + len(block) + 2 > max_chars and current:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(block)
        size += len(block) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def ensure_chunks(article_id: str, text: str) -> int:
    """Index a long article once.  Returns the chunk count."""
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM reader_chunks WHERE article_id = ?",
        (article_id,),
    ).fetchone()
    if row and int(row["n"] or 0) > 0:
        return int(row["n"])
    chunks = chunk_article(text)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for seq, chunk in enumerate(chunks):
        heading = ""
        first = chunk.strip().splitlines()[0] if chunk.strip() else ""
        if first.startswith("#"):
            heading = first.lstrip("# ").strip()[:200]
        conn.execute(
            "INSERT INTO reader_chunks (id, article_id, seq, heading, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, article_id, seq, heading, chunk, now),
        )
    conn.commit()
    return len(chunks)


def _query_terms(question: str) -> list[str]:
    """Cheap bilingual term extraction for chunk scoring (no embeddings)."""
    text = (question or "").lower()
    terms = re.findall(r"[a-z0-9_]{3,}|[\u4e00-\u9fff]{2,}", text)
    # For Chinese runs, add 2-char n-grams so "银行监管" still matches
    # "银行" and "监管" in different sentences.
    expanded: list[str] = []
    for term in terms:
        expanded.append(term)
        if re.fullmatch(r"[\u4e00-\u9fff]{4,}", term):
            expanded.extend(term[i : i + 2] for i in range(0, len(term) - 1))
    return list(dict.fromkeys(t for t in expanded if len(t) >= 2))[:24]


def select_relevant_chunks(article_id: str, question: str, *, limit: int = 6) -> list[dict[str, Any]]:
    conn = get_db()
    rows = conn.execute(
        "SELECT seq, heading, content FROM reader_chunks WHERE article_id = ? ORDER BY seq",
        (article_id,),
    ).fetchall()
    if not rows:
        return []
    terms = _query_terms(question)
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        haystack = f"{row['heading'] or ''}\n{row['content']}".lower()
        score = sum(haystack.count(term) for term in terms)
        scored.append((score, dict(row)))
    scored.sort(key=lambda item: (-item[0], item[1]["seq"]))
    picked = [item[1] for item in scored[:limit] if item[0] > 0]
    if not picked:
        picked = [item[1] for item in scored[: min(limit, 2)]]
    return sorted(picked, key=lambda item: item["seq"])


def outline_chunks(article_id: str, *, limit: int = 40) -> str:
    conn = get_db()
    rows = conn.execute(
        "SELECT seq, heading FROM reader_chunks WHERE article_id = ? ORDER BY seq LIMIT ?",
        (article_id, limit),
    ).fetchall()
    lines = [f"{r['seq'] + 1}. {r['heading'] or '未命名段落'}" for r in rows]
    return "\n".join(lines)


def _history_block(messages: list[dict[str, Any]]) -> str:
    recent = messages[-MAX_HISTORY_MESSAGES:]
    total = 0
    lines: list[str] = []
    for msg in reversed(recent):
        role = "用户" if msg.get("role") == "user" else "助手"
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if total + len(content) > MAX_HISTORY_CHARS:
            content = content[: max(0, MAX_HISTORY_CHARS - total)]
        if not content:
            break
        lines.append(f"{role}: {content}")
        total += len(content)
        if total >= MAX_HISTORY_CHARS:
            break
    lines.reverse()
    return "\n\n".join(lines)


def build_reader_prompt(
    *,
    article: dict[str, Any],
    question: str,
    prompt_body: str = "",
    quote_text: str = "",
    history: list[dict[str, Any]] | None = None,
    article_text: str | None = None,
    chunk_note: str = "",
) -> str:
    """Compose one stateless turn.  Random nonce keeps article text from
    closing the data block and impersonating instructions."""
    nonce = uuid.uuid4().hex[:10]
    body = clean_article_text(article_text if article_text is not None else article.get("raw_content"))
    quote = (quote_text or "").strip()[:MAX_QUOTE_CHARS]
    instruction = (prompt_body or "").strip()[:MAX_PROMPT_CHARS]
    ask = (question or "").strip()
    if not ask and instruction:
        ask = instruction

    parts = [
        load_reader_system(),
        f"<article-{nonce}>\n"
        f"标题：{article.get('title') or ''}\n"
        f"来源：{article.get('source') or ''}\n"
        f"作者：{article.get('publisher') or ''}\n"
        f"原文时间：{article.get('published_at') or ''}\n\n"
        f"{body}\n"
        f"</article-{nonce}>",
    ]
    if chunk_note:
        parts.append(chunk_note)
    if quote:
        parts.append(f"<selection-{nonce}>\n{quote}\n</selection-{nonce}>")
    hist = _history_block(history or [])
    if hist:
        parts.append(f"<history>\n{hist}\n</history>")
    if instruction and ask != instruction:
        parts.append(f"<prompt>\n{instruction}\n</prompt>")
    parts.append(f"<question>\n{ask or '请阅读这篇文章。'}\n</question>")
    return "\n\n".join(parts)


def build_chunk_prompt(
    *,
    article: dict[str, Any],
    prompt_body: str,
    chunk: str,
    index: int,
    total: int,
) -> str:
    nonce = uuid.uuid4().hex[:10]
    return "\n\n".join(
        [
            load_reader_system(),
            f"<article-chunk-{nonce}>\n"
            f"标题：{article.get('title') or ''}\n"
            f"这是全文第 {index}/{total} 部分。只提取本部分与任务相关的要点，不要假装看过其它部分。\n\n"
            f"{chunk}\n"
            f"</article-chunk-{nonce}>",
            f"<task>\n{prompt_body}\n</task>",
            "<question>\n按任务输出本部分的要点，控制在 300 字以内。\n</question>",
        ]
    )


def build_merge_prompt(
    *,
    article: dict[str, Any],
    prompt_body: str,
    partials: list[str],
    quote_text: str = "",
) -> str:
    nonce = uuid.uuid4().hex[:10]
    joined = "\n\n".join(f"第 {i + 1} 部分：\n{p}" for i, p in enumerate(partials))
    parts = [
        load_reader_system(),
        f"<article-{nonce}>\n标题：{article.get('title') or ''}\n来源：{article.get('source') or ''}\n</article-{nonce}>",
        f"<partials>\n{joined}\n</partials>",
        f"<task>\n{prompt_body}\n</task>",
    ]
    quote = (quote_text or "").strip()[:MAX_QUOTE_CHARS]
    if quote:
        parts.append(f"<selection-{nonce}>\n{quote}\n</selection-{nonce}>")
    parts.append(
        "<question>\n把各部分要点合并成一份最终答案。去掉重复，保留分歧，按任务要求的格式输出。\n</question>"
    )
    return "\n\n".join(parts)



def choose_reader_mode(
    *,
    text_len: int,
    prompt_body: str = "",
    scope: str = "article",
    quote: str = "",
    has_history: bool = False,
) -> str:
    """How a turn sees a long article.

    ``full`` sends the article once. ``map_reduce`` covers every chunk
    (preset whole-article prompts, and the first free question).
    ``selection`` sends the quote plus nearby chunks. ``retrieve`` is a
    follow-up: lead, tail, and the chunks that match the question.
    """
    if text_len <= MAX_FULL_CHARS:
        return "full"
    whole = bool((prompt_body or "").strip()) and (scope or "article") == "article"
    if whole:
        return "map_reduce"
    if (quote or "").strip():
        return "selection"
    if has_history:
        return "retrieve"
    return "map_reduce"


def group_chunk_texts(chunks: list[str], *, max_groups: int = MAX_CHUNKS) -> list[str]:
    """Pack chunk texts into at most ``max_groups`` groups, keeping order."""
    if not chunks:
        return []
    size = max(1, (len(chunks) + max_groups - 1) // max_groups)
    groups = ["\n\n".join(chunks[i : i + size]) for i in range(0, len(chunks), size)]
    return groups[:max_groups]


def load_chunk_rows(article_id: str) -> list[dict[str, Any]]:
    conn = get_db()
    rows = conn.execute(
        "SELECT seq, heading, content FROM reader_chunks WHERE article_id = ? ORDER BY seq",
        (article_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def chunks_for_question(article_id: str, question: str, *, limit: int = 6) -> list[dict[str, Any]]:
    """Relevant chunks plus the lead and the tail, in reading order."""
    rows = load_chunk_rows(article_id)
    if not rows:
        return []
    picked = select_relevant_chunks(article_id, question, limit=limit)
    by_seq = {int(item["seq"]): item for item in picked}
    by_seq[int(rows[0]["seq"])] = rows[0]
    by_seq[int(rows[-1]["seq"])] = rows[-1]
    return [by_seq[seq] for seq in sorted(by_seq)]


class ReaderStreamError(RuntimeError):
    pass


def run_reader_stream(
    prompt: str,
    *,
    reasoning: str = "low",
    on_start: Callable[[subprocess.Popen[str]], None] | None = None,
    on_delta: Callable[[str], None] | None = None,
    timeout: int = 180,
) -> dict[str, Any]:
    """Run one streaming hermes call and return text + usage.

    Cancellation is handled by the caller killing the registered Popen
    process group; this function then returns a partial result.
    """
    scope = current_desensitize_scope()
    eff_task = scope.task_type or "reader"
    ctx = prepare_outbound(
        prompt,
        call_site="reader",
        task_type=eff_task,
        article_id=scope.article_id,
        job_id=scope.job_id,
        share_task_id=scope.share_task_id,
        json_output=False,
        db_path=scope.db_path,
    )
    if ctx is not None:
        if ctx.mask_result.blocked:
            record_event(ctx, blocked=True)
            raise ReaderStreamError(f"desensitize blocked: {ctx.mask_result.blocked_reason}")
        record_event(ctx, blocked=False)
        send_prompt = ctx.masked_text
    else:
        send_prompt = prompt

    cmd = [
        "hermes",
        "chat",
        "-q",
        send_prompt,
        "--oneshot",
        "-Q",
        "--format",
        "stream-json",
        "--ignore-rules",
        "-t",
        "",
        "--reasoning",
        reasoning if reasoning in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"} else "low",
        "--source",
        "tool",
        "--max-turns",
        "1",
    ]
    env = os.environ.copy()
    env["HERMES_PROFILE"] = HERMES_PROFILE
    started = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
        start_new_session=True,
    )
    if on_start:
        on_start(proc)

    timed_out = threading.Event()

    def _expire() -> None:
        timed_out.set()
        _kill_group(proc)

    watchdog = threading.Timer(timeout, _expire)
    watchdog.daemon = True
    watchdog.start()
    text_parts: list[str] = []
    usage: dict[str, Any] = {}
    result_text = ""
    stream_restorer = StreamRestorer(ctx.vault) if ctx is not None else None
    stream_aborted = False
    flushed = False
    try:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type = event.get("type")
            if event_type == "text":
                delta = event.get("text") or ""
                if delta:
                    if stream_restorer is not None:
                        safe_delta = stream_restorer.feed(delta)
                        if safe_delta:
                            text_parts.append(safe_delta)
                            if on_delta:
                                on_delta(safe_delta)
                    else:
                        text_parts.append(delta)
                        if on_delta:
                            on_delta(delta)
            elif event_type == "result":
                result_text = event.get("text") or ""
                tokens = event.get("tokens") or {}
                usage = {
                    "input_tokens": int(tokens.get("input") or 0),
                    "output_tokens": int(tokens.get("output") or 0),
                    "cache_read_tokens": int(tokens.get("cache_read") or 0),
                }
            elif event_type in {"error", "failure"}:
                stream_aborted = True
                raise ReaderStreamError(event.get("message") or event.get("error") or "hermes error")
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            stream_aborted = True
            _kill_group(proc)
    except Exception:
        stream_aborted = True
        if ctx is not None:
            ctx.fallback_override = ctx.fallback_override or "stream_aborted"
        raise
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            _kill_group(proc)
        if stream_restorer is not None and not flushed:
            aborted = stream_aborted or timed_out.is_set() or (proc.returncode not in (0, None))
            tail_delta = stream_restorer.flush(aborted=aborted)
            flushed = True
            if tail_delta:
                text_parts.append(tail_delta)
                if on_delta:
                    on_delta(tail_delta)
        if proc.stderr is not None:
            try:
                err = proc.stderr.read().strip()
            except Exception:  # noqa: BLE001
                err = ""
        else:
            err = ""
        if ctx is not None and stream_aborted:
            record_event(ctx, blocked=False)

    text = "".join(text_parts).strip()
    if not text and result_text:
        text = strip_status_footer(result_text)
    if proc.returncode not in (0, None) and not text:
        if ctx is not None:
            ctx.fallback_override = ctx.fallback_override or "stream_aborted"
            record_event(ctx, blocked=False)
        if timed_out.is_set():
            raise ReaderStreamError(f"模型响应超时（{timeout}s），可重试或换更短的 prompt")
        raise ReaderStreamError(f"hermes exited rc={proc.returncode}: {(err or '')[:300]}")
    if ctx is not None:
        text = finalize_inbound(ctx, text)
        record_event(ctx, blocked=False)
    return {
        "text": text,
        "usage": usage,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "returncode": proc.returncode,
    }


def _kill_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
