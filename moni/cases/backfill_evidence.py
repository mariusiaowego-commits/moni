"""moni.cases.backfill_evidence — 存量案例的原文证据校验回填 (0 LLM).

逐条把 cases.evidence_snippet 放回 articles.raw_content 找位置：
命中则 evidence_verified=1 并写 evidence_offset；未命中保持 0/NULL。

用法:
  python -m moni.cases.backfill_evidence --db /path/to/news.db           # dry-run
  python -m moni.cases.backfill_evidence --db /path/to/news.db --apply   # 写库
"""

from __future__ import annotations

import argparse
from pathlib import Path

from moni.cases.evidence import locate_evidence
from moni.cases.backfill_common import backup_database, connect


def run_backfill(db_path: str | Path, *, apply: bool = False) -> dict[str, int]:
    conn = connect(db_path, apply=apply)
    try:
        rows = conn.execute(
            """
            SELECT c.case_id, c.evidence_snippet, a.raw_content
            FROM cases c
            LEFT JOIN articles a ON a.id = c.article_id
            WHERE c.evidence_snippet IS NOT NULL AND c.evidence_snippet != ''
              AND c.case_lifecycle != 'archived'
            """
        ).fetchall()
        verified = missing_article = not_found = 0
        updates: list[tuple[int, int | None, str]] = []
        for row in rows:
            if not row["raw_content"]:
                missing_article += 1
                continue
            ok, offset, _ = locate_evidence(row["evidence_snippet"], row["raw_content"])
            if ok:
                verified += 1
                updates.append((1, offset, row["case_id"]))
            else:
                not_found += 1
                updates.append((0, None, row["case_id"]))
        if apply and updates:
            conn.executemany(
                "UPDATE cases SET evidence_verified=?, evidence_offset=? WHERE case_id=?",
                updates,
            )
            conn.commit()
        return {
            "total": len(rows),
            "verified": verified,
            "not_found": not_found,
            "missing_article": missing_article,
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify case evidence quotes against articles")
    parser.add_argument("--db", required=True, help="SQLite DB path")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="仅统计不写库 (默认)")
    mode.add_argument("--apply", action="store_true", help="写入 DB")
    parser.add_argument("--no-backup", action="store_true", help="apply 前不自动备份")
    args = parser.parse_args(argv)

    if args.apply and not args.no_backup:
        print(f"backup: {backup_database(args.db, tag='backfill-evidence')}")

    stats = run_backfill(args.db, apply=args.apply)
    print(
        "total={total} verified={verified} not_found={not_found} "
        "missing_article={missing_article} mode={mode}".format(
            mode="apply" if args.apply else "dry-run", **stats
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
