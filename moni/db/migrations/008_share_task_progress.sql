-- Migration 008: share-task progress tracking (v1.0.5.17)
--
-- Idea: the user wants near-realtime visibility into the worker
-- pipeline so they don't have to stare at a stuck "pending" or
-- "running" row.  We add 3 columns to share_tasks so the worker
-- can checkpoint where it is in the 5-stage pipeline:
--
--   queued            — task is in the queue file, worker hasn't picked it up
--   fetching          — worker is reading article raw_content from the DB
--   building_prompt   — worker is composing the LLM prompt
--   invoking_llm      — worker is running `hermes chat` (5 min timeout)
--   writing_artifact  — worker is writing the output file to data/artifacts/
--
-- These are written via sqlite3 in default_worker.sh at each
-- stage transition.  The frontend polls /api/share/tasks every
-- 30s (existing behaviour) and reads these columns to show
-- progress (stage + percentage) in the queue table and a task
-- detail modal.
--
-- Backfill: existing rows get progress_stage = status (a reasonable
-- approximation: pending → queued, running → invoking_llm, done
-- → writing_artifact, failed → invoking_llm).  The 30s polling
-- cycle will overwrite these as new tasks come in.

ALTER TABLE share_tasks ADD COLUMN progress_stage TEXT;
ALTER TABLE share_tasks ADD COLUMN progress_pct INTEGER;
ALTER TABLE share_tasks ADD COLUMN progress_message TEXT;

-- Backfill: existing rows (pre-v1.0.5.17) get a reasonable
-- progress snapshot.  We use the existing `status` as a fallback
-- since we don't have stage information for old tasks.
UPDATE share_tasks SET
    progress_stage = CASE status
        WHEN 'pending'  THEN 'queued'
        WHEN 'running'  THEN 'invoking_llm'
        WHEN 'done'     THEN 'writing_artifact'
        WHEN 'failed'   THEN 'invoking_llm'
        ELSE 'queued'
    END,
    progress_pct = CASE status
        WHEN 'pending'  THEN 0
        WHEN 'running'  THEN 60
        WHEN 'done'     THEN 100
        WHEN 'failed'   THEN 60
        ELSE 0
    END
WHERE progress_stage IS NULL;
