-- 054: rebuild article_bv_direction with the two CASCADE foreign keys
--
-- 053 created this table without them, and CREATE TABLE IF NOT EXISTS cannot
-- add a constraint to a table that already exists.  A database that ran the
-- first version of 053 therefore keeps the constraint-less shape forever,
-- even after 053 is re-applied.  This migration rebuilds the table with the
-- constraints in place and copies the rows across.
--
-- foreign_keys is turned off for the copy: the old table carries no
-- constraints, so it may already hold rows whose article or direction is
-- gone, and refusing the copy would abort the whole upgrade.  The pragma is
-- turned back on at the end of the script.
--
-- Re-runnable: the staging table is dropped first, so a partially applied
-- 054 can simply be run again.

PRAGMA foreign_keys = OFF;

DROP TABLE IF EXISTS article_bv_direction_new;

CREATE TABLE article_bv_direction_new (
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
    PRIMARY KEY (article_id, direction_id, research_version),
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE,
    FOREIGN KEY (direction_id) REFERENCES bv_direction(id) ON DELETE CASCADE
);

INSERT INTO article_bv_direction_new (
    article_id, direction_id, research_version, score, note, status, error,
    breakdown_json, content_hash, rubric_id, scored_at
)
SELECT
    article_id, direction_id, research_version, score, note, status, error,
    breakdown_json, content_hash, rubric_id, scored_at
FROM article_bv_direction;

DROP TABLE IF EXISTS article_bv_direction;

ALTER TABLE article_bv_direction_new RENAME TO article_bv_direction;

CREATE INDEX IF NOT EXISTS idx_abvd_direction_score
    ON article_bv_direction (direction_id, research_version, score DESC);

PRAGMA foreign_keys = ON;
