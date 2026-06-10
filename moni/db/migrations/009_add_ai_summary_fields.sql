-- Migration 009: article-level AI summary / fact-check fields (v1.0.5.18)
--
-- Idea: the user wants the share pipeline to optionally include an
-- AI-generated abstract and a fact-check analysis for each article
-- they select.  We split these from the existing `summary` /
-- `fact_check_*` fields because the existing fields are *fetch-time*
-- values (auto-generated when the article was crawled) while the
-- new fields are *share-time* values (freshly generated when the
-- user opts in).
--
-- Four new columns on `articles`:
--   has_ai_summary   INTEGER (0/1) — flag: have we generated an
--     on-demand summary for this article?  Used by the worker to
--     skip the "call hermes for a summary" step if it's already
--     been done.
--   has_fact_check   INTEGER (0/1) — flag: have we done a fact-check
--     for this article?  Same purpose.
--   ai_summary       TEXT — the freshly-generated summary, or NULL
--     if the user never opted in.
--   ai_summary_at    TEXT — ISO8601 timestamp of the most recent
--     on-demand generation.  NULL if never generated.
--
-- We do NOT touch the existing `summary` / `fact_check_*` fields
-- (PRD §5.1: those are fetch-time values and stay as-is).
--
-- Backfill: every existing row gets has_ai_summary=0 and
-- has_fact_check=0 because nothing was generated on-demand before
-- v1.0.5.18.  The two TEXT columns default to NULL, which SQLite
-- already does for nullable columns without an explicit DEFAULT.

ALTER TABLE articles ADD COLUMN has_ai_summary INTEGER NOT NULL DEFAULT 0;
ALTER TABLE articles ADD COLUMN has_fact_check INTEGER NOT NULL DEFAULT 0;
ALTER TABLE articles ADD COLUMN ai_summary TEXT;
ALTER TABLE articles ADD COLUMN ai_summary_at TEXT;

-- Backfill: existing rows pre-v1.0.5.18 have no on-demand analysis.
UPDATE articles SET has_ai_summary = 0 WHERE has_ai_summary IS NULL OR has_ai_summary != 0;
UPDATE articles SET has_fact_check = 0 WHERE has_fact_check IS NULL OR has_fact_check != 0;
-- (The above is defensive: NOT NULL DEFAULT 0 means backfill is
-- technically a no-op, but we run it explicitly so a fresh
-- re-apply of this migration on an already-migrated DB doesn't
-- accidentally zero out flags that have been set since.)
