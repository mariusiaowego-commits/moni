"""moni.cli.extract_geo — 存量案例地理维度回填 (v1.0.37 sprint-19).

阶段 1: GEO_ALIAS_MAP + SUBJECT_GEO_DEFAULTS 规则推断 (0 token)
阶段 2: 未命中的走轻量 LLM

用法:
    moni extract geo [--source NAME] [--limit N] [--dry-run] [--overwrite]
"""

from __future__ import annotations

import json
import re
import signal
import time
from collections import Counter
from datetime import datetime, timezone

from moni.cases.normalize import normalize_geos
from moni.db.connection import get_db
from moni.llm.client import resolve_active_model, call_llm


_GEO_PROMPT = """你是金融同业案例地理标注助手. 根据案例主体/摘要判断发生地国家.
只返回 JSON: {{"country": "中国"}} 或跨境 {{"country": ["中国","美国"]}}.
可用值示例: 中国/美国/英国/新加坡/日本/香港/全球.
主体: {subject}
对手方: {counterparty}
摘要: {summary}
"""

_LLM_GAP_SEC = 0.3


def _candidates(*, source: str | None, limit: int | None, include_with_geo: bool = False) -> list[dict]:
    conn = get_db()
    sql = """
        SELECT c.case_id, c.subject, c.counterparty, c.summary, c.evidence_snippet,
               a.source AS source
        FROM cases c
        JOIN articles a ON a.id = c.article_id
        WHERE c.subject != '__processing__'
          AND c.case_lifecycle != 'archived'
    """
    params: list = []
    if source:
        sql += " AND a.source = ?"
        params.append(source)
    if not include_with_geo:
        sql += " AND NOT EXISTS (SELECT 1 FROM case_geography g WHERE g.case_id = c.case_id)"
    sql += " ORDER BY c.extracted_at DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _rule_geo(case: dict) -> list[dict]:
    return normalize_geos(
        None, subject=case.get("subject"), counterparty=case.get("counterparty"),
    )


def _llm_extract_geo(provider: str, model: str, case: dict) -> list[dict]:
    prompt = _GEO_PROMPT.format(
        subject=case.get("subject") or "",
        counterparty=case.get("counterparty") or "",
        summary=(case.get("summary") or "")[:300],
    )
    time.sleep(_LLM_GAP_SEC)
    raw = call_llm(provider, model, prompt, timeout=120)
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    country = data.get("country")
    geos = normalize_geos(
        country, subject=case.get("subject"), counterparty=case.get("counterparty"),
    )
    for g in geos:
        if country not in (None, "", []):
            g["source_type"] = "auto_llm"
    return geos


def _insert_geography(conn, case_id: str, geos: list[dict], *, overwrite: bool) -> int:
    now = datetime.now(timezone.utc).isoformat()
    if overwrite:
        conn.execute(
            "DELETE FROM case_geography WHERE case_id = ? AND source_type IN ('auto_rule','auto_llm')",
            (case_id,),
        )
    n = 0
    for g in geos:
        code = g.get("country_code")
        if not code:
            continue
        src = g.get("source_type") or "auto_rule"
        cur = conn.execute(
            """INSERT OR IGNORE INTO case_geography
                   (case_id, country_code, country_name, region, sub_region,
                    is_primary, source_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                case_id, code, g.get("country_name") or code,
                g.get("region") or "GLOBAL", g.get("sub_region"),
                1 if g.get("is_primary", 1) else 0, src, now,
            ),
        )
        if cur.rowcount > 0:
            n += 1
    return n


def run_extract_geo(args) -> int:
    overwrite = bool(getattr(args, "overwrite", False) or getattr(args, "overwrite_auto", False))
    dry_run = bool(getattr(args, "dry_run", False))

    rows = _candidates(
        source=getattr(args, "source", None),
        limit=getattr(args, "limit", None),
        include_with_geo=overwrite,
    )

    if dry_run:
        rule_n = sum(1 for r in rows if _rule_geo(r))
        by_source = Counter((r.get("source") or "?") for r in rows)
        print(f"[dry-run] 地理回填候选: {len(rows)} 条")
        print(f"  规则可命中: {rule_n}  需 LLM: {len(rows) - rule_n}")
        for s, c in by_source.most_common():
            print(f"  {s}: {c}")
        return 0

    if not rows:
        print("[extract geo] 无候选")
        return 0

    provider = model = None
    stats = {"total": len(rows), "ok": 0, "failed": 0, "rule": 0, "llm": 0, "inserted": 0}
    interrupted = {"flag": False}

    def _sigint(_sig, _frame):
        interrupted["flag"] = True
        print("\n[SIGINT] 中断, 处理完当前条后停止...", flush=True)

    signal.signal(signal.SIGINT, _sigint)

    t0 = time.time()
    for i, row in enumerate(rows, 1):
        if interrupted["flag"]:
            break
        try:
            geos = _rule_geo(row)
            if geos:
                stats["rule"] += 1
            else:
                if provider is None:
                    provider, model = resolve_active_model()
                geos = _llm_extract_geo(provider, model, row)
                stats["llm"] += 1
            conn = get_db()
            try:
                n = _insert_geography(conn, row["case_id"], geos, overwrite=overwrite)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            stats["inserted"] += n
            stats["ok"] += 1
        except Exception as exc:
            stats["failed"] += 1
            print(f"  [{i}] failed: {row['case_id']}: {exc}", flush=True)
        if i % 10 == 0 or i == len(rows) or interrupted["flag"]:
            print(
                f"  进度 {i}/{len(rows)} | 完成={stats['ok']} 规则={stats['rule']} "
                f"LLM={stats['llm']} 失败={stats['failed']}",
                flush=True,
            )

    el = time.time() - t0
    print(
        f"[extract geo] 完成: {stats['total']} 条, {el:.1f}s, "
        f"插入 {stats['inserted']} 条地理"
    )
    return 0
