"""routes/moderation — xbot_feed 劣质内容**只读**预演端点 (sprint-54, id 26111703)。

端点::

    GET /api/moderation/xbot/candidates

⚠️ **只读端点**：

* 本模块**只**跑 ``SELECT``（外加只读 ``PRAGMA``）；
* **不** INSERT / UPDATE / DELETE / DDL；
* **不** rebuild / repair / FTS integrity 写命令；
* **不**屏蔽作者、**不**归档、不触发任何处置；
* **没有** POST / PUT / DELETE 路由 —— **不得**挂载写库方法。

⚠️ **候选 ≠ 处置**：本端点只是给 dad **看**候选清单。
隔离 / 屏蔽 / 归档都**不在**这里，且都还需要 dad 单独 GO。

⚠️ **不含正文**：响应只含**元数据 + 标签**（article_id、作者 handle、类别、
reason_code、evidence token、长度、命中词数、保护标记）。
黄赌毒/灰产类**原文一律不落盘、不返回**。

⚠️ **主题 ≠ 准入**：命中 tag 关键词只说明「相关」；这里的劣质判定是**另一层**。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from moni.db.connection import get_db
from moni.fetch.moderation import Decision, aggregate_author, evaluate_article
from moni.fetch.protected_assets import ProtectedAssetGuard, split_protected

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/moderation", tags=["moderation"])

#: 本端点只服务 xbot_feed (handle / publisher 语义是它专有的)
_XBOT_SOURCE = "xbot_feed"

#: 单次最多返回多少篇候选 (防止把大结果灌进响应)
_MAX_LIMIT = 500

#: 中文分词口径 (PLAN R11) —— 空白切分的**整段连续串**。
#:
#: ⚠️ 这里**不做**中文分词 —— FTS5 的 ``unicode61`` 按空白切分,
#: 所以「微信小程序」是一个 token, 「小程序」**不是**。
#: 详见 ``scripts/verify_fts_index_criteria.py``。
_CJK = r"一-鿿"


def _load_tag_keywords(conn, tag_names: list[str]) -> dict[str, list[str]]:
    """读 tag 的当前关键词 (**只读**)。"""
    import json

    names = [t for t in dict.fromkeys(tag_names) if t]
    if not names:
        return {}
    marks = ",".join("?" * len(names))
    rows = conn.execute(
        f"SELECT name, keywords FROM tag_definitions "
        f"WHERE is_active = 1 AND name IN ({marks})",
        names,
    ).fetchall()
    out: dict[str, list[str]] = {}
    for row in rows:
        raw = row[1]
        if not raw:
            continue
        try:
            arr = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(arr, list):
            continue
        kws = [str(k).strip() for k in arr if str(k).strip()]
        if kws:
            out[row[0]] = kws
    return out


def _source_tag_names(conn, source_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT t.name FROM source_tags st "
        "JOIN tag_definitions t ON t.name = st.tag_name "
        "WHERE st.source_id = ?",
        (source_id,),
    ).fetchall()
    return [r[0] for r in rows]


def _replay_matched_keywords(
    *, title: str, body: str, tag_keywords: dict[str, list[str]]
) -> list[str]:
    """重放**完整**命中词表 (C-4)。

    ⚠️ **不得**改查 ``article_keyword_hits`` —— 那个表每个 tag 只记**首**个
    命中词 ⇒ 是**下界** ⇒ 会把「正文含『微信小程序』」的真业务文章
    误判成「仅命中裸词『微信』」。

    这里对每个 tag 跑**全部**关键词, 不 break。
    """
    from moni.fetch.keyword_match import keyword_matches

    matched: list[str] = []
    for kws in tag_keywords.values():
        for kw in kws:
            if kw not in matched and keyword_matches(
                kw, title=title, body=body, field="title+content"
            ):
                matched.append(kw)
    return matched


@router.get("/xbot/candidates")
def xbot_candidates(
    limit: int = Query(100, ge=1, le=_MAX_LIMIT, description="最多返回多少篇候选"),
    source: str = Query(_XBOT_SOURCE, description="信源名（默认 xbot_feed）"),
) -> dict:
    """**只读**返回 xbot_feed 的劣质内容候选清单。

    响应里**必含**：

    * ``protected_article_ids`` —— 带保护资产的候选 id (C-6)
    * 每条候选的 ``is_protected`` / ``protected_kinds`` (C-6)

    ⚠️ **不返回正文**；⚠️ **不执行任何处置**；⚠️ **不报准确率**
    （没有人工标注 ⇒ 没有精确率/召回率/误杀率）。
    """
    conn = get_db()

    row = conn.execute("SELECT id FROM sources WHERE name = ?", (source,)).fetchone()
    if not row:
        return {
            "source": source,
            "scanned_articles": 0,
            "candidate_count": 0,
            "candidates": [],
            "protected_article_ids": [],
            "unprotected_candidate_ids": [],
            "author_candidates": [],
            "metrics_reported": [],
            "note": f"信源不存在: {source}",
        }
    source_id = row[0]

    tag_names = _source_tag_names(conn, source_id)
    tag_keywords = _load_tag_keywords(conn, tag_names)

    rows = conn.execute(
        "SELECT id, title, publisher, raw_content, published_at "
        "FROM articles WHERE source = ? ORDER BY published_at DESC",
        (source,),
    ).fetchall()

    decisions: list[tuple[str, Decision]] = []
    candidates: list[dict] = []
    per_author: dict[str, list[tuple[str, Decision]]] = {}

    for r in rows:
        title = r[1] or ""
        body = r[3] or ""
        publisher = r[2] or ""

        all_matched = _replay_matched_keywords(
            title=title, body=body, tag_keywords=tag_keywords
        )
        d = evaluate_article(
            title=title,
            content=body,
            author_name=publisher,
            handle=publisher,
            all_matched_keywords=all_matched,
        )
        decisions.append((r[0], d))
        per_author.setdefault(publisher, []).append((r[0], d))

        if d.action == "quarantine":
            candidates.append(
                {
                    "article_id": r[0],
                    "author_handle": publisher,
                    "category": d.category,
                    "reason_code": d.reason_code,
                    "evidence_tokens": list(d.evidences),
                    "is_weak_only": bool(d.is_weak_only),
                    "author_signal": bool(d.author_signal),
                    "published_at": r[4],
                    # ⚠️ 只含长度, 不含内容
                    "title_length": len(title),
                    "body_length": len(body),
                    "matched_keyword_count": len(all_matched),
                }
            )

    # ── 保护资产 (C-6) ──────────────────────────────────────────────────
    guard = ProtectedAssetGuard(conn=conn)
    cand_ids = [c["article_id"] for c in candidates]
    unprotected, protected, kinds_map = split_protected(guard, cand_ids)
    for c in candidates:
        aid = c["article_id"]
        c["is_protected"] = aid in kinds_map
        c["protected_kinds"] = list(kinds_map.get(aid, ()))
        c["protected_article_ids"] = [aid] if aid in kinds_map else []

    # ── 作者级聚合 (C-7) ────────────────────────────────────────────────
    author_rows = []
    for author, dlist in per_author.items():
        agg = aggregate_author(author_key=author, decisions=dlist)
        if agg.author_candidate or agg.strong_samples:
            author_rows.append(
                {
                    "author_handle": author,
                    "strong_samples": agg.strong_samples,
                    "weak_samples": agg.weak_samples,
                    # ⚠️ 只是候选, **绝不自动封禁**
                    "author_candidate": agg.author_candidate,
                    "needs_dad_confirmation": True,
                }
            )

    limited = candidates[:limit]
    limited_ids = {c["article_id"] for c in limited}

    return {
        "source": source,
        "scanned_articles": len(rows),
        "candidate_count": len(candidates),
        "returned": len(limited),
        "candidates": limited,
        # ⚠️ C-6: 保护标记 —— **全量**候选的（不受 limit 截断）
        "protected_article_ids": protected,
        "unprotected_candidate_ids": unprotected,
        "author_candidates": author_rows,
        "tag_names": tag_names,
        # ⚠️ 没有人工标注 ⇒ 不报任何准确率
        "metrics_reported": [],
        "metrics_omitted_reason": "没有人工标注集 ⇒ 不报精确率/召回率/误杀率",
        "read_only": True,
        "note": (
            "候选 ≠ 处置。本端点不隔离、不屏蔽、不归档；"
            "所有处置都需 dad 单独 GO。"
        ),
        "privacy": {
            "contains_article_body": False,
            "contains_explicit_content": False,
        },
    }
