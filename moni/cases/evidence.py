"""moni.cases.evidence — 原文证据定位与上下文截取 (0 LLM).

模型给出的 `evidence_snippet` 只是"它认为是原文的句子"。这里用确定性规则
把句子放回 `articles.raw_content`：

- 空白归一（换行/多余空格不影响命中）后逐字匹配；
- 命中则记 offset，前端可据此截前后文、做原文定位；
- 未命中就老实标 0，不假装是引用。
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")


def _pattern_for(evidence: str) -> re.Pattern[str] | None:
    """把证据句转成"空白宽松"的正则：词块之间允许任意空白。"""
    text = evidence.strip()
    if not text:
        return None
    parts = [p for p in _WS.split(text) if p]
    if not parts:
        return None
    return re.compile(r"\s*".join(re.escape(p) for p in parts))


def locate_evidence(
    evidence: str | None,
    raw_content: str | None,
) -> tuple[bool, int | None, int]:
    """返回 (verified, offset, length)；未命中为 (False, None, 0)."""
    if not evidence or not raw_content:
        return False, None, 0
    pattern = _pattern_for(evidence)
    if pattern is None:
        return False, None, 0
    match = pattern.search(raw_content)
    if match is None:
        return False, None, 0
    return True, match.start(), match.end() - match.start()


def build_context(
    raw_content: str | None,
    offset: int | None,
    length: int,
    *,
    before: int = 200,
    after: int = 200,
) -> dict[str, str]:
    """截证据句前后各 N 字，供详情页"展开上下文"使用。"""
    if not raw_content or offset is None or offset < 0:
        return {"context_before": "", "context_after": ""}
    start = max(0, offset - before)
    end = min(len(raw_content), offset + max(0, length) + after)
    return {
        "context_before": raw_content[start:offset].strip(),
        "context_after": raw_content[offset + max(0, length):end].strip(),
    }
