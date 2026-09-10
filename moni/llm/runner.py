"""moni.llm.runner — Hardened LLM call layer (v1.0.42, sprint-22).

解决 moni LLM 集成的三个结构性脆弱点:

1. **零 failover** — 单 provider 429 时整批任务全灭 (LLM 43% 失败率主因)
2. **零熔断** — 每次新任务重新撞墙,无失败记忆
3. **裸 subprocess.run** — 长 prompt 触发 E2BIG, 超时只 kill 直杀 PID 留孤儿

设计: 三个组件
- ``HardenedSubprocess``: 进程组击杀 (killpg SIGTERM → 5s grace → SIGKILL →
  wait) + 200KB argv 预检 → PromptTooLargeError + stdin 协议可插拔
- ``CircuitBreaker``: 内存态熔断(failure_threshold × reset_timeout),
  状态 CLOSED / OPEN / HALF_OPEN
- ``FailoverRunner``: 链式 provider 切换,每跳过一次熔断器

薄壳红线: moni 不持 key,链 只在 hermes 已配置的 provider 间切。
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── token estimation (moved here to avoid circular import with client) ────
_CN_RANGE = re.compile(r"[\u4e00-\u9fff]")


def estimate_tokens(text: str) -> int:
    """Crude token estimation. Mirrors moni.llm.client.estimate_tokens.

    - Chinese chars: ~1.5 chars/token  →  cn * 2 // 3
    - Other chars:   ~4 chars/token    →  other // 4
    """
    if not text:
        return 0
    cn = sum(1 for _ in _CN_RANGE.findall(text))
    other = len(text) - cn
    return cn * 2 // 3 + other // 4


# ── 配置常量 (env var 可覆盖) ──────────────────────────────────────────────

#: macOS 单参上限 ~256KB, ARG_MAX 总量 ~1MB;保守预检 200KB。
ARGV_PROMPT_LIMIT_BYTES = int(os.environ.get("MONI_ARGV_PROMPT_LIMIT_BYTES", "200000"))

#: 进程组超时 → SIGTERM → grace → SIGKILL。
KILL_GRACE_SEC = float(os.environ.get("MONI_KILL_GRACE_SEC", "5.0"))


# ── 异常 ──────────────────────────────────────────────────────────────────


class LLMRunnerError(Exception):
    """Base error for runner failures."""


class PromptTooLargeError(LLMRunnerError):
    """Prompt > ARGV_PROMPT_LIMIT_BYTES, 上层应截断或放弃。"""

    def __init__(self, size_bytes: int, limit_bytes: int) -> None:
        super().__init__(
            f"prompt too large for argv: {size_bytes} bytes > limit {limit_bytes} bytes"
        )
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes


class FailoverExhausted(LLMRunnerError):
    """All models in the failover chain are unhealthy / unavailable."""

    def __init__(self, errors: list[tuple[str, str]]) -> None:
        super().__init__(f"failover chain exhausted after {len(errors)} attempts")
        self.errors = errors  # [(model_id, error_message), ...]


# ── HardenedSubprocess ────────────────────────────────────────────────────


@dataclass
class SubprocessResult:
    """Result of a hardened subprocess invocation."""

    stdout: str
    stderr: str
    returncode: int
    duration_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class HardenedSubprocess:
    """Process-group hardened subprocess invocation.

    Differences vs raw subprocess.run:
    1. ``start_new_session=True`` so the subprocess + its children form
       a process group (pgid == child_pid). When we kill, we kill the
       *group*, not just the direct PID — orphan grandchildren die too.
    2. Timeout → SIGTERM the *group*, sleep grace, SIGKILL the *group*,
       then ``wait()`` to reap zombies.
    3. 200KB argv pre-check so long prompts raise ``PromptTooLargeError``
       explicitly rather than OSError E2BIG (unhelpful).

    Args:
        cmd: argv list. ``{prompt}`` placeholder will be replaced with
            ``prompt_text`` (used when stdin transport is off, default).
        prompt_text: the full prompt to send.
        timeout: subprocess timeout in seconds.
        stdin_transport: if True, write prompt to stdin instead of argv.
            Falls back to argv automatically if hermes rejects stdin
            (rc=2 usage error). Default False (argv path is battle-tested
            in production v1.0.32+).
    """

    def __init__(
        self,
        cmd: list[str],
        prompt_text: str,
        *,
        timeout: int = 300,
        stdin_transport: bool = False,
    ) -> None:
        self.cmd = list(cmd)
        self.prompt_text = prompt_text
        self.timeout = timeout
        self.stdin_transport = stdin_transport
        self._prompt_bytes = len(prompt_text.encode("utf-8"))

    def run(self) -> SubprocessResult:
        """Execute the subprocess with hardened kill semantics.

        Raises:
            PromptTooLargeError: prompt > ARGV_PROMPT_LIMIT_BYTES and
                stdin_transport is False.
            subprocess.TimeoutExpired: if timeout hits (after grace kill).
            FileNotFoundError: if hermes binary missing.
        """
        if not self.stdin_transport and self._prompt_bytes > ARGV_PROMPT_LIMIT_BYTES:
            raise PromptTooLargeError(self._prompt_bytes, ARGV_PROMPT_LIMIT_BYTES)

        cmd = self._build_argv()
        proc: subprocess.Popen[str] | None = None
        start = time.monotonic()
        try:
            env = dict(os.environ)
            env.setdefault("HERMES_PROFILE", "coder")
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if self.stdin_transport else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,  # crucial for killpg
                env=env,
            )
            try:
                if self.stdin_transport:
                    stdout, stderr = proc.communicate(
                        input=self.prompt_text,
                        timeout=self.timeout,
                    )
                else:
                    stdout, stderr = proc.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self._kill_process_group(proc)
                # After kill, communicate again to drain pipes cleanly.
                try:
                    stdout, stderr = proc.communicate(timeout=2.0)
                except subprocess.TimeoutExpired:
                    stdout, stderr = "", ""
                raise subprocess.TimeoutExpired(cmd, self.timeout)
            duration_ms = int((time.monotonic() - start) * 1000)
            return SubprocessResult(
                stdout=stdout or "",
                stderr=stderr or "",
                returncode=proc.returncode,
                duration_ms=duration_ms,
            )
        finally:
            # Belt-and-suspenders: even if we already killed via timeout
            # path, ensure the pg is fully reaped so we don't zombie.
            if proc is not None and getattr(proc, "returncode", None) is None:
                self._kill_process_group(proc)
                try:
                    proc.wait(timeout=2.0)
                except (subprocess.TimeoutExpired, TypeError):
                    pass

    def _build_argv(self) -> list[str]:
        """Build argv. If a ``{prompt}`` placeholder is in self.cmd, replace
        it with self.prompt_text; otherwise append at end (legacy behavior
        matching moni.llm.client.call_llm).
        """
        if not self.cmd:
            raise ValueError("cmd cannot be empty")
        if "{prompt}" in self.cmd:
            return [self.prompt_text if c == "{prompt}" else c for c in self.cmd]
        # Legacy: append prompt as last argv (hermes -z takes prompt positionally)
        return [*self.cmd, self.prompt_text]

    def _kill_process_group(self, proc: subprocess.Popen[str]) -> None:
        """SIGTERM → grace → SIGKILL → wait. Never leaves a zombie behind."""
        poll_fn = getattr(proc, "poll", None)
        if callable(poll_fn) and poll_fn() is not None:
            return  # already exited
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
            logger.warning("pid %d still alive after SIGKILL — leak", proc.pid)


# ── CircuitBreaker ───────────────────────────────────────────────────────


class CircuitBreakerOpen(LLMRunnerError):
    """Raised when the breaker for a model is OPEN and rejects calls."""

    def __init__(self, model_id: str, reset_in_sec: float) -> None:
        super().__init__(
            f"circuit breaker OPEN for {model_id!r}, "
            f"resets in {reset_in_sec:.1f}s"
        )
        self.model_id = model_id
        self.reset_in_sec = reset_in_sec


@dataclass
class CircuitBreaker:
    """In-memory circuit breaker per model_id.

    State machine:
      CLOSED     → calls pass through; on failure: failure_count += 1.
                   If failure_count >= failure_threshold: → OPEN.
      OPEN       → calls fail-fast (CircuitBreakerOpen).
                   After reset_timeout sec: → HALF_OPEN.
      HALF_OPEN  → next call is a probe. Success → CLOSED + count = 0.
                   Failure → back to OPEN with reset timer refreshed.

    Thread safety: not safe across processes (intended — see SprintPlan §3
    反目标 4). Within one process, GIL + single-threaded llm_jobs worker
    means no contention. CLI / batch paths that share DB but not memory
    each have their own breaker — 4×K wasted calls worst case, well
    under the failure budget.
    """

    model_id: str
    failure_threshold: int = 2
    reset_timeout: float = 600.0  # seconds

    state: str = field(default="CLOSED", init=False)
    failure_count: int = field(default=0, init=False)
    opened_at: float | None = field(default=None, init=False)

    def allow(self) -> bool:
        """Return True if the next call should be attempted."""
        if self.state == "CLOSED":
            return True
        if self.state == "OPEN":
            assert self.opened_at is not None
            elapsed = time.monotonic() - self.opened_at
            if elapsed >= self.reset_timeout:
                self.state = "HALF_OPEN"
                logger.info(
                    "[breaker] %s OPEN → HALF_OPEN after %.1fs",
                    self.model_id, elapsed,
                )
                return True
            return False
        # HALF_OPEN: allow one probe
        return True

    def record_success(self) -> None:
        if self.state != "CLOSED":
            logger.info("[breaker] %s → CLOSED (probe ok)", self.model_id)
        self.state = "CLOSED"
        self.failure_count = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.state == "HALF_OPEN":
            self.state = "OPEN"
            self.opened_at = time.monotonic()
            logger.warning("[breaker] %s HALF_OPEN → OPEN (probe failed)", self.model_id)
            return
        if self.failure_count >= self.failure_threshold:
            self.state = "OPEN"
            self.opened_at = time.monotonic()
            logger.warning(
                "[breaker] %s → OPEN after %d failures",
                self.model_id, self.failure_count,
            )

    def reset_in_sec(self) -> float:
        """Time until next OPEN → HALF_OPEN transition (0 if not OPEN)."""
        if self.state != "OPEN" or self.opened_at is None:
            return 0.0
        return max(0.0, self.reset_timeout - (time.monotonic() - self.opened_at))


# ── FailoverRunner ────────────────────────────────────────────────────────


@dataclass
class CallRequest:
    """Single LLM call: which model + the prompt."""

    provider: str
    model_id: str
    prompt: str
    timeout: int = 300
    extra_args: list[str] | None = None
    usage_file: str | None = None


@dataclass
class CallResult:
    """Successful LLM call result."""

    reply_text: str
    provider: str
    model_id: str
    duration_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class FailoverRunner:
    """Chain a single LLM call through multiple providers/models with
    in-memory circuit breakers per model.

    Usage::

        runner = FailoverRunner.from_settings()  # reads llm_settings
        result = runner.call(CallRequest(...))

    Failure semantics:
      - Per-call: subprocess exit non-zero / TimeoutExpired / OSError.
        These increment the model's CircuitBreaker; runner moves on to
        the next model.
      - All chain members open or hard-fail: raises FailoverExhausted.

    Success: writes back into the breaker (CLOSED or reset from HALF_OPEN).
    """

    def __init__(self, breakers: dict[str, CircuitBreaker]) -> None:
        self.breakers = breakers

    @classmethod
    def from_settings(
        cls,
        *,
        fallback_chain: list[tuple[str, str]] | None = None,
        failure_threshold: int = 2,
        reset_seconds: float = 600.0,
    ) -> "FailoverRunner":
        """Build a runner. If ``fallback_chain`` is None/empty, reads
        ``llm_settings.fallback_chain`` (CSV "provider:model,provider:model").
        Empty chain = single-provider mode (back-compat).
        """
        chain = fallback_chain
        if chain is None:
            chain = _read_fallback_chain_from_db()
        breakers = {
            m_id: CircuitBreaker(
                model_id=m_id,
                failure_threshold=failure_threshold,
                reset_timeout=reset_seconds,
            )
            for _, m_id in chain
        } if chain else {}
        return cls(breakers=breakers)

    def call(self, req: CallRequest) -> CallResult:
        """Run req.prompt. If breaker is closed, execute. On failure
        advance to the next breaker in the chain. On success return.

        Raises:
            FailoverExhausted: all breakers open or all hard-fail.
        """
        chain = list(self.breakers.keys())
        if not chain:
            # Single-provider mode: caller must have set up a breaker
            # manually or just run without one. Treat as 1-element chain.
            chain = [req.model_id]
            self.breakers.setdefault(
                req.model_id,
                CircuitBreaker(model_id=req.model_id),
            )

        errors: list[tuple[str, str]] = []
        for m_id in chain:
            breaker = self.breakers[m_id]
            if not breaker.allow():
                errors.append((m_id, f"breaker OPEN, reset in {breaker.reset_in_sec():.1f}s"))
                continue
            try:
                result = self._invoke(req, m_id)
            except PromptTooLargeError as exc:
                # Truncate by chunked summarization is out of scope. Just
                # bubble up — caller decides what to do.
                raise
            except Exception as exc:
                err_msg = f"{type(exc).__name__}: {exc}"
                breaker.record_failure()
                errors.append((m_id, err_msg))
                logger.warning("[failover] %s failed: %s", m_id, err_msg)
                continue
            breaker.record_success()
            return CallResult(
                reply_text=result.stdout,
                provider=req.provider,
                model_id=m_id,
                duration_ms=result.duration_ms,
                input_tokens=getattr(result, "input_tokens", 0),
                output_tokens=getattr(result, "output_tokens", 0),
                cost_usd=getattr(result, "cost_usd", 0.0),
            )
        raise FailoverExhausted(errors)

    def _invoke(self, req: CallRequest, model_id: str) -> SubprocessResult:
        """Build argv for the given model and run it via HardenedSubprocess."""
        cmd = [
            "hermes", "-z", "{prompt}",
            "--provider", req.provider,
            "-m", model_id,
        ]
        if req.extra_args:
            cmd.extend(req.extra_args)
        if req.usage_file:
            cmd.extend(["--usage-file", req.usage_file])

        subprocess_runner = HardenedSubprocess(
            cmd=cmd,
            prompt_text=req.prompt,
            timeout=req.timeout,
        )
        result = subprocess_runner.run()

        # Parse usage file if produced. Skip when caller already wrote
        # usage into SubprocessResult (test path) by checking token counter.
        if (
            req.usage_file
            and os.path.isfile(req.usage_file)
            and result.input_tokens == 0  # not pre-populated
            and result.output_tokens == 0
        ):
            try:
                with open(req.usage_file, encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    result.input_tokens = int(data.get("input_tokens") or 0)
                    result.output_tokens = int(data.get("output_tokens") or 0)
                    result.cost_usd = float(data.get("estimated_cost_usd") or 0.0)
            except (OSError, ValueError, TypeError):
                logger.debug("usage file parse failed: %s", req.usage_file)

        if result.returncode != 0:
            err = (result.stderr or "").strip()[:300]
            raise LLMRunnerError(
                f"hermes failed rc={result.returncode}: {err}"
            )
        return result


# ── helpers ───────────────────────────────────────────────────────────────


def _read_fallback_chain_from_db() -> list[tuple[str, str]]:
    """Read llm_settings.fallback_chain (CSV "provider:model,provider:model").

    Empty/missing → empty list → runner runs single-provider mode.
    """
    try:
        from moni.db.connection import get_db

        row = get_db().execute(
            "SELECT value FROM llm_settings WHERE key = 'fallback_chain'"
        ).fetchone()
        if not row or not row["value"]:
            return []
        out: list[tuple[str, str]] = []
        for entry in str(row["value"]).split(","):
            entry = entry.strip()
            if not entry or ":" not in entry:
                continue
            provider, model = entry.split(":", 1)
            provider = provider.strip()
            model = model.strip()
            if provider and model:
                out.append((provider, model))
        return out
    except Exception as exc:  # noqa: BLE001 — best-effort, never break the runner
        logger.debug("read fallback_chain failed: %s", exc)
        return []