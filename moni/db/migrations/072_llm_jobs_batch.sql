-- 072_llm_jobs_batch.sql — 案例抽取批次分组
-- schema_version 71 → 72
--
-- 一次批量抽取会产生多条 case_extract 任务（每篇一行）。前端按 batch_id
-- 折叠成一行，展开看逐篇明细；不再复用 parent_job_id（那是父任务语义）。

ALTER TABLE llm_jobs ADD COLUMN batch_id TEXT;

CREATE INDEX IF NOT EXISTS idx_llm_jobs_batch_id ON llm_jobs(batch_id);
