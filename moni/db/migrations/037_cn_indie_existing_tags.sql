-- 037_cn_indie_existing_tags.sql - v1.0.63
--
-- v1.0.60 gave the new chinese_independent_blogs source its own tag
-- ("independent-blog") with an intentionally empty keyword set.  Because
-- tag_definitions.keywords was empty, apply_tag_keyword_filter() became a
-- no-op pass-through for that source: every long-tail post (travel notes,
-- book reviews, diary entries) was ingested.  On top of that,
-- ArticleRepository.dedupe_insert only writes article_tags for tags that
-- were actually hit by a keyword, so all 105 ingested rows ended up with
-- zero tags and were invisible to tag filtering in the UI.
--
-- Fix: drop the one-off tag and bind the source to the project's existing
-- business tags instead.  Their keyword union becomes the ingest gate and
-- only the tags that actually match are written per article - the same
-- behaviour every other source already has.
--
-- The binding list mirrors seed_config._DEFAULT_SOURCE_TAGS.  Rows are
-- only inserted when both the source and the tag definition exist, so this
-- migration is safe on a database that has not been seeded yet.
-- Article cleanup for rows already ingested is a separate, reviewable data
-- operation (scripts/purge_cn_indie_unmatched.py); it is not done here.

BEGIN IMMEDIATE;

INSERT OR IGNORE INTO source_tags (source_id, tag_name, created_at)
SELECT s.id, t.name, datetime('now')
FROM sources s
JOIN tag_definitions t
  ON t.name IN ('APP', 'ai-fintech', 'banking', 'general-tech',
                'miniprogram', 'model-providers', 'payment',
                'regulation', 'wechat', 'workbuddy')
WHERE s.id = 'src-chinese-independent-blogs';

-- Remove the retired one-off tag.  Explicit child deletes first so the
-- result does not depend on PRAGMA foreign_keys being enabled on this
-- connection.  article_keyword_hits carries tag_name without an FK, so it
-- needs its own DELETE.
DELETE FROM article_tags         WHERE tag_name = 'independent-blog';
DELETE FROM article_keyword_hits WHERE tag_name = 'independent-blog';
DELETE FROM source_tags          WHERE tag_name = 'independent-blog';
DELETE FROM tag_definitions      WHERE name     = 'independent-blog';

-- Only record success when the source really ended up with the ten
-- bindings.  Without this guard an unseeded database would silently keep
-- zero bindings, turning the tag gate back into the pass-through this
-- migration exists to remove.
CREATE TABLE IF NOT EXISTS schema_cleanup_log (
    migration   TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_cleanup_log (migration, applied_at)
SELECT '037_cn_indie_existing_tags', datetime('now')
WHERE (
    SELECT COUNT(*) FROM source_tags
    WHERE source_id = 'src-chinese-independent-blogs'
) >= 10;

COMMIT;
