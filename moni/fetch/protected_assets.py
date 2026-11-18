"""sprint-54 / id 26111703 —— 保护资产只读探针 (``ProtectedAssetGuard``)。

**只读**：本模块**只**执行 ``SELECT``。不 INSERT / UPDATE / DELETE / DDL，
不 rebuild / repair / migrate，不触发任何 FTS 写入。

**用途**：阶段一的只读候选预演器（I3）与只读 dry-run 端点（I8）在输出候选清单时，
必须同时输出 ``protected_article_ids`` 与 ``is_protected`` ——
这是 **dad 亲自审阅阶段一结果的唯一抓手**（p7 第四轮 C-6）：
没有它，dad 看不出候选里哪些是高价值 / 保护资产。

覆盖的保护资产（全部**只读**查询）：

| 资产 | 来源 |
|---|---|
| Pin（置顶） | ``article_pins`` |
| 质量评分 | ``articles.quality_score`` / ``quality_status`` |
| 业务价值评分 | ``articles.business_value_score`` / ``business_value_status`` |
| AI 摘要 | ``articles.has_ai_summary`` / ``ai_summary`` |
| 事实核查 | ``articles.has_fact_check`` / ``fact_check_conclusion`` |
| 简报 | ``articles.brief_body_md`` / ``brief_summary`` |
| 案例 | ``cases.article_id`` |
| 事件 | ``events.article_id`` |
| 阅读对话 | ``reader_conversations.article_id`` |
| 分享任务 | ``share_tasks.article_ids``（JSON 数组，**含**匹配即算） |

⚠️ **零新表 / 零 schema 变更** —— 全部复用既有表与列。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

#: 保护资产来源的中文标签 (输出给 dad 看, **不含正文**)
PROTECTED_KINDS: tuple[str, ...] = (
    "pin",
    "quality_score",
    "business_value_score",
    "ai_summary",
    "fact_check",
    "brief",
    "case",
    "event",
    "reader_conversation",
    "share_task",
)


@dataclass(frozen=True)
class ProtectedAsset:
    """某篇文章持有的保护资产 (只含**元数据**, 不含任何正文)。"""

    article_id: str
    kinds: tuple[str, ...] = ()

    @property
    def is_protected(self) -> bool:
        return bool(self.kinds)


@dataclass
class ProtectedAssetGuard:
    """只读查询「哪些文章是保护资产」。

    ⚠️ **只读**：所有方法只跑 ``SELECT``。
    """

    conn: sqlite3.Connection
    _cache: dict[str, tuple[str, ...]] = field(default_factory=dict, repr=False)

    # ── 单条查询 (逐类) ────────────────────────────────────────────

    def _kinds_for(self, article_id: str) -> tuple[str, ...]:
        found: list[str] = []
        cur = self.conn.execute

        if cur(
            "SELECT 1 FROM article_pins WHERE article_id = ? LIMIT 1",
            (article_id,),
        ).fetchone():
            found.append("pin")

        row = cur(
            "SELECT quality_score, business_value_score, has_ai_summary, ai_summary, "
            "       has_fact_check, fact_check_conclusion, brief_body_md, brief_summary "
            "FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
        if row is not None:
            if row["quality_score"] is not None:
                found.append("quality_score")
            if row["business_value_score"] is not None:
                found.append("business_value_score")
            if row["has_ai_summary"] or row["ai_summary"]:
                found.append("ai_summary")
            if row["has_fact_check"] or row["fact_check_conclusion"]:
                found.append("fact_check")
            if row["brief_body_md"] or row["brief_summary"]:
                found.append("brief")

        if cur(
            "SELECT 1 FROM cases WHERE article_id = ? LIMIT 1", (article_id,)
        ).fetchone():
            found.append("case")
        if cur(
            "SELECT 1 FROM events WHERE article_id = ? LIMIT 1", (article_id,)
        ).fetchone():
            found.append("event")
        if cur(
            "SELECT 1 FROM reader_conversations WHERE article_id = ? LIMIT 1",
            (article_id,),
        ).fetchone():
            found.append("reader_conversation")

        # share_tasks.article_ids 是 JSON 数组 ⇒ 含匹配即算
        for r in cur("SELECT article_ids FROM share_tasks").fetchall():
            raw = r["article_ids"]
            if not raw:
                continue
            try:
                ids = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(ids, int):
                ids = [ids]
            if article_id in ids:
                found.append("share_task")
                break

        return tuple(found)

    # ── 批量查询 (推荐: 一次查一批, 避免 N+1) ───────────────────────

    def protected_kinds_map(self, article_ids: list[str]) -> dict[str, tuple[str, ...]]:
        """批量返回 ``{article_id: (kind, ...)}`` (只含**有**保护资产的条目)。

        ⚠️ **article_id 是 TEXT** (生产实测: 24 位十六进制串, 如 ``13525e80d88e7816``)
        ⇒ 必须按**字符串**比较; 用 int 查会**一条都命中不了** (静默漏检)。
        """
        if not article_ids:
            return {}
        ids = list(dict.fromkeys(str(i) for i in article_ids))

        out: dict[str, list[str]] = {i: [] for i in ids}
        marks = ",".join("?" * len(ids))

        def add(sql: str, kind: str) -> None:
            for r in self.conn.execute(sql, ids).fetchall():
                # ⚠️ 一律按 **str** 索引 —— article_id 是 TEXT 列
                aid = str(r[0])
                out.setdefault(aid, []).append(kind)

        add(f"SELECT article_id FROM article_pins WHERE article_id IN ({marks})", "pin")
        add(f"SELECT article_id FROM cases WHERE article_id IN ({marks})", "case")
        add(f"SELECT article_id FROM events WHERE article_id IN ({marks})", "event")
        add(
            f"SELECT article_id FROM reader_conversations WHERE article_id IN ({marks})",
            "reader_conversation",
        )
        add(
            f"SELECT id FROM articles WHERE id IN ({marks}) AND quality_score IS NOT NULL",
            "quality_score",
        )
        add(
            f"SELECT id FROM articles WHERE id IN ({marks}) AND business_value_score IS NOT NULL",
            "business_value_score",
        )
        add(
            f"SELECT id FROM articles WHERE id IN ({marks}) "
            "AND (has_ai_summary = 1 OR ai_summary IS NOT NULL)",
            "ai_summary",
        )
        add(
            f"SELECT id FROM articles WHERE id IN ({marks}) "
            "AND (has_fact_check = 1 OR fact_check_conclusion IS NOT NULL)",
            "fact_check",
        )
        add(
            f"SELECT id FROM articles WHERE id IN ({marks}) "
            "AND (brief_body_md IS NOT NULL OR brief_summary IS NOT NULL)",
            "brief",
        )

        # share_tasks 是 JSON 数组 ⇒ 逐行解析
        for r in self.conn.execute("SELECT article_ids FROM share_tasks").fetchall():
            raw = r["article_ids"]
            if not raw:
                continue
            try:
                arr = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(arr, int):
                arr = [arr]
            for aid in arr:
                aid_s = str(aid)
                if aid_s in out:
                    out[aid_s].append("share_task")

        return {k: tuple(v) for k, v in out.items() if v}

    # ── 单条便捷方法 ────────────────────────────────────────────────

    def is_protected(self, article_id: str) -> bool:
        return bool(self.kinds_for(article_id))

    def kinds_for(self, article_id: str) -> tuple[str, ...]:
        key = str(article_id)
        if key not in self._cache:
            self._cache[key] = self._kinds_for(key)
        return self._cache[key]


def split_protected(
    guard: ProtectedAssetGuard, article_ids: list[str]
) -> tuple[list[str], list[str], dict[str, tuple[str, ...]]]:
    """把候选文章分成 (普通, 受保护, 保护种类映射)。

    返回三元组::

        (unprotected_ids, protected_ids, {article_id: (kind, ...)})

    ⚠️ **article_id 是 TEXT** (生产实测为 24 位十六进制串) ⇒ 按字符串比较。
    """
    kinds_map = guard.protected_kinds_map(article_ids)
    protected = sorted(kinds_map.keys())
    protected_set = set(protected)
    unprotected = [i for i in article_ids if i not in protected_set]
    return unprotected, protected, kinds_map
