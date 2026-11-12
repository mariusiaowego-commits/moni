BEGIN IMMEDIATE;

-- 041_bank_feed_source.sql - v1.0.76 (sprint-40)
--
-- Adds the bank-intel source (``bank_feed``), the tag it gates on
-- (``peer-banks``) and the binding between them.
--
-- Same upgrade contract as 039: this migration only INSERTs. A row the
-- user already owns (renamed, deactivated, edited fetch_config) is left
-- exactly as it is -- ``moni db seed-config`` is the path that updates
-- seeded values, and it runs only when the user asks for it.
--
-- The tag keywords are written ONLY when the tag is missing. They are
-- the ingest gate for this source, so an empty ``peer-banks`` row would
-- turn the gate into a pass-through and let every macro piece through --
-- the exact failure mode migration 037 removed. If the user already has
-- a ``peer-banks`` tag, their keywords win (never overwritten).
--
-- WARNING (literal constraint -- do not break it): no string literal in
-- this file may contain a semicolon, a double dash or a slash-star.
-- migrate.py strips everything after a double dash, and its fallback
-- path splits the whole file on semicolons, so either one inside a
-- literal truncates the statement and the migration dies (schema_version
-- would stay put and every ``moni db`` subcommand would keep failing).

INSERT INTO tag_definitions
    (name, display_name, description, color, keywords, created_at, is_active)
SELECT
    'peer-banks',
    '同业',
    '中农工建邮储等银行的资讯与动作 (非公告)',
    '#0F766E',
    '["工商银行", "中国工商银行", "工行", "ICBC", "农业银行", "中国农业银行", "农行", "中国银行", "中行", "建设银行", "中国建设银行", "建行", "CCB", "邮储银行", "中国邮政储蓄银行", "邮储", "PSBC", "交通银行", "交行"]',
    datetime('now'),
    1
WHERE NOT EXISTS (
    SELECT 1 FROM tag_definitions WHERE name = 'peer-banks'
);

INSERT INTO sources
    (id, name, display_name, source_type, url, fetch_interval_min,
     is_active, notes, fetch_config, created_at, updated_at)
SELECT
    'src-bank-feed',
    'bank_feed',
    '银行同业',
    'json',
    '',
    120,
    1,
    '中农工建邮储的资讯 (非公告)。agent 抓官网栏目+媒体 RSS+已知公众号, 落到 data/bank_feed 的 JSON, 本源只做导入。抓取时若当天 feed 缺失会就地调 agent 补跑。',
    '{"targets": [], "max_days": 30, "timeout_sec": 1800, "bot_timeout_sec": 1500, "retry_after_min": 30, "bot_enabled": true, "bot_script": "scripts/bank_bot/run_bot.py", "targets_file": "config/bank_targets.json", "skip_existing_urls": true}',
    datetime('now'),
    datetime('now')
WHERE NOT EXISTS (
    SELECT 1 FROM sources
    WHERE id = 'src-bank-feed' OR name = 'bank_feed'
);

-- source_tags.source_id and source_tags.tag_name are both foreign keys
-- (004_sources_and_tags.sql) and INSERT OR IGNORE does not swallow a
-- foreign key violation, so the join guards both ends -- no dangling
-- link can be created when either row is missing.
INSERT OR IGNORE INTO source_tags (source_id, tag_name, created_at)
SELECT s.id, t.name, datetime('now')
FROM sources s
JOIN tag_definitions t ON t.name = 'peer-banks'
WHERE s.id = 'src-bank-feed';

-- Only log success when the binding really took effect. Count the
-- *effective* bindings (tag active AND keywords non-empty), because
-- apply_tag_keyword_filter() joins tag_definitions with is_active = 1 and
-- skips tags without keywords -- either case leaves the gate open while
-- this row claimed success.
CREATE TABLE IF NOT EXISTS schema_cleanup_log (
    migration   TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_cleanup_log (migration, applied_at)
SELECT '041_bank_feed_source', datetime('now')
WHERE (
    SELECT COUNT(*) FROM source_tags st
    JOIN tag_definitions t ON t.name = st.tag_name
    WHERE st.source_id = 'src-bank-feed'
      AND t.is_active = 1
      AND t.keywords IS NOT NULL
      AND TRIM(t.keywords) NOT IN ('', '[]')
) >= 1;

COMMIT;
