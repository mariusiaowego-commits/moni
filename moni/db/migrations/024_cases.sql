-- moni v1.0.17 F6 同业案例库 migration (sprint 05)
-- schema_version 23 → 24
--
-- 设计要点:
--   1. case_id = "MN-<2字母分类>-<YYMM>-<4位序号>" (人脑可读)
--      例: MN-AI-2608-0042 = 2026年8月第42条AI应用案例
--   2. cases_fts 用 trigram tokenizer (中文场景必需) + 3 trigger
--   3. case_tags 多对多表 (跟 article_tags 同模式, 复用 idx_article_tags_tag 风格)
--   4. 状态机重写: case_lifecycle (draft/verified/published/deprecated/archived)
--      不复用 events 表的 extraction_status
--   5. case_action 5 类枚举 (上线/试点/规模化/转型/退出)
--   6. case_type 2 类枚举 (AI/PI) — AI 应用 + 产品创新
--   7. reference_value 3 档 (high/medium/low) + 简短依据
--   8. CHECK 约束覆盖所有枚举
--   9. UNIQUE(case_id) (主键);非唯一索引加速常用 query

-- ═══ cases 表 ═══════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS cases (
    case_id              TEXT PRIMARY KEY,        -- MN-XX-YYMM-NNNN
    article_id           TEXT NOT NULL,
    case_date            TEXT,                    -- YYYY-MM-DD / YYYY-MM
    case_date_precision  TEXT NOT NULL DEFAULT 'unknown'
        CHECK (case_date_precision IN ('exact','month','quarter','year','relative','unknown')),
    subject              TEXT NOT NULL,           -- 案例主体 (规范全称)
    subject_raw          TEXT,                    -- 原文表述 (LLM 抽出的原文)
    case_type            TEXT NOT NULL
        CHECK (case_type IN ('AI','PI')),         -- AI 应用 / 产品创新
    case_action          TEXT NOT NULL
        CHECK (case_action IN ('launch','pilot','scale','transform','exit')),
    counterparty         TEXT,                    -- 案例对手方 (并购/合作时的另一方)
    amount               TEXT,                    -- 金额/规模描述 ("5 亿元" / "100 万用户")
    reference_value      TEXT NOT NULL DEFAULT 'medium'
        CHECK (reference_value IN ('high','medium','low')),
    reference_rationale  TEXT,                    -- LLM 给的高/中/低依据
    summary              TEXT,                    -- 案例摘要 ≤80 字
    evidence_snippet     TEXT,                    -- 原文证据 1-2 句
    extraction_confidence INTEGER,                -- 0-100
    extraction_status    TEXT NOT NULL DEFAULT 'pending'
        CHECK (extraction_status IN ('pending','processing','completed','failed','skipped','normalize_failed')),
    case_lifecycle       TEXT NOT NULL DEFAULT 'draft'
        CHECK (case_lifecycle IN ('draft','verified','published','deprecated','archived')),
    review_status        TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending','confirmed','rejected')),
    extraction_attempts  INTEGER NOT NULL DEFAULT 0,
    last_error           TEXT,
    extracted_at         TEXT,
    normalize_version    INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cases_case_date      ON cases(case_date);
CREATE INDEX IF NOT EXISTS idx_cases_case_type      ON cases(case_type);
CREATE INDEX IF NOT EXISTS idx_cases_case_action    ON cases(case_action);
CREATE INDEX IF NOT EXISTS idx_cases_subject        ON cases(subject);
CREATE INDEX IF NOT EXISTS idx_cases_reference      ON cases(reference_value);
CREATE INDEX IF NOT EXISTS idx_cases_lifecycle      ON cases(case_lifecycle);
CREATE INDEX IF NOT EXISTS idx_cases_extraction     ON cases(extraction_status);
CREATE INDEX IF NOT EXISTS idx_cases_review         ON cases(review_status);
CREATE INDEX IF NOT EXISTS idx_cases_article        ON cases(article_id);
CREATE INDEX IF NOT EXISTS idx_cases_created        ON cases(created_at);

-- 防止同 article 重复抽同一 case (subject + case_type + case_action)
CREATE UNIQUE INDEX IF NOT EXISTS uniq_cases_article_subject_type_action
    ON cases(article_id, subject, case_type, case_action);

-- ═══ case_tags 表 (多对多, 跟 article_tags 同模式) ═══════════════
CREATE TABLE IF NOT EXISTS case_tags (
    case_id     TEXT NOT NULL,
    tag_name    TEXT NOT NULL,
    predicted   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (case_id, tag_name),
    FOREIGN KEY (case_id)  REFERENCES cases(case_id) ON DELETE CASCADE,
    FOREIGN KEY (tag_name) REFERENCES tag_definitions(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_case_tags_tag ON case_tags(tag_name);

-- ═══ cases_fts FTS5 虚拟表 (content=外部表 + trigram tokenizer) ═══
-- 关键: 中文必须用 trigram tokenizer (SQLite ≥3.34),默认 unicode61 不切中文词
-- reference: https://sqlite.org/fts5.html#the_trigram_tokenizer
CREATE VIRTUAL TABLE IF NOT EXISTS cases_fts USING fts5(
    case_id,
    subject,
    counterparty,
    summary,
    evidence_snippet,
    amount,
    tokenize = 'trigram'
);

-- ═══ cases_fts 三 trigger (INSERT/UPDATE/DELETE) ═══════════════════
-- 关键: external content tables 不会自动同步,必须手写 3 trigger
-- reference: https://sqlite.org/fts5.html#external_content_tables
-- FTS5 delete 用普通 DELETE + WHERE case_id=old.case_id (而非 'delete' special cmd,
-- 因 FTS5 自己的 rowid 跟 cases 的 rowid 是独立 namespace, 无法跨表对齐)
CREATE TRIGGER IF NOT EXISTS cases_fts_ai AFTER INSERT ON cases BEGIN
    INSERT INTO cases_fts(rowid, case_id, subject, counterparty, summary, evidence_snippet, amount)
    VALUES (new.rowid, new.case_id, new.subject, COALESCE(new.counterparty, ''),
            COALESCE(new.summary, ''), COALESCE(new.evidence_snippet, ''), COALESCE(new.amount, ''));
END;

CREATE TRIGGER IF NOT EXISTS cases_fts_ad AFTER DELETE ON cases BEGIN
    DELETE FROM cases_fts WHERE case_id = old.case_id;
END;

CREATE TRIGGER IF NOT EXISTS cases_fts_au AFTER UPDATE ON cases BEGIN
    DELETE FROM cases_fts WHERE case_id = old.case_id;
    INSERT INTO cases_fts(rowid, case_id, subject, counterparty, summary, evidence_snippet, amount)
    VALUES (new.rowid, new.case_id, new.subject, COALESCE(new.counterparty, ''),
            COALESCE(new.summary, ''), COALESCE(new.evidence_snippet, ''), COALESCE(new.amount, ''));
END;

-- ═══ schema_version 更新 ═══════════════════════════════════════════════
-- 单 PK (version) — 不能 UPDATE 全部; 改 INSERT OR IGNORE (兼容)
INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (24, datetime('now'));