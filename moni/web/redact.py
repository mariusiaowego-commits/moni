"""moni/web/redact.py — out-bound credential redaction for the web API (v1.0.42).

Audit P1-10: ``GET /api/config/sources`` returned ``sources.url`` verbatim,
and the two WeChat aggregator sources carry a long-lived JWT in their query
string (``…/api/rss/all?token=eyJhbGci…``).  Anything able to open the 8900
UI therefore received that credential.

Scope — **out-bound only**.  ``sources.url`` is the *fetch input*; masking it
before storage would break RSS pulling (the aggregator would answer 401), so
redaction happens exclusively at the API boundary.  See
``docs/plans/2026-09-10-sprint-22-safety-primitives.md``.

The parameter-name set mirrors the long-standing masking already used by the
offline showcase generator (``scripts/build_showcase_html.py``: ``_TOKEN_QS``
/ ``_JWT``) so demo output and the production API agree on what "a
credential" looks like instead of drifting into two implementations.
"""

from __future__ import annotations

import re

#: Placeholder that replaces a credential value.  Kept short and non-ASCII so
#: it can never be mistaken for (or confused with) a real token.
MASK = "…"

#: Query parameter names whose value is always a credential.  Superset of the
#: showcase generator's list; compared case-insensitively with ``-`` folded
#: to ``_`` (so ``access-token`` / ``Access_Token`` both match).
_TOKEN_PARAM_NAMES = frozenset(
    {
        "token",
        "poc_token",
        "access_token",
        "refresh_token",
        "id_token",
        "auth_token",
        "secret",
        "client_secret",
        "api_key",
        "apikey",
        "key",
        "auth",
        "authorization",
        "password",
        "passwd",
        "jwt",
        "signature",
        "sig",
    }
)

#: A JWT-shaped value (``eyJ….….…``) is masked even under an unexpected
#: parameter name — same pattern the showcase generator uses.
_JWT_VALUE_RE = re.compile(r"^eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}$")


def _is_token_param(name: str) -> bool:
    return name.strip().lower().replace("-", "_") in _TOKEN_PARAM_NAMES


def _is_credential_value(value: str) -> bool:
    return _JWT_VALUE_RE.match(value) is not None


def sanitize_source_url(url: str | None) -> str | None:
    """Mask credential values in a URL query string, preserving param names.

    Keeps ``token=…`` readable as "this source carries a credential" while
    removing the value itself.  Deliberately conservative:

    * no query string / no credential → returned **unchanged** (identity), so
      the 13 non-credential sources in ``sources`` are byte-identical;
    * already-masked input → returned unchanged (**idempotent**);
    * non-``str`` input, or any internal failure → returned **unchanged**: a
      security primitive must never become a new source of 500s.
    """
    if not isinstance(url, str) or "?" not in url:
        return url
    try:
        head, _, tail = url.partition("?")
        fragment = ""
        if "#" in tail:
            tail, _, frag_body = tail.partition("#")
            fragment = "#" + frag_body
        if not tail:
            return url

        changed = False
        out: list[str] = []
        for segment in tail.split("&"):
            if not segment:
                out.append(segment)
                continue
            name, sep, value = segment.partition("=")
            if sep and value and value != MASK and (
                _is_token_param(name) or _is_credential_value(value)
            ):
                out.append(f"{name}={MASK}")
                changed = True
                continue
            out.append(segment)

        if not changed:
            return url
        return f"{head}?{'&'.join(out)}{fragment}"
    except Exception:  # pragma: no cover - defensive, primitive must not raise
        return url


def is_masked_url(url: str | None) -> bool:
    """True when ``url`` is a redacted (masked) form produced by this module.

    Used by the update path to mean "client sent the masked value back = it
    did not change the URL" — writing ``…`` into ``sources.url`` would break
    fetching, so the stored value must be preserved instead.
    """
    if not isinstance(url, str) or "?" not in url:
        return False
    try:
        _, _, tail = url.partition("?")
        if "#" in tail:
            tail, _, _ = tail.partition("#")
        for segment in tail.split("&"):
            name, sep, value = segment.partition("=")
            if sep and value == MASK and _is_token_param(name):
                return True
    except Exception:  # pragma: no cover - defensive
        return False
    return False
