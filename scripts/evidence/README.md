#!/usr/bin/env node
/* scripts/evidence/README — sprint-24 evidence probes
 *
 * These are NOT pytest tests. `pytest tests/` never collects them and CI
 * does not run them. They exist to produce reviewable artifacts for one
 * round of verification; each needs a dev server on a free port and a
 * local Google Chrome, so they cannot run unattended.
 *
 * Kept out of tests/ on purpose (warden L-3): a .mjs file sitting in tests/
 * reads as "this is covered by the test suite", which is false.
 *
 * Usage (from the repo root, with the server already listening):
 *
 *   # 1. which family actually rendered each weight  (needs server)
 *   node scripts/evidence/sprint24_font_probe.mjs \
 *        http://127.0.0.1:8902 docs/sprint-24-evidence/font-platform-probe.json
 *
 *   # 2. is the variable wght axis really applied  (needs server)
 *   node scripts/evidence/sprint24_font_axis_probe.mjs \
 *        http://127.0.0.1:8902 docs/sprint-24-evidence/font-axis-probe.json
 *
 *   # 3. offline first-paint timing + screenshots   (needs server)
 *   node scripts/evidence/sprint24_offline_probe.mjs \
 *        http://127.0.0.1:8902 docs/sprint-24-evidence
 *
 * Each exits non-zero if its own check fails, so they can be chained in CI
 * later if a headless environment is ever wired up.
 *
 * Port hygiene: the probes take the base URL as argv[2] and bind Chrome to
 * fixed debug ports (9333/9334/9335/9336). Check those are free before
 * running, and never point a probe at another sprint's server.
 */

## sprint24_flash_timing_probe.mjs

Measures how long the pin colour flash is actually visible at runtime, on all
three paths (GSAP present / GSAP absent / prefers-reduced-motion: reduce).
Confirms the 700ms pin (dad, 2026-10-10) holds in a real browser, not just in
the source constants.

```bash
node scripts/evidence/sprint24_flash_timing_probe.mjs \
     http://127.0.0.1:8904 docs/sprint-24-evidence/flash-timing-probe.json
```

Needs a dev server and uses Chrome debug port 9336.
