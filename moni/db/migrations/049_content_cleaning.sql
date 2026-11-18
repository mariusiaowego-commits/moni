-- 048: 内容清洗 (sprint-56)
--
-- Additive only.  Historical rows are not backfilled here; `moni clean-articles
-- --apply` fills them behind a dry-run gate.
--
-- content_contract 说明「这个源本来该长什么样」，避免把合规受限的空正文
-- 误报成抓取故障：
--   full_text  期望有正文（默认）
--   snippet    源只给摘要（bestblogs / qbitai）
--   short_form 一句话快讯或推文（36kr / xbot_feed / xwatch）
--   title_only 合规限制，只能抓标题

ALTER TABLE articles ADD COLUMN content_state TEXT;
ALTER TABLE articles ADD COLUMN content_chars INTEGER;
ALTER TABLE articles ADD COLUMN content_reasons TEXT;
ALTER TABLE articles ADD COLUMN content_cleaner TEXT;
ALTER TABLE articles ADD COLUMN content_cleaned_at TEXT;
CREATE INDEX IF NOT EXISTS idx_articles_content_state ON articles(content_state);

ALTER TABLE sources ADD COLUMN content_contract TEXT;
UPDATE sources SET content_contract = 'snippet'
 WHERE name IN ('bestblogs', 'bestblog-x', 'bestblog-podcast', 'bestblog-yt', 'qbitai');
UPDATE sources SET content_contract = 'short_form'
 WHERE name IN ('xbot_feed', 'xwatch', 'follow_builders')
    OR url LIKE '%36kr.com%';

CREATE TABLE IF NOT EXISTS cleaning_runs (
    id                  TEXT PRIMARY KEY,
    mode                TEXT NOT NULL,          -- preview | apply
    scope               TEXT NOT NULL,          -- all | source:<name>
    status              TEXT NOT NULL,          -- running | done | failed
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    scanned             INTEGER NOT NULL DEFAULT 0,
    changed             INTEGER NOT NULL DEFAULT 0,
    backfilled          INTEGER NOT NULL DEFAULT 0,
    emptied             INTEGER NOT NULL DEFAULT 0,
    content_empty       INTEGER NOT NULL DEFAULT 0,
    content_short       INTEGER NOT NULL DEFAULT 0,
    chars_removed       INTEGER NOT NULL DEFAULT 0,
    archived_test_rows  INTEGER NOT NULL DEFAULT 0,
    details_json        TEXT,
    error               TEXT
);
CREATE INDEX IF NOT EXISTS idx_cleaning_runs_started ON cleaning_runs(started_at);
