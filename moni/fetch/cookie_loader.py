"""Helpers for loading persisted HTTP cookies from ``config/<source>_cookies.json``.

The 36kr fetcher is the first (and only, so far) consumer.  When
36kr.com is hit via plain ``httpx`` it returns the TTGCaptcha
challenge page (no captcha challenge if the client has a real
``krtoken`` cookie, but captcha-free access still requires the
fingerprint of a real browser).  Our workaround: persist a working
cookie set from a logged-in headless Chrome session (see
``scripts/dump_36kr_cookies.py``) and replay it via ``httpx`` so
the captcha gate sees a known client.

Adding a new source
-------------------
1. Add a JSON file at ``config/<source>_cookies.json`` with the same
   shape (see ``36kr_cookies.json``).
2. Call ``load_cookies_for(<source>)`` at fetch time; merge into the
   ``httpx`` request's cookie jar.

The function returns an empty dict if the file is missing or
malformed — the fetcher should treat that as "no cookies, use bare
httpx" and proceed; the existing captcha-tolerant logic in
``Fetcher36krRSS.fetch`` will still warn + return ``[]`` rather
than crash.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# config/ dir lives at the repo root, two levels up from moni/fetch/.
_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


def load_cookies_for(source: str) -> dict[str, str]:
    """Return a name->value dict of cookies for the given source.

    Reads ``config/<source>_cookies.json``.  Returns an empty dict
    when the file is missing or malformed; never raises.
    """
    path = _CONFIG_DIR / f"{source}_cookies.json"
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "[cookies] failed to read %s (%s) — falling back to no cookies",
            path, exc,
        )
        return {}
    cookies = data.get("cookies")
    if not isinstance(cookies, list):
        return {}
    out: dict[str, str] = {}
    for c in cookies:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        value = c.get("value")
        if isinstance(name, str) and isinstance(value, str) and name and value:
            out[name] = value
    if out:
        logger.info("[cookies] loaded %d cookie(s) for %s", len(out), source)
    return out
