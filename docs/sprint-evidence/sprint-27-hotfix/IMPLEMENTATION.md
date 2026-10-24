# v1.0.61 hotfix — normalize legacy 36kr source id

## Trigger

After v1.0.60 shipped, `moni db seed-config` failed on the live database:

```
sqlite3.IntegrityError: UNIQUE constraint failed: sources.name
```

Root cause: live databases created before v1.0.34.2 still carry the legacy
primary key `src-36kr_rss`, while `config/sources.yaml` and
`moni/db/seed_config.py` use `src-36kr-rss`. The seed upserts by `id`, so it
inserted a second `36kr_rss` row and hit the UNIQUE constraint on
`sources.name`. The failure rolled back the whole seed, including the new
v1.0.60 source.

## Fix

Migration `036_normalize_36kr_source_id.sql`:

1. Copies the legacy source row into a temp table.
2. Frees the legacy `name` and inserts the canonical parent row, preserving
   all source metadata.
3. Moves `source_tags` and `source_keyword_filters` to the canonical id in
   place. The filter row keeps its `id`, so
   `article_keyword_hits.filter_id` is not set to NULL.
4. Deletes the legacy parent only after the canonical parent exists; this
   also merges child rows when both ids were present.
5. Records `schema_cleanup_log` only when a legacy row was actually found.

`_CURRENT_VERSION` advances from 35 to 36.

## Dry-run on a copy of the production database

```
before {'version': 35, '36kr_ids': ['src-36kr_rss'], 'tags': 0, 'filters': 0,
        'hits_nonnull': 0, 'articles': 421}
migrate -> schema_version = 36
after_migrate {'version': 36, '36kr_ids': ['src-36kr-rss'], 'tags': 1,
               'filters': 0, 'hits_nonnull': 0, 'articles': 421}
after_seed {'version': 36, '36kr_ids': ['src-36kr-rss'], 'tags': 1,
            'filters': 1, 'hits_nonnull': 0, 'articles': 421}
new_source [{'id': 'src-chinese-independent-blogs', 'name':
             'chinese_independent_blogs', 'is_active': 1}]
```

Then `seed()` completed:

```
Config seed complete: 11 tag(s), 16 source(s), 41 source-tag link(s), 6 keyword filter(s)
new: [{'id': 'src-chinese-independent-blogs', 'name': 'chinese_independent_blogs', 'is_active': 1}]
```

## Tests

- Targeted (`036` + `030` + LLM counters): **29 passed**
- Full suite: **1226 passed / 2 skipped / 0 failed**

## Independent audit

The first audit verdict was REQUEST_CHANGES. It caught three P1 issues:

- rebuilding filter rows would clear `article_keyword_hits.filter_id`;
- canonical + legacy coexistence would silently drop legacy children;
- tests bypassed the real `migrate()` entry point.

All three were fixed, and migration tests now cover the v35 -> v36 entry
point, filter-id preservation, coexistence merge, idempotence, and cleanup
log semantics.

### Round 2 verdict: APPROVE

No P0/P1 remained. The reviewer raised two P2s:

1. A failed migration could leave the connection inside an open transaction.
2. This evidence file had not yet recorded round 2.

P2 #1 was fixed: `_run_sql_file()` now rolls back after a failed statement,
and `test_failed_migration_rolls_back_and_connection_is_usable` verifies both
the rollback and the absence of partial writes.
