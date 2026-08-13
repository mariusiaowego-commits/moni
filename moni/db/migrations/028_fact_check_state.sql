-- Migration 028: 事实核查+ 状态机 (人工覆写, 与 cases 同构)
-- schema_version 27 → 28
--
-- articles 新加 4 字段 (spec §3.1):
--   fact_check_state         TEXT  draft|verified|published|deprecated|archived
--   fact_check_reviewed_by   TEXT  审核人
--   fact_check_reviewed_at   TEXT  ISO8601
--   fact_check_review_note   TEXT  审核备注 (端点 reason)
--
-- 流转 (§3.2): 相邻档单向 + verified 可直达 deprecated/archived
-- schema_version 单 PK: INSERT OR IGNORE (sprint 05 教训)

ALTER TABLE articles ADD COLUMN fact_check_state TEXT NOT NULL DEFAULT 'draft';
ALTER TABLE articles ADD COLUMN fact_check_reviewed_by TEXT;
ALTER TABLE articles ADD COLUMN fact_check_reviewed_at TEXT;
ALTER TABLE articles ADD COLUMN fact_check_review_note TEXT;

CREATE INDEX IF NOT EXISTS idx_articles_state ON articles(fact_check_state);

-- 存量回填: 已核查 = verified (业务已在用的结论视为确认过)
UPDATE articles SET fact_check_state = 'verified'
WHERE COALESCE(has_fact_check, 0) = 1
  AND fact_check_state = 'draft';

INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (28, datetime('now'));
