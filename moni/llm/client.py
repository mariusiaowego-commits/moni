"""ChatClient — singleton LLM client with Hermes-first fallback and token logging."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from .token_log import TokenLogger

logger = logging.getLogger(__name__)

#: Maximum retries on transient HTTP errors
_MAX_RETRIES = 3
#: Base delay for exponential backoff (seconds)
_BACKOFF_BASE = 1.5


class ChatClient:
    """Singleton LLM chat client.

    Provider priority:
    1. Hermes internal chat API (``HERMES_CHAT_URL`` env var or
       ``http://localhost:8080/chat``).
    2. OpenAI-compatible API (``OPENAI_API_KEY`` env var,
       ``OPENAI_BASE_URL`` defaults to ``https://api.openai.com/v1``).

    Each call is retried up to 3 times with exponential backoff on
    HTTP 429 / 5xx responses.  Successful calls are logged by
    :class:`TokenLogger`.
    """

    _instance: "ChatClient | None" = None

    def __init__(self) -> None:
        if ChatClient._instance is not None:
            raise RuntimeError("Use ChatClient.get_instance() to obtain the singleton")
        self._hermes_url = os.environ.get(
            "HERMES_CHAT_URL", "http://localhost:8080/chat"
        )
        self._openai_key = os.environ.get("OPENAI_API_KEY", "")
        self._openai_base = os.environ.get(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )
        self._token_logger = TokenLogger()

    @classmethod
    def get_instance(cls) -> "ChatClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def chat(
        self,
        prompt: str,
        *,
        model: str = "claude-3-5-sonnet-latest",
        max_tokens: int = 1024,
        temperature: float = 0.3,
        system: str | None = None,
    ) -> str:
        """Send a chat prompt and return the assistant's text reply.

        Args:
            prompt: The user message.
            model: Model name to pass to the provider.
            max_tokens: Maximum tokens in the completion.
            temperature: Sampling temperature (0–2).
            system: Optional system message.

        Returns:
            The assistant's reply text.

        Raises:
            RuntimeError: When neither Hermes nor OpenAI is configured.
        """
        # Try Hermes first
        if self._try_hermes(prompt, model=model, max_tokens=max_tokens,
                            temperature=temperature, system=system):
            return self._last_reply  # type: ignore[return-value]

        # Fallback to OpenAI-compatible
        if self._openai_key:
            return self._openai_chat(
                prompt, model=model, max_tokens=max_tokens,
                temperature=temperature, system=system
            )

        raise RuntimeError(
            "No LLM provider available. Set HERMES_CHAT_URL or OPENAI_API_KEY."
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _try_hermes(
        self,
        prompt: str,
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        system: str | None,
    ) -> bool:
        """Attempt a Hermes internal chat call.

        Returns:
            True if the call succeeded (and self._last_reply is set).
            False if Hermes is unavailable — caller should fall back.
        """
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(self._hermes_url, json=payload)
        except Exception as exc:
            logger.debug("Hermes chat unavailable: %s", exc)
            return False

        if resp.status_code == 200:
            data = resp.json()
            self._last_reply = data.get("reply") or data.get("content", "")
            # Log token usage if provided
            usage = data.get("usage") or data.get("token_usage", {})
            if usage:
                self._token_logger.log(
                    model=model,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    extra={"provider": "hermes"},
                )
            return True

        logger.debug("Hermes chat returned %s", resp.status_code)
        return False

    def _openai_chat(
        self,
        prompt: str,
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        system: str | None,
    ) -> str:
        """Call an OpenAI-compatible endpoint with retry logic."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        headers = {
            "Authorization": f"Bearer {self._openai_key}",
            "Content-Type": "application/json",
        }

        url = f"{self._openai_base.rstrip('/')}/chat/completions"
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                with httpx.Client(timeout=60.0) as client:
                    resp = client.post(url, json=payload, headers=headers)
            except Exception as exc:
                last_exc = exc
                logger.warning("OpenAI request attempt %s failed: %s", attempt + 1, exc)
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** attempt)
                continue

            if resp.status_code == 200:
                data = resp.json()
                reply = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                self._token_logger.log(
                    model=model,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    extra={"provider": "openai"},
                )
                return reply

            if resp.status_code in (429, 500, 502, 503, 504):
                last_exc = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                logger.warning(
                    "OpenAI attempt %s returned %s, retrying...",
                    attempt + 1, resp.status_code,
                )
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE ** attempt)
                continue

            # Non-retryable error
            resp.raise_for_status()
            raise RuntimeError(f"Unexpected response: {resp.status_code}")

        raise RuntimeError(
            f"OpenAI call failed after {_MAX_RETRIES} attempts"
        ) from last_exc

    # Placeholder; set by _try_hermes on success
    _last_reply: str = ""
