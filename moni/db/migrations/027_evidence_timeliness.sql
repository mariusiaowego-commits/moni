-- Migration 027: evidence 时效校验 (闸 3)
-- schema_version 26 → 27
--
-- fact_check_evidence 新加 3 字段:
--   timeliness       TEXT    fresh | warn | demote | unknown
--   age_days         INTEGER 距今天数 (fetched_at 解析失败则为 NULL)
--   timeliness_flag  TEXT    NULL | '[过期]' | '[存疑]'
--
-- 闸 3 规则 (不降级 articles.fact_check_conclusion):
--   age <  30  → fresh  / flag NULL
--   age >= 30  → warn   / flag '[过期]'
--   age >= 90  → demote / flag '[存疑]'   (只标, 不把【已核实】改成【待核实】)
--   fetched_at 空/不可解析 → unknown / flag NULL
--
-- schema_version 单 PK: INSERT OR IGNORE (sprint 05 教训)

ALTER TABLE fact_check_evidence ADD COLUMN timeliness TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE fact_check_evidence ADD COLUMN age_days INTEGER;
ALTER TABLE fact_check_evidence ADD COLUMN timeliness_flag TEXT;

CREATE INDEX IF NOT EXISTS idx_fce_timeliness ON fact_check_evidence(timeliness);

-- 存量回填: 用 fetched_at 日期部分算 age (ISO8601 取前 10 位 YYYY-MM-DD)
UPDATE fact_check_evidence
SET age_days = CASE
        WHEN fetched_at IS NULL OR trim(fetched_at) = '' THEN NULL
        ELSE CAST(julianday('now') - julianday(substr(fetched_at, 1, 10)) AS INTEGER)
    END,
    timeliness = CASE
        WHEN fetched_at IS NULL OR trim(fetched_at) = '' THEN 'unknown'
        WHEN CAST(julianday('now') - julianday(substr(fetched_at, 1, 10)) AS INTEGER) >= 90 THEN 'demote'
        WHEN CAST(julianday('now') - julianday(substr(fetched_at, 1, 10)) AS INTEGER) >= 30 THEN 'warn'
        ELSE 'fresh'
    END,
    timeliness_flag = CASE
        WHEN fetched_at IS NULL OR trim(fetched_at) = '' THEN NULL
        WHEN CAST(julianday('now') - julianday(substr(fetched_at, 1, 10)) AS INTEGER) >= 90 THEN '[存疑]'
        WHEN CAST(julianday('now') - julianday(substr(fetched_at, 1, 10)) AS INTEGER) >= 30 THEN '[过期]'
        ELSE NULL
    END;

INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (27, datetime('now'));
