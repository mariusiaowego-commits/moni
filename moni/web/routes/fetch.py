"""routes/fetch — fetch source listing, config update, manual run and health.

Note: there is no ``fetch_sources`` DB table.  Source configs live in
``config/sources.yaml`` and are managed by ``moni.fetch.scheduler.Scheduler``.
This module lists sources from that YAML config and runs fetchers via
``moni.fetch.run(source_name)``.

v1.0.5: the route URLs accept BOTH the DB-style source id
(``src-36kr-rss``, what the SPA sends) and the YAML-style source name
(``36kr_rss``, what the CLI uses).  The frontend SPA loaded the source
list from ``/api/config/sources`` (DB id) but then POSTed to
``/api/fetch/sources/{id}/run`` and ``/ping``, where the backend was
looking up by ``source_name`` — so the user got 404 "Fetch source
'src-36kr-rss' not found" and 405 "Method Not Allowed" on /ping.
This module now resolves either form.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException

from moni.db.connection import get_db
# v1.0.11: ``moni.fetch.run_fetcher`` is an alias set in
# ``moni/fetch/__init__.py`` that cannot be shadowed by the
# ``moni.fetch.run`` submodule (the CLI's `from ..fetch.run import main`
# import path overwrites the original ``run`` attribute with the
# submodule).  Binding to ``run_fetcher`` ensures we always get the
# function.
from moni.fetch import run_fetcher
from moni.fetch.jobs import FetchJobStore
from moni.fetch.base import _FETCHER_REGISTRY, _ensure_sources_loaded
from moni.db import leases
from moni.web.exceptions import NotFoundError
from moni.web.models import FetchSource, HealthResponse
from moni.web.routes.config_sources import _FETCH_CONFIG_DEFAULTS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fetch", tags=["fetch"])

# Lease heartbeat cadence. Grace is 3× the interval (90s), so a heartbeat
# may survive up to two transient failures; the third one means the DB has
# been unreachable for ~90s and the row is about to be swept anyway.
_HEARTBEAT_INTERVAL_SEC = 30.0
_HEARTBEAT_MAX_FAILURES = 3


def _start_heartbeat(
    handle: leases.LeaseHandle,
    source_name: str,
    job_id: str,
    *,
    interval: float = _HEARTBEAT_INTERVAL_SEC,
    max_failures: int = _HEARTBEAT_MAX_FAILURES,
) -> tuple[threading.Event, threading.Thread]:
    """Renew ``handle`` until the returned stop event is set.

    A single transient SQLite error used to kill this thread for good, which
    made a live job look like a dead one 90 seconds later.
    """
    stop = threading.Event()

    def _beat() -> None:
        failures = 0
        while not stop.wait(interval):
            try:
                if leases.heartbeat(handle):
                    failures = 0
                    continue
                row = FetchJobStore().get_raw(job_id)
                if row is None or row.get("status") != "running":
                    logger.info(
                        "[%s] heartbeat stopping: job %s already finished",
                        source_name, job_id[:8],
                    )
                    return
                # Still running but our owner/fence no longer matches: another
                # writer owns this row. Retrying can never succeed.
                logger.error(
                    "[%s] heartbeat lost lease for job %s (fence=%s)",
                    source_name, job_id[:8], handle.fence,
                )
                return
            except Exception as exc:  # noqa: BLE001 — never let the thread die silently
                failures += 1
                logger.warning(
                    "[%s] heartbeat error %d/%d for job %s: %s",
                    source_name, failures, max_failures,
                    job_id[:8], exc,
                )
                if failures >= max_failures:
                    logger.error(
                        "[%s] heartbeat giving up after %d consecutive failures "
                        "for job %s — sweep will mark it stale",
                        source_name, failures, job_id[:8],
                    )
                    return

    thread = threading.Thread(
        target=_beat,
        name=f"lease-hb-{source_name}-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return stop, thread


def _start_batch_heartbeat(batch_id: str) -> tuple[threading.Event, threading.Thread]:
    """Keep queued rows of a batch alive while it runs.

    If this thread dies (process restart), the queued rows stop being
    refreshed and ``sweep_expired`` reaps them instead of leaving the batch
    stuck in "running" forever.
    """
    stop = threading.Event()

    def _tick() -> None:
        while not stop.wait(_HEARTBEAT_INTERVAL_SEC):
            try:
                FetchJobStore().refresh_pending(batch_id)
            except Exception as exc:  # noqa: BLE001 — keep the queue alive
                logger.warning(
                    "[batch %s] pending refresh failed: %s", batch_id[:8], exc
                )

    thread = threading.Thread(
        target=_tick,
        name=f"batch-hb-{batch_id[:8]}",
        daemon=True,
    )
    thread.start()
    return stop, thread

# Path to sources config — same resolution as moni.fetch.scheduler
_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "sources.yaml"

# v1.0.7.6: hard timeout for async fetch jobs (10 minutes per user spec).
JOB_TIMEOUT_SECONDS = 600


def _job_timeout(source_name: str) -> int:
    """Per-source job timeout (seconds), read from sources.fetch_config['timeout_sec'].

    Falls back to the UI default for the source, then
    ``JOB_TIMEOUT_SECONDS`` (600) when unset or invalid.

    v1.0.35.17: heavy sources like bestblogs (375-feed full sweep ~880s) were
    finishing the whole run but got falsely marked 'timeout after 10min' because
    the global 600s budget was tighter than a legit full sweep. Making the budget
    per-source (configurable in /config fetch_config) fixes that false positive
    without cutting coverage.
    """
    fallback = _FETCH_CONFIG_DEFAULTS.get(source_name, {}).get(
        "timeout_sec", JOB_TIMEOUT_SECONDS
    )
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT fetch_config FROM sources WHERE name = ?", (source_name,)
        ).fetchone()
        if row and row["fetch_config"]:
            cfg = json.loads(row["fetch_config"])
            val = cfg.get("timeout_sec")
            if (
                isinstance(val, int)
                and not isinstance(val, bool)
                and 0 < val <= 86_400
            ):
                return val
    except Exception as exc:
        logger.debug("[%s] failed to read timeout_sec: %s", source_name, exc)
    return fallback


def _load_sources() -> list[dict[str, Any]]:
    """Load source configs from config/sources.yaml."""
    if not _CONFIG_PATH.exists():
        return []
    with _CONFIG_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data.get("sources", []) if isinstance(data, dict) else []


def _row_to_fetch_source(name: str, cfg: dict[str, Any]) -> FetchSource:
    now = datetime.now(timezone.utc).isoformat()
    return FetchSource(
        id=0,
        name=name,
        base_url=cfg.get("base_url", ""),
        crawler_type=cfg.get("crawler_type", "unknown"),
        is_active=cfg.get("enabled", True),
        config=cfg,
        created_at=now,
        updated_at=now,
    )


def _resolve_source_name(ref: str) -> str | None:
    """Resolve a route param to the YAML ``source_name``.

    Accepts:
      * YAML ``source_name`` directly (``36kr_rss``, ``mpaypass`` …)
      * DB source ``id`` (``src-36kr-rss``, ``src-mpaypass`` …), looked
        up via ``sources.name`` in the DB

    Returns the YAML ``source_name`` that ``moni.fetch.run`` expects, or
    None if neither form matches.
    """
    yaml_sources = _load_sources()
    yaml_names = {cfg.get("source_name", "") for cfg in yaml_sources}
    if ref in yaml_names:
        return ref
    # Try the DB mapping: src-36kr-rss -> 36kr_rss (sources.name)
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT name FROM sources WHERE id = ?", (ref,)
        ).fetchone()
        if row and row["name"] in yaml_names:
            return row["name"]
    except Exception:
        # DB unavailable — fall through
        pass
    return None


@router.get("/sources", response_model=list[FetchSource])
def list_fetch_sources() -> list[FetchSource]:
    """List all fetch sources defined in config/sources.yaml."""
    sources = _load_sources()
    return [_row_to_fetch_source(cfg.get("source_name", ""), cfg) for cfg in sources]


@router.post("/sources/{name}/run")
def trigger_fetch_run(
    name: str,
    date_from: str | None = None,
    date_to: str | None = None,
    force: bool = True,
) -> dict[str, Any]:
    """Trigger an immediate fetch for the named source.

    v1.0.5: ``name`` can be either the YAML ``source_name`` (e.g.
    ``36kr_rss``) or the DB source id (e.g. ``src-36kr-rss``) — see
    ``_resolve_source_name``.

    v1.0.5.24: optional ``date_from`` / ``date_to`` (ISO date strings)
    are passed through to the fetcher for date-range filtering.

    v1.0.6.5: ``force=True`` by default — UI "立即抓取" always fetches,
    bypassing the interval check.

    v1.0.7.6: ASYNC — returns immediately with ``{job_id, status:running}``
    while the actual fetch runs in a background thread. The frontend
    polls ``GET /api/fetch/jobs/{job_id}`` every 5s to render a
    progress bar. Pass ``?async=false`` to fall back to the old
    blocking behaviour (used by CLI smoke tests).
    """
    resolved = _resolve_source_name(name)
    if resolved is None:
        raise NotFoundError(message=f"Fetch source {name!r} not found")

    jobs = FetchJobStore()
    if jobs.has_running(resolved):
        # v1.0.7.6-hotfix.1: scope the lookup to THIS source so the SPA
        # doesn't get a job_id from a different (or already-swept) job.
        # If we can't find a live job for this source, treat it as not
        # running and fall through to start a new one.
        existing = jobs.list_recent(limit=1, status="running", source=resolved)
        queued = False
        if not existing:
            # Queued in a batch — starting it again would double-run it.
            existing = jobs.list_recent(limit=1, status="pending", source=resolved)
            queued = bool(existing)
        running_job_id = existing[0]["id"] if existing else None
        if not running_job_id:
            # Race: has_running said True but the row vanished (swept
            # between the two calls). Don't 409 — let the new job start.
            pass
        else:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "fetch_queued" if queued else "fetch_in_progress",
                    "message": (
                        f"Source {resolved!r} is queued in a batch"
                        if queued
                        else f"Source {resolved!r} is already being fetched"
                    ),
                    "running_job_id": running_job_id,
                },
            )

    job_id = jobs.create(resolved, total_feeds=0)
    thread = threading.Thread(
        target=_run_fetch_job,
        args=(job_id, resolved, date_from, date_to, force),
        daemon=True,
        name=f"fetch-{resolved}-{job_id[:8]}",
    )
    thread.start()

    return {
        "job_id": job_id,
        "source": resolved,
        "status": "running",
        "poll_url": f"/api/fetch/jobs/{job_id}",
    }


# ── v1.0.7.6: synchronous fallback for CLI / smoke tests ────────────────────


@router.post("/sources/{name}/run-sync")
def trigger_fetch_run_sync(
    name: str,
    date_from: str | None = None,
    date_to: str | None = None,
    force: bool = True,
) -> dict[str, Any]:
    """Synchronous fetch — blocks until done. Used by CLI tests.

    Kept separate from ``/run`` so the SPA never accidentally calls
    the blocking variant.
    """
    resolved = _resolve_source_name(name)
    if resolved is None:
        raise NotFoundError(message=f"Fetch source {name!r} not found")

    result: dict[str, Any] | None = run_fetcher(
        resolved, date_from=date_from, date_to=date_to, force=force,
    )

    if result is None:
        return {
            "source": resolved,
            "status": "skipped",
            "message": "Source not due for fetching",
        }

    return result


def _run_fetch_job(
    job_id: str,
    source_name: str,
    date_from: str | None = None,
    date_to: str | None = None,
    force: bool = False,
) -> None:
    """Background worker — runs the fetcher and persists progress.

    Catches all exceptions to ensure the job row always reaches a
    terminal status (done | failed | timeout).  Uses a wall-clock
    deadline to enforce JOB_TIMEOUT_SECONDS.

    v1.0.42 (sprint-22): heartbeat-driven lease.  Backend thread acquires
    a lease (or falls back to raw write for legacy paths), keeps the
    heartbeat alive every 30s via ``leases.heartbeat``, and uses the
    ``LeaseHandle`` for the fenced terminal write so late writers after
    a restart are rejected.
    """
    import time
    import threading

    from datetime import datetime, timezone

    from moni.db import leases

    jobs = FetchJobStore()
    started = time.monotonic()
    timeout_sec = _job_timeout(source_name)
    deadline = started + timeout_sec

    # v1.0.42: take ownership of the lease that ``jobs.create()`` already
    # minted. We don't run leases.acquire() here because that would race
    # with sweep — the row was INSERTed with status='running' AND a fresh
    # heartbeat; sweep won't reap us for 90s.
    # Read the freshly-minted owner+fence and bump fence to claim it as
    # our own (so finish() below has a handle to fence on).
    import sqlite3
    from moni.db.connection import get_db as _get_db
    try:
        row = _get_db().execute(
            "SELECT lease_owner, fence FROM fetch_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            logger.warning("[%s] job %s vanished after create()", source_name, job_id[:8])
            return
        owner = row["lease_owner"] or ""
        # Bump fence + refresh heartbeat atomically. RETURNING is fetched
        # then we commit; the lease handle we hand to the heartbeat thread
        # carries the new fence so finish() below can fence on it.
        cur = _get_db().execute(
            "UPDATE fetch_jobs SET fence = fence + 1, "
            "lease_heartbeat_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE id = ?",
            (job_id,),
        )
        _get_db().commit()
        new_fence = cur.rowcount  # rowcount==1 means our UPDATE landed
        # If we got 0 rows, the row was reaped between SELECT and UPDATE.
        if new_fence != 1:
            logger.warning(
                "[%s] job %s vanished between SELECT and UPDATE — orphan",
                source_name, job_id[:8],
            )
            return
        # Read the freshly-bumped fence (separate query after commit).
        fence_row = _get_db().execute(
            "SELECT fence FROM fetch_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        # v1.0.51 (audit fix): the row can vanish between the UPDATE and this
        # SELECT (job reaped / DB swapped / table dropped underneath a background thread).
        # In tests or teardown, if the DB or table is dropped, sqlite3.OperationalError
        # was raised outside try:. We catch sqlite3.Error and degrade to a warning
        # rather than crashing the daemon thread with an unhandled exception.
        if fence_row is None:
            logger.warning(
                "[%s] job %s vanished between UPDATE and fence read — orphan",
                source_name, job_id[:8],
            )
            return
        new_fence = fence_row["fence"]
        handle = leases.LeaseHandle(job_id=job_id, owner=owner, fence=new_fence)
        if not owner:
            # create() always mints an owner; a missing one means the row was
            # tampered with. Acquiring an arbitrary pending row here would
            # steal another source's queued job.
            logger.error(
                "[%s] job %s has no lease owner — giving up",
                source_name, job_id[:8],
            )
            return
    except sqlite3.Error as exc:
        logger.warning(
            "[%s] DB error initializing lease for job %s: %s",
            source_name, job_id[:8], exc,
        )
        return

    hb_stop, hb_thread = _start_heartbeat(handle, source_name, job_id)

    try:
        _ensure_sources_loaded()
        if source_name not in _FETCHER_REGISTRY:
            jobs.mark_failed(job_id, f"No fetcher registered for {source_name!r}", handle=handle)
            return

        fetcher = _FETCHER_REGISTRY[source_name]()
        fetcher.force = bool(force)
        if date_from:
            fetcher.date_from = date_from
        if date_to:
            fetcher.date_to = date_to

        # Wire progress callback to persist into fetch_jobs.
        def _persist_progress(
            *,
            total_feeds: int | None = None,
            done_feeds: int | None = None,
            current_feed: str | None = None,
            articles_so_far: int | None = None,
        ) -> None:
            try:
                jobs.update_progress(
                    job_id,
                    total_feeds=total_feeds,
                    done_feeds=done_feeds,
                    current_feed=current_feed,
                    articles_so_far=articles_so_far,
                )
            except Exception as exc:
                logger.debug("[%s] progress persist failed: %s", source_name, exc)
            # Check timeout opportunistically — keeps the job from
            # running past its budget even if downstream httpx hangs.
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Fetch exceeded {timeout_sec}s timeout"
                )

        fetcher.progress_callback = _persist_progress

        result = fetcher.run()

        # Defensive: in case the fetcher didn't raise but we're past
        # the deadline, mark as timeout.
        if time.monotonic() > deadline:
            jobs.mark_timeout(job_id, handle=handle)
            return

        if result is None:
            jobs.mark_done(
                job_id,
                {
                    "source": source_name,
                    "status": "skipped",
                    "message": "Source not due for fetching",
                },
                handle=handle,
            )
            return

        # A fetcher reports per-source failures in ``result["error"]`` instead
        # of raising (fetch() swallows its own exceptions). Treat that as a
        # failed job — otherwise the UI shows a green "done" with 0 articles.
        if result.get("error"):
            jobs.mark_failed(job_id, str(result["error"]), handle=handle)
            return

        # 有数据入库但有目标/子步骤失败 → partial, 别把已入库的成果
        # 显示成「失败 · 0 篇」(v1.0.88)。
        if result.get("warning"):
            jobs.mark_partial(job_id, result, str(result["warning"]), handle=handle)
            return

        jobs.mark_done(job_id, result, handle=handle)

    except TimeoutError as exc:
        logger.warning("[%s] job %s timed out: %s", source_name, job_id[:8], exc)
        try:
            jobs.mark_timeout(job_id, str(exc), handle=handle)
        except Exception as te_exc:
            logger.warning("[%s] could not record timeout for job %s: %s", source_name, job_id[:8], te_exc)
    except Exception as exc:
        logger.exception("[%s] job %s failed", source_name, job_id[:8])
        try:
            jobs.mark_failed(job_id, f"{type(exc).__name__}: {exc}", handle=handle)
        except Exception as mf_exc:
            logger.warning("[%s] could not mark_failed for job %s: %s", source_name, job_id[:8], mf_exc)
    finally:
        hb_stop.set()
        hb_thread.join(timeout=2.0)


# ── v1.0.7.6: fetch_jobs query endpoints (SPA polls these) ──────────────────


@router.get("/jobs/batch/recent")
def get_recent_batch() -> dict[str, Any]:
    """Return the batch_id of the most recent batch fetch (v1.0.8.9).

    The frontend uses this to populate the "📋 上次结果" button — clicking
    it re-opens the progress modal for the most recent batch, even after
    the user has closed it.  Returns 404 if no batch has ever been run.
    """
    batch_id = FetchJobStore().get_recent_batch()
    if batch_id is None:
        raise NotFoundError(message="No batch fetch history yet")
    return {"batch_id": batch_id}


@router.get("/jobs/batch/{batch_id}")
def get_batch_status(batch_id: str) -> dict[str, Any]:
    """Return aggregate progress for a batch fetch job (v1.0.8.9).

    The frontend polls this every 5s while "全部抓取" is running to
    render a multi-source progress modal showing how many sources are
    done, the current source being fetched, and per-feed progress.
    """
    batch = FetchJobStore().get_batch(batch_id)
    if batch is None:
        raise NotFoundError(message=f"Fetch batch {batch_id!r} not found")
    return batch


@router.get("/jobs/{job_id}")
def get_fetch_job(job_id: str) -> dict[str, Any]:
    """Return the current status of a single fetch job.

    Frontend polls this every 5s while the job is running to render
    a progress bar.  Once ``status`` becomes ``done | failed | timeout``
    the SPA stops polling and shows the final result / error.
    """
    job = FetchJobStore().get(job_id)
    if job is None:
        raise NotFoundError(message=f"Fetch job {job_id!r} not found")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_fetch_job(job_id: str) -> dict[str, Any]:
    """Cancel a running fetch job (soft). Unblocks UI + same-source re-run.

    Marks the job ``cancelled``. In-flight daemon thread may still finish
    and overwrite status if it races; intended for zombie / stuck UI cases
    after process restart.
    """
    store = FetchJobStore()
    job = store.get_raw(job_id)
    if job is None:
        raise NotFoundError(message=f"Fetch job {job_id!r} not found")
    st = job.get("status")
    if st in ("done", "failed", "timeout", "stale", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "already_terminal",
                "message": f"Job already {st!r}; cannot cancel",
                "job_id": job_id,
                "status": st,
            },
        )
    ok = store.mark_cancelled(job_id, "cancelled by user")
    if not ok:
        raise HTTPException(
            status_code=409,
            detail={"error": "cancel_failed", "message": "Could not cancel job", "job_id": job_id},
        )
    updated = store.get_raw(job_id)
    return {"job_id": job_id, "status": (updated or {}).get("status", "cancelled"), "ok": True}


@router.get("/jobs")
def list_fetch_jobs(
    status: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """List recent fetch jobs (newest first), optionally filtered by status.

    The SPA uses ``status=running`` on page load to discover jobs that
    survived a page refresh and resume polling.
    """
    if limit < 1 or limit > 100:
        limit = 20
    jobs = FetchJobStore().list_recent(limit=limit, status=status)
    return {"jobs": jobs, "count": len(jobs)}


def _db_source_url(source_name: str) -> str:
    """Look up the live URL from the ``sources`` table (may differ from YAML)."""
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT url FROM sources WHERE name = ?", (source_name,)
        ).fetchone()
        if row and row["url"]:
            return str(row["url"]).strip()
    except Exception:
        pass
    return ""


def _probe_url(url: str, timeout: float = 5.0) -> tuple[bool, str | None]:
    """Probe a single URL/path. Returns (ok, error_message).

    Handles:
    - comma-separated multi-URL configs (try first only — used by bestblogs)
    - local file paths (bestblog-x OPML etc.)
    - HTTP(S): HEAD first, GET fallback (some hosts reject HEAD)
    """
    if not url:
        return False, "no feed_url configured"

    # Multi-URL (comma-separated) → probe the first entry
    first = url.split(",")[0].strip()
    if not first:
        return False, "empty feed_url"

    # Local filesystem path (absolute or relative OPML)
    if first.startswith("/") or first.startswith("./") or first.startswith("../"):
        p = Path(first)
        if p.is_file():
            return True, None
        return False, f"local file not found: {first}"

    if not first.startswith("http://") and not first.startswith("https://"):
        return False, f"unsupported url scheme: {first[:48]}"

    import urllib.error
    import urllib.request

    headers = {"User-Agent": "moni/1.0"}
    last_err: str | None = None
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(first, method=method, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if 200 <= resp.status < 400:
                    return True, None
                last_err = f"HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            # 401/403 still means the host is reachable (auth token may
            # be missing in probe but real fetch uses DB credentials).
            if exc.code in (401, 403, 405):
                return True, f"reachable (HTTP {exc.code})"
            last_err = f"HTTP {exc.code}"
            if method == "HEAD" and exc.code in (404, 405, 501):
                continue  # try GET
        except Exception as exc:
            last_err = str(exc)
            if method == "HEAD":
                continue
    return False, last_err


@router.post("/sources/{name}/health", response_model=HealthResponse)
def check_source_health(name: str) -> HealthResponse:
    """Check the health / availability of a named fetch source.

    v1.0.5: ``name`` can be either the YAML ``source_name`` or the DB
    source id (see ``_resolve_source_name``).

    v1.0.12: ``xwatch`` uses dual-probe (syndication + DDG + passive)
    instead of HEAD on empty feed_url (which always reported degraded).

    v1.0.25: use DB url when YAML feed_url is empty; support local file
    paths and multi-URL feeds; HEAD→GET fallback; clearer degraded reason.
    """
    resolved = _resolve_source_name(name)
    if resolved is None:
        raise NotFoundError(message=f"Fetch source {name!r} not found")

    sources = _load_sources()
    cfg = next((s for s in sources if s.get("source_name") == resolved), None)

    db_connected = True
    try:
        conn = get_db()
        conn.execute("SELECT 1").fetchone()
    except Exception:
        db_connected = False

    # v1.0.12: dedicated path for xwatch (no feed_url)
    if resolved == "xwatch":
        from moni.fetch.health import check_xwatch
        result = check_xwatch()
        status = "ok" if (result.ok and db_connected) else "degraded"
        if result.ok and result.error and result.error.startswith("ok with warnings"):
            status = "degraded"  # content path OK but DDG/staleness warnings
        return HealthResponse(
            status=status,
            version="1.0.12",
            db_connected=db_connected,
            detail=result.detail or None,
            message=result.error,
        )

    # Prefer DB url (live config UI edits) over YAML, then YAML feed/base.
    yaml_url = ""
    if cfg:
        yaml_url = (cfg.get("base_url") or cfg.get("feed_url") or "").strip()
    db_url = _db_source_url(resolved)
    probe_url = db_url or yaml_url

    source_ok, err = _probe_url(probe_url)
    status = "ok" if (source_ok and db_connected) else "degraded"
    msg = None
    if not source_ok:
        msg = err or "probe failed"
    elif not db_connected:
        msg = "source reachable but DB disconnected"
    elif err:
        msg = err  # e.g. reachable via 401

    if resolved in ("36kr_rss", "36kr"):
        try:
            from moni.fetch.chrome_daemon import get_daemon_status

            c_status = get_daemon_status()
            if c_status.get("is_degraded"):
                status = "degraded"
                msg = f"chrome daemon degraded: {c_status.get('last_error')}"
        except Exception:
            pass

    return HealthResponse(
        status=status,
        version="1.0.25",
        db_connected=db_connected,
        message=msg,
    )


# v1.0.5 alias: the SPA POSTs to ``/api/fetch/sources/{name}/ping`` (the
# intuitive verb), but the canonical name on this module is ``/health``.
# Wire the alias to the same handler so both work without touching the
# frontend.  Define it AFTER the canonical route so FastAPI registers
# the alias after the primary path (avoids matching surprises).
@router.post("/sources/{name}/ping", response_model=HealthResponse)
def ping_source_health(name: str) -> HealthResponse:
    """Alias of ``/sources/{name}/health`` for the SPA's ``pingSource()``."""
    return check_source_health(name)


@router.post("/all/run")
def trigger_fetch_all() -> dict[str, Any]:
    """Fetch all enabled sources as an async batch job (v1.0.8.9).

    Returns immediately with ``{batch_id, status:running}`` while the
    actual fetches run sequentially in a background thread. The
    frontend polls ``GET /api/fetch/jobs/batch/{batch_id}`` every 5s
    to render a multi-source progress modal.

    On Monday, automatically sets date_from to Saturday for weekend
    catch-up (same logic as the old sync version).
    """
    import uuid

    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from moni.fetch.scheduler import Scheduler as FetchScheduler

    CST = ZoneInfo("Asia/Shanghai")
    now = datetime.now(CST)
    triggered_at = now.isoformat()

    # Monday weekend catch-up
    date_from: str | None = None
    if now.weekday() == 0:
        saturday = now - timedelta(days=2)
        date_from = saturday.strftime("%Y-%m-%d")

    fetch_scheduler = FetchScheduler()
    enabled = list(fetch_scheduler.batch_sources())
    if not enabled:
        return {"status": "skipped", "message": "No enabled sources", "results": []}

    batch_id = str(uuid.uuid4())

    # Create the queued rows up front: a batch must always own rows, even if
    # the worker dies before it reaches the first source.
    store = FetchJobStore()
    job_ids: dict[str, str] = {}
    try:
        for cfg in enabled:
            source_name: str = cfg["source_name"]
            job_id = store.create(source_name, batch_id=batch_id, status="pending")
            store.update_progress(job_id, current_feed="(排队中)")
            job_ids[source_name] = job_id

        thread = threading.Thread(
            target=_run_batch_job,
            args=(batch_id, enabled, date_from, triggered_at, job_ids),
            daemon=True,
            name=f"fetch-batch-{batch_id[:8]}",
        )
        thread.start()
    except Exception as exc:
        # No worker will pick these rows up — close the queue right away.
        store.fail_pending(batch_id, f"批量任务启动失败：{exc}")
        logger.exception("[batch %s] could not start the batch", batch_id[:8])
        raise HTTPException(
            status_code=503,
            detail={"error": "batch_start_failed", "message": str(exc)},
        )

    return {
        "batch_id": batch_id,
        "status": "running",
        "sources_total": len(enabled),
        "poll_url": f"/api/fetch/jobs/batch/{batch_id}",
    }


def _run_batch_job(
    batch_id: str,
    enabled: list[dict[str, Any]],
    date_from: str | None,
    triggered_at: str,
    job_ids: dict[str, str],
) -> None:
    """Batch worker — keeps the queue alive and closes it if we die.

    The inner worker runs one source after another; this wrapper refreshes
    the heartbeat of the queued rows and, on any failure, marks the ones we
    never reached as failed instead of leaving the batch "running" forever.
    """
    stop, thread = _start_batch_heartbeat(batch_id)
    try:
        _run_batch_job_inner(batch_id, enabled, date_from, triggered_at, job_ids)
    except Exception:
        logger.exception("[batch %s] batch worker failed", batch_id[:8])
    finally:
        stop.set()
        thread.join(timeout=2.0)
        try:
            FetchJobStore().fail_pending(
                batch_id, "批量任务中断（服务重启或内部错误），未执行的信源已中止"
            )
        except Exception as exc:
            logger.warning(
                "[batch %s] could not close queued rows: %s", batch_id[:8], exc
            )


def _run_batch_job_inner(
    batch_id: str,
    enabled: list[dict[str, Any]],
    date_from: str | None,
    triggered_at: str,
    job_ids: dict[str, str],
) -> None:
    """Background worker for batch fetch — runs each source sequentially.

    The queued ``fetch_jobs`` rows were created by the route; this worker
    adopts them one by one, runs the fetcher with progress tracking, and
    logs to cron_runs + fetch_log.  Each source gets its own timeout.

    Catches all exceptions per-source so one failure doesn't abort
    the entire batch.
    """
    import time

    from zoneinfo import ZoneInfo

    from moni.cron.run_history import CronRunHistory
    from moni.fetch.base import _ensure_sources_loaded, _FETCHER_REGISTRY

    CST = ZoneInfo("Asia/Shanghai")
    history = CronRunHistory()
    jobs = FetchJobStore()

    _ensure_sources_loaded()

    # Create cron_runs row
    cron_run_rowid = history.log_run(
        "fetch_all_sources",
        triggered_at=triggered_at,
        finished_at=triggered_at,
        success=True,
        new_articles=0,
        sources_run=0,
    )

    total_inserted = 0
    sources_failed = 0
    sources_partial = 0
    attempted = 0
    aborted = False

    try:
        for cfg in enabled:
            source_name: str = cfg["source_name"]
            job_id = job_ids[source_name]

            if source_name not in _FETCHER_REGISTRY:
                jobs.mark_failed(job_id, "No fetcher registered")
                sources_failed += 1
                continue

            handle = jobs.adopt(job_id)
            if handle is None:
                logger.warning(
                    "[%s] batch job %s is no longer pending — skipping",
                    source_name, job_id[:8],
                )
                sources_failed += 1
                continue
            attempted += 1
            try:
                hb_stop, hb_thread = _start_heartbeat(handle, source_name, job_id)
            except Exception as exc:
                jobs.mark_failed(job_id, f"心跳线程启动失败：{exc}", handle=handle)
                raise

            try:
                started = time.monotonic()
                timeout_sec = _job_timeout(source_name)
                deadline = started + timeout_sec

                fetcher = _FETCHER_REGISTRY[source_name]()
                if date_from:
                    fetcher.date_from = date_from

                def _persist_progress(
                    *,
                    total_feeds: int | None = None,
                    done_feeds: int | None = None,
                    current_feed: str | None = None,
                    articles_so_far: int | None = None,
                ) -> None:
                    try:
                        jobs.update_progress(
                            job_id,
                            total_feeds=total_feeds,
                            done_feeds=done_feeds,
                            current_feed=current_feed,
                            articles_so_far=articles_so_far,
                        )
                    except Exception as exc:
                        logger.debug("[%s] progress persist failed: %s", source_name, exc)
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            f"Fetch exceeded {timeout_sec}s timeout"
                        )

                fetcher.progress_callback = _persist_progress
                result = fetcher.run()

                if time.monotonic() > deadline:
                    jobs.mark_timeout(job_id, handle=handle)
                    sources_failed += 1
                    continue

                if result is None:
                    jobs.mark_done(job_id, {
                        "source": source_name,
                        "status": "skipped",
                        "message": "Source not due for fetching",
                    }, handle=handle)
                    continue

                if result.get("error"):
                    jobs.mark_failed(job_id, str(result["error"]), handle=handle)
                    sources_failed += 1
                elif result.get("warning"):
                    jobs.mark_partial(
                        job_id, result, str(result["warning"]), handle=handle
                    )
                    sources_partial += 1
                else:
                    jobs.mark_done(job_id, result, handle=handle)

                # 失败源也要写 fetch_log / 计入入库数 —— 部分失败时
                # inserted 可能 > 0, 跳过会让 cron_runs.new_articles 少算。
                dropped_total = (
                    result.get("dropped_blacklist", 0)
                    + result.get("dropped_whitelist", 0)
                    + result.get("dropped_tag_filter", 0)
                )
                deduped = max(
                    0,
                    result.get("parsed", 0) - result.get("inserted", 0) - dropped_total,
                )
                result["deduped"] = deduped

                # Log to fetch_log
                history.log_fetch(
                    cron_run_rowid=cron_run_rowid,
                    source=source_name,
                    fetched=result.get("fetched", 0),
                    parsed=result.get("parsed", 0),
                    inserted=result.get("inserted", 0),
                    parse_errors=result.get("parse_errors", 0),
                    cleaned=result.get("cleaned", 0),
                    filtered=result.get("filtered", 0),
                    dropped_blacklist=result.get("dropped_blacklist", 0),
                    dropped_whitelist=result.get("dropped_whitelist", 0),
                    dropped_global_blacklist=result.get("dropped_global_blacklist", 0),
                    dropped_global_whitelist=result.get("dropped_global_whitelist", 0),
                    dropped_tag_filter=result.get("dropped_tag_filter", 0),
                    filters_applied=result.get("filters_applied", 0),
                    global_filters_applied=result.get("global_filters_applied", 0),
                    tags_used=result.get("tags_used", 0),
                    deduped=deduped,
                    error=result.get("error"),
                )

                total_inserted += result.get("inserted", 0)

            except TimeoutError as exc:
                logger.warning("[%s] batch job timed out: %s", source_name, exc)
                jobs.mark_timeout(job_id, str(exc), handle=handle)
                sources_failed += 1
            except Exception as exc:
                logger.exception("[%s] batch job failed", source_name)
                jobs.mark_failed(job_id, f"{type(exc).__name__}: {exc}", handle=handle)
                sources_failed += 1
            finally:
                hb_stop.set()
                hb_thread.join(timeout=2.0)

    except Exception:
        # The batch died mid-way: report it as aborted, not as a success.
        aborted = True
        raise
    finally:
        # Always close the cron_runs row — an aborted batch must not
        # stay recorded as a success.
        # Update cron_runs with final totals
        finished_at = datetime.now(CST).isoformat()
        # partial 也是「这一轮没抓全」, 不能记成功 —— 否则调度器的成功率
        # 统计会把缺口算成健康。
        success = 1 if (
            sources_failed == 0 and sources_partial == 0 and not aborted
        ) else 0
        if aborted:
            error = f"批次中断（已执行 {attempted}/{len(enabled)} 个信源）"
        elif sources_failed:
            error = f"{sources_failed} source(s) failed"
        elif sources_partial:
            error = f"{sources_partial} source(s) partial"
        else:
            error = None
        sources_run = attempted if aborted else len(enabled) - sources_failed
        try:
            conn = get_db()
            conn.execute(
                "UPDATE cron_runs SET finished_at=?, success=?, error=?, new_articles=?, sources_run=? WHERE id=?",
                (
                    finished_at,
                    success,
                    error,
                    total_inserted,
                    sources_run,
                    cron_run_rowid,
                ),
            )
            conn.commit()
        except Exception:
            pass
