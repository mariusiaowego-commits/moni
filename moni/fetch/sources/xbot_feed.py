"""FetcherXbotFeed — X-bot 精选 feed 桥接源 (v1.0.66).

上游: 私有仓库 ``mariusiaowego-commits/xbot-feed`` (仓库名 ``xbot-feed``),
由 X-bot 的导出器每天写一份 JSON:

    feed/YYYY-MM-DD.json
    feed/latest.json          # 与当天那份内容相同

每份 JSON 的结构::

    {
      "date": "2026-11-03",
      "generated_at": "2026-11-03T08:31:42+08:00",
      "counts_by_topic": {"wechat": 46, "xcircle": 157},
      "items": [
        {
          "id": "2117414151186502091",
          "url": "https://x.com/oran_ge/status/2117414151186502091",
          "author_name": "Orange AI",
          "author_handle": "oran_ge",
          "author_followers": 188424,
          "text": "...",
          "created_at": "2026-11-03T00:30:47.000Z",
          "metrics": {"like": 0, "retweet": 0, "impression": 0,
                      "reply": 0, "quote": 0, "bookmark": 0},
          "lang": "zh",
          "topic": "xcircle",
          "captured_at": "2026-11-03T08:31:42+08:00",
          "source_file": "/workspace/x-digest/atoday3.txt",
          "sent_by_xbot": false
        }
      ]
    }

``topic`` 目前有两个取值: ``wechat`` (微信/小程序话题) 与 ``xcircle``
(推特圈固定账号)。两者都映射到已有的业务 tag。

取数方式 (取数方案 (d) 桥接)
-----------------------------
moni 不持有任何 key, 也不直接访问 X API。X-bot 那边已经用 Grok Bot 的
免费额度把内容抓好并落成了 JSON; 这个 fetcher 只做三件事:

1. 保证 moni 数据目录下有一份 xbot-feed 的本地 clone (没有就 ``git
   clone``, 有就 ``git pull --ff-only``)。**只用机器上已有的 git 凭据**
   —— moni 自己不存 key, 私有仓库的访问权来自机器的 SSH agent /
   git credential helper。
2. 读取 clone 里的 ``feed/*.json``, 跳过已经导入过的 (按 tweet id 去重),
   返回还没导入的 raw 列表。
3. ``parse()`` 把一条 item 映射成 moni 的规范 article dict, 并按
   ``topic`` 决定它该打哪些 tag。

设计取舍
--------
* **软去重**: 同一条推文可能已经被 ``bestblog-x`` / ``xwatch`` 存过
  (URL 变体: ``twitter.com`` vs ``x.com``、带 ``?s=20``)。照
  ``follow_builders`` 的做法在插入前做全局 EXISTS 检查, 命中就不入库。
  物理 ``UNIQUE(source, url)`` 约束不动。
* **id 去重 + 导入状态**: feed 里同一天可能有重复 id (``latest.json``
  与 ``YYYY-MM-DD.json`` 内容更是完全相同), 所以导入状态记在数据目录下
  的 ``.xbot_feed_state.json`` 里 (只记已导入的 tweet id), 重复跑是幂等的。
* **topic → 候选 tag → 关键词闸门 (v1.0.66 审计 B1 重做)**: 一条 item
  先按 topic 找到**候选** tag (``fetch_config.topic_tags``, 默认
  ``wechat → [wechat, miniprogram]`` / ``xcircle → [general-tech,
  ai-fintech]``), 再拿这些 tag **当前**的关键词 (tag_definitions.keywords,
  可在 web 端改) 走 ``moni/fetch/keyword_match.py`` 的 ``keyword_matches``
  匹配标题 + 正文。
  * **只挂命中的 tag** —— 命中哪个就挂哪个, 绝不按 topic 硬打。
  * **零命中不入库** —— 不产生零 tag 的文章 (AGENTS.md 铁律 3)。
  * **被关键词挡掉的条目不写进已导入 state** —— 用户改完关键词下一轮
    它们还能进来。
  * ``bypass_keyword_filters`` 只短路全局/源级黑白名单; 源级 tag 闸门
    (tag_filter 的 bypass 分支) 合成的那批 ``hit_field="bypass"`` 命中
    在 ``dedupe_insert()`` 里丢掉, 换成 fetcher 自己算出来的真实命中。
* **handle 黑名单**: 明显的刷量 / 营销 / 灰产号在这里挡掉。这不是
  moni 的关键词闸门 (那套走 ``keyword_match`` 引擎, 管的是内容), 而是
  **按作者** 的一道窄兜底, 配置在 ``sources.fetch_config`` 的
  ``blacklisted_handles``。
* **git 失败必须报错**: clone / pull 失败时记 ``error``, 绝不静默返回
  0 篇 —— 静默 0 篇跟"今天真的没内容"看起来一模一样, 但含义完全相反。
"""

from __future__ import annotations

import copy
import json
import logging
import re
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, NamedTuple, Sequence

from ..base import BaseFetcher, register_fetcher

logger = logging.getLogger(__name__)

#: 上游仓库默认地址 (私有仓库; moni 不存凭据, 用机器上已有的 git 凭据)
DEFAULT_REPO_URL = "https://github.com/mariusiaowego-commits/xbot-feed.git"

#: 本地 clone 在 moni 数据目录下的子目录名
CLONE_DIRNAME = "xbot-feed"

#: feed 目录在 clone 里的相对路径
FEED_SUBDIR = "feed"

#: 最新的那份 feed (与当天的 YYYY-MM-DD.json 内容相同)
LATEST_FILENAME = "latest.json"

#: 导入状态文件名 (放在 moni 数据目录下)
STATE_FILENAME = ".xbot_feed_state.json"

#: topic → **候选** tag (默认值; 可在 ``fetch_config.topic_tags`` 覆盖)。
#:
#: * ``wechat`` → ``wechat`` + ``miniprogram``: 这半边就是微信生态话题,
#:   小程序是核心议题 (2026-11-03 首日 46 条里绝大多数是小程序/微信支付)。
#: * ``xcircle`` → ``general-tech`` + ``ai-fintech``: 推特圈那半是中文
#:   技术/投资圈的一般讨论 (28 个固定账号, 见
#:   ``/workspace/x-digest/accounts.txt``)。``general-tech`` 承接"通用
#:   科技"语义, ``ai-fintech`` 承接其中"AI × 金融/投资"的硬核部分
#:   (oran_ge 的 AI×A股讨论、量化、投顾)。**不新建 tag** —— 新建一个
#:   关键词为空的 tag 会让 tag 闸门变成透传, 且每篇文章会带一个语义
#:   空泛的标签 (migration 037 的教训)。
#:
#: 注意: 这些是**候选**, 不是最终 tag。最终挂哪些 tag 由
#: ``_match_topic_tags()`` 按 tag 当前关键词的真实命中决定。
TOPIC_TAGS: dict[str, tuple[str, ...]] = {
    "wechat": ("wechat", "miniprogram"),
    "xcircle": ("general-tech", "ai-fintech"),
}

#: ``fetch_config.topic_tags`` 的默认值 (topic → 候选 tag 名单)。
#: 与 ``TOPIC_TAGS`` 同源 —— ``TOPIC_TAGS`` 是权威常量, 这里只是把它
#: 摊平成可 JSON 序列化的形式, 供 ``DEFAULT_FETCH_CONFIG`` 用。
DEFAULT_TOPIC_TAGS: dict[str, list[str]] = {
    topic: list(tags) for topic, tags in TOPIC_TAGS.items()
}

#: 内置 handle 黑名单 —— 明显的刷量 / 营销 / 灰产号。
#:
#: 判据 (2026-11-03 首日 feed 实测, 46 条 wechat + 157 条 xcircle):
#:   * ``jkghzone`` / ``sensenxnyz`` —— 淘宝代购/代付灰产 (接单代买、
#:     "rate: rmb x 9.7" 这种报价), 每天刷同一条文案;
#:   * ``Eason3110188520`` —— "各种 App 签到打卡" 接单号;
#:   * ``duizhanggege`` —— "小程序掘金副业, 实测一周到手 1700+" 营销号;
#:   * ``HunchoJ01`` —— 关键词误命中 (正文讲 NXT 乐队的 "mini
#:     program", 跟微信小程序毫无关系), 属于 xcircle 半边的噪音。
#:
#: 这是一道**按作者**的窄兜底, 不是内容闸门 —— 内容闸门统一走
#: ``moni/fetch/keyword_match.py``, 这里只按 handle 精确匹配。
DEFAULT_BLACKLISTED_HANDLES: tuple[str, ...] = (
    "jkghzone",          # 代购/代付灰产
    "sensenxnyz",        # 代购/代付灰产
    "Eason3110188520",   # 签到打卡接单
    "duizhanggege",      # 小程序副业营销号
    "HunchoJ01",         # 关键词误命中的噪音号
)


def normalize_handle(raw: object) -> str:
    """把一个 handle 归一成**唯一**的规范形式 (**全仓唯一实现**)。

    规则: ``strip()`` → ``lstrip("@")`` → ``lower()``。空串返回 ``""``。

    为什么必须只有一份实现 (warden 审计 P2-3):
    写入侧 (``moni/web/routes/author_blocks.py`` 的 POST / DELETE) 与
    匹配侧 (``FetcherXbotFeed`` 的黑名单 / 屏蔽名单匹配) 必须走**同一套**
    归一 —— 否则 dad 手输 ``@JiaMeXyz`` 写进名单, 抓取时却归一成别的形式,
    就会出现「名单里明明有, 作者却还在入库」的**静默失效**
    (这种失效极难被发现, 因为名单看起来是对的)。
    """
    if raw is None:
        return ""
    return str(raw).strip().lstrip("@").lower()


def normalize_handles(handles: object) -> set[str]:
    """把 handle 列表归一成匹配用的规范形式集合。

    接受 ``None`` / 单个 str / list / tuple / set / frozenset;
    空串与归一后为空的一律丢掉。逐个元素的归一走 :func:`normalize_handle`。
    """
    if handles is None:
        return set()
    if isinstance(handles, str):
        handles = [handles]
    if not isinstance(handles, (list, tuple, set, frozenset)):
        return set()
    out: set[str] = set()
    for h in handles:
        text = normalize_handle(h)
        if text:
            out.add(text)
    return out

#: ``sources.fetch_config`` 里 xbot_feed 的**唯一**默认值来源。
#:
#: v1.0.66 (审计 B5): 同一份默认值原本抄在三处 ——
#: ``moni/fetch/sources/xbot_feed.py`` 的 ``_DEFAULTS``、
#: ``moni/db/seed_config.py`` 的 source 定义、
#: ``moni/web/routes/config_sources.py`` 的 ``_FETCH_CONFIG_DEFAULTS``。
#: 改一处忘另两处, 就会出现「web 面板显示的默认值」≠「seed 写进库的」
#: ≠「fetcher 实际生效的」。现在这三个地方都引用这里。
#:
#: 注意: ``moni/db/seed_config.py`` 从这里 import 是安全的 ——
#: ``moni/fetch/sources/xbot_feed.py`` 只在**函数内** import ``moni.db.*``,
#: 模块层不 import ``moni.db`` → 无循环 import。
DEFAULT_FETCH_CONFIG: dict[str, Any] = {
    # 上游仓库地址 (私有仓库; moni 不存 key, 用机器已有的 git 凭据)
    "repo_url": DEFAULT_REPO_URL,
    # clone 放在 moni 数据目录下; 留空则用默认子目录名
    "clone_dir": "",
    # fetch_config 里的 bypass_keyword_filters (跟 follow_builders 同款):
    # 精选内容整体入库, 不受全局/源级黑白名单影响。**tag 关键词闸门不受
    # 影响** —— 那条由 fetcher 自己按候选 tag 执行 (见 _match_topic_tags)。
    "bypass_keyword_filters": True,
    # handle 黑名单 (覆盖内置黑名单)
    "blacklisted_handles": list(DEFAULT_BLACKLISTED_HANDLES),
    # topic → 候选 tag (B1)。用户可在 web 端改; 没配的 topic 退到
    # TOPIC_TAGS 默认值, 再没有就找同名 active tag, 都没有则跳过。
    "topic_tags": dict(DEFAULT_TOPIC_TAGS),
    # 只导入最近 N 天的 feed (0 = 不限制)。历史文件留着不删, 但没必要
    # 全部导一遍 —— 默认只导最近 30 天。
    "max_days": 30,
    # git 命令超时 (秒)
    "git_timeout": 120,
    # 是否跳过跨源已存在的 URL (软去重)
    "skip_existing_urls": True,
}

#: fetcher 内部的别名 —— 语义上就是 ``DEFAULT_FETCH_CONFIG``。
_DEFAULTS: dict[str, Any] = DEFAULT_FETCH_CONFIG

# ── sprint-35 (v1.0.71): per-topic 门槛 + scope 副本 ─────────────────────
#
# **方案 A**: 这两个键**不进** ``DEFAULT_FETCH_CONFIG``。原因: 那个字典是
# migration 039 的 SQL 字面量的唯一对照物
# (``tests/test_migration_039_xbot_feed.py::test_039_fetch_config_matches_default``
# 逐字比对), 往里加键 = 把那条护栏自己放宽。
#
# 所以它们是**独立常量**, 在**读取时**合并:
#   * fetcher: ``_load_config()`` 额外放行这两个键;
#   * web:     ``GET /{id}/fetch_config`` 与 ``GET /{id}/scope`` 组装响应时合并。
#
#: 门槛默认值 —— **全 0 = 不设限** (Q1=A)。不设限时门槛闸门完全不生效,
#: 入库行为与改动前**逐条相同**。
#:
#: 语义 (每个 topic 独立):
#:   * ``min_followers`` > 0 → ``author_followers`` 低于它就丢弃;
#:   * ``min_likes`` > 0 → ``metrics.like`` 低于它就丢弃;
#:   * 字段**缺失** → **放行 + 计数** (Q2=A), 不丢条目;
#:   * 边界相等 → 放行 (``>=`` 语义)。
DEFAULT_THRESHOLDS: dict[str, dict[str, int]] = {
    "wechat": {"min_followers": 0, "min_likes": 0},
    "xcircle": {"min_followers": 0, "min_likes": 0},
}

#: 门槛闸门认识的 topic。**只有这两个** —— 上游新增的 topic 一律放行
#: (不因门槛丢条目), 并记 warning。
THRESHOLD_TOPICS: tuple[str, ...] = ("wechat", "xcircle")

#: moni 侧存的 **scope 副本** (可编辑, 仅用于记录与展示)。
#:
#: 它**不参与入库闸门** —— moni 改不了 X-bot 的搜索, 而上游已经筛过一遍;
#: 按一份手抄名单再过滤 = 静默丢条目, 收益为零。
#: 编辑它只改这份副本, UI 会明确标注"不会改变 X-bot 的搜索范围"。
#:
#: 账号名单来自 brief 的快照 (2026-11-11); 上游目前**没有**导出 scope 键
#: (8 个 feed 文件顶层都只有 date/generated_at/counts_by_topic/items),
#: 所以这份是"moni 侧兜底"来源, UI 标注快照日期以暴露漂移。
DEFAULT_SCOPE: dict[str, Any] = {
    "xcircle_accounts": [
        "dotey", "Gorden_Sun", "lxfater", "op7418", "oran_ge", "vista8",
        "imxiaohu", "FinanceYF5", "ruanyf", "onevcat", "fenng", "yihong0618",
        "waylybaye", "cloudwu", "wshuyi", "shao__meng", "Psyche_Bastion",
        "hi_xiaopeng", "javacruiser", "manateelazycat", "nekocode", "soasme",
        "leveaal", "excalith", "turingou", "orangexiao", "liang2min",
        "huangyunmin",
    ],
    "wechat_regex": "微信|WeChat|Weixin|小程序|公众号|视频号|mini program",
    # 上游 2026-11-12 起才把每日 query 写进 out/wechat-queries-*.txt
    # (在 X-bot 那台机器上), moni 看不到 → 空列表 + UI 标注"上游未导出"。
    "wechat_queries": [],
}

#: 这两个键在 ``sources.fetch_config`` 里是**用户配的**, 读取时按它们
#: 的键名放行 (见 ``_load_config``) —— ``_DEFAULTS`` 的白名单只认
#: ``DEFAULT_FETCH_CONFIG`` 的键, 门槛与 scope 都不在里面。
EXTRA_CONFIG_KEYS: tuple[str, ...] = (
    "thresholds",
    "scope",
    # sprint-54 / id 26111703: the **new** tag-gate bypass switch.
    # Without this, ``_load_config()`` silently drops the key
    # (it only keeps ``_DEFAULTS`` keys ∪ ``EXTRA_CONFIG_KEYS``), so
    # a configured value would never reach the fetcher.
    #
    # ⚠️ The name must NOT collide with the deprecated alias
    # ``bypass_tag_filter`` (which is an alias of ``bypass_keyword_filters``
    # and is normalised/popped by ``config_sources.py``) — see
    # ``moni/fetch/filters.py::_bypass_tag_or_gate``.
    "bypass_tag_or_gate",
)


class ThresholdDecision(NamedTuple):
    """门槛判定的结果 (纯函数 ``evaluate_thresholds`` 的返回值)。

    * ``passed`` —— 是否放行;
    * ``reason`` —— 未放行时的原因码 (``below_min_followers`` /
      ``below_min_likes``); 放行时为 ``None``;
    * ``missing`` —— 遇到的缺失字段名 (``followers`` / ``likes``)。
      缺失**不影响放行** (Q2=A), 只用于计数。
    """

    passed: bool
    reason: str | None = None
    missing: tuple[str, ...] = ()


def _threshold_value(raw: Any) -> int | None:
    """把配置里的阈值读成 int; 非法/缺失 → None (= 该维度不设限)。"""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        val = int(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return val if val > 0 else None


def evaluate_thresholds(
    item: dict[str, Any],
    thresholds: dict[str, Any],
    topic: str,
) -> ThresholdDecision:
    """纯函数: 判断一条 item 是否通过该 topic 的门槛。

    不碰 DB、不读配置 —— 输入 item + 阈值表 + topic, 输出判定 + 原因码。

    规则 (详见 ``DEFAULT_THRESHOLDS`` 的注释):

    * 阈值 0 / topic 未配置 → 该维度不设限 → 永远放行;
    * 阈值 > 0 且字段**缺失** → **放行**并记入 ``missing`` (Q2=A);
    * 阈值 > 0 且字段存在但低于阈值 → 丢弃, ``reason`` 记原因码;
    * 边界相等 → 放行。
    """
    topic_key = (topic or "").strip().lower()
    cfg = thresholds.get(topic_key) if isinstance(thresholds, dict) else None
    if not isinstance(cfg, dict):
        # 未配置的 topic → 不设限。上游新增的 topic 也走这条路:
        # 放行 + warning, 绝不因门槛丢条目。
        if topic_key and topic_key not in THRESHOLD_TOPICS:
            logger.warning(
                "[xbot-feed] unknown topic %r — thresholds not applied "
                "(known topics: %s)",
                topic_key, ", ".join(THRESHOLD_TOPICS),
            )
        return ThresholdDecision(passed=True, reason=None, missing=())

    min_followers = _threshold_value(cfg.get("min_followers"))
    min_likes = _threshold_value(cfg.get("min_likes"))
    if min_followers is None and min_likes is None:
        return ThresholdDecision(passed=True, reason=None, missing=())

    missing: list[str] = []

    if min_followers is not None:
        raw_followers = item.get("author_followers")
        if raw_followers is None:
            missing.append("followers")
        elif isinstance(raw_followers, bool) or not isinstance(
            raw_followers, (int, float)
        ):
            # P2-3: 字段存在但**类型不对** (字符串 / None-ish / bool) → 与
            # 缺失同等处理: 放行 + 计数。修复前既不判缺失也不参与比较 →
            # 静默放行且计数为 0 (运维看是"门槛生效但没丢东西"的假象)。
            missing.append("followers")
        elif int(raw_followers) < min_followers:
            return ThresholdDecision(
                passed=False, reason="below_min_followers", missing=(),
            )

    if min_likes is not None:
        metrics = item.get("metrics")
        raw_likes = metrics.get("like") if isinstance(metrics, dict) else None
        if raw_likes is None:
            missing.append("likes")
        elif isinstance(raw_likes, bool) or not isinstance(
            raw_likes, (int, float)
        ):
            # P2-3: 同上 —— 类型不对按缺失处理。
            missing.append("likes")
        elif int(raw_likes) < min_likes:
            return ThresholdDecision(
                passed=False, reason="below_min_likes", missing=(),
            )

    return ThresholdDecision(passed=True, reason=None, missing=tuple(missing))

#: 匹配 X / Twitter URL 里的数字 tweet id
_STATUS_RE = re.compile(r"/status/(\d+)")

#: Twitter/X 的 host 变体, 归一化成 x.com
_HOST_ALIASES = (
    "twitter.com",
    "mobile.twitter.com",
    "www.x.com",
    "www.twitter.com",
    "x.com",
)

#: 允许的 repo_url 形态: https://github.com/<owner>/<repo>[.git]
#: (moni 只跟 github.com 打交道 —— BRIEF: 网络只到 github.com)
_REPO_URL_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$"
)

#: git 输出里可能出现的 URL 凭据段 —— 脱敏时替换成 ***
#:   https://user:token@github.com/...   → https://***@github.com/...
#:   https://token@github.com/...        → https://***@github.com/...
_CREDENTIAL_URL_RE = re.compile(
    r"(https?://)[^/\s:@]+(?::[^/\s@]*)?@(?=[^/\s]+)",
    re.IGNORECASE,
)


def _parse_feed_date(path: Path) -> date | None:
    """Parse the ``YYYY-MM-DD`` date out of a feed file's name.

    Returns ``None`` when the name doesn't carry a valid date (e.g. a
    stray ``scratch.json``) — the caller then has to decide what to do
    (the ``max_days`` filter drops such files).
    """
    stem = path.stem
    try:
        return datetime.strptime(stem, "%Y-%m-%d").date()
    except ValueError:
        return None


def _redact_git_output(text: str) -> str:
    """Strip credentials out of git's stdout/stderr before logging it.

    git 在认证失败时会把**带用户名/token 的 URL** 打进 stderr::

        fatal: Authentication failed for 'https://<token>@github.com/.../xbot-feed.git'

    那行原样写进 moni 的日志 = 凭据落盘 (BRIEF: 绝不读取/打印任何 token)。
    这里把 URL 里的 ``user:token@`` / ``token@`` 段替换成 ``***``。
    """
    if not text:
        return ""
    return _CREDENTIAL_URL_RE.sub(r"\1***@", text)


# ── sprint-51 (v1.0.95): 上游 git 临时网络失败的退避重试 ────────────────
#
# 2026-11-16 16:32 生产 xbot_feed 抓取失败 (``0 篇 0/0 (0s)``), 底层
# stderr 是 ``LibreSSL SSL_connect: SSL_ERROR_SYSCALL`` —— 上游私有仓库经
# 本机代理访问时, 隧道建立后 TLS 握手被上游重置。诊断量化: 同一条代理路径
# 上 ``git ls-remote`` 15 次采样 OK=12/FAIL=3, 且 google.com 与
# api.github.com 经同一代理也各有 1 次失败 ⇒ **隧道整体间歇抖动**, 不是
# GitHub 专项, 也不是配置丢失。
#
# 原来 ``_sync_clone`` 对 ``git pull --ff-only`` **单次尝试、无重试** ⇒
# 一次抖动即整轮失败, 上游从此不再更新 (最后一次成功 pull: 11-12 10:57)。
#
# 判据 (warden 裁定 B): **rc 只判成功/失败, 重试与否看 stderr 文本**。
# git 的 rc 在网络类失败下不携带语义 —— TLS 握手失败、仓库不存在、认证
# 失败**都是 128**; 只看 rc 会把「仓库被删了」和「隧道抖了一下」当成同一
# 类, 对永久错误做无谓重试。所以三层求值:
#
#   L1 rc == 0            → 成功, 不重试 (stderr 里可能有无关 warning)
#   L2 命中永久签名        → 不重试, 立即失败
#   L3 命中临时签名        → 重试 (在预算内)
#   L4 都不命中 (未知)     → 不重试, 立即失败 (保守默认: 宁可少重试一次)
#
# 未知不重试是有代价的 (新增的临时错误措辞不会被重试) —— 所以临时签名的
# 每一条都必须有独立测试用例, 新增签名必须同时补测试。

#: 可重试的临时网络/TLS 错误签名 (stderr 子串, 匹配时先脱敏再小写比较)。
#: 每一条都要有独立用例 (warden AC-2)。
TRANSIENT_GIT_ERROR_SIGNATURES: tuple[str, ...] = (
    "ssl_error_syscall",       # 本次生产故障 (LibreSSL SSL_connect)
    "connection reset",       # 连接被重置
    "connection timed out",   # 连接超时
    "could not resolve host",  # DNS 解析失败 (git 措辞)
    "couldn't resolve host",  # DNS 解析失败 (libc 措辞)
    "temporary failure in name resolution",  # DNS 临时失败 (libc 措辞)
    "early eof",              # 提前 EOF
    "gnutls recv error",      # GnuTLS 接收错误 (连接被非正常终止)
    "gnutls error",           # GnuTLS 通用错误 (含握手被非正常终止)
    "gnutls_handshake",       # GnuTLS 握手失败
    "curl 28",                # curl 28 (operation timeout)
    "curl 35",                # curl 35 (SSL connect error)
    "curl 52",                # curl 52 (empty reply)
    "curl 56",                # curl 56 (recv failure)
)

#: 不可重试的永久错误签名 (stderr 子串, 匹配时先脱敏再小写比较)。
#: 每一条都要有独立用例 (warden AC-3)。
PERMANENT_GIT_ERROR_SIGNATURES: tuple[str, ...] = (
    "not fast-forward",                # 本地有分叉 (pull --ff-only 拒绝)
    "authentication failed",           # 凭据无效/过期
    "could not read username",         # 缺凭据 / 交互式终端被拒
    "repository not found",            # 仓库不存在/被删/无权限
    "permission denied",               # 权限不足
    "host key verification failed",    # SSH host key 不匹配
    "local changes would be overwritten",  # 工作区有未提交改动
)

#: 一次 ``_sync_clone`` 最多尝试几次 (1 次原始 + 2 次重试)。
#: 与 ``bestblogs.py`` 的 ``for attempt in (1, 2, 3)`` 保持仓内一致。
GIT_RETRY_MAX_ATTEMPTS = 3

#: 每次重试前的退避秒数 (第 1 次失败后等 2s, 第 2 次后等 4s)。合计 6s。
GIT_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (2.0, 4.0)

#: 整个重试循环的总时限 (秒)。必须小于 job 级 ``JOB_TIMEOUT_SECONDS``
#: (600), 否则重试会把 job 预算吃掉。每次尝试的单次超时另取
#: ``min(git_timeout, 剩余预算)`` —— 否则 3×120+6=366s 会突破总时限 4 倍。
GIT_RETRY_TOTAL_TIMEOUT = 90.0


def _is_transient_git_error(stderr: str) -> bool:
    """这份 git stderr 是否属于**可重试**的临时网络/TLS 失败。

    先脱敏再匹配 (``user:token@`` 里的字符会干扰子串匹配), 再小写比较。

    永久签名优先判定 —— 一条 stderr 里同时含临时与永久签名时 (例如
    ``SSL_ERROR_SYSCALL ... Authentication failed``) 必须判**不重试**,
    避免把永久错误误判成临时错误。
    """
    if not stderr:
        return False
    text = _redact_git_output(stderr).lower()
    if any(sig in text for sig in PERMANENT_GIT_ERROR_SIGNATURES):
        return False
    return any(sig in text for sig in TRANSIENT_GIT_ERROR_SIGNATURES)


def normalize_tweet_url(raw_url: str, handle: str = "", tweet_id: str = "") -> str:
    """把 X / Twitter URL 归一成 ``https://x.com/<handle>/status/<id>``。

    与 ``moni/fetch/sources/follow_builders.py`` 的同名函数同规则:
    先抽数字 tweet id, 再用 handle 补全路径; 抽不到 id 时只做
    host 别名归一 + 去 query/fragment。

    跨源软去重靠的就是这个归一 —— ``bestblog-x`` 存的是
    ``https://twitter.com/foo/status/123?s=20``, 这里算出的是
    ``https://x.com/foo/status/123``, 两者指向同一条推文。
    """
    if not raw_url:
        raw_url = ""

    id_m = _STATUS_RE.search(raw_url)
    resolved_id = (id_m.group(1) if id_m else None) or tweet_id

    base = raw_url.split("?")[0].split("#")[0]

    if not handle:
        m = re.search(r"/status/\d+", base)
        if m:
            path = base[: m.start()]
            seg = path.rstrip("/").rsplit("/", 1)[-1]
            if seg and seg not in _HOST_ALIASES and seg != "status":
                handle = seg

    if resolved_id:
        handle_seg = (handle or "_").strip().lstrip("@")
        handle_seg = re.sub(r"[^A-Za-z0-9_]", "_", handle_seg) or "_"
        return f"https://x.com/{handle_seg}/status/{resolved_id}"

    for host in _HOST_ALIASES:
        if base.startswith(f"http://{host}"):
            return "https://x.com" + base[len(f"http://{host}"):]
        if base.startswith(f"https://{host}"):
            return "https://x.com" + base[len(f"https://{host}"):]
    return base


def _truncate_title(text: str, handle: str, limit: int = 80) -> str:
    """用正文第一行做标题, 过长则截断。

    80 字符上限照 ``moni/fetch/xwatch_syndication.py`` 的
    ``tweet_to_article()`` (79 + 省略号)。
    """
    text = (text or "").strip()
    if not text:
        return f"@{handle or 'x'} 的推文"
    first_line = text.splitlines()[0].strip()
    if len(first_line) > limit:
        return first_line[: limit - 1].rstrip() + "…"
    return first_line


def _normalize_published_at(raw: str | None) -> str | None:
    """把 ``created_at`` 归一成带时区偏移的 ISO8601 字符串。"""
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        logger.warning("[xbot-feed] unparseable created_at %r — null", raw)
        return None


def topic_tags(topic: str) -> tuple[str, ...]:
    """Return the **candidate** tags bound to a feed topic.

    未知 topic 返回空元组 —— 该条会被记成 warning 并且**不入库**:
    上游加新 topic 时不能默默塞进库里, 至少要人工确认它该绑哪个 tag。

    这只是**候选**名单; 最终挂哪些 tag 由
    ``FetcherXbotFeed._match_topic_tags()`` 按 tag 当前关键词的真实命中
    决定 (只有命中的 tag 才会被挂上)。
    """
    return TOPIC_TAGS.get((topic or "").strip().lower(), ())


def _load_tag_keywords(tag_names: Sequence[str]) -> dict[str, list[str]]:
    """Return ``{tag_name: [keywords]}`` for the given **active** tags.

    与 ``moni/fetch/tag_filter.py::_load_tag_keywords_for_source`` 同规则
    (只取 ``is_active = 1`` 的 tag, ``keywords`` 为 NULL/空/非法 JSON 的
    tag 直接跳过), 但作用域是**显式的 tag 名单**而不是"源绑定的 tag"。

    tag 的关键词是用户可在 web 端改的 —— 每次抓取都重新读, 改完下一轮
    立刻生效 (不缓存到磁盘)。
    """
    names = [t for t in dict.fromkeys(tag_names) if t]
    if not names:
        return {}
    try:
        from ...db.connection import get_db

        placeholders = ",".join("?" * len(names))
        rows = get_db().execute(
            f"""
            SELECT name, keywords FROM tag_definitions
            WHERE is_active = 1 AND name IN ({placeholders})
            """,
            names,
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[xbot-feed] cannot read tag keywords: %s", exc)
        return {}

    out: dict[str, list[str]] = {}
    for row in rows:
        raw = row["keywords"]
        if not raw:
            continue
        try:
            arr = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning(
                "[xbot-feed] tag %r has invalid JSON keywords — skipping",
                row["name"],
            )
            continue
        if not isinstance(arr, list):
            continue
        kws = [k.strip() for k in arr if isinstance(k, str) and k.strip()]
        if kws:
            out[row["name"]] = kws
    return out


def _is_active_tag(tag_name: str) -> bool:
    """True if a tag with this exact name exists and is active."""
    if not tag_name:
        return False
    try:
        from ...db.connection import get_db

        row = get_db().execute(
            "SELECT 1 FROM tag_definitions WHERE name = ? AND is_active = 1 LIMIT 1",
            (tag_name,),
        ).fetchone()
        return row is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[xbot-feed] cannot check tag %r: %s", tag_name, exc)
        return False


@register_fetcher
class FetcherXbotFeed(BaseFetcher):
    """Import the X-bot curated feed (private repo → local clone → JSON)."""

    source_name: str = "xbot_feed"

    def __init__(self) -> None:
        super().__init__()
        # 本轮生效的 fetch_config (fetch() 里从 DB 读一次)。
        # parse() 要用它算候选 tag, 所以缓存在实例上。
        self._cfg: dict[str, Any] = {}
        # 本轮真正入库的 tweet id (实例属性, 不共享 —— 见 B3)。
        self._round_ids: list[str] = []
        self._inserted_ok = False
        self._pending_state: dict[str, Any] | None = None
        # sprint-35: 门槛计数 (按 topic × 原因) —— 实例属性, 每轮重置。
        self._thresholds: dict[str, Any] = {}
        self._threshold_drops: dict[str, int] = {}
        self._threshold_missing: dict[str, int] = {}
        # sprint-38: 作者屏蔽丢弃计数 —— **标量 int** (不是 dict)。
        #
        # 为什么是标量: 门槛计数是「按 topic × 原因」的 dict (一个作者可能
        # 同时出现在 wechat 与 xcircle 两个 topic 下, 用 dict 会把同一条
        # raw 拆成两项)。而作者屏蔽是**作者级**事件 —— dad 的心智模型是
        # 「这个人我不要了」, 与「门槛调太严了」是两回事 (AC-19)。
        #
        # **不并入** threshold_dropped / threshold_missing (AC-8): 混在一起
        # 会让 sprint-35 已验收的计数口径失真, 也无法归因。
        self._author_blocked: int = 0

    # ── config ────────────────────────────────────────────────────────────
    def _load_config(self) -> dict[str, Any]:
        cfg = copy.deepcopy(_DEFAULTS)
        try:
            from ...db.connection import get_db

            row = get_db().execute(
                "SELECT fetch_config FROM sources WHERE name = ?",
                (self.source_name,),
            ).fetchone()
            if row and row["fetch_config"]:
                user = json.loads(row["fetch_config"])
                if isinstance(user, dict):
                    for k, v in user.items():
                        # sprint-35 (G-3): ``_DEFAULTS`` 的白名单只认
                        # ``DEFAULT_FETCH_CONFIG`` 的键 —— 门槛与 scope 是
                        # **独立常量** (方案 A), 不在里面 → 不放行的话
                        # 写进库的门槛会被**静默吃掉** (配了却不生效)。
                        if k in _DEFAULTS or k in EXTRA_CONFIG_KEYS:
                            cfg[k] = v
        except Exception as exc:  # noqa: BLE001
            logger.debug("[xbot-feed] read fetch_config failed: %s", exc)
        return cfg

    # ── paths ────────────────────────────────────────────────────────────
    def _data_dir(self) -> Path:
        """moni 的数据目录 (照 moni 现有的 MONI_DATA_DIR 解析方式)。"""
        import os

        env_dir = os.environ.get("MONI_DATA_DIR")
        if env_dir:
            return Path(env_dir)
        return Path("data")

    def _validate_repo_url(self, repo_url: str) -> str:
        """Validate the upstream repo URL; raise ValueError if unusable.

        v1.0.66 (audit B-6): ``repo_url`` comes from ``sources.fetch_config``
        and is editable in the ``/config`` panel.  It goes straight into
        ``git clone`` argv, so it must not be able to:

        * look like a git **option** (``--upload-pack=...``,
          ``-c core.sshCommand=...``) → arbitrary command execution;
        * use a non-https scheme (``file://`` / ``ssh://`` / ``ext::``) →
          the network egress is no longer confined to github.com;
        * carry whitespace / control characters (argv smuggling into
          git's own option parsing).

        Only ``https://github.com/<owner>/<repo>[.git]`` is accepted.
        """
        url = (repo_url or "").strip()
        if not url:
            raise ValueError("repo_url is empty")
        if url.startswith("-"):
            raise ValueError(
                f"repo_url must not start with '-': {url!r}"
            )
        if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
            raise ValueError(
                "repo_url must not contain whitespace or control characters"
            )
        if not _REPO_URL_RE.match(url):
            raise ValueError(
                "repo_url must look like https://github.com/<owner>/<repo>[.git]: "
                f"{url!r}"
            )
        return url

    def _validate_clone_dir(self, clone_root: Path) -> Path:
        """Validate that the clone target stays inside moni's data dir.

        v1.0.66 (audit B-6): ``clone_dir`` is user-editable too.  A path
        like ``/etc`` or ``../../..`` would make moni write files **outside**
        its data directory (BRIEF: 写文件只在 moni 数据目录下).
        """
        data_dir = self._data_dir().resolve()
        resolved = clone_root.resolve()
        if resolved != data_dir and not resolved.is_relative_to(data_dir):
            raise ValueError(
                f"clone_dir must stay inside the moni data dir ({data_dir}): "
                f"{clone_root}"
            )
        return resolved

    def _clone_root(self, cfg: dict[str, Any]) -> Path:
        """The local clone directory of the upstream xbot-feed repo."""
        configured = (cfg.get("clone_dir") or "").strip()
        if configured:
            path = Path(configured)
            if not path.is_absolute():
                path = self._data_dir() / path
            return path
        return self._data_dir() / CLONE_DIRNAME

    def _feed_dir(self, cfg: dict[str, Any]) -> Path:
        return self._clone_root(cfg) / FEED_SUBDIR

    def _state_path(self) -> Path:
        return self._data_dir() / STATE_FILENAME

    # ── git ──────────────────────────────────────────────────────────────
    def _sleep_backoff(self, seconds: float) -> None:
        """退避等待 (测试里被替换成 no-op, 避免真等)。

        单独抽出来是为了**测试可注入**: warden 的脆弱测试警示 W-2 说
        「不要靠真 sleep 计时」—— 断言退避**被请求的秒数序列** (== [2, 4]),
        墙钟只断言上界。
        """
        if seconds > 0:
            time.sleep(seconds)

    def _run_git_with_retry(
        self,
        argv: list[str],
        *,
        cwd: str | None,
        git_timeout: float,
        total_timeout: float,
        cleanup: Callable[[], None] | None = None,
    ) -> tuple[bool, str]:
        """跑一次 git 命令, 遇**临时网络/TLS**失败按退避重试。

        返回 ``(ok, stderr)``:

        - ``ok=True``  → git 成功 (rc == 0), ``stderr`` 是原始输出
        - ``ok=False`` → 最终失败, ``stderr`` 是**最后一次**的输出 (已脱敏)

        判据见模块级 ``_is_transient_git_error`` 的三层求值说明。

        三个约束 (warden AC-11 / AC-12 / AC-13):

        - **总时限**: ``total_timeout`` 是整个重试循环的预算; 每次尝试的
          单次超时取 ``min(git_timeout, 剩余预算)`` —— 否则 3×120+6=366s
          会突破 90s 总时限 4 倍, 还吃掉 job 级 600s 预算的 61%。
          预算耗尽后**不再发起新的 git 调用**。
        - **不死锁**: 全程不持有任何锁 —— 本函数不 new 任何 Lock, 也不碰
          任何共享状态 (并发调用互不影响)。
        - **日志保真**: 每次尝试都记「第几次 / 共几次」+ 真实 stderr
          (脱敏后), 且最终失败时能看到最后一次的 stderr。
        """
        deadline = time.monotonic() + max(0.0, total_timeout)
        attempts = GIT_RETRY_MAX_ATTEMPTS
        last_stderr = ""

        for attempt in range(1, attempts + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # 预算耗尽 → 不再发起新的 git 调用 (AC-11)。
                logger.error(
                    "[xbot-feed] git %s aborted: total budget %.0fs exhausted "
                    "after %d attempt(s): %s",
                    argv[1] if len(argv) > 1 else argv[0],
                    total_timeout,
                    attempt - 1,
                    last_stderr[:300] or "(no stderr)",
                )
                return False, last_stderr

            # 单次超时取 min(git_timeout, 剩余预算) —— 否则单次就能吃满
            # 120s, 总时限形同虚设 (warden Q-1 的核心矛盾)。
            call_timeout = min(git_timeout, remaining)

            try:
                proc = subprocess.run(
                    argv,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=call_timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                # git 自己卡死 (e.g. 隧道半开) —— 按临时网络失败处理, 可重试。
                last_stderr = f"git timed out after {call_timeout:.0f}s"
                logger.error(
                    "[xbot-feed] git %s attempt %d/%d timed out after %.0fs",
                    argv[1] if len(argv) > 1 else argv[0],
                    attempt,
                    attempts,
                    call_timeout,
                )
                if attempt >= attempts:
                    return False, last_stderr
                self._sleep_backoff(self._backoff_for(attempt))
                continue
            except OSError as exc:
                # git 起不来 (不存在 / 权限) —— 永久错误, 不重试。
                logger.error(
                    "[xbot-feed] git %s attempt %d/%d failed to start: %s",
                    argv[1] if len(argv) > 1 else argv[0],
                    attempt,
                    attempts,
                    exc,
                )
                return False, str(exc)

            if proc.returncode == 0:
                # L1: rc == 0 → 成功, 不看 stderr 里有没有无关 warning。
                return True, proc.stderr or ""

            raw = (proc.stderr or proc.stdout or "").strip()
            last_stderr = _redact_git_output(raw)

            # L2 / L3 / L4: 永久 → 不重试; 临时 → 重试; 未知 → 不重试。
            if not _is_transient_git_error(raw):
                logger.error(
                    "[xbot-feed] git %s attempt %d/%d failed (rc=%d): %s",
                    argv[1] if len(argv) > 1 else argv[0],
                    attempt,
                    attempts,
                    proc.returncode,
                    last_stderr[:300],
                )
                return False, last_stderr

            logger.error(
                "[xbot-feed] git %s attempt %d/%d failed (rc=%d, transient "
                "network error): %s",
                argv[1] if len(argv) > 1 else argv[0],
                attempt,
                attempts,
                proc.returncode,
                last_stderr[:300],
            )
            if attempt >= attempts:
                return False, last_stderr

            # 重试前的副作用清理: clone 失败会留下半成品目录, 不清掉下一次
            # clone 会撞 "destination path already exists" (warden AC-7)。
            if cleanup is not None:
                cleanup()

            self._sleep_backoff(self._backoff_for(attempt))

        return False, last_stderr

    @staticmethod
    def _backoff_for(attempt: int) -> float:
        """第 ``attempt`` 次失败后该退避多久 (秒)。"""
        idx = attempt - 1
        if idx < 0:
            idx = 0
        if idx >= len(GIT_RETRY_BACKOFF_SECONDS):
            idx = len(GIT_RETRY_BACKOFF_SECONDS) - 1
        return GIT_RETRY_BACKOFF_SECONDS[idx]

    def _sync_clone(self, cfg: dict[str, Any]) -> Path | None:
        """确保本地有一份最新的 clone; 返回 clone 根目录, 失败返回 None。

        没有就 ``git clone``, 有就 ``git pull --ff-only``。**不传任何
        凭据** —— 私有仓库的访问权来自机器上已有的 git 凭据 (SSH agent
        / credential helper), moni 自己不存 key。

        v1.0.66 (audit B-6/B-7): repo_url 与 clone_dir 先过校验 (拒绝
        以 ``-`` 开头的 git 选项 / 非 github.com 的协议 / 越出数据目录
        的路径); 所有 git 输出进日志前先脱敏 (去掉 URL 里的
        ``user:token@``)。

        v1.0.95 (sprint-51): 上游 git 遇**临时网络/TLS** 失败时按有限次数
        退避重试 (总时限 90s, 单次超时取 min(git_timeout, 剩余预算))。
        永久错误 (非快进 / 认证 / 仓库不存在 / 本地冲突) 不重试。
        """
        try:
            clone_root = self._validate_clone_dir(self._clone_root(cfg))
        except ValueError as exc:
            logger.error("[xbot-feed] invalid clone_dir: %s", exc)
            return None

        try:
            repo_url = self._validate_repo_url(
                (cfg.get("repo_url") or DEFAULT_REPO_URL).strip()
            )
        except ValueError as exc:
            logger.error("[xbot-feed] invalid repo_url: %s", exc)
            return None

        timeout = float(cfg.get("git_timeout") or 120)
        # sprint-51 (v1.0.95): 重试循环的总时限。默认 90s —— 必须远小于
        # job 级 JOB_TIMEOUT_SECONDS (600), 否则重试会把 job 预算吃掉。
        _total_raw = cfg.get("git_retry_total_timeout")
        total_timeout = float(
            _total_raw if _total_raw is not None else GIT_RETRY_TOTAL_TIMEOUT
        )

        if (clone_root / ".git").is_dir():
            logger.info("[xbot-feed] updating existing clone at %s", clone_root)
            # pull 分支**不做任何 cleanup** —— `git pull --ff-only` 失败时
            # 不会动原 clone 的 HEAD 或工作区 (warden AC-8)。
            ok, _stderr = self._run_git_with_retry(
                ["git", "pull", "--ff-only"],
                cwd=str(clone_root),
                git_timeout=timeout,
                total_timeout=total_timeout,
            )
            if not ok:
                return None
            return clone_root

        logger.info("[xbot-feed] cloning %s → %s", repo_url, clone_root)
        clone_root.parent.mkdir(parents=True, exist_ok=True)

        # 记录 clone **开始前** clone_root 是否已存在 —— 这是判断「哪些内容
        # 是我们这次 clone 造出来的」的**唯一可靠依据**（warden P0-1）。
        # 判据: 目录之前不存在 ⇒ 里面的一切都是 git clone 留下的 ⇒ 整个可删;
        #       目录之前就存在 ⇒ 那是用户数据 ⇒ **绝不能删整个目录**。
        _clone_existed_before = clone_root.exists()
        _entries_before: set[str] = set()
        if _clone_existed_before:
            try:
                _entries_before = {e.name for e in clone_root.iterdir()}
            except OSError:
                _entries_before = set()

        def _cleanup_partial_clone() -> None:
            """清掉 clone 失败留下的半成品, 但**绝不删用户数据**。

            背景 (sprint-51 warden P0-1): ``clone_dir`` 是**用户可配项**
            (UI/API 都能改), 可以指向 moni 数据目录本身, 或指向一个含用户
            重要文件的子目录。若用「目录存在就 rmtree」, 一次 clone 失败就
            会把 ``data/news.db`` 连同整个数据目录删光 ——
            ``_validate_clone_dir`` 的既有口径 (``resolved != data_dir
            and ...``) 恰好放行 ``clone_dir == 数据目录``, 该路径可达。

            修法 (按「是不是我们造的」判定, 而不是猜内容):

            - clone **之前目录不存在** ⇒ 里面的一切都是这次 clone 留下的
              ⇒ 整个目录可安全删除;
            - clone **之前目录已存在** ⇒ 用户数据 ⇒ 只删「我们这次新增的
              条目」(通常是 ``.git``), 目录本身与原有内容一律保留;
            - 任何不确定的情形 ⇒ **不删**, 只记 error 日志。

            「保守优先」: 宁可留一个垃圾目录, 也不误删生产库。
            """
            if not clone_root.exists():
                return
            try:
                entries_now = {e.name for e in clone_root.iterdir()}
            except OSError:
                return

            if not _clone_existed_before:
                # 目录是我们这次 clone 造的 ⇒ 整个删掉
                shutil.rmtree(clone_root, ignore_errors=True)
                return

            # 目录本来就在 ⇒ 只删「我们这次新增的条目」
            added = entries_now - _entries_before
            if not added:
                # 没有任何新增 ⇒ 没有我们留下的半成品 ⇒ 不动
                return
            # P2-3（warden round-2）：只删**名为 .git** 的新增条目。
            # 其余新增项（用户在 clone 期间新建的文件等）**只记日志不删** ——
            # 「是不是半成品」的真实特征就是那个 .git 目录，其余内容一律保留。
            removed: list[str] = []
            kept: list[str] = []
            for name in sorted(added):
                if name != ".git":
                    kept.append(name)
                    continue
                victim = clone_root / name
                try:
                    if victim.is_dir():
                        shutil.rmtree(victim, ignore_errors=True)
                    else:
                        victim.unlink(missing_ok=True)
                    removed.append(name)
                except OSError:
                    continue
            logger.error(
                "[xbot-feed] partial clone at %s: removed %s — kept %s",
                clone_root,
                removed or ["(nothing)"],
                sorted(_entries_before) + kept,
            )

        ok, _stderr = self._run_git_with_retry(
            # `--` 之后的参数一律当**位置参数** (路径/URL), 不再被 git
            # 当成选项解析 —— 双保险 (repo_url 已经过 _validate_repo_url)。
            ["git", "clone", "--quiet", "--", repo_url, str(clone_root)],
            cwd=None,
            git_timeout=timeout,
            total_timeout=total_timeout,
            cleanup=_cleanup_partial_clone,
        )
        if not ok:
            # 最后一次也失败 → 把半成品清掉, 不留垃圾目录。
            _cleanup_partial_clone()
            return None
        return clone_root

    # ── import state ─────────────────────────────────────────────────────
    def _load_state(self) -> dict[str, Any]:
        """Load the import state (only the imported tweet ids).

        v1.0.66 (audit S-1): the old version also round-tripped a ``files``
        dict (supposedly "已处理文件的 sha256") — **nothing ever read it**,
        and the sha256 part was never implemented.  Dead state + misleading
        docstring → dropped.
        """
        path = self._state_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"imported_ids": []}
        if not isinstance(data, dict):
            return {"imported_ids": []}
        ids = data.get("imported_ids")
        return {"imported_ids": list(ids) if isinstance(ids, list) else []}

    def _save_state(self, state: dict[str, Any]) -> None:
        path = self._state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(path)
        except OSError as exc:
            logger.warning("[xbot-feed] cannot persist import state: %s", exc)

    # ── feed files ───────────────────────────────────────────────────────
    def _feed_files(self, cfg: dict[str, Any]) -> list[Path]:
        """列出要导入的 feed 文件 (按日期倒序, latest.json 排最后)。

        ``latest.json`` 与当天的 ``YYYY-MM-DD.json`` 内容相同, 两者都
        读进来靠 tweet id 去重, 所以顺序无所谓; 但把它排到最后能让
        "最新那一份" 总是最后被处理, 日志更好读。
        """
        feed_dir = self._feed_dir(cfg)
        if not feed_dir.is_dir():
            return []

        dated: list[tuple[str, Path]] = []
        latest: Path | None = None
        for path in feed_dir.glob("*.json"):
            if path.name == LATEST_FILENAME:
                latest = path
                continue
            dated.append((path.stem, path))
        dated.sort(key=lambda pair: pair[0], reverse=True)

        files = [p for _, p in dated]
        if latest is not None:
            files.append(latest)
        return files

    def _filter_by_max_days(self, files: list[Path], max_days: int) -> list[Path]:
        """只保留最近 N **天**的日期文件 (latest.json 不受此限制)。

        v1.0.66 (audit S-2): 旧实现靠列表顺序切片 (``dated[:max_days]``),
        隐含契约是"调用方已经按日期倒序排好" —— 而且 ``max_days`` 的实际
        语义是"最近 N 个**文件**"而不是"最近 N 天": 某天没有导出文件时,
        N 个文件可能覆盖超过 N 天。

        现在按**文件名里的日期**判断: 解析 ``YYYY-MM-DD`` → 与"今天"比
        真实日期, 落在窗口外的一律丢掉。语义变成字面意义的"最近 N 天",
        不依赖排序。
        """
        if not max_days or max_days <= 0:
            return files

        today = date.today()
        cutoff = today - timedelta(days=max_days - 1)   # 含今天, 共 N 天

        dated: list[Path] = []
        latest: list[Path] = []
        for path in files:
            if path.name == LATEST_FILENAME:
                latest.append(path)
                continue
            file_date = _parse_feed_date(path)
            if file_date is None:
                # 名字里没有合法日期 → 无法判断, 保守起见**丢掉**
                # (旧实现会按排序把它算进"最近 N 个文件")。
                logger.warning(
                    "[xbot-feed] %s has no YYYY-MM-DD date in its name — "
                    "skipped by max_days filter", path.name,
                )
                continue
            if file_date >= cutoff:
                dated.append(path)

        return dated + latest

    def _read_feed_file(self, path: Path) -> list[dict[str, Any]]:
        """读一份 feed JSON, 返回 items 列表 (坏文件跳过, 不炸整轮)。"""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.error("[xbot-feed] cannot read %s: %s", path.name, exc)
            return []
        if not isinstance(payload, dict):
            logger.error(
                "[xbot-feed] %s: unexpected payload type %s",
                path.name, type(payload).__name__,
            )
            return []
        items = payload.get("items")
        if not isinstance(items, list):
            logger.error("[xbot-feed] %s: no items array", path.name)
            return []
        return [it for it in items if isinstance(it, dict)]

    # ── fetch / parse ────────────────────────────────────────────────────
    def fetch(self) -> list[dict[str, Any]]:
        """Clone/pull 上游仓库, 读取 feed/*.json, 返回还没导入的 raw item。"""
        cfg = self._load_config()
        self._prepare(cfg)

        clone_root = self._sync_clone(cfg)
        if clone_root is None:
            # git 失败 → 抛出去让 base.run() 记成 error。
            # 绝不静默返回 0 篇。
            raise RuntimeError(
                "xbot-feed: git clone/pull failed — cannot read upstream feed"
            )

        files = self._filter_by_max_days(
            self._feed_files(cfg), int(cfg.get("max_days") or 0)
        )
        if not files:
            raise RuntimeError(
                f"xbot-feed: no feed JSON under {self._feed_dir(cfg)} — "
                "the upstream repo has no data yet"
            )

        state = self._load_state()
        imported = {str(i) for i in state.get("imported_ids", [])}

        raws: list[dict[str, Any]] = []
        seen_in_round: set[str] = set()
        skipped_already = 0
        for path in files:
            for item in self._read_feed_file(path):
                tweet_id = str(item.get("id") or "").strip()
                key = tweet_id or str(item.get("url") or "").strip()
                if not key:
                    continue
                # 已经导入过的 → 跳过 (幂等: 重复跑不重复入库)。
                if key in imported:
                    skipped_already += 1
                    continue
                # 本轮内重复 (latest.json 与 YYYY-MM-DD.json 内容相同) → 跳过。
                if key in seen_in_round:
                    continue
                seen_in_round.add(key)
                raws.append(item)

        self._pending_state = state
        logger.info(
            "[xbot-feed] %d feed file(s) → %d new raw item(s) "
            "(%d already imported)",
            len(files), len(raws), skipped_already,
        )
        return raws

    def parse(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """把一条 feed item 映射成 moni 的规范 article dict。"""
        tweet_id = str(raw.get("id") or "").strip()
        handle = (raw.get("author_handle") or "").strip().lstrip("@")
        author_name = (raw.get("author_name") or "").strip()
        text = (raw.get("text") or "").strip()
        topic = (raw.get("topic") or "").strip().lower()

        if not tweet_id and not raw.get("url"):
            logger.debug("[xbot-feed] item missing id+url — skipping")
            return None

        # handle 黑名单 (刷量 / 营销 / 灰产号 + dad 手动屏蔽的作者)
        if handle and handle.lower() in self._blacklist:
            logger.debug("[xbot-feed] blacklisted handle %s — skipping", handle)
            # sprint-38: 独立计数 (标量 int), **不并入** threshold_dropped
            # —— 「门槛不达标」与「这个作者我不要了」是两套心智模型 (AC-8)。
            self._author_blocked += 1
            return None

        # topic → 候选 tag → 关键词闸门 (B1)。
        # 未知 topic (没配候选 tag、也没有同名 active tag) 不入库
        # (宁可少收也不要错打 tag)。
        candidates = self._topic_candidate_tags(topic, self._cfg)
        if not candidates:
            logger.warning(
                "[xbot-feed] item %s has unknown topic %r — skipping "
                "(no candidate tag for it)", tweet_id or raw.get("url"), topic,
            )
            return None

        # ── sprint-35: per-topic 门槛 (第二道闸门) ────────────────────
        # 独立于关键词闸门: bypass_keyword_filters 只短路黑白名单, 门槛
        # 不受它影响 (G-5)。默认全 0 → 不设限 → 完全不影响本条路径。
        # 被丢弃的条目**不进** _round_ids → 不写 state → 门槛调松后
        # 还能重新导入 (AC-8)。
        decision = evaluate_thresholds(raw, self._thresholds, topic)
        if decision.missing:
            for field in decision.missing:
                key = f"{topic}:missing_{field}"
                self._threshold_missing[key] = self._threshold_missing.get(key, 0) + 1
        if not decision.passed:
            key = f"{topic}:{decision.reason}"
            self._threshold_drops[key] = self._threshold_drops.get(key, 0) + 1
            logger.info(
                "[xbot-feed] item %s (topic=%s) dropped by threshold: %s",
                tweet_id or raw.get("url"), topic or "?", decision.reason,
            )
            return None

        canonical_url = normalize_tweet_url(
            str(raw.get("url") or ""), handle=handle, tweet_id=tweet_id,
        )

        # 跨源软去重: bestblog-x / xwatch 可能已经存过同一条推文
        if self._skip_existing_urls and self._is_url_exists_globally(
            canonical_url, tweet_id
        ):
            return None

        published_at = _normalize_published_at(raw.get("created_at"))

        content = text
        meta = self._metrics_summary(raw.get("metrics"))
        if meta:
            content = f"{text}\n\n[{meta}]" if text else f"[{meta}]"

        title = _truncate_title(text, handle)

        # 关键词闸门: 只挂**真实命中**的 tag; 零命中 → 不入库。
        # (被挡掉的条目不会进 _round_ids, 所以不会写进已导入 state ——
        #  用户改完关键词下一轮它们还能进来。)
        hit_tags, hits = self._match_topic_tags(
            topic, title, content, self._cfg
        )
        if not hit_tags:
            logger.info(
                "[xbot-feed] item %s (topic=%s) hit no candidate-tag keyword — "
                "not ingested (stays eligible for a later round)",
                tweet_id or canonical_url, topic or "?",
            )
            return None

        return {
            "title": title,
            "url": canonical_url,
            "source": self.source_name,
            "published_at": published_at,
            "content": content,
            "publisher": (
                f"{author_name} (@{handle})" if author_name or handle else None
            ),
            # topic / tag 信息: base.run() 会走 filters + tag_filter,
            # 这里只把 tag 名单带在 article dict 里给下游用。
            "xbot_topic": topic,
            "xbot_tags": list(hit_tags),
            "xbot_candidate_tags": list(candidates),
            "xbot_hits": hits,
            "xbot_tweet_id": tweet_id,
        }

    # ── topic → tag 闸门 (B1) ──────────────────────────────────────────
    def _topic_candidate_tags(self, topic: str, cfg: dict[str, Any]) -> list[str]:
        """候选 tag 名单 (topic → 候选 tag)。

        有效映射 = 内置默认 (``wechat → [wechat, miniprogram]`` /
        ``xcircle → [general-tech, ai-fintech]``) 叠加
        ``fetch_config.topic_tags`` 里用户配的条目 (web 端可改, 逐 topic
        覆盖)。

        **没配过的 topic** (上游新增的): 若存在**同名**的 active tag 就用它
        (一个人为新加 topic 的最小代价路径); 否则返回空列表 → 调用方记
        warning 并跳过该条 (宁可少收也不要错打 tag)。
        """
        mapping: dict[str, list[str]] = {
            t: list(v) for t, v in TOPIC_TAGS.items()
        }

        configured = cfg.get("topic_tags")
        if isinstance(configured, dict):
            for key, val in configured.items():
                if not isinstance(key, str):
                    continue
                if isinstance(val, str):
                    val = [val]
                if not isinstance(val, (list, tuple)):
                    continue
                topic_key = key.strip().lower()
                if not topic_key:
                    continue
                # 显式给空名单 = 停用这个 topic 的导入
                mapping[topic_key] = [
                    str(v).strip() for v in val if str(v).strip()
                ]

        topic_key = (topic or "").strip().lower()
        if topic_key in mapping:
            return list(mapping[topic_key])

        # 没配过的 topic → 找同名 active tag?
        if topic_key and _is_active_tag(topic_key):
            return [topic_key]

        return []

    def _match_topic_tags(
        self,
        topic: str,
        title: str,
        body: str,
        cfg: dict[str, Any],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """按候选 tag 的**当前关键词**匹配标题 + 正文。

        返回 ``(hit_tags, hits)``:

        * ``hit_tags`` — 真实命中的 tag 名单 (可能为空 → 该条不入库)。
        * ``hits`` — ``article_keyword_hits`` 用的命中记录, ``keyword``
          记的是**真实关键词** (不是 ``(topic:xxx)`` 这种合成串), 这样
          前端高亮出来的就是用户自己在 ``/config`` 里配的那个词。

        匹配一律走 ``moni/fetch/keyword_match.py`` 的 ``keyword_matches``
        —— AGENTS.md 铁律 1, 关键词匹配只能有这一个引擎。
        """
        from ..keyword_match import keyword_matches

        candidates = self._topic_candidate_tags(topic, cfg)
        if not candidates:
            return [], []

        tag_keywords = _load_tag_keywords(candidates)

        hit_tags: list[str] = []
        hits: list[dict[str, Any]] = []
        for tag_name in candidates:
            kws = tag_keywords.get(tag_name)
            if not kws:
                # tag 不存在 / 不 active / 没有关键词 → 不算命中
                continue
            for kw in kws:
                if keyword_matches(kw, title=title, body=body, field="title+content"):
                    hit_tags.append(tag_name)
                    hits.append({
                        "article_url": "",          # 调用方补
                        "keyword": kw,
                        "tag_name": tag_name,
                        "filter_id": None,
                        "hit_field": "title+content",
                    })
                    break                    # 一个 tag 只记一个命中关键词

        return hit_tags, hits

    # ── pipeline overrides ───────────────────────────────────────────────
    def dedupe_insert(
        self,
        articles: list[dict[str, Any]],
        keyword_hits: list[dict[str, Any]] | None = None,
    ) -> int:
        """Insert articles with the fetcher-computed, **real** keyword hits.

        ``bypass_keyword_filters`` 打开时, ``moni/fetch/tag_filter.py`` 的
        bypass 分支会给**每一篇**文章合成**全部**绑定 tag 的命中
        (``hit_field == "bypass"``) —— 那些是假的, 必须丢掉, 否则一篇
        推特圈的推文也会被打上 ``wechat``。

        真正挂到文章上的 tag 来自 ``_match_topic_tags()`` 算出的真实命中
        (记在 article dict 的 ``xbot_hits`` 里), 只挂**命中的** tag。

        文章一定带 tag (AGENTS.md 铁律 3: 任何 pass-through 路径都必须
        显式合成 hit, 否则入库后零 tag、前端筛不到) —— 零命中的条目在
        ``parse()`` 阶段就被挡掉了, 根本走不到这里。
        """
        hits: list[dict[str, Any]] = []
        for art in articles:
            art_hits = art.get("xbot_hits") or []
            for hit in art_hits:
                record = dict(hit)
                record["article_url"] = art.get("url", "")
                hits.append(record)

        # 上游闸门算出来的命中里, 只有带**真实 tag_name** 的才算数:
        # bypass 分支合成的那批 (hit_field == "bypass") 必须丢掉 (否则
        # 一篇推特圈的推文也会被打上 wechat); 全局/源级黑白名单的命中
        # 带的是 "[global]" 或没有 tag_name, 不能进 article_tags (FK)。
        for hit in keyword_hits or []:
            if hit.get("hit_field") == "bypass":
                continue
            tag_name = hit.get("tag_name")
            if not tag_name or tag_name == "[global]":
                continue
            hits.append(dict(hit))

        # 走到这一步的条目已经过了所有闸门 —— 记下它们的 id, 入库成功后
        # 才落盘 (见 _flush_state)。被闸门挡掉的条目不在这里, 所以下一轮
        # 还会被重新处理 (闸门配置变了以后它们还有机会入库)。
        for art in articles:
            tid = art.get("xbot_tweet_id")
            if tid:
                self._round_ids.append(tid)

        inserted = super().dedupe_insert(articles, hits)
        self._inserted_ok = True
        return inserted

    def run(self, dry_run: bool = False) -> dict[str, Any]:
        """Run the pipeline, then persist the import state.

        状态在**入库成功之后**才落盘 —— 抓取失败 / 入库失败都不会把
        条目标记成"已导入" (否则这批内容就永久丢了)。
        """
        self._inserted_ok = False
        self._pending_state = None
        self._round_ids = []
        result = super().run(dry_run=dry_run)
        if not dry_run and not result.get("error") and self._inserted_ok:
            self._flush_state(result)
        # sprint-35 (G-13): 门槛计数必须能贯通到 job 的 result_json →
        # 前端 ``fetchResultObj``。``moni/fetch/base.py::_ingest`` 返回的
        # 是固定 key 的 dict, ``_merge_stats`` 只认 ``_SUM_STAT_FIELDS``
        # 里的字段 —— 所以这里在 **fetcher 层**把三个键合并进结果
        # (不扩 base 的字段表, 免得影响别的源)。
        result["threshold_dropped"] = sum(self._threshold_drops.values())
        result["threshold_dropped_by"] = dict(self._threshold_drops)
        result["threshold_missing_by"] = dict(self._threshold_missing)
        # sprint-38: 作者屏蔽丢弃独立计数 (标量) —— 与上面三个门槛字段
        # **彼此独立、互不包含** (AC-8 / AC-19)。前端结果面板单列一行。
        result["author_blocked"] = self._author_blocked
        if self._author_blocked:
            logger.info(
                "[xbot-feed] author-blocked dropped %d item(s)", self._author_blocked,
            )
        if self._threshold_drops:
            logger.info(
                "[xbot-feed] threshold dropped %d item(s): %s",
                result["threshold_dropped"],
                ", ".join(
                    f"{k}={v}" for k, v in sorted(self._threshold_drops.items())
                ),
            )
        if self._threshold_missing:
            logger.info(
                "[xbot-feed] threshold missing field(s): %s",
                ", ".join(
                    f"{k}={v}" for k, v in sorted(self._threshold_missing.items())
                ),
            )
        return result

    def _flush_state(self, result: dict[str, Any]) -> None:
        """记录本轮导入过的 tweet id。"""
        state = getattr(self, "_pending_state", None) or self._load_state()
        imported = list(state.get("imported_ids", []))
        known = set(imported)
        for tid in getattr(self, "_round_ids", []) or []:
            if tid not in known:
                known.add(tid)
                imported.append(tid)
        state["imported_ids"] = imported
        self._save_state(state)

    # ── helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def _metrics_summary(metrics: Any) -> str:
        """把 metrics dict 渲染成 ``❤ 12 | 🔁 3 | 👁 90`` 形式。"""
        if not isinstance(metrics, dict):
            return ""
        parts: list[str] = []
        like = metrics.get("like")
        retweet = metrics.get("retweet")
        impression = metrics.get("impression")
        if isinstance(like, int) and like:
            parts.append(f"❤ {like}")
        if isinstance(retweet, int) and retweet:
            parts.append(f"🔁 {retweet}")
        if isinstance(impression, int) and impression:
            parts.append(f"👁 {impression}")
        return " | ".join(parts)

    @staticmethod
    def _is_url_exists_globally(canonical_url: str, tweet_id: str) -> bool:
        """True if any source already stored this tweet.

        v1.0.66 (audit B2): the old query was ``url LIKE '%status/{id}%'`` —
        the trailing ``%`` made it a **prefix** match, so tweet id ``...123``
        matched an already-stored ``...1234`` → the genuine row got silently
        swallowed by the cross-source soft dedupe (漏采).

        Now the id must be a **complete path segment**: ``/status/<id>`` must
        be followed by the end of the string, a query/fragment marker, or a
        path separator — never by another digit.
        """
        try:
            from ...db.connection import get_db

            conn = get_db()
            if not tweet_id:
                row = conn.execute(
                    "SELECT 1 FROM articles WHERE url = ? LIMIT 1",
                    (canonical_url,),
                ).fetchone()
                return row is not None

            # tweet id 一定是纯数字。非数字的 id 不做子串匹配 (LIKE 的
            # `%` / `_` 通配符会引入误命中), 只按精确 url 查。
            if not tweet_id.isdigit():
                row = conn.execute(
                    "SELECT 1 FROM articles WHERE url = ? LIMIT 1",
                    (canonical_url,),
                ).fetchone()
                return row is not None

            marker = f"/status/{tweet_id}"
            # 精确尾部匹配: marker 之后只能是 字符串结束 / '?' / '#' / '/'。
            # 用 4 个 LIKE 而不是 instr+substr —— 后者只看**第一次**出现,
            # 万一 url 里有多处 marker 会漏判; 前者对所有出现都成立。
            # tweet_id 已验证是纯数字 (不含 % / _ 通配符), 所以无需 ESCAPE。
            row = conn.execute(
                """
                SELECT 1 FROM articles
                WHERE url = ?
                   OR url LIKE ?
                   OR url LIKE ?
                   OR url LIKE ?
                   OR url LIKE ?
                LIMIT 1
                """,
                (
                    canonical_url,
                    f"%{marker}",        # 正好在结尾
                    f"%{marker}?%",      # 带 query
                    f"%{marker}#%",      # 带 fragment
                    f"%{marker}/%",      # 后面还有路径段
                ),
            ).fetchone()
            return row is not None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[xbot-feed] global url-exists check failed for %s: %s",
                canonical_url, exc,
            )
            return False

    # 缓存的黑名单 / 软去重开关, 在 fetch() 里从 config 填一次,
    # parse() 逐条用 (parse() 不该再读一遍 DB)。
    _blacklist: frozenset[str] = frozenset()
    _skip_existing_urls: bool = True
    # _cfg / _inserted_ok / _pending_state / _round_ids 都是**实例**属性,
    # 在 __init__ 里初始化 —— 绝不放在类体上共享 (见 B3)。

    @staticmethod
    def _normalize_handles(handles: object) -> set[str]:
        """把 handle 列表归一成匹配用的规范形式。

        规则不变 (与既有实现一致, 不改语义): ``strip()`` → ``lstrip("@")`` →
        ``lower()``。空串直接丢掉。

        实现委托给模块级的 :func:`normalize_handles` —— **全仓唯一实现**。
        写入侧 (author_blocks.py) 与匹配侧 (这里) 必须走同一套归一, 否则
        会出现「名单里明明有, 作者却还在入库」的静默失效 (warden 审计 P2-3)。
        """
        return normalize_handles(handles)

    def _load_db_blocked_handles(self) -> set[str]:
        """读持久化的作者屏蔽名单 (sprint-38 方案 B)。

        来源表 ``source_author_blocks`` 的列存 ``source_name`` (不是
        ``sources.id``) —— 因为 fetcher 只有类属性
        ``source_name = "xbot_feed"``, 没有 ``self.source_id``
        (warden AC-15)。

        **DB 访问必须函数内延迟 import** ``get_db`` —— 模块级 import 会
        相对导入成环 (既有先例: ``_load_config`` / ``_metrics_summary`` /
        ``_is_url_exists_globally`` 都是这么写的, AC-16)。

        读不到表 (老库还没 migrate) 时**静默降级**成空集合: 名单读不到
        不该让整轮抓取失败 —— 内置黑名单仍然生效。
        """
        try:
            from ...db.connection import get_db

            rows = get_db().execute(
                "SELECT handle FROM source_author_blocks WHERE source_name = ?",
                (self.source_name,),
            ).fetchall()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[xbot-feed] cannot read source_author_blocks: %s", exc
            )
            return set()
        return self._normalize_handles([r[0] for r in rows])

    def _prepare(self, cfg: dict[str, Any]) -> None:
        """Cache the effective config for this round."""
        self._cfg = cfg
        handles = cfg.get("blacklisted_handles")
        if handles is None:
            handles = DEFAULT_BLACKLISTED_HANDLES
        if isinstance(handles, str):
            handles = [handles]
        # sprint-38: 名单 = 内置/存量 fetch_config **并集** 持久化名单表。
        #
        # 三份来源合并 (都归一成 strip / 去 @ / 小写):
        #   1. fetch_config.blacklisted_handles —— 内置 5 个号 + 用户手填的
        #      (存量行为, 必须保留 —— AC-10 回归);
        #   2. source_author_blocks 表 —— dad 在详情弹窗里点的「屏蔽作者」
        #      (方案 B: 独立表, **不受 seed 覆盖**, AC-3);
        #   3. DEFAULT_BLACKLISTED_HANDLES —— 兜底 (只在 fetch_config 完全
        #      没有这个键时才是上面那份, 见上)。
        self._blacklist = frozenset(
            self._normalize_handles(handles)
            | self._load_db_blocked_handles()
        )
        self._skip_existing_urls = bool(cfg.get("skip_existing_urls", True))
        # sprint-35: 门槛表 (独立常量, 方案 A) —— 没配就用默认值 (全 0 = 不设限)。
        raw_thresholds = cfg.get("thresholds")
        if isinstance(raw_thresholds, dict):
            self._thresholds = raw_thresholds
        else:
            self._thresholds = copy.deepcopy(DEFAULT_THRESHOLDS)
        # 每轮的计数从头开始 (同一个 fetcher 实例可能被复用)。
        self._threshold_drops = {}
        self._threshold_missing = {}
        # sprint-38: 作者屏蔽计数同样每轮重置 (生命周期同门槛计数)。
        self._author_blocked = 0
