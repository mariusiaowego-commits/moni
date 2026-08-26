"""moni.cases.normalize — 案例字段归一化。

v1 normalize 规则:
1. 金融机构别名 TOP 100+ (扩 F5 BANK_ALIAS_MAP 47 条):
   - 银行 (含理财子 31 家)
   - 券商
   - 保险
   - AMC + 城商行
   - 监管机构
2. case_action 关键词映射 (5 类):
   - 上线/推出/发布/上线 → launch
   - 试点/试运行/小范围 → pilot
   - 规模化/全面推广/大规模 → scale
   - 转型/重塑/升级 → transform
   - 退出/下线/终止 → exit
3. case_type 关键词映射 (2 类):
   - AI/人工智能/大模型/数字化 → AI
   - 产品/服务/新业务/创新 → PI (Product Innovation)
4. 中文日期归一 (复用 F5 normalize_event)
5. amount 归一 (提取数字 + 单位, 标准化 "X 亿元" 格式)
6. counterparty 主体别名归一
7. reference_value 高/中/低 (默认 medium)
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

CST = ZoneInfo("Asia/Shanghai")

# ── 金融机构别名 TOP 100+ (按 5 档分组) ──────────────────────────────────
# key = 常见简称/别名, value = 规范全称
BANK_ALIAS_MAP: dict[str, str] = {
    # ── 银行 (国有大行 + 股份制 + 城商行 + 农商行 + 民营) ──
    "招行": "招商银行",
    "招行银行": "招商银行",
    "工行": "工商银行",
    "工商银行": "工商银行",
    "建行": "建设银行",
    "建设银行": "建设银行",
    "农行": "农业银行",
    "中行": "中国银行",
    "交行": "交通银行",
    "邮储": "邮储银行",
    "邮储银行": "邮储银行",
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
    "杭州银行": "杭州银行",
    "长沙银行": "长沙银行",
    "成都银行": "成都银行",
    "重庆银行": "重庆银行",
    "贵阳银行": "贵阳银行",
    "青岛银行": "青岛银行",
    "齐鲁银行": "齐鲁银行",
    "苏州银行": "苏州银行",
    "微众": "微众银行",
    "微众银行": "微众银行",
    "网商": "网商银行",
    "网商银行": "网商银行",
    "新网": "新网银行",
    "新网银行": "新网银行",
    "众邦": "众邦银行",
    "众邦银行": "众邦银行",
    "百信": "百信银行",
    "百信银行": "百信银行",
    "裕民": "裕民银行",
    "裕民银行": "裕民银行",
    "民商": "民商银行",
    "民商银行": "民商银行",
    # ── 理财子公司 (31 家) ──
    "招银理财": "招银理财",
    "兴银理财": "兴银理财",
    "信银理财": "信银理财",
    "平安理财": "平安理财",
    "光大理财": "光大理财",
    "华夏理财": "华夏理财",
    "民生理财": "民生理财",
    "浦银理财": "浦银理财",
    "中银理财": "中银理财",
    "工银理财": "工银理财",
    "农银理财": "农银理财",
    "建信理财": "建信理财",
    "交银理财": "交银理财",
    "邮储理财": "邮储理财",
    "中邮理财": "中邮理财",
    "宁银理财": "宁银理财",
    "杭银理财": "杭银理财",
    "苏银理财": "苏银理财",
    "南银理财": "南银理财",
    "青银理财": "青银理财",
    "渝农商理财": "渝农商理财",
    "广银理财": "广银理财",
    "贝莱德建信理财": "贝莱德建信理财",
    "汇华理财": "汇华理财",
    "施罗德交银理财": "施罗德交银理财",
    "法巴农银理财": "法巴农银理财",
    "高盛工银理财": "高盛工银理财",
    "摩根资管": "摩根资管",
    "东方汇理": "东方汇理",
    "富达": "富达",
    "路博迈": "路博迈",
    # ── 券商 ──
    "华泰证券": "华泰证券",
    "中信证券": "中信证券",
    "国泰君安": "国泰君安",
    "海通证券": "海通证券",
    "招商证券": "招商证券",
    "广发证券": "广发证券",
    "申万宏源": "申万宏源",
    "银河证券": "银河证券",
    "中信建投": "中信建投",
    "国信证券": "国信证券",
    "中金": "中金公司",
    "中金公司": "中金公司",
    "华泰": "华泰证券",
    "国君": "国泰君安",
    "海通": "海通证券",
    # ── 保险 ──
    "平安保险": "中国平安",
    "平安": "中国平安",
    "中国人寿": "中国人寿",
    "国寿": "中国人寿",
    "太平洋保险": "中国太保",
    "太保": "中国太保",
    "新华保险": "新华保险",
    "泰康": "泰康保险",
    "人保": "中国人民保险",
    "PICC": "中国人民保险",
    "众安": "众安保险",
    "众安保险": "众安保险",
    "中国平安": "中国平安",
    # ── AMC + 城商行 ──
    "华融": "中国华融",
    "中国华融": "中国华融",
    "长城": "中国长城资产",
    "东方资产": "中国东方资产",
    "信达": "中国信达",
    # ── 监管机构 ──
    "央行": "中国人民银行",
    "人民银行": "中国人民银行",
    "人行": "中国人民银行",
    "PBOC": "中国人民银行",
    "金监总局": "国家金融监督管理总局",
    "金融监管总局": "国家金融监督管理总局",
    "NFRA": "国家金融监督管理总局",
    "银保监会": "国家金融监督管理总局",
    "证监会": "中国证监会",
    "CSRC": "中国证监会",
    "交易所": "交易所",
    "上交所": "上海证券交易所",
    "深交所": "深圳证券交易所",
    "北交所": "北京证券交易所",
    # ── 科技公司 (F5 没有, F6 案例需要) ──
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
    "华为": "华为",
    "阿里": "阿里巴巴",
    "阿里巴巴": "阿里巴巴",
    "阿里云": "阿里云",
    "腾讯云": "腾讯云",
    "华为云": "华为云",
    "百度云": "百度云",
    "京东云": "京东云",
}

# ── case_action 关键词映射 (5 类) ─────────────────────────────────────────
CASE_ACTION_KEYWORDS: dict[str, list[str]] = {
    "launch": ["上线", "推出", "发布", "落地", "启用", "正式", "投入运营", "推出新"],
    "pilot": ["试点", "试运行", "试运营", "内测", "小范围", "局部", "测试期", "试水"],
    "scale": ["规模化", "全面推广", "大规模", "全覆盖", "全行推广", "扩大", "扩展到", "推广到"],
    "transform": ["转型", "重塑", "升级", "重构", "改造", "数字化转型", "智能化升级"],
    "exit": ["退出", "下线", "终止", "关停", "停运", "撤出", "结束"],
}

# ── case_type 关键词映射 (2 类) ──────────────────────────────────────────
CASE_TYPE_KEYWORDS: dict[str, list[str]] = {
    "AI": ["AI", "人工智能", "大模型", "数字化", "智能", "机器学习", "深度学习",
           "LLM", "GPT", "AI 大模型", "智能体", "Agent", "大语言模型"],
    "PI": ["产品创新", "新业务", "新产品", "新服务", "金融产品", "理财产品",
           "信贷产品", "信用卡", "数字钱包", "新模式"],
}

# ── 中文日期归一 (复用 F5 模式) ─────────────────────────────────────────
DATE_PATTERNS = [
    # 2026年8月7日 / 2026年8月 / 2026-08-07 / 2026/08/07
    (r"(\d{4})[年\-/](\d{1,2})[月\-/](\d{1,2})[日]?", "exact"),
    # 2026年8月
    (r"(\d{4})[年\-/](\d{1,2})[月\-/]?", "month"),
    # 2026年第一季度 / Q1
    (r"(\d{4})年(第[一二三四]季度|[Qq][1234])", "quarter"),
    # 本季度 / 近期 / 近日
    (r"(本季度|近期|近日|最近|本月初)", "relative"),
]


def normalize_date(raw_date: str | None) -> tuple[str | None, str]:
    """归一中文日期, 返回 (date, precision)."""
    if not raw_date:
        return None, "unknown"

    raw_date = raw_date.strip()
    if not raw_date:
        return None, "unknown"

    # ISO 格式直接识别
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw_date):
        return raw_date, "exact"
    if re.match(r"^\d{4}-\d{2}$", raw_date):
        return raw_date, "month"

    for pattern, precision in DATE_PATTERNS:
        m = re.search(pattern, raw_date)
        if m:
            if precision == "exact":
                y, mo, d = m.groups()
                return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}", "exact"
            elif precision == "month":
                y, mo = m.groups()[:2]
                return f"{int(y):04d}-{int(mo):02d}", "month"
            elif precision == "quarter":
                return raw_date, "quarter"
            elif precision == "relative":
                return raw_date, "relative"

    return raw_date, "unknown"


def normalize_subject(raw_subject: str | None) -> str | None:
    """主体别名归一 (TOP 100+).  返回 None  主体无效.

    v1.0.35.19 修: 关键词 substring 替换会越界, e.g. "意大利人民银行" 里
    "人民银行" 被 alias 替换成 "中国人民银行" → "意大利中国人民银行".
    新规则: alias 须出现在 subject 开头, 或前面是非中文+非字母数字 (即
    alias 边界是行首/标点/数字, 不在中文串内). 避开"土耳其X"/"意大利X".
    """
    if not raw_subject:
        return None

    raw_subject = raw_subject.strip()
    if not raw_subject:
        return None

    # 先看别名映射 (精确匹配)
    if raw_subject in BANK_ALIAS_MAP:
        return BANK_ALIAS_MAP[raw_subject]

    # 关键词匹配:
    # - alias 在 subject 开头 → 允许后接任何字符 ("招行某业务部" → "招商银行某业务部")
    # - alias 不在开头 → 必须前后都不是中文 (避 "意大利人民银行" → "意大利中国人民银行")
    for alias, canonical in BANK_ALIAS_MAP.items():
        if alias not in raw_subject or len(alias) < 2:
            continue
        idx = raw_subject.find(alias)
        end = idx + len(alias)
        if idx == 0:
            # 开头别名: 后接任何字符都 OK (业务场景: "招行某业务部" 等)
            return raw_subject[:idx] + canonical + raw_subject[end:]
        # 不在开头: 前缀和后缀都不能是 CJK
        if not _is_cjk(raw_subject[idx - 1]) and (
            end == len(raw_subject) or not _is_cjk(raw_subject[end])
        ):
            return raw_subject[:idx] + canonical + raw_subject[end:]

    return raw_subject


def _is_cjk(ch: str) -> bool:
    """判断字符是否在 CJK 统一表意文字区 (避免 substring alias 越界)."""
    if not ch:
        return False
    return "\u4e00" <= ch <= "\u9fff"


def normalize_case_action(raw_action: str | None) -> str:
    """case_action 关键词映射 (5 类枚举).  默认 'launch'."""
    if not raw_action:
        return "launch"

    raw_action = raw_action.strip()
    for action, keywords in CASE_ACTION_KEYWORDS.items():
        for kw in keywords:
            if kw in raw_action:
                return action
    return "launch"


def normalize_case_type(raw_type: str | None, subject: str | None = None) -> str:
    """case_type 关键词映射 (2 类枚举: AI / PI).  默认 'AI'."""
    text = (raw_type or "") + " " + (subject or "")
    text = text.strip()
    if not text:
        return "AI"

    # AI 优先 (LLM/数字化 是当前最热的, 且 moni 主战场)
    for kw in CASE_TYPE_KEYWORDS["AI"]:
        if kw in text:
            return "AI"
    # 否则 PI
    for kw in CASE_TYPE_KEYWORDS["PI"]:
        if kw in text:
            return "PI"
    return "AI"


def normalize_amount(raw_amount: str | None) -> str | None:
    """金额归一: 提取数字 + 单位, 输出 "X 亿元" / "X 万户" 格式.

    原始: "融资 5 亿元" / "1000 万用户" / "投入 30 亿元" / "500 万户"
    输出: "5 亿元" / "1000 万用户" / "30 亿元" / "500 万户"

    顺序: 先匹配 X<数>户 (复合单位), 再匹配 X<万/亿> (会附加 元),
    最后匹配纯数字。
    """
    if not raw_amount:
        return None

    raw_amount = raw_amount.strip()
    if not raw_amount:
        return None

    def _fmt(num: str, unit: str) -> str:
        num_clean = num.rstrip(".0") if num.endswith(".0") else num
        return f"{num_clean} {unit}"

    # 1. 复合单位 (优先 — 避免被 "万" 截胡)
    #    "1000 用户" / "500 万户" / "300 家" / "2000 笔" / "100 个"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(万户|千家|万笔|万个|万用户|用户|家|笔|个|户)", raw_amount)
    if m:
        num, unit = m.groups()
        # 万户/千家/万笔/万个/万用户 — 单位已是复合, 直接输出
        return _fmt(num, unit)

    # 2. 金额单位 (X 亿元 / X 万元 / X 百万元)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(亿元|万元|百万元|千万元|亿|万|百万|千)", raw_amount)
    if m:
        num, unit = m.groups()
        unit_map = {"亿": "亿元", "万": "万元", "百万": "百万元", "千": "千万元"}
        unit = unit_map.get(unit, unit)
        return _fmt(num, unit)

    # 3. 没匹配上 — 原样返回 (保持语义)
    return raw_amount


def normalize_reference_value(raw_value: str | None) -> str:
    """reference_value 高/中/低 映射.  默认 medium."""
    if not raw_value:
        return "medium"
    raw = raw_value.strip().lower()
    if "高" in raw or "high" in raw or "重要" in raw:
        return "high"
    if "低" in raw or "low" in raw or "次要" in raw:
        return "low"
    return "medium"


def normalize_case(raw: dict[str, Any]) -> dict[str, Any] | None:
    """归一化一条案例原始数据.

    Args:
        raw: LLM 返回的原始 dict (含 subject / case_type / case_action / amount / counterparty / case_date / reference_value 等)

    Returns:
        归一化后的 dict, 或 None (主体无效丢弃)
    """
    subject = normalize_subject(raw.get("subject"))
    if not subject:
        return None  # 主体为空, 丢弃

    case_date, precision = normalize_date(raw.get("case_date"))
    case_type = normalize_case_type(raw.get("case_type"), subject)
    case_action = normalize_case_action(raw.get("case_action"))
    amount = normalize_amount(raw.get("amount"))
    counterparty = normalize_subject(raw.get("counterparty"))  # 复用主体别名归一
    reference_value = normalize_reference_value(raw.get("reference_value"))

    return {
        "subject": subject,
        "subject_raw": raw.get("subject_raw") or raw.get("subject"),
        "case_date": case_date,
        "case_date_precision": precision,
        "case_type": case_type,
        "case_action": case_action,
        "counterparty": counterparty,
        "amount": amount,
        "reference_value": reference_value,
        "reference_rationale": (raw.get("reference_rationale") or "")[:200],  # 截断
        "summary": (raw.get("summary") or "")[:200],
        "evidence_snippet": (raw.get("evidence_snippet") or "")[:400],
        "extraction_confidence": int(raw.get("extraction_confidence") or 70),
    }