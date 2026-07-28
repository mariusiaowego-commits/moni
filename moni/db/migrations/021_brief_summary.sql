-- Migration 021: brief_summary — compressed summary for one-click brief only
-- Keeps full ai_summary / bb_summary intact; brief rebuild prefers brief_summary when set.

ALTER TABLE articles ADD COLUMN brief_summary TEXT;
ALTER TABLE articles ADD COLUMN brief_summary_at TEXT;
