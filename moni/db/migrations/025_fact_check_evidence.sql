-- Migration 025: fact_check_evidence 表 (事实核查+ 证据链)
-- schema_version 24 → 25
--
-- 存联网核查的检索证据 (query/url/domain/snippet/model/provider/conclusion),
-- 支持评审反查与双闸防幻觉.
--
-- 设计要点:
-- * append-only, 无 UPDATE 触发器
-- * job_id 可空 (CLI/人工路径)
-- * 存量 fact_check_method 'llm' → 'llm_inference' 收敛
-- * schema_version 单 PK: INSERT OR IGNORE (sprint 05 教训)

CREATE TABLE IF NOT EXISTS fact_check_evidence (
    id           TEXT PRIMARY KEY,
    article_id   TEXT NOT NULL,
    job_id       TEXT,
    query        TEXT NOT NULL,
    url          TEXT NOT NULL,
    domain       TEXT NOT NULL,
    snippet      TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    model        TEXT NOT NULL,
    provider     TEXT NOT NULL,
    conclusion   TEXT NOT NULL,
    rationale    TEXT,
    evidence_seq INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_fce_article ON fact_check_evidence(article_id);
CREATE INDEX IF NOT EXISTS idx_fce_url     ON fact_check_evidence(url);
CREATE INDEX IF NOT EXISTS idx_fce_job     ON fact_check_evidence(job_id);

-- 存量化债: 刷 'llm' → 'llm_inference' (jobs.py 旧写库值)
UPDATE articles SET fact_check_method='llm_inference' WHERE fact_check_method='llm';

-- schema_version 单 PK — 必须 INSERT OR IGNORE
INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (25, datetime('now'));
