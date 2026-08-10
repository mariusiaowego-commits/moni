"""moni → Obsidian 微信待剪藏清单生成 (v1.0.22, 微信方向 A1b).

微信反爬验证码墙 → moni 不自动抓正文。改用 Web Clipper 手动剪藏:
moni 用标题关键词规则识别"值得剪藏的微信文章" → 推送 Obsidian 清单
→ 用户点链接 → 浏览器打开原文 → Obsidian Web Clipper 剪藏。

用法:
    /usr/local/bin/python3 -m moni.wechat_clip --days 7 --push
    /usr/local/bin/python3 -m moni.wechat_clip --days 7 --dry-run   # 只看不推

设计原则 (2026-08-10 dad 决策):
- 最安全稳定 = 用户手动剪藏, moni 只负责"找值得剪的"
- 规则引擎 (标题关键词) 而非 LLM: 零成本、确定性、可解释
- 不碰微信正文 → 不触发反爬/验证码

依赖:
    Obsidian CLI: /Applications/Obsidian.app/Contents/MacOS/obsidian
    Vault: tqob
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
VAULT_ROOT = Path(os.environ.get(
    "OBSIDIAN_VAULT_PATH",
    "/Users/mt16/Library/Mobile Documents/iCloud~md~obsidian/Documents/tqob",
))
NOTE_NAME = "moni-待剪藏"

# 微信生态源 (bestblogs = 375 公众号 OPML 聚合)
WECHAT_SOURCES = ("bestblogs", "wechat_rss_a", "wechat_rss_b")

# 标题命中关键词 → 值得剪藏 (银行/金融/AI 同业情报主战场)
KEEP_KEYWORDS = (
    "银行", "央行", "支付", "监管", "金融", "数字人民币", "数币",
    "智能体", "大模型", "AI", "人工智能", "数字化", "信创",
    "风控", "信贷", "理财", "保险", "券商", "跨境", "稳定币",
    "微信", "小程序", "支付机构", "银联", "清算",
)

# 标题命中 → 排除 (非同业情报)
DROP_KEYWORDS = (
    "游戏", "手游", "明星", "娱乐", "美食", "旅游", "影视",
    "追星", "穿搭", "美妆", "八卦", "综艺",
)


def _source_label(source: str) -> str:
    return {
        "bestblogs": "公众号",
        "wechat_rss_a": "微信A",
        "wechat_rss_b": "微信B",
    }.get(source, source)


def _title_matches(title: str) -> bool:
    """规则引擎: 标题是否值得剪藏 (命中 KEEP 且未命中 DROP)。"""
    if not title:
        return False
    if any(k in title for k in DROP_KEYWORDS):
        return False
    return any(k in title for k in KEEP_KEYWORDS)


def collect_candidates(*, days: int = 7, limit: int = 50) -> list[dict]:
    """取最近 N 天微信源文章, 规则筛出值得剪藏的。"""
    from moni.db.connection import get_db

    conn = get_db()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    placeholders = ",".join("?" * len(WECHAT_SOURCES))
    rows = conn.execute(
        f"""
        SELECT id, title, url, source, fetched_at
        FROM articles
        WHERE source IN ({placeholders})
          AND fetched_at >= ?
          AND (raw_content IS NULL OR length(raw_content) < 200)
        ORDER BY fetched_at DESC
        LIMIT ?
        """,
        (*WECHAT_SOURCES, since, limit),
    ).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        if _title_matches(d.get("title") or ""):
            out.append(d)
    return out


def push_to_obsidian(articles: list[dict]) -> int:
    """append 到 Obsidian 笔记, 返回条数。"""
    if not articles:
        logger.info("无匹配文章")
        return 0

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = f"\n## {stamp} 批次\n"
    lines = []
    for a in articles:
        title = (a.get("title") or "").strip().replace("[", "(").replace("]", ")")
        url = a.get("url") or ""
        label = _source_label(a.get("source") or "")
        lines.append(f"- [{title}]({url}) — {label}")

    cmd = [
        OBSIDIAN_CLI, "append",
        f"file={NOTE_NAME}",
        f"content={header + chr(10).join(lines)}",
        "vault=tqob",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.error("Obsidian CLI 调用失败: %s", exc)
        return 0
    if result.returncode != 0:
        logger.error("Obsidian append 失败: %s", result.stderr.strip()[:200])
        return 0
    logger.info("已推送 %d 条", len(lines))
    return len(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="微信待剪藏清单生成")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--push", action="store_true", help="推送 Obsidian (默认 dry-run)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    articles = collect_candidates(days=args.days, limit=args.limit)
    print(f"匹配 {len(articles)} 篇值得剪藏的微信文章 (最近 {args.days} 天)")
    for a in articles:
        print(f"  - {a['title'][:40]} [{_source_label(a['source'])}]")

    if args.push:
        pushed = push_to_obsidian(articles)
        print(f"pushed={pushed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
