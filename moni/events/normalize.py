"""moni.events.normalize — 事件字段归一化。

v1 normalize 规则：
1. 银行别名 TOP 20 映射（招行→招商银行 等）
2. 中文日期归一：
   - "8月7日" → 当年-08-07 + exact
   - "2026年8月" → 2026-08 + month
   - "2026年8月7日" → 2026-08-07 + exact
   - "2026年第一季度/Q1" → 2026-Q1 + quarter
   - "本季度/近期/近日" → relative
   - 空 → unknown
3. event_type 映射：监管/新规→regulation，上线/推出/发布→product，
   融资/投资→financing，技术/AI/数字化→tech，其他→other
4. subject 为空 → 整条丢弃（返回 None）
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

CST = ZoneInfo("Asia/Shanghai")

# ── 银行/金融机构别名 TOP 20 ──────────────────────────────────────────
#  key = 常见简称/别名，value = 规范全称
BANK_ALIAS_MAP: dict[str, str] = {
    "招行": "招商银行",
    "招行银行": "招商银行",
    "工行": "工商银行",
    "工商银行": "工商银行",
    "建行": "建设银行",
    "农行": "农业银行",
    "中行": "中国银行",
    "交行": "交通银行",
    "邮储": "邮储银行",
    "邮储银行": "邮储银行",
    "招行": "招商银行",
    "兴业": "兴业银行",
    "浦发": "浦发银行",
    "中信": "中信银行",
    "光大": "光大银行",
    "民生": "民生银行",
    "平安银行": "平安银行",
    "平安": "平安银行",
    "华夏": "华夏银行",
    "北京银行": "北京银行",
    "上海银行": "上海银行",
    "江苏银行": "江苏银行",
    "宁波银行": "宁波银行",
    "南京银行": "南京银行",
    "微众": "微众银行",
    "微众银行": "微众银行",
    "网商": "网商银行",
    "网商银行": "网商银行",
    "支付宝": "支付宝",
    "蚂蚁": "蚂蚁集团",
    "蚂蚁集团": "蚂蚁集团",
    "腾讯": "腾讯",
    "腾讯金融": "腾讯金融",
    "微信支付": "微信支付",
    "京东": "京东",
    "京东数科": "京东数科",
    "京东科技": "京东科技",
    "度小满": "度小满",
    "百度": "百度",
    "字节": "字节跳动",
    "字节跳动": "字节跳动",
    "美团": "美团",
    "美团金融": "美团金融",
    "360数科": "360数科",
    "奇富": "奇富科技",
    "奇富科技": "奇富科技",
    "乐信": "乐信",
    "陆金所": "陆金所",
    "平安壹账通": "金融壹账通",
    "金融壹账通": "金融壹账通",
}

# ── 事件类型映射 ──────────────────────────────────────────────────────

_TYPE_KEYWORDS: dict[str, list[str]] = {
    "regulation": [
        "监管", "新规", "政策", "罚单", "处罚", "合规", "央行", "银保监",
        "金监总局", "证监会", "管理办法", "指导意见", "通知", "规定",
        "整治", "规范", "约谈", "处罚决定书",
    ],
    "product": [
        "上线", "推出", "发布", "新品", "新产品", "功能", "版本", "升级",
        "App", "APP", "app", "落地", "首发", "开闸",
    ],
    "financing": [
        "融资", "投资", "并购", "收购", "上市", "IPO", "募资", "增发",
        "入股", "领投", "跟投", "Pre-", "pre-", "轮", "战略投资",
        "股权转让", "增持", "减持",
    ],
    "tech": [
        "AI", "ai", "人工智能", "大模型", "技术", "数字化", "转型",
        "研发", "创新", "智能", "算法", "数据", "云", "区块链",
        "隐私计算", "风控",
    ],
}

_TYPE_EN_TO_ZH: dict[str, str] = {
    "regulation": "监管",
    "product": "产品",
    "financing": "融资",
    "tech": "技术",
    "other": "其他",
}


def _normalize_subject(raw: str | None) -> str | None:
    """主体别名归一化。

    Args:
        raw: 原始主体字符串

    Returns:
        归一化后的主体名称，空字符串或 None 返回 None（调用方应丢弃整条事件）
    """
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    # 先做精确匹配
    if s in BANK_ALIAS_MAP:
        return BANK_ALIAS_MAP[s]
    # 去掉常见后缀再试（如"招商银行股份有限公司"→"招商银行"）
    for suffix in ("股份有限公司", "有限公司", "集团", "银行", "科技"):
        if s.endswith(suffix) and s[: -len(suffix)] in BANK_ALIAS_MAP:
            return BANK_ALIAS_MAP[s[: -len(suffix)]]
    return s


def _normalize_date(raw: str | None) -> tuple[str | None, str]:
    """中文日期归一化为 ISO 格式 + 精度标签。

    Returns:
        (event_date, precision)
        precision 取值：exact / month / quarter / relative / unknown
    """
    if not raw or not raw.strip():
        return None, "unknown"

    s = raw.strip()
    now = datetime.now(CST)
    current_year = now.year

    # ── 模糊/相对日期 ──
    relative_keywords = [
        "近期", "近日", "最近", "本季度", "近期内", "前不久",
        "日前", "早些时候", "不久前",
    ]
    for kw in relative_keywords:
        if kw in s:
            return "relative", "relative"

    # ── 季度 ──
    # 2026年第一季度 / 2026 Q1 / Q1 2026 / 第一季度
    q_match = re.search(r"(20\d{2}).*?第([一二三四1-4])季度", s)
    if q_match:
        year = int(q_match.group(1))
        q_char = q_match.group(2)
        q_num = {"一": 1, "二": 2, "三": 3, "四": 4,
                 "1": 1, "2": 2, "3": 3, "4": 4}[q_char]
        return f"{year}-Q{q_num}", "quarter"
    q_match2 = re.search(r"Q([1-4])\s*(20\d{2})?", s, re.IGNORECASE)
    if q_match2:
        q_num = int(q_match2.group(1))
        year_str = q_match2.group(2)
        year = int(year_str) if year_str else current_year
        return f"{year}-Q{q_num}", "quarter"

    # ── 完整日期：2026年8月7日 / 2026-08-07 / 2026/8/7 ──
    full_match = re.search(r"(20\d{2})[年\-/](\d{1,2})[月\-/](\d{1,2})日?", s)
    if full_match:
        year = int(full_match.group(1))
        month = int(full_match.group(2))
        day = int(full_match.group(3))
        try:
            dt = datetime(year, month, day)
            return dt.strftime("%Y-%m-%d"), "exact"
        except ValueError:
            pass  # 非法日期，继续往下

    # ── 只有月份：2026年8月 / 2026-08 ──
    month_match = re.search(r"(20\d{2})[年\-/](\d{1,2})月?", s)
    if month_match:
        year = int(month_match.group(1))
        month = int(month_match.group(2))
        if 1 <= month <= 12:
            return f"{year}-{month:02d}", "month"

    # ── 只有月日：8月7日（当年） ──
    md_match = re.search(r"(\d{1,2})月(\d{1,2})日", s)
    if md_match:
        month = int(md_match.group(1))
        day = int(md_match.group(2))
        try:
            dt = datetime(current_year, month, day)
            # 如果解析出的日期在当前时间之后（未来日期），用去年
            if dt > now:
                dt = datetime(current_year - 1, month, day)
            return dt.strftime("%Y-%m-%d"), "exact"
        except ValueError:
            pass

    # ── 只有年份：2026年 ──
    year_match = re.search(r"(20\d{2})年", s)
    if year_match:
        return f"{year_match.group(1)}-01", "month"

    # 无法解析
    return None, "unknown"


def _normalize_event_type(raw: str | None, summary: str | None = None) -> str:
    """事件类型归一化。

    优先信任 LLM 直接返回的 event_type 字段（如果已经是英文枚举值）；
    否则根据 raw + summary 中的关键词做映射。
    """
    if raw:
        s = raw.strip().lower()
        if s in ("regulation", "product", "financing", "tech", "other"):
            return s
        # 中文枚举值
        zh_map = {
            "监管": "regulation", "政策": "regulation", "新规": "regulation",
            "产品": "product", "上线": "product", "发布": "product",
            "融资": "financing", "投资": "financing", "并购": "financing",
            "技术": "tech", "科技": "tech", "AI": "tech", "ai": "tech",
            "其他": "other",
        }
        if s in zh_map:
            return zh_map[s]

    # 关键词匹配（用 summary + raw）
    text = f"{raw or ''} {summary or ''}"
    best_type = "other"
    best_count = 0
    for type_name, keywords in _TYPE_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in text)
        if count > best_count:
            best_count = count
            best_type = type_name
    return best_type


def normalize_event(raw_event: dict[str, Any]) -> dict[str, Any] | None:
    """归一化单条事件的所有字段。

    阶段 1/3 of pipeline：normalize。

    Args:
        raw_event: LLM 返回的原始事件 dict，至少包含 subject / event_type

    Returns:
        归一化后的事件 dict，字段：
        - subject: str（规范名称）
        - subject_raw: str | None（原始主体）
        - event_date: str | None
        - event_date_precision: str
        - event_type: str（枚举值）
        - summary: str | None
        - evidence_snippet: str | None
        - extraction_confidence: int | None

        如果 subject 为空 → 返回 None（整条丢弃）
    """
    subject_raw = raw_event.get("subject") or raw_event.get("company") or ""
    subject = _normalize_subject(subject_raw)
    if not subject:
        return None  # 主体为空 → 丢弃

    event_date, precision = _normalize_date(raw_event.get("event_date") or "")
    event_type = _normalize_event_type(
        raw_event.get("event_type") or raw_event.get("type"),
        raw_event.get("summary"),
    )

    return {
        "subject": subject,
        "subject_raw": subject_raw if subject_raw != subject else None,
        "event_date": event_date,
        "event_date_precision": precision,
        "event_type": event_type,
        "summary": raw_event.get("summary") or None,
        "evidence_snippet": raw_event.get("evidence_snippet") or raw_event.get("evidence") or None,
        "extraction_confidence": raw_event.get("extraction_confidence") or raw_event.get("confidence"),
    }
