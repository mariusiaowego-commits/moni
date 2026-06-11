"""moni.llm.client — Thin-shell LLM caller.

设计：moni 不持有任何 API key。所有 key 由用户在 ~/.hermes/config.yaml
或 ~/.hermes/.env 配好。moni 通过 subprocess 调本机 `hermes chat` CLI，
由 Hermes 负责实际 provider 调用 + key 管理。

PRD §4.4 / §4.7 薄壳模式。
"""
from __future__ import annotations

import logging
import re
import subprocess
from typing import Any

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


def call_llm(
    provider: str,
    model: str,
    prompt: str,
    *,
    timeout: int = _TIMEOUT_SECONDS,
    extra_args: list[str] | None = None,
    module: str = "",
) -> str:
    """Call Hermes chat CLI and return the text reply.

    Args:
        provider: hermes provider name (e.g. "google", "openrouter", "minimax-cn").
        model: model id passed via `-m`.
        prompt: full prompt text.
        timeout: subprocess timeout in seconds.
        extra_args: optional additional CLI flags.

    Returns:
        Reply text (stripped). Strips the leading "session_id: ..." line.

    Raises:
        RuntimeError: if hermes chat exits non-zero or times out.
    """
    cmd = [
        "hermes", "chat",
        "-q", prompt,
        "--provider", provider,
        "-m", model,
        "-Q",
    ]
    if extra_args:
        cmd.extend(extra_args)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"hermes chat timeout after {timeout}s (provider={provider} model={model})"
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            "hermes CLI not found on PATH. Install Hermes Agent or fix $PATH."
        ) from exc

    if result.returncode != 0:
        err = (result.stderr or "").strip()[:300]
        raise RuntimeError(
            f"hermes chat failed (rc={result.returncode}): {err}"
        )

    # Parse stdout: first line is "session_id: <id>", remainder is content
    lines = (result.stdout or "").splitlines()
    content_lines = [ln for ln in lines if not ln.startswith("session_id:")]
    reply = "\n".join(content_lines).strip()

    # Log estimated usage
    try:
        TokenLogger().log(
            model=model,
            prompt_tokens=estimate_tokens(prompt),
            completion_tokens=estimate_tokens(reply),
            module=module,
            extra={"provider": provider, "source": "hermes_chat_cli"},
        )
    except Exception as exc:  # logging must not fail the call
        logger.debug("TokenLogger.log failed: %s", exc)

    return reply


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


# Backwards-compat shim — older callers may still use ChatClient.get_instance().
class ChatClient:
    """Deprecated. Use the module-level call_llm() function instead.

    Kept so existing imports `from moni.llm.client import ChatClient` don't break
    before the F4 routes are updated to use call_llm() directly.
    """

    _instance: "ChatClient | None" = None

    def __init__(self) -> None:
        if ChatClient._instance is not None:
            raise RuntimeError("Use ChatClient.get_instance() to obtain the singleton")
        self._token_logger = TokenLogger()

    @classmethod
    def get_instance(cls) -> "ChatClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def chat(self, prompt: str, **kwargs: Any) -> str:
        """Deprecated pass-through. New code should call call_llm() directly."""
        provider = kwargs.get("provider", "minimax-cn")
        model = kwargs.get("model", "MiniMax-M3")
        return call_llm(provider, model, prompt)
