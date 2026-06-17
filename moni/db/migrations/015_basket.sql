-- 015_basket.sql
-- 菜篮子: 被过滤丢弃的文章暂存区 (v1.0.6.9)

CREATE TABLE IF NOT EXISTS basket (
    id                      TEXT PRIMARY KEY,
    title                   TEXT NOT NULL,
    url                     TEXT NOT NULL,
    source                  TEXT NOT NULL,
    publisher               TEXT,
    published_at            TEXT,
    fetched_at              TEXT NOT NULL,
    raw_content             TEXT,
    dropped_reason          TEXT NOT NULL,      -- 'blacklist' | 'whitelist' | 'tag_filter'
    dropped_by              TEXT,               -- 命中的关键词或 tag 名
    rescued_at              TEXT,               -- 被 rescue 到 articles 后标记时间
    created_at              TEXT NOT NULL,
    UNIQUE(source, url)
);
CREATE INDEX IF NOT EXISTS idx_basket_source ON basket(source);
CREATE INDEX IF NOT EXISTS idx_basket_created ON basket(created_at);
CREATE INDEX IF NOT EXISTS idx_basket_rescued ON basket(rescued_at);
