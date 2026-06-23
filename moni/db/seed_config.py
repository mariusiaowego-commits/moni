"""moni.db.seed_config — v1.0.2 seed for sources / source_tags / source_keyword_filters.

业务范围 (PRD §0)：银行 × 科技 × 支付。

5 个 tag_definitions:
  - banking        银行 (央妈罚款/上市/财报/并购)
  - payment        支付 (PayPal/银联/二维码/跨境)
  - ai-fintech     AI × 金融 (智能投顾/风控模型/大模型落地)
  - regulation     监管 (政策/法规/合规)
  - general-tech   通用科技 (不属于以上 4 类)

5 个 sources (与 config/sources.yaml 同步, v1.0.5.8 删 36kr_newsflash, v1.0.5.9 退役 wechat_rss legacy):
  - 36kr_rss         36氪 RSS          → 关联 ai-fintech
  - mpaypass         移动支付网         → 关联 banking, payment
  - qbitai           量子位            → 关联 ai-fintech
  - wechat_rss_a     WeChatRSS 账号 A   → 关联 general-tech
  - wechat_rss_b     WeChatRSS 账号 B   → 关联 general-tech

keyword filters (黑名单为主，过滤业务无关内容):
  - 36kr_rss       blacklist 入境游/文娱/明星/游戏/影视/电商/消费/美食
  - qbitai         blacklist 机器人/自动驾驶/波士顿动力/Tesla
  - mpaypass / wechat_rss_a/b     无 (业务天然相关或等公众号业务明确后再配)

可重复跑 (INSERT OR REPLACE / UPSERT)。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .connection import get_db


def _env_token(account: str) -> str:
    """Read WeChatRSS token from env. Returns empty string if not set.

    v1.0.7.6.2: previously returned the placeholder ``__WECHAT_RSS_X_TOKEN__``
    when the env var was unset.  That placeholder URL was then persisted
    into ``sources.url`` and committed to prod DB — causing the wechat_rss
    fetcher to hit ``?token=__WECHAT_RSS_A_TOKEN__`` (which waytomaster
    treats as 404) and the user saw "0 articles" with no error.

    Two security issues with the old behaviour:
    1. The placeholder URL pollutes the DB and any subsequent
       debug-dump.  Worse, it encourages the "fix" of editing the URL
       to contain a real token — which then leaks into git/PR diffs.
    2. v1.0.5.6 *did* hardcode real tokens in ``url`` (see commit
       5b86616); v1.0.5.7 introduced env-var injection via this func.
       Old config and DB rows may still contain the real token; the
       new contract "empty string → don't seed" prevents the same
       leak path from recurring.

    Now: empty string in ``sources.url``.  The fetcher checks for
    this and returns ``[]`` with a loud error log instead of hitting
    waytomaster with a garbage URL.  User must set the env var (or
    re-run ``moni db seed-config``) for the source to work.
    """
    import os
    key = f"WECHAT_RSS_{account}_TOKEN"
    val = os.environ.get(key, "")
    if not val and account == "A":
        val = os.environ.get("WECHAT_RSS_TOKEN", "")
    return val  # empty string is fine; fetcher refuses placeholder URLs


# ── Tag 定义 (5 个) ─────────────────────────────────────────────────────────
# v1.0.5.14: each tag now carries a `display_name` — a human-friendly
# Chinese / unicode string the UI shows.  The `name` column is the
# immutable system primary key + FK reference (source_tags /
# article_tags).  Mirrors the sources.name / sources.display_name
# pattern.  The display_name values are what the user sees in
# article-list tag chips, configuration tabs, and share cards.
_DEFAULT_TAGS = [
    ("banking",      "银行",        "银行 (央行罚款/上市/财报/并购)",      "#2563EB"),  # blue
    ("payment",      "支付",        "支付 (PayPal/银联/二维码/跨境)",     "#16A34A"),  # green
    ("ai-fintech",   "AI×金融",     "AI × 金融 (智能投顾/风控模型/大模型落地)", "#DC2626"),  # red
    ("regulation",   "监管",        "监管 (政策/法规/合规)",              "#EA580C"),  # orange
    ("general-tech", "通用科技",    "通用科技 (不属于以上 4 类)",          "#6B7280"),  # gray
    ("wechat",       "微信生态",    "微信公众号/小程序/企业微信",         "#7C3AED"),  # purple
]


# ── Source 定义 (5 个) ──────────────────────────────────────────────────────
_DEFAULT_SOURCES = [
    {
        "id": "src-36kr-rss",
        "name": "36kr_rss",
        "display_name": "36氪 RSS",
        "source_type": "html",          # 实际是 RSS (server returns RSS XML)
        "url": "https://36kr.com/feed",
        "fetch_interval_min": 5,
        "notes": "36氪 科技/创投资讯。范围广，需关键词过滤业务无关内容。",
    },
    {
        "id": "src-mpaypass",
        "name": "mpaypass",
        "display_name": "移动支付网",
        "source_type": "html",
        "url": "https://www.mpaypass.com.cn/",
        "fetch_interval_min": 5,
        "notes": "移动支付网。业务天然相关（银行/支付）。v1.0.2 需深抓详情页正文。",
    },
    {
        "id": "src-qbitai",
        "name": "qbitai",
        "display_name": "量子位",
        "source_type": "rss",          # v1.0.3 改用 RSS feed (Cloudflare 403 on HTML)
        "url": "https://www.qbitai.com/feed",
        "fetch_interval_min": 5,
        "notes": "量子位 AI/前沿科技。v1.0.3 改用 RSS feed 抓取。",
    },
    {
        # v1.0.5.6: WeChatRSS 拆 2 个 source (waytomaster 免费账户上限 2 公众号)
        "id": "src-wechat-rss-a",
        "name": "wechat_rss_a",
        "display_name": "WeChatRSS 账号 A",
        "source_type": "wechat_aggregator",
        "url": f"https://wechatrss.waytomaster.com/api/rss/all?token={_env_token('A')}",
        "fetch_interval_min": 5,
        "notes": "WeChatRSS 账号 A，token 1（v1.0.5.6 启用）。免费账户上限 2 公众号。",
    },
    {
        "id": "src-wechat-rss-b",
        "name": "wechat_rss_b",
        "display_name": "WeChatRSS 账号 B",
        "source_type": "wechat_aggregator",
        "url": f"https://wechatrss.waytomaster.com/api/rss/all?token={_env_token('B')}",
        "fetch_interval_min": 5,
        "notes": "WeChatRSS 账号 B，token 2（v1.0.5.6 启用）。免费账户上限 2 公众号。",
    },
    {
        "id": "src-huxiu",
        "name": "huxiu",
        "display_name": "虎嗅",
        "source_type": "rss",
        "url": "https://rss.huxiu.com/",
        "fetch_interval_min": 5,
        "notes": "虎嗅 RSS。科技商业深度分析，含全文。",
    },
    {
        "id": "src-bestblogs",
        "name": "bestblogs",
        "display_name": "BestBlogs 公众号聚合",
        "source_type": "rss",
        "url": "https://raw.githubusercontent.com/ginobefun/BestBlogs/main/opml/bestblogs_wechat2rss_opml_all.opml,https://raw.githubusercontent.com/ginobefun/BestBlogs/main/BestBlogs_RSS_ALL.opml",
        "fetch_interval_min": 5,
        "notes": "BestBlogs 公众号 RSS 聚合 (375 feeds) + ALL 技术博客 (72 feeds, 合并去重)。OPML → 逐 feed 拉取，三层限制。",
    },
    {
        "id": "src-bestblog-podcast",
        "name": "bestblog-podcast",
        "display_name": "BestBlogs 播客",
        "source_type": "rss",
        "url": "https://raw.githubusercontent.com/ginobefun/BestBlogs/main/opml/bestblogs_podcast_opml_all.opml",
        "fetch_interval_min": 5,
        "notes": "BestBlogs 小宇宙播客聚合 (60 feeds)。OPML → 逐 feed 拉取。",
    },
    {
        "id": "src-bestblog-yt",
        "name": "bestblog-yt",
        "display_name": "BestBlogs YouTube",
        "source_type": "rss",
        "url": "https://raw.githubusercontent.com/ginobefun/BestBlogs/main/opml/bestblogs_youtube_opml_all.opml",
        "fetch_interval_min": 5,
        "notes": "BestBlogs YouTube 频道聚合 (20 feeds)。OPML → 逐 feed 拉取。",
    },
    {
        "id": "src-bestblog-x",
        "name": "bestblog-x",
        "display_name": "BestBlogs X (Twitter)",
        "source_type": "rss",
        "url": "/Users/mt16/dev/moni/config/bestblogs_x.opml",
        "fetch_interval_min": 5,
        "notes": "BestBlogs X (Twitter) 聚合 (160 feeds)。本地 OPML → 逐 feed 拉取。",
    },
]


# ── Source ↔ Tag 关联 ──────────────────────────────────────────────────────
# v1.0.5.8: list of source names that USED to be seeded but no longer
# are.  Running seed() will DELETE these rows from the sources table
# (and CASCADE-deletes the related source_tags and
# source_keyword_filters rows).  Articles retain their source column
# for historical reference.
_RETIRED_SOURCES: list[str] = [
    "36kr_newsflash",  # v1.0.5.8 — React rendering + captcha, not worth fixing
    "wechat_rss",      # v1.0.5.9 — superseded by wechat_rss_a/b which embed
                       #               their token in the source URL.  This
                       #               legacy source needed a WECHAT_RSS_TOKEN
                       #               env var that no one in the project
                       #               bothers to set any more.
]

_DEFAULT_SOURCE_TAGS = [
    ("src-36kr-rss", "ai-fintech"),
    ("src-mpaypass", "banking"),
    ("src-mpaypass", "payment"),
    ("src-qbitai", "ai-fintech"),
    ("src-wechat-rss-a", "general-tech"),
    ("src-wechat-rss-b", "general-tech"),
    ("src-huxiu",       "ai-fintech"),
    ("src-huxiu",       "banking"),
    ("src-huxiu",       "general-tech"),
    ("src-huxiu",       "wechat"),
    ("src-bestblogs",   "wechat"),
    ("src-bestblog-podcast", "wechat"),
    ("src-bestblog-podcast", "ai-fintech"),
    ("src-bestblog-yt",     "wechat"),
    ("src-bestblog-yt",     "ai-fintech"),
    ("src-bestblog-x",      "wechat"),
    ("src-bestblog-x",      "ai-fintech"),
]


# ── Keyword Filters (黑名单为主) ──────────────────────────────────────────
_DEFAULT_FILTERS = [
    {
        "source_id": "src-36kr-rss",
        "mode": "blacklist",
        "match_field": "title+content",
        "keywords": ["入境游", "文娱", "明星", "游戏", "影视", "电商", "消费", "美食"],
        "notes": "36kr 范围太广，过滤非业务相关内容。",
    },
    {
        "source_id": "src-qbitai",
        "mode": "blacklist",
        "match_field": "title+content",
        "keywords": ["机器人", "自动驾驶", "波士顿动力", "Tesla"],
        "notes": "量子位多 AI 机器人/自动驾驶，过滤非金融 AI。",
    },
    {
        "source_id": "src-bestblog-podcast",
        "mode": "blacklist",
        "match_field": "title+content",
        "keywords": ["入境游", "文娱", "明星", "游戏", "影视", "电商", "消费", "美食", "腾讯控股"],
        "notes": "BestBlogs 播客，过滤业务无关内容。",
    },
    {
        "source_id": "src-bestblog-yt",
        "mode": "blacklist",
        "match_field": "title+content",
        "keywords": ["入境游", "文娱", "明星", "游戏", "影视", "电商", "消费", "美食", "腾讯控股"],
        "notes": "BestBlogs YouTube，过滤业务无关内容。",
    },
    {
        "source_id": "src-bestblog-x",
        "mode": "blacklist",
        "match_field": "title+content",
        "keywords": ["入境游", "文娱", "明星", "游戏", "影视", "电商", "消费", "美食", "腾讯控股"],
        "notes": "BestBlogs X (Twitter)，过滤业务无关内容。",
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def seed() -> None:
    """Insert or update config seed data. Idempotent.

    v1.0.5.8: also drops sources that were once seeded but have
    since been removed from this file.  The set of "retired" source
    names lives in ``_RETIRED_SOURCES`` below; running seed() after
    a release that retires a source will DELETE the old row, its
    source_tags entries, and its source_keyword_filters entries.
    Articles that reference a retired source keep their source
    column (weakly coupled — see migration 004) so history is
    preserved.
    """
    conn = get_db()
    now = _now()

    # ── v1.0.5.8: retire sources removed in this release ─────────────
    # The seed function only knows how to upsert; explicit DELETEs
    # are needed when a source leaves the catalog.  CASCADE on
    # source_tags / source_keyword_filters handles the related rows.
    for retired in _RETIRED_SOURCES:
        conn.execute("DELETE FROM sources WHERE name = ?", (retired,))

    # ── tag_definitions (复用已有表) ────────────────────────────────────
    for name, display_name, desc, color in _DEFAULT_TAGS:
        conn.execute(
            """
            INSERT INTO tag_definitions (name, display_name, description, color, created_at, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(name) DO UPDATE SET
                display_name = excluded.display_name,
                description  = excluded.description,
                color        = excluded.color,
                is_active    = 1
            """,
            (name, display_name, desc, color, now),
        )

    # ── sources ────────────────────────────────────────────────────────
    for src in _DEFAULT_SOURCES:
        conn.execute(
            """
            INSERT INTO sources
                (id, name, display_name, source_type, url,
                 fetch_interval_min, is_active, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name               = excluded.name,
                display_name       = excluded.display_name,
                source_type        = excluded.source_type,
                url                = excluded.url,
                fetch_interval_min = excluded.fetch_interval_min,
                notes              = excluded.notes,
                updated_at         = excluded.updated_at
            """,
            (src["id"], src["name"], src["display_name"], src["source_type"],
             src["url"], src["fetch_interval_min"], src["notes"], now, now),
        )

    # ── source_tags ────────────────────────────────────────────────────
    for source_id, tag_name in _DEFAULT_SOURCE_TAGS:
        conn.execute(
            """
            INSERT INTO source_tags (source_id, tag_name, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(source_id, tag_name) DO NOTHING
            """,
            (source_id, tag_name, now),
        )

    # ── source_keyword_filters ─────────────────────────────────────────
    # 幂等策略：先 DELETE 该 source_id 全部行, 再 INSERT 当前 1 行。
    # 简单粗暴但绝对幂等 — 重复运行不会累积。
    for f in _DEFAULT_FILTERS:
        conn.execute(
            "DELETE FROM source_keyword_filters WHERE source_id = ? AND mode = ?",
            (f["source_id"], f["mode"]),
        )
    for f in _DEFAULT_FILTERS:
        keywords_json = json.dumps(f["keywords"], ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO source_keyword_filters
                (source_id, mode, match_field, keywords,
                 is_active, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (f["source_id"], f["mode"], f["match_field"], keywords_json,
             f["notes"], now, now),
        )

    conn.commit()

    n_tags = conn.execute("SELECT COUNT(*) FROM tag_definitions").fetchone()[0]
    n_sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    n_source_tags = conn.execute("SELECT COUNT(*) FROM source_tags").fetchone()[0]
    n_filters = conn.execute("SELECT COUNT(*) FROM source_keyword_filters").fetchone()[0]
    print(
        f"Config seed complete: {n_tags} tag(s), {n_sources} source(s), "
        f"{n_source_tags} source-tag link(s), {n_filters} keyword filter(s)"
    )


if __name__ == "__main__":
    seed()
