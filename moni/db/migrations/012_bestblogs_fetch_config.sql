-- Migration 012: Add fetch_config to sources + BestBlogs source
-- v1.0.5.23

-- Add fetch_config column (idempotent via Python-level duplicate column check)
ALTER TABLE sources ADD COLUMN fetch_config TEXT;

-- Insert BestBlogs source (idempotent via OR IGNORE)
INSERT OR IGNORE INTO sources (id, name, display_name, source_type, url, fetch_interval_min, is_active, fetch_config, created_at, updated_at)
VALUES (
    'src-bestblogs',
    'bestblogs',
    'BestBlogs 公众号聚合',
    'rss',
    'https://raw.githubusercontent.com/ginobefun/BestBlogs/main/opml/bestblogs_wechat2rss_opml_all.opml',
    120,
    1,
    '{"max_feeds": 375, "max_articles_per_feed": 5, "max_articles_total": 500, "request_delay": 0.5}',
    datetime('now'),
    datetime('now')
);

-- NOTE: source_tags binding (bestblogs → wechat) is in seed_config.py,
-- not here, because tag_definitions may not exist yet at migration time.
