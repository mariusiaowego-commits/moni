-- Migration 019: Add per-field BB fetch timestamps for independent deduplication
-- 
-- Currently all BB fields share bb_fetched_at. This migration adds
-- 4 new columns to track when each BB field was last fetched:
--   bb_score_fetched_at
--   bb_main_points_fetched_at
--   bb_key_quotes_fetched_at
--   bb_tags_fetched_at
--
-- This allows independent fetching of each BB field instead of
-- fetching all 6 fields when any one is requested.

ALTER TABLE articles ADD COLUMN bb_score_fetched_at TEXT;      -- ISO8601, score 拉取时间
ALTER TABLE articles ADD COLUMN bb_main_points_fetched_at TEXT; -- ISO8601, main_points 拉取时间
ALTER TABLE articles ADD COLUMN bb_key_quotes_fetched_at TEXT;  -- ISO8601, key_quotes 拉取时间
ALTER TABLE articles ADD COLUMN bb_tags_fetched_at TEXT;        -- ISO8601, tags 拉取时间

-- Backfill: copy existing bb_fetched_at to all new columns
-- This preserves the "already fetched" status for existing articles
UPDATE articles 
SET 
    bb_score_fetched_at = bb_fetched_at,
    bb_main_points_fetched_at = bb_fetched_at,
    bb_key_quotes_fetched_at = bb_fetched_at,
    bb_tags_fetched_at = bb_fetched_at
WHERE bb_fetched_at IS NOT NULL;
