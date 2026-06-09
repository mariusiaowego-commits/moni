-- Migration 007: tag display_name (v1.0.5.14)
--
-- Idea: the user wants to give tags a human-friendly Chinese name
-- (or any UI-friendly string) while keeping the ASCII / no-spaces
-- `name` column as the system primary key + FK reference for
-- source_tags / article_tags.  This mirrors the sources table
-- pattern (sources.name = "36kr_rss", sources.display_name = "36氪
-- RSS"), so it's a well-precedented design.
--
-- The `name` column is left strictly as-is.  Renaming a tag (changing
-- its `name`) is still not supported because the FK chain in
-- tag_definitions.name → source_tags.tag_name / article_tags.tag_name
-- has no ON UPDATE CASCADE, and the previous v1.0.5.14 attempt to
-- expose a rename through the API tripped an FK constraint mid-
-- transaction.  The user-facing "rename" is now done by editing
-- `display_name` instead — same effect on the UI, no FK dance.
--
-- Backfill: existing rows get display_name = name (no-op visually),
-- preserving compatibility with old clients that don't know about
-- the new column.  The seed_config step in v1.0.5.14 then re-runs
-- and overwrites the 5 default tags with their proper Chinese
-- display names (银行 / 支付 / AI×金融 / 监管 / 通用科技).

ALTER TABLE tag_definitions ADD COLUMN display_name TEXT;

-- Backfill existing rows: if display_name is NULL, fall back to the
-- existing `name` so the chip / list / tooltip render path always
-- has something to display.
UPDATE tag_definitions SET display_name = name WHERE display_name IS NULL;
