-- Migration 029: LLM usage counters on llm_jobs (v1.0.32 sprint-13)
--
-- Q4 LLM 用量统计: hermes --usage-file 报告的 token/cost 落库, 供
-- /api/stats/llm-usage 看板聚合。三列均可空 (旧行无数据, 统计时
-- COALESCE 为 0)。

ALTER TABLE llm_jobs ADD COLUMN input_tokens INTEGER;
ALTER TABLE llm_jobs ADD COLUMN output_tokens INTEGER;
ALTER TABLE llm_jobs ADD COLUMN cost_usd REAL;
