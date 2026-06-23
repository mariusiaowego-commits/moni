-- 016: fetch_jobs table — async fetch job tracking with per-feed progress (v1.0.7.6)
--
-- When the user clicks "立即抓取" in the SPA, the request returns immediately
-- with a job_id and runs the actual fetch in a background thread. The frontend
-- polls GET /api/fetch/jobs/{job_id} every 5s to render a progress bar.
--
-- Lifecycle: pending → running → (done | failed | timeout)
-- Single source per job (multi-source goes through /api/fetch/all/run which
-- still uses cron_runs + fetch_log and is not affected by this table).

CREATE TABLE IF NOT EXISTS fetch_jobs (
    id                TEXT PRIMARY KEY,            -- uuid4
    source            TEXT NOT NULL,               -- e.g. 'bestblog-yt'
    status            TEXT NOT NULL DEFAULT 'running',  -- running | done | failed | timeout
    total_feeds       INTEGER NOT NULL DEFAULT 0,  -- total feeds to process (set at start)
    done_feeds        INTEGER NOT NULL DEFAULT 0,  -- feeds completed (incremented per feed)
    current_feed      TEXT,                        -- title of feed currently being fetched
    articles_so_far   INTEGER NOT NULL DEFAULT 0,  -- articles accumulated so far
    started_at        TEXT NOT NULL,               -- ISO datetime
    finished_at       TEXT,                        -- ISO datetime (set on terminal status)
    elapsed_seconds   INTEGER NOT NULL DEFAULT 0,  -- updated on each poll
    error             TEXT,                        -- error message (failed / timeout)
    result_json       TEXT                         -- final result dict (done status)
);

CREATE INDEX IF NOT EXISTS idx_fetch_jobs_status ON fetch_jobs(status);
CREATE INDEX IF NOT EXISTS idx_fetch_jobs_source ON fetch_jobs(source);
CREATE INDEX IF NOT EXISTS idx_fetch_jobs_started_at ON fetch_jobs(started_at);
