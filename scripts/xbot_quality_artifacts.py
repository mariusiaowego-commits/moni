#!/usr/bin/env python3
"""sprint-54 / id 26111703 —— I4：把候选清单与标注模板**真正落盘**。

⚠️ **只读**：本脚本**只**执行 ``SELECT``（连生产库时是 ``mode=ro`` +
``PRAGMA query_only=ON``）。

产出 3 个文件（默认写到
``artifacts/xbot-quality-2026-11-18/``，**worktree 内非生产路径**）：

1. ``candidates.json`` —— **脱敏**候选清单（元数据 + 标签，**不含正文**）
2. ``annotation-template.json`` —— 标注模板（**agent 草标，不是 dad 真值**）
3. ``uncertain-cases.json`` —— **不确定案例**单独小清单（供 dad 优先看）

**脱敏规则（AC-PRV-01 / C-6）**：

- 只输出 **元数据 + 标签**：article_id、作者 handle、类别、reason_code、
  evidence token、长度、命中词数、保护标记。
- **不含正文**（``raw_content`` / ``title`` 原文一律不进产物）。
- **不含露骨正文**：黄赌毒/灰产类只给 evidence token，不给原文片段。
- **不含凭据**：不输出任何 token / cookie / key。

**标注真值声明**：产物里的 ``agent_suggestion`` 是 **agent 草标**，
``dad_verdict`` / ``dad_note`` 一律留空 —— **dad 没标之前没有任何真值**。

用法::

    python3 scripts/xbot_quality_artifacts.py --db <快照.db> --out-dir <目录>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.xbot_quality_candidates import (  # noqa: E402
    build_candidates,
    open_readonly,
)

#: 产物目录名（worktree 内，非生产路径）
DEFAULT_OUT_DIRNAME = "artifacts/xbot-quality-2026-11-18"

#: 「不确定」的判定：弱特征命中（单条弱证据 ⇒ 可能误杀）⇒ 需 dad 优先看
UNCERTAIN_REASONS = ("weak_only",)


def desensitize_candidate(c: dict) -> dict:
    """把一条候选脱敏成可落盘的形状。

    ⚠️ **不含正文、不含标题原文、不含露骨片段、不含凭据**。
    """
    return {
        "article_id": c["article_id"],
        "author_handle": c.get("author", ""),
        "category": c["category"],
        "reason_code": c["reason_code"],
        # evidence 是**规则 token**（如 ``only_bare_wechat`` / ``rant_term:疯了``）,
        # 不是正文片段 ⇒ 可落盘
        "evidence_tokens": list(c.get("evidence", [])),
        "is_weak_only": bool(c.get("is_weak_only")),
        "author_signal": bool(c.get("author_signal")),
        "is_protected": bool(c.get("is_protected")),
        "protected_kinds": list(c.get("protected_kinds", [])),
        "protected_article_ids": list(c.get("protected_article_ids", [])),
        # 只留长度与计数, 不留内容
        "title_length": c.get("title_length", 0),
        "body_length": c.get("body_length", 0),
        "matched_keyword_count": c.get("matched_keyword_count", 0),
        "published_at": c.get("published_at", ""),
        # ⚠️ agent 草标 —— 不是 dad 真值
        "label_source": "agent_draft",
        "dad_verdict": "",
        "dad_note": "",
    }


def build_uncertain_cases(candidates: list[dict]) -> list[dict]:
    """挑出**不确定案例** —— 单列小清单，供 dad 优先看。

    判据：弱特征命中（``is_weak_only=True``）⇒ 单条弱证据 ⇒ **最可能误杀**。
    """
    out = []
    for c in candidates:
        if not c.get("is_weak_only"):
            continue
        out.append(
            {
                "article_id": c["article_id"],
                "author_handle": c.get("author", ""),
                "category": c["category"],
                "reason_code": c["reason_code"],
                "evidence_tokens": list(c.get("evidence", [])),
                "is_protected": bool(c.get("is_protected")),
                "uncertain_reason": "弱特征单证据 ⇒ 可能误杀真业务内容",
                "needs": "dad 人工判定",
            }
        )
    return out


def write_artifacts(result: dict, out_dir: Path) -> dict:
    """写出 3 个产物文件。返回写出的路径清单。"""
    out_dir.mkdir(parents=True, exist_ok=True)

    cands = [desensitize_candidate(c) for c in result["candidates"]]

    meta = {
        "sprint": "sprint-54",
        "sprint_id": "26111703",
        "source": result["source"],
        "scanned_articles": result["scanned_articles"],
        "candidate_count": len(cands),
        "protected_article_ids": result["protected_article_ids"],
        "unprotected_candidate_ids": result["unprotected_candidate_ids"],
        "author_candidates": result["author_candidates"],
        "tag_names": result["tag_names"],
        "tag_keyword_counts": result["tag_keyword_counts"],
        # ⚠️ 真值声明
        "labels_are": "agent_draft_labels_not_dad_ground_truth",
        "ground_truth_status": "尚未人工标注 ⇒ 无精确率/召回率/误杀率",
        "metrics_reported": [],
        "metrics_omitted_reason": "没有人工标注集 ⇒ 不报精确率/召回率/误杀率",
        "privacy": {
            "contains_article_body": False,
            "contains_explicit_content": False,
            "contains_credentials": False,
            "note": "只含元数据 + 规则 token；黄赌毒/灰产原文一律不落盘",
        },
        "read_only": True,
    }

    paths = {}
    for name, payload in (
        ("candidates.json", {"meta": meta, "candidates": cands}),
        (
            "annotation-template.json",
            {
                "meta": meta,
                "instructions": {
                    "for_dad": (
                        "请逐条填 dad_verdict（keep / quarantine / unsure）与 dad_note。"
                        "填完这批才是**人工标注真值**。"
                    ),
                    "current_state": "dad_verdict / dad_note 全为空 ⇒ 尚无真值",
                    "evidence_hint": "evidence_tokens 是规则命中 token，不是正文",
                },
                "rows": [
                    {
                        "annotation_id": f"ANN-{i:04d}",
                        "article_id": c["article_id"],
                        "author_handle": c["author_handle"],
                        "source": result["source"],
                        "category": c["category"],
                        "reason_codes": c["reason_code"],
                        "evidence_tokens": c["evidence_tokens"],
                        "is_weak_only": c["is_weak_only"],
                        "is_protected": c["is_protected"],
                        "protected_kinds": c["protected_kinds"],
                        "agent_suggestion": c["reason_code"],
                        "dad_verdict": "",
                        "dad_note": "",
                    }
                    for i, c in enumerate(cands, 1)
                ],
            },
        ),
        (
            "uncertain-cases.json",
            {
                "meta": meta,
                "uncertain_count": 0,  # 下面填
                "criteria": "is_weak_only=True ⇒ 单条弱证据 ⇒ 最可能误杀",
                "cases": [],
            },
        ),
    ):
        p = out_dir / name
        p.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        paths[name] = p

    # 回填 uncertain 计数（避免上面写死 0）
    unc = build_uncertain_cases(result["candidates"])
    up = out_dir / "uncertain-cases.json"
    payload = json.loads(up.read_text(encoding="utf-8"))
    payload["uncertain_count"] = len(unc)
    payload["cases"] = unc
    up.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return paths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="数据库路径（**只读** 打开）")
    ap.add_argument(
        "--out-dir",
        default=str(_REPO_ROOT / DEFAULT_OUT_DIRNAME),
        help="产物输出目录（worktree 内非生产路径）",
    )
    ap.add_argument("--source", default="xbot_feed")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print(f"DB 不存在: {args.db}", file=sys.stderr)
        return 2

    conn = open_readonly(args.db)
    try:
        result = build_candidates(conn, source_name=args.source, limit=args.limit)
    finally:
        conn.close()

    out_dir = Path(args.out_dir)
    paths = write_artifacts(result, out_dir)

    print(f"✓ 产物已落盘: {out_dir}")
    for name, p in paths.items():
        print(f"   {name:28s} {p.stat().st_size:8d} 字节  {p}")
    print()
    print(f"   扫描 {result['scanned_articles']} 篇 ⇒ 候选 {result['candidate_count']} 篇")
    print(f"   受保护 {len(result['protected_article_ids'])} / 未受保护 "
          f"{len(result['unprotected_candidate_ids'])}")
    print()
    print("⚠️ 标签是 **agent 草标**；dad_verdict 全空 ⇒ 目前**没有真值**。")
    print("⚠️ 因此产物**不含**精确率 / 召回率 / 误杀率。")
    print("⚠️ 产物只含元数据 + 规则 token，**不含正文、不含露骨内容、不含凭据**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
