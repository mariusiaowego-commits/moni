-- 060: reader perspective and the clarification round (sprint-74)
--
-- A direction could say *what* it cares about (label, brief, guidance) but
-- not *whose* point of view its articles are judged from.  The research
-- prompt hard-coded "问题一律从银行或支付机构角度问", so a direction whose
-- reader is, say, a bank new-media channel operator was forced into
-- questions that were not its questions.
--
-- Three additive columns:
--   bv_direction.reader_perspective
--       Who the direction's articles are for.  Optional; empty keeps the
--       old behaviour and the old research prompt.
--   bv_direction_research.perspective_snapshot
--       The perspective a given research version was built from.
--   bv_direction_research.clarification_snapshot
--       The clarification Q&A a given research version was built from.
--
-- New table bv_direction_clarification: one row per clarification round,
-- with the same running/passed/failed/expired state machine the research
-- rows use.  The model asks up to five questions about a one-line
-- perspective, the user answers them, and the answers become part of the
-- research input -- a single sentence cannot tell the model what the
-- perspective really is.
--
-- Existing rows get '' on all three columns, which compares equal: the
-- live "微信生态" direction keeps its passed research v3 and stays usable.

ALTER TABLE bv_direction ADD COLUMN reader_perspective TEXT NOT NULL DEFAULT '';

ALTER TABLE bv_direction_research ADD COLUMN perspective_snapshot TEXT NOT NULL DEFAULT '';

ALTER TABLE bv_direction_research ADD COLUMN clarification_snapshot TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS bv_direction_clarification (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    direction_id            INTEGER NOT NULL REFERENCES bv_direction(id) ON DELETE CASCADE,
    clarification_version   INTEGER NOT NULL,
    status                  TEXT NOT NULL,
    perspective_snapshot    TEXT NOT NULL DEFAULT '',
    questions_json          TEXT NOT NULL DEFAULT '[]',
    answers_json            TEXT NOT NULL DEFAULT '[]',
    prompt_id               TEXT NOT NULL,
    raw_output              TEXT,
    error                   TEXT,
    started_at              TEXT NOT NULL,
    finished_at             TEXT,
    UNIQUE (direction_id, clarification_version)
);

CREATE INDEX IF NOT EXISTS idx_bvd_clarification_latest
    ON bv_direction_clarification (direction_id, clarification_version DESC);
