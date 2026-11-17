-- 053: Business-value direction scoring (sprint 53, design 2026-11-16 §10)
--
-- Additive only.  The general ten-dimension score keeps living on
-- articles.business_value_*; a direction run writes its own table row so the
-- two question sets never share a cache key or a column.
--
-- research_version is monotonic per direction.  Only the highest version is
-- ever consulted (running / passed / failed / expired); older rows stay as an
-- audit trail and are never used as a fallback.

CREATE TABLE IF NOT EXISTS bv_direction (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    label           TEXT NOT NULL,
    brief           TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bv_direction_research (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    direction_id        INTEGER NOT NULL REFERENCES bv_direction(id) ON DELETE CASCADE,
    research_version    INTEGER NOT NULL,
    status              TEXT NOT NULL,
    label_snapshot      TEXT NOT NULL,
    brief_snapshot      TEXT NOT NULL,
    prompt_id           TEXT NOT NULL,
    scope               TEXT,
    out_of_scope        TEXT,
    rubric_json         TEXT,
    error               TEXT,
    model               TEXT,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    UNIQUE (direction_id, research_version)
);

CREATE TABLE IF NOT EXISTS article_bv_direction (
    article_id          TEXT NOT NULL,
    direction_id        INTEGER NOT NULL,
    research_version    INTEGER NOT NULL,
    score               INTEGER,
    note                TEXT,
    status              TEXT NOT NULL,
    error               TEXT,
    breakdown_json      TEXT,
    content_hash        TEXT NOT NULL,
    rubric_id           TEXT,
    scored_at           TEXT NOT NULL,
    PRIMARY KEY (article_id, direction_id, research_version)
);

CREATE INDEX IF NOT EXISTS idx_bvd_research_latest
    ON bv_direction_research (direction_id, research_version DESC);
CREATE INDEX IF NOT EXISTS idx_abvd_direction_score
    ON article_bv_direction (direction_id, research_version, score DESC);
