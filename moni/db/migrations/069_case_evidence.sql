-- 069_case_evidence.sql — 原文证据校验标记 + 在正文中的位置 (0 LLM)
-- schema_version 68 → 69
--
-- 背景：evidence_snippet 是模型挑的 1-2 句，之前没有任何校验，
-- 分享/详情页无法区分「原文引用」与「模型转述」。这里存两列：
--   evidence_verified  1 = 该句在 articles.raw_content 里按空白归一后逐字命中
--   evidence_offset    命中起点（字符偏移），用于前端截上下文与原文定位
-- 未命中时 verified=0、offset=NULL，界面据此标注「模型转述」。

ALTER TABLE cases ADD COLUMN evidence_verified INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cases ADD COLUMN evidence_offset INTEGER;
