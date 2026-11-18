-- 046: 事实核查人话结论 + 逐条声明/判定 (share 文案修复, 2026-11-18)
--
-- 背景: PNG 分享把 fact_check_note 里的 rationale 原样枚举成一句话，
-- 因为全链路只有三档标签、没有一句人话结论，也没有逐条判定。
-- Additive only: 历史行为 NULL，由 moni/share/enrichment.py 回退到
-- rationale 渲染，不改写既有数据。
-- allow-missing-tables: 手写的 v38 测试库没有 fact_check_evidence。

ALTER TABLE articles ADD COLUMN fact_check_summary TEXT;
ALTER TABLE fact_check_evidence ADD COLUMN claim TEXT;
ALTER TABLE fact_check_evidence ADD COLUMN verdict TEXT;
