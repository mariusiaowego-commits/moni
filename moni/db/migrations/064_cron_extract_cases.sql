-- 064_cron_extract_cases.sql — 案例抽取账本 + cron 调度种子 + 日预算闸 (P0-4)
-- schema_version 63 → 64

CREATE TABLE IF NOT EXISTS case_extract_runs (
    article_id     TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_hash     TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL
        CHECK (status IN ('done', 'no_cases', 'skipped_gate', 'failed', 'processing')),
    case_count     INTEGER NOT NULL DEFAULT 0,
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT,
    lease_owner    TEXT,
    lease_at       TEXT,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (article_id, prompt_version),
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cer_status ON case_extract_runs(status, updated_at);

-- 回填已有案例到账本 (prompt_version='v1')
INSERT OR IGNORE INTO case_extract_runs
    (article_id, prompt_version, input_hash, status, case_count, attempts, updated_at)
SELECT
    article_id,
    'v1',
    '',
    'done',
    COUNT(*),
    1,
    COALESCE(MAX(updated_at), datetime('now'))
FROM cases
WHERE subject != '__processing__'
GROUP BY article_id;

-- 案例抽取独立补漏 cron (工作日 13:30 / 20:30, 错开 fetch 9:10 与 events 7:00/8:30/12:00/18:00)
INSERT OR IGNORE INTO cron_jobs
    (id, name, func_path, trigger_type, hour, minute, day_of_week,
     func_kwargs, is_active, created_at, updated_at)
VALUES
    ('extract_cases_noon_post', '同业案例抽取-午后', 'moni.cron.jobs:extract_cases_pending', 'cron',
     13, 30, 'mon-fri', '{"batch_size": 30}', 1,
     strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now'), strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now')),
    ('extract_cases_night', '同业案例抽取-晚间', 'moni.cron.jobs:extract_cases_pending', 'cron',
     20, 30, 'mon-fri', '{"batch_size": 30}', 1,
     strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now'), strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now'));

INSERT OR IGNORE INTO app_settings (key, value, updated_at)
VALUES ('case_extract_daily_input_token_budget', '300000', NULL);

INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (63, datetime('now'));
