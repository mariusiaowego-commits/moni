"""moni.llm.client — Thin-shell LLM caller.

设计：moni 不持有任何 API key。所有 key 由用户在 ~/.hermes/config.yaml
或 ~/.hermes/.env 配好。moni 通过 subprocess 调本机 `hermes chat` CLI，
由 Hermes 负责实际 provider 调用 + key 管理。

PRD §4.4 / §4.7 薄壳模式。

v1.0.42 (sprint-22): ``call_llm`` thin wrapper sh  routed through
``moni.llm.runner.FailoverRunner`` for circuit-breaker + process-group
kill + argv pre-check. Signatures are unchanged so callers don't have
to migrate.
"""
from __future__ import annotations

import logging
import json
import os
import re
import warnings
from typing import Any

from .runner import (
    CallRequest,
    CallResult,
    FailoverExhausted,
    HardenedSubprocess,
    LLMRunnerError,
    PromptTooLargeError,
)
from .token_log import TokenLogger

logger = logging.getLogger(__name__)

#: Subprocess timeout for a single LLM call (seconds).
#: v1.0.5.1: bumped from 60s -> 300s.  MiniMax-M3 (the active thinking
#: model on this machine) routinely takes 90-180s on a single prompt and
#: was tripping the 60s wall.  5 minutes is a generous upper bound; the
#: retry logic in ``moni.llm.jobs`` will catch true failures.
_TIMEOUT_SECONDS = 300

#: Rough token estimation: English ~4 chars/token, Chinese ~1.5 chars/token.
#: (Coarse estimate — not meant to match provider billing exactly.)
_CN_RANGE = re.compile(r"[\u4e00-\u9fff]")


def estimate_tokens(text: str) -> int:
    """Crude token estimation. PRD §4.4 用法。

    - Chinese chars: ~1.5 chars/token  →  divide by 1.5 (= multiply by 2/3)
    - Other chars:   ~4   chars/token  →  divide by 4

    Returns 0 for empty / None.
    """
    if not text:
        return 0
    cn = sum(1 for _ in _CN_RANGE.findall(text))
    other = len(text) - cn
    # cn * 2 // 3 ≈ cn / 1.5 (rounded)
    return cn * 2 // 3 + other // 4


def resolve_active_model(key: str = "active_summary_model_id") -> tuple[str, str]:
    """从 llm_settings 解析当前激活 (provider, model_id)。

    v1.0.35.19: 抽取管道统一入口。此前 cases/events pipeline 硬编码
    minimax-cn/MiniMax-M3 (2026-08-24 已 429 配额耗尽), 导致批量提取必失败。
    回退链: 指定 key → active_summary_model_id → 任意 active provider 模型。

    Args:
        key: llm_settings 中的模型 key (默认 active_summary_model_id,
            与案例/事件抽取同为"长文→结构化"任务)。

    Returns:
        (hermes_provider, model_id) e.g. ("ark", "ark-code-latest")

    Raises:
        RuntimeError: 没有任何可用模型配置。
    """
    from moni.db.connection import get_db

    conn = get_db()
    keys = (key, "active_summary_model_id") if key != "active_summary_model_id" else (key,)
    for k in keys:
        row = conn.execute(
            "SELECT value FROM llm_settings WHERE key = ?", (k,)
        ).fetchone()
        if not row or not row["value"]:
            continue
        model_row = conn.execute(
            """
            SELECT m.model_id, p.hermes_provider
            FROM llm_models m
            JOIN llm_providers p ON p.id = m.provider_id
            WHERE m.id = ?
            """,
            (row["value"],),
        ).fetchone()
        if model_row:
            return model_row["hermes_provider"], model_row["model_id"]

    # 兜底: 任意 active provider 的启用模型
    row = conn.execute(
        """
        SELECT m.model_id, p.hermes_provider
        FROM llm_models m
        JOIN llm_providers p ON p.id = m.provider_id
        WHERE p.is_active = 1 AND m.enabled = 1
        ORDER BY m.created_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row:
        return row["hermes_provider"], row["model_id"]
    raise RuntimeError("No active LLM model configured in llm_settings")


# ── v1.0.42: single-process runner cache (avoid rebuilding per call) ────────
_RUNNER_CACHE: dict[str, Any] = {}


def _get_runner() -> Any:
    """Build or fetch a cached FailoverRunner keyed on db path.

    Building the runner reads llm_settings (a SELECT) — cheap, but the
    ChainBreaker dict allocation + signature inspection costs ~1ms; we
    cache so a tight loop doesn't redo it 10x/sec.
    """
    from moni.db.connection import get_db_path
    from .runner import FailoverRunner

    key = str(get_db_path())
    cached = _RUNNER_CACHE.get(key)
    if cached is not None:
        return cached
    runner = FailoverRunner.from_settings()
    _RUNNER_CACHE[key] = runner
    return runner


def call_llm(
    provider: str,
    model: str,
    prompt: str,
    *,
    timeout: int = _TIMEOUT_SECONDS,
    extra_args: list[str] | None = None,
    usage_file: str | None = None,
    usage: bool = False,
) -> str | tuple[str, dict]:
    """Call Hermes CLI and return the reply text (plus usage when asked).

    v1.0.42: thin wrapper over FailoverRunner — see runner.py for full
    semantics. Same signature as before so existing callers don't need
    to change. Uses ``runner.call(CallRequest)`` so we get:
    - circuit breaker (fail-fast on provider 429 storms)
    - failover chain (advance to next model on failure)
    - process-group kill (zero orphan grandchildren)
    - argv pre-check (PromptTooLargeError instead of OSError E2BIG)
    """
    req = CallRequest(
        provider=provider,
        model_id=model,
        prompt=prompt,
        timeout=timeout,
        extra_args=extra_args,
        usage_file=usage_file,
    )
    runner = _get_runner()
    try:
        result: CallResult = runner.call(req)
    except PromptTooLargeError:
        raise  # explicit; caller (jobs.py retry path) can decide
    except FailoverExhausted as exc:
        # Preserve old RuntimeError contract for backwards compat.
        msg = "hermes failed (failover exhausted): " + " | ".join(
            f"{m}: {e}" for m, e in exc.errors
        )
        raise RuntimeError(msg) from exc
    except LLMRunnerError as exc:
        raise RuntimeError(f"hermes failed: {exc}") from exc

    reply = _parse_session_id_prefix(result.reply_text)
    usage_info = {
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost_usd": result.cost_usd,
        "raw": {},
    }

    # Log estimated usage (matches old behavior)
    try:
        TokenLogger().log(
            model=model,
            prompt_tokens=estimate_tokens(prompt),
            completion_tokens=estimate_tokens(result.reply_text),
            extra={"provider": provider, "source": "hermes_chat_cli"},
        )
    except Exception as exc:  # logging must not fail the call
        logger.debug("TokenLogger.log failed: %s", exc)

    if usage:
        return reply, usage_info
    return reply


def _parse_session_id_prefix(stdout: str) -> str:
    """Strip leading ``session_id: <id>`` line from hermes stdout."""
    lines = (stdout or "").splitlines()
    content_lines = [ln for ln in lines if not ln.startswith("session_id:")]
    return "\n".join(content_lines).strip()


def call_llm_json(
    provider: str,
    model: str,
    prompt: str,
    *,
    timeout: int = _TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Call LLM and parse the reply as JSON.

    Strips leading/trailing ```json fences if present. Returns {} on parse error.
    """
    import json
    raw = call_llm(provider, model, prompt, timeout=timeout)
    text = raw.strip()
    if text.startswith("```"):
        # Strip ```json ... ``` or ``` ... ```
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("LLM reply is not valid JSON: %s; raw=%r", exc, raw[:200])
        return {}


# Backwards-compat shim — DEPRECATED. Use ``call_llm()`` directly.
# v1.0.42: raising DeprecationError on .chat() so callers know to migrate;
# silenced to keep CLI / older callers working until v1.0.43.
class ChatClient:
    """Deprecated. Use the module-level ``call_llm()`` function instead.

    Kept so existing imports ``from moni.llm.client import ChatClient`` don't
    break. ``.chat()`` ignores kwargs that are no longer supported and
    silently calls ``call_llm()`` with the configured default model —
    behaviour matches v1.0.41 (last green release).
    """

    _instance: "ChatClient | None" = None

    def __init__(self) -> None:
        if ChatClient._instance is not None:
            raise RuntimeError("Use ChatClient.get_instance() to obtain the singleton")
        self._token_logger = TokenLogger()
        warnings.warn(
            "ChatClient is deprecated; use moni.llm.client.call_llm() directly",
            DeprecationWarning,
            stacklevel=2,
        )

    @classmethod
    def get_instance(cls) -> "ChatClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def chat(self, prompt: str, **kwargs: Any) -> str:
        """Deprecated pass-through. New code should call ``call_llm()`` directly.

        v1.0.42: kwargs are ignored (model/max_tokens/temperature were
        silently dropped in v1.0.41 anyway — see P0-1 audit). The
        active model comes from ``resolve_active_model()`` instead.
        """
        provider, model = resolve_active_model()
        return call_llm(provider, model, prompt)
