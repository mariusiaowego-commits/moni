# Sprint Evidence Archive

Local evidence retained from completed sprints. These are verification
artifacts, not source files; nothing here is imported or served by the app.

## Contents

| Directory | Source | What it proves |
|---|---|---|
| `sprint-24/` | `moni-worktrees/26101001-render-hygiene/docs/sprint-24-evidence/` | v1.0.44 render hygiene: online/offline/reduced-motion screenshots plus flash-timing, font-axis, font-platform and offline probe JSON |
| `sprint-25/` | `moni-worktrees/26101201-motion/docs/sprint-25-evidence/` | v1.0.45 motion work: before/after FPS runs, B-group verification, mutation probes |
| `reference-reports/` | `moni-ref-fact-check/*REPORT.md` | Read-only research for sprint-16 ($name placeholder fix) and sprint-16.5/16.6 fact-check hardening |

## Provenance

- Archived 2026-10-23 from temporary worktrees before removing them.
- The sprint-24 and sprint-25 files were verified byte-for-byte against
  their source directories (`shasum -a 256`); the four reference reports
  were verified with `cmp`.
- Probe scripts themselves live in `scripts/evidence/` on `main`.

## Not archived

- Code, tests and plan documents belong in their normal tracked locations.
- Build artifacts, caches and unrelated working-tree files were not copied.
