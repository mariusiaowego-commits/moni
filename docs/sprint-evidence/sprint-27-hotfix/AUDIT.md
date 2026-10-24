# Independent audit — v1.0.61 hotfix

Auditor: separate read-only sub-agent (no code changes, no commits).

## Round 1 — REQUEST_CHANGES

### P1-1 — filter id lost, hits cleared

The first draft deleted and re-inserted `source_keyword_filters` rows. The
new row got a new `id`, so `article_keyword_hits.filter_id` was set to NULL
by `ON DELETE SET NULL`.

**Fix**: move rows with `UPDATE source_keyword_filters SET source_id = ...`
so the primary key stays stable.

### P1-2 — canonical + legacy coexistence silently dropped children

The first draft deleted legacy child rows before knowing whether the
canonical parent existed.

**Fix**: ensure the canonical parent exists first, then move tags/filters,
then delete the legacy parent.

### P1-3 — tests bypassed the real entry point

Tests called `_run_sql_file()` directly instead of `migrate()`, so version
gating and real startup ordering were not covered.

**Fix**: tests now build a real v35 database, remove the v36 version marker,
and call `migrate()`.

### P2s from round 1

- stale docstrings in the migration 030 tests: fixed;
- cleanup log recorded on fresh databases: now inserted only when a legacy
  row was actually found;
- explicit transaction semantics: 036 now uses `BEGIN IMMEDIATE` / `COMMIT`.

## Round 2 — APPROVE

No P0/P1 remained.

### P2 — failed statement left an open transaction

**Fixed**: `_run_sql_file()` rolls back after a failed statement, and a
regression test verifies `in_transaction == False` plus no partial writes.

## Verification evidence

- Targeted tests: `29 passed`
- Full suite: `1226 passed / 2 skipped / 0 failed`
- Production-database copy replay:
  - before: v35, id `src-36kr_rss`, articles 421, tags 0, filters 0
  - after migrate: v36, id `src-36kr-rss`, articles 421, tags 1
  - after seed: new source `chinese_independent_blogs` present
  - `cleanup_log` records 036 exactly once

## Residual risk

The production database has no non-null `article_keyword_hits.filter_id`
rows, so the production replay cannot demonstrate an actual surviving link.
That path is covered by the isolated regression test, which creates a
non-null link and asserts the same filter id after migration.
