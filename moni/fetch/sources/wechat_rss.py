"""FetcherWechatRSS — WeChat articles via WeChatRSS aggregation endpoint.

聚合 RSS 入口: https://wechatrss.waytomaster.com/api/rss/all
需要 ?token=... 查询参数 (WECHAT_RSS_TOKEN 环境变量提供).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from ..base import BaseFetcher, register_fetcher

logger = logging.getLogger(__name__)

FEED_URL = "https://wechatrss.waytomaster.com/api/rss/all"


def _load_token_from_envfile() -> str:
    """Fallback: 读 .env 文件 (找 WECHAT_RSS_TOKEN=...).

    v1.0.4: moni 单用户本地项目, web 服务靠 `scripts/web.sh` source .env 注入,
    但 `python3 -m moni fetch` 直接跑没 source .env. 这里 fallback 让 fetcher
    自身能 load token, 生产用户不必每次手动 export.
    """
    import os
    from pathlib import Path
    env_path = Path(__file__).parent.parent.parent.parent / ".env"
    if not env_path.exists():
        return ""
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("WECHAT_RSS_TOKEN="):
                value = line.split("=", 1)[1].strip()
                # 去掉可能的引号
                if value.startswith(("'", '"')) and value.endswith(("'", '"')):
                    value = value[1:-1]
                return value
    except Exception as exc:
        logger.warning("[wechat_rss] Failed to read .env: %s", exc)
    return ""


@register_fetcher
class FetcherWechatRSS(BaseFetcher):
    source_name: str = "wechat_rss"

    def fetch(self) -> list[dict[str, Any]]:
        token = os.environ.get("WECHAT_RSS_TOKEN", "") or _load_token_from_envfile()
        if not token:
            logger.error("[wechat_rss] WECHAT_RSS_TOKEN env var not set — cannot fetch")
            return []

        url = f"{FEED_URL}?token={token}"
        try:
            resp = httpx.get(url, timeout=15.0)
            if resp.status_code == 404:
                logger.warning("[wechat_rss] Feed not found (404) — check token")
                return []
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("[wechat_rss] HTTP %s fetching %s", exc.response.status_code, exc.request.url)
            return []
        except httpx.TimeoutException:
            logger.error("[wechat_rss] Timeout fetching %s", url)
            return []
        except Exception as exc:
            logger.error("[wechat_rss] Unexpected error fetching %s: %s", url, exc)
            return []

        try:
            root = ET.fromstring(resp.text.encode("utf-8"))
        except Exception as exc:
            logger.error("[wechat_rss] Failed to parse RSS XML: %s", exc)
            return []

        # RSS 2.0: <channel> → <item>
        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else []

        articles: list[dict[str, Any]] = []
        for item in items:
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                description = (item.findtext("description") or "").strip()
                pub_text = item.findtext("pubDate")

                # Extract author from <dc:creator> or <author>
                author = (
                    item.findtext("dc:creator")
                    or item.findtext("author")
                    or ""
                ).strip()

                published_at: str | None = None
                if pub_text:
                    try:
                        dt = datetime.strptime(pub_text, "%a, %d %b %Y %H:%M:%S %z")
                        published_at = dt.isoformat()
                    except ValueError:
                        try:
                            dt = datetime.fromisoformat(pub_text.replace("Z", "+00:00"))
                            published_at = dt.isoformat()
                        except ValueError:
                            logger.warning("[wechat_rss] Unparseable pubDate %r — skipping", pub_text)

                articles.append({
                    "title": title,
                    "url": link,
                    "source": self.source_name,
                    "published_at": published_at,
                    "content": description,
                    "publisher": author,
                })
            except Exception as exc:
                logger.warning("[wechat_rss] Error parsing RSS item: %s — skipping", exc)

        return articles

    def parse(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        if not raw.get("url"):
            return None
        return {
            "title": raw.get("title") or "",
            "url": raw.get("url"),
            "source": self.source_name,
            "published_at": raw.get("published_at"),
            "content": raw.get("content") or "",
            "publisher": raw.get("publisher"),
        }
