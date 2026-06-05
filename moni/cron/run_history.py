"""run_history — persist cron run log to SQLite.

Writes two things after every job run:
1. ``fetch_log`` — one row per fetch source within the job
2. ``cron_runs`` — one row per top-level cron job execution

Both tables live in the same news.db as the rest of the moni schema.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ..db.connection import get_db

logger = logging.getLogger(__name__)

class CronRunHistory:
    """Persist apscheduler job run outcomes to SQLite.

    v1.0: WAL mode + busy_timeout are set at the connection level
    (see ``moni.db.connection``); this class only writes.
    """

    def log_run(
        self,
        job_id: str,
        *,
        triggered_at: Optional[str] = None,
        finished_at: Optional[str] = None,
        success: bool = True,
        error: Optional[str] = None,
        new_articles: int = 0,
        sources_run: Optional[int] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> int:
        """Write one row to ``cron_runs``.

        Args:
            job_id:         APScheduler job id (e.g. 'nightly_fetch')
            triggered_at:    ISO8601 wall-clock time the job fired (default: now UTC)
            finished_at:    ISO8601 wall-clock time the job completed
            success:        True if the job completed without raising
            error:          Exception message if success is False
            new_articles:   Number of new articles inserted by this run
            sources_run:     Number of sources that were actually fetched
            extra:          Arbitrary metadata dict (stored as JSON)

        Returns:
            The rowid of the inserted cron_runs row.
        """
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()

        row = conn.execute(
            """
            INSERT INTO cron_runs
                (job_id, triggered_at, finished_at, success, error,
                 new_articles, sources_run, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                triggered_at or now,
                finished_at or now,
                1 if success else 0,
                error,
                new_articles,
                sources_run,
                _json_dumps(extra) if extra else None,
            ),
        )
        conn.commit()
        rowid = row.lastrowid
        logger.debug("Logged cron_runs rowid=%s for job_id=%r", rowid, job_id)
        return int(rowid) if rowid is not None else 0

    def log_fetch(
        self,
        cron_run_rowid: int,
        source: str,
        fetched: int,
        parsed: int,
        inserted: int,
        parse_errors: int,
        error: Optional[str] = None,
    ) -> None:
        """Write one row to ``fetch_log`` for a single source within a cron run.

        Args:
            cron_run_rowid:  rowid from the parent ``cron_runs`` INSERT
            source:          Source name, e.g. '36kr_rss'
            fetched:        Number of raw articles fetched
            parsed:         Number successfully parsed
            inserted:       Number of new articles inserted
            parse_errors:   Number of parse errors
            error:          Exception message if the source fetch failed
        """
        conn = get_db()
        conn.execute(
            """
            INSERT INTO fetch_log
                (cron_run_rowid, source, fetched, parsed, inserted,
                 parse_errors, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cron_run_rowid,
                source,
                fetched,
                parsed,
                inserted,
                parse_errors,
                error,
            ),
        )
        conn.commit()
        logger.debug(
            "Logged fetch_log for cron_run=%d source=%s inserted=%d",
            cron_run_rowid, source, inserted,
        )


def _json_dumps(data: dict[str, Any]) -> str:
    """Serialize dict to JSON string, using stdlib json."""
    import json
    return json.dumps(data, ensure_ascii=False)
