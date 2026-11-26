-- 070_case_extract_daily.sql — 案例抽取改为每天一次 + 默认暂停 + 手动演示任务
-- schema_version 69 → 70
--
-- 背景：13:30 / 20:30 两条工作日任务每天合计最多送 60 篇给 LLM；
-- 质量闸上线后按需抽取即可，定时任务默认暂停，保留每天 13:30 的一条，
-- 通过界面「立即抽取」按钮按需触发（单次最多 10 条，仅作演示）。

UPDATE cron_jobs
SET hour = 13,
    minute = 30,
    day_of_week = '*',
    func_kwargs = '{"batch_size": 12}',
    is_active = 0,
    updated_at = strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now')
WHERE id = 'extract_cases_noon_post';

DELETE FROM cron_jobs WHERE id = 'extract_cases_night';

INSERT OR REPLACE INTO cron_jobs
    (id, name, func_path, trigger_type, hour, minute, day_of_week,
     func_kwargs, is_active, created_at, updated_at)
VALUES
    ('extract_cases_demo', '案例抽取-立即演示', 'moni.cron.jobs:extract_cases_demo', 'manual',
     0, 0, '*', '{"batch_size": 10}', 0,
     strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now'), strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now'));

UPDATE cron_run_requests
SET status = 'failed',
    finished_at = strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now'),
    error = 'job removed by migration 070'
WHERE job_id = 'extract_cases_night' AND status IN ('pending', 'claimed');

UPDATE cron_meta
SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)
WHERE key = 'schedule_revision';

INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (70, datetime('now'));
