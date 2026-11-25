-- 061_case_metrics.sql — 案例结构化指标 + 原始输出保留 + 动作来源标记 (P0-3 / P0-2)
-- schema_version 60 → 61

CREATE TABLE IF NOT EXISTS case_metrics (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id        TEXT NOT NULL,
    metric_type    TEXT NOT NULL,
    value_num      REAL,
    value_approx   TEXT,
    unit           TEXT NOT NULL,
    period         TEXT,
    period_type    TEXT,
    value_raw      TEXT NOT NULL,
    evidence_quote TEXT,
    is_primary     INTEGER NOT NULL DEFAULT 0,
    source_type    TEXT NOT NULL DEFAULT 'auto_llm'
        CHECK (source_type IN ('auto_llm', 'rule_backfill', 'manual_user')),
    created_at     TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_case_metrics_case ON case_metrics(case_id);
CREATE INDEX IF NOT EXISTS idx_case_metrics_type ON case_metrics(metric_type, unit);

ALTER TABLE cases ADD COLUMN raw_json TEXT;
ALTER TABLE cases ADD COLUMN case_action_source TEXT;
ALTER TABLE cases ADD COLUMN search_text TEXT;
UPDATE cases SET case_action_source = 'default' WHERE case_action_source IS NULL;

INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (61, datetime('now'));
