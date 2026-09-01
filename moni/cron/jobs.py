"""moni.cron.jobs — APScheduler job handlers.

Each public function here is a top-level cron job entry-point registered via
``config/schedule.yaml``.  The scheduler imports them by dotted path, so they
must live at the package level (not nested inside a class).

Job handlers
-----------
1. fetch_all_sources
       Calls ``moni.fetch.fetch_all()`` in a try/except per source so one
       failed source never blocks the others.  Logs a ``cron_runs`` row and
       one ``fetch_log`` row per source.

2. predict_tags_pending
       Finds articles that have no tags yet, renders the predict_tag prompt,
       calls the LLM, and writes the predicted tags to ``article_tags``.

Failure isolation
----------------
Every source fetch is wrapped independently.  A crashing fetcher logs 'failed'
to fetch_log but the loop continues.  The scheduler itself never re-raises.

CST timezone
------------
All wall-clock comparisons use ``CST`` (UTC+8, ``zoneinfo.ZoneInfo("Asia/Shanghai")``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..db.connection import get_db
from ..fetch import fetch_all
from ..fetch.base import _ensure_sources_loaded, _FETCHER_REGISTRY
from ..fetch.scheduler import Scheduler as FetchScheduler
from ..llm.client import ChatClient
from ..llm.prompts import render_predict_tag
from .run_history import CronRunHistory

logger = logging.getLogger(__name__)

# CST = UTC+8, used for all wall-clock display and comparisons
CST = ZoneInfo("Asia/Shanghai")


def _cst_now() -> datetime:
    """Return the current wall-clock time in CST."""
    return datetime.now(CST)


def fetch_all_sources() -> dict[str, Any] | None:
    """Fetch all enabled sources, with per-source failure isolation.

    Runs on a cron schedule (weekday 9:10 AM). On Monday, automatically
    sets date_from to cover Saturday+Sunday (weekend catch-up).

    Logs
    ----
    - One ``cron_runs`` row with job_id ``fetch_all_sources``.
    - One ``fetch_log`` row per source (including failed ones).

    Returns
    -------
    None (the APScheduler integration does not use the return value).
    """
    job_id = "fetch_all_sources"
    triggered_at = _cst_now().isoformat()
    history = CronRunHistory()

    logger.info("[%s] job started at %s", job_id, triggered_at)

    # ── Monday weekend catch-up ──────────────────────────────────────────
    # On Monday, set date_from to Saturday so we fetch weekend articles too.
    now = _cst_now()
    date_from: str | None = None
    if now.weekday() == 0:  # Monday = 0
        saturday = now - timedelta(days=2)
        date_from = saturday.strftime("%Y-%m-%d")
        logger.info("[%s] Monday detected — weekend catch-up from %s", job_id, date_from)

    # Pull per-source configs from the fetch Scheduler
    fetch_scheduler = FetchScheduler()
    enabled = list(fetch_scheduler.enabled_sources())
    if not enabled:
        logger.info("[%s] no enabled sources — nothing to do", job_id)
        finished_at = _cst_now().isoformat()
        history.log_run(job_id, triggered_at=triggered_at, finished_at=finished_at,
                        success=True, new_articles=0, sources_run=0)
        return None

    # _ensure_sources_loaded must be called before iterating _FETCHER_REGISTRY
    _ensure_sources_loaded()

    total_inserted = 0
    sources_run = 0
    sources_failed = 0

    # Create ONE cron_runs row for the whole job; record its rowid so we can
    # write per-source fetch_log entries linked to it.
    cron_run_rowid: int = 0
    try:
        cron_run_rowid = history.log_run(
            job_id,
            triggered_at=triggered_at,
            finished_at=_cst_now().isoformat(),
            success=True,  # will be updated below
            error=None,
            new_articles=0,
            sources_run=0,
        )
    except Exception as exc:
        logger.error("[%s] Failed to create cron_runs row: %s", job_id, exc)
        cron_run_rowid = 0

    for cfg in enabled:
        source_name: str = cfg["source_name"]

        # ── Per-source failure isolation ──────────────────────────────────
        fetched = parsed = inserted = parse_errors = 0
        cleaned = filtered = 0
        dropped_blacklist = dropped_whitelist = dropped_tag_filter = 0
        filters_applied = tags_used = deduped = 0
        error: str | None = None

        try:
            if source_name not in _FETCHER_REGISTRY:
                raise RuntimeError(f"No fetcher registered for source {source_name!r}")

            fetcher_cls = _FETCHER_REGISTRY[source_name]
            fetcher = fetcher_cls()

            # Set date_from for Monday weekend catch-up
            if date_from:
                fetcher.date_from = date_from

            result: dict[str, Any] = fetcher.run()

            fetched = result.get("fetched", 0)
            parsed = result.get("parsed", 0)
            inserted = result.get("inserted", 0)
            parse_errors = result.get("parse_errors", 0)
            cleaned = result.get("cleaned", 0)
            filtered = result.get("filtered", 0)
            dropped_blacklist = result.get("dropped_blacklist", 0)
            dropped_whitelist = result.get("dropped_whitelist", 0)
            dropped_global_blacklist = result.get("dropped_global_blacklist", 0)
            dropped_global_whitelist = result.get("dropped_global_whitelist", 0)
            dropped_tag_filter = result.get("dropped_tag_filter", 0)
            filters_applied = result.get("filters_applied", 0)
            global_filters_applied = result.get("global_filters_applied", 0)
            tags_used = result.get("tags_used", 0)
            error = result.get("error")

            # Calculate deduped: articles that were parsed but not inserted
            # and not filtered out (i.e., already existed in DB)
            dropped_total = dropped_blacklist + dropped_whitelist + dropped_tag_filter
            deduped = max(0, parsed - inserted - dropped_total)

        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.error("[%s] fetch failed: %s", source_name, error)
            sources_failed += 1

        # Always mark fetched so the interval timer resets, even on failure
        try:
            fetch_scheduler.mark_fetched(source_name)
        except Exception as exc:
            logger.warning("[%s] mark_fetched() failed: %s", source_name, exc)

        # Record the per-source result under the shared cron_run_rowid
        if cron_run_rowid and error is None:
            sources_run += 1
            total_inserted += inserted

        if cron_run_rowid:
            try:
                history.log_fetch(
                    cron_run_rowid=cron_run_rowid,
                    source=source_name,
                    fetched=fetched,
                    parsed=parsed,
                    inserted=inserted,
                    parse_errors=parse_errors,
                    cleaned=cleaned,
                    filtered=filtered,
                    dropped_blacklist=dropped_blacklist,
                    dropped_whitelist=dropped_whitelist,
                    dropped_global_blacklist=dropped_global_blacklist,
                    dropped_global_whitelist=dropped_global_whitelist,
                    dropped_tag_filter=dropped_tag_filter,
                    filters_applied=filters_applied,
                    global_filters_applied=global_filters_applied,
                    tags_used=tags_used,
                    deduped=deduped,
                    error=error,
                )
            except Exception as log_exc:
                logger.error("[%s] Failed to write fetch_log: %s", source_name, log_exc)

        if error is None:
            logger.info("[%s] done — inserted=%d new articles", source_name, inserted)
        else:
            logger.warning("[%s] status=failed — scheduler continues", source_name)

    finished_at = _cst_now().isoformat()
    overall_success = sources_failed == 0

    # Update the cron_runs row with final totals
    if cron_run_rowid:
        try:
            from ..db.connection import get_db
            conn = get_db()
            conn.execute(
                """
                UPDATE cron_runs
                SET finished_at=?, success=?, error=?,
                    new_articles=?, sources_run=?
                WHERE id=?
                """,
                (
                    finished_at,
                    1 if overall_success else 0,
                    None if overall_success else f"{sources_failed} source(s) failed",
                    total_inserted,
                    sources_run,
                    cron_run_rowid,
                ),
            )
            conn.commit()
        except Exception as exc:
            logger.error("[%s] Failed to update cron_runs row: %s", job_id, exc)

    logger.info(
        "[%s] finished at %s — sources_run=%d inserted=%d failed=%d",
        job_id, finished_at, sources_run, total_inserted, sources_failed,
    )
    return None


def predict_tags_pending(
    *,
    batch_size: int = 10,
    max_articles: int | None = None,
) -> dict[str, Any] | None:
    """Tag untagged articles with LLM-predicted tags.

    Finds articles in the DB that have no entries in ``article_tags``,
    renders the ``predict_tag`` prompt for each, calls the LLM, and
    upserts the returned tag(s) into ``article_tags`` with ``predicted=1``.

    Parameters
    ----------
    batch_size : int
        Number of articles to process per cron run (default 10).
        Keeps a single job run short even when many articles are pending.
    max_articles : int | None
        Hard cap on total articles processed (default None = unlimited).
        Useful for testing or rate-limiting.

    Logging
    -------
    Writes one ``cron_runs`` row with job_id ``predict_tags_pending``.
    Writes per-article fetch_log entries (source='predict_tags').

    Returns
    -------
    None (APScheduler does not use the return value).
    """
    job_id = "predict_tags_pending"
    triggered_at = _cst_now().isoformat()
    history = CronRunHistory()

    logger.info("[%s] job started at %s", job_id, triggered_at)

    conn = get_db()

    # ── Collect available tags ─────────────────────────────────────────────
    tag_rows = conn.execute(
        "SELECT name FROM tag_definitions WHERE is_active = 1"
    ).fetchall()
    available_tags = [row["name"] for row in tag_rows]

    if not available_tags:
        logger.warning("[%s] No active tags defined in tag_definitions — nothing to do", job_id)
        finished_at = _cst_now().isoformat()
        history.log_run(job_id, triggered_at=triggered_at, finished_at=finished_at,
                        success=True, new_articles=0, sources_run=0)
        return None

    # ── Find untagged articles ─────────────────────────────────────────────
    query = """
        SELECT a.id, a.title, a.url, a.raw_content, a.content
        FROM articles a
        LEFT JOIN article_tags at ON at.article_id = a.id
        WHERE at.article_id IS NULL
        ORDER BY a.fetched_at DESC
        LIMIT ?
    """
    limit = (max_articles if max_articles is not None else batch_size)
    article_rows = conn.execute(query, (limit,)).fetchall()

    if not article_rows:
        logger.info("[%s] No pending articles — nothing to do", job_id)
        finished_at = _cst_now().isoformat()
        history.log_run(job_id, triggered_at=triggered_at, finished_at=finished_at,
                        success=True, new_articles=0, sources_run=0)
        return None

    logger.info("[%s] %d article(s) pending tagging", job_id, len(article_rows))

    # ── LLM client ─────────────────────────────────────────────────────────
    try:
        llm = ChatClient.get_instance()
    except Exception as exc:
        logger.error("[%s] ChatClient unavailable: %s — aborting", job_id, exc)
        finished_at = _cst_now().isoformat()
        history.log_run(job_id, triggered_at=triggered_at, finished_at=finished_at,
                        success=False, error=str(exc), new_articles=0, sources_run=0)
        return None

    processed = 0
    errors = 0
    now_cst = _cst_now()

    for row in article_rows:
        article_id: str = row["id"]
        article: dict[str, Any] = {
            "title": row["title"],
            "url": row["url"],
            "raw_content": row["raw_content"],
            "content": row["content"],
        }

        try:
            prompt = render_predict_tag(article, available_tags)
            raw_reply = llm.chat(
                prompt,
                model="claude-3-5-sonnet-latest",
                max_tokens=256,
                temperature=0.3,
            )
        except Exception as exc:
            logger.warning("[%s] LLM call failed for article %s: %s — skipping",
                           job_id, article_id, exc)
            errors += 1
            continue

        # Parse the comma-separated tag list from the LLM reply
        predicted_names = _parse_tags_from_reply(raw_reply)

        if not predicted_names:
            logger.debug("[%s] No tags predicted for article %s", job_id, article_id)
            # Still record that we processed it (with no tags added)
            processed += 1
            continue

        # Upsert each predicted tag
        for tag_name in predicted_names:
            if tag_name not in available_tags:
                logger.debug("[%s] Predicted tag %r not in tag_definitions — skipping",
                             job_id, tag_name)
                continue
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO article_tags
                        (article_id, tag_name, predicted, created_at)
                    VALUES (?, ?, 1, ?)
                    """,
                    (article_id, tag_name, now_cst.isoformat()),
                )
            except Exception as exc:
                logger.warning("[%s] Failed to insert tag %r for article %s: %s",
                               job_id, tag_name, article_id, exc)

        conn.commit()
        processed += 1
        logger.debug("[%s] Tagged article %s with %d tag(s): %s",
                     job_id, article_id, len(predicted_names), predicted_names)

    finished_at = _cst_now().isoformat()
    success = errors == 0

    try:
        history.log_run(
            job_id,
            triggered_at=triggered_at,
            finished_at=finished_at,
            success=success,
            error=f"{errors} article errors" if errors else None,
            new_articles=processed,
            sources_run=processed,
        )
    except Exception as exc:
        logger.error("[%s] Failed to write cron_runs row: %s", job_id, exc)

    logger.info(
        "[%s] finished at %s — processed=%d errors=%d",
        job_id, finished_at, processed, errors,
    )
    return None


def _parse_tags_from_reply(reply: str) -> list[str]:
    """Extract a list of tag names from an LLM text reply.

    Handles comma-separated, newline-separated, and bullet-list formats.
    Strips whitespace and ignores empty entries.
    """
    # Replace bullets, newlines, and commas with a common separator
    normalized = reply.replace("•", ",").replace("\n", ",").replace("，", ",")
    names = []
    for part in normalized.split(","):
        name = part.strip().strip("\"' ·•*-")
        if name:
            names.append(name)
    return names


# ── 行业事件抽取 ──────────────────────────────────────────────────────


def extract_events_pending(
    *,
    batch_size: int = 20,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any] | None:
    """为新入库的文章批量抽取行业事件（F5 行业事件日历）。

    找到还没有 events 记录的文章，调用 LLM 抽取事件，
    经过 normalize → insert → state-transition 三层管道写入 DB。

    Parameters
    ----------
    batch_size : int
        每批处理的文章数（默认 20）。
    provider : str | None
        LLM provider 名称 (缺省解析激活模型, v1.0.35.19 不再硬编码 minimax-cn)。
    model : str | None
        LLM 模型 ID。

    Logging
    -------
    Writes one ``cron_runs`` row with job_id ``extract_events_pending``.

    Returns
    -------
    None (APScheduler does not use the return value).
    """
    job_id = "extract_events_pending"
    triggered_at = _cst_now().isoformat()
    history = CronRunHistory()

    logger.info("[%s] job started at %s", job_id, triggered_at)

    # 记录 cron_runs
    cron_run_rowid: int = 0
    try:
        cron_run_rowid = history.log_run(
            job_id,
            triggered_at=triggered_at,
            finished_at=_cst_now().isoformat(),
            success=True,
            error=None,
            new_articles=0,
            sources_run=0,
        )
    except Exception as exc:
        logger.error("[%s] Failed to create cron_runs row: %s", job_id, exc)
        cron_run_rowid = 0

    try:
        from moni.events.pipeline import extract_events_batch

        result = extract_events_batch(
            batch_size=batch_size,
            provider=provider,
            model=model,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.error("[%s] batch extraction failed: %s", job_id, error)
        if cron_run_rowid:
            try:
                from ..db.connection import get_db as _get_db
                conn = _get_db()
                conn.execute(
                    """
                    UPDATE cron_runs
                    SET finished_at=?, success=0, error=?,
                        new_articles=0, sources_run=0
                    WHERE id=?
                    """,
                    (_cst_now().isoformat(), error, cron_run_rowid),
                )
                conn.commit()
            except Exception as log_exc:
                logger.error("[%s] Failed to update cron_runs row: %s", job_id, log_exc)
        return None

    finished_at = _cst_now().isoformat()
    success = result["failed"] == 0

    if cron_run_rowid:
        try:
            from ..db.connection import get_db as _get_db
            conn = _get_db()
            conn.execute(
                """
                UPDATE cron_runs
                SET finished_at=?, success=?, error=?,
                    new_articles=?, sources_run=?
                WHERE id=?
                """,
                (
                    finished_at,
                    1 if success else 0,
                    f"{result['failed']} failed" if result["failed"] else None,
                    result["events_extracted"],
                    result["processed"],
                    cron_run_rowid,
                ),
            )
            conn.commit()
        except Exception as log_exc:
            logger.error("[%s] Failed to update cron_runs row: %s", job_id, log_exc)

    logger.info(
        "[%s] finished at %s — processed=%d succeeded=%d "
        "failed=%d skipped=%d events=%d",
        job_id, finished_at,
        result["processed"], result["succeeded"],
        result["failed"], result["skipped"],
        result["events_extracted"],
    )
    return None
