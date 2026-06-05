"""moni CLI — python3 -m moni <subcmd>.

v1.0 subcommands:
    db stats          Show row count for each table
    db export <fmt>   Export data (fmt: json | csv)
    cron start        Start the BackgroundScheduler (background daemon)
    cron stop         Stop the running scheduler
    cron status       Show scheduler running state
    fetch [--source NAME] [--dry-run] [--health]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..db.connection import close_db, get_db
from ..db.migrate import migrate
from ..fetch import fetch_all
from ..fetch.run import main as run_fetch

PARSER = argparse.ArgumentParser(prog="moni", description="moni — 同业信源新闻库")
SUB = PARSER.add_subparsers(dest="subcmd", required=True)

# ── db subcommand ────────────────────────────────────────────────────────────

db_parser = SUB.add_parser("db", help="Database operations")
db_sub = db_parser.add_subparsers(dest="db_subcmd", required=True)

stats_parser = db_sub.add_parser("stats", help="Show row count per table")

init_parser = db_sub.add_parser(
    "init",
    help="Run migrations and seed default LLM data (providers, models, settings)",
)


def cmd_init() -> None:
    migrate()
    from ..db.seed_llm import seed as seed_llm_seed
    seed_llm_seed()
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) as n FROM llm_providers"
    ).fetchone()
    print(f"\nllm_providers: {row['n']} row(s)")


seed_llm_parser = db_sub.add_parser(
    "seed-llm",
    help="Seed LLM providers, models, and settings (idempotent — safe to re-run)",
)


def cmd_seed_llm() -> None:
    from ..db.seed_llm import seed as seed_llm_seed
    seed_llm_seed()


def _table_counts(conn) -> dict[str, int]:
    tables = [
        "articles", "tag_definitions", "article_tags", "share_tasks",
        "llm_providers", "llm_models", "llm_settings",
    ]
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
    tables = [
        "articles", "tag_definitions", "article_tags", "share_tasks",
        "llm_providers", "llm_models", "llm_settings",
    ]
    data = {}
    for t in tables:
        rows = conn.execute(f"SELECT * FROM {t}").fetchall()
        data[t] = [dict(r) for r in rows]
    return json.dumps(data, ensure_ascii=False, indent=2)


def _export_csv(conn) -> str:
    tables = [
        "articles", "tag_definitions", "article_tags", "share_tasks",
        "llm_providers", "llm_models", "llm_settings",
    ]
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


# ── cron subcommand ──────────────────────────────────────────────────────────

cron_parser = SUB.add_parser("cron", help="Cron scheduler operations")
cron_sub = cron_parser.add_subparsers(dest="cron_subcmd", required=True)

cron_sub.add_parser("start", help="Start the BackgroundScheduler (background daemon)")
cron_sub.add_parser("stop", help="Stop the running scheduler")
cron_sub.add_parser("status", help="Show scheduler running state")


def cmd_cron_start() -> int:
    """Start the cron scheduler as a background daemon.

    Forks a child process that:
    1. Runs DB migration (so cron_runs/fetch_log tables exist).
    2. Starts the BackgroundScheduler and blocks forever.
    3. Writes its PID to data/.cron.pid.
    """
    from ..cron.scheduler import CronScheduler

    # Ensure schema is up to date
    migrate()

    pid_path = Path("data") / ".cron.pid"
    pid_path.parent.mkdir(parents=True, exist_ok=True)

    # Check for stale PID file
    if pid_path.exists():
        try:
            old_pid = int(pid_path.read_text().strip())
            os.kill(old_pid, 0)
            print(f"Scheduler already running (PID {old_pid}). Use 'moni cron stop' first.")
            return 1
        except (ValueError, OSError):
            pid_path.unlink()

    # Verify APScheduler is available
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        print("ERROR: apscheduler is not installed. Run: pip install apscheduler")
        return 1

    pid = os.fork()
    if pid == 0:
        # Child — daemon process
        os.setsid()
        # Detach from controlling terminal
        devnull = os.open(os.devnull, os.O_RDWR)
        os.dup2(devnull, 0)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        os.close(devnull)

        pid_path.write_text(str(os.getpid()))

        scheduler = CronScheduler()
        scheduler.start()

        # Block forever; shutdown is triggered by SIGTERM
        import time
        while True:
            time.sleep(86400)
    else:
        print(f"Scheduler started in background (PID {pid})")
        return 0


def cmd_cron_stop() -> int:
    """Stop the running cron scheduler via SIGTERM."""
    pid_path = Path("data") / ".cron.pid"
    if not pid_path.exists():
        print("Scheduler PID file not found. Is it running?")
        return 1

    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        pid_path.unlink()
        print(f"Sent SIGTERM to PID {pid}")
        return 0
    except (ValueError, OSError) as exc:
        print(f"Failed to stop scheduler: {exc}")
        pid_path.unlink()
        return 1


def cmd_cron_status() -> None:
    """Print scheduler status."""
    pid_path = Path("data") / ".cron.pid"
    if not pid_path.exists():
        print("Scheduler is NOT running (no PID file)")
        return

    try:
        pid_text = pid_path.read_text().strip()
        pid = int(pid_text)
        os.kill(pid, 0)
    except (ValueError, OSError) as exc:
        print(f"Scheduler PID file is stale — removing ({exc})")
        pid_path.unlink()
        return

    print(f"Scheduler is RUNNING (PID {pid})")

    # Show recent cron_runs summary
    try:
        migrate()
        conn = get_db()
        row = conn.execute(
            "SELECT COUNT(*) as total, "
            "SUM(success=1) as succeeded, "
            "SUM(success=0) as failed "
            "FROM cron_runs"
        ).fetchone()
        print(f"cron_runs: {row['total']} total, {row['succeeded']} succeeded, {row['failed']} failed")
    except Exception as exc:
        print(f"(Could not read cron_runs: {exc})")


# ── fetch subcommand ─────────────────────────────────────────────────────────────────

fetch_parser = SUB.add_parser("fetch", help="Fetch articles from one or all sources")
fetch_parser.add_argument(
    "--source",
    dest="source_name",
    metavar="NAME",
    help="Source name prefix (e.g. 36kr). Omit to fetch all enabled sources.",
)
fetch_parser.add_argument(
    "--dry-run",
    action="store_true",
    help="Fetch and parse but skip database insertion.",
)
fetch_parser.add_argument(
    "--health",
    action="store_true",
    help="Run health checks only, print results, and exit.",
)


def cmd_fetch(source_name: str | None, dry_run: bool, health: bool) -> None:
    """Delegate to moni.fetch.run.main() which handles all fetch CLI modes."""
    import sys as _sys

    argv: list[str] = []
    if source_name:
        argv.extend(["--source", source_name])
    if dry_run:
        argv.append("--dry-run")
    if health:
        argv.append("--health")

    exit_code = run_fetch(argv)
    raise SystemExit(exit_code)


# ── dispatch ─────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    args = PARSER.parse_args(argv)

    if args.subcmd == "db":
        migrate()
        if args.db_subcmd == "stats":
            cmd_stats()
        elif args.db_subcmd == "init":
            cmd_init()
        elif args.db_subcmd == "seed-llm":
            cmd_seed_llm()
        elif args.db_subcmd == "export":
            cmd_export(args.fmt)
        close_db()
        return 0

    if args.subcmd == "cron":
        if args.cron_subcmd == "start":
            return cmd_cron_start()
        elif args.cron_subcmd == "stop":
            return cmd_cron_stop()
        elif args.cron_subcmd == "status":
            cmd_cron_status()
            close_db()
            return 0

    if args.subcmd == "fetch":
        cmd_fetch(
            source_name=getattr(args, "source_name", None),
            dry_run=getattr(args, "dry_run", False),
            health=getattr(args, "health", False),
        )
        return 0

    PARSER.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
