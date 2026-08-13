-- Migration 026: 简报 web 模式 — evidence_rendered_at + has_web_fact_check
-- schema_version 25 → 26
--
-- evidence_rendered_at: 某次简报生成时实际使用核查结果的时刻 (审计)
-- has_web_fact_check:   是否已完成联网核查 (fact_check_method=web_search / 有证据链)
--
-- schema_version 单 PK: INSERT OR IGNORE (sprint 05 教训)

ALTER TABLE articles ADD COLUMN evidence_rendered_at TEXT;
ALTER TABLE articles ADD COLUMN has_web_fact_check INTEGER NOT NULL DEFAULT 0;

-- 存量回填: 已 web_search 或已有 evidence 行 → 标已联网核查
UPDATE articles
SET has_web_fact_check = 1
WHERE fact_check_method = 'web_search'
   OR id IN (SELECT DISTINCT article_id FROM fact_check_evidence);

INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (26, datetime('now'));
