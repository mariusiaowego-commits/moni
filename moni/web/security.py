"""Local-origin guard: block CSRF and DNS-rebinding against the loopback web UI.

v1.0.66.  The web service binds ``127.0.0.1`` with no auth, on the
assumption that "only this machine can reach it".  But the browser *is*
on this machine, so any page the user opens can talk to it:

* **CSRF** — a POST with no body (or ``text/plain`` / form body) is a CORS
  "simple request": the browser sends it without a preflight.  CORS only
  hides the *response*; the handler still runs.  ``POST /api/service/stop``,
  ``/api/fetch/all/run`` or the LLM endpoints were all reachable this way.
* **DNS rebinding** — an attacker domain re-resolves to 127.0.0.1, after
  which the browser treats it as same-origin and can read *and* write
  every endpoint.  Only a ``Host`` header check stops this.

Rules (dad 2026-11-03, Q1–Q4 = A):

1. Every request: the ``Host`` hostname must be loopback (``127.0.0.1`` /
   ``localhost`` / ``::1``), ``testserver`` (Starlette TestClient) or listed
   in ``MONI_ALLOWED_HOSTS`` (comma separated).  Port is not checked so
   8900 / 8901 / 8902 all work.
2. Unsafe methods (and websocket handshakes): if the browser says
   ``Sec-Fetch-Site`` is anything other than ``same-origin`` / ``none``, or
   sends an ``Origin`` that is ``null`` or differs from ``Host``, reject.
3. No ``Origin`` and no ``Sec-Fetch-Site`` means a non-browser client
   (curl, CLI, hermes, scripts), which CSRF cannot forge, so it is allowed.

Rejections return ``403 {"code": "forbidden_origin", ...}`` and log a warning.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

#: Hostnames always accepted in the ``Host`` header (port-agnostic).
DEFAULT_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {"127.0.0.1", "localhost", "::1", "testserver"}
)

#: Methods that must not change state; only the Host check applies.
SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

#: ``Sec-Fetch-Site`` values that prove the request came from our own page
#: (or from the user typing in the address bar).
_TRUSTED_FETCH_SITES: frozenset[str] = frozenset({"same-origin", "none"})

ERROR_CODE = "forbidden_origin"

# Reason strings (also used in logs and the response ``details``).
REASON_HOST = "host_not_allowed"
REASON_FETCH_SITE = "cross_site_request"
REASON_ORIGIN = "origin_mismatch"


def allowed_hosts() -> frozenset[str]:
    """Return the default hosts plus any listed in ``MONI_ALLOWED_HOSTS``.

    Read on every call so tests (and a restarted service) pick up changes.
    """
    extra = os.environ.get("MONI_ALLOWED_HOSTS", "")
    names = {_normalize_hostname(h) for h in extra.split(",") if h.strip()}
    return DEFAULT_ALLOWED_HOSTS | frozenset(n for n in names if n)


def _normalize_hostname(value: str) -> str:
    """Lower-case a host name and strip IPv6 brackets."""
    value = value.strip().lower()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return value


def hostname_of(host_header: str) -> str:
    """Extract the hostname from a ``Host`` header (drop the port).

    ``"127.0.0.1:8900"`` -> ``"127.0.0.1"``, ``"[::1]:8900"`` -> ``"::1"``.
    Returns ``""`` for anything unparsable or containing userinfo.
    """
    try:
        raw = host_header.strip()
        if not raw or " " in raw or "@" in raw:
            return ""
        parsed = urlsplit("//" + raw)
        if parsed.username or parsed.password:
            return ""
        return (parsed.hostname or "").lower()
    except ValueError:
        return ""


def check_request(method: str, headers: Mapping[str, str]) -> str | None:
    """Decide whether a request may proceed.

    ``headers`` must use lower-case keys.  Returns ``None`` when allowed,
    otherwise one of the ``REASON_*`` strings.
    """
    host = headers.get("host")
    if host is not None and hostname_of(host) not in allowed_hosts():
        return REASON_HOST

    if method.upper() in SAFE_METHODS:
        return None

    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site.strip().lower() not in _TRUSTED_FETCH_SITES:
        return REASON_FETCH_SITE

    origin = headers.get("origin")
    if origin is not None:
        origin = origin.strip()
        if origin.lower() == "null" or host is None:
            return REASON_ORIGIN
        try:
            origin_netloc = urlsplit(origin).netloc.lower()
        except ValueError:
            return REASON_ORIGIN
        if not origin_netloc or origin_netloc != host.strip().lower():
            return REASON_ORIGIN

    return None


Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class LocalOriginGuard:
    """Pure ASGI middleware applying :func:`check_request`.

    Pure ASGI (not ``BaseHTTPMiddleware``) so streaming / background
    responses pass through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope.get("type")
        if scope_type not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers") or []
        }
        # A websocket handshake is a GET but carries ambient credentials and
        # opens a two-way channel, so treat it like an unsafe method.
        method = "WEBSOCKET" if scope_type == "websocket" else scope.get("method", "GET")
        reason = check_request(method, headers)
        if reason is None:
            await self.app(scope, receive, send)
            return

        logger.warning(
            "blocked %s %s reason=%s host=%r origin=%r sec-fetch-site=%r",
            method,
            scope.get("path", ""),
            reason,
            headers.get("host"),
            headers.get("origin"),
            headers.get("sec-fetch-site"),
        )

        if scope_type == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return

        body = json.dumps(
            {
                "code": ERROR_CODE,
                "message": "Request blocked: cross-origin or untrusted host",
                "details": {"reason": reason},
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


__all__ = [
    "DEFAULT_ALLOWED_HOSTS",
    "ERROR_CODE",
    "LocalOriginGuard",
    "REASON_FETCH_SITE",
    "REASON_HOST",
    "REASON_ORIGIN",
    "SAFE_METHODS",
    "allowed_hosts",
    "check_request",
    "hostname_of",
]
