-- Migration 023: 行业事件日历 events 表
--
-- 从 articles 中通过 LLM 抽取行业事件（监管/产品/融资/技术/其他），
-- 支持按日期、主体、类型筛选，作为 F5 行业事件日历后端。
--
-- 设计要点：
-- * event_id = sha256(article_id + event_date + subject + event_type)[:16]
-- * UNIQUE(article_id, event_date, subject, event_type) 防重复
-- * extraction_status 状态机：pending → processing → completed / failed / skipped / normalize_failed
-- * review_status 人工审核：pending / confirmed / rejected
-- * 三层事务管道：normalize → insert → state-transition（同事务，失败全回滚）

CREATE TABLE IF NOT EXISTS events (
    event_id              TEXT PRIMARY KEY,
    article_id            TEXT NOT NULL,
    event_date            TEXT,
    event_date_precision  TEXT NOT NULL DEFAULT 'unknown'
        CHECK (event_date_precision IN ('exact','month','year','relative','unknown')),
    subject               TEXT NOT NULL,
    subject_raw           TEXT,
    event_type            TEXT NOT NULL
        CHECK (event_type IN ('regulation','product','financing','tech','other')),
    summary               TEXT,
    extraction_confidence INTEGER,
    review_status         TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending','confirmed','rejected')),
    extraction_status     TEXT NOT NULL DEFAULT 'pending'
        CHECK (extraction_status IN ('pending','processing','completed','failed','skipped','normalize_failed')),
    extraction_attempts   INTEGER NOT NULL DEFAULT 0,
    last_error            TEXT,
    extracted_at          TEXT,
    evidence_snippet      TEXT,
    normalize_version     INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT NOT NULL,
    UNIQUE(article_id, event_date, subject, event_type),
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
);

-- 常用查询索引
CREATE INDEX IF NOT EXISTS idx_events_event_date      ON events(event_date);
CREATE INDEX IF NOT EXISTS idx_events_event_type      ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_subject         ON events(subject);
CREATE INDEX IF NOT EXISTS idx_events_extraction_status ON events(extraction_status);
CREATE INDEX IF NOT EXISTS idx_events_review_status   ON events(review_status);
CREATE INDEX IF NOT EXISTS idx_events_article_id      ON events(article_id);
