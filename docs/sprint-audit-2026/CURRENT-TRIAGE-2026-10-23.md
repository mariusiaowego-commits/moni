# Current Triage — 2026-10-23

Read-only re-verification of the 2026-09-06 audit against `main` @
`40c14b2` (v1.0.45), plus the leftovers found while cleaning branches.

This file answers three questions per item: does it still exist on current
`main`, how much value does it have, and what should happen next.

## Test baseline (measured, not claimed)

- `pytest tests/` -> **1018 passed / 8 failed / 2 skipped**.
- The 8 failures reproduce the pre-existing set already recorded in
  `STATUS.md` for v1.0.42/44/45:
  `test_api` share cases, `test_fetch_integration::test_dry_run_skips_insert`,
  `test_prompts::test_call_llm_logs_to_file`, and two
  `test_share_manifest` cases.
- `test_api.py` alone: 93 passed. `test_share_manifest.py` alone: passes.
  Therefore these are **test-isolation failures**, not product regressions.
- `test_prompts::test_call_llm_logs_to_file` is a genuine stale mock: it
  patches `moni.llm.client.subprocess.run`, but v1.0.42 moved subprocess
  execution to `moni/llm/runner.py`.

## Status of audit tickets on current main

| Ticket | Still present? | Evidence | Value | Next |
|---|---|---|---|---|
| P0-1 predict_tags on dead provider | **Partially fixed** | `cron/jobs.py` still calls `ChatClient`; but v1.0.42 shim now calls `resolve_active_model()` and current DB points tag model at DeepSeek | Medium (code hygiene + untested path), not outage | Migrate to `call_llm`, add spy test |
| P0-2 SPA path traversal | **Fixed** | `moni/web/server.py::resolve_static_path`, 31 tests | High, already shipped | None |
| P0-3 fetch_jobs no heartbeat | **Fixed** | `moni/db/leases.py`, migration 034, tests | High, already shipped | None |
| P0-4 orphan hermes child | **Fixed** | `moni/llm/runner.py` killpg | High, already shipped | None |
| P0-5 scheduler absent | **Present** | no `.cron.pid`; launchd plist not installed; `KeepAlive` true in repo | **High**: scheduled fetches/tagging do not run automatically | Decide scheduler ownership, then fix |
| P1-5 no failover/breaker | **Fixed** | `runner.py` CircuitBreaker/FailoverRunner | High, already shipped | None |
| P1-6 prompt via argv | **Fixed** | stdin transport in `runner.py` | High, already shipped | None |
| P1-7 llm_jobs claim race | **Fixed** | leases + atomic claim | High, already shipped | None |
| P1-8 predict_tags no mutex | **Present** | no per-job lock in `cron/jobs.py` | Medium | Bundle with P0-1/P0-5 |
| P1-9 dynamic href no scheme allow-list | **Present** | 11 `:href` sinks in `index.html`, no helper | **Medium-high**: LLM/fetched content can inject `javascript:` | Add `safeHref()` + tests |
| P1-10 RSS JWT leaked | **Fixed** | `moni/web/redact.py`, 34 tests | High, already shipped | None |
| P1-11 PUT source fans out | **Not reproduced** | `config_sources.py::update_source` updates one row by id | Low (stale/incorrect audit claim) | Close as not-applicable |
| P1-12 running-job alerts / broad except | **Partially present** | leases give detectability; no alerting | Medium | Defer |
| P2-13 polling lifecycle | **Mostly fixed** | teardown already exists in `app.js` share/fetch/worker polls | Low-medium | Audit remaining timers only |
| P2-14 CDN no SRI / no vendored copy | **Present** | `index.html` loads Alpine + GSAP from jsdelivr | **High for offline/demo use** | Self-host or vendor |
| P2-15 modal title innerHTML | **Fixed** | `modal.js` escapes title/buttons | Medium, shipped | None |
| P2-16 no CI | **Present** | no `.github/workflows` | Medium | Add minimal pytest workflow |
| P2-17 ad-hoc f-string SQL | **Present** | table-name interpolation only | Low | Defer |
| P3-18 llm imports web | **Present** | `moni/llm/jobs.py` imports `moni.web.routes.fact_check_plus` | Low-medium | Extract shared helper |
| P3-19 connection comments/retired fd | **Mostly fixed** | thread-local + real `close()` from v1.0.37.6 | Low | Close |
| P3-20 worker lock check-then-act | **Present** | `share/worker.py` checks lock, then Popen | Medium | Use atomic lock acquisition |

## Immediate queue (proposed order)

1. **Fix the 8 flaky tests** — establishes a trustworthy gate for everything
   else. Root cause is cross-module state: global `Path.write_text` patch,
   thread-local DB connections, and stale subprocess mocks.
2. **P0-5 scheduler ownership** — decide embedded-in-web vs launchd vs manual;
   install nothing until decided. This is a product decision, not just code.
3. **P1-9 safeHref** — bounded frontend change with high security payoff.
4. **P2-14 self-host Alpine/GSAP** — removes the remaining offline blank-page
   failure documented in STATUS v1.0.44.
5. **P0-1/P1-8 migrate predict_tags** — small code cleanup, needs spy test.
6. **P3-20 atomic worker lock** — small concurrency hardening.
7. **P2-16 CI** — only after the suite is reliably green.

## Deliberately not doing now

- Rewriting `P2-13` wholesale: most timers already have teardown.
- `P3-18` layering refactor: no user-visible value, touches many imports.
- `P1-12` alerting: needs an ops-channel decision.
