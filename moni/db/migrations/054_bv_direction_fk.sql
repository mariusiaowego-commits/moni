-- 054: rebuild article_bv_direction with the two CASCADE foreign keys
--
-- 053 created this table without them, and CREATE TABLE IF NOT EXISTS cannot
-- add a constraint to a table that already exists.  A database that ran the
-- first version of 053 therefore keeps the constraint-less shape forever,
-- even after 053 is re-applied.  This migration rebuilds the table with the
-- constraints in place and copies the rows across.
--
-- The rebuild runs inside one transaction.  A table rebuild is two visible
-- steps -- DROP the old table, RENAME the new one into its place -- and
-- without a transaction a crash between them would leave the rows only in
-- the staging table, with no way to get them back.
--
-- foreign_keys is turned off for the copy: the old table carries no
-- constraints, so it may already hold rows whose article or direction is
-- gone, and refusing the copy would abort the whole upgrade.  Both pragmas
-- sit outside the transaction -- PRAGMA foreign_keys is a no-op inside one.
--
-- The staging table is created with IF NOT EXISTS and the copy uses
-- INSERT OR IGNORE, so a half-finished earlier run is never destroyed: the
-- surviving rows stay and are merged with whatever the old table still
-- holds.

PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

-- The real table may be gone if an earlier, non-transactional version of
-- this migration was interrupted between its DROP and its RENAME.  Putting
-- the pre-054 shape back keeps the copy below from failing, and whatever is
-- still in the staging table survives into the rebuilt one.
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

CREATE TABLE IF NOT EXISTS article_bv_direction_new (
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

INSERT OR IGNORE INTO article_bv_direction_new (
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

COMMIT;

PRAGMA foreign_keys = ON;
