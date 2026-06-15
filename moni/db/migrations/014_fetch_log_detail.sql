-- 014: Extend fetch_log with detailed filter/dedup/clean metrics
-- v1.0.6.2: Comprehensive cron logging

ALTER TABLE fetch_log ADD COLUMN cleaned INTEGER DEFAULT 0;
ALTER TABLE fetch_log ADD COLUMN filtered INTEGER DEFAULT 0;
ALTER TABLE fetch_log ADD COLUMN dropped_blacklist INTEGER DEFAULT 0;
ALTER TABLE fetch_log ADD COLUMN dropped_whitelist INTEGER DEFAULT 0;
ALTER TABLE fetch_log ADD COLUMN dropped_tag_filter INTEGER DEFAULT 0;
ALTER TABLE fetch_log ADD COLUMN filters_applied INTEGER DEFAULT 0;
ALTER TABLE fetch_log ADD COLUMN tags_used INTEGER DEFAULT 0;
ALTER TABLE fetch_log ADD COLUMN deduped INTEGER DEFAULT 0;
