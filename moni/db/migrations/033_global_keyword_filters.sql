-- Migration 033: 全局关键词过滤规则 (PRD v1.0.40 / sprint-20 PR2)
-- 独立于 source_keyword_filters，对所有信源生效的跨源黑/白名单规则

CREATE TABLE IF NOT EXISTS global_keyword_filters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mode            TEXT NOT NULL,                           -- 'whitelist' | 'blacklist'
    match_field     TEXT NOT NULL DEFAULT 'title+content',   -- 'title' | 'content' | 'title+content'
    keywords        TEXT NOT NULL,                           -- JSON array of strings
    is_active       INTEGER NOT NULL DEFAULT 1,
    notes           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_global_kw_active ON global_keyword_filters(is_active);

-- 透出全局过滤统计到 fetch_log
ALTER TABLE fetch_log ADD COLUMN dropped_global_blacklist INTEGER DEFAULT 0;
ALTER TABLE fetch_log ADD COLUMN dropped_global_whitelist INTEGER DEFAULT 0;
ALTER TABLE fetch_log ADD COLUMN global_filters_applied INTEGER DEFAULT 0;
