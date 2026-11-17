-- 045: Pin quality + business-value harness (sprint 52, design 2026-11-16 v4)
--
-- Additive only.  Historical values are never backfilled: rows are identified
-- by quality_formula / business_value_status and re-scored lazily.

ALTER TABLE articles ADD COLUMN quality_status TEXT;
ALTER TABLE articles ADD COLUMN quality_error TEXT;
ALTER TABLE articles ADD COLUMN quality_formula TEXT;
ALTER TABLE articles ADD COLUMN quality_breakdown_json TEXT;
ALTER TABLE articles ADD COLUMN quality_input_hash TEXT;

ALTER TABLE articles ADD COLUMN business_value_status TEXT;
ALTER TABLE articles ADD COLUMN business_value_error TEXT;
ALTER TABLE articles ADD COLUMN business_value_breakdown_json TEXT;
ALTER TABLE articles ADD COLUMN business_value_hash TEXT;
ALTER TABLE articles ADD COLUMN business_value_rubric TEXT;
