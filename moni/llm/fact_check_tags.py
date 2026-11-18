"""事实核查结论的档位标签读写（v1.0.100）。

闸门以前用 ``==`` 比较整个 conclusion，模型写「【已核实】+ 说明」就
整条绕过白名单。这里统一按标签判定，改写时保留说明文字。
"""
from __future__ import annotations

import re

_TAG_RE = re.compile(r"【(已核实|待核实|存疑|无需核查)】")

TAGS: tuple[str, ...] = ("【已核实】", "【待核实】", "【存疑】", "【无需核查】")


def conclusion_tag(text: str | None) -> str:
    """取第一个档位标签；没有标签返回空串。"""
    m = _TAG_RE.search(text or "")
    return f"【{m.group(1)}】" if m else ""


def with_tag(tag: str, text: str | None) -> str:
    """改写结论档位，保留标签后面的说明文字。"""
    original = (text or "").strip()
    detail = _TAG_RE.sub("", original).strip(" ：:，,")
    return f"{tag}{detail}" if detail else tag
