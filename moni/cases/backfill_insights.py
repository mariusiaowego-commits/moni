"""moni.cases.backfill_insights — 案例深度解读回填 CLI.

用法:
  python -m moni.cases.backfill_insights --db data/news.db                  # dry-run
  python -m moni.cases.backfill_insights --db data/news.db --limit 20 --apply --show
  python -m moni.cases.backfill_insights --db data/news.db --ids MN-AI-2601-0001 --apply
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import sqlite3
import time
from pathlib import Path

from moni.cases.insights import (
    PROMPT_VERSION,
    build_prompt,
    generate_insight,
    input_hash,
    record_failure,
    save_insight,
)

_LLM_GAP_SEC = 0.3
_QUOTA_RE = re.compile(r"\b429\b|quota|配额|rate.?limit", re.IGNORECASE)


def _candidates(
    conn: sqlite3.Connection,
    *,
    ids: list[str] | None,
    with_article: bool,
    max_article_chars: int,
) -> list[dict]:
    """取全部候选（不按 limit / 已有 insight 过滤，交由调用方按 hash 判定）."""
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
    sql += " ORDER BY c.extracted_at DESC, c.case_id DESC"
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
    parser.add_argument("--timeout", type=int, default=300, help="单条 LLM 超时秒数")
    parser.add_argument("--show", action="store_true", help="打印每条生成的 JSON")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="仅统计不调用 LLM (默认)")
    mode.add_argument("--apply", action="store_true", help="真实调用 LLM 并写库")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须 > 0")

    # 让 resolve_active_model / get_db 读的就是 --db 指向的库
    from moni.db.connection import set_db_path

    set_db_path(Path(args.db).expanduser().resolve())
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        ids = [x.strip() for x in args.ids.split(",") if x.strip()] if args.ids else None
        rows = _candidates(
            conn, ids=ids,
            with_article=args.with_article, max_article_chars=args.max_article_chars,
        )
        prompts = [build_prompt(row, max_article_chars=args.max_article_chars) for row in rows]
        hashes = [input_hash(p) for p in prompts]

        if not args.refresh:
            done = {
                (r["case_id"], r["input_hash"])
                for r in conn.execute(
                    "SELECT case_id, input_hash FROM case_insights"
                    " WHERE prompt_version = ? AND status = 'done'",
                    (PROMPT_VERSION,),
                )
            }
            keep = [
                i for i, row in enumerate(rows)
                if (row["case_id"], hashes[i]) not in done
            ]
            rows = [rows[i] for i in keep]
            prompts = [prompts[i] for i in keep]
            hashes = [hashes[i] for i in keep]
        if args.limit:
            rows, prompts, hashes = rows[: args.limit], prompts[: args.limit], hashes[: args.limit]

        if not rows:
            if ids:
                print("[insights] 指定 case_id 未匹配到候选（不存在 / 已归档 / 同输入已有 insight；重跑加 --refresh）")
            else:
                print("[insights] 无候选（都已有同输入 insight；需要重跑加 --refresh）")
            return 0

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
        stats = {"ok": 0, "failed": 0, "insufficient": 0, "remaining": 0}
        stop = {"flag": False}
        quota_blocked = False

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
                body, usage = generate_insight(
                    row, provider, model,
                    timeout=args.timeout,
                    max_article_chars=args.max_article_chars,
                    with_usage=True,
                )
                if not body:
                    raise ValueError("empty/invalid JSON reply")
                save_insight(
                    conn, case_id, body,
                    provider=provider, model=model,
                    prompt_hash=hashes[i - 1],
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                )
                if body.get("insufficient"):
                    stats["insufficient"] += 1
                stats["ok"] += 1
                if args.show:
                    print(f"  {case_id} {json.dumps(body, ensure_ascii=False)}", flush=True)
            except Exception as exc:
                if stop["flag"]:
                    # Ctrl-C 打断 call_llm：当前条不算失败，留给重跑
                    break
                if _QUOTA_RE.search(str(exc)):
                    quota_blocked = True
                    print(f"  [{i}] 上游 429/配额耗尽，提前停止: {exc}", flush=True)
                    break
                stats["failed"] += 1
                print(f"  [{i}] failed {case_id}: {exc}", flush=True)
                try:
                    record_failure(
                        conn, case_id,
                        provider=provider, model=model,
                        prompt_hash=hashes[i - 1], error=str(exc),
                    )
                except sqlite3.Error as db_exc:
                    print(f"  [{i}] 失败记录也未写库: {db_exc}", flush=True)
            if i % 5 == 0 or i == len(rows):
                el = time.time() - t0
                print(
                    f"  进度 {i}/{len(rows)} ok={stats['ok']} failed={stats['failed']} "
                    f"insufficient={stats['insufficient']} {el:.0f}s",
                    flush=True,
                )
            time.sleep(_LLM_GAP_SEC)
        stats["remaining"] = len(rows) - stats["ok"] - stats["failed"]
        print(f"[apply] done {json.dumps(stats, ensure_ascii=False)}")
        if quota_blocked:
            print(f"[apply] 配额阻断：还有 {stats['remaining']} 条未处理（恢复配额后重跑本命令）")
            return 2
        if stop["flag"]:
            print(f"[apply] 已中断：还有 {stats['remaining']} 条未处理（重跑本命令会续跑）")
            return 130
        return 0 if stats["failed"] == 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
