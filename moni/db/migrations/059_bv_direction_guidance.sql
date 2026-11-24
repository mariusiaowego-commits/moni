-- 059: business-direction research guidance (sprint-73)
--
-- The direction editor only had two short fields: a 6-character label and a
-- 200-character brief.  Real user intent is longer and messier, so it was
-- being squeezed into 200 characters and the resulting rubric drifted from
-- what the user actually wanted.
--
-- Two additive columns:
--   bv_direction.research_guidance
--       The guidance currently attached to the direction.  Editable, and
--       part of the direction definition next to label and brief.
--   bv_direction_research.guidance_snapshot
--       The guidance a given research version was actually built from.
--
-- A passed research stays usable only while the current guidance still
-- matches its snapshot -- the same rule label_snapshot / brief_snapshot
-- already follow in usable_research().  Changing the guidance therefore
-- expires the research and forces a re-run, so scores never carry a rubric
-- that was built from different instructions.
--
-- Existing rows get '' on both sides, which compares equal: the live
-- "微信生态" direction keeps its passed research v3 and stays usable.
-- Adding a column must not invalidate the only working direction.

ALTER TABLE bv_direction ADD COLUMN research_guidance TEXT NOT NULL DEFAULT '';

ALTER TABLE bv_direction_research ADD COLUMN guidance_snapshot TEXT NOT NULL DEFAULT '';
