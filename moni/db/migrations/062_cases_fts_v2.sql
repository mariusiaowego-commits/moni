-- 062_cases_fts_v2.sql — 扩展 cases_fts 索引 keywords + article_title，加 search_text 兜底列 (P0-5)
-- schema_version 61 → 62

DROP TRIGGER IF EXISTS cases_fts_ai;
DROP TRIGGER IF EXISTS cases_fts_ad;
DROP TRIGGER IF EXISTS cases_fts_au;
DROP TRIGGER IF EXISTS case_keywords_fts_ai;
DROP TRIGGER IF EXISTS case_keywords_fts_ad;
DROP TRIGGER IF EXISTS case_keywords_fts_au;
DROP TABLE IF EXISTS cases_fts;

CREATE VIRTUAL TABLE cases_fts USING fts5(
    case_id UNINDEXED,
    subject,
    counterparty,
    summary,
    evidence_snippet,
    amount,
    keywords,
    article_title,
    tokenize = 'trigram'
);

CREATE TRIGGER cases_fts_ai AFTER INSERT ON cases
WHEN new.subject != '__processing__'
BEGIN
    INSERT INTO cases_fts(
        case_id, subject, counterparty, summary, evidence_snippet, amount, keywords, article_title
    )
    VALUES (
        new.case_id,
        new.subject,
        COALESCE(new.counterparty, ''),
        COALESCE(new.summary, ''),
        COALESCE(new.evidence_snippet, ''),
        COALESCE(new.amount, ''),
        COALESCE((SELECT group_concat(keyword, ' ') FROM case_keywords WHERE case_id = new.case_id), ''),
        COALESCE((SELECT title FROM articles WHERE id = new.article_id), '')
    );
    UPDATE cases
    SET search_text = TRIM(
        COALESCE(new.subject, '') || ' ' ||
        COALESCE(new.counterparty, '') || ' ' ||
        COALESCE(new.summary, '') || ' ' ||
        COALESCE(new.evidence_snippet, '') || ' ' ||
        COALESCE(new.amount, '') || ' ' ||
        COALESCE((SELECT group_concat(keyword, ' ') FROM case_keywords WHERE case_id = new.case_id), '') || ' ' ||
        COALESCE((SELECT title FROM articles WHERE id = new.article_id), '')
    )
    WHERE case_id = new.case_id;
END;

CREATE TRIGGER cases_fts_ad AFTER DELETE ON cases BEGIN
    DELETE FROM cases_fts WHERE case_id = old.case_id;
END;

CREATE TRIGGER cases_fts_au AFTER UPDATE OF subject, counterparty, summary, evidence_snippet, amount, article_id ON cases
WHEN new.subject != '__processing__'
BEGIN
    DELETE FROM cases_fts WHERE case_id = old.case_id;
    INSERT INTO cases_fts(
        case_id, subject, counterparty, summary, evidence_snippet, amount, keywords, article_title
    )
    VALUES (
        new.case_id,
        new.subject,
        COALESCE(new.counterparty, ''),
        COALESCE(new.summary, ''),
        COALESCE(new.evidence_snippet, ''),
        COALESCE(new.amount, ''),
        COALESCE((SELECT group_concat(keyword, ' ') FROM case_keywords WHERE case_id = new.case_id), ''),
        COALESCE((SELECT title FROM articles WHERE id = new.article_id), '')
    );
    UPDATE cases
    SET search_text = TRIM(
        COALESCE(new.subject, '') || ' ' ||
        COALESCE(new.counterparty, '') || ' ' ||
        COALESCE(new.summary, '') || ' ' ||
        COALESCE(new.evidence_snippet, '') || ' ' ||
        COALESCE(new.amount, '') || ' ' ||
        COALESCE((SELECT group_concat(keyword, ' ') FROM case_keywords WHERE case_id = new.case_id), '') || ' ' ||
        COALESCE((SELECT title FROM articles WHERE id = new.article_id), '')
    )
    WHERE case_id = new.case_id;
END;

CREATE TRIGGER case_keywords_fts_ai AFTER INSERT ON case_keywords BEGIN
    DELETE FROM cases_fts WHERE case_id = new.case_id;
    INSERT INTO cases_fts(
        case_id, subject, counterparty, summary, evidence_snippet, amount, keywords, article_title
    )
    SELECT
        c.case_id,
        c.subject,
        COALESCE(c.counterparty, ''),
        COALESCE(c.summary, ''),
        COALESCE(c.evidence_snippet, ''),
        COALESCE(c.amount, ''),
        COALESCE((SELECT group_concat(k.keyword, ' ') FROM case_keywords k WHERE k.case_id = c.case_id), ''),
        COALESCE((SELECT a.title FROM articles a WHERE a.id = c.article_id), '')
    FROM cases c
    WHERE c.case_id = new.case_id AND c.subject != '__processing__';

    UPDATE cases
    SET search_text = TRIM(
        COALESCE(subject, '') || ' ' ||
        COALESCE(counterparty, '') || ' ' ||
        COALESCE(summary, '') || ' ' ||
        COALESCE(evidence_snippet, '') || ' ' ||
        COALESCE(amount, '') || ' ' ||
        COALESCE((SELECT group_concat(k.keyword, ' ') FROM case_keywords k WHERE k.case_id = cases.case_id), '') || ' ' ||
        COALESCE((SELECT a.title FROM articles a WHERE a.id = cases.article_id), '')
    )
    WHERE case_id = new.case_id;
END;

CREATE TRIGGER case_keywords_fts_au AFTER UPDATE ON case_keywords BEGIN
    DELETE FROM cases_fts WHERE case_id = new.case_id;
    INSERT INTO cases_fts(
        case_id, subject, counterparty, summary, evidence_snippet, amount, keywords, article_title
    )
    SELECT
        c.case_id,
        c.subject,
        COALESCE(c.counterparty, ''),
        COALESCE(c.summary, ''),
        COALESCE(c.evidence_snippet, ''),
        COALESCE(c.amount, ''),
        COALESCE((SELECT group_concat(k.keyword, ' ') FROM case_keywords k WHERE k.case_id = c.case_id), ''),
        COALESCE((SELECT a.title FROM articles a WHERE a.id = c.article_id), '')
    FROM cases c
    WHERE c.case_id = new.case_id AND c.subject != '__processing__';

    UPDATE cases
    SET search_text = TRIM(
        COALESCE(subject, '') || ' ' ||
        COALESCE(counterparty, '') || ' ' ||
        COALESCE(summary, '') || ' ' ||
        COALESCE(evidence_snippet, '') || ' ' ||
        COALESCE(amount, '') || ' ' ||
        COALESCE((SELECT group_concat(k.keyword, ' ') FROM case_keywords k WHERE k.case_id = cases.case_id), '') || ' ' ||
        COALESCE((SELECT a.title FROM articles a WHERE a.id = cases.article_id), '')
    )
    WHERE case_id = new.case_id;
END;

CREATE TRIGGER case_keywords_fts_ad AFTER DELETE ON case_keywords BEGIN
    DELETE FROM cases_fts WHERE case_id = old.case_id;
    INSERT INTO cases_fts(
        case_id, subject, counterparty, summary, evidence_snippet, amount, keywords, article_title
    )
    SELECT
        c.case_id,
        c.subject,
        COALESCE(c.counterparty, ''),
        COALESCE(c.summary, ''),
        COALESCE(c.evidence_snippet, ''),
        COALESCE(c.amount, ''),
        COALESCE((SELECT group_concat(k.keyword, ' ') FROM case_keywords k WHERE k.case_id = c.case_id), ''),
        COALESCE((SELECT a.title FROM articles a WHERE a.id = c.article_id), '')
    FROM cases c
    WHERE c.case_id = old.case_id AND c.subject != '__processing__';

    UPDATE cases
    SET search_text = TRIM(
        COALESCE(subject, '') || ' ' ||
        COALESCE(counterparty, '') || ' ' ||
        COALESCE(summary, '') || ' ' ||
        COALESCE(evidence_snippet, '') || ' ' ||
        COALESCE(amount, '') || ' ' ||
        COALESCE((SELECT group_concat(k.keyword, ' ') FROM case_keywords k WHERE k.case_id = cases.case_id), '') || ' ' ||
        COALESCE((SELECT a.title FROM articles a WHERE a.id = cases.article_id), '')
    )
    WHERE case_id = old.case_id;
END;

UPDATE cases
SET search_text = TRIM(
    COALESCE(subject, '') || ' ' ||
    COALESCE(counterparty, '') || ' ' ||
    COALESCE(summary, '') || ' ' ||
    COALESCE(evidence_snippet, '') || ' ' ||
    COALESCE(amount, '') || ' ' ||
    COALESCE((SELECT group_concat(k.keyword, ' ') FROM case_keywords k WHERE k.case_id = cases.case_id), '') || ' ' ||
    COALESCE((SELECT a.title FROM articles a WHERE a.id = cases.article_id), '')
)
WHERE subject != '__processing__';

INSERT INTO cases_fts(
    case_id, subject, counterparty, summary, evidence_snippet, amount, keywords, article_title
)
SELECT
    c.case_id,
    c.subject,
    COALESCE(c.counterparty, ''),
    COALESCE(c.summary, ''),
    COALESCE(c.evidence_snippet, ''),
    COALESCE(c.amount, ''),
    COALESCE((SELECT group_concat(k.keyword, ' ') FROM case_keywords k WHERE k.case_id = c.case_id), ''),
    COALESCE((SELECT a.title FROM articles a WHERE a.id = c.article_id), '')
FROM cases c
WHERE c.subject != '__processing__';

INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (62, datetime('now'));
