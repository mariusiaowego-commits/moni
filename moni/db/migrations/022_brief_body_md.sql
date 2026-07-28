-- Migration 022: agent-laid-out article body for offline HTML brief
-- Keeps raw_content untouched; brief HTML prefers brief_body_md when set.

ALTER TABLE articles ADD COLUMN brief_body_md TEXT;
ALTER TABLE articles ADD COLUMN brief_body_at TEXT;
