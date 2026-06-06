-- Migration 006: tag-keyword content filter (v1.0.5.4)
--
-- Idea: the user wants to drive fetch-time content filtering from the
-- tag system.  Each tag can carry an optional list of keywords
-- ("AI 监管", "支付清算", ...).  When the fetcher processes a parsed
-- article, it builds the union of keywords from every tag the source
-- is bound to (via ``source_tags``); if the article's title+content
-- contains ANY of those keywords, it is kept, otherwise dropped.
--
-- This is intentionally simpler than the v1.0.5.4 design notes
-- suggested (no LLM scoring, no per-tag thresholds, no whitelist vs
-- blacklist split).  We just need: any-tag hit -> keep, no hit -> drop.
-- Tags that don't carry keywords are skipped (no effect), so the
-- existing 5 tag definitions stay backwards-compatible.
--
-- The match is substring-based (same shape as
-- ``source_keyword_filters``), not LLM, so it's free, fast, and works
-- for Chinese without tokenization.

ALTER TABLE tag_definitions ADD COLUMN keywords TEXT;  -- JSON array, nullable

-- Backfill is a no-op: existing rows have NULL keywords, which the
-- fetcher treats as "this tag doesn't constrain content".
