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
    from ..db.seed_config import seed as seed_config_seed
    seed_config_seed()
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


seed_config_parser = db_sub.add_parser(
    "seed-config",
    help="Seed sources, source_tags, and source_keyword_filters (idempotent)",
)


def cmd_seed_config() -> None:
    from ..db.seed_config import seed as seed_config_seed
    seed_config_seed()


# ── clean subcommand (v1.0.3) ──────────────────────────────────────────────

clean_parser = SUB.add_parser(
    "clean-articles",
    help="Retroactively clean HTML in articles.raw_content (v1.0.3)",
)


def cmd_clean_articles() -> None:
    """Run ArticleRepository.clean_all_html() and print stats.

    Walks every article whose raw_content contains '<' and rewrites it
    with plain-text output from moni.fetch.cleaner.extract_text.
    Idempotent.
    """
    from ..db.repository import ArticleRepository

    repo = ArticleRepository()
    stats = repo.clean_all_html()
    print(
        f"clean-articles: scanned={stats['scanned']}, "
        f"updated={stats['updated']}, unchanged={stats['unchanged']}"
    )


# ── layout-articles (v1.0.10 offline body layout) ────────────────────────────

layout_parser = SUB.add_parser(
    "layout-articles",
    help=(
        "Fill articles.brief_body_md for offline HTML reading "
        "(local layout by default; --agent for hermes upgrade)"
    ),
)
layout_parser.add_argument(
    "--limit", type=int, default=100, help="Max articles missing layout (default 100)"
)
layout_parser.add_argument(
    "--agent",
    action="store_true",
    help="Use hermes agent layout (slower); falls back to local on timeout",
)


def cmd_layout_articles(limit: int = 100, use_agent: bool = False) -> None:
    """Backfill brief_body_md for articles that lack offline layout.

    New inserts already get local layout in ArticleRepository.dedupe_insert.
    This CLI covers history + optional agent quality upgrade.
    """
    from ..share.enrichment import layout_pending_bodies

    stats = layout_pending_bodies(limit=limit, use_agent=use_agent)
    mode = "agent" if use_agent else "local"
    print(
        f"layout-articles ({mode}): scanned={stats.get('scanned', 0)}, "
        f"updated={stats.get('updated', 0)}, skipped={stats.get('skipped', 0)}, "
        f"failed={stats.get('failed', 0)}"
    )


# ── backfill-publisher subcommand (v1.0.5.7) ─────────────────────────────────

backfill_publisher_parser = SUB.add_parser(
    "backfill-publisher",
    help=(
        "Retroactively extract publisher from title prefixes for "
        "articles where it's NULL/empty (v1.0.5.7).  Applies to "
        "WeChatRSS-sourced items with titles like ``[公众号名] 文章``."
    ),
)


def cmd_backfill_publisher() -> None:
    """Walk all articles with empty publisher, try title prefix regex.

    Updates ``articles.publisher`` when the regex extracts a non-empty
    name.  Idempotent — running it twice in a row produces no
    second-pass changes (because publisher is no longer NULL).

    The implementation lives in the fetcher module to keep the
    extraction logic in one place (single source of truth).  We
    import it lazily so the CLI doesn't pay the wechat_rss import
    cost for unrelated subcommands.
    """
    from ..fetch.sources.wechat_rss import _extract_publisher_from_title
    from ..db.connection import get_db

    conn = get_db()
    candidates = conn.execute(
        "SELECT id, title FROM articles "
        "WHERE publisher IS NULL OR TRIM(publisher) = ''"
    ).fetchall()
    scanned = len(candidates)
    updated = 0
    unchanged = 0
    for row in candidates:
        extracted = _extract_publisher_from_title(row["title"] or "")
        if extracted:
            conn.execute(
                "UPDATE articles SET publisher = ? WHERE id = ?",
                (extracted, row["id"]),
            )
            updated += 1
        else:
            unchanged += 1
    conn.commit()
    print(
        f"backfill-publisher: scanned={scanned}, "
        f"updated={updated}, unchanged={unchanged}"
    )


# ── xwatch subcommand (v1.0.11) ────────────────────────────────────────────

xwatch_parser = SUB.add_parser("xwatch", help="X 关键词监控工具 (v1.0.11+)")
xwatch_sub = xwatch_parser.add_subparsers(dest="xwatch_subcmd", required=True)
ingest_parser = xwatch_sub.add_parser(
    "ingest",
    help="按 URL 或 tweet id 收录单条推文（无 DDG、立即去重写入）。",
)
ingest_parser.add_argument(
    "url_or_id",
    help="https://x.com/.../status/<id> 或纯数字 tweet id",
)
ingest_parser.add_argument(
    "--dry-run",
    action="store_true",
    help="只解析推文并按 tag 命中检查，不写库。",
)
health_parser = xwatch_sub.add_parser(
    "health",
    help="xwatch 健康检查：syndication + DDG + accounts/被动指标 (v1.0.12)。",
)
following_parser = xwatch_sub.add_parser(
    "following",
    help="Channel C：抓 Following 时间线 (CDP Profile 6)。",
)
following_parser.add_argument(
    "--max-posts",
    type=int,
    default=100,
    help="最多抓多少条（默认 100，可到 300）。",
)
following_parser.add_argument(
    "--max-scrolls",
    type=int,
    default=40,
    help="最大滚动次数（默认 40）。",
)
following_parser.add_argument(
    "--dry-run",
    action="store_true",
    help="只打印抓到的帖，不写库。",
)
following_parser.add_argument(
    "--ingest",
    action="store_true",
    help="写入 articles（走 tag 门控 + dedupe）。与 --dry-run 互斥时 ingest 优先。",
)
chrome_stop_parser = xwatch_sub.add_parser(
    "chrome-stop",
    help="只停 Profile 6 / 端口 9222 的 Chrome（修复 Dock 点不开）。不影响日常 Chrome。",
)


def cmd_xwatch_ingest(url_or_id: str, dry_run: bool = False) -> int:
    """Ingest one tweet by URL or id; dedupe_insert if it passes tag filter.

    Exit codes
    ----------
    * 0  —  ingested OK, OR skipped (tag filter rejected with SKIP notice)
    * 1  —  bad input / syndication failed

    The tag filter mirrors ``apply_tag_keyword_filter`` semantics: only
    articles whose title+content hits a keyword for one of xwatch's
    bound tags (wechat / miniprogram) get persisted.  This keeps the
    ``moni xwatch ingest <whatever>`` UX aligned with the bulk fetcher
    — a tweet that wouldn't have made it through batch scraping won't
    slip in via the manual path either.
    """
    from ..fetch.xwatch_syndication import fetch_tweet, tweet_id_from_url, tweet_to_article

    tid = tweet_id_from_url(url_or_id)
    if not tid:
        print("ERROR: 无法解析推文 id — 期待 https://x.com/.../status/<id> 或纯数字 id")
        return 1

    data = fetch_tweet(tid)
    if not data:
        print(f"ERROR: syndication 抓取失败 (tweet id={tid}) — 可能已删除/受保护/限流")
        return 1

    art = tweet_to_article(data, fallback_url=url_or_id)
    if not art:
        print("ERROR: tweet 内容解析失败 (缺少 text/id_str)")
        return 1

    # Tag-keyword gate — shared with the batch fetcher and `following` so
    # the three xwatch ingestion paths cannot drift apart (v1.0.64).
    from ..fetch.xwatch_gate import describe_gate, gate_article, load_gate_keywords
    tag_keywords = load_gate_keywords()
    if not tag_keywords:
        # Same pass-through as the batch fetcher — but never silently.
        print("WARN: xwatch 没有绑定任何带关键词的 tag — 闸门关闭 (check seed-config)")
    gate = gate_article(art, tag_keywords)

    if not gate.allowed:
        print(f"SKIP: {describe_gate(tag_keywords)} "
              f"(tweet id={tid}, url={art['url']!r})")
        return 0

    if dry_run:
        print(
            f"[dry-run]  would insert: title={art['title']!r}\n"
            f"            url={art['url']}\n"
            f"            publisher={art['publisher']!r}\n"
            f"            published_at={art['published_at']!r}\n"
            f"            matched_tags={gate.matched_tags}"
        )
        return 0

    from ..db.repository import ArticleRepository
    inserted = ArticleRepository().dedupe_insert([art], gate.keyword_hits)
    print(f"inserted={inserted} url={art['url']}")
    close_db()
    return 0


def cmd_xwatch_health() -> int:
    """Print dual-probe xwatch health (v1.0.12). Exit 0 if synd OK else 1."""
    from ..fetch.health import check_xwatch

    r = check_xwatch()
    icon = "✓" if r.ok else "✗"
    print(f"{icon} xwatch  ok={r.ok}  latency={r.latency_ms}ms")
    if r.error:
        print(f"  note: {r.error}")
    d = r.detail or {}
    synd = d.get("syndication") or {}
    ddg = d.get("ddg") or {}
    pas = d.get("passive") or {}
    print(
        f"  syndication: {'OK' if synd.get('ok') else 'FAIL'}"
        f"  ({synd.get('latency_ms')}ms, probe_id={synd.get('probe_id')})"
    )
    print(
        f"  ddg: status={ddg.get('status', '?')}"
        f"  urls={ddg.get('urls_found', '-')}"
        f"  circuit_open={(ddg.get('circuit') or {}).get('open')}"
    )
    print(
        f"  config: accounts={pas.get('accounts', '-')}"
        f"  queries={pas.get('queries', '-')}"
        f"  active={pas.get('is_active', '-')}"
    )
    print(
        f"  inventory: total={pas.get('article_count', '-')}"
        f"  last_48h={pas.get('articles_last_48h', '-')}"
        f"  last_fetched={pas.get('last_fetched_at', '-')}"
    )
    if pas.get("last_job"):
        j = pas["last_job"]
        print(
            f"  last_job: status={j.get('status')}"
            f"  articles_so_far={j.get('articles_so_far')}"
            f"  started={j.get('started_at')}"
        )
    return 0 if r.ok else 1


def cmd_xwatch_following(
    max_posts: int = 100,
    max_scrolls: int = 40,
    dry_run: bool = True,
    ingest: bool = False,
) -> int:
    """Channel C: scrape Following via CDP. Default dry-run print only."""
    from ..fetch.xwatch_following import (
        following_tweet_to_synd_dict,
        scrape_following,
    )
    from ..fetch.xwatch_syndication import tweet_to_article

    print(
        f"[xwatch following] max_posts={max_posts} max_scrolls={max_scrolls} "
        f"ingest={ingest and not dry_run}",
    )
    tweets = scrape_following(
        max_posts=max_posts,
        max_scrolls=max_scrolls,
        ensure=True,
        stop_chrome=True,  # Dock-safe: do not leave Profile 6 Chrome running
    )
    print(f"scraped={len(tweets)}")
    for t in tweets[:15]:
        text = (t.get("text") or "").replace("\n", " ")[:50]
        print(f"  @{t.get('account')} | {text}")
        print(f"    {t.get('url')}")
    if len(tweets) > 15:
        print(f"  ... +{len(tweets) - 15} more")

    if not ingest or dry_run:
        if not tweets:
            return 1
        return 0

    # Ingest with the same gate as cmd_xwatch_ingest and the batch fetcher.
    from ..db.repository import ArticleRepository
    from ..fetch.xwatch_gate import gate_article, load_gate_keywords

    tag_keywords = load_gate_keywords()
    if not tag_keywords:
        # Match the batch fetcher's pass-through, but say so out loud.
        print("WARN: xwatch 没有绑定任何带关键词的 tag — 闸门关闭 (check seed-config)")
    arts: list[dict] = []
    hits: list[dict] = []
    skipped = 0
    for tw in tweets:
        data = following_tweet_to_synd_dict(tw)
        if not data:
            continue
        art = tweet_to_article(data, fallback_url=tw.get("url"))
        if not art:
            continue
        gate = gate_article(art, tag_keywords)
        if not gate.allowed:
            skipped += 1
            continue
        arts.append(art)
        hits.extend(gate.keyword_hits)
    inserted = ArticleRepository().dedupe_insert(arts, hits) if arts else 0
    print(f"tag_pass={len(arts)} tag_skip={skipped} inserted={inserted}")
    close_db()
    return 0 if tweets else 1


def _table_counts(conn) -> dict[str, int]:
    tables = [
        "articles", "tag_definitions", "article_tags", "share_tasks",
        "llm_providers", "llm_models", "llm_settings",
        "sources", "source_tags", "source_keyword_filters",
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
        "sources", "source_tags", "source_keyword_filters",
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
        "sources", "source_tags", "source_keyword_filters",
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


# ── extract subcommand (v1.0.35.19) ───────────────────────────────────────────
# 批量提取案例/事件 (竞赛成果数据支撑 P1)。实现在 moni/cli/extract.py。

extract_parser = SUB.add_parser(
    "extract", help="批量提取案例/事件 (v1.0.35.19+): moni extract cases|events"
)
extract_sub = extract_parser.add_subparsers(dest="extract_kind", required=True)

for _kind in ("cases", "events"):
    _p = extract_sub.add_parser(_kind, help=f"批量提取 {_kind} (LLM 抽取 → normalize → 入库)")
    _p.add_argument("--source", dest="source", metavar="NAME", help="只提取指定信源")
    _p.add_argument("--limit", type=int, default=None, help="最多处理 N 篇 (默认全部候选)")
    _p.add_argument("--dry-run", action="store_true", help="只出候选报告, 不调用 LLM")
    _p.add_argument("--include-skipped", action="store_true", help="重新处理 skipped 的文章")
    _p.add_argument("--retry-failed", action="store_true", help="重新处理 failed 的文章")
    _p.add_argument("--after-id", dest="after_id", help="resume: 只处理 id > 此值的文章")
    _p.add_argument(
        "--min-content-len", type=int, default=100,
        help="raw_content 最短长度 (过滤无正文文章, 默认 100)",
    )

# 关键词回填子命令 (v1.0.36 sprint-18 P0)
kw_p = extract_sub.add_parser(
    "keywords",
    help="存量案例关键词回填 (LLM 抽取 → case_keywords, 只针对无关键词的案例)"
)
kw_p.add_argument("--source", dest="source", metavar="NAME", help="只回填指定信源的案例")
kw_p.add_argument("--limit", type=int, default=None, help="最多处理 N 条案例 (默认全部候选)")
kw_p.add_argument("--dry-run", action="store_true", help="只统计候选, 不调用 LLM")
kw_p.add_argument(
    "--overwrite-auto", action="store_true",
    help="覆盖已有 auto_llm 关键词 (manual_user 保留, 强制重抽)"
)

geo_p = extract_sub.add_parser(
    "geo",
    help="存量案例地理回填 (规则推断 + LLM → case_geography)",
)
geo_p.add_argument("--source", dest="source", metavar="NAME", help="只回填指定信源的案例")
geo_p.add_argument("--limit", type=int, default=None, help="最多处理 N 条案例")
geo_p.add_argument("--dry-run", action="store_true", help="只统计候选, 不写库")
geo_p.add_argument(
    "--overwrite", action="store_true",
    help="覆盖已有 auto_rule/auto_llm 地理 (manual_user 保留)",
)


def cmd_extract(args) -> int:
    """Dispatch to moni.cli.extract.run_extract / extract_keywords / extract_geo."""
    from moni.cli.extract import run_extract
    from moni.cli.extract_keywords import run_extract_keywords
    if args.extract_kind == "keywords":
        return run_extract_keywords(args)
    if args.extract_kind == "geo":
        from moni.cli.extract_geo import run_extract_geo
        return run_extract_geo(args)
    return run_extract(args.extract_kind, args)


# ── cases subcommand (v1.0.36 sprint-18, auto_verify 一键) ──────────────

def cmd_cases_verify_all(args) -> int:
    """auto-verify pending 案例按 confidence 阈值批量 verified.

    v1.0.35.19 auto_verify_threshold 默认 80 (agy 评审), 这条 CLI 让
    dad 在跑完新一批 LLM 抽取后一键审核, 不必逐条点 confirm.
    """
    from datetime import datetime, timezone
    from moni.db.connection import get_db

    conn = get_db()
    threshold = args.threshold
    base_where = (
        "review_status='pending' "
        "AND (subject != '__processing__' OR subject IS NULL) "
        "AND extraction_confidence >= ?"
    )

    if args.dry_run:
        row = conn.execute(
            f"SELECT COUNT(*) FROM cases WHERE {base_where}", (threshold,)
        ).fetchone()
        print(f"[dry-run] 将核验 confidence >= {threshold}: {row[0]} 条")
        return 0

    now_iso = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        f"UPDATE cases SET review_status='confirmed', case_lifecycle='verified', updated_at=? "
        f"WHERE {base_where}",
        (now_iso, threshold),
    )
    conn.commit()
    pending_after = conn.execute(
        "SELECT COUNT(*) FROM cases WHERE review_status='pending'"
    ).fetchone()[0]
    print(f"已核验: {cur.rowcount} 条 | 仍 pending: {pending_after} 条")
    return 0


cases_parser = SUB.add_parser("cases", help="案例管理 (auto_verify 等)")
cases_sub = cases_parser.add_subparsers(dest="cases_subcmd", required=True)
verify_all_p = cases_sub.add_parser(
    "verify-all", help="按 confidence 阈值批量 verified"
)
verify_all_p.add_argument(
    "--threshold", type=int, default=80, help="confidence >= 阈值 (默认 80)"
)
verify_all_p.add_argument(
    "--dry-run", action="store_true", help="只统计不写入"
)


# ── keyword-audit (v1.0.64) ──────────────────────────────────────────────────
# Read-only replay of the stored corpus through both the old substring rule
# and the current match rules.  Run this BEFORE trusting a keyword change.

keyword_audit_p = SUB.add_parser(
    "keyword-audit",
    help="只读: 用当前语料对比「旧子串规则」与「现规则」的命中差异",
)
keyword_audit_p.add_argument(
    "--source", dest="source", metavar="NAME",
    help="只审计某个信源 (默认全部; 自动套用该源的 tag 绑定与 title-only 规则)",
)
keyword_audit_p.add_argument(
    "--show-lost", dest="show_lost", type=int, default=10,
    help="列出多少篇「旧规则留下、新规则丢掉」的文章标题 (默认 10, 0=不列)",
)


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
        elif args.db_subcmd == "seed-config":
            cmd_seed_config()
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

    if args.subcmd == "extract":
        return cmd_extract(args)

    if args.subcmd == "cases":
        if args.cases_subcmd == "verify-all":
            return cmd_cases_verify_all(args)
        cases_parser.print_help()
        return 1

    if args.subcmd == "keyword-audit":
        from .keyword_audit import run_keyword_audit
        code = run_keyword_audit(args)
        close_db()
        return code

    if args.subcmd == "clean-articles":
        cmd_clean_articles()
        return

    if args.subcmd == "layout-articles":
        cmd_layout_articles(limit=args.limit, use_agent=args.agent)
        return
        close_db()
        return 0

    if args.subcmd == "backfill-publisher":
        cmd_backfill_publisher()
        close_db()
        return 0

    if args.subcmd == "xwatch":
        if args.xwatch_subcmd == "ingest":
            code = cmd_xwatch_ingest(
                url_or_id=args.url_or_id,
                dry_run=getattr(args, "dry_run", False),
            )
            close_db()
            return code
        if args.xwatch_subcmd == "health":
            code = cmd_xwatch_health()
            close_db()
            return code
        if args.xwatch_subcmd == "following":
            code = cmd_xwatch_following(
                max_posts=getattr(args, "max_posts", 100),
                max_scrolls=getattr(args, "max_scrolls", 40),
                dry_run=getattr(args, "dry_run", False),
                ingest=getattr(args, "ingest", False),
            )
            close_db()
            return code
        if args.xwatch_subcmd == "chrome-stop":
            from ..fetch.xwatch_following import stop_x_chrome
            n = stop_x_chrome()
            print(f"stopped {n} process(es) on port 9222 (Profile 6)")
            print("现在可点程序坞 Chrome 打开日常浏览器。登录 cookie 仍在 Profile 6 磁盘上。")
            return 0
        xwatch_parser.print_help()
        return 1

    PARSER.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
