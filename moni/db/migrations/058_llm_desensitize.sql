-- 058: 对大模型信息脱敏的审计与配置（零原值）

INSERT OR IGNORE INTO app_settings (key, value, updated_at) VALUES
    ('desensitize.enabled', '0', NULL),
    ('desensitize.level_l1', '1', NULL),
    ('desensitize.level_l2', '1', NULL),
    ('desensitize.level_l3', '0', NULL),
    ('desensitize.strict_fail_closed', '0', NULL);

CREATE TABLE IF NOT EXISTS desensitize_rules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_type     TEXT NOT NULL CHECK (rule_type IN ('word','regex','allowlist')),
    kind          TEXT NOT NULL DEFAULT 'ORG',
    pattern       TEXT NOT NULL,
    aliases_json  TEXT NOT NULL DEFAULT '[]',
    pseudonym     TEXT,
    action        TEXT NOT NULL DEFAULT 'mask' CHECK (action IN ('mask','warn','block')),
    match_case    INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    notes         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_desens_rules_active ON desensitize_rules(is_active, rule_type);

CREATE TABLE IF NOT EXISTS desensitize_events (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id                TEXT NOT NULL UNIQUE,
    job_id                 TEXT,
    share_task_id          TEXT,
    article_id             TEXT,
    call_site              TEXT NOT NULL,
    task_type              TEXT NOT NULL,
    provider               TEXT,
    model                  TEXT,
    policy_mode            TEXT NOT NULL DEFAULT 'standard',
    checked                INTEGER NOT NULL DEFAULT 1,
    blocked                INTEGER NOT NULL DEFAULT 0,
    masked_total           INTEGER NOT NULL DEFAULT 0,
    masked_distinct        INTEGER NOT NULL DEFAULT 0,
    restored_total         INTEGER NOT NULL DEFAULT 0,
    fuzzy_restored_total   INTEGER NOT NULL DEFAULT 0,
    unrestored_count       INTEGER NOT NULL DEFAULT 0,
    hallucinated_count     INTEGER NOT NULL DEFAULT 0,
    leak_scan_hits         INTEGER NOT NULL DEFAULT 0,
    residual_placeholders  INTEGER NOT NULL DEFAULT 0,
    fallback_used          TEXT,
    prompt_chars_before    INTEGER NOT NULL DEFAULT 0,
    prompt_chars_after     INTEGER NOT NULL DEFAULT 0,
    tokens_delta_est       INTEGER NOT NULL DEFAULT 0,
    mask_latency_us        INTEGER NOT NULL DEFAULT 0,
    restore_latency_us     INTEGER NOT NULL DEFAULT 0,
    kinds_json             TEXT NOT NULL DEFAULT '{}',
    by_json                TEXT NOT NULL DEFAULT '{}',
    items_json             TEXT NOT NULL DEFAULT '[]',
    items_truncated        INTEGER NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_desens_events_created ON desensitize_events(created_at);
CREATE INDEX IF NOT EXISTS idx_desens_events_task    ON desensitize_events(task_type, created_at);
CREATE INDEX IF NOT EXISTS idx_desens_events_job     ON desensitize_events(job_id) WHERE job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_desens_events_article ON desensitize_events(article_id) WHERE article_id IS NOT NULL;

INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (58, datetime('now'));
