"""moni.cases.backfill_insights — 案例深度解读回填 CLI.

用法:
  python -m moni.cases.backfill_insights --db data/news.db                  # dry-run
  python -m moni.cases.backfill_insights --db data/news.db --limit 20 --apply --show
  python -m moni.cases.backfill_insights --db data/news.db --ids MN-AI-2601-0001 --apply
"""

from __future__ import annotations

import argparse
import json
import signal
import sqlite3
import sys
import time

from moni.cases.insights import (
    PROMPT_VERSION,
    build_prompt,
    generate_insight,
    input_hash,
    save_insight,
)

_LLM_GAP_SEC = 0.3


def _candidates(
    conn: sqlite3.Connection,
    *,
    limit: int | None,
    ids: list[str] | None,
    refresh: bool,
    with_article: bool,
    max_article_chars: int,
) -> list[dict]:
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT c.case_id, c.subject, c.counterparty, c.amount,
               c.summary, c.evidence_snippet,
               e.industry_l1, cat.name AS category_name,
               a.raw_content AS article_raw, a.title AS article_title,
               (SELECT s.name FROM case_scenario_links l
                  JOIN case_scenarios s ON s.code = l.scenario_code
                 WHERE l.case_id = c.case_id ORDER BY l.is_primary DESC, l.rowid LIMIT 1) AS scenario_name
        FROM cases c
        LEFT JOIN articles a ON a.id = c.article_id
        LEFT JOIN enterprises e ON e.enterprise_id = c.subject_enterprise_id
        LEFT JOIN case_categories cat ON cat.code = c.case_category
        WHERE c.subject != '__processing__'
          AND c.case_lifecycle != 'archived'
    """
    params: list = []
    if ids:
        sql += f" AND c.case_id IN ({','.join('?' * len(ids))})"
        params.extend(ids)
    if not refresh:
        sql += (
            " AND NOT EXISTS (SELECT 1 FROM case_insights i"
            " WHERE i.case_id = c.case_id AND i.prompt_version = ? AND i.status = 'done')"
        )
        params.append(PROMPT_VERSION)
    sql += " ORDER BY c.extracted_at DESC, c.case_id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for row in rows:
        if with_article:
            raw = (row.pop("article_raw", None) or "").strip()
            title = (row.pop("article_title", None) or "").strip()
            row["article_text"] = f"{title}\n{raw}"[:max_article_chars] if (title or raw) else ""
        else:
            row.pop("article_raw", None)
            row.pop("article_title", None)
        metrics = conn.execute(
            "SELECT metric_type, value_num, unit FROM case_metrics WHERE case_id = ? LIMIT 4",
            (row["case_id"],),
        ).fetchall()
        row["metrics"] = [dict(m) for m in metrics]
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill case_insights (做法/风险/可复制点)")
    parser.add_argument("--db", required=True, help="SQLite DB path")
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少条")
    parser.add_argument("--ids", default=None, help="逗号分隔的 case_id")
    parser.add_argument("--refresh", action="store_true", help="已有 insight 也重跑")
    parser.add_argument("--with-article", action="store_true", help="把文章原文节选喂给模型")
    parser.add_argument("--max-article-chars", type=int, default=1200)
    parser.add_argument("--show", action="store_true", help="打印每条生成的 JSON")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="仅统计不调用 LLM (默认)")
    mode.add_argument("--apply", action="store_true", help="真实调用 LLM 并写库")
    args = parser.parse_args(argv)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        ids = [x.strip() for x in args.ids.split(",") if x.strip()] if args.ids else None
        rows = _candidates(
            conn, limit=args.limit, ids=ids, refresh=args.refresh,
            with_article=args.with_article, max_article_chars=args.max_article_chars,
        )
        if not rows:
            print("[insights] 无候选（都已有 insight；需要重跑加 --refresh）")
            return 0

        prompts = [build_prompt(row) for row in rows]
        approx_tokens = sum(len(p) // 2 for p in prompts)
        if not args.apply:
            print(
                f"[dry-run] 候选 {len(rows)} 条 | 预计 {len(rows)} 次 LLM 调用 "
                f"| 输入约 {approx_tokens} tokens | 粗算 {len(rows) * 1.5 / 60:.1f} 分钟"
            )
            return 0

        from moni.llm.client import resolve_active_model

        provider, model = resolve_active_model()
        print(f"[apply] provider={provider} model={model} candidates={len(rows)}")
        stats = {"ok": 0, "failed": 0, "insufficient": 0}
        stop = {"flag": False}

        def _sigint(_sig, _frame):
            stop["flag"] = True
            print("\n[SIGINT] 处理完当前条后停止...", flush=True)

        signal.signal(signal.SIGINT, _sigint)
        t0 = time.time()
        for i, row in enumerate(rows, 1):
            if stop["flag"]:
                break
            case_id = row["case_id"]
            try:
                body = generate_insight(row, provider, model)
                if not body:
                    raise ValueError("empty/invalid JSON reply")
                save_insight(
                    conn, case_id, body,
                    provider=provider, model=model,
                    prompt_hash=input_hash(prompts[i - 1]),
                )
                if body.get("insufficient"):
                    stats["insufficient"] += 1
                stats["ok"] += 1
                if args.show:
                    print(f"  {case_id} {json.dumps(body, ensure_ascii=False)}", flush=True)
            except Exception as exc:
                stats["failed"] += 1
                print(f"  [{i}] failed {case_id}: {exc}", flush=True)
            if i % 5 == 0 or i == len(rows):
                el = time.time() - t0
                print(
                    f"  进度 {i}/{len(rows)} ok={stats['ok']} failed={stats['failed']} "
                    f"insufficient={stats['insufficient']} {el:.0f}s",
                    flush=True,
                )
            time.sleep(_LLM_GAP_SEC)
        print(f"[apply] done {json.dumps(stats, ensure_ascii=False)}")
        return 0 if stats["failed"] == 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
