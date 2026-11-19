"""xbot_feed 内容质量准入决策函数 (sprint-54 / id 26111703, 阶段一).

为什么有这层
------------
现行链路里**主题相关与准入被压成同一道门**: ``xbot_feed.parse()`` 的
``_match_topic_tags()`` (xbot_feed.py:1410) 用候选 tag 的**关键词命中**当作
**入库许可** —— ``wechat`` tag 的 512 个关键词里含裸词 ``微信`` / ``小程序``,
于是劣质内容只要正文带一句「微信」就 100% 通过。

本模块把两者拆开:

* **主题相关** = 「这篇可能跟我们关心的话题有关」 ⇒ 由 tag 关键词命中回答 (既有逻辑, 不改)
* **准入**     = 「这篇能不能进正常列表」 ⇒ 由本模块回答

本模块是**纯函数**: 不读写 DB、不发起网络请求、不 import 任何有副作用的东西。
⇒ 可离线单测、可对存量数据重放、可作为只读预演器与离线验证器的唯一判定源。

三类劣质内容 (dad 2026-11-17 提) 与强度分层
------------------------------------------
============  ====================  ==========  ====================================
类别          reason_code           强度        处置
============  ====================  ==========  ====================================
黄赌毒        ``mod.nsfw.promo``    强         quarantine
交易广告      ``mod.fin.crypto.ad`` 强         quarantine
灰产服务      ``mod.spam.service``  强         quarantine
低质情绪      ``mod.lowvalue.rant`` 弱         quarantine (**永不自动封作者**)
============  ====================  ==========  ====================================

**弱特征铁律**: 感叹号/问号密度、正文有效字数、粉丝数、点赞数 —— 这些**单独出现
永不升级到作者级**。只有同一作者在**多个独立样本**上重复命中同一强信号,
才产出 ``author_signal=True`` 的**候选**, 仍需 dad 确认 (本模块**不**执行封禁)。

硬边界 (写死, 改动需改 PLAN)
----------------------------
1. **媒体无 URL 是硬盲区**: 上游 feed (实测 2084 条) 的字段全集里**没有任何图片/视频
   URL**, 也没有 X 的 ``possibly_sensitive``; moni 全仓无图像分类能力。
   ⇒ 本模块**不得**声称能识别 NSFW 媒体; 配文干净但配图露骨的账号**必然漏放**,
   只能靠作者级封禁兜底。
2. **不引入外部模型**: 未经 dad 批准的模型依赖 = 费用 + 正文外发 + 隐私 + 数据出境。
   本模块只用确定性规则, 阈值一律标「待实测建议」。
3. **组合证据, 不做单词一刀切**: 出现「性/赌/毒/股票/比特币/合约」本身**不构成**违规;
   必须是「敏感词 + 引流/招揽动作」的**组合**。
4. **否定样本不得误杀**: 性健康科普 / 医疗普法 / 案件政策新闻 / 反诈禁毒宣传 /
   正常投资分析讨论 / 安全研究员分析诈骗手法 —— 这些命中豁免词簇时**降级放行**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Sequence

#: 规则版本 —— 写进每条决策, 让「哪一版规则判的」可追溯。
#: 规则词簇一改 ⇒ 版本号必须跟着改 (否则历史决策无法复现)。
RULE_VERSION = "xbot-mod-v1"

#: 决策动作。
#:
#: - ``allow``          放行 (走既有入库路径)
#: - ``quarantine``     文章级隔离 (进 basket, 不进 articles)
#: - ``author_candidate`` 产出**作者级候选** (本模块**不**执行封禁!)
Action = Literal["allow", "quarantine", "author_candidate"]

#: 质量类别。
Category = Literal[
    "nsfw_gamble",       # 黄赌毒 / 招嫖 / 成人福利引流
    "fin_crypto_ad",     # 股票 / 期货 / BTC / 虚拟币 带单引流
    "spam_service",      # 代写 / 刷粉 / 拉票 / 代购 灰产
    "pure_ad",           # 商品/课程/服务硬推销（无专项词，但三要素成立）
    "engagement_bait",   # 涨粉 / 互关 / 求转发 / 集赞
    "low_value_rant",    # 感叹号密集 / 纯情绪发泄 / 极短水帖
]


@dataclass(frozen=True)
class Decision:
    """一次判定的结果 (不可变, 便于当缓存键与快照签名用)。"""

    action: Action
    category: Category | None = None
    reason_code: str | None = None
    #: 命中的信号 (可解释, **不含原文**, 只含「命中了什么」)
    evidences: tuple[str, ...] = ()
    #: True 表示**只**命中了弱特征 ⇒ 绝不可用于自动封号
    is_weak_only: bool = False
    #: True 表示该作者在多个样本上重复命中强信号 ⇒ 可产出作者级**候选**
    author_signal: bool = False
    #: 规则版本 (写进每条决策)
    rule_version: str = RULE_VERSION

    @property
    def allowed(self) -> bool:
        return self.action == "allow"


# ── 词簇 (全部为「待实测建议」起点, 未经标注集标定不得当作已验证阈值) ──────────

#: 敏感词 (只说明「话题沾边」, **单独出现不违规**)
#: 黄赌毒词簇（标注路由要单独用它，所以从敏感词里拆出来；
#: ``_SENSITIVE_TERMS`` 仍是两者的并集，xbot 行为不变）。
_NSFW_TERMS: tuple[str, ...] = (
    "招嫖", "上门", "约炮", "裸聊", "成人福利", "福利姬", "博彩", "网赌",
    "百家乐", "六合彩", "私彩", "毒品", "冰毒", "大麻", "摇头丸",
)

#: 灰产服务词簇。
_GRAY_TERMS: tuple[str, ...] = (
    "代写", "代做", "刷粉", "刷赞", "刷量", "拉票", "买票", "代购",
    "刷单", "兼职刷",
)

#: 敏感词 (只说明「话题沾边」, **单独出现不违规**)。
#: ⚠️ 交易/币 词**不在**这里 —— 它们属于 `_MARKET_TERMS`。
#: 放两处会让 crypto 类被 nsfw 分支抢走 (词表重叠 ⇒ 类别错判)。
_SENSITIVE_TERMS: tuple[str, ...] = _NSFW_TERMS + _GRAY_TERMS

#: 引流/招揽动作 (与敏感词**组合**才构成强证据)
_SOLICIT_ACTIONS: tuple[str, ...] = (
    "加微", "加微信", "私信", "拉群", "进群", "群号", "客服", "助理",
    "门槛", "电报", "飞机", "telegram", "威信", "vx", "薇信",
    "扫码", "扫我", "联系我", "私聊", "福利群",
)

#: 账号名里的灰产招牌词 (账号名本身就是广告)
_NAME_STUFFING_TERMS: tuple[str, ...] = (
    "代写", "代做", "代购", "刷粉", "刷赞", "刷量", "拉票", "买票",
    "刷单", "包过", "代发", "水军",
)

#: 币/股标的词 (只说明话题沾边)
_MARKET_TERMS: tuple[str, ...] = (
    "BTC", "ETH", "USDT", "合约", "期货", "股票", "涨停", "带单",
    "止盈", "止损", "现货", "杠杆", "出入金",
)

#: 情绪宣泄/低信息信号 (**弱特征**, 单独出现永不封号)
_RANT_TERMS: tuple[str, ...] = (
    "家人们", "绝了", "救命", "崩溃", "离谱", "无语", "疯了", "笑死",
)

#: **强豁免词** —— 命中即**降级放行**, 无条件压过所有强信号。
#:
#: 只有司法/反诈/禁毒这类**语境标志**够格: 它们出现时, 内容几乎必然是
#: 「报道某案件」而不是「本人作案」。
_EXEMPT_STRONG_TERMS: tuple[str, ...] = (
    # 新闻/通报/司法
    "警方", "通报", "查处", "判刑", "判处", "曝光", "立案", "起诉",
    "法院", "检察", "监管", "通报称",
    # 反诈/禁毒
    "反诈", "诈骗手法", "骗局", "杀猪盘", "禁毒", "戒毒",
)

#: **弱豁免词** —— 只有在**没有招揽动作**时才降级放行。
#:
#: ⚠️ 这些是**行业通用词**, 与黑产文案**高频共现** (p8 P2-C):
#:   "BTC合约带单, 量化风控稳定盈利, 出入金防冻卡, 加微信进群"
#:   —— 命中「量化/风控/冻卡」但同时有明确招揽动作 ⇒ **不得**豁免。
#:   "海外教育论文代写, 加微信私聊" —— 命中「教育」同理。
#:   ⇒ 只有在**无** ``_SOLICIT_ACTIONS`` 命中时, 弱豁免词才生效
#:   (正常投资讨论本来就没有招揽动作, 天然会走到 allow)。
_EXEMPT_WEAK_TERMS: tuple[str, ...] = (
    # 科普/普法
    "科普", "普法", "宣传", "警惕", "远离", "防范", "谨防", "教育",
    # 正常投资分析
    "宏观", "研报", "合规", "冻卡", "量化", "风控", "监管新规",
)

#: 合并词簇 (兼容旧引用)
_EXEMPT_TERMS: tuple[str, ...] = _EXEMPT_STRONG_TERMS + _EXEMPT_WEAK_TERMS

#: 弱特征阈值 (**待实测建议** —— 未经标注集标定不得当作已验证值)
#:
#: - 感叹号/问号总数 ≥ 此值 ⇒ 记为「标点爆发」信号
#: - 去掉 URL 后的有效正文 < 此值 ⇒ 记为「极短口水贴」信号
PUNCTUATION_BURST_MIN = 6
SHORT_BODY_MAX_CHARS = 25

#: 同一作者重复命中强信号的最小样本数 (**待实测建议**)
AUTHOR_SIGNAL_MIN_SAMPLES = 2

#: 行为化弱特征豁免所需的「正常入库历史篇数」下限 (**待实测建议**)
CLEAN_HISTORY_MIN = 3

#: 具体业务词 —— 用于判定「是否**仅**命中裸词『微信』」。
#:
#: ⚠️ 判定必须基于**完整命中集** (重放 512 词表), 不能读
#: ``article_keyword_hits`` (那个表每个 tag 只记首词 ⇒ 是下界)。
#: ⚠️ 全部**小写** —— 判定时 ``matched`` 已 ``.lower()``,
#: 这里不统一小写的话, 将来有人加一个大小写混合的词就会求交失效
#: (p8 P2-B)。中文不受 ``.lower()`` 影响。
_SPECIFIC_BUSINESS_TERMS: frozenset[str] = frozenset(
    w.lower()
    for w in (
        "小程序", "公众号", "微信支付", "视频号", "企业微信", "微信小程序",
        "微信公众号", "微信服务号", "微信订阅号", "微信红包", "微信转账",
        "微信卡包", "会员卡", "微信开放平台", "微信公众平台", "微信搜一搜",
        "微信看一看", "微信广告", "微信客服", "微信钱包", "微信生活缴费",
        "微信扫码", "微信扫一扫", "微信开放标签", "微信网页授权",
        "miniprogram", "公众号文章",
    )
)

#: URL 提取 (用于算「有效正文长度」)
_URL_RE = re.compile(r"https?://\S+")
_PUNCT_RE = re.compile(r"[!！?？]")

#: 归一化用的小写 + 去符号
_CJK_LATIN_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _contains_any(text: str, terms: Sequence[str]) -> tuple[str, ...]:
    """返回 ``terms`` 中出现在 ``text`` 里的那些 (原样, 大小写不敏感)。"""
    if not text:
        return ()
    low = text.lower()
    return tuple(t for t in terms if t.lower() in low)


def _effective_body_len(text: str) -> int:
    """去掉 URL 后的有效正文长度 (空白也去掉)。"""
    if not text:
        return 0
    stripped = _URL_RE.sub("", text)
    return len(re.sub(r"\s+", "", stripped))


def _punctuation_count(text: str) -> int:
    """感叹号 + 问号 的总数。"""
    return len(_PUNCT_RE.findall(text or ""))


def _name_tokens(author_name: str) -> tuple[str, ...]:
    """账号名里的小写字母/数字 token (用于英文灰产招牌词匹配)。"""
    if not author_name:
        return ()
    return tuple(_CJK_LATIN_TOKEN_RE.findall(author_name.lower()))


def evaluate_article(
    *,
    title: str,
    content: str,
    author_name: str = "",
    handle: str = "",
    topic: str = "",
    author_followers: int | None = None,
    like_count: int | None = None,
    author_strong_sample_count: int = 0,
    author_is_curated: bool = False,
    all_matched_keywords: Sequence[str] = (),
    author_clean_history_count: int = 0,
) -> Decision:
    """判定一篇文章的质量准入。

    **纯函数** —— 不读 DB、不发网络、不依赖全局状态。

    Args:
        title: 文章标题 (xbot 的标题 = 正文首行, 截断 80 字)
        content: 正文
        author_name: 作者显示名 (``articles.publisher`` 里 ``(@handle)`` 之前那截)
        handle: 作者 handle (已归一或未归一皆可; 本模块只做展示用)
        topic: feed topic (``wechat`` / ``xcircle``)
        author_followers: 粉丝数 (**只在下架时可得** —— 上游 feed item 才有, 入库后不可复盘)
        like_count: 点赞数 (同上)
        author_strong_sample_count: 该作者此前**已命中强信号**的独立样本数
            (由调用方在跨文章聚合时统计; 本模块不查 DB)
        author_is_curated: 该作者是否属于**人工精选**集合 (如 xcircle 的 28 个技术大 V)
            ⇒ 命中时**豁免弱特征检查** (但**不**豁免强证据)
        all_matched_keywords: 该文命中的**全部** tag 关键词 (由调用方重放完整词表得出)
            ⚠️ **不能**只读 ``article_keyword_hits``: ``xbot_feed.py:1454`` 在
            ``_match_topic_tags`` 里每个 tag 命中首词即 ``break``, 该表**只记了
            每个 tag 的第一个命中词** ⇒ 一篇正文同时含「微信」与「微信小程序」的
            文章, 表里只有「微信」⇒ 只看该表会把真业务内容误判成「仅命中裸词」。
            ⚠️ 口径说明 (生产 257 篇 xbot_feed 实测): 「并集」与「只看 wechat tag
            的命中集」两种口径的判定结果**逐篇相等** (差 0 篇) ⇒ 保留**并集**口径
            (更保守: 任何 tag 命中了具体业务词 ⇒ 都不算「仅裸词」),
            **不**引入按 tag 分组的第二套口径 (无收益, 且会硬编码 tag 名)。
        author_clean_history_count: 该作者在库内**正常入库**的历史篇数
            ⇒ 用作**行为化**弱特征豁免 (≥3 篇正常记录 ⇒ 豁免第 3 类弱特征),
            **不依赖**会漂移的 ``DEFAULT_SCOPE["xcircle_accounts"]`` 手抄名单

    Returns:
        Decision
    """
    text = f"{title or ''}\n{content or ''}"

    # ── 0. 裸词微信的判定必须基于**完整词表**重放 ───────────────────
    # ⚠️ 不可只读 ``article_keyword_hits`` (那个表每个 tag 只记首词, 是下界)。
    # 判定「是否只靠裸词『微信』进来」时, 必须看**完整命中集**里有没有
    # 具体业务词 (小程序/公众号/微信支付/视频号/企业微信…)。
    matched = {k.lower() for k in (all_matched_keywords or ())}
    only_bare_wechat = bool(matched) and ("微信" in matched) and not (
        matched & _SPECIFIC_BUSINESS_TERMS
    )

    # ── 1. 否定样本豁免 (分两档, p8 P2-C) ────────────────────────────
    sensitive = _contains_any(text, _SENSITIVE_TERMS)
    solicit = _contains_any(text, _SOLICIT_ACTIONS)
    market = _contains_any(text, _MARKET_TERMS)

    # 账号名里的灰产招牌词 —— **正文豁免词管不到它**。
    # 「XX教育Essay代写」这类账号名本身就是广告, 不该被正文里的
    # 「教育」二字豁免掉。
    name_stuffing = _contains_any(author_name or "", _NAME_STUFFING_TERMS)

    # 强豁免 (司法/反诈/禁毒语境) ⇒ 无条件降级放行
    exempt_strong = _contains_any(text, _EXEMPT_STRONG_TERMS)
    if exempt_strong and not name_stuffing:
        return Decision(
            action="allow",
            evidences=tuple(f"exempt_strong_term:{e}" for e in exempt_strong[:3]),
        )

    # 弱豁免 (行业通用词) ⇒ **仅当没有招揽动作、且账号名不是广告**时降级放行。
    # ⚠️ 有招揽动作时, 弱豁免词**不得**压过强信号 (p8 P2-C):
    #    "量化风控 + 加微信进群" 是带单黑产, 不是正常投资分析。
    # ⚠️ 账号名堆砌时, 弱豁免词**一律**不生效 (p7 最终复核 P1-1·新):
    #    豁免只豁免「内容像合规语境」, 豁免不了「账号名本身就是广告」。
    #    实测漏放: content="海外教育论文代写 辅导",
    #    author_name="论文教育Essay代写" ⇒ 原先 allow(教育) ⇒ 现 quarantine。
    exempt_weak = _contains_any(text, _EXEMPT_WEAK_TERMS)
    if exempt_weak and not solicit and not name_stuffing:
        return Decision(
            action="allow",
            evidences=tuple(f"exempt_weak_term:{e}" for e in exempt_weak[:3]),
        )

    # ── 2. 强信号: 交易引流 (市场词 + 引流动作) ────────────────────
    # ⚠️ 放在 NSFW 分支**之前**: 市场词 (BTC/合约/带单) 更具体, 若让 NSFW
    # 先跑, 币圈引流会被误标成 nsfw_gamble (词表重叠 ⇒ 类别错判)。
    if market and solicit:
        return Decision(
            action="quarantine",
            category="fin_crypto_ad",
            reason_code="mod.fin.crypto.ad",
            evidences=(
                tuple(f"market_term:{m}" for m in market[:2])
                + tuple(f"solicit_action:{a}" for a in solicit[:2])
            ),
            # ⚠️ sprint-54 / id 26111703 (p8 P1-A): 强信号命中时
            # **可能**产出作者级候选 —— 但只是**候选**, 绝不自动封禁。
            # 缺了这一行, ``author_signal`` 对任何输入恒为 False,
            # 「先构造真产出 author_signal=True 的输入」就不可达。
                author_signal=evaluate_author_candidate(
                    author_strong_sample_count=author_strong_sample_count
                ),
        )

    # ── 3. 强信号: 黄赌毒 ──────────────────────────────────────────
    if sensitive and solicit:
        return Decision(
            action="quarantine",
            category="nsfw_gamble",
            reason_code="mod.nsfw.promo",
            evidences=(
                tuple(f"sensitive_term:{s}" for s in sensitive[:2])
                + tuple(f"solicit_action:{a}" for a in solicit[:2])
            ),
            # ⚠️ sprint-54 / id 26111703 (p8 P1-A): 见上 —— 强信号才可能
            # 产出作者级**候选** (绝不自动封禁)。
                author_signal=evaluate_author_candidate(
                    author_strong_sample_count=author_strong_sample_count
                ),
        )

    # ── 4. 强信号: 灰产服务 (账号名堆砌) ───────────────────────────
    if name_stuffing:
        return Decision(
            action="quarantine",
            category="spam_service",
            reason_code="mod.spam.service",
            evidences=tuple(f"author_name_stuffing:{n}" for n in name_stuffing[:2]),
            # ⚠️ sprint-54 / id 26111703 (p8 P1-A): 见上 —— 强信号才可能
            # 产出作者级**候选** (绝不自动封禁)。
                author_signal=evaluate_author_candidate(
                    author_strong_sample_count=author_strong_sample_count
                ),
        )

    # ── 5. 弱信号: 低质情绪/口水贴 ─────────────────────────────────
    # **精选作者豁免弱特征** (技术大 V 偶尔也发带感叹号的吐槽)。
    if author_is_curated:
        return Decision(action="allow", evidences=("author_curated:weak_exempt",))

    rant = _contains_any(text, _RANT_TERMS)
    punct = _punctuation_count(text)
    body_len = _effective_body_len(content)
    weak_evidences: list[str] = []
    if punct >= PUNCTUATION_BURST_MIN:
        weak_evidences.append(f"punctuation_burst:{punct}")
    if rant:
        weak_evidences.append(f"rant_term:{rant[0]}")

    # ⚠️ 「正文短」**不能单独**构成弱特征 —— 任何短技术帖 (「分包后主包体积
    # 减少 60%」= 21 字) 都会被它误杀。短**只**在与标点爆发/情绪词**同时**
    # 出现时才算一个佐证信号 (组合证据原则的又一次应用)。
    if body_len < SHORT_BODY_MAX_CHARS and weak_evidences:
        weak_evidences.append(f"short_body:{body_len}")

    # 「仅靠裸词『微信』进来」的弱信号 —— **仅当完整命中集证明只命中了裸词**时
    # 才成立 (不看 article_keyword_hits 那个下界表)。
    if only_bare_wechat:
        weak_evidences.append("only_bare_wechat")

    # ⚠️ 「≥N 篇干净历史」只能当**弱信号降权**, **不能**当硬豁免
    # (dad 2026-11-17 拍板):
    #   · 降权 = 干净历史作者需要**更多佐证**才被隔离 (阈值 1 → 2 条弱证据),
    #     而不是直接放行;
    #   · 绝不因此绕过高置信安全信号 (黄赌毒 / 交易推广) —— 那两类在
    #     第 2/3 步就**已经**返回 quarantine, 根本走不到这里;
    #   · 阈值是**暂定值**, 必须离线验证; agent 草标不是 dad 真值,
    #     不得当作已测准确率。
    downweighted = False
    if (
        author_clean_history_count >= CLEAN_HISTORY_MIN
        and author_strong_sample_count == 0
    ):
        downweighted = True

    if weak_evidences and (not downweighted or len(weak_evidences) >= 2):
        # ⚠️ 弱特征**永远不**升级成作者级封禁。
        # 只有**同一作者多篇独立样本重复命中强信号**才产出作者候选。
        return Decision(
            action="quarantine",
            category="low_value_rant",
            reason_code="mod.lowvalue.rant",
            evidences=tuple(weak_evidences),
            is_weak_only=True,
            author_signal=False,
        )

    if downweighted and weak_evidences:
        # 降权后仍不足以隔离 ⇒ 放行, 但**留痕**降权依据 + 被降权的弱证据
        # (p8 P2-A: 0 条弱证据时**不该**出现 downweighted 痕迹 ——
        #  正常推文不该留下「被降权」的审计噪声)。
        return Decision(
            action="allow",
            evidences=(
                *weak_evidences,
                f"author_clean_history_downweighted:{author_clean_history_count}",
            ),
        )

    # ── 6. 放行 ────────────────────────────────────────────────────
    return Decision(action="allow")


def evaluate_author_candidate(
    *,
    author_strong_sample_count: int,
    min_samples: int = AUTHOR_SIGNAL_MIN_SAMPLES,
) -> bool:
    """该作者是否够格进入**作者级候选** (供 dad 确认)。

    **只回答「够不够格进候选」, 绝不回答「要不要封」** —— 封禁是 dad 的决定。

    弱特征命中的文章**不计入** ``author_strong_sample_count``
    (调用方只把**强信号**的样本数传进来) ⇒ 弱特征天然无法升级到作者级。
    """
    if min_samples <= 0:
        return False
    return author_strong_sample_count >= min_samples


# ── 跨文章聚合 (阶段一 I3/I5 的调用方契约, p7 C-7) ──────────────────────────

@dataclass
class AuthorAggregate:
    """同一作者在**一批**文章上的聚合结果。

    ⚠️ **弱特征命中的文章永远不计入强样本数** —— 这是「弱特征不升级成作者级
    封禁」这条规则的**端到端**保证。

    p7 第二轮 P1-1 / 第四轮 C-7 指出: 纯函数 ``evaluate_article()`` 看不到
    「同作者的其他篇」⇒ 单点断言测不到「弱特征命中集 ∩ 作者候选计数集 = ∅」。
    ⇒ 聚合必须在这里做, 且**调用方只能把本类的 ``strong_sample_count``
    传进** ``evaluate_author_candidate()``。

    字段:
        ``author_key``      —— 作者标识 (通常 ``@handle`` 或 ``publisher``)
        ``strong_samples``  —— 命中**强**信号的文章数 (弱信号**不计入**)
        ``weak_samples``    —— 命中**弱**特征的文章数
        ``author_candidate`` —— 是否够格进入**作者级候选** (仍需 dad 确认)
        ``weak_article_ids`` —— 弱特征命中的文章 id (供交叉断言用)
        ``strong_article_ids`` —— 强信号命中的文章 id
    """

    author_key: str
    strong_samples: int = 0
    weak_samples: int = 0
    weak_article_ids: tuple[str, ...] = ()
    strong_article_ids: tuple[str, ...] = ()
    author_candidate: bool = False


def aggregate_author(
    *,
    author_key: str,
    decisions: Sequence[tuple[str, Decision]],
    min_samples: int = AUTHOR_SIGNAL_MIN_SAMPLES,
) -> AuthorAggregate:
    """把同一作者的**一批**决策聚合成作者级结果。

    ⚠️ **关键不变量**（p7 C-7 的交叉断言对象）::

        弱特征命中的文章 id 集合 ∩ 计入作者候选计数的文章 id 集合 == ∅

    ⇒ 弱特征**永远**不会把一个作者推成作者级候选。
    弱特征命中的文章只累加 ``weak_samples``, **不**累加 ``strong_samples``。

    Args:
        author_key: 作者标识。
        decisions: ``(article_id, Decision)`` 序列 —— 每个元素是**一篇**文章的
            决策结果 (通常来自 ``evaluate_article()``)。
            ⚠️ **article_id 是 TEXT** (生产实测: 24 位十六进制串) ⇒ 按 str 处理。
        min_samples: 产出作者候选所需的**强**样本数。

    Returns:
        AuthorAggregate
    """
    strong_ids: list[str] = []
    weak_ids: list[str] = []

    for article_id, decision in decisions:
        if decision.is_weak_only:
            # ⚠️ 弱特征 ⇒ 只记 weak, **绝不**进 strong 计数
            weak_ids.append(article_id)
            continue
        if decision.action == "quarantine":
            strong_ids.append(article_id)

    strong_count = len(strong_ids)
    weak_count = len(weak_ids)

    # ⚠️ 计数**只**用 strong_ids ⇒ 弱特征永远推不上去
    candidate = evaluate_author_candidate(
        author_strong_sample_count=strong_count, min_samples=min_samples
    )

    return AuthorAggregate(
        author_key=author_key,
        strong_samples=strong_count,
        weak_samples=weak_count,
        weak_article_ids=tuple(weak_ids),
        strong_article_ids=tuple(strong_ids),
        author_candidate=candidate,
    )


# ── 全语料标注 (sprint-58) ───────────────────────────────────────────────────
#
# 为什么不能直接复用 ``evaluate_article()``
# ------------------------------------------
# ``evaluate_article()`` 是 **xbot 准入** 判定：它默认"宁拦勿放"，并且把
# 「敏感词 + 招揽动作」当作强证据。全语料里同一组词会大面积误杀：实测
# 4,663 篇非空正文上跑 ``evaluate_article()``，``fin_crypto_ad`` 42 篇、
# ``nsfw_gamble`` 14 篇，全部是正常文章（"股票 + 客服"、"杠杆 + 门槛"、
# "代做 + 客服"、"上门 + 扫码"）—— 单词共现在这份语料里等于误杀。
#
# 因此标注路径用**同一套词表 + 更严的结构约束**：
#   联系方式与利益承诺必须落在同一个 80 字窗内；先专项后通用。
# 两个入口共享词簇与豁免表，不允许出现第三套词表。

#: 标注规则版本 —— 与 xbot 的 ``RULE_VERSION`` 分开，两边可独立演进。
LABEL_RULE_VERSION = "labels-v1"

#: 局部窗半径：联系方式前后各 80 字。
LABEL_WINDOW = 80


@dataclass(frozen=True)
class Label:
    """一条内容标签（纯数据，可直接落库）。"""

    category: str
    severity: Literal["strong", "weak"]
    confidence: float
    #: 只记录"命中了哪一类信号"，**不存原文、不存账号**
    evidences: tuple[str, ...] = ()


#: 联系方式 —— 带边界，排除"参加/增加/加速"这类伪命中。
_LABEL_CONTACT_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("add_wechat", re.compile(r"(?<![参增更追添])加(?:我)?微信|(?<![参增更追添])加微")),
    ("wechat_id", re.compile(r"微信号[：:]\s*[A-Za-z][A-Za-z0-9_-]{4,19}")),
    ("qq_id", re.compile(r"qq号[：:]\s*\d{5,}", re.IGNORECASE)),
    ("group_qr", re.compile(r"扫码进群|进群(?:领|聊|获取)")),
    ("dm", re.compile(r"私信我")),
    ("telegram", re.compile(r"tg[：:]\s*@\w+", re.IGNORECASE)),
)

#: 利益承诺 —— 只保留强承诺，裸词"翻倍/必涨/收益"不算。
_LABEL_PROMISE_RE = re.compile(
    r"稳赚|保本保息|收益翻倍|本金翻倍|包过|日入\d|躺赚|内部渠道|免费领取"
)

#: 真实引流凭证 —— 强豁免的唯一例外。
_REAL_CREDENTIAL_RE = re.compile(
    r"微信号[：:]\s*[A-Za-z][A-Za-z0-9_-]{4,19}"
    r"|qq号[：:]\s*\d{5,}"
    r"|tg[：:]\s*@\w+",
    re.IGNORECASE,
)

#: 强豁免（司法/反诈/禁毒语境）：命中即不产强标签。
_LABEL_HARD_EXEMPT = re.compile(
    r"反诈|禁毒|反洗钱|案件|判决|监管处罚|行政处罚|警方通报|查处|抓获"
)

#: 弱豁免：只压制弱标签。
_LABEL_SOFT_EXEMPT = re.compile(r"科普|风险提示|合规声明|研报|白皮书")

#: 涨粉模板（严格版）。"互关" 必须排除 "相互关联/相互关系" 这类正常词。
_BAIT_RE = re.compile(r"涨粉|互关(?!联|系)|求关注|求转发|集赞")

#: 结构豁免：长文 / 节目简介天然含"关注/订阅"。
_PODCAST_RE = re.compile(r"本期|节目|嘉宾|EP\d+|播客")

#: 低质情绪 ONLY 判定用的情绪词（含口语脏话，xbot 那份 `_RANT_TERMS` 不动）。
_LABEL_RANT_RE = re.compile("|".join(_RANT_TERMS) + r"|卧槽|我靠")

#: 各类别的强度与置信度。
_LABEL_STRENGTH: dict[str, tuple[str, float]] = {
    "nsfw_gamble": ("strong", 0.9),
    "fin_crypto_ad": ("strong", 0.9),
    "spam_service": ("strong", 0.9),
    "pure_ad": ("strong", 0.8),
    "engagement_bait": ("weak", 0.4),
    "low_value_rant": ("weak", 0.4),
}


def _contact_evidence(window: str) -> str | None:
    for name, pattern in _LABEL_CONTACT_RES:
        if pattern.search(window):
            return name
    return None


def _strong_label(text: str) -> Label | None:
    """三要素 + 80 字窗；返回**一个**强标签（先专项后通用）。"""
    if _LABEL_HARD_EXEMPT.search(text) and not _REAL_CREDENTIAL_RE.search(text):
        return None

    windows: list[tuple[str, str]] = []
    for name, pattern in _LABEL_CONTACT_RES:
        # 同一类联系方式最多看前 5 次出现：前段正常提及「加微信」、
        # 后段才是「加微信…稳赚」的长文不能被前面的 break 漏掉。
        for m in list(pattern.finditer(text))[:5]:
            start = max(0, m.start() - LABEL_WINDOW)
            windows.append((name, text[start : m.end() + LABEL_WINDOW]))
    if not windows:
        return None

    def _hit(window: str, cluster: Sequence[str]) -> bool:
        low = window.lower()
        return any(term.lower() in low for term in cluster)

    for contact, window in windows:
        if _hit(window, _NSFW_TERMS):
            severity, confidence = _LABEL_STRENGTH["nsfw_gamble"]
            return Label(
                category="nsfw_gamble",
                severity=severity,
                confidence=confidence,
                evidences=(f"contact:{contact}", "cluster:nsfw_gamble"),
            )
        if _hit(window, _MARKET_TERMS) and _LABEL_PROMISE_RE.search(window):
            severity, confidence = _LABEL_STRENGTH["fin_crypto_ad"]
            return Label(
                category="fin_crypto_ad",
                severity=severity,
                confidence=confidence,
                evidences=(f"contact:{contact}", "cluster:fin_crypto_ad"),
            )
        if _hit(window, _GRAY_TERMS):
            severity, confidence = _LABEL_STRENGTH["spam_service"]
            return Label(
                category="spam_service",
                severity=severity,
                confidence=confidence,
                evidences=(f"contact:{contact}", "cluster:spam_service"),
            )

    for contact, window in windows:
        if any(term.lower() in window.lower() for term in _NAME_STUFFING_TERMS):
            severity, confidence = _LABEL_STRENGTH["spam_service"]
            return Label(
                category="spam_service",
                severity=severity,
                confidence=confidence,
                evidences=(f"contact:{contact}", "cluster:spam_service"),
            )

    contact = next(
        (
            contact
            for contact, window in windows
            if _LABEL_PROMISE_RE.search(window)
        ),
        None,
    )
    if contact is None:
        return None
    severity, confidence = _LABEL_STRENGTH["pure_ad"]
    return Label(
        category="pure_ad",
        severity=severity,
        confidence=confidence,
        evidences=(f"contact:{contact}", "promise:strong"),
    )


def _weak_labels(content: str) -> list[Label]:
    """弱标签只看正文（标题里的'单日涨粉'是新闻，不是求关注）。"""
    if not content:
        return []
    labels: list[Label] = []
    if _LABEL_SOFT_EXEMPT.search(content):
        return labels

    long_form = len(content) > 800 or bool(_PODCAST_RE.search(content))
    if not long_form:
        bait = _BAIT_RE.search(content)
        if bait:
            severity, confidence = _LABEL_STRENGTH["engagement_bait"]
            labels.append(
                Label(
                    category="engagement_bait",
                    severity=severity,
                    confidence=confidence,
                    evidences=(f"bait_term:{bait.group(0)}",),
                )
            )

    # 低质情绪要**三个条件同时成立**才标：情绪词 + 感叹号爆发 + 短帖。
    # 只按"感叹号密度"会在真实语料上标出一堆正常科技标题（实测 39 篇，
    # 其中 18 篇是 bestblogs/qbitai 的一行摘要），比不标更糟。
    bangs = len(re.findall(r"[!！]", content))
    if len(content) < 300 and bangs >= 3 and _LABEL_RANT_RE.search(content):
        severity, confidence = _LABEL_STRENGTH["low_value_rant"]
        labels.append(
            Label(
                category="low_value_rant",
                severity=severity,
                confidence=confidence,
                evidences=(f"rant_term:{_LABEL_RANT_RE.search(content).group(0)}",),
            )
        )
    return labels


def evaluate_labels(*, title: str = "", content: str = "") -> tuple[Label, ...]:
    """全语料内容分级 —— 纯函数，无 DB / 无网络 / 无随机。

    Args:
        title: 标题（只参与强信号；涨粉与情绪类只看正文）
        content: 清洗后的正文

    Returns:
        0..N 条标签；顺序稳定（强标签在前，弱标签按类别固定顺序）。
    """
    text = f"{title or ''}\n{content or ''}"
    labels: list[Label] = []
    strong = _strong_label(text)
    if strong is not None:
        labels.append(strong)
    labels.extend(_weak_labels(content or ""))
    return tuple(labels)
