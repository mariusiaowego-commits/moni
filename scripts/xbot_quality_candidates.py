#!/usr/bin/env python3
"""sprint-54 / id 26111703 —— xbot_feed 劣质内容**只读候选预演器**（I3）。

⚠️ **只读**：本脚本**只**执行 ``SELECT``。

- 连接用 ``mode=ro`` + ``PRAGMA query_only=ON``（C-3 / G-C）
- **不** INSERT / UPDATE / DELETE / DDL
- **不** rebuild / repair / migrate / checkpoint
- **不** 跑 FTS5 ``integrity-check``（那是 INSERT 语法 ⇒ 写命令）
- **不** 连生产抓取 / 不屏蔽 / 不恢复 / 不改任何配置

**输出只含元数据 + 标签，不含正文**（``AC-PRV-01``）——
黄赌毒 / 灰产类内容**不落盘**露骨正文，只输出 id / handle / 分类 / reason_code /
evidence token / article_ids / protected_article_ids。

**C-4 硬约束**：判定「是否仅命中裸词『微信』」时，必须**重放完整词表**，
**严禁**直接查 ``article_keyword_hits``（那个表每个 tag 只记**首**个命中词 ⇒ 是**下界**）。

**C-6 硬约束**：输出必须含 ``protected_article_ids`` 与 ``is_protected``。

用法::

    # 只读预演 (默认)
    python3 scripts/xbot_quality_candidates.py --db data/news.db

    # 输出标注集模板 (元数据 + 标签, 不含正文)
    python3 scripts/xbot_quality_candidates.py --emit-annotation-template

    # 输出 JSON
    python3 scripts/xbot_quality_candidates.py --json

⚠️ 本脚本**不**报精确率 / 召回率 / 误杀率 —— **没有标注集就没有这些数字**。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

# 允许从仓库根直接运行
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from moni.fetch.keyword_match import keyword_matches  # noqa: E402
from moni.fetch.moderation import (  # noqa: E402
    aggregate_author,
    evaluate_article,
)
from moni.fetch.protected_assets import ProtectedAssetGuard, split_protected  # noqa: E402

#: 本预演覆盖的源
TARGET_SOURCE = "xbot_feed"

#: 判定「主题归属」用的 topic→候选 tag 映射 (与 xbot_feed 的 scope 同构)
#: ⚠️ 默认按源绑定的 **全部 active tag** 取候选 (保守: 不预设 dad 的偏好)。
DEFAULT_TOPIC = "wechat"


# ── 只读连接 ────────────────────────────────────────────────────────────────

def open_readonly(db_path: str) -> sqlite3.Connection:
    """打开**只读**连接 (``mode=ro`` + ``PRAGMA query_only=ON``)。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def load_tag_keywords(conn: sqlite3.Connection, tag_names: list[str]) -> dict[str, list[str]]:
    """读 tag 的**当前**关键词 (与 ``xbot_feed._load_tag_keywords`` 同规则)。

    ⚠️ **只取** ``is_active = 1`` 的 tag; ``keywords`` 为 NULL/空/非法 JSON 的跳过。
    """
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
        raw = row["keywords"]
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
            out[row["name"]] = kws
    return out


def source_tag_names(conn: sqlite3.Connection, source_id: str) -> list[str]:
    """取源绑定的 tag 名 (只读)。"""
    rows = conn.execute(
        "SELECT t.name FROM source_tags st JOIN tag_definitions t ON t.name = st.tag_name "
        "WHERE st.source_id = ?",
        (source_id,),
    ).fetchall()
    return [r[0] for r in rows]


def source_id_for(conn: sqlite3.Connection, source_name: str) -> str | None:
    row = conn.execute(
        "SELECT id FROM sources WHERE name = ?", (source_name,)
    ).fetchone()
    return row[0] if row else None


# ── 完整词表重放 (C-4) ──────────────────────────────────────────────────────

def replay_all_matched_keywords(
    *,
    title: str,
    body: str,
    tag_keywords: dict[str, list[str]],
) -> list[str]:
    """重放**完整**命中词表 —— 每个 tag 的**所有**命中词, 不是一个。

    ⚠️ 这是 **C-4** 的落点。``xbot_feed._match_topic_tags`` 对每个 tag
    命中**首词即 break** ⇒ 持久化的 ``article_keyword_hits`` 只记了
    **每个 tag 的第一个**命中词 ⇒ 那是**下界**。

    例: 一篇正文同时含「微信」与「微信小程序」的推文,
    ``article_keyword_hits`` 里只有「微信」⇒ 只看该表会把真业务内容
    误判成「仅命中裸词微信」。

    ⇒ 这里对每个 tag 跑**全部**关键词, 不 break。
    """
    matched: list[str] = []
    for kws in tag_keywords.values():
        for kw in kws:
            if kw not in matched and keyword_matches(
                kw, title=title, body=body, field="title+content"
            ):
                matched.append(kw)
    return matched


# ── 主流程 ──────────────────────────────────────────────────────────────────

def build_candidates(
    conn: sqlite3.Connection,
    *,
    source_name: str = TARGET_SOURCE,
    topic: str = DEFAULT_TOPIC,
    limit: int | None = None,
) -> dict:
    """跑一遍只读预演, 返回候选清单 (元数据 + 标签)。"""
    src_id = source_id_for(conn, source_name)
    tag_names = source_tag_names(conn, src_id) if src_id else []
    tag_keywords = load_tag_keywords(conn, tag_names)

    sql = (
        "SELECT id, title, publisher, raw_content, published_at, url "
        "FROM articles WHERE source = ? ORDER BY published_at DESC"
    )
    params: list = [source_name]
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()

    # ── 逐篇判定 ────────────────────────────────────────────────────────
    decisions: list[tuple[str, object]] = []
    per_article: list[dict] = []
    author_decisions: dict[str, list[tuple[str, object]]] = {}

    for r in rows:
        title = r["title"] or ""
        body = r["raw_content"] or ""
        publisher = r["publisher"] or ""

        # ⚠️ C-4: **必须**重放完整词表并真的传进去
        all_matched = replay_all_matched_keywords(
            title=title, body=body, tag_keywords=tag_keywords
        )

        d = evaluate_article(
            title=title,
            content=body,
            author_name=publisher,
            handle=publisher,
            all_matched_keywords=all_matched,
        )
        decisions.append((r["id"], d))
        author_decisions.setdefault(publisher, []).append((r["id"], d))

        if d.action == "quarantine":
            per_article.append(
                {
                    "article_id": r["id"],
                    "author": publisher,
                    "category": d.category,
                    "reason_code": d.reason_code,
                    "evidence": list(d.evidences),
                    "is_weak_only": d.is_weak_only,
                    "author_signal": d.author_signal,
                    "published_at": r["published_at"],
                    # ⚠️ 只含**元数据**, 不含正文
                    "title_length": len(title),
                    "body_length": len(body),
                    "matched_keyword_count": len(all_matched),
                }
            )

    # ── 保护资产 (C-6) ─────────────────────────────────────────────────
    guard = ProtectedAssetGuard(conn=conn)
    all_ids = [r["id"] for r in rows]
    candidate_ids = [a["article_id"] for a in per_article]
    unprotected_ids, protected_ids, kinds_map = split_protected(guard, candidate_ids)

    for a in per_article:
        aid = a["article_id"]
        a["is_protected"] = aid in kinds_map
        a["protected_article_ids"] = [aid] if aid in kinds_map else []
        a["protected_kinds"] = list(kinds_map.get(aid, ()))

    # ── 作者级聚合 (C-7) ──────────────────────────────────────────────
    author_rows = []
    for author, dlist in author_decisions.items():
        agg = aggregate_author(author_key=author, decisions=dlist)
        if agg.author_candidate or agg.strong_samples:
            author_rows.append(
                {
                    "author": author,
                    "strong_samples": agg.strong_samples,
                    "weak_samples": agg.weak_samples,
                    "author_candidate": agg.author_candidate,
                    "weak_article_ids": list(agg.weak_article_ids),
                    "strong_article_ids": list(agg.strong_article_ids),
                }
            )

    return {
        "source": source_name,
        "source_id": src_id,
        "topic": topic,
        "tag_names": tag_names,
        "tag_keyword_counts": {k: len(v) for k, v in tag_keywords.items()},
        "scanned_articles": len(rows),
        "candidate_count": len(per_article),
        "candidates": per_article,
        "protected_article_ids": protected_ids,
        "unprotected_candidate_ids": unprotected_ids,
        "protected_kinds_map": {k: list(v) for k, v in kinds_map.items()},
        "author_candidates": author_rows,
        # ⚠️ 显式声明本轮**没有**这些数字 (没有标注集就没有)
        "metrics_reported": [],
        "metrics_omitted_reason": "没有人工标注集 ⇒ 不报精确率/召回率/误杀率",
    }


ANNOTATION_TEMPLATE_FIELDS = (
    "annotation_id",
    "article_id",
    "author_handle",
    "source",
    "category",
    "reason_codes",
    "evidence_tokens",
    "is_weak_only",
    "is_protected",
    "protected_kinds",
    "agent_suggestion",
    "dad_verdict",
    "dad_note",
)


def annotation_template(candidates: list[dict]) -> list[dict]:
    """产出标注集**模板** (元数据 + 标签, **不含正文**)。

    ⚠️ 模板内容是 **agent 建议, 不是 dad 的人工标注** ⇒
    ``dad_verdict`` / ``dad_note`` 一律留空。
    """
    out = []
    for i, c in enumerate(candidates, 1):
        out.append(
            {
                "annotation_id": f"ANN-{i:04d}",
                "article_id": c["article_id"],
                "author_handle": c["author"],
                "source": TARGET_SOURCE,
                "category": c["category"],
                "reason_codes": c["reason_code"],
                "evidence_tokens": c["evidence"],
                "is_weak_only": c["is_weak_only"],
                "is_protected": c["is_protected"],
                "protected_kinds": c["protected_kinds"],
                "agent_suggestion": c["reason_code"],
                "dad_verdict": "",
                "dad_note": "",
            }
        )
    return out


def render_text(result: dict) -> str:
    lines = [
        "=" * 72,
        f"xbot_feed 劣质内容候选预演（**只读**）— {result['source']}",
        "=" * 72,
        f"扫描文章数        : {result['scanned_articles']}",
        f"候选文章数        : {result['candidate_count']}",
        f"其中受保护        : {len(result['protected_article_ids'])}",
        f"  (未受保护)      : {len(result['unprotected_candidate_ids'])}",
        f"作者级候选        : {len(result['author_candidates'])}",
        f"参与判定的 tag    : {', '.join(result['tag_names']) or '(无)'}",
        f"tag 关键词数      : {result['tag_keyword_counts']}",
        "",
        "⚠️ 本结果**不含**正文；黄赌毒/灰产原文一律不落盘。",
        "⚠️ 本结果**不含**精确率/召回率/误杀率 —— 没有人工标注集就没有这些数字。",
        "⚠️ 规则阈值一律「待实测建议」，未经标注集标定不得当作已验证值。",
        "⚠️ 上游 feed 无媒体字段 ⇒ **媒体类劣质内容是硬盲区**，本结果不覆盖。",
        "",
    ]
    by_cat: dict[str, list[dict]] = {}
    for c in result["candidates"]:
        by_cat.setdefault(c["category"] or "(未分类)", []).append(c)
    for cat, items in sorted(by_cat.items()):
        weak = sum(1 for i in items if i["is_weak_only"])
        prot = sum(1 for i in items if i["is_protected"])
        lines.append(f"── 类别 {cat}: {len(items)} 篇 (弱特征 {weak} / 受保护 {prot})")
        for c in items[:50]:
            flag = " [保护]" if c["is_protected"] else ""
            weakflag = " [弱]" if c["is_weak_only"] else ""
            lines.append(
                f"   {c['article_id']}  {c['reason_code']:<24s} "
                f"ev={','.join(c['evidence'][:3])}{weakflag}{flag}"
            )
        if len(items) > 50:
            lines.append(f"   … 另有 {len(items) - 50} 篇 (--json 看全量)")
        lines.append("")
    if result["author_candidates"]:
        lines.append("── 作者级候选（**只是候选，绝不自动封禁**）")
        for a in result["author_candidates"]:
            if a["author_candidate"]:
                lines.append(
                    f"   {a['author']}  强样本 {a['strong_samples']} / 弱样本 {a['weak_samples']}"
                    f"  ⇒ **够格进候选，需 dad 确认**"
                )
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--db",
        default=str(_REPO_ROOT / "data" / "news.db"),
        help="数据库路径 (以 **只读** 方式打开)",
    )
    ap.add_argument("--source", default=TARGET_SOURCE)
    ap.add_argument("--topic", default=DEFAULT_TOPIC)
    ap.add_argument("--limit", type=int, default=None, help="只扫前 N 篇 (调试用)")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument(
        "--emit-annotation-template",
        action="store_true",
        help="输出标注集模板 (元数据 + 标签, 不含正文)",
    )
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print(f"DB 不存在: {args.db}", file=sys.stderr)
        return 2

    conn = open_readonly(args.db)
    try:
        result = build_candidates(
            conn, source_name=args.source, topic=args.topic, limit=args.limit
        )
    finally:
        conn.close()

    if args.emit_annotation_template:
        tpl = annotation_template(result["candidates"])
        if args.json:
            print(json.dumps(tpl, ensure_ascii=False, indent=2))
        else:
            print(f"# 标注集模板 —— **agent 建议，不是 dad 人工标注**（dad_verdict 留空待填）")
            print(f"# 候选数: {len(tpl)}")
            print(json.dumps(tpl, ensure_ascii=False, indent=2))
        return 0

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
