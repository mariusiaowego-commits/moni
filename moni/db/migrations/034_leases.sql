-- 034_leases.sql — v1.0.42 (sprint-22): 统一租约运行时
-- fetch_jobs / llm_jobs 加 lease_owner + lease_heartbeat_at + fence 列。
-- 心跳制取代 wall-clock 起点判死;fence 单调递增防迟到写者覆盖。
-- 回滚: 新列保留无害(查询不引用即透明)。

ALTER TABLE fetch_jobs ADD COLUMN lease_owner TEXT;
ALTER TABLE fetch_jobs ADD COLUMN lease_heartbeat_at TEXT;
ALTER TABLE fetch_jobs ADD COLUMN fence INTEGER NOT NULL DEFAULT 0;

ALTER TABLE llm_jobs ADD COLUMN lease_owner TEXT;
ALTER TABLE llm_jobs ADD COLUMN lease_heartbeat_at TEXT;
ALTER TABLE llm_jobs ADD COLUMN fence INTEGER NOT NULL DEFAULT 0;

-- 心跳索引: sweep_expired 只查 running + 心跳过期的行,避免全表扫
CREATE INDEX IF NOT EXISTS idx_fetch_jobs_lease_heartbeat
    ON fetch_jobs(lease_heartbeat_at) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_llm_jobs_lease_heartbeat
    ON llm_jobs(lease_heartbeat_at) WHERE status = 'running';