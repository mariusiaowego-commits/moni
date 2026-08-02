"""moni.fetch.xwatch_syndication — Pull a single tweet from twitter's syndication CDN.

The syndication endpoint at ``cdn.syndication.twimg.com/tweet-result`` is a
public, unauthenticated JSON API used by twitter's own oEmbed-style embeds.
It returns the full tweet object (text, user, timestamps, engagement
counters) without the HTML noise you'd get from the web UI.

Why this works without authentication
------------------------------------

The endpoint exists specifically so client-side widgets (and content
aggregators like ours) can render tweet previews; it does not require a
bearer token or session cookie.  It DOES, however, 404 on tweets that
are protected, deleted, or withheld by twitter — we treat those as
"unavailable" and return ``None`` rather than retrying.

Why we use a token parameter
----------------------------

The endpoint accepts an arbitrary ``token`` value; the real validation
happens against the tweet id.  Any short string ("a", "0", "x") keeps
the request shape identical and avoids triggering CloudFlare's bot
heuristic that the genuine twitter web client uses.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

SYNDICATION_URL = "https://cdn.syndication.twimg.com/tweet-result"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

# Match `<host>/<user>/status/<digits>` — host can be x.com or twitter.com
# (the spec sample uses x.com, but historical tweets in archives may
# still link twitter.com).  Group(1) is the digits.
STATUS_URL_RE = re.compile(r"https?://(?:x|twitter)\.com/[^/]+/status/(\d+)")


def tweet_id_from_url(url: str) -> str | None:
    """Pull a tweet id out of a URL — or pass through a bare digit string.

    * ``https://x.com/<user>/status/12345``       → ``"12345"``
    * ``http://twitter.com/<user>/status/12345``  → ``"12345"``
    * ``"12345"`` (pure digits)                    → ``"12345"``
    * ``"https://example.com/foo"``                 → ``None``
    """
    if not url:
        return None
    s = url.strip()
    if s.isdigit():
        return s
    match = STATUS_URL_RE.search(s)
    return match.group(1) if match else None


def fetch_tweet(tweet_id: str, *, timeout: int = 20) -> dict | None:
    """Fetch a single tweet as a Python dict, or ``None`` on failure.

    Never raises.  The syndication endpoint returns a JSON object whose
    shape mirrors the public-facing tweet object (``id_str``, ``text``,
    ``user.screen_name``, ``created_at`` as RFC-822 UTC).  For deleted /
    withheld tweets it returns either an HTTP 404 or a JSON body with
    ``__typename == "TweetWithheldError"`` — we treat both as a ``None``
    result, never an exception.

    v1.0.11 note: 429 responses are logged but not retried here —
    ``FetcherXWatch`` already spaces requests via ``request_delay``.
    """
    if not tweet_id:
        return None
    try:
        resp = httpx.get(
            SYNDICATION_URL,
            params={"id": tweet_id, "lang": "zh", "token": "a"},
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        )
    except Exception as exc:
        logger.warning("[xwatch] syndication request failed for %s: %s", tweet_id, exc)
        return None

    if resp.status_code == 404:
        logger.debug("[xwatch] syndication 404 for %s", tweet_id)
        return None
    if resp.status_code == 429:
        logger.warning("[xwatch] syndication 429 for %s — skipping", tweet_id)
        return None
    if resp.status_code >= 400:
        logger.warning(
            "[xwatch] syndication HTTP %d for %s", resp.status_code, tweet_id,
        )
        return None

    try:
        data = resp.json()
    except Exception as exc:
        logger.warning("[xwatch] syndication JSON decode failed for %s: %s", tweet_id, exc)
        return None

    if not isinstance(data, dict):
        return None
    # twitter sometimes returns a one-key {"errors":[...]} envelope on failure
    if "errors" in data and not data.get("id_str"):
        logger.debug("[xwatch] syndication error envelope for %s: %s", tweet_id, data)
        return None
    return data


def _normalize_published_at(raw: str | None) -> str | None:
    """Convert twitter's RFC-822 ``created_at`` to an ISO8601 in UTC.

    Twitter emits e.g. ``Wed Oct 22 21:30:00 +0000 2026``.  We keep the
    timezone (``+0000`` in this case is almost always the truth), then
    strip seconds and force TZ to ``+00:00`` so downstream consumers
    have a single ISO format to parse.
    """
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
    except (TypeError, ValueError):
        # Fall back: maybe already ISO8601 with a trailing Z
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    # Force UTC offset and ISO format (e.g. 2026-10-22T21:30:00+00:00).
    dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def tweet_to_article(
    data: dict,
    *,
    source: str = "xwatch",
    fallback_url: str | None = None,
) -> dict | None:
    """Map a syndication JSON dict → canonical article dict.

    Returns ``None`` if the input is missing the fields we need (text /
    user / id_str); the caller is expected to skip those articles
    without raising.

    Schema contract
    ---------------
    The returned dict has exactly the keys BaseFetcher.run() expects:

    * ``title``            first line of the tweet, 80-char cap
    * ``url``              canonical https://x.com/<screen_name>/status/<id>
                          (uses ``fallback_url`` if user is missing)
    * ``source``           the caller-provided source name (default "xwatch")
    * ``published_at``     ISO 8601 UTC string (None if unparseable)
    * ``content``          full text — engagement counters excluded so the
                          LLM summariser sees only the tweet body
    * ``publisher``        ``@screen_name`` form for easy scanning
    """
    if not isinstance(data, dict):
        return None

    text = (data.get("text") or "").strip()
    if not text:
        return None

    id_str = data.get("id_str")
    user = data.get("user") or {}
    screen_name = user.get("screen_name") if isinstance(user, dict) else None

    if id_str:
        if screen_name:
            url = f"https://x.com/{screen_name}/status/{id_str}"
        elif fallback_url:
            url = fallback_url
        else:
            url = f"https://x.com/i/status/{id_str}"
    else:
        # No id_str → cannot build a URL; refuse to ingest.
        return None

    title = text.splitlines()[0] if text else ""
    if len(title) > 80:
        # Reserve 1 char for the ellipsis suffix so the result stays at 80.
        title = title[:79].rstrip() + "…"

    return {
        "title": title,
        "url": url,
        "source": source,
        "published_at": _normalize_published_at(data.get("created_at")),
        "content": text,
        "publisher": f"@{screen_name}" if screen_name else None,
    }
