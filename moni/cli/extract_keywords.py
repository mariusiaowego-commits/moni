"""moni.cli.extract_keywords — 存量案例关键词批量回填 (v1.0.36 sprint-18).

针对已有 cases 但无 case_keywords 的存量记录 (LLM 打标 pipeline 只对
新增 article 生效), 走轻量 LLM 调用 (只读 subject+counterparty+summary+
evidence_snippet, ~200 tokens/篇) 补打 keywords, 写 case_keywords (auto_llm).

用法:
    moni extract keywords [--source NAME] [--limit N] [--dry-run]
                          [--overwrite-auto]
"""

from __future__ import annotations

import signal
import sys
import time
from collections import Counter

from moni.cases.normalize import normalize_keywords
from moni.db.connection import get_db
from moni.llm.client import resolve_active_model, call_llm


# 轻量 prompt: 只读结构化字段, 避免重新读 4000 字原文 (~200 tokens/篇)
_RETRACTION_PROMPT = """你是金融行业同业案例关键词抽取助手. 给定一条已有案例的结构化文本,
从中抽取 3-5 个**细粒度中文业务关键词** (如"智能投顾"/"跨境支付"/"反欺诈").

要求:
- 必须是中文, 长度 >= 2
- 禁泛词: 银行/金融/系统/创新/业务/AI (单独出现即不合格)
- 必须精准体现具体业务子场景 (跨境支付/智能风控/智能体/大模型应用/数字人民币/生物识别 等)
- 返回纯 JSON: {{"keywords": ["词1", "词2", "词3"]}}

主体: {subject}
对手方: {counterparty}
摘要: {summary}
证据: {evidence_snippet}
"""


def _candidates(*, source: str | None, limit: int | None, include_with_keywords: bool = False) -> list[dict]:
    """选候选案例 (默认: 无 keywords 的非占位)."""
    conn = get_db()
    sql = """
        SELECT c.case_id, c.subject, c.counterparty, c.summary, c.evidence_snippet,
               (SELECT COUNT(*) FROM case_keywords k WHERE k.case_id = c.case_id) AS kw_n
        FROM cases c
        WHERE c.subject != '__processing__'
          AND c.case_lifecycle != 'archived'
    """
    params: list = []
    if source:
        sql += " AND c.article_id IN (SELECT id FROM articles WHERE source = ?)"
        params.append(source)
    if not include_with_keywords:
        sql += " AND NOT EXISTS (SELECT 1 FROM case_keywords k WHERE k.case_id = c.case_id)"
    sql += " ORDER BY c.extracted_at DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _llm_extract_keywords(provider: str, model: str, case: dict) -> list[str]:
    """单条调用 → 返回 normalize_keywords 归一后的 list."""
    prompt = _RETRACTION_PROMPT.format(
        subject=case.get("subject") or "",
        counterparty=case.get("counterparty") or "",
        summary=(case.get("summary") or "")[:300],
        evidence_snippet=(case.get("evidence_snippet") or "")[:300],
    )
    raw = call_llm(provider, model, prompt, timeout=120)
    # 解析 {"keywords": [...]}
    import json, re
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    kws = data.get("keywords") or []
    return normalize_keywords(kws)


def _insert(conn, case_id: str, kws: list[str], source_type: str = "auto_llm") -> int:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    n = 0
    for kw in kws:
        conn.execute(
            """INSERT INTO case_keywords
                   (case_id, keyword, source_type, weight, created_at)
               VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(case_id, keyword) DO NOTHING""",
            (case_id, kw, source_type, now),
        )
        n += 1
    return n


def run_extract_keywords(args) -> int:
    """批量回填关键词 CLI 入口."""
    if args.overwrite_auto:
        # 含 keywords 的也覆盖 (只覆盖 auto_llm, manual_user 保留)
        conn = get_db()
        conn.execute(
            "DELETE FROM case_keywords WHERE source_type = 'auto_llm'"
        )
        conn.commit()

    rows = _candidates(
        source=args.source,
        limit=args.limit,
        include_with_keywords=args.overwrite_auto,
    )

    if args.dry_run:
        by_source = Counter(_get_source(r["case_id"]) for r in rows)
        print(f"[dry-run] 关键词回填候选: {len(rows)} 条案例")
        for s, c in by_source.most_common():
            print(f"  {s}: {c}")
        print(f"  预计 LLM 调用: {len(rows)} 次 (~200 tokens/次, ~1-2s/次)")
        print(f"  粗算时长: {len(rows) * 1.5 / 60:.0f} 分钟")
        return 0

    if not rows:
        print("[extract keywords] 无候选 (案例都已有 keywords)")
        return 0

    provider, model = resolve_active_model()
    stats = {"total": len(rows), "ok": 0, "failed": 0, "inserted": 0}
    t0 = time.time()
    interrupted = {"flag": False}

    def _sigint(_sig, _frame):
        interrupted["flag"] = True
        print("\n[SIGINT] 中断, 处理完当前条后停止...", flush=True)

    signal.signal(signal.SIGINT, _sigint)

    for i, row in enumerate(rows, 1):
        if interrupted["flag"]:
            break
        try:
            kws = _llm_extract_keywords(provider, model, row)
            conn = get_db()
            n = _insert(conn, row["case_id"], kws)
            conn.commit()
            stats["inserted"] += n
            stats["ok"] += 1
        except Exception as exc:
            stats["failed"] += 1
            print(f"  [{i}] failed: {row['case_id']}: {exc}", flush=True)

        if i % 10 == 0 or i == len(rows) or interrupted["flag"]:
            el = time.time() - t0
            print(
                f"  进度 {i}/{len(rows)} | 完成={stats['ok']} 失败={stats['failed']} "
                f"已插入关键词={stats['inserted']} | {el:.0f}s",
                flush=True,
            )

    el = time.time() - t0
    print(
        f"[extract keywords] 完成: 处理 {stats['total']} 条, {el / 60:.1f} 分钟, "
        f"插入 {stats['inserted']} 个关键词"
    )
    return 0


def _get_source(case_id: str) -> str:
    conn = get_db()
    row = conn.execute(
        "SELECT a.source FROM cases c JOIN articles a ON a.id = c.article_id "
        "WHERE c.case_id = ?",
        (case_id,),
    ).fetchone()
    return row["source"] if row else "?"