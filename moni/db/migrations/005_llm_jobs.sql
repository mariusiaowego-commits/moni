-- Migration 005: LLM async jobs queue (v1.0.5.1)
--
-- Problem: /api/articles/{id}/summary and /fact-check used to invoke
-- ``hermes chat`` synchronously, blocking the request for up to 60s
-- (the v1.0 timeout) and surfacing a hard error to the user.  The
-- frontend had no way to show progress and no way to retry.
--
-- Fix: introduce a lightweight async job table.  The HTTP route
-- enqueues a row and returns 202 with the job id immediately; a
-- background thread (moni/llm/jobs.py) drains the queue, calls the
-- LLM, and updates status / result / error.
--
-- The shape mirrors ``share_tasks`` (003 share manifest) on purpose —
-- same operational semantics: enqueue → worker → done/failed.

CREATE TABLE IF NOT EXISTS llm_jobs (
    id           TEXT PRIMARY KEY,         -- uuid4
    task_type    TEXT NOT NULL,            -- 'summary' | 'fact_check' | (future: 'tag_predict')
    article_id   TEXT NOT NULL,            -- FK -> articles.id
    provider     TEXT,                     -- hermes provider name (snapshot at enqueue)
    model        TEXT,                     -- hermes model id   (snapshot at enqueue)
    status       TEXT NOT NULL DEFAULT 'pending',
                                            -- pending | running | done | failed
    result       TEXT,                     -- LLM output (summary text or fact-check conclusion)
    error        TEXT,                     -- error message (when status='failed')
    attempts     INTEGER NOT NULL DEFAULT 0,  -- how many LLM calls we've made so far
    duration_ms  INTEGER,                  -- wall-clock time of the last LLM call
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_llm_jobs_status      ON llm_jobs(status);
CREATE INDEX IF NOT EXISTS idx_llm_jobs_article_id  ON llm_jobs(article_id);
CREATE INDEX IF NOT EXISTS idx_llm_jobs_created_at  ON llm_jobs(created_at DESC);
