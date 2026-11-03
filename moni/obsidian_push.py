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
#
# v1.0.66 审计 B4: **xbot_feed 不在这里**。它是**混合源** —— 一半
# wechat topic (微信/小程序), 一半 xcircle topic (推特圈)。整个塞进
# WECHAT_SOURCES 会把 xcircle 的推文也当成微信生态推进 Obsidian 清单,
# 清单语义被污染。改成按文章**实际挂的 tag** 判断 (见 _is_wechat_ecosystem)。
WECHAT_SOURCES = {"bestblogs", "wechat_rss_a", "wechat_rss_b", "bestblog-podcast", "bestblog-x", "bestblog-yt"}

# 微信生态的 tag 名单 (v1.0.66 审计 B4): 挂了这些 tag 的文章属于微信
# 生态 —— xbot_feed 的 wechat 半边正是这样来的。
WECHAT_TAGS = {"wechat", "miniprogram"}


def _is_wechat_ecosystem(article: dict) -> bool:
    """True if the article belongs to the WeChat ecosystem.

    判定: 源在 ``WECHAT_SOURCES`` 里, **或者**文章挂了 ``wechat`` /
    ``miniprogram`` tag。

    v1.0.66 审计 B4: 之前是"源在集合里就算微信生态", 混合源 xbot_feed
    只能整个算或整个不算。现在按文章实际的 tag 判断 —— xbot_feed 的
    wechat 半边 (挂了 wechat tag) 照样进清单, xcircle 半边 (只挂
    general-tech / ai-fintech) 不进。
    """
    if (article.get("source") or "") in WECHAT_SOURCES:
        return True
    raw = article.get("tag_names") or ""
    tags = {t.strip() for t in raw.split(",") if t.strip()}
    return bool(tags & WECHAT_TAGS)


def _source_label(source: str) -> str:
    return {
        "bestblogs": "公众号",
        "wechat_rss_a": "微信A",
        "wechat_rss_b": "微信B",
        "bestblog-podcast": "播客",
        "bestblog-x": "X",
        "bestblog-yt": "YouTube",
        # v1.0.66 审计 S-3: 标签要如实反映来源 —— xbot_feed 是「X-bot 精选」,
        # 不是纯微信源 (它只有挂了 wechat/miniprogram tag 的那半边会进清单)。
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
        SELECT a.id, a.title, a.url, a.source, a.business_value_score, a.fetched_at,
               GROUP_CONCAT(t.tag_name) AS tag_names
        FROM articles a
        LEFT JOIN article_tags t ON t.article_id = a.id
        WHERE a.business_value_score IS NOT NULL
          AND a.business_value_score >= ?
          AND a.fetched_at >= ?
    """
    params: list = [min_score, since]

    if source_filter:
        if source_filter == "wechat":
            placeholders = ",".join("?" * len(WECHAT_SOURCES))
            tag_placeholders = ",".join("?" * len(WECHAT_TAGS))
            # 源在微信生态集合里 **或者** 文章挂了微信生态 tag
            # (v1.0.66 审计 B4: 混合源按文章实际 tag 判定, 不按源名)
            sql += f"""
              AND (
                    a.source IN ({placeholders})
                 OR EXISTS (
                        SELECT 1 FROM article_tags t2
                        WHERE t2.article_id = a.id
                          AND t2.tag_name IN ({tag_placeholders})
                    )
              )
            """
            params.extend(sorted(WECHAT_SOURCES))
            params.extend(sorted(WECHAT_TAGS))
        else:
            sql += " AND a.source = ?"
            params.append(source_filter)

    sql += """
        GROUP BY a.id, a.title, a.url, a.source, a.business_value_score, a.fetched_at
        ORDER BY a.business_value_score DESC LIMIT ?
    """
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
