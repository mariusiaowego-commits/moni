-- 067_share_case_ids.sql — share_tasks 扩展 case_ids 与 item_kind (P2-1 方案 A 融合)
-- schema_version 66 → 67

ALTER TABLE share_tasks ADD COLUMN case_ids TEXT;
ALTER TABLE share_tasks ADD COLUMN item_kind TEXT NOT NULL DEFAULT 'article';

CREATE INDEX IF NOT EXISTS idx_share_tasks_item_kind ON share_tasks(item_kind, status);

INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (67, datetime('now'));
