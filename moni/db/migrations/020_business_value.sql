-- Migration 020: business value + quality score (v1.0.10 one-click brief)
-- Article-level cache so re-brief in another format reuses enrichment.

ALTER TABLE articles ADD COLUMN business_value_score INTEGER;  -- 0-100
ALTER TABLE articles ADD COLUMN business_value_note TEXT;     -- 2-4 句中文
ALTER TABLE articles ADD COLUMN business_value_at TEXT;
ALTER TABLE articles ADD COLUMN quality_score INTEGER;       -- 0-100 when no bb_score
ALTER TABLE articles ADD COLUMN quality_score_at TEXT;
