"""案例可见性口径：全仓统一用这一个函数，避免各查询各写一套。

「可见案例」= 真实抽取行（非 ``__processing__`` 占位）+ 非软删除（``archived``）。
需要看归档行的管理场景显式传 ``include_archived=True``。
"""

from __future__ import annotations

PLACEHOLDER_SUBJECT = "__processing__"


def case_scope_clause(alias: str = "c", *, include_archived: bool = False) -> str:
    """返回可以直接拼进 WHERE 的口径片段。

    Args:
        alias: cases 表的别名，传 ``""`` 表示查询里没有别名。
        include_archived: 管理/恢复场景需要连归档一起看时传 True。
    """
    prefix = f"{alias}." if alias else ""
    clause = f"{prefix}subject != '{PLACEHOLDER_SUBJECT}'"
    if not include_archived:
        clause += f" AND {prefix}case_lifecycle != 'archived'"
    return clause
