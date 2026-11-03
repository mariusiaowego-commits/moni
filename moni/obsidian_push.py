"""moni → Obsidian 待剪藏推送 (v1.0.22, 微信方向 A1b).

把 moni 库中高业务价值文章推送成 Obsidian 「moni-待剪藏」清单。
用户打开 Obsidian 笔记 → 点链接 → 浏览器打开原文 → Web Clipper 剪藏。

用法:
    /usr/local/bin/python3 -m moni.obsidian_push [--min-score 70] [--days 7] [--source wechat]

设计原则 (2026-08-10 dad 决策):
- moni 不自动抓微信全文 (反爬/验证码墙) — 只推送标题 + 链接
- 微信正文由用户手动 Web Clipper 剪藏 (最安全稳定)
- 本模块只读 articles 表 + 写 Obsidian vault, 不碰微信

依赖:
    Obsidian CLI: /Applications/Obsidian.app/Contents/MacOS/obsidian
    Vault: tqob (OBSIDIAN_VAULT_PATH 或默认路径)
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

OBSIDIAN_CLI = "/Applications/Obsidian.app/Contents/MacOS/obsidian"
VAULT = os.environ.get("OBSIDIAN_VAULT_PATH", "/Users/mt16/Library/Mobile Documents/iCloud~md~obsidian/Documents/tqob")
NOTE_NAME = "moni-待剪藏"

# 微信生态源 (bestblogs 是 375 公众号聚合)
# v1.0.66: xbot_feed 的 "wechat" topic 那半边也是微信生态, 一并纳入。
WECHAT_SOURCES = {"bestblogs", "wechat_rss_a", "wechat_rss_b", "bestblog-podcast", "bestblog-x", "bestblog-yt", "xbot_feed"}


def _source_label(source: str) -> str:
    return {
        "bestblogs": "公众号",
        "wechat_rss_a": "微信A",
        "wechat_rss_b": "微信B",
        "bestblog-podcast": "播客",
        "bestblog-x": "X",
        "bestblog-yt": "YouTube",
        "xbot_feed": "X-bot 精选",
    }.get(source, source)


def fetch_high_value_articles(
    *,
    min_score: int = 70,
    days: int = 7,
    source_filter: str | None = None,
    limit: int = 30,
) -> list[dict]:
    """从 articles 表取高业务价值文章 (最近 N 天)。"""
    from moni.db.connection import get_db

    conn = get_db()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    sql = """
        SELECT id, title, url, source, business_value_score, fetched_at
        FROM articles
        WHERE business_value_score IS NOT NULL
          AND business_value_score >= ?
          AND fetched_at >= ?
    """
    params: list = [min_score, since]

    if source_filter:
        if source_filter == "wechat":
            placeholders = ",".join("?" * len(WECHAT_SOURCES))
            sql += f" AND source IN ({placeholders})"
            params.extend(sorted(WECHAT_SOURCES))
        else:
            sql += " AND source = ?"
            params.append(source_filter)

    sql += " ORDER BY business_value_score DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def push_to_obsidian(articles: list[dict]) -> int:
    """把文章清单 append 到 Obsidian 笔记, 返回写入条数。"""
    if not articles:
        logger.info("无高价值文章可推送")
        return 0

    # 生成条目 (Obsidian markdown 链接 + 来源 + 评分)
    lines = []
    for a in articles:
        title = (a.get("title") or "").strip().replace("[", "(").replace("]", ")")
        url = a.get("url") or ""
        score = a.get("business_value_score") or 0
        label = _source_label(a.get("source") or "")
        lines.append(f"- [{title}]({url}) — {label} — {score}分")

    # 一条 append 全量内容 (Obsidian CLI 支持多行 content)
    content = "\n".join(lines)
    cmd = [
        OBSIDIAN_CLI, "append",
        f"file={NOTE_NAME}",
        f"content={content}",
        f"vault=tqob",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        logger.error("Obsidian CLI 不存在: %s", OBSIDIAN_CLI)
        return 0
    except subprocess.TimeoutExpired:
        logger.error("Obsidian CLI 超时")
        return 0

    if result.returncode != 0:
        logger.error("Obsidian append 失败: %s", result.stderr.strip()[:200])
        return 0

    logger.info("已推送 %d 条到 %s", len(lines), NOTE_NAME)
    return len(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="moni → Obsidian 待剪藏推送")
    parser.add_argument("--min-score", type=int, default=70, help="最低业务价值评分")
    parser.add_argument("--days", type=int, default=7, help="最近 N 天")
    parser.add_argument("--source", default=None, help="来源过滤 (wechat / 具体源名)")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    articles = fetch_high_value_articles(
        min_score=args.min_score,
        days=args.days,
        source_filter=args.source,
        limit=args.limit,
    )
    pushed = push_to_obsidian(articles)
    print(f"pushed={pushed} articles={len(articles)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
