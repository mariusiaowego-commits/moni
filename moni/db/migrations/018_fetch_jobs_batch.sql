-- 018: fetch_jobs batch support (v1.0.8.9)
--
-- "全部抓取" now runs as an async batch job instead of blocking.
-- Each source in the batch gets its own fetch_jobs row (as before),
-- but they share a common batch_id so the frontend can poll a single
-- endpoint to get aggregate progress across all sources.

ALTER TABLE fetch_jobs ADD COLUMN batch_id TEXT;
CREATE INDEX IF NOT EXISTS idx_fetch_jobs_batch_id ON fetch_jobs(batch_id);
