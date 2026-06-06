"""FetcherMpaypass — mpaypass.com.cn (https://www.mpaypass.com.cn/).

v1.0.3 deep-crawl:
  1. ``fetch()`` returns the list of article URLs harvested from the homepage.
     /download/ resource pages are filtered out at this stage.
  2. ``_fetch_article(url)`` deep-crawls the detail page to extract:
        - title  (from <title>, stripping the trailing " -移动支付网" suffix)
        - published_at  (from <div class="newsbody-author"> first <span>)
        - content (text from <div class="newsbody">)
        - publisher (from the "来源：" segment of the author line, if present)
  3. ``parse(raw)`` is intentionally a pass-through that defers to
     ``_fetch_article`` when ``raw`` only contains ``url``. This keeps the
     test surface small while still giving callers the full content.

The deep-crawl adds at most N additional HTTP requests per fetch run.
The homepage already has a typical ceiling (~70 article links), so the
upper bound is ~70 detail requests; this is acceptable given the v1.0.2
filter pipeline already runs per-article and the source has a fetch
interval of 30 minutes.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

from ..base import BaseFetcher, register_fetcher

logger = logging.getLogger(__name__)

FEED_URL = "https://www.mpaypass.com.cn/"

# Detail-page articles. We only deep-crawl /news/ (real editorial articles).
# /download/ pages are PDF/资料 links with no editorial body.
# Accept both relative ("/news/202606/...html") and absolute
# ("https://www.mpaypass.com.cn/news/202606/...html") hrefs — the live
# site renders absolute URLs, while some test fixtures use relative.
_NEWS_PATH_RE = re.compile(r"/news/\d+/\d+\.html$")

# Homepage link pattern. mpaypass uses /YYYYMM/NNNNNN.html across both
# /news/ (articles) and /download/ (resources).
_HOME_HREF_RE = re.compile(r"/(news|download)/\d+/\d+\.html$")

_TITLE_SUFFIX_RE = re.compile(r"\s*[-—–]\s*移动支付网\s*$")

# "2026-5-31 21:09" or "2026-05-31 21:09:33" — single-digit month/day allowed.
# "2026-5-31 21:09" or "2026-05-31 21:09:33" — single-digit month/day allowed.
_AUTHOR_DATE_RE = re.compile(
    r"(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?"
)

# "来源：xxx 作者：yyy" — publisher is the "来源" segment, author is the
# "作者" segment. Both optional.
_SOURCE_RE = re.compile(r"来源\s*[\:\uff1a]\s*([^\s\u00a0]+)")
_AUTHOR_RE = re.compile(r"作者\s*[\:\uff1a]\s*([^\s\u00a0]+)")


@register_fetcher
class FetcherMpaypass(BaseFetcher):
    source_name: str = "mpaypass"

    # ── network knobs (overridable from tests via monkeypatch) ─────────────
    detail_timeout: float = 15.0
    detail_retries: int = 1  # 1 retry on transient errors
    detail_sleep: float = 0.0  # set to e.g. 0.05 in tests if you want

    # ── list stage ─────────────────────────────────────────────────────────

    def fetch(self) -> list[dict[str, Any]]:
        """Return URL dicts from the homepage.

        Skips ``/download/`` resource pages at this stage — they're not
        editorial articles and have no body content to extract.
        """
        try:
            resp = httpx.get(FEED_URL, timeout=15.0)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "[mpaypass] HTTP %s fetching %s",
                exc.response.status_code, exc.request.url,
            )
            return []
        except httpx.TimeoutException:
            logger.error("[mpaypass] Timeout fetching %s", FEED_URL)
            return []
        except Exception as exc:
            logger.error("[mpaypass] Unexpected error fetching %s: %s", FEED_URL, exc)
            return []

        try:
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as exc:
            logger.error("[mpaypass] Failed to parse HTML: %s", exc)
            return []

        # harvest candidate hrefs from anywhere on the page
        seen: set[str] = set()
        candidates: list[str] = []
        for a in soup.find_all("a", href=_HOME_HREF_RE):
            href = str(a.get("href") or "").strip()
            if not href:
                continue
            # skip /download/ — they're resource pages, not articles
            if "/download/" in href:
                continue
            # must look like /news/YYYYMM/NNNNNN.html (use search, not match,
            # because the live site renders absolute URLs and the regex
            # only anchors at the trailing end)
            if not _NEWS_PATH_RE.search(href):
                continue
            if href in seen:
                continue
            seen.add(href)
            candidates.append(href)

        # Build absolute URLs
        base = "https://www.mpaypass.com.cn"
        urls: list[dict[str, Any]] = []
        for href in candidates:
            absolute = href if href.startswith("http") else base + href
            urls.append({
                "title": "",           # filled by _fetch_article
                "url": absolute,
                "source": self.source_name,
                "published_at": None,  # filled by _fetch_article
                "content": "",         # filled by _fetch_article
            })

        logger.info(
            "[mpaypass] homepage harvest: %d /news/ candidate URLs (after /download/ filter)",
            len(urls),
        )
        return urls

    # ── detail stage ───────────────────────────────────────────────────────

    def _fetch_article(self, url: str) -> dict[str, Any] | None:
        """Deep-crawl a single article URL.

        Returns a normalised dict with title / published_at / content / publisher
        fields populated, or ``None`` if the page could not be parsed.
        """
        last_exc: Optional[Exception] = None
        for attempt in range(self.detail_retries + 1):
            try:
                resp = httpx.get(url, timeout=self.detail_timeout, follow_redirects=True)
                resp.raise_for_status()
                return self._parse_detail_html(resp.text, url)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
                logger.warning(
                    "[mpaypass] detail network error (attempt %d/%d) %s: %s",
                    attempt + 1, self.detail_retries + 1, url, exc,
                )
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "[mpaypass] detail HTTP %s for %s — skipping",
                    exc.response.status_code, url,
                )
                return None
            except Exception as exc:
                logger.warning("[mpaypass] detail unexpected error %s: %s", url, exc)
                return None
        logger.error(
            "[mpaypass] detail %d attempts failed for %s: %s",
            self.detail_retries + 1, url, last_exc,
        )
        return None

    def _parse_detail_html(self, html: str, url: str) -> dict[str, Any] | None:
        """Extract title / published_at / content / publisher from detail HTML."""
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:
            logger.warning("[mpaypass] detail parse-fail %s: %s", url, exc)
            return None

        title = self._extract_title(soup)
        published_at = self._extract_published_at(soup)
        content = self._extract_content(soup)
        publisher = self._extract_publisher(soup)

        if not title:
            logger.debug("[mpaypass] detail no title: %s", url)
            return None

        return {
            "title": title,
            "url": url,
            "source": self.source_name,
            "published_at": published_at,
            "content": content,
            "publisher": publisher,
        }

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> str:
        # Prefer <h1>; fall back to <title> minus the site suffix.
        h1 = soup.find("h1")
        if h1:
            t = h1.get_text(strip=True)
            if t:
                return t
        if soup.title and soup.title.string:
            return _TITLE_SUFFIX_RE.sub("", soup.title.string.strip())
        return ""

    @staticmethod
    def _extract_published_at(soup: BeautifulSoup) -> str | None:
        # Primary: <div class="newsbody-author"> first <span> contains the date.
        author_box = soup.find(class_="newsbody-author")
        if author_box:
            # Look at the first <span> only — subsequent spans are author/source
            # text fragments whose digits we don't want to grab.
            first_span = author_box.find("span")
            text = first_span.get_text(" ", strip=True) if first_span else author_box.get_text(" ", strip=True)
            dt = FetcherMpaypass._parse_date_text(text)
            if dt:
                return dt.isoformat()
        return None

    @staticmethod
    def _parse_date_text(text: str) -> datetime | None:
        """Parse '2026-5-31 21:09' or '2026-05-31 21:09:33' etc. → UTC datetime."""
        m = _AUTHOR_DATE_RE.search(text)
        if not m:
            return None
        y, mo, d, h, mi, s = m.groups()
        try:
            return datetime(
                int(y), int(mo), int(d), int(h), int(mi), int(s or 0),
                tzinfo=timezone.utc,
            )
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _extract_content(soup: BeautifulSoup) -> str:
        # Primary: <div class="newsbody"> holds the article body.
        # NB: must use exact class match — .newsbody-author also contains
        # the substring "newsbody" and would be matched by a regex.
        body = soup.find(class_="newsbody")
        if body:
            text = body.get_text("\n", strip=True)
            if text:
                return text
        # Fallback: <article> tag
        article = soup.find("article")
        if article:
            text = article.get_text("\n", strip=True)
            if text:
                return text
        return ""

    @staticmethod
    def _extract_publisher(soup: BeautifulSoup) -> str | None:
        author_box = soup.find(class_="newsbody-author")
        if not author_box:
            return None
        text = author_box.get_text(" ", strip=True)
        m = _SOURCE_RE.search(text)
        return m.group(1) if m else None

    @staticmethod
    def _extract_author(soup: BeautifulSoup) -> str | None:
        author_box = soup.find(class_="newsbody-author")
        if not author_box:
            return None
        text = author_box.get_text(" ", strip=True)
        m = _AUTHOR_RE.search(text)
        return m.group(1) if m else None

    # ── canonicalisation ──────────────────────────────────────────────────

    def parse(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Normalise a single article.

        ``raw`` may be either:
          - a URL-only dict from ``fetch()`` (deep-crawl on parse), or
          - a fully populated dict from a test fixture.

        Defensive: skip URLs that contain ``/download/`` even if a test
        accidentally feeds one in (the spec requires the fetcher to
        *never* surface a /download/ row).
        """
        url = raw.get("url")
        if not url:
            return None
        if "/download/" in url:
            return None

        # Already populated (test fixture) — just normalise.
        if raw.get("content") or raw.get("title"):
            return {
                "title": raw.get("title") or "",
                "url": url,
                "source": self.source_name,
                "published_at": raw.get("published_at"),
                "content": raw.get("content") or "",
                "publisher": raw.get("publisher"),
            }

        # Empty fixture from fetch() — deep-crawl.
        detail = self._fetch_article(url)
        if detail is None:
            return None
        return detail
