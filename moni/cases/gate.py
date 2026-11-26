"""案例质量闸：抽取前规则闸（0 token）+ 抽取后字段校验.

设计原则：宁可漏放，不要误杀。短而有价值的公告（银行/支付机构的上线、
试点、收购）在存量 432 条真案例里占一定比例，闸门必须靠「业务信号」
放行，而不是只看长度。

前置闸挡掉的条目只写 case_extract_runs(status='skipped_gate')，
不写 cases 占位行，也不再送 LLM。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

MIN_CONFIDENCE = 60
MIN_SUMMARY_CHARS = 12
MIN_KEYWORDS = 2

# 只有「纯营销/纯栏目」才无条件拦；招聘、早晚报、直播、行情里经常藏着
# 真实业务动作（如「汇丰设立 AI 中心并招聘 100 人」），必须先看业务信号。
_HARD_NOISE_TITLE = ("福利", "抽奖")
_SOFT_NOISE_TITLE = ("招聘", "直播", "早报", "晚报", "行情", "快讯", "周报", "盘点", "活动")

_AMOUNT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:亿|万|千|百万|十亿|%|％)")

_ORG_WORDS = (
    "银行", "农商行", "城商行", "券商", "证券", "保险", "支付", "金融",
    "央行", "监管", "证监会", "银保监", "大模型", "智能体", "数字人民币",
    "消费金融", "理财", "基金", "信托", "AMC",
)
_ACTION_WORDS = (
    "上线", "发布", "推出", "试点", "中标", "收购", "并购", "融资",
    "合作", "关停", "成立", "落地", "投产", "接入", "挂牌", "开业",
    "获批", "签署", "部署", "设立", "组建", "启用", "开设", "投用", "扩建",
)

# 中文短讯 180 字已能承载「谁 + 做了什么」；再短才需要业务信号兜底。
_DEFAULT_MIN_CHARS = 180
_MIN_CHARS_BY_SOURCE = {"bank_feed": 180, "mpaypass": 180}


def pre_gate(
    article: Mapping[str, Any],
    alias_map: Mapping[str, Any] | None = None,
) -> str | None:
    """返回拦截原因；None 表示放行."""
    title = str(article.get("title") or "")
    content = str(article.get("raw_content") or "")

    for word in _HARD_NOISE_TITLE:
        if word in title:
            return f"标题硬噪声：{word}"

    has_signal = _has_business_signal(title, content, alias_map or {})

    for word in _SOFT_NOISE_TITLE:
        if word in title and not has_signal:
            return f"标题软噪声且无业务信号：{word}"

    source = str(article.get("source") or "")
    min_chars = _MIN_CHARS_BY_SOURCE.get(source, _DEFAULT_MIN_CHARS)
    if len(content) < min_chars and not has_signal:
        return f"正文过短且无业务信号：{len(content)}<{min_chars}"
    return None


def validate_case(case: Mapping[str, Any]) -> str | None:
    """抽取后单条校验；返回丢弃原因，None 表示保留."""
    subject = str(case.get("subject") or "").strip()
    if not subject or subject == "__processing__":
        return "subject 为空"
    if len(str(case.get("summary") or "").strip()) < MIN_SUMMARY_CHARS:
        return "summary 过短"
    try:
        confidence = int(case.get("extraction_confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    if confidence < MIN_CONFIDENCE:
        return "confidence 过低"
    keywords = [k for k in (case.get("keywords") or []) if str(k).strip()]
    if len(keywords) < MIN_KEYWORDS:
        return "keywords 不足"
    return None


def _has_business_signal(
    title: str,
    content: str,
    alias_map: Mapping[str, Any],
) -> bool:
    text = title + "\n" + content
    if _AMOUNT_RE.search(text):
        return True
    if any(word in text for word in _ORG_WORDS) and any(
        word in text for word in _ACTION_WORDS
    ):
        return True
    if alias_map:
        lowered = text.lower()
        for alias in alias_map:
            token = str(alias).lower()
            if not token:
                continue
            # 纯 ASCII 别名加词边界，避免 meta 命中 metadata、swift 命中 swiftly。
            if token.isascii():
                if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", lowered):
                    return True
            elif token in lowered:
                return True
    return False
