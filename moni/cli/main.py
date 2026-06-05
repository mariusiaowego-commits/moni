"""moni CLI — python3 -m moni <subcmd>.

v1.0 subcommands:
    db stats          Show row count for each table
    db export <fmt>   Export data (fmt: json | csv)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..db.connection import close_db, get_db
from ..db.migrate import migrate
from ..fetch import run as fetch_run, fetch_all

PARSER = argparse.ArgumentParser(prog="moni", description="moni — 同业信源新闻库")
SUB = PARSER.add_subparsers(dest="subcmd", required=True)

# ── db subcommand ────────────────────────────────────────────────────────────

db_parser = SUB.add_parser("db", help="Database operations")
db_sub = db_parser.add_subparsers(dest="db_subcmd", required=True)

stats_parser = db_sub.add_parser("stats", help="Show row count per table")


def _table_counts(conn) -> dict[str, int]:
    tables = ["articles", "tag_definitions", "article_tags", "share_tasks"]
    out = {}
    for t in tables:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        out[t] = n
    return out


def cmd_stats() -> None:
    conn = get_db()
    counts = _table_counts(conn)
    for name, n in counts.items():
        print(f"  {name}: {n} rows")
    print(f"\nTotal: {sum(counts.values())} rows across {len(counts)} tables")


export_parser = db_sub.add_parser(
    "export", help="Export all tables",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    description="Export format: json | csv (v1.0 — no complex formats)"
)
export_parser.add_argument("fmt", choices=["json", "csv"], help="Export format")


def _export_json(conn) -> str:
    tables = ["articles", "tag_definitions", "article_tags", "share_tasks"]
    data = {}
    for t in tables:
        rows = conn.execute(f"SELECT * FROM {t}").fetchall()
        data[t] = [dict(r) for r in rows]
    return json.dumps(data, ensure_ascii=False, indent=2)


def _export_csv(conn) -> str:
    tables = ["articles", "tag_definitions", "article_tags", "share_tasks"]
    buf = io.StringIO()
    for t in tables:
        buf.write(f"\n===== {t} =====\n")
        rows = conn.execute(f"SELECT * FROM {t}").fetchall()
        if not rows:
            buf.write("(empty)\n")
            continue
        writer = csv.writer(buf)
        writer.writerow(rows[0].keys())
        writer.writerows(rows)
    return buf.getvalue()


def cmd_export(fmt: str) -> None:
    conn = get_db()
    if fmt == "json":
        print(_export_json(conn))
    else:
        print(_export_csv(conn), end="")


# ── fetch subcommand ───────────────────────────────────────────────────────────

fetch_parser = SUB.add_parser("fetch", help="Fetch articles from one or all sources")
fetch_parser.add_argument(
    "--source",
    dest="source_name",
    metavar="NAME",
    help="Source name (e.g. 36kr_rss). Omit to fetch all enabled sources.",
)


def cmd_fetch(source_name: str | None) -> None:
    if source_name:
        result = fetch_run(source_name)
        if result is None:
            print(f"  ERROR: no fetcher registered for {source_name!r} or source not due")
        else:
            _print_fetch_result(result)
    else:
        results = fetch_all()
        if not results:
            print("  No sources were due for fetching.")
        for result in results:
            _print_fetch_result(result)


def _print_fetch_result(result: dict) -> None:
    err = f"  ERROR: {result['error']}" if result["error"] else ""
    print(
        f"  [{result['source']}] "
        f"fetched={result['fetched']}  "
        f"parsed={result['parsed']}  "
        f"inserted={result['inserted']}  "
        f"parse_errors={result['parse_errors']}"
        + (f"  {err}" if err else "")
    )


# ── dispatch ─────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    args = PARSER.parse_args(argv)

    if args.subcmd == "db":
        # Auto-migrate on any db subcommand
        migrate()
        if args.db_subcmd == "stats":
            cmd_stats()
        elif args.db_subcmd == "export":
            cmd_export(args.fmt)
        close_db()
        return 0

    if args.subcmd == "fetch":
        cmd_fetch(getattr(args, "source_name", None))
        return 0

    PARSER.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
