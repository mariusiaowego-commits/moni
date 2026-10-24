-- 036_normalize_36kr_source_id.sql - v1.0.61 (sprint-27 hotfix)
-- Live databases created before v1.0.34.2 used the legacy id
-- "src-36kr_rss", while config/sources.yaml and seed_config.py both use
-- "src-36kr-rss". Because sources.name is UNIQUE, any later seed run
-- attempted to insert the same name under the canonical id and failed
-- with "UNIQUE constraint failed: sources.name", rolling back the whole
-- seed (including newly added sources such as v1.0.60).
--
-- The source row is the FK parent for source_tags and
-- source_keyword_filters. We therefore copy the parent row to the
-- canonical id, move both child tables in place (keeping
-- source_keyword_filters.id so article_keyword_hits.filter_id survives),
-- and only then delete the legacy parent. If the canonical source already
-- exists, the same child move merges the legacy children into it.

BEGIN IMMEDIATE;

DROP TABLE IF EXISTS temp._m036_36kr_legacy;
CREATE TEMP TABLE _m036_36kr_legacy AS
SELECT id, name, display_name, source_type, url, fetch_interval_min,
       is_active, notes, fetch_config, created_at, updated_at
FROM sources
WHERE id = 'src-36kr_rss';

-- Free the legacy name so the canonical row can reuse the real name when
-- the canonical row does not exist yet.
UPDATE sources
SET name = '__m036_36kr_rss_legacy__'
WHERE id = 'src-36kr_rss'
  AND NOT EXISTS (SELECT 1 FROM sources WHERE id = 'src-36kr-rss');

INSERT OR IGNORE INTO sources
    (id, name, display_name, source_type, url, fetch_interval_min,
     is_active, notes, fetch_config, created_at, updated_at)
SELECT 'src-36kr-rss', name, display_name, source_type, url,
       fetch_interval_min, is_active, notes, fetch_config,
       created_at, updated_at
FROM _m036_36kr_legacy
WHERE NOT EXISTS (SELECT 1 FROM sources WHERE id = 'src-36kr-rss');

-- UPDATE OR IGNORE handles a tag already present on a canonical row;
-- the duplicate legacy tag is removed by the parent DELETE below.
UPDATE OR IGNORE source_tags
SET source_id = 'src-36kr-rss'
WHERE source_id = 'src-36kr_rss';

-- Keep filter ids stable. article_keyword_hits.filter_id then continues to
-- point at the same row instead of being SET NULL.
UPDATE source_keyword_filters
SET source_id = 'src-36kr-rss'
WHERE source_id = 'src-36kr_rss';

DELETE FROM sources
WHERE id = 'src-36kr_rss'
  AND EXISTS (SELECT 1 FROM sources WHERE id = 'src-36kr-rss');

INSERT OR IGNORE INTO schema_cleanup_log (migration, applied_at)
SELECT '036_normalize_36kr_source_id', datetime('now')
WHERE EXISTS (SELECT 1 FROM _m036_36kr_legacy);

DROP TABLE temp._m036_36kr_legacy;

COMMIT;
