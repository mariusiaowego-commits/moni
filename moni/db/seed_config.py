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

import copy
import json
import sqlite3
from datetime import datetime, timezone

from .connection import get_db

# v1.0.66 审计 B5: xbot_feed 的 fetch_config 默认值只有一个来源 ——
# moni/fetch/sources/xbot_feed.py 的 DEFAULT_FETCH_CONFIG。
# 循环 import 风险: moni/fetch/sources/xbot_feed.py 只在**函数内** import
# moni.db.*, 模块层不 import moni.db → 这里从 moni.fetch 拉常量不会形成环。
from moni.fetch.sources.xbot_feed import (
    DEFAULT_FETCH_CONFIG as XBOT_FEED_DEFAULT_FETCH_CONFIG,
)
# v1.0.76: bank_feed 同理 —— 默认值只有一个来源 (bank_feed.py)。
from moni.fetch.sources.bank_feed import (
    DEFAULT_FETCH_CONFIG as BANK_FEED_DEFAULT_FETCH_CONFIG,
)


def _env_token(account: str) -> str:
    """Read WeChatRSS token from env. Returns placeholder if not set.

    v1.0.7.6.2 (revised): we now KEEP the placeholder behaviour as a
    diagnostic signal — the placeholder URL in ``sources.url`` is
    what makes the bug visible (0 articles + loud error in the
    fetcher) instead of silently hitting waytomaster with a
    truncated token.

    The fetcher (``moni/fetch/sources/wechat_rss.py``) refuses
    placeholder URLs at fetch time, so the user still gets a
    clear "set WECHAT_RSS_A_TOKEN in .env and re-run seed-config"
    message instead of an opaque 0-article result.

    Security note: v1.0.5.6 (commit 5b86616) hardcoded real tokens
    in this file; v1.0.5.7 (commit 7b5f52c) moved them to env-var
    injection.  Git history still contains the old tokens — user
    should rotate them at waytomaster to invalidate.
    """
    import os
    key = f"WECHAT_RSS_{account}_TOKEN"
    val = os.environ.get(key, "")
    if not val and account == "A":
        val = os.environ.get("WECHAT_RSS_TOKEN", "")
    return val or f"__{key}__"


# ── Tag 定义 ────────────────────────────────────────────────────────────────
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
    # v1.0.11: miniprogram tag (TECH-SPEC §0.2 references its keywords for
    # xwatch's tag-keyword filter as "已有").  The spec author expected
    # this row to already exist in tag_definitions; adding it here closes
    # the gap so xwatch's source_tags binding (src-xwatch → miniprogram)
    # doesn't violate the FK.
    ("miniprogram",  "小程序",      "微信小程序 (订阅消息 / 服务通知 / 搜一搜)", "#10B981"),  # emerald
    # v1.0.63: APP / model-providers / workbuddy were created through the
    # /config UI in the live database, so seed never knew about them.  The
    # cn-indie source now binds to them, which meant a fresh database hit a
    # source_tags FK failure.  Values below mirror the live rows exactly.
    # NOTE: keywords are NOT seeded here (they are user-managed in the DB);
    # the ON CONFLICT clause below does not touch the keywords column.
    ("APP",             "APP",          "APP手机应用",                     "#8A71E8"),
    ("model-providers", "模型提供商",   "大模型提供商相关资讯",            "#EA580C"),
    ("workbuddy",       "workbuddy",    "workbuddy",                       "#16A34A"),
    # v1.0.76 (sprint-40): 银行同业 —— 中/农/工/建/邮储 (及交通银行) 的资讯与
    # 实际动作。与 banking (主题词: 监管/宏观/财报) 的区别是这里只放
    # **点名到具体银行** 的条目 —— banking 的 300+ 关键词放进来的多是
    # 不含任何银行主体的宏观稿 (实测近 90 天 1223 篇 banking 里只有
    # 36 篇 = 2.9% 提到具体银行名)。
    ("peer-banks",      "同业",        "中农工建邮储等银行的资讯与动作 (非公告)", "#0F766E"),
]  # noqa: E501


# ── Tag 关键词 (只在首次 INSERT 时写入, 绝不覆盖用户已配的) ────────────────
# 语义: tag_filter 的 OR 闸门。命中任意一个就进, 所以这里只放**银行主体词**
# (全称 + 新闻里常用的两字简称), 不放动作词 —— 动作词 (发布/签约/落地) 在
# 任何宏观稿里都会出现, 放进来等于把闸门拆了。真正的"哪家银行做了什么"由
# bot 侧的目标清单负责 (每个 target 钉死一家行), 闸门只做主体词兜底。
#
# 长词 (>=3 字, 如"工商银行") 走普通子串, 正文任意位置命中即可;
# 两字简称 (工行/农行/建行/中行/邮储) 按 keyword_match 的短 CJK 规则,
# 命中标题或正文重复 2 次以上 —— 银行新闻的标题几乎必然点名, 所以召回
# 不掉, 反而挡掉了正文里一句带过的宏观稿。
PEER_BANKS_KEYWORDS: list[str] = [
    "工商银行", "中国工商银行", "工行", "ICBC",
    "农业银行", "中国农业银行", "农行",
    "中国银行", "中行",
    "建设银行", "中国建设银行", "建行", "CCB",
    "邮储银行", "中国邮政储蓄银行", "邮储", "PSBC",
    "交通银行", "交行",
]

_DEFAULT_TAG_KEYWORDS: dict[str, list[str]] = {
    "peer-banks": PEER_BANKS_KEYWORDS,
}


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
    # v1.0.11: X 关键词监控 — DDG 发现 + syndication 抓全文（无 API key）。
    # v1.0.12: 双通道 — accounts (RSS/xgo via bestblogs_x.opml) + queries (DDG)。
    # feed_url 故意留空；health 走 check_xwatch() 双探针。
    # 用户可在 /config → xwatch → fetch_config 改 accounts / queries。
    {
        "id": "src-xwatch",
        "name": "xwatch",
        "display_name": "X 关键词监控",
        "source_type": "x",
        "url": "",
        "fetch_interval_min": 30,
        "notes": (
            "X 三通道（v1.0.12）：C Following CDP(Profile6) + A accounts RSS "
            "+ B DDG queries → tag 门控入库。改 max_posts/accounts 见 fetch_config。"
        ),
        "fetch_config": json.dumps({
            # Channel C — Following timeline (logged-in Chrome Profile 6 / 9222)
            "following_enabled": True,
            "following_max_posts": 100,
            "following_max_scrolls": 40,
            "following_scroll_pause": 1.8,
            "following_skip_syndication": True,
            # Channel A — 观察列表（OPML/xgo RSS；可改）
            "accounts": [
                "op7418",
                "imxiaohu",
                "vista8",
                "PMbackttfuture",
                "oran_ge",
                "lijigang_com",
                "HiTw93",
                "hidecloud",
                "geekbb",
                "idoubicc",
            ],
            "accounts_per_run": 5,
            "max_results_per_account": 5,
            "account_rss": {},
            "account_ddg_fallback": True,
            # Channel B — 关键词（每 run 轮转 1 条）
            "queries": [
                "微信小程序 触达",
                "微信小程序 服务通知",
                "微信小程序 提醒",
            ],
            "queries_per_run": 1,
            "max_results_per_query": 10,
            "request_delay": 8.0,
            "search_timeout": 20,
            "syndication_timeout": 20,
            "ddg_circuit_minutes": 60.0,
            "skip_existing_urls": True,
        }, ensure_ascii=False),
    },
    # v1.0.41 / sprint-21: follow-builders — 中央日更 curated X feed (feed-x.json)。
    # 试验性源: 默认 disabled (is_active=0), 用户在 /config 打开才抓。
    # bypass_keyword_filters=true: 双层短路全局/源级关键词门控 + tag 关键词 OR 门控,
    # 使精选英文推文 100% 入库, 由 _DEFAULT_SOURCE_TAGS 绑定 ai-fintech 合成打 tag。
    # 跨源软去重由 FetcherFollowBuilders 在插入前做全局 URL EXISTS, 不改 UNIQUE。
    {
        "id": "src-follow-builders",
        "name": "follow_builders",
        "display_name": "Follow Builders (精选 X)",
        "source_type": "json",
        "url": "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-x.json",
        "fetch_interval_min": 360,
        "is_active": 0,
        "notes": "follow-builders 中央日更 curated X 推文 (26 AI builders)。试验源默认关，/config 打开才抓。",
        "fetch_config": json.dumps({
            "bypass_keyword_filters": True,
            "timeout_sec": 60,
            "http_timeout": 15.0,
            "skip_existing_urls": True,
        }, ensure_ascii=False),
    },
    # v1.0.60 / sprint-27: Chinese independent blogs.
    # 306/695 feeds were reachable in the 2026-10-24 feasibility sweep.
    # The source rotates through a bounded window instead of sweeping all
    # 695 feeds every run.  fetch_config is user-tunable in /config.
    {
        "id": "src-chinese-independent-blogs",
        "name": "chinese_independent_blogs",
        "display_name": "中文独立博客",
        "source_type": "rss",
        "url": "https://raw.githubusercontent.com/geekplux/chinese-independent-blogs/master/feed.opml",
        "fetch_interval_min": 180,
        "notes": "中文独立博客列表 (MIT)。695 feeds，2026-10-24 实测 306 可抓；轮转窗口抓取。",
        "fetch_config": json.dumps({
            "max_feeds": 80,
            "max_articles_per_feed": 3,
            "max_articles_total": 240,
            "request_delay": 0.35,
            "http_timeout": 12.0,
            "min_feeds_before_failure_rate_check": 10,
            "max_failure_rate": 0.7,
            "timeout_sec": 1200,
        }, ensure_ascii=False),
    },
    # v1.0.66: xbot_feed — X-bot 精选桥接源。
    # 上游是私有仓库 mariusiaowego-commits/xbot-feed (X-bot 的导出器每天写
    # 一份 feed/YYYY-MM-DD.json)。moni 侧不持有任何 key: 本地 clone 用机器
    # 上已有的 git 凭据 (SSH agent / credential helper)。
    # bypass_keyword_filters=true (同 follow_builders): 精选内容整体入库,
    # 不受全局/源级黑白名单影响。tag 由 fetcher 按 topic 的**候选 tag**
    # 的当前关键词真实命中写入 (wechat → wechat+miniprogram,
    # xcircle → general-tech+ai-fintech), 不靠全局/源级黑白名单。
    # v1.0.66 审计 B5: 默认值统一引用 moni.fetch.sources.xbot_feed 的
    # DEFAULT_FETCH_CONFIG —— 不再在本文件里抄一遍 (三处抄本会漂移)。
    {
        "id": "src-xbot-feed",
        "name": "xbot_feed",
        "display_name": "X-bot 精选",
        "source_type": "json",
        "url": "https://github.com/mariusiaowego-commits/xbot-feed.git",
        "fetch_interval_min": 60,
        # v1.0.68 (sprint-30): 文案去掉字面量里的 ";" 和 "/*" ——
        # migration 039 的 SQL 字面量有同一条硬约束 (migrate.py 的兜底路径
        # 是 naive split(";"))，两处必须逐字相同 (测试用例 11 钉死)。
        "notes": "X-bot 精选 feed (私有仓库 xbot-feed 的 feed 目录下的 json 文件)。moni 只做导入，私有仓库访问权来自机器已有的 git 凭据。",
        "fetch_config": json.dumps(
            copy.deepcopy(XBOT_FEED_DEFAULT_FETCH_CONFIG),
            ensure_ascii=False,
        ),
    },
    # v1.0.76 / sprint-40: bank_feed — 银行同业情报 agent 管道。
    # 上游是一个**外部 agent** (scripts/bank_bot/run_bot.py): 抓五大行官网
    # 栏目 + 媒体 RSS + 已知公众号 URL, 落到 data/bank_feed/*.json。
    # 本源只读本地目录, 不联网、不持 key; agent 自己走 hermes --profile moni
    # (即 magpie 路由组), 与 moni 现有 LLM 链路同一条。
    # 用户想加/换信源时改 fetch_config.targets (web 端 fetch_config 编辑器),
    # **不需要改代码**。
    {
        "id": "src-bank-feed",
        "name": "bank_feed",
        "display_name": "银行同业",
        "source_type": "json",
        "url": "",
        "fetch_interval_min": 120,
        "notes": "中农工建邮储的资讯 (非公告)。agent 抓官网栏目+媒体 RSS+已知公众号, 落到 data/bank_feed 的 JSON, 本源只做导入。抓取时若当天 feed 缺失会就地调 agent 补跑。",
        "fetch_config": json.dumps(
            copy.deepcopy(BANK_FEED_DEFAULT_FETCH_CONFIG),
            ensure_ascii=False,
        ),
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
    # v1.0.11: xwatch 只绑定带关键词的 2 个 tag — wechat (微信小程序 / 服务通知) +
    # miniprogram (小程序 / 订阅消息)。DDG 搜索已经把 query 限定为微信小程序 / 服务通知
    # / 提醒这类关键词, tag 层再做一次 OR 命中门控保证内容相关性; xwatch 不入黑名单
    # (源目标域就是腾讯生态, 任何关键词黑名单都可能误伤).
    ("src-xwatch", "wechat"),
    ("src-xwatch", "miniprogram"),
    # v1.0.41 / sprint-21: follow_builders → ai-fintech tag (bypass 合成打 tag)
    ("src-follow-builders", "ai-fintech"),
    # v1.0.63: cn-indie reuses the project's existing business tags instead of
    # a dedicated no-keyword "independent-blog" tag.  A keyword-less tag made
    # the tag gate a pass-through, so every long-tail post (travel notes,
    # book reviews, ...) was ingested AND left without any article_tags row
    # (dedupe_insert only writes tags that were actually hit).  These ten
    # bindings mirror what the user picked in /config; the union of their
    # keywords is the ingest gate, and only hit tags are written per article.
    ("src-chinese-independent-blogs", "APP"),
    ("src-chinese-independent-blogs", "ai-fintech"),
    ("src-chinese-independent-blogs", "banking"),
    ("src-chinese-independent-blogs", "general-tech"),
    ("src-chinese-independent-blogs", "miniprogram"),
    ("src-chinese-independent-blogs", "model-providers"),
    ("src-chinese-independent-blogs", "payment"),
    ("src-chinese-independent-blogs", "regulation"),
    ("src-chinese-independent-blogs", "wechat"),
    ("src-chinese-independent-blogs", "workbuddy"),
    # v1.0.66: xbot_feed 绑 4 个已有业务 tag ——
    #   wechat + miniprogram  → 微信/小程序那一半 (topic "wechat")
    #   general-tech + ai-fintech → 推特圈那一半 (topic "xcircle"): 中文技术/
    #     投资圈的一般讨论, 其中"AI × 金融/投资"归 ai-fintech。
    # 这 4 个 tag 的关键词并集就是入库闸门; 具体每篇打哪些 tag 由
    # FetcherXbotFeed 按 topic 精确计算 (bypass 模式下 bypass 分支会把
    # 全部绑定 tag 都合成命中, 所以 fetcher 覆写了那批命中)。
    ("src-xbot-feed", "wechat"),
    ("src-xbot-feed", "miniprogram"),
    ("src-xbot-feed", "general-tech"),
    ("src-xbot-feed", "ai-fintech"),
    # v1.0.76: bank_feed 只绑 1 个 tag —— 这个 tag 的关键词并集就是入库闸门,
    # 命中哪家行就只给那篇打 peer-banks (dedupe_insert 只写命中的 tag)。
    ("src-bank-feed", "peer-banks"),
]


# ── Keyword Filters (黑名单为主) ──────────────────────────────────────────
# v1.0.11: xwatch 故意不入 _DEFAULT_FILTERS.  原因:
#   1) sources.fetch_config.queries 已经把搜索范围限定到「微信小程序 触达/服务通知/
#      提醒」这种场景, 大多数内容自然是腾讯相关;
#   2) 关键词黑名单 (例如「腾讯控股」/「波士顿动力」) 在 bestblog-* 上用来拦截无关品类,
#      但对 xwatch 是反向 — 腾讯词恰好是 xwatch 想关注的内容;
#   3) xwatch 触发频率低 (30 min / 默认 3 query), 即使个别 query 命中边界内容, 也由
#      tag_keyword_filter (wechat/miniprogram) 兜底过滤, 无需另加黑名单.
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
        # v1.0.76: keywords 只在**首次 INSERT** 时落库。ON CONFLICT 分支
        # 故意不写 keywords —— 那是用户在 /config 里维护的数据, seed 每次
        # 启动都会跑 (moni db init), 覆盖它等于把用户的编辑擦掉。
        keywords = _DEFAULT_TAG_KEYWORDS.get(name)
        conn.execute(
            """
            INSERT INTO tag_definitions
                (name, display_name, description, color, keywords, created_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(name) DO UPDATE SET
                display_name = excluded.display_name,
                description  = excluded.description,
                color        = excluded.color,
                is_active    = 1
            """,
            (
                name, display_name, desc, color,
                json.dumps(keywords, ensure_ascii=False) if keywords else None,
                now,
            ),
        )

    # ── sources ────────────────────────────────────────────────────────
    # v1.0.11: add `fetch_config` to the INSERT so xwatch (and any future
    # source we seed with fetch_config) gets persisted.  Falls back to
    # NULL when the source dict doesn't carry fetch_config — existing
    # sources (36kr_rss, mpaypass, ...) stay NULL so no behaviour
    # change for them.
    # v1.0.41: honor per-source `is_active` (follow_builders seeds 0 /
    # disabled).  Omitted elsewhere defaults to 1 as before.
    for src in _DEFAULT_SOURCES:
        seed_active = src.get("is_active", 1)
        conn.execute(
            """
            INSERT INTO sources
                (id, name, display_name, source_type, url,
                 fetch_interval_min, is_active, notes,
                 fetch_config, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name               = excluded.name,
                display_name       = excluded.display_name,
                source_type        = excluded.source_type,
                url                = excluded.url,
                fetch_interval_min = excluded.fetch_interval_min,
                notes              = excluded.notes,
                fetch_config       = COALESCE(
                    excluded.fetch_config, sources.fetch_config
                ),
                updated_at         = excluded.updated_at
            """,
            (src["id"], src["name"], src["display_name"], src["source_type"],
             src["url"], src["fetch_interval_min"], seed_active, src["notes"],
             src.get("fetch_config"), now, now),
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
