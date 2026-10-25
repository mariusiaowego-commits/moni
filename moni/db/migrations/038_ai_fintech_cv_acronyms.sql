-- 038_ai_fintech_cv_acronyms.sql - v1.0.65
--
-- v1.0.64 added ASCII word boundaries to the keyword matcher.  That fixed
-- real false positives (MA inside MASAJIN, ark inside Markdown) but also
-- dropped one legitimate class: the ai-fintech tag carries the bare
-- acronym CV ("computer vision"), and CVPR / ECCV conference papers used
-- to match through it.  After v1.0.64 the boundary correctly refuses
-- "CVPR" for "CV" — the acronym never appears standalone in those titles.
--
-- 26 stored articles in the 2026-10-25 snapshot are affected (all still in
-- the DB; v1.0.64 never deleted anything).  Measured replay: adding the
-- two conference names rescues exactly those 26 and introduces 0 articles
-- that the old substring rule had not already kept.
--
-- Deliberately NOT added:
--   * NTIRE  — one workshop series, and "CVPR NTIRE" already matches CVPR
--   * CVE    — security advisories, not computer vision; matching it was
--              itself a false positive of the old substring rule
--   * Application(s) for the APP tag — the 9 rescued rows are "Web
--     Application Security" / "Applications are now open for Fellowships",
--     which is not what the APP (手机应用) tag means
--
-- Keywords are user-managed data, so this migration appends only when the
-- entry is missing; it never rewrites or reorders the existing list.  That
-- rules out UNION (which sorts) — we rebuild from json_each in order and
-- then append.

BEGIN IMMEDIATE;

-- Two separate UPDATEs (one per new keyword) so each stays a plain append.
-- json_each preserves array order; the CASE only appends when absent.
UPDATE tag_definitions
SET keywords = CASE
      WHEN EXISTS (SELECT 1 FROM json_each(tag_definitions.keywords) WHERE value = 'CVPR')
        THEN keywords
      ELSE json_insert(keywords, '$[#]', 'CVPR')
    END
WHERE name = 'ai-fintech' AND keywords IS NOT NULL AND json_valid(keywords);

UPDATE tag_definitions
SET keywords = CASE
      WHEN EXISTS (SELECT 1 FROM json_each(tag_definitions.keywords) WHERE value = 'ECCV')
        THEN keywords
      ELSE json_insert(keywords, '$[#]', 'ECCV')
    END
WHERE name = 'ai-fintech' AND keywords IS NOT NULL AND json_valid(keywords);

INSERT OR IGNORE INTO schema_cleanup_log (migration, applied_at)
SELECT '038_ai_fintech_cv_acronyms', datetime('now')
WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_cleanup_log');

COMMIT;
