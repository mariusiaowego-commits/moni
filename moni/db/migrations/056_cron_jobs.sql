-- 056: cron 任务注册表（sprint-69 cron-console / v1.0.116）
--
-- 旧行为：任务定义只存在于 config/schedule.yaml，启动时一次性读取；
-- 界面只能看 cron_runs 历史，不能改时间/启停/立即运行。
--
-- 新行为：cron_jobs 作为任务注册表（由 YAML seed 一次），调度器 15s tick
-- 读取 schedule_revision 热加载；立即运行走 cron_run_requests 队列。
-- YAML 保留为 seed 与回滚来源。

CREATE TABLE IF NOT EXISTS cron_jobs (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    func_path     TEXT NOT NULL,
    trigger_type  TEXT NOT NULL DEFAULT 'cron',
    hour          INTEGER NOT NULL,
    minute        INTEGER NOT NULL,
    day_of_week   TEXT NOT NULL DEFAULT 'mon-fri',
    func_kwargs   TEXT NOT NULL DEFAULT '{}',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cron_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cron_run_requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    claimed_at   TEXT,
    finished_at  TEXT,
    error        TEXT
);

CREATE INDEX IF NOT EXISTS idx_cron_run_requests_status
    ON cron_run_requests (status, id);
CREATE INDEX IF NOT EXISTS idx_cron_run_requests_job
    ON cron_run_requests (job_id, status);

INSERT OR IGNORE INTO cron_jobs
    (id, name, func_path, trigger_type, hour, minute, day_of_week,
     func_kwargs, is_active, created_at, updated_at)
VALUES
    ('fetch_all_sources', '抓取所有源', 'moni.cron.jobs:fetch_all_sources', 'cron',
     9, 10, 'mon-fri', '{}', 1,
     strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now'), strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now')),
    ('extract_events_morning', '行业事件抽取-早间', 'moni.cron.jobs:extract_events_pending', 'cron',
     7, 0, 'mon-fri', '{"batch_size": 20}', 1,
     strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now'), strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now')),
    ('extract_events_am', '行业事件抽取-上午', 'moni.cron.jobs:extract_events_pending', 'cron',
     8, 30, 'mon-fri', '{"batch_size": 20}', 1,
     strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now'), strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now')),
    ('extract_events_noon', '行业事件抽取-午间', 'moni.cron.jobs:extract_events_pending', 'cron',
     12, 0, 'mon-fri', '{"batch_size": 20}', 1,
     strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now'), strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now')),
    ('extract_events_evening', '行业事件抽取-傍晚', 'moni.cron.jobs:extract_events_pending', 'cron',
     18, 0, 'mon-fri', '{"batch_size": 20}', 1,
     strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now'), strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now'));

INSERT OR IGNORE INTO cron_meta (key, value) VALUES ('schedule_revision', '1');
INSERT OR IGNORE INTO cron_meta (key, value) VALUES ('heartbeat_at', '');
