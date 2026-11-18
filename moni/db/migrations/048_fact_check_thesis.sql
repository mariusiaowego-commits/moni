-- 048: 文章主结论（thesis）(2026-11-18)
--
-- 联网档开始要求模型先写"作者想让你相信什么"，用于后续论点级审计。
-- Additive only：历史行 NULL。

ALTER TABLE articles ADD COLUMN fact_check_thesis TEXT;
