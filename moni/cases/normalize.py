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
8. keywords 归一 (别名 / 去泛词 / 去重截断 5)
"""

from __future__ import annotations

import json
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


# ── 案例关键词归一 (sprint-18) ─────────────────────────────────────────
KEYWORD_ALIAS_MAP: dict[str, str] = {
    "跨境清算": "跨境支付",
    "跨境结算": "跨境支付",
    "跨境汇款": "跨境支付",
    "数字货币跨境": "跨境支付",
    "数币跨境": "跨境支付",
    "P2P支付": "移动支付",
    "二维码支付": "移动支付",
    "刷脸支付": "生物支付",
    "大语言模型": "大模型应用",
    "生成式AI": "大模型应用",
    "LLM": "大模型应用",
    "AI Agent": "智能体",
    "AI智能体": "智能体",
    "Agentic AI": "智能体",
    "智能投研": "智能投顾",
    "AI投顾": "智能投顾",
    "行为反欺诈": "智能风控",
    "行为生物识别": "智能风控",
    "反洗钱系统": "反洗钱",
    "AML": "反洗钱",
    "KYC认证": "合规KYC",
    "身份核验": "合规KYC",
    "信用卡核心": "核心系统",
    "新一代核心": "核心系统",
    "微服务重构": "系统改造",
    "分布式架构": "系统改造",
}

# 单独出现即不合格的泛词 (prompt 约束 + 写入侧兜底)
_KEYWORD_BANNED = frozenset({
    "银行", "金融", "系统", "创新", "业务", "AI", "ai", "支付",
})

_KEYWORD_SPLIT_RE = re.compile(r"[,，;；、/|]+")
_KEYWORD_LEAD_NUM_RE = re.compile(r"^[\d]+[\.．、\)）:：]\s*")
_KEYWORD_WRAP_RE = re.compile(r"^[\(\[（【「『\"'“‘]+|[\)\]）】」』\"'”’]+$")


def _coerce_keyword_list(raw_keywords: Any) -> list[str]:
    """把 LLM 各种形状的 keywords 收成字符串列表."""
    if raw_keywords is None:
        return []
    if isinstance(raw_keywords, str):
        s = raw_keywords.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except (json.JSONDecodeError, TypeError):
                pass
        return [p for p in _KEYWORD_SPLIT_RE.split(s) if p.strip()]
    if isinstance(raw_keywords, (list, tuple, set)):
        return [str(x) for x in raw_keywords]
    return [str(raw_keywords)]


def _clean_keyword_token(raw: str) -> str:
    """去前缀编号 / 包裹标点 / 引号."""
    s = raw.strip()
    s = _KEYWORD_LEAD_NUM_RE.sub("", s).strip()
    # 反复剥一层包裹标点
    for _ in range(3):
        nxt = _KEYWORD_WRAP_RE.sub("", s).strip()
        if nxt == s:
            break
        s = nxt
    s = s.strip(".,;:，。；、 ")
    return s.strip()


def _alias_keyword(token: str) -> str:
    if token in KEYWORD_ALIAS_MAP:
        return KEYWORD_ALIAS_MAP[token]
    lower = token.lower()
    for alias, canonical in KEYWORD_ALIAS_MAP.items():
        if alias.lower() == lower:
            return canonical
    return token


def normalize_keywords(raw_keywords: Any) -> list[str]:
    """归一化关键词列表.

    - 兼容字符串/数组输入
    - 过滤空串/单字/长度 < 2 (防 'AI'/'银行'/'支付' 误伤)
    - 字符清洗 (去前缀数字编号/标点/英文引号)
    - KEYWORD_ALIAS_MAP 归一
    - 去重, 截断前 5 个
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in _coerce_keyword_list(raw_keywords):
        token = _clean_keyword_token(raw)
        if not token:
            continue
        token = _alias_keyword(token)
        if len(token) < 2:
            continue
        if token in _KEYWORD_BANNED or token.lower() in _KEYWORD_BANNED:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= 5:
            break
    return out


# ── 地理维度 (sprint-19) ──────────────────────────────────────────────
# (ISO-2, 规范中文名, 大区, 次区域)
GEO_ALIAS_MAP: dict[str, tuple[str, str, str, str | None]] = {
    # ── 中国及港澳台 ──
    "中国": ("CN", "中国", "APAC", "东亚"),
    "国内": ("CN", "中国", "APAC", "东亚"),
    "境内": ("CN", "中国", "APAC", "东亚"),
    "China": ("CN", "中国", "APAC", "东亚"),
    "CN": ("CN", "中国", "APAC", "东亚"),
    "PRC": ("CN", "中国", "APAC", "东亚"),
    "中国香港": ("HK", "中国香港", "APAC", "东亚"),
    "香港": ("HK", "中国香港", "APAC", "东亚"),
    "HK": ("HK", "中国香港", "APAC", "东亚"),
    "Hong Kong": ("HK", "中国香港", "APAC", "东亚"),
    "中国澳门": ("MO", "中国澳门", "APAC", "东亚"),
    "澳门": ("MO", "中国澳门", "APAC", "东亚"),
    "Macau": ("MO", "中国澳门", "APAC", "东亚"),
    "中国台湾": ("TW", "中国台湾", "APAC", "东亚"),
    "台湾": ("TW", "中国台湾", "APAC", "东亚"),
    "Taiwan": ("TW", "中国台湾", "APAC", "东亚"),
    # ── 亚太其他 ──
    "新加坡": ("SG", "新加坡", "APAC", "东南亚"),
    "狮城": ("SG", "新加坡", "APAC", "东南亚"),
    "Singapore": ("SG", "新加坡", "APAC", "东南亚"),
    "SG": ("SG", "新加坡", "APAC", "东南亚"),
    "日本": ("JP", "日本", "APAC", "东亚"),
    "Japan": ("JP", "日本", "APAC", "东亚"),
    "JP": ("JP", "日本", "APAC", "东亚"),
    "韩国": ("KR", "韩国", "APAC", "东亚"),
    "Korea": ("KR", "韩国", "APAC", "东亚"),
    "KR": ("KR", "韩国", "APAC", "东亚"),
    "澳大利亚": ("AU", "澳大利亚", "APAC", "大洋洲"),
    "澳洲": ("AU", "澳大利亚", "APAC", "大洋洲"),
    "Australia": ("AU", "澳大利亚", "APAC", "大洋洲"),
    "AU": ("AU", "澳大利亚", "APAC", "大洋洲"),
    "越南": ("VN", "越南", "APAC", "东南亚"),
    "Vietnam": ("VN", "越南", "APAC", "东南亚"),
    "印尼": ("ID", "印度尼西亚", "APAC", "东南亚"),
    "印度尼西亚": ("ID", "印度尼西亚", "APAC", "东南亚"),
    "Indonesia": ("ID", "印度尼西亚", "APAC", "东南亚"),
    "马来西亚": ("MY", "马来西亚", "APAC", "东南亚"),
    "马来": ("MY", "马来西亚", "APAC", "东南亚"),
    "Malaysia": ("MY", "马来西亚", "APAC", "东南亚"),
    "印度": ("IN", "印度", "APAC", "南亚"),
    "India": ("IN", "印度", "APAC", "南亚"),
    "泰国": ("TH", "泰国", "APAC", "东南亚"),
    "Thailand": ("TH", "泰国", "APAC", "东南亚"),
    "菲律宾": ("PH", "菲律宾", "APAC", "东南亚"),
    "东南亚": ("ASEAN", "东南亚", "APAC", "东南亚"),
    "东盟": ("ASEAN", "东南亚", "APAC", "东南亚"),
    "亚太": ("APAC", "亚太", "APAC", None),
    "APAC": ("APAC", "亚太", "APAC", None),
    # ── 北美 ──
    "美国": ("US", "美国", "NAM", "北美"),
    "美利坚": ("US", "美国", "NAM", "北美"),
    "USA": ("US", "美国", "NAM", "北美"),
    "US": ("US", "美国", "NAM", "北美"),
    "America": ("US", "美国", "NAM", "北美"),
    "United States": ("US", "美国", "NAM", "北美"),
    "U.S.": ("US", "美国", "NAM", "北美"),
    "U.S.A.": ("US", "美国", "NAM", "北美"),
    "华尔街": ("US", "美国", "NAM", "北美"),
    "加拿大": ("CA", "加拿大", "NAM", "北美"),
    "Canada": ("CA", "加拿大", "NAM", "北美"),
    "CA": ("CA", "加拿大", "NAM", "北美"),
    "北美": ("NAM", "北美", "NAM", "北美"),
    "NAM": ("NAM", "北美", "NAM", "北美"),
    # ── 欧洲 ──
    "英国": ("GB", "英国", "EMEA", "西欧"),
    "英格兰": ("GB", "英国", "EMEA", "西欧"),
    "UK": ("GB", "英国", "EMEA", "西欧"),
    "GB": ("GB", "英国", "EMEA", "西欧"),
    "United Kingdom": ("GB", "英国", "EMEA", "西欧"),
    "德国": ("DE", "德国", "EMEA", "西欧"),
    "Germany": ("DE", "德国", "EMEA", "西欧"),
    "DE": ("DE", "德国", "EMEA", "西欧"),
    "法国": ("FR", "法国", "EMEA", "西欧"),
    "France": ("FR", "法国", "EMEA", "西欧"),
    "FR": ("FR", "法国", "EMEA", "西欧"),
    "瑞士": ("CH", "瑞士", "EMEA", "中欧"),
    "Switzerland": ("CH", "瑞士", "EMEA", "中欧"),
    "意大利": ("IT", "意大利", "EMEA", "南欧"),
    "Italy": ("IT", "意大利", "EMEA", "南欧"),
    "荷兰": ("NL", "荷兰", "EMEA", "西欧"),
    "西班牙": ("ES", "西班牙", "EMEA", "南欧"),
    "欧洲": ("EU", "欧洲", "EMEA", "欧洲"),
    "欧盟": ("EU", "欧洲", "EMEA", "欧洲"),
    "EU": ("EU", "欧洲", "EMEA", "欧洲"),
    "EMEA": ("EMEA", "欧洲中东非洲", "EMEA", None),
    "西欧": ("EU", "欧洲", "EMEA", "西欧"),
    # ── 中东 ──
    "阿联酋": ("AE", "阿联酋", "EMEA", "中东"),
    "迪拜": ("AE", "阿联酋", "EMEA", "中东"),
    "UAE": ("AE", "阿联酋", "EMEA", "中东"),
    "沙特": ("SA", "沙特阿拉伯", "EMEA", "中东"),
    "沙特阿拉伯": ("SA", "沙特阿拉伯", "EMEA", "中东"),
    "中东": ("ME", "中东", "EMEA", "中东"),
    # ── 拉美 ──
    "巴西": ("BR", "巴西", "LATAM", "南美"),
    "Brazil": ("BR", "巴西", "LATAM", "南美"),
    "墨西哥": ("MX", "墨西哥", "LATAM", "北美"),
    "Mexico": ("MX", "墨西哥", "LATAM", "北美"),
    "阿根廷": ("AR", "阿根廷", "LATAM", "南美"),
    "智利": ("CL", "智利", "LATAM", "南美"),
    "LATAM": ("LATAM", "拉美", "LATAM", None),
    "拉美": ("LATAM", "拉美", "LATAM", None),
    # ── 跨国 / 全球组织 ──
    "全球": ("GLOBAL", "全球", "GLOBAL", None),
    "国际": ("GLOBAL", "全球", "GLOBAL", None),
    "跨国": ("GLOBAL", "全球", "GLOBAL", None),
    "多边": ("GLOBAL", "全球", "GLOBAL", None),
    "多国": ("GLOBAL", "全球", "GLOBAL", None),
    "BIS": ("GLOBAL", "国际清算银行", "GLOBAL", None),
    "国际清算银行": ("GLOBAL", "国际清算银行", "GLOBAL", None),
    "SWIFT": ("GLOBAL", "SWIFT", "GLOBAL", None),
    "mBridge": ("GLOBAL", "多边央行数字货币桥", "GLOBAL", None),
    "IMF": ("GLOBAL", "国际货币基金组织", "GLOBAL", None),
    "世界银行": ("GLOBAL", "世界银行", "GLOBAL", None),
    "GLOBAL": ("GLOBAL", "全球", "GLOBAL", None),
}

_CN = ("CN", "中国", "APAC", "东亚")
_US = ("US", "美国", "NAM", "北美")
_GB = ("GB", "英国", "EMEA", "西欧")
_SG = ("SG", "新加坡", "APAC", "东南亚")
_AU = ("AU", "澳大利亚", "APAC", "大洋洲")
_JP = ("JP", "日本", "APAC", "东亚")
_HK = ("HK", "中国香港", "APAC", "东亚")
_GL = ("GLOBAL", "全球", "GLOBAL", None)

SUBJECT_GEO_DEFAULTS: dict[str, tuple[str, str, str, str | None]] = {}
for _n in (
    "工商银行", "农业银行", "中国银行", "建设银行", "交通银行", "邮储银行",
    "招商银行", "兴业银行", "浦发银行", "中信银行", "光大银行", "民生银行",
    "平安银行", "华夏银行", "浙商银行", "渤海银行", "广发银行", "恒丰银行",
    "北京银行", "上海银行", "江苏银行", "宁波银行", "南京银行", "杭州银行",
    "长沙银行", "成都银行", "重庆银行", "贵阳银行", "青岛银行", "齐鲁银行",
    "苏州银行", "微众银行", "网商银行", "新网银行", "众邦银行", "百信银行",
    "招银理财", "兴银理财", "信银理财", "平安理财", "光大理财", "华夏理财",
    "民生理财", "浦银理财", "中银理财", "工银理财", "农银理财", "建信理财",
    "交银理财", "邮储理财", "中邮理财",
    "华泰证券", "中信证券", "国泰君安", "海通证券", "招商证券", "广发证券",
    "申万宏源", "银河证券", "中信建投", "国信证券", "中金公司",
    "中国平安", "中国人寿", "中国太保", "新华保险", "泰康保险", "中国人民保险",
    "众安保险",
    "蚂蚁集团", "腾讯", "腾讯金融", "微信支付", "京东", "京东数科", "京东科技",
    "度小满", "百度", "字节跳动", "美团", "金融壹账通", "华为", "阿里巴巴", "阿里云",
    "中国人民银行", "国家金融监督管理总局", "中国证监会",
    "上海证券交易所", "深圳证券交易所", "北京证券交易所",
    "中国华融", "中国长城资产", "中国东方资产", "中国信达",
):
    SUBJECT_GEO_DEFAULTS[_n] = _CN
for _n in (
    "摩根大通", "高盛", "摩根士丹利", "花旗", "花旗银行", "美国银行", "富国银行",
    "Visa", "Mastercard", "PayPal", "Stripe",
    "JPMorgan", "Goldman Sachs", "Morgan Stanley", "Citibank",
    "Bank of America", "Wells Fargo",
):
    SUBJECT_GEO_DEFAULTS[_n] = _US
for _n in ("汇丰银行", "渣打银行", "巴克莱", "HSBC", "Standard Chartered", "Barclays", "劳埃德银行"):
    SUBJECT_GEO_DEFAULTS[_n] = _GB
for _n in ("星展银行", "大华银行", "华侨银行", "GrabPay", "DBS", "UOB", "OCBC"):
    SUBJECT_GEO_DEFAULTS[_n] = _SG
for _n in ("西太平洋银行", "Westpac", "澳新银行", "ANZ", "麦格理", "Macquarie", "联邦银行"):
    SUBJECT_GEO_DEFAULTS[_n] = _AU
for _n in ("三菱日联", "三井住友", "瑞穗", "MUFG"):
    SUBJECT_GEO_DEFAULTS[_n] = _JP
for _n in ("国际清算银行", "SWIFT", "世界银行", "国际货币基金组织", "BIS", "IMF"):
    SUBJECT_GEO_DEFAULTS[_n] = _GL

_GEO_ALIAS_LOWER = {k.lower(): v for k, v in GEO_ALIAS_MAP.items()}


def _lookup_geo_alias(token: str) -> tuple[str, str, str, str | None] | None:
    t = (token or "").strip()
    if not t:
        return None
    if t in GEO_ALIAS_MAP:
        return GEO_ALIAS_MAP[t]
    return _GEO_ALIAS_LOWER.get(t.lower())


def _lookup_subject_geo(name: str | None) -> tuple[str, str, str, str | None] | None:
    if not name:
        return None
    raw = name.strip()
    if not raw:
        return None
    if raw in SUBJECT_GEO_DEFAULTS:
        return SUBJECT_GEO_DEFAULTS[raw]
    canon = normalize_subject(raw)
    if canon and canon in SUBJECT_GEO_DEFAULTS:
        return SUBJECT_GEO_DEFAULTS[canon]
    best = None
    best_len = 0
    hay = (canon or raw)
    for inst, geo in SUBJECT_GEO_DEFAULTS.items():
        if len(inst) >= 4 and hay.startswith(inst) and len(inst) > best_len:
            best, best_len = geo, len(inst)
    return best


def _tuple_to_geo(
    t: tuple[str, str, str, str | None],
    *,
    is_primary: int = 1,
    source_type: str = "auto_rule",
) -> dict[str, Any]:
    code, cname, region, sub = t
    return {
        "country_code": code,
        "country_name": cname,
        "region": region,
        "sub_region": sub,
        "is_primary": is_primary,
        "source_type": source_type,
    }


def _coerce_geo_tokens(raw_geo: Any) -> list[str]:
    if raw_geo is None:
        return []
    if isinstance(raw_geo, dict):
        for key in ("country", "country_code", "country_name", "name"):
            v = raw_geo.get(key)
            if v:
                return [str(v)]
        return []
    if isinstance(raw_geo, str):
        s = raw_geo.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed if x]
            except (json.JSONDecodeError, TypeError):
                pass
        return [p.strip() for p in re.split(r"[,，;；、/|]+", s) if p.strip()]
    if isinstance(raw_geo, (list, tuple, set)):
        out: list[str] = []
        for x in raw_geo:
            if isinstance(x, dict):
                out.extend(_coerce_geo_tokens(x))
            elif x is not None and str(x).strip():
                out.append(str(x).strip())
        return out
    return [str(raw_geo).strip()] if str(raw_geo).strip() else []


def normalize_geos(
    raw_geo: Any = None,
    *,
    subject: str | None = None,
    counterparty: str | None = None,
) -> list[dict[str, Any]]:
    """解析 0..N 条地理记录. 首条 is_primary=1. 无命中返回 []."""
    seen: set[str] = set()
    geos: list[dict[str, Any]] = []
    from_llm = False
    for tok in _coerce_geo_tokens(raw_geo):
        hit = _lookup_geo_alias(tok)
        if not hit:
            continue
        from_llm = True
        if hit[0] in seen:
            continue
        seen.add(hit[0])
        src = "auto_llm"
        geos.append(_tuple_to_geo(hit, is_primary=1 if not geos else 0, source_type=src))
    if not geos:
        for name in (subject, counterparty):
            hit = _lookup_subject_geo(name)
            if hit and hit[0] not in seen:
                seen.add(hit[0])
                geos.append(_tuple_to_geo(hit, is_primary=1 if not geos else 0, source_type="auto_rule"))
                break
    return geos


def normalize_geo(
    raw_geo: Any = None,
    *,
    subject: str | None = None,
    counterparty: str | None = None,
) -> dict[str, Any] | None:
    """归一化单条地理. 无命中返回 None.

    优先 GEO_ALIAS_MAP, 再 SUBJECT_GEO_DEFAULTS (subject 然后 counterparty).
    """
    geos = normalize_geos(raw_geo, subject=subject, counterparty=counterparty)
    return geos[0] if geos else None


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
        "keywords": normalize_keywords(raw.get("keywords")),
        "geography": normalize_geos(
            raw.get("country") if raw.get("country") not in (None, "") else raw.get("geography"),
            subject=subject,
            counterparty=counterparty,
        ),
    }