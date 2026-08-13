-- Migration 027: evidence 时效校验 (闸 3)
-- schema_version 26 → 27
--
-- articles.fact_check_timeliness  TEXT     最新一次 evidence 抓取时效
--   取值: fresh | 30d_warn | 90d_demote | unknown
-- fact_check_evidence.days_old    INTEGER  写入时距今天数 (加速 query)
--
-- 闸 3:
--   days_old  0-30  → fresh      结论不变
--   days_old 31-90  → 30d_warn   结论加 (已过期 30-90 天) 后缀
--   days_old  91+   → 90d_demote 结论强制【存疑】
--   days_old  NULL  → unknown    不影响结论
--
-- schema_version 单 PK: INSERT OR IGNORE (sprint 05 教训)

ALTER TABLE articles ADD COLUMN fact_check_timeliness TEXT;
ALTER TABLE fact_check_evidence ADD COLUMN days_old INTEGER;

CREATE INDEX IF NOT EXISTS idx_fce_days_old ON fact_check_evidence(days_old);

-- 存量回填 days_old (ISO8601 取日期部分, julianday 更稳)
UPDATE fact_check_evidence
SET days_old = CAST(julianday('now') - julianday(substr(fetched_at, 1, 10)) AS INTEGER)
WHERE fetched_at IS NOT NULL AND trim(fetched_at) != '';

-- 存量回填 articles.fact_check_timeliness (按该文最大 days_old, 闸 3 最严)
UPDATE articles
SET fact_check_timeliness = (
    SELECT CASE
        WHEN MAX(e.days_old) IS NULL THEN 'unknown'
        WHEN MAX(e.days_old) <= 30 THEN 'fresh'
        WHEN MAX(e.days_old) <= 90 THEN '30d_warn'
        ELSE '90d_demote'
    END
    FROM fact_check_evidence e
    WHERE e.article_id = articles.id
)
WHERE id IN (SELECT DISTINCT article_id FROM fact_check_evidence);

INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (27, datetime('now'));
