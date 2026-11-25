---
name: verify-moni
description: "Drive the moni news app in a real browser and prove its UI behavior before shipping: 12 SPA routes, article table data, the LLM-usage dashboard card, and the desensitize lab's local preview. Use when asked to verify moni end to end, check console cleanliness, re-test a UI change on port 8901, or produce release evidence. Not for running the fetch pipeline or editing articles."
---

# verify-moni

moni is a FastAPI + SQLite + Alpine.js single-page app served by uvicorn. This skill drives the real pages with Playwright (real Chromium, no mocks), asserts observable state, and keeps screenshots plus a machine-readable `result.json` as proof.

All paths below are relative to the repo root (the moni checkout you are working in).

## Launch

1. Load secrets. Any moni process that touches sources needs `.env`; a worktree checkout does not have one (it is gitignored).

   ```bash
   cp /Users/mt16/dev/moni/.env .env   # skip if already present
   set -a && source .env && set +a
   ```

2. Isolate the database. `data/news.db` is gitignored, so a fresh worktree has none. Take a consistent snapshot instead of copying the live `.db` (WAL means a bare file copy loses data):

   ```bash
   sqlite3 /Users/mt16/dev/moni/data/news.db ".backup '$(pwd)/data/news.db'"
   ```

3. Start the dev server **in a persistent PTY**, not with `&`:

   ```bash
   /usr/local/bin/python3 -c "import uvicorn; from moni.web.server import create_app; uvicorn.run(create_app(), host='127.0.0.1', port=8901, log_level='warning')"
   ```

   A backgrounded child is reaped when the launching shell exits, so the server dies mid-run and Playwright reports `ERR_CONNECTION_REFUSED`. Keep the PTY session open for the whole run.

   `scripts/web-8901.sh start` also works when the calling shell survives, but it does not load `.env` for a worktree unless you copied it first.

4. Ready means usable, not just "port answers": `GET /` returns 200 **and** `GET /api/stats/llm-usage?period=30d` returns a body with `totals`. With an empty or missing DB the page still returns 200 while every card is zero — that is not ready.

Never start this on 8900. 8900 is launchd-managed production.

## Doctor

One read-only pass before driving anything:

```bash
lsof -nP -iTCP:8901 -sTCP:LISTEN          # port owned by us
git rev-parse --short HEAD                 # which code is under test
sqlite3 data/news.db "select count(*) from articles; select count(*) from llm_jobs;"
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8901/
```

Expected shape: one listener, article count in the thousands (a 0 means you skipped step 2), HTTP 200. If any check fails, fix it before interpreting UI results.

## Drive

Use `scripts/verify_moni.py` (Playwright, real Chromium). It covers the 12 SPA routes, the article table, the desensitize lab, and four responsive widths.

```bash
/usr/local/bin/python3 .codex/skills/verify-moni/scripts/verify_moni.py
```

Environment: `MONI_BASE` (default `http://127.0.0.1:8901`) and `MONI_EVIDENCE_DIR` (default `docs/moni-screenshots-verify-moni-$(date +%Y%m%d)`).

Selectors that are stable in this repo:

| Target | Selector |
|---|---|
| Nav rendered | `.topnav-links a` (11 links) |
| Article rows | `/articles` → `table tbody tr` |
| LLM usage card | `#stats-llm-usage` → `.stats-module-primary`, `.stats-module-row`, `table.stats-usage-table tbody tr` |
| Desensitize lab | `/desens-lab.html` → `.cols .col` (4), `#sample`, `#preview`, `#status`, `#c1` `#c2` `#c3`, `#switchState` |

Rules that keep results honest:

- Navigate with `wait_until="domcontentloaded"` then wait. `networkidle` on a route that polls (share, llmJobs) can time out legitimately.
- Do **not** sweep routes back-to-back and call the resulting `Failed to fetch` a defect. Leaving a page aborts its in-flight requests; re-test the route alone with a 5-6 s dwell before believing it.
- Attach the console listener once and clear the buffer per route. Attaching one listener per route multiplies every later error by the number of listeners and invents failures.
- `Transition was skipped` is Alpine's benign `x-transition` cancellation notice. Classify it as noise; never delete the transition to silence it.
- `/basket` ships two `422 (Unprocessable Entity)` resource errors on production as well (2026-11-25). They are listed in `KNOWN_BASELINE` inside the script: baseline errors are reported but do not fail the route; anything new does. Re-verify the baseline when it moves.

## Evidence

Write to `docs/moni-screenshots-verify-moni-<YYYYMMDD>/`, which `.gitignore` line 89 (`docs/moni-screenshots-*/`) already excludes — evidence never pollutes `main`.

Capture per check: screenshot file, the numbers that decided PASS/FAIL (`http`, `nav_links`, `tbody_rows`, card text, `scrollWidth` vs `innerWidth`), and the console error kinds. `result.json` holds the machine-readable roll-up.

A proof is the user path plus the resulting state: the rendered card must be compared against the API body (`/api/stats/llm-usage` `totals.calls`), not just "the page showed something". When a check is skipped, record `SKIPPED` with the reason — never count it as passed.

## Cleanup

- Stop only the PTY/process you started (Ctrl-C in the session). Never `pkill -f uvicorn`, which can hit production 8900.
- Keep the evidence directory. It is the deliverable of the run.
- Remove scratch scripts under `/private/tmp` you created for one-off diagnosis.

## Helpers

| Script | Purpose |
|---|---|
| `scripts/verify_moni.py` | Read-only UI sweep (12 routes + data + dashboard + lab + responsive). Safe to run any time. |
| `scripts/real_model_sample.py` | One-shot real-model sampling of the three LLM channels (summary / fact-check-plus / share brief). **Spends model quota and writes `llm_jobs` / share rows** — needs explicit authorization for the turn. |

Feature map: [`features/README.md`](features/README.md).
