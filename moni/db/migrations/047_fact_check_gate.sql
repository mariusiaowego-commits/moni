-- 047: 事实核查闸门留痕 + 核查方式 + 证据来源日期 (2026-11-18)
--
-- 背景: 白名单闸门按 conclusion 精确比较, 带后缀的【已核实】整条绕过;
-- 时效闸用抓取时间算年龄, days_old 恒为 0; 结论里没有"怎么核的"。 
-- Additive only: 历史行为 NULL, UI 自行回退。
-- allow-missing-tables: 手写的 v38 测试库没有 fact_check_evidence。

ALTER TABLE articles ADD COLUMN fact_check_gate TEXT;
ALTER TABLE articles ADD COLUMN fact_check_how TEXT;
ALTER TABLE fact_check_evidence ADD COLUMN source_date TEXT;
