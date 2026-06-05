-- moni v1.0 schema (PRD §3.1–3.4)
-- articles / tag_definitions / article_tags / share_tasks + FTS5

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- articles table
CREATE TABLE IF NOT EXISTS articles (
    id                      TEXT PRIMARY KEY,  -- sha256(source + url + title)[:16]
    title                   TEXT NOT NULL,
    url                     TEXT NOT NULL,
    source                  TEXT NOT NULL,      -- '36kr' | 'mpaypass' | 'qbitai' | 'wechat_rss' | ...
    publisher               TEXT,               -- 发布者（公众号名/作者）
    published_at            TEXT,               -- ISO8601，内容发布日期
    fetched_at              TEXT NOT NULL,      -- ISO8601，录入日期
    raw_content             TEXT,               -- 抓取到的正文（HTML 解析后纯文本）
    summary                 TEXT,               -- LLM 生成摘要
    summary_at              TEXT,               -- summary 生成时间
    fact_check_conclusion   TEXT,               -- 事实核查结论（NULL 表示未做）
    fact_check_method       TEXT,               -- 'llm_inference' | 'web_search' | 'human'
    fact_checked_at         TEXT,               -- 事实核查时间
    fact_check_note         TEXT,               -- 备注
    UNIQUE(source, url)
);
CREATE INDEX IF NOT EXISTS idx_articles_fetched_at  ON articles(fetched_at);
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_source     ON articles(source);

-- FTS5 virtual table for full-text search
-- v1.0: data volume small (<10k rows), no optimisation needed;
-- note: FTS5 triggers keep articles_fts in sync with articles automatically
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title, publisher, raw_content, summary,
    content='articles', content_rowid='rowid'
);

-- tag_definitions table
CREATE TABLE IF NOT EXISTS tag_definitions (
    name        TEXT PRIMARY KEY,
    description TEXT,
    color       TEXT,               -- hex '#RRGGBB'，给 UI 用
    created_at  TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1
);

-- article_tags table (many-to-many)
CREATE TABLE IF NOT EXISTS article_tags (
    article_id  TEXT NOT NULL,
    tag_name    TEXT NOT NULL,
    predicted   INTEGER NOT NULL DEFAULT 1,  -- 1=LLM 预测，0=用户人工打
    created_at  TEXT NOT NULL,
    PRIMARY KEY (article_id, tag_name),
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_name)   REFERENCES tag_definitions(name) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_article_tags_tag ON article_tags(tag_name);

-- share_tasks table (manifest queue)
CREATE TABLE IF NOT EXISTS share_tasks (
    id          TEXT PRIMARY KEY,             -- uuid4
    format      TEXT NOT NULL,                -- 'markdown' | 'info_graphic' | ...
    article_ids TEXT NOT NULL,                -- JSON array
    output_path TEXT,                         -- default profile 写完后回填
    status      TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'running' | 'done' | 'failed'
    error       TEXT,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    extra       TEXT                          -- JSON，格式特定参数
);
CREATE INDEX IF NOT EXISTS idx_share_tasks_status ON share_tasks(status);

-- cron_runs table — one row per top-level cron job execution (PRD §4.6)
CREATE TABLE IF NOT EXISTS cron_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL,            -- APScheduler job id, e.g. 'fetch_36kr_rss'
    triggered_at    TEXT NOT NULL,            -- ISO8601 wall-clock time the job fired
    finished_at     TEXT NOT NULL,            -- ISO8601 wall-clock time the job completed
    success         INTEGER NOT NULL DEFAULT 1, -- 1 = success, 0 = failed
    error           TEXT,                      -- exception message if success=0
    new_articles    INTEGER NOT NULL DEFAULT 0,
    sources_run     INTEGER,
    extra           TEXT                       -- JSON metadata
);

-- fetch_log table — one row per source fetch within a cron_runs job
CREATE TABLE IF NOT EXISTS fetch_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cron_run_rowid   INTEGER NOT NULL,         -- FK to cron_runs.id
    source           TEXT NOT NULL,            -- source_name, e.g. '36kr_rss'
    fetched          INTEGER NOT NULL DEFAULT 0,
    parsed           INTEGER NOT NULL DEFAULT 0,
    inserted         INTEGER NOT NULL DEFAULT 0,
    parse_errors     INTEGER NOT NULL DEFAULT 0,
    error            TEXT,
    FOREIGN KEY (cron_run_rowid) REFERENCES cron_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_fetch_log_cron_run ON fetch_log(cron_run_rowid);
CREATE INDEX IF NOT EXISTS idx_fetch_log_source  ON fetch_log(source);

