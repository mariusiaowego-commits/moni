"""moni.cron.jobs — APScheduler job handlers.

Each public function here is a top-level cron job entry-point registered via
``config/schedule.yaml``.  The scheduler imports them by dotted path, so they
must live at the package level (not nested inside a class).

Job handlers
-----------
1. fetch_all_sources
       Runs its own per-source loop over ``Scheduler.batch_sources()`` in a
       try/except per source so one failed source never blocks the others.
       Logs a ``cron_runs`` row and one ``fetch_log`` row per source.

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

import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..db.connection import get_db
from ..fetch import fetch_all
from ..fetch.base import _ensure_sources_loaded, _FETCHER_REGISTRY
from ..fetch.scheduler import Scheduler as FetchScheduler
from ..fetch.source_gate import split_fetchable
from ..llm.client import call_llm, resolve_active_model
from ..llm.prompts import render_predict_tag
from .run_history import CronRunHistory

logger = logging.getLogger(__name__)

# CST = UTC+8, used for all wall-clock display and comparisons
CST = ZoneInfo("Asia/Shanghai")

#: In-process mutex guarding ``predict_tags_pending`` (P1-8).
#:
#: Two overlapping runs would double-charge the LLM and race on
#: ``article_tags`` writes.  This is a *process-local* guard only — it does
#: not protect against a second OS process running the same job (the
#: planned launchd daemon).  Cross-process exclusion needs the DB lease
#: table (``moni/db/leases.py``) and is out of scope here.
_PREDICT_TAGS_LOCK = threading.Lock()

#: Chain trigger: how many pending articles one post-fetch tagging run may take.
TAG_CHAIN_MAX_ARTICLES = 2000

#: Chain trigger: max pending articles per post-fetch case extraction run.
CASE_CHAIN_MAX_ARTICLES = 40

#: In-process mutex guarding ``extract_cases_pending`` (prevents concurrent case_id race).
_CASE_LOCK = threading.Lock()

#: Cap on tags accepted per article — ``predict_tag.md`` asks for 1-3.
_MAX_TAGS_PER_ARTICLE = 3

#: Regex for the ``"tags": [...]`` array inside a non-strict-JSON reply.
_TAGS_ARRAY_RE = re.compile(r'"tags"\s*:\s*\[(.*?)\]', re.DOTALL)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a JSON object from an LLM reply.

    Handles the three shapes we actually see from hermes:
    - bare JSON: ``{"tags": [...], "reasoning": "..."}``
    - fenced JSON: ```json ... ```
    - JSON embedded in prose (leading/trailing chatter)

    Returns ``None`` when nothing parseable is found — the caller then
    falls back to the regex / plain-text parsers.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clean_tag_names(names: list[str]) -> list[str]:
    """Normalise tag names: strip, drop empties, dedupe, cap at 3."""
    out: list[str] = []
    for raw in names:
        name = (raw or "").strip().strip("\"' ·•*-")
        if not name or name in out:
            continue
        out.append(name)
        if len(out) >= _MAX_TAGS_PER_ARTICLE:
            break
    return out


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
    started_at_dt = _cst_now()
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
    enabled = list(fetch_scheduler.batch_sources())
    # The UI switches in the ``sources`` table outrank the YAML list:
    # a source the user switched off (is_active=0) or hid (is_hidden=1)
    # must not run in the scheduled batch.
    enabled, skipped_sources = split_fetchable(enabled)
    for source_name, reason in skipped_sources:
        logger.info(
            "[%s] skip %s — %s", job_id, source_name,
            "信源已停用" if reason == "inactive" else "信源已隐藏",
        )
    if not enabled:
        logger.info("[%s] no enabled sources — nothing to do", job_id)
        finished_at = _cst_now().isoformat()
        history.log_run(job_id, triggered_at=triggered_at, finished_at=finished_at,
                        success=True, new_articles=0, sources_run=0,
                        extra={"skipped_sources": dict(skipped_sources)})
        return None

    # _ensure_sources_loaded must be called before iterating _FETCHER_REGISTRY
    _ensure_sources_loaded()

    total_inserted = 0
    sources_run = 0
    sources_failed = 0
    sources_extra: dict[str, dict[str, Any]] = {}

    # Create ONE cron_runs row for the whole job; record its rowid so we can
    # write per-source fetch_log entries linked to it.
    cron_run_rowid: int = 0
    try:
        cron_run_rowid = history.log_run(
            job_id,
            triggered_at=triggered_at,
            finished_at=_cst_now().isoformat(),
            success=False,  # provisional; finalised below
            error=None,
            new_articles=0,
            sources_run=0,
            # Marks the row as still running so the API reports "running"
            # instead of a fake success if this process dies mid-sweep.
            extra={"in_progress": True},
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
        dropped_global_blacklist = dropped_global_whitelist = 0
        filters_applied = tags_used = deduped = 0
        global_filters_applied = 0
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

            # BestBlogs-family fetchers expose their sweep counters here
            # (feeds_scanned / candidates_new / budget_capped ...).
            run_stats = getattr(fetcher, "run_stats", None)
            if isinstance(run_stats, dict) and run_stats:
                sources_extra[source_name] = dict(run_stats)

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
            if error is not None:
                # fetch() 内部的失败以 error 字段返回, 不抛异常; 不在这里
                # 计数的话 cron_runs 会把「全部源失败」记成 success=1。
                error = str(error)
                sources_failed += 1

            # Calculate deduped: articles that were parsed but not inserted
            # and not filtered out (i.e., already existed in DB)
            # NOTE: dropped_blacklist / dropped_whitelist 已含 global 子项
            # (filters.py 里 global+source 累加到同一计数器)。
            # dropped_global_* 是独立 breakdown, 别在这里再加进 dropped_total,
            # 否则会重复扣减 deduped。
            dropped_total = dropped_blacklist + dropped_whitelist + dropped_tag_filter
            deduped = max(0, parsed - inserted - dropped_total)

        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.error("[%s] fetch failed: %s", source_name, error)
            sources_failed += 1

        if source_name in sources_extra:
            sources_extra[source_name].update({
                "parsed": parsed,
                "inserted": inserted,
                "dropped_tag_filter": dropped_tag_filter,
            })

        # Always mark fetched so the interval timer resets, even on failure
        try:
            fetch_scheduler.mark_fetched(source_name)
        except Exception as exc:
            logger.warning("[%s] mark_fetched() failed: %s", source_name, exc)

        # Record the per-source result under the shared cron_run_rowid
        if cron_run_rowid:
            total_inserted += inserted
            if error is None:
                sources_run += 1

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
        extra: dict[str, Any] = {
            "duration_sec": round((_cst_now() - started_at_dt).total_seconds(), 1),
        }
        if skipped_sources:
            extra["skipped_sources"] = dict(skipped_sources)
        if sources_extra:
            extra["sources"] = sources_extra
        # One retry: if this UPDATE never lands, the row keeps in_progress and
        # does not read as success — but it would look "running" until the
        # next scheduler start. A transient lock is worth one more attempt.
        for attempt in (1, 2):
            try:
                conn = get_db()
                conn.execute(
                    """
                    UPDATE cron_runs
                    SET finished_at=?, success=?, error=?,
                        new_articles=?, sources_run=?, extra=?
                    WHERE id=?
                    """,
                    (
                        finished_at,
                        1 if overall_success else 0,
                        None if overall_success else f"{sources_failed} source(s) failed",
                        total_inserted,
                        sources_run,
                        json.dumps(extra, ensure_ascii=False),
                        cron_run_rowid,
                    ),
                )
                conn.commit()
                break
            except Exception as exc:
                logger.error(
                    "[%s] cron_runs update failed (attempt %d/2): %s",
                    job_id, attempt, exc,
                )
                if attempt == 1:
                    time.sleep(1)

    # ── Chain: tag the articles this run just inserted ───────────────────
    # Replaces the old 9:25 standalone cron entry.  Runs after the fetch row
    # is finalised so a slow LLM never makes the fetch look unfinished.
    if total_inserted > 0:
        _run_tag_chain(job_id=job_id, cron_run_rowid=cron_run_rowid)
        _run_case_chain(job_id=job_id, cron_run_rowid=cron_run_rowid)

    logger.info(
        "[%s] finished at %s — sources_run=%d inserted=%d failed=%d",
        job_id, finished_at, sources_run, total_inserted, sources_failed,
    )
    return None


def _pending_tag_count() -> int:
    """Articles with no ``article_tags`` row yet."""
    conn = get_db()
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM articles a
        LEFT JOIN article_tags at ON at.article_id = a.id
        WHERE at.article_id IS NULL
        """
    ).fetchone()
    return int(row[0] or 0)


def _run_tag_chain(*, job_id: str, cron_run_rowid: int) -> None:
    """Tag pending articles right after a fetch run; record the outcome."""
    if not _tag_model_configured():
        # Without a model the chained job would only write an error row.
        _write_tag_chain(cron_run_rowid, {"skipped": "no active tag model"})
        return

    chain: dict[str, Any] = {}
    try:
        pending_before = _pending_tag_count()
        chain["pending_before"] = pending_before
        if pending_before:
            predict_tags_pending(
                batch_size=min(pending_before, TAG_CHAIN_MAX_ARTICLES),
                max_articles=TAG_CHAIN_MAX_ARTICLES,
                triggered_by=job_id,
            )
        chain["pending_after"] = _pending_tag_count()
    except Exception as exc:
        logger.error("[%s] chained tagging failed: %s", job_id, exc)
        chain["error"] = f"{type(exc).__name__}: {exc}"

    _write_tag_chain(cron_run_rowid, chain)


def _case_model_configured() -> bool:
    try:
        resolve_active_model()
        return True
    except Exception:
        return False


def _write_case_chain(cron_run_rowid: int, chain: dict[str, Any]) -> None:
    if not cron_run_rowid:
        return
    try:
        conn = get_db()
        conn.execute(
            """
            UPDATE cron_runs
            SET extra = json_set(COALESCE(extra, '{}'), '$.case_chain', json(?))
            WHERE id = ?
            """,
            (json.dumps(chain, ensure_ascii=False), cron_run_rowid),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("failed to record case chain: %s", exc)


def _run_case_chain(*, job_id: str, cron_run_rowid: int) -> None:
    """Extract cases for pending articles right after fetch+tag; record outcome."""
    if not _case_model_configured():
        _write_case_chain(cron_run_rowid, {"skipped": "no active case model"})
        return

    chain: dict[str, Any] = {}
    try:
        res = extract_cases_pending(
            batch_size=CASE_CHAIN_MAX_ARTICLES,
            triggered_by=job_id,
        )
        if res is not None:
            chain.update(res)
    except Exception as exc:
        logger.error("[%s] chained case extraction failed: %s", job_id, exc)
        chain["error"] = f"{type(exc).__name__}: {exc}"

    _write_case_chain(cron_run_rowid, chain)


def _tag_model_configured() -> bool:
    try:
        resolve_active_model("active_tag_model_id")
        return True
    except Exception:
        return False


def _write_tag_chain(cron_run_rowid: int, chain: dict[str, Any]) -> None:
    if not cron_run_rowid:
        return
    try:
        conn = get_db()
        conn.execute(
            """
            UPDATE cron_runs
            SET extra = json_set(COALESCE(extra, '{}'), '$.tag_chain', json(?))
            WHERE id = ?
            """,
            (json.dumps(chain, ensure_ascii=False), cron_run_rowid),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("failed to record tag chain: %s", exc)


def predict_tags_pending(
    *,
    batch_size: int = 10,
    max_articles: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    llm_call_fn: Any = None,
    triggered_by: str | None = None,
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
    provider : str | None
        hermes provider name.  Default None → resolved from
        ``llm_settings.active_tag_model_id`` (falls back to the summary
        model).  v1.0.47: no longer hardcodes a model.
    model : str | None
        hermes model id, same resolution rules as ``provider``.
    llm_call_fn : Any
        Optional callable ``fn(prompt: str) -> str`` used instead of the
        real LLM (test injection).
    triggered_by : str | None
        Job id that chained this run (e.g. ``fetch_all_sources``).  Stored
        in ``cron_runs.extra`` so a chained tick is distinguishable from a
        manual/scheduled one.

    Logging
    -------
    Writes one ``cron_runs`` row with job_id ``predict_tags_pending`` —
    including the re-entrancy skip path, so a skipped tick stays visible
    in the run history.

    Returns
    -------
    None (APScheduler does not use the return value).
    """
    job_id = "predict_tags_pending"
    triggered_at = _cst_now().isoformat()
    history = CronRunHistory()
    run_extra = {"triggered_by": triggered_by} if triggered_by else None

    logger.info("[%s] job started at %s", job_id, triggered_at)

    # ── Re-entrancy guard (P1-8) ─────────────────────────────────────────
    if not _PREDICT_TAGS_LOCK.acquire(blocking=False):
        logger.warning(
            "[%s] another run is already in progress — skipping this tick", job_id
        )
        # v1.0.47.1 (audit F1): this early return used to bypass
        # ``cron_runs`` entirely, leaving a silent hole in the run history.
        try:
            history.log_run(
                job_id,
                triggered_at=triggered_at,
                finished_at=_cst_now().isoformat(),
                # A skipped tick is a normal no-op, consistent with the
                # other "nothing to do" branches.  ``error`` remains the
                # machine-readable marker that distinguishes the skip.
                success=True,
                error="skipped: another run already in progress",
                new_articles=0,
                sources_run=0,
                extra=run_extra,
            )
        except Exception as exc:
            logger.error("[%s] Failed to write skipped cron_runs row: %s", job_id, exc)
        return None

    try:
        return _predict_tags_pending_locked(
            job_id=job_id,
            triggered_at=triggered_at,
            history=history,
            batch_size=batch_size,
            max_articles=max_articles,
            provider=provider,
            model=model,
            llm_call_fn=llm_call_fn,
            run_extra=run_extra,
        )
    finally:
        _PREDICT_TAGS_LOCK.release()


def _predict_tags_pending_locked(
    *,
    job_id: str,
    triggered_at: str,
    history: CronRunHistory,
    batch_size: int,
    max_articles: int | None,
    provider: str | None,
    model: str | None,
    llm_call_fn: Any,
    run_extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Body of ``predict_tags_pending`` — runs with the job mutex held."""
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
                        success=True, new_articles=0, sources_run=0, extra=run_extra)
        return None

    # ── Find untagged articles ─────────────────────────────────────────────
    # v1.0.47: dropped ``a.content`` — the articles table has no such
    # column (the only "content" in schema.sql is the FTS5 external-content
    # mapping ``content='articles'``).  Selecting it raised
    # "no such column: a.content" on every single run, so this job had
    # never reached the LLM at all.
    query = """
        SELECT a.id, a.title, a.url, a.raw_content
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
                        success=True, new_articles=0, sources_run=0, extra=run_extra)
        return None

    logger.info("[%s] %d article(s) pending tagging", job_id, len(article_rows))

    # ── Model resolution ───────────────────────────────────────────────────
    # v1.0.47: was ``ChatClient.get_instance().chat(prompt, model="claude-3-5-
    # sonnet-latest", max_tokens=256, temperature=0.3)``.  The deprecated
    # shim silently dropped every kwarg, so the call really ran on the
    # *summary* model and ignored max_tokens/temperature.  Now the tag model
    # is resolved explicitly from ``llm_settings.active_tag_model_id``
    # (falling back to the summary model), matching the cases/events
    # extraction pipelines.
    # v1.0.47.1 (audit F2): resolve only the missing half.  The old
    # ``or`` condition overwrote an explicitly supplied provider when
    # the caller omitted only ``model`` (and vice versa).
    if llm_call_fn is None and (provider is None or model is None):
        try:
            resolved_provider, resolved_model = resolve_active_model(
                "active_tag_model_id"
            )
            if provider is None:
                provider = resolved_provider
            if model is None:
                model = resolved_model
        except Exception as exc:
            logger.error("[%s] No active tag model configured: %s — aborting",
                         job_id, exc)
            finished_at = _cst_now().isoformat()
            history.log_run(job_id, triggered_at=triggered_at, finished_at=finished_at,
                            success=False, error=str(exc), new_articles=0, sources_run=0,
                            extra=run_extra)
            return None
    logger.info("[%s] using model %s/%s", job_id, provider, model)

    processed = 0
    errors = 0
    write_errors = 0
    now_cst = _cst_now()

    for row in article_rows:
        article_id: str = row["id"]
        article: dict[str, Any] = {
            "title": row["title"],
            "url": row["url"],
            "raw_content": row["raw_content"],
        }

        try:
            prompt = render_predict_tag(article, available_tags)
            if llm_call_fn is not None:
                raw_reply = llm_call_fn(prompt)
            else:
                raw_reply = call_llm(provider, model, prompt)
        except Exception as exc:
            logger.warning("[%s] LLM call failed for article %s: %s — skipping",
                           job_id, article_id, exc)
            errors += 1
            continue

        # Parse the tag list from the LLM reply (strict JSON first)
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
                write_errors += 1

        conn.commit()
        processed += 1
        logger.debug("[%s] Tagged article %s with %d tag(s): %s",
                     job_id, article_id, len(predicted_names), predicted_names)

    finished_at = _cst_now().isoformat()
    success = errors == 0 and write_errors == 0
    error_parts: list[str] = []
    if errors:
        error_parts.append(f"{errors} article errors")
    if write_errors:
        error_parts.append(f"{write_errors} tag write errors")

    try:
        history.log_run(
            job_id,
            triggered_at=triggered_at,
            finished_at=finished_at,
            success=success,
            error="; ".join(error_parts) if error_parts else None,
            new_articles=processed,
            sources_run=processed,
            extra=run_extra,
        )
    except Exception as exc:
        logger.error("[%s] Failed to write cron_runs row: %s", job_id, exc)

    logger.info(
        "[%s] finished at %s — processed=%d errors=%d write_errors=%d",
        job_id, finished_at, processed, errors, write_errors,
    )
    return None


def _parse_tags_from_reply(reply: str) -> list[str]:
    """Extract a list of tag names from an LLM text reply.

    ``predict_tag.md`` asks for **strict JSON**:
    ``{"tags": ["银行", "AI"], "reasoning": "..."}``.
    v1.0.47: the old implementation split the *raw text* on commas, which
    shredded a JSON reply into fragments like ``{"tags": ["银行"`` — none of
    which ever matched a real tag name, so the job inserted **zero** tags.

    Three tiers, in order:
    1. strict JSON object (bare / fenced / embedded in prose)
    2. regex extraction of the ``"tags": [...]`` array (broken JSON)
    3. plain text: comma / newline / bullet separated

    Strips whitespace, ignores empty entries, dedupes and caps at 3.
    """
    text = (reply or "").strip()
    if not text:
        return []

    # 1) strict JSON
    data = _extract_json_object(text)
    if data is not None:
        tags = data.get("tags")
        if isinstance(tags, list):
            return _clean_tag_names([str(t) for t in tags])
        if isinstance(tags, str):
            return _clean_tag_names([tags])

    # 2) regex — JSON-ish reply whose object body failed to parse
    match = _TAGS_ARRAY_RE.search(text)
    if match:
        inner = match.group(1)
        names = [part.strip().strip("\"'") for part in inner.split(",")]
        return _clean_tag_names([n for n in names if n])

    # 3) plain text — replace bullets, newlines and CJK commas with ","
    normalized = text.replace("•", ",").replace("\n", ",").replace("，", ",")
    names = []
    for part in normalized.split(","):
        name = part.strip().strip("\"' ·•*-")
        # drop JSON-ish key fragments ("tags": ...) leaking from tier 1/2
        if ":" in name:
            continue
        if name:
            names.append(name)
    return _clean_tag_names(names)


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


# ── 同业案例抽取 (P0-4) ──────────────────────────────────────────────


def extract_cases_pending(
    *,
    batch_size: int = 30,
    provider: str | None = None,
    model: str | None = None,
    llm_call_fn: Any = None,
    triggered_by: str | None = None,
) -> dict[str, Any] | None:
    """为新入库的文章批量抽取同业案例 (走 case_extract_runs 账本 + 互斥锁 + 预算闸)."""
    if not _CASE_LOCK.acquire(blocking=False):
        logger.info("[extract_cases_pending] skipped: previous run still in progress")
        return {"skipped": "busy"}

    job_id = "extract_cases_pending"
    triggered_at = _cst_now().isoformat()
    history = CronRunHistory()
    cron_run_rowid = 0
    try:
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

        try:
            from moni.cases.pipeline import extract_cases_batch

            result = extract_cases_batch(
                batch_size=batch_size,
                provider=provider,
                model=model,
                llm_call_fn=llm_call_fn,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.error("[%s] batch extraction failed: %s", job_id, error)
            if cron_run_rowid:
                try:
                    conn = get_db()
                    conn.execute(
                        "UPDATE cron_runs SET finished_at=?, success=0, error=? WHERE id=?",
                        (_cst_now().isoformat(), error, cron_run_rowid),
                    )
                    conn.commit()
                except Exception:
                    pass
            return {"error": error}

        finished_at = _cst_now().isoformat()
        success = result["failed"] == 0 and not result.get("rate_limited")
        err_msg = None
        if result.get("rate_limited"):
            err_msg = "rate_limited (429)"
        elif result["failed"]:
            err_msg = f"{result['failed']} failed"

        extra_payload: dict[str, Any] = {}
        if result.get("budget_exhausted"):
            extra_payload["budget_exhausted"] = True
        if triggered_by:
            extra_payload["triggered_by"] = triggered_by

        if cron_run_rowid:
            try:
                conn = get_db()
                conn.execute(
                    """
                    UPDATE cron_runs
                    SET finished_at=?, success=?, error=?,
                        new_articles=?, sources_run=?,
                        extra=CASE WHEN ? != '{}' THEN ? ELSE extra END
                    WHERE id=?
                    """,
                    (
                        finished_at,
                        1 if success else 0,
                        err_msg,
                        result["cases_extracted"],
                        result["processed"],
                        json.dumps(extra_payload),
                        json.dumps(extra_payload),
                        cron_run_rowid,
                    ),
                )
                conn.commit()
            except Exception as log_exc:
                logger.error("[%s] Failed to update cron_runs row: %s", job_id, log_exc)

        return result
    finally:
        _CASE_LOCK.release()
