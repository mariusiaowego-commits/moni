-- 067_case_insights.sql — 案例深度解读表 (P2-1 建表，本轮不跑 LLM)
-- schema_version 66 → 67

CREATE TABLE IF NOT EXISTS case_insights (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id        TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_hash     TEXT NOT NULL,
    provider       TEXT,
    model          TEXT,
    status         TEXT NOT NULL DEFAULT 'done'
        CHECK (status IN ('done', 'failed', 'stale')),
    body_json      TEXT,
    error          TEXT,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (case_id, prompt_version, input_hash),
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_case_insights_case ON case_insights(case_id, status);

ALTER TABLE cases ADD COLUMN insight_id INTEGER;
ALTER TABLE llm_jobs ADD COLUMN target_id TEXT;

INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (66, datetime('now'));
