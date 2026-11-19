-- 050: 内容分级标注 (sprint-58)
--
-- Additive only: two new tables.  No UPDATE, no DELETE, no backfill; the
-- labels themselves are written by `moni label-articles --apply` behind a
-- preview gate.  `articles` is never touched by this feature.

CREATE TABLE IF NOT EXISTS content_labels (
    article_id    TEXT NOT NULL,
    category      TEXT NOT NULL,
    severity      TEXT NOT NULL CHECK (severity IN ('strong', 'weak')),
    confidence    REAL NOT NULL DEFAULT 0,
    evidences     TEXT NOT NULL DEFAULT '[]',   -- JSON array, 只存"命中了什么"
    rule_version  TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'auto' CHECK (source IN ('auto', 'manual')),
    review_state  TEXT NOT NULL DEFAULT 'pending'
                  CHECK (review_state IN ('pending', 'confirmed', 'dismissed')),
    reviewed_by   TEXT,
    reviewed_at   TEXT,
    review_note   TEXT,
    labeled_at    TEXT NOT NULL,
    PRIMARY KEY (article_id, category)
);

CREATE INDEX IF NOT EXISTS idx_content_labels_cat
    ON content_labels(category, review_state);

CREATE TABLE IF NOT EXISTS label_runs (
    id            TEXT PRIMARY KEY,
    mode          TEXT NOT NULL CHECK (mode IN ('preview', 'apply')),
    status        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    scanned       INTEGER NOT NULL DEFAULT 0,
    labeled       INTEGER NOT NULL DEFAULT 0,
    new_rows      INTEGER NOT NULL DEFAULT 0,
    kept_manual   INTEGER NOT NULL DEFAULT 0,
    by_category   TEXT NOT NULL DEFAULT '{}',
    rule_version  TEXT NOT NULL,
    details_json  TEXT NOT NULL DEFAULT '{}',
    error         TEXT
);
