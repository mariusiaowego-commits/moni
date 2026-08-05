# moni frontend architecture & accessibility audit — 2026-08-05

Scope: read-only audit of `/Users/mt16/dev/moni/moni/web/static/{index.html, css/main.css, js/app.js, js/theme.js, js/icons.js, js/pin_anim.js, js/articles.js, js/cron.js, js/fetch.js, js/share.js, js/crud.js}`.
Goal: surface maintainability risks that directly affect UX consistency, performance, accessibility, responsive behaviour, and design-token drift. Read-only — no files edited, no tests fabricated.

## TL;DR

| # | Risk | Severity | Scope |
|---|---|---|---|
| 1 | `app.js` is a 3612-line / 149 KB single Alpine `data()` factory — every fetch, modal, form, poll, and animation lives here | **P0** | high |
| 2 | 5 stub `js/*.js` files (`articles.js`, `cron.js`, `fetch.js`, `share.js`, `crud.js`) shipped but not loaded; `crud.js` registers a duplicate `Alpine.data('crud', …)` that *would* collide if loaded | **P0** | high |
| 3 | All long-running async work has no global teardown: rAF loops, `_fetchPollTimer`, `_sharePollInterval`, `_workerLogPollInterval`, `pinUndoBatch.timer`, `_pendingPinToggles[*].timer`, `setTimeout(initStageWithSources, 100)` busy-wait | **P0** | high |
| 4 | 42 `alert()`/`confirm()` calls in `app.js` + 1 in `crud.js` — native blocking modals, no focus management, no aria-live announcement | **P0 (a11y)** | high |
| 5 | 7 of 12 modals miss Escape-key handling (`articleDetail`, `basketDetail`, `crudFormOpen`, `showModal`, `configModalOpen`, `fetchProgressModal`, `fetchDateModal`); zero modals trap focus | **P0 (a11y)** | high |
| 6 | `_pollFetchJob` busy-waits on `setTimeout(initStageWithSources, 100)` with no upper bound — if `loadSources()` fails, the loop runs forever | **P1** | high |
| 7 | `navigate()` and `onPopState()` are byte-identical dispatch trees; any view-loader change must be made twice | **P1** | mid |
| 8 | Pin-column sticky issue: pin icon uses inline `x-html` SVG, but the article-detail modal re-renders the icon DOM via the same SVG — every click forces full SVG re-parse | **P2** | low |
| 9 | Source-icon `iconMap`/`labelMap`/`colorMap` is duplicated four times: `getSourceIcon()` (app.js:1959), `_activeSourcesForModal()` (app.js:1702), HTML `tag-chip` (iconMap in `/static/icons/sources/*.png`), and the bundle-rebuilt `theme.js` parser does not see them | **P1** | mid |
| 10 | CSS uses `oklch(from var(--xxx) l c h / α)` (Relative Color Syntax) in 30+ places — Chromium 119+, Safari 16.4+, Firefox 128+. Older / corporate browsers silently render flat fallback; gradient/box-shadow values degrade | **P1** | mid |
| 11 | No responsive layout past `@media (max-width: 768px) { .fetch-result-panel { grid-template-columns: 1fr; } }` — single media query at css line 1085. Top nav, tables, particle stage, tag grid are not breakpoint-aware | **P1** | mid |
| 12 | Design tokens are declared in CSS `:root` and re-declared as a JS constant in `theme.js DEFAULT_THEME` (theme.js:8-47). Adding a new `--xyz` token requires editing both files; nothing prevents drift | **P1** | mid |
| 13 | Modal close handler is overloaded: 7 different modals open/close via direct property assignment (`this.showModal = true` / `this.configModalOpen = false`) mixed with named methods (`closeModal()`, `closeConfigModal()`) — no single helper to escape, focus-restoration, or aria-hidden swap | **P1** | mid |
| 14 | All 12 modals use bare `<div class="modal-backdrop">`, no `role="dialog"`, no `aria-modal="true"`, no `aria-labelledby` | **P1 (a11y)** | high |
| 15 | Alpine polls (`_startSharePoll`, `_pollFetchJob`, `loadLlmJobs`) re-create network-bound `.then` chains inside `setInterval` callbacks — a 30s hung fetch stacks inside the next tick | **P2** | low |
| 16 | `index.html` lacks `<meta name="theme-color">`, `<meta name="color-scheme">`, `<meta name="description">`, og:* tags, and a favicon | **P2** | low |

Low-risk scope that does **not** need to be in scope for follow-up:
- Per-component CSS rules (well-organized, fine)
- The `theme.js` parsing logic
- The Alpine `x-template` template literals (good for stateless markup)
- The 4 unused JS files (just dead code on disk; not user-visible)

---

## Findings — detailed

### F1 — 3612-line `app.js` is a single Alpine `data()` factory (P0, high scope)

**Where**: `moni/web/static/js/app.js` (full file)
**Evidence**: `function app() { return { ...themeMixin(), … }; }` spans L64 to L3612. Every loader, form, modal, animation, and ticker lives inside the returned object. The class `FetchParticle` (L12-62) is the only thing declared at module scope.
**Why it matters**:
- 148 KB of plain-JS that the browser parses once per hard-refresh.
- Every release concatenates an unrelated feature diff (e.g. fetch-config modal, brief progress, pin undos) into the same file → merge conflicts and review surface balloon.
- No tree-shaking. No minification. `?v=1785000000` cache-bust on every release.
- Init/validation for ~12 separate views (articles, crud, cron, fetch, llmJobs, share, basket, config, llmSettings, theme, article-detail, fetch-progress) all share the same object → state pollution risk when one view writes to another's key.
**Maintenance blast radius**: any change to pin, fetch-poll, or share-queue has to thread through 6 unrelated state slices.

### F2 — Stub JS files are shipped but not loaded (P0, high scope)

**Where**: `moni/web/static/js/articles.js`, `js/cron.js`, `js/fetch.js`, `js/share.js` (all 1 line: `// … is in app.js`) and `js/crud.js` (62 lines, registers `Alpine.data('crud', …)` at L7-60).
**Evidence**: `index.html` L2348-2353 loads only `app.js`, `theme.js`, `icons.js`, `pin_anim.js`. The 4 stub files are committed but `<script>`-less. `crud.js` defines a full Alpine CRUD component that would shadow the `crud`, `loadCrudArticles`, `showDeleteModal`, `closeModal`, `confirmDelete` methods already inside `app()` (the same names exist on `app.js:1016-1045`, see below). If anyone re-adds `<script src="js/crud.js">`, the loadCrudArticles/createBasis methods overwrite each other.
**Why it matters**:
- Dead code on disk confuses future maintainers (will look like a real boundary to refactor toward).
- `crud.js` reaching production once would silently change DELETE behaviour (it does *not* send `?confirm=1`, whereas `app.js:1036` does).
- `articles.js`/`cron.js`/`fetch.js`/`share.js` are misleading — they are pure comments.

### F3 — Zero global async teardown on view change (P0, high scope)

**Where**: throughout `app.js`. Concrete references:

| Timer / loop | Started at | Cancellation path |
|---|---|---|
| `_fetchPollTimer` (single-source poll) | `app.js:1439`, `app.js:1552` | cleared at L1385/1419/1474/1521/1559/closeFetchProgress |
| `_animFrame` (particle rAF) | `app.js:1665`, `app.js:1825` | only cancelled in `_stopParticleAnimation` (L1680) |
| `_burstTimer` (200 ms particle spawn) | `app.js:1668` | cleared only in `_stopParticleAnimation` |
| `_sharePollInterval` (5 s queue poll) | `app.js:2638` | never cleared — runs for the SPA lifetime |
| `_workerLogPollInterval` (3 s log tail) | `app.js:2557-2565` | cleared only when modal closes (L2586). If `triggerWorker()` fails between fetch and `_startWorkerLogPoll()`, no cleanup. |
| `pinUndoBatch.timer` | `app.js:2912` | cleared in `cancelPinUndo()` (L2928) and on expire (L2915) |
| `_pendingPinToggles[id].timer` | `app.js:2748` | cleared on toggle within window (L2737) or self-expiry |
| `poll` (1 s restart-status poll) | `app.js:94` | cleared at L100/L107; has 15-iteration cap (✓) |
| `setTimeout(initStageWithSources, 100)` | `app.js:1400`, `app.js:1489` | **no exit condition** — busy-waits if `sources` never loads |
| `setTimeout(() => this.jobDetailCopied = false, 1500)` | `app.js:3268` | no-op for a closed modal |
| `setTimeout(() => this.copyToast = false, 2000)` | `app.js:1979` | safe |
| `setTimeout(() => workerRunning reset, 2000)` | `app.js:2277` | safe |
| `_startWorkerLogPoll` 3 s setInterval | `app.js:2562` | cleared only via `closeWorkerModal` |

**Why it matters**:
- `_sharePollInterval` runs forever once the user visits `/share` (every 5 s, hits `/api/share/tasks`). The poll is never torn down on logout, navigation away, or error. A new visit does *not* start a duplicate (it does check `_sharePollInterval`), so it only leaks by *not stopping*.
- `setTimeout(initStageWithSources, 100)` (app.js:1400, 1489) is the worst pattern: if the Fetch-progress modal opens, `_initParticleStage()` is called only after `sources` array is populated. If `loadSources()` 4xx's, the timeout keeps re-queuing forever, eating CPU and logging nothing.
- `_animFrame` + `_burstTimer` are tied to particle stage but stage DOM is rebuilt each fetch — old rAF callbacks can race against new DOM references (`getElementById('particleStage')` is called every callback) producing Z-fighting flicker.

### F4 — 42 `alert()`/`confirm()` calls block the SPA (P0 a11y, high scope)

**Where**: full grep at `app.js: alert\(\)|confirm\(\(` returns 42 hits (see F4 evidence). All actions that need confirmation (delete article, delete tag, delete source, delete filter, delete provider, cancel LLM job, restart service, test provider, generate summary, fact-check, copy article URL, pin toggle failure, brief creation, share-format validation, queue add, worker trigger) use native blocking dialogs.
**Why it matters**:
- Native `alert()` stops event-loop and does not auto-restore focus after dismissal.
- Screen readers announce them, but SPA focus state is lost — keyboard users have to re-tab from the top.
- Native dialogs are not styleable, so dark/light themes and density tokens don't apply.
- Look-and-feel jank breaks the "Sophon.at / moni / Nullframe" tonal consistency the rest of the SPA commits to.
- Confirmation flow for `restartService()` (L87) is the worst: 15-iteration 1 s poll runs after the user OKs, but `confirm()` doesn't reveal the 15s worst-case.

### F5 — Modal accessibility regresses on the most-used modals (P0 a11y, high scope)

**Where**: `index.html` L403 `fetchDateModal`, L454 `fetchProgressModal`, L1081 `basketDetail`, L1637 `articleDetail`, L1886 `configModalOpen`, L2218 `showModal` (delete confirm), L2234 `crudFormOpen`. None carry `@keydown.escape.window`.
**Evidence**: `index.html` only attaches Escape to `llmProviderFormOpen` (L1556), `llmModelFormOpen` (L1589), `jobDetail` (L1711), `shareEstimate` (L1756), `workerModalOpen` (L1836), `themeModalOpen` (L2298).
**Why it matters**:
- `articleDetail` is the *primary* read flow for any article. Keyboard-only users cannot dismiss it.
- `crudFormOpen` / `showModal` (delete confirm) / `configModalOpen` (config edit) — destructive flows. Escape close is baseline WCAG 2.1.1 expectation.
- Zero modals have `role="dialog"` or `aria-modal="true"`. None expose a labelled-by header. None trap focus. None restore focus on close.

### F6 — `setTimeout(initStageWithSources, 100)` busy-wait (P1, high scope)

**Where**: `app.js:1400` and `app.js:1489`.
**Why it matters**: a fetch modal opens with `sources` empty; `_initParticleStage()` calls `getElementById('particleStage')` etc. The two consecutive `setTimeout(initStageWithSources, 100)` calls reschedule themselves indefinitely until `this.sources.length > 0`. If `loadSources()` 5xx's (server down, vite hot-reload misfire), this loop runs forever, holding the modal open. There is no max-retry counter, no expiration, no `setTimeout(..., 5000)` "give up" branch.
**Reproduction**: kill the dev server while a fetch modal is open, watch browser tab freeze after a few seconds.

### F7 — `navigate()` and `onPopState()` have identical dispatch trees (P1, mid scope)

**Where**: `app.js:287-330` (`navigate`) vs `app.js:334-361` (`onPopState`) duplicate the same 9-branch `if/else if` ladder for view loading. `init()` at L2649 has a *third* copy for the boot path.
**Why it matters**:
- Adding the new `_loadArticlesWithPaging()` branch was already duplicated three times.
- `navigate()` calls `loadPinnedArticles()` (L310) and so does `onPopState()` (L341), but `init()` (L2674) only calls `_loadArticlesWithPaging()` and *not* `loadPinnedArticles()` — silent inconsistency.
- Refactoring a loader (e.g. CR1/Sub-tab index hydration) means three diff sites.
- Three sites have *almost* identical logic — none are tests-protected.

### F8 — Pin render thrash (P2, low scope)

**Where**: `index.html:134` `: <span x-html="pinIcon(a.is_pinned)"></span>`; `app.js:2801` `_pinPopByAid` triggers pop, which re-applies `.is-pinned` and a transient class. The icon is re-rendered on every property change of `a.is_pinned`.
**Why it matters**: each render goes through `pinIcon(filled)` which builds a full SVG string. For 200 pinned rows on the Pin tab, 200 SVG strings concatenated into the DOM on every state change. Not a blocker, but pure rework.

### F9 — Source-icon map duplicated 4× (P1, mid scope)

**Where**:
- `app.js:1959-1973` `getSourceIcon()`
- `app.js:1702-1761` `_activeSourcesForModal()` (also redraws `iconMap`, `labelMap`, `colorMap`)
- `index.html:293` `<img :src="getSourceIcon(s.name)" ...>` — at runtime
- `moni/web/static/icons/sources/*.png` (10 PNG files)

**Why it matters**: adding a new source (e.g. `v2ex_rss`) requires: drop a PNG; update `getSourceIcon()`; update `_activeSourcesForModal()`; update the `_chainRows` sortOrder (L1730). Three JS files for a single config edit.

### F10 — `oklch(from var(--xxx) ...)` Relative-Color Syntax adoption (P1, mid scope)

**Where**: 30+ rules in `css/main.css` (lines 96, 108, 188, 191, 192, 203, 222, 223, 236, 284, 334-335, 369, 389, 404, 446, 481-490, 516, 577, 782, 789, 791, 839-846, 870, 881, 903, 947, 1139, 1154, 1192-1193).
**Compatibility**:
- Chrome 119+ (Nov 2023) ✓
- Safari 16.4+ (Mar 2023) ✓
- Firefox 128+ (Jul 2024) ✓
- Any browser older than mid-2024 silently falls back to the *initial computed value* (often `transparent` for backgrounds, `none` for shadows). Box-shadows in particular — buttons "lose" hover-tints on Firefox 127.
**Why it matters**: moni is a local dashboard for `mt16/dev` on macOS 26.5.1 (Firefox-capable). If the dev opens it on a pinned browser tab, occasional Firefox upgrades may break style-driven visibility (focus-rings, hover-states) silently.

### F11 — Single mobile breakpoint (P1, mid scope)

**Where**: `css/main.css:1085` is the only `@media` rule.

```css
@media (max-width: 768px) { .fetch-result-panel { grid-template-columns: 1fr; } }
```

Everything else is fixed-width:
- `.content { max-width: 1280px; padding: 32px 40px 64px; }` — no narrow-screen padding collapse.
- `.topnav-links { display: flex; gap: 2px; overflow-x: auto; }` — relies on horizontal scroll, no hamburger.
- `.table-container` tables have no `overflow-x: auto` (or have one inside `.table-container`'s implicit overflow:hidden, but render is raw `<table>`). On a phone, the URL/source columns scroll horizontally while the rest doesn't (visible with the demo phone preview).
- `.article-card-grid { grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)) }` — minmax 220 means on 320 px phones, only one column. Fine. But nested `.pin-col { width: 44px }` plus 8 other columns makes the articles table scroll on any width < ~980 px.
- `.particle-stage { width: 100%; height: 420px; }` — fixed 420 px height on a 5" phone = takes >60% of viewport. The animation runs regardless of `prefers-reduced-motion`.
- Viewport meta: `<meta name="viewport" content="width=device-width, initial-scale=1.0">` (L5). No `maximum-scale`; Apple devices default to 1.0 zoom behaviour but assistive tech may not respect this.

### F12 — Design tokens drift between CSS and theme.js (P1, mid scope)

**Where**:
- `css/main.css:7-29` `:root { --radius, --background, --foreground, --card, --card-fg, --muted, --muted-fg, --accent, --accent-fg, --destructive, --destructive-fg, --border, --input, --ring, --chart-2..5, --shadow, --shadow-lg, --font }`.
- `css/main.css:32-48` `.dark` block re-declares 17 of these.
- `js/theme.js:8-47` `DEFAULT_THEME` re-declares 14 of these in JavaScript object form.
**Why it matters**: adding a new `--ring-strong` requires editing three places (`:root`, `.dark`, `theme.js`). The drift-surface is real — `.dark` declares `--ring: oklch(0.967 0.015 90.2);` (茉莉白, hue ≈ 90°) but `DEFAULT_THEME.dark` declares `--ring: oklch(0.967 0.015 90.2)` ✓. OK on this token, but `--accent-fg` differs by **dark/light**: `oklch(0.218 0.000 89.9)` (玄色) for light, `oklch(0.170 0.004 164.5)` (玄黑) for dark. The JS theme engine re-encodes everything at runtime (`applyTheme()` at L137-157), so any visual debug usually needs to compare `styleEl.textContent` against the `main.css` `:root`.
**Possible cross-check**: comment in `theme.js:107` says "Alpine.js theme data + methods (merged into app())" — but this is a single-string write to `<style id="theme-override">` overwriting the entire `:root`, so dark-mode is also affected. L154 toggles `body.classList.toggle('dark', …)` for the `.dark` block in main.css — and then `applyTheme()` injects the full light/dark vars into `:root` regardless. The order of operations works but is fragile.

### F13 — Modal wiring is ad-hoc (P1, mid scope)

**Where**: 12 modals, each opens via the state flag alone (`x-show="flagName"`). Closing styles:
- `closeLlmProviderForm()` sets `this.llmProviderFormOpen = false`
- `closeWorkerModal()` clears poll, sets `this.workerModalOpen = false`
- `basketDetail = null` direct assignment in 4 places (L1081 close, L1085 close-button, L1154 close-button, L1154 confirm-delete)
- `crudFormOpen`, `showModal`, `configModalOpen` have method-and-assignment mixed usage
**Why it matters**:
- No central registry → adding focus trap or aria-modal attribute to all modals is N-file edit.
- The "polling" modal (fetch progress) has its own logic for `if (this._fetchPollTimer)` but doesn't reset e.g. `_burstTimer` — see F3.
- Critical modals (basket, articleDetail, crudForm) have no `close()` method at all.

### F14 — Modals don't expose dialog semantics (P1 a11y, high scope)

**Where**: every `<div class="modal-backdrop">` in `index.html` L403-L2234.
**What's missing**:
- `role="dialog"` (or `role="alertdialog"` for confirm/delete modals)
- `aria-modal="true"`
- `aria-labelledby="..."` pointing at the modal `<h3>` (none of the `<h3>` carry an `id`)
- `aria-describedby` for modal body content
- `<button>` close buttons need `aria-label="关闭"` instead of bare `&times;` glyphs

### F15 — Polling `.then()` chains inside `setInterval` (P2, low scope)

**Where**: `app.js:1494-1550` (the `tick` function inside `_pollBatchJob` polls every 5 s; if a 4s fetching job is hung, the *next* tick fires before this one resolves, stacking `setInterval` and `Promise` overlaps).

### F16 — Missing head-meta for a complete web app (P2, low scope)

**Where**: `index.html` L1-17 (`<head>`).
**Missing**:
- `<meta name="description">` (currently just `<title>moni</title>`).
- `<meta name="theme-color">` (would let the OS tint the address bar on mobile/Chrome).
- `<meta name="color-scheme" content="light dark">` (lets the browser choose default form widget colours instead of flashing white-on-dark).
- No favicon link — `GET /favicon.ico` 404 logged for every page load.
- No `<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,...">` — there's a `moni_v2.png` referenced at `index.html:468`.

---

## Theme / token inventory (cross-ref F12)

`css/main.css` declares 18 semantic tokens under `:root` (lines 7-29):

| Token | Light | Dark | theme.js DEFAULT_THEME | Drifted? |
|---|---|---|---|---|
| `--background` | oklch(0.963 0.013 97.4) | oklch(0.170 0.004 164.5) | both ✓ | no |
| `--foreground` | oklch(0.218 0.000 89.9) | oklch(0.928 0.034 84.6) | both ✓ | no |
| `--card` | oklch(0.996 0.007 97.3) | oklch(0.218 0.000 89.9) | both ✓ | no |
| `--card-fg` | oklch(0.218 0.000 89.9) | oklch(0.928 0.034 84.6) | both ✓ | no |
| `--muted` | oklch(0.928 0.034 84.6) | oklch(0.257 0.030 61.5) | both ✓ | no |
| `--muted-fg` | oklch(0.486 0.040 87.0) | oklch(0.633 0.000 89.9) | both ✓ | no |
| `--accent` | oklch(0.819 0.109 302.2) | oklch(0.819 0.109 302.2) | both ✓ | no |
| `--accent-fg` | oklch(0.218 0.000 89.9) | oklch(0.170 0.004 164.5) | both ✓ | no |
| `--destructive` | oklch(0.542 0.208 19.5) | oklch(0.542 0.208 19.5) | both ✓ | no |
| `--destructive-fg` | oklch(0.963 0.013 97.4) | oklch(0.928 0.034 84.6) | both ✓ | no |
| `--border` | oklch(0.848 0.030 75.6) | oklch(0.337 0.007 84.6) | both ✓ | no |
| `--input` | oklch(0.848 0.030 75.6) | oklch(0.337 0.007 84.6) | both ✓ | no |
| `--ring` | oklch(0.963 0.013 97.4) | oklch(0.967 0.015 90.2) | both ✓ | no |
| `--chart-2..5` | defined | not redeclared in dark | light ✓ | **dark undeclared — falls through to :root** (intentional, but means dark-mode charts share light-mode hues) |
| `--shadow` / `--shadow-lg` | rgba-ish oklch | heavier oklch | not in theme.js DEFAULT_THEME (theme.js:8-47 ends before `--shadow` at L30) | theme.js drops shadow tokens — when a custom theme imports, it loses the shadows unless they re-include them |
| `--radius` | 0.375rem | not redeclared | not in theme.js | **theme.js drops radius** — themed apps lose corner radius on import |
| `--font` | `'Inter', …` | not redeclared | not in theme.js | fonts survive via main.css only; themes can't re-font |

---

## Cross-file impact matrix

| Action | Files touched | Risk |
|---|---|---|
| Add a new view (e.g. /reports) | index.html (section), app.js (`navigate`, `onPopState`, `init` — 3 sites), `_viewToPath` + `_pathToView`, optionally a load-X function | 3-site copy-paste (F7) + risk of stale dispatch |
| Add a new source icon | `static/icons/sources/x.png` + `getSourceIcon()` + `_activeSourcesForModal()` (iconMap + sortOrder) | 2-site JavaScript (F9) |
| Add a new design token | `main.css :root` + `.dark` if needed + `theme.js DEFAULT_THEME` | 3-site (F12) |
| Add a modal | index.html (12th `<div class="modal-backdrop">`) + `app.js` open/close + escape handler | repeated F13 boilerplate |
| Pin / unpin behaviour change | app.js togglePin/_applyPinVisual/_commitPinToggle (3 sites, ~80 LOC of related state) plus index.html three `<tr>` rows | ok, single-file mostly |

---

## Low-risk scope (don't change unless directly relevant)

- The stats-row, animation `@keyframes fadeUp`/`pulse`, button hover transitions (`.btn:active { transform: scale(0.96) }`) — all fine.
- The article-card grid (`.article-card-grid` L858) and `.format-chip` (L876) — both modern patterns.
- The `<details>`/`<summary>` native disclosure used in share sections and tag-filter dropped article detail — accessible by default.
- Inline `x-html="pinIcon(...)"` — only fails on F8 perf, but works.
- `theme.js DEFAULT_THEME` is robust (HEX fallback at L100, hue-based fallback at L93-101).
- `icons.js` and `pin_anim.js` are well-scoped — small, single-purpose, no scope leak.

---

## Recommended follow-up (not blocking)

If a follow-up sprint is planned, the highest-leverage items are:
1. **F5 + F14** together: add a `useModal()` helper that wires `role="dialog"`, `aria-modal="true"`, focus trap + restore, Escape handler. Convert all 12 modals via small patch passes. Combined cost: ~150 LOC + 12 modal closures.
2. **F1**: split `app.js` into feature modules. Even a partial split (basket, share, llm) into Alpine.plugins/local data would cut the file by ~50% and isolate the F3 timer leaks per file.
3. **F2**: delete the 4 stub files; if `crud.js` was meant to be a real boundary, integrate it (and remove the duplicate in `app.js:1016-1045`).
4. **F6**: replace the busy-wait `setTimeout(initStageWithSources, 100)` with a one-shot `watch('sources.length', ...)` (Alpine.$watch) or a 5 s max-retry cap.
5. **F10**: provide a Firefox-128-or-newer lint warning in CI, or wrap `oklch(from …)` in `@supports (background: oklch(from red 0 0 0))` queries with a fallback.

---

Audit performed read-only. No code was modified, no tests fabricated, no commits made.
