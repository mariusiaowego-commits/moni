"""Business-value rubric v1: ten dimensions, weights, anchors, house style.

This module is the single source of truth for the anchors.  The design doc
mirrors it; if they disagree, this file wins.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

RUBRIC_ID = "bv_rubric_v1"
MIN_BODY_CHARS = 200
MAX_EVIDENCE = 8
MIN_QUOTE_CHARS = 12
MAX_DIM_SCORE = 10
NO_EVIDENCE_CAP = 3
HIGH_SCORE = 7
NOTE_MAX_CHARS = 50

# Shared by the reason check and the research validator.
BANNED_SUBSTRINGS = (
    "值得关注",
    "赋能",
    "闭环",
    "抓手",
    "底层逻辑",
    "深刻",
    "凸显",
)
BANNED_PATTERN = re.compile(r"(不是|并非|不只是|不仅是).{1,20}(而是|更是)")
WRITING_BANNED = ("深度", "可读", "文笔", "观点", "洞察")


@dataclass(frozen=True)
class Dimension:
    key: str
    label: str
    weight: int
    question: str
    anchors: tuple[str, str, str, str]
    template: str


def _d(
    key: str,
    label: str,
    weight: int,
    question: str,
    anchors: tuple[str, str, str, str],
    template: str,
) -> Dimension:
    return Dimension(key, label, weight, question, anchors, template)


DIMENSIONS: tuple[Dimension, ...] = (
    _d(
        "rule_delta",
        "规则约束",
        14,
        "有没有会改变银行或支付机构义务的规则、处罚或窗口指导？点评和后果不算",
        (
            "没有监管机构、规章、处罚或窗口指导。",
            "只有「监管趋严」这类话，没有文件名、机构名或条款。",
            "点名了文件或机构，但只转述大意，没有条款号或生效日。",
            "有文件全称、文号或条款、生效日期，或罚单写明机构、金额和违规事由。",
        ),
        "{机构}发布{文件}，{日期}起执行，要求{义务}",
    ),
    _d(
        "named_peer_move",
        "同业动作",
        12,
        "机构、动作、时间是否同时点得出？缺一件就低。不评产品细节",
        (
            "没有具体银行或支付机构名。",
            "只有「多家银行」「头部机构」。",
            "有机构和动作，没有日期，或只有「近期」「今年以来」。",
            "机构名 + 动作（上线、下线、调价、组建、合作）+ 日期。",
        ),
        "{机构}于{日期}{动作}{对象}",
    ),
    _d(
        "portable_product",
        "产品规则",
        10,
        "有没有可照搬的产品形态或业务规则（对象、条件、额度、流程）？技术栈不算",
        (
            "没有具体产品或业务规则。",
            "只有产品名和宣传语。",
            "有对象和形态，条件、额度、流程缺了两项以上。",
            "对象、准入条件、额度或费率、办理流程都写到。",
        ),
        "{机构}{产品}面向{对象}，额度{数字}，条件{要点}",
    ),
    _d(
        "portable_system",
        "系统实现",
        8,
        "有没有可对照的实现、架构、接口或控制点？只有名词就低。产品口号不算",
        (
            "没有技术实现。",
            "只有「AI」「大模型」「中台」等名词。",
            "写了落在哪个环节，没有架构、接口或控制点。",
            "有架构或模块、关键接口或风控控制点，并写明环节。",
        ),
        "{机构}在{环节}采用{架构或组件}，控制点为{要点}",
    ),
    _d(
        "customer_lever",
        "客户杠杆",
        10,
        "获客、留存、转化、财富或对公，是否写清对象和动作？通道本身不算",
        (
            "不涉及客户或经营动作。",
            "只有「以客户为中心」。",
            "对象或动作只写清一项。",
            "客群和动作都写清（例如代发客户 + 权益或交叉销售路径）。",
        ),
        "{机构}针对{客群}做{动作}，用于{获客或留存}",
    ),
    _d(
        "money_path",
        "资金路径",
        10,
        "支付、账户、钱包、清算或跨境的资金或信息路径是否写清？经营话术不算",
        (
            "不涉及支付、账户或清算。",
            "只有「支付竞争激烈」这类话。",
            "点了通道或账户类型，没写从哪到哪。",
            "写清从哪一方经过哪些节点到哪一方。",
        ),
        "资金经{节点1}到{节点2}，走{通道或清算方式}",
    ),
    _d(
        "downside",
        "下行损失",
        10,
        "做错或不做的合规、声誉或操作损失是否写清？仅仅「有一条新规」不算",
        (
            "没有损失或后果。",
            "只说有新规或「存在合规风险」。",
            "有损失类型（罚款、通报、暂停业务、客诉），没有金额、主体或范围。",
            "罚没金额、停业或暂停新增、公开通报、受影响客户数，至少一项有数字或主体。",
        ),
        "{机构}因{事由}被{处罚}，损失{金额或范围}",
    ),
    _d(
        "applicability",
        "适用对象",
        8,
        "适用的机构类型或客户类型是否写清？不打「离我方近不近」",
        (
            "看不出哪类机构或客户。",
            "只有「金融机构」「广大用户」。",
            "只到「商业银行」「支付机构」这一层。",
            "细到城商行、农商行、非银行支付机构储值账户、跨境电商卖家这类。",
        ),
        "适用于{机构类型}，覆盖{客户类型}",
    ),
    _d(
        "decision_window",
        "决策窗口",
        8,
        "截止日期或尚未落地的新动作是否还赶得上？不奖「文章写得新」",
        (
            "一年前已结束，或没有任何时间。",
            "已落地数月，只剩复盘，没有下一个节点。",
            "仍在推进，但没写下一个节点的日期。",
            "有征求意见截止、生效日或上线日，且距今不足 90 天，日期可解析。",
        ),
        "{事件}将于{日期}{节点}，距今{n}天",
    ),
    _d(
        "adoption_cost",
        "落地门槛",
        10,
        "牌照、系统、预算、周期是否写清？施行日期、截止日期、罚款不算这一维",
        (
            "牌照或资质、要改的系统、预算金额、实施周期，四类都没有。",
            "只有「需改造」「成本不低」「周期较长」，没有专名、金额或时长。",
            "四类里恰好一类写清，且带专名、金额或时长。时长单独出现就算这一类。",
            "四类里至少两类同时出现，每一类都带专名、金额或时长。",
        ),
        "需{牌照或系统}，投入约{数字}，周期{时长}",
    ),
)

BY_KEY: dict[str, Dimension] = {d.key: d for d in DIMENSIONS}
KEYS: tuple[str, ...] = tuple(d.key for d in DIMENSIONS)
WEIGHT_TOTAL = sum(d.weight for d in DIMENSIONS)


@dataclass(frozen=True)
class Rubric:
    """One complete question set.  V2 direction research produces another."""

    id: str
    dimensions: tuple[Dimension, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(d.key for d in self.dimensions)

    @property
    def by_key(self) -> dict[str, Dimension]:
        return {d.key: d for d in self.dimensions}

    def prompt_block(self) -> str:
        lines = []
        for i, dim in enumerate(self.dimensions, 1):
            a0, a3, a6, a10 = dim.anchors
            lines.append(
                f"{i}. {dim.key}「{dim.label}」权重 {dim.weight}\n"
                f"   问题：{dim.question}\n"
                f"   0 分：{a0}\n"
                f"   3 分：{a3}\n"
                f"   6 分：{a6}\n"
                f"   10 分：{a10}\n"
                f"   理由模板：{dim.template}"
            )
        return "\n".join(lines)


RUBRIC_V1 = Rubric(RUBRIC_ID, DIMENSIONS)


def banned_hits(text: str) -> list[str]:
    """Return the banned markers found in a reason string."""
    hits = [word for word in BANNED_SUBSTRINGS if word in text]
    if BANNED_PATTERN.search(text):
        hits.append("不是X而是Y")
    return hits


def writing_hits(text: str) -> list[str]:
    return [word for word in WRITING_BANNED if word in text]


def rubric_prompt_block() -> str:
    """Render the rubric for the scoring prompt."""
    return RUBRIC_V1.prompt_block()
