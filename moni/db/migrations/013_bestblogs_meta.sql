-- Migration 013: BestBlogs API 增强元数据字段
-- v1.0.5.25

-- 按需拉取 BestBlogs 元数据（分享时触发）
ALTER TABLE articles ADD COLUMN bb_score INTEGER;
ALTER TABLE articles ADD COLUMN bb_summary TEXT;
ALTER TABLE articles ADD COLUMN bb_one_sentence TEXT;
ALTER TABLE articles ADD COLUMN bb_main_points TEXT;   -- JSON [{point, explanation}]
ALTER TABLE articles ADD COLUMN bb_key_quotes TEXT;    -- JSON ["quote1", ...]
ALTER TABLE articles ADD COLUMN bb_tags TEXT;          -- JSON ["AI Agent", ...] (BestBlogs tags, not moni tags)
ALTER TABLE articles ADD COLUMN bb_resource_id TEXT;   -- RAW_xxxxx (BestBlogs resource ID)
ALTER TABLE articles ADD COLUMN bb_fetched_at TEXT;    -- ISO8601, 拉取时间（防重复调用）
