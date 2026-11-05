-- 039_xbot_feed_source.sql - v1.0.67 (sprint-30)
--
-- v1.0.66 (PR #172) added the xbot_feed source, but only to
-- seed_config.py and config/sources.yaml -- there was no migration.
-- Consequences for an existing database:
--   * a database that never ran `moni db seed-config` has no such row
--     in sources, so /config does not show it and `moni fetch
--     --source xbot_feed` cannot find its fetch_config;
--   * running seed-config does add it, but the ON CONFLICT DO UPDATE
--     clause in seed_config.py resets the display_name / url /
--     fetch_interval_min / notes / fetch_config the user edited in
--     /config and re-adds the tag bindings the user unticked.
--
-- This migration makes the upgrade independent of seed-config: it only
-- INSERTs, it never UPDATEs an existing row.
--
-- Semantics (the difference from seed_config is deliberate):
--   * INSERT ... WHERE NOT EXISTS -- an existing row is not touched at
--     all. A source the user deactivated (is_active=0) stays
--     deactivated, a renamed source keeps its name, and edited
--     fetch_config survives.
--   * tag_definitions.keywords is never touched -- the user's keywords
--     are the user's data.
--   * no tag is created -- the four bound tags are existing business
--     tags; a tag that is not in the library produces no link (the
--     source_tags.tag_name foreign key points at tag_definitions and
--     INSERT OR IGNORE does NOT swallow a foreign key violation).
--   * a source the user deleted is restored (Mttt decided this on
--     2026-11-05, question Q-O-2): `moni db init` runs seed() every
--     time and seed() re-adds the row, so not restoring it would mean
--     "one db init later the source is back anyway". Deactivating is
--     the documented way to turn a source off (USER_GUIDE.md 3.1).
--
-- Why source_tags is written as INSERT ... SELECT ... FROM sources:
--   source_tags.source_id has a foreign key to sources(id) and
--   source_tags.tag_name has one to tag_definitions(name) (004_
--   sources_and_tags.sql). The connection turns foreign keys on
--   (connection.py:121) and INSERT OR IGNORE does not swallow a
--   foreign key violation -- it raises FOREIGN KEY constraint failed.
--   Selecting from sources JOIN tag_definitions therefore produces rows
--   only when both ends exist, so no dangling link can be created.
--   This mirrors migration 037 (037_cn_indie_existing_tags.sql:25-26).
--
-- Why the source insert checks sources.name:
--   sources.name is UNIQUE (004_sources_and_tags.sql:15). The user may
--   have deleted the seeded row and created their own source under
--   their own id with name = 'xbot_feed' (the "new source" form in
--   /config lets the user pick the id). Inserting the migration row
--   anyway would violate the unique constraint and abort the whole
--   migration, which would pin schema_version at 38 and break every
--   `moni db *` subcommand (each of them runs migrate() first). So the
--   name check treats "the user already owns that name" as satisfied:
--   the migration stays a no-op instead of failing.
--
-- WARNING (literal constraint -- do not break it): no string literal
--   in this file may contain ";", "--" or "/*". migrate.py's
--   _run_sql_file first tries executescript (which handles ";" inside a
--   literal correctly), but as soon as executescript fails for any
--   reason the fallback path is a naive sql.split(";"), which cuts a
--   literal in half -> "unrecognized token" -> the migration dies ->
--   schema_version stays at 38 and every `moni db *` subcommand keeps
--   failing. "--" is fatal on both paths because migrate.py:52 strips
--   everything after a "--" with re.sub(r"--+.*", "", raw). The 37
--   existing .sql files have zero hits for ";" / "--" / "/*" inside a
--   literal -- keep it that way.

BEGIN IMMEDIATE;

INSERT INTO sources
    (id, name, display_name, source_type, url, fetch_interval_min,
     is_active, notes, fetch_config, created_at, updated_at)
SELECT
    'src-xbot-feed',
    'xbot_feed',
    'X-bot 精选',
    'json',
    'https://github.com/mariusiaowego-commits/xbot-feed.git',
    60,
    1,
    'X-bot 精选 feed (私有仓库 xbot-feed 的 feed 目录下的 json 文件)。moni 只做导入，私有仓库访问权来自机器已有的 git 凭据。',
    '{"repo_url": "https://github.com/mariusiaowego-commits/xbot-feed.git", "clone_dir": "", "bypass_keyword_filters": true, "blacklisted_handles": ["jkghzone", "sensenxnyz", "Eason3110188520", "duizhanggege", "HunchoJ01"], "topic_tags": {"wechat": ["wechat", "miniprogram"], "xcircle": ["general-tech", "ai-fintech"]}, "max_days": 30, "git_timeout": 120, "skip_existing_urls": true}',
    datetime('now'),
    datetime('now')
WHERE NOT EXISTS (
    SELECT 1 FROM sources
    WHERE id = 'src-xbot-feed' OR name = 'xbot_feed'
);

-- The four candidate tag bindings. A tag that is not in tag_definitions
-- produces no row (see the header note), so no dangling link.
INSERT OR IGNORE INTO source_tags (source_id, tag_name, created_at)
SELECT s.id, t.name, datetime('now')
FROM sources s
JOIN tag_definitions t
  ON t.name IN ('wechat', 'miniprogram', 'general-tech', 'ai-fintech')
WHERE s.id = 'src-xbot-feed';

-- Only record success when the source really ended up with the four
-- effective bindings. Without this guard an unseeded database would
-- log a success it did not achieve, and the tag gate would silently
-- stay a pass-through -- the exact failure mode migration 037 removed.
-- Count only *effective* bindings: apply_tag_keyword_filter() joins
-- tag_definitions with is_active = 1, so four inactive tags would
-- still leave the gate open while this log claimed success.
CREATE TABLE IF NOT EXISTS schema_cleanup_log (
    migration   TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_cleanup_log (migration, applied_at)
SELECT '039_xbot_feed_source', datetime('now')
WHERE (
    SELECT COUNT(*) FROM source_tags st
    JOIN tag_definitions t ON t.name = st.tag_name
    WHERE st.source_id = 'src-xbot-feed'
      AND t.is_active = 1
) >= 4;

COMMIT;
