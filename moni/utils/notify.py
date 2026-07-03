"""moni.utils.notify — lightweight failure notifications.

Channels (configured via .env):
- BARK_URL=https://api.day.app/xxxx (Bark iOS push)
- TG_BOT_TOKEN=xxx / TG_CHAT_ID=xxx (Telegram message)

All honour MONI_NOTIFY_SILENT=1 to suppress during testing.
Falls back to logging when no channel is configured.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 5  # fast fail — notification must never block the main flow


def _should_silence() -> bool:
    return os.environ.get("MONI_NOTIFY_SILENT") == "1"


def send_notification(
    subject: str,
    body: str,
    *,
    level: str = "error",
) -> None:
    """Send a notification via the first available channel.

    Args:
        subject: Short title (also used as Bark title, TG message prefix).
        body: Detailed message text.
        level: 'error' | 'warn' | 'info' — affects Bark group/emoji.
    """
    if _should_silence():
        logger.info("[notify] SILENT mode, NOT sending: %s — %s", subject, body[:80])
        return

    # ── Try Bark (iOS push) ──────────────────────────────────────────────
    bark_url = os.environ.get("BARK_URL")
    if bark_url:
        try:
            _send_bark(bark_url, subject, body, level)
            return
        except Exception as exc:
            logger.warning("[notify] Bark failed: %s", exc)
            # fall through to next channel

    # ── Try Telegram ─────────────────────────────────────────────────────
    tg_token = os.environ.get("TG_BOT_TOKEN")
    tg_chat = os.environ.get("TG_CHAT_ID")
    if tg_token and tg_chat:
        try:
            _send_telegram(tg_token, tg_chat, subject, body)
            return
        except Exception as exc:
            logger.warning("[notify] Telegram failed: %s", exc)
            # fall through

    # ── Fallback: log ────────────────────────────────────────────────────
    emoji = {"error": "❌", "warn": "⚠️", "info": "ℹ️"}.get(level, "🔔")
    logger.info("%s [notify] %s — %s", emoji, subject, body[:200])


def _send_bark(url: str, subject: str, body: str, level: str) -> None:
    """Send via Bark (iOS push)."""
    # Bark URL format: https://api.day.app/KEY/Title/Body
    import urllib.parse

    title_enc = urllib.parse.quote(subject)
    body_enc = urllib.parse.quote(body[:2000])  # 2k char limit
    tmp = f"{url}/{title_enc}/{body_enc}"
    # Group by level so errors don't mix with info
    group = "moni-" + level
    sep = "&" if "?" in tmp else "?"
    full = f"{tmp}{sep}group={group}"

    resp = httpx.get(full, timeout=_TIMEOUT)
    resp.raise_for_status()


def _send_telegram(token: str, chat_id: str, subject: str, body: str) -> None:
    """Send via Telegram Bot API."""
    text = f"🔔 *{subject}*\n\n{body[:3000]}"  # 4k limit, leave room for formatting
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"

    resp = httpx.post(
        api_url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
