"""moni.stats.split_institutions — 同业机构拆分与去重 (v1.0.35.19).

v2 方案 SQL 拆分实现 (dad 拍板 8-26). 数据层独立模块, 不依赖 stats/repository
避免大文件再膨胀. 案例库/事件库未来若需"同业追踪数"可直接复用.

设计要点 (grok 评审 F4 + F22):
1. 拆分串: 按 [、，,;；/及与] 切 (agF22 共识).
2. 去"等 N 家机构"后缀.
3. 复用 cases/normalize.py 的 normalize_subject (BANK_ALIAS_MAP 边界修 v1.0.35.19).
4. distinct_institutions() 返回规范名 list (用于 total_institutions 计数).
"""

from __future__ import annotations

import re
from typing import Any

# v2 方案口径: SQL 拆分 + alias 归一, 不建 case_institutions 关联表
# (agy 推荐建表但 dad 拍板 SQL 拆分, 简单快速)

# 拆分分隔符 (中文 + 半角逗号/分号/斜杠 + 中文连词"及""与")
_SPLIT_RE = re.compile(r"[、，,;；/及与]+")

# "等 N 家" "等 11 家机构" "等 N 家机构" 等后缀
_TAIL_ET_AL_RE = re.compile(r"等\s*\d+\s*家.*$")


def split_institutions(raw: str | None) -> list[str]:
    """拆分机构串 → 规范机构名列表.

    规则: split [、，,;；/及与] → 去"等N家.*"后缀 → alias 归一.
    未匹配 alias 的国际组织 (SWIFT/Visa 等) 保留原名, 同样参与计数.
    """
    from moni.cases.normalize import normalize_subject

    if not raw:
        return []
    cleaned = _TAIL_ET_AL_RE.sub("", raw.strip())
    if not cleaned:
        return []
    out: list[str] = []
    for part in _SPLIT_RE.split(cleaned):
        p = part.strip()
        if not p:
            continue
        canon = normalize_subject(p)
        if canon:
            out.append(canon)
    return out


def distinct_institutions(
    rows: list[tuple[str | None, str | None]],
) -> list[str]:
    """subject ∪ counterparty 拆分后 DISTINCT 列表.

    用于 cases_dashboard_stats.total_institutions 计数 (dad 8-26 拍板口径).
    返回顺序按出现频次 DESC, 同频次按名字典序 (稳定, 测试友好).
    """
    from collections import Counter

    counter: Counter[str] = Counter()
    for subject, counterparty in rows:
        for name in split_institutions(subject):
            counter[name] += 1
        for name in split_institutions(counterparty):
            counter[name] += 1

    # 频次降序, 同频次名字典序
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [name for name, _ in items]


# 兼容旧 import 路径: moni.stats.repository._split_institutions 测试直调
# (tests/test_stats.py::TestSplitInstitutions). 保留旧名字以不破坏现有测试.
def _split_institutions_compat(raw: str | None) -> list[str]:
    return split_institutions(raw)