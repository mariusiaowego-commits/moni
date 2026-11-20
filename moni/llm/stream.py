"""Hermes stream-json execution for jobs that need live progress.

The plain one-shot runner only returns after a model call finishes.  For
fact-check-plus that leaves the reader with nothing but a stopwatch for
several minutes.  Hermes' ``--format stream-json`` protocol emits one JSON
event per line, so progress can be derived from real tool calls.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

ARGV_PROMPT_LIMIT_BYTES = 200_000
KILL_GRACE_SEC = 5.0


class PromptTooLargeError(RuntimeError):
    """Kept local to avoid a runner ↔ stream import cycle."""

    def __init__(self, size_bytes: int, limit_bytes: int) -> None:
        super().__init__(
            f"prompt too large for argv: {size_bytes} bytes > limit {limit_bytes} bytes"
        )
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes


class StreamMidwayError(RuntimeError):
    """Hermes produced events and then failed before returning a result."""


@dataclass(frozen=True)
class StreamResult:
    text: str
    duration_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    session_id: str = ""


EventCallback = Callable[[dict[str, Any]], None]


def run_hermes_stream(
    prompt: str,
    *,
    provider: str = "",
    model: str = "",
    tools: str = "web",
    timeout: int = 600,
    on_event: EventCallback | None = None,
) -> StreamResult:
    """Run Hermes and return the final response.

    Any parsed event is passed to ``on_event`` before this function returns.
    A watchdog kills the process group at ``timeout`` so a silent Hermes hang
    cannot block the sole jobs worker.
    """
    prompt_bytes = len(prompt.encode("utf-8"))
    if prompt_bytes > ARGV_PROMPT_LIMIT_BYTES:
        raise PromptTooLargeError(prompt_bytes, ARGV_PROMPT_LIMIT_BYTES)

    argv = ["hermes", "chat", "-q", prompt, "--format", "stream-json"]
    if provider:
        argv.extend(["--provider", provider])
    if model:
        argv.extend(["-m", model])
    if tools:
        argv.extend(["-t", tools])

    env = dict(os.environ)
    env.setdefault("HERMES_PROFILE", "moni")
    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=env,
    )
    timed_out = threading.Event()

    def _expire() -> None:
        timed_out.set()
        _kill_process_group(proc)

    watchdog = threading.Timer(timeout, _expire)
    watchdog.daemon = True
    watchdog.start()
    try:
        return read_stream(
            proc,
            on_event=on_event,
            timeout=timeout,
            started=started,
            timed_out=timed_out,
        )
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            _kill_process_group(proc)


def read_stream(
    proc: subprocess.Popen[str],
    *,
    on_event: EventCallback | None = None,
    timeout: int = 600,
    started: float | None = None,
    timed_out: threading.Event | None = None,
) -> StreamResult:
    """Consume Hermes stream-json stdout into a final result."""
    started = time.monotonic() if started is None else started
    events: list[dict[str, Any]] = []
    text_parts: list[str] = []
    result: dict[str, Any] | None = None

    def deadline_expired() -> bool:
        if timed_out is not None and timed_out.is_set():
            return True
        return time.monotonic() - started >= timeout

    assert proc.stdout is not None
    for line in proc.stdout:
        if deadline_expired():
            raise StreamMidwayError(
                f"hermes stream timeout after {timeout}s with {len(events)} events"
            )
        event = parse_event(line)
        if event is None:
            continue
        events.append(event)
        if on_event is not None:
            on_event(event)
        if event.get("type") == "text":
            text_parts.append(str(event.get("text") or ""))
        elif event.get("type") == "result":
            result = event
            break

    if result is None:
        stderr = _drain_stderr(proc)
        error = _stream_error(stderr, len(events))
        raise _failure(error, len(events))

    try:
        proc.wait(timeout=KILL_GRACE_SEC)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
    if int(result.get("exit_code") or 0) != 0:
        raise _failure(
            str(result.get("error") or f"hermes stream exit_code={result.get('exit_code')}"),
            len(events),
        )

    final_text = str(result.get("text") or "").strip()
    duration_ms = int((time.monotonic() - started) * 1000)
    tokens = result.get("tokens") if isinstance(result.get("tokens"), dict) else {}
    return StreamResult(
        text=final_text or "".join(text_parts).strip(),
        duration_ms=duration_ms,
        input_tokens=int(tokens.get("input") or 0),
        output_tokens=int(tokens.get("output") or 0),
        session_id=str(result.get("session_id") or ""),
    )


def parse_event(line: str) -> dict[str, Any] | None:
    """Parse one JSONL event; malformed lines are progress, not failures."""
    try:
        value = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _drain_stderr(proc: subprocess.Popen[str]) -> str:
    try:
        stderr = proc.stderr.read() if proc.stderr is not None else ""
    except (OSError, ValueError):
        stderr = ""
    try:
        proc.wait(timeout=KILL_GRACE_SEC)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
    return stderr.strip()


def _stream_error(stderr: str, event_count: int) -> str:
    detail = stderr[-600:].strip()
    if detail:
        return detail
    return f"hermes stream ended without result ({event_count} events)"


def _failure(error: str, event_count: int) -> Exception:
    if event_count == 0:
        return RuntimeError(error)
    return StreamMidwayError(error)


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=KILL_GRACE_SEC)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass
