/* scripts/evidence/sprint24_flash_timing_probe.mjs
 *
 * Measures how long the pin colour flash is actually visible, per path.
 * dad pinned all three paths to 700ms (2026-10-10), so this proves the
 * runtime behaviour rather than just the source constants:
 *
 *   path A  GSAP present, reduced-motion off   -> the normal case
 *   path B  GSAP absent                         -> CDN never loaded
 *   path C  GSAP present + prefers-reduced-motion: reduce
 *
 * For each path we watch the button's classList and record how long
 * `pin-btn--just-*` stays on, plus the computed animation-duration of the
 * CSS rule that paints it.
 *
 * Run: node scripts/evidence/sprint24_flash_timing_probe.mjs <base-url> [out.json]
 */
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const BASE = process.argv[2] || 'http://127.0.0.1:8904';
const OUTJSON = process.argv[3] || null;
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9336;
const profile = mkdtempSync(join(tmpdir(), 'sprint24-flash-'));

const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
  '--disable-gpu', '--hide-scrollbars', '--window-size=1200,900', 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const cleanup = () => {
  try { chrome.kill('SIGKILL'); } catch { /* gone */ }
  for (let i = 0; i < 10; i++) {
    try { rmSync(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 }); return; }
    catch { /* still writing */ }
  }
};
process.on('exit', cleanup);

async function endpoint() {
  for (let i = 0; i < 60; i++) {
    try {
      const j = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
      if (j.webSocketDebuggerUrl) return j.webSocketDebuggerUrl;
    } catch { /* not up */ }
    await sleep(250);
  }
  throw new Error('devtools endpoint never came up');
}

const ws = new WebSocket(await endpoint());
await new Promise((res, rej) => { ws.addEventListener('open', res); ws.addEventListener('error', rej); });
let id = 0; const pending = new Map();
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) {
    const { resolve, reject } = pending.get(m.id); pending.delete(m.id);
    m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
  }
});
const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
  const mid = ++id; pending.set(mid, { resolve, reject });
  ws.send(JSON.stringify({ id: mid, method, params, sessionId }));
});

const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
await send('Page.enable', {}, sessionId);
await send('Runtime.enable', {}, sessionId);
await send('Page.navigate', { url: `${BASE}/` }, sessionId);
await sleep(4500);

const evaluate = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true }, sessionId);
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 300));
  return r.result?.value;
};

// Install the harness: a real button carrying the same classes as the pin
// button, driven through the real window.pinPop() the app exposes.
const MEASURE = `(async () => {
  const btn = document.createElement('button');
  btn.className = 'pin-btn';
  btn.style.cssText = 'position:fixed;left:20px;top:20px;z-index:99999';
  btn.textContent = 'PIN';
  document.body.appendChild(btn);

  const FLASHES = ['pin-btn--just-pinned', 'pin-btn--just-unpinned'];
  const present = () => FLASHES.some((c) => btn.classList.contains(c));

  // Wait for the CSS animation to actually run so we time the paint, not a
  // no-op class toggle.
  const measureOnce = () => new Promise((resolve) => {
    btn.classList.remove(...FLASHES);
    void btn.offsetWidth;
    const t0 = performance.now();
    window.pinPop(btn, true);
    if (!present()) { resolve({ ms: 0, error: 'flash class never applied' }); return; }
    const tick = () => {
      if (!present()) { resolve({ ms: performance.now() - t0 }); return; }
      if (performance.now() - t0 > 3000) { resolve({ ms: -1, error: 'flash never removed' }); return; }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });

  const runs = [];
  for (let i = 0; i < 3; i++) runs.push(await measureOnce());

  // CSS side: the animation duration that paints it.
  const probe = document.createElement('div');
  probe.style.cssText = 'position:fixed;left:0;top:0';
  btn.classList.add('pin-btn--just-pinned');
  const animDur = getComputedStyle(btn).animationDuration;
  btn.classList.remove('pin-btn--just-pinned');
  probe.remove();

  const good = runs.filter((r) => r.ms > 0).map((r) => Math.round(r.ms));
  return JSON.stringify({
    runs: runs.map((r) => ({ ms: r.ms === -1 ? r.ms : Math.round(r.ms), error: r.error })),
    medianMs: good.length ? good.sort((a, b) => a - b)[Math.floor(good.length / 2)] : null,
    cssAnimationDuration: animDur,
    gsapPresent: typeof window.gsap !== 'undefined',
    reducedMotion: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  });
})()`;

const paths = {};

// Path A — GSAP present, reduced-motion off
paths.A_gsap_normal = JSON.parse(await evaluate(MEASURE));

// Path B — GSAP absent
await evaluate(`window.__gsapSaved = window.gsap; delete window.gsap; 'ok'`);
paths.B_no_gsap = JSON.parse(await evaluate(MEASURE));
await evaluate(`if (window.__gsapSaved) window.gsap = window.__gsapSaved; 'ok'`);

// Path C — GSAP present + prefers-reduced-motion: reduce
await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] }, sessionId);
await sleep(400);
paths.C_reduced_motion = JSON.parse(await evaluate(MEASURE));

const EXPECTED = 700;
const summary = Object.entries(paths).map(([name, d]) => ({
  path: name,
  medianMs: d.medianMs,
  cssAnimationDuration: d.cssAnimationDuration,
  gsapPresent: d.gsapPresent,
  reducedMotion: d.reducedMotion,
  withinTolerance:
    d.medianMs !== null && d.medianMs >= EXPECTED - 120 && d.medianMs <= EXPECTED + 250,
}));

const allWithin = summary.every((s) => s.withinTolerance);
const report = {
  base: BASE,
  capturedAt: new Date().toISOString(),
  expectedMs: EXPECTED,
  toleranceMs: 'measured value must land in [580, 950] (rAF granularity + paint start)',
  paths,
  summary,
  verdict: allWithin
    ? 'all three paths flash for ~700ms'
    : 'at least one path deviates from 700ms',
};

if (OUTJSON) writeFileSync(OUTJSON, JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
cleanup();
process.exit(allWithin ? 0 : 1);