"""回填 cases.case_action / case_action_source（纯规则，不调 LLM）.

存量 432 条是迁移 062 之前写入的：case_action_source 被补成 'default'，
且动作枚举当时缺「收购 / 重组 / 合作」等词，导致全部塌缩成 launch。

用法:
  python -m moni.cases.backfill_actions --db data/news.db            # dry-run
  python -m moni.cases.backfill_actions --db data/news.db --apply
  python -m moni.cases.backfill_actions --db data/news.db --ids MN-AI-2608-0004 --apply
"""

from __future__ import annotations

import argparse
import collections
import json
import sqlite3
from datetime import datetime, timezone

from moni.cases.normalize import normalize_case_action_with_source


def _rows(conn: sqlite3.Connection, ids: list[str] | None, limit: int | None) -> list[dict]:
    sql = """
        SELECT c.case_id, c.article_id, c.subject, c.case_type,
               c.case_action, c.case_action_source,
               c.summary, c.evidence_snippet
        FROM cases c
        WHERE c.case_lifecycle != 'archived'
          AND c.subject != '__processing__'
    """
    params: list = []
    if ids:
        sql += f" AND c.case_id IN ({','.join('?' * len(ids))})"
        params.extend(ids)
    sql += " ORDER BY c.extracted_at DESC, c.case_id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _context(row: dict) -> str:
    """与实时 normalize_case 同口径：只吃 summary + evidence，不拼标题."""
    return f"{row.get('summary') or ''} {row.get('evidence_snippet') or ''}".strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill case_action (rule-based)")
    parser.add_argument("--db", required=True, help="SQLite DB path")
    parser.add_argument("--ids", default=None, help="逗号分隔的 case_id")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--apply", action="store_true", help="写库（默认 dry-run）")
    parser.add_argument("--include-llm", action="store_true",
                        help="连 llm_enum / manual 来源的行一起重算（默认跳过）")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须 > 0")

    ids = [x.strip() for x in args.ids.split(",") if x.strip()] if args.ids else None
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        rows = _rows(conn, ids, args.limit)
        if not rows:
            print("[actions] 无候选")
            return 0

        plan = []
        before = collections.Counter(r["case_action"] for r in rows)
        after = collections.Counter()
        skipped_locked = 0
        for row in rows:
            if not args.include_llm and row["case_action_source"] in ("llm_enum", "manual"):
                skipped_locked += 1
                after[row["case_action"]] += 1
                continue
            action, source = normalize_case_action_with_source(None, context_text=_context(row))
            after[action] += 1
            if (action, source) != (row["case_action"], row["case_action_source"]):
                plan.append((row["case_id"], action, source))

        print(f"[actions] 候选 {len(rows)} 条，需改动 {len(plan)} 条")
        if skipped_locked:
            print(f"  跳过 llm_enum/manual {skipped_locked} 条（--include-llm 可覆盖）")
        print(f"  before {json.dumps(dict(before), ensure_ascii=False)}")
        print(f"  after  {json.dumps(dict(after), ensure_ascii=False)}")

        if not args.apply:
            for case_id, action, source in plan[:10]:
                print(f"  {case_id} → {action} ({source})")
            if len(plan) > 10:
                print(f"  … 其余 {len(plan) - 10} 条省略")
            return 0

        now = datetime.now(timezone.utc).isoformat()
        applied = 0
        conflicts = 0
        with conn:
            for case_id, action, source in plan:
                # UNIQUE(article_id, subject, case_type, case_action)：同文同主体
                # 已有目标动作时跳过，避免整批回滚。
                try:
                    conn.execute(
                        "UPDATE cases SET case_action = ?, case_action_source = ?,"
                        " updated_at = ? WHERE case_id = ?",
                        (action, source, now, case_id),
                    )
                    applied += 1
                except sqlite3.IntegrityError:
                    conflicts += 1
        print(f"[actions] 已写入 {applied} 条" + (f"，唯一键冲突跳过 {conflicts} 条" if conflicts else ""))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
