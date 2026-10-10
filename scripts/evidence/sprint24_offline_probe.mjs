/* scripts/evidence/sprint24_offline_probe.mjs — AC-7 evidence capture.
 *
 * Loads the app with the network blocked AFTER the document shell arrives,
 * so we can observe what a user sees when the CDN subresources fail. Reports
 * how long until first readable content, and screenshots each state.
 *
 * Note on scope (AC-7 boundary): moni still loads Alpine from jsdelivr, so a
 * cold offline load can leave the SPA blank. This probe measures and
 * documents that; it does not treat it as fixed by this sprint.
 *
 * Run: node scripts/evidence/sprint24_offline_probe.mjs <base-url> <out-dir>
 */
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const BASE = process.argv[2] || 'http://127.0.0.1:8902';
const OUT = process.argv[3] || '.';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9334;
const profile = mkdtempSync(join(tmpdir(), 'sprint24-offline-'));

const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
  '--disable-gpu', '--hide-scrollbars', '--window-size=1440,900', 'about:blank',
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
await send('Network.enable', {}, sessionId);

async function shot(name) {
  const { data } = await send('Page.captureScreenshot', { format: 'png' }, sessionId);
  writeFileSync(join(OUT, name), Buffer.from(data, 'base64'));
  return name;
}
const evalJs = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true }, sessionId);
  return r.result?.value;
};

const report = { base: BASE, capturedAt: new Date().toISOString(), states: [] };

// ── state 1: warm online load ─────────────────────────────────────────────
const t0 = Date.now();
await send('Page.navigate', { url: `${BASE}/` }, sessionId);
await sleep(4000);
const online = await evalJs(`(() => {
  const body = document.body;
  const cs = getComputedStyle(body);
  const nav = document.querySelector('.topnav, nav, #app');
  const text = (body.innerText || '').trim();
  return JSON.stringify({
    title: document.title,
    bodyChars: text.length,
    firstText: text.slice(0, 120).replace(/\\s+/g, ' '),
    bg: cs.backgroundColor,
    navPresent: !!nav,
    gsapLoaded: typeof window.gsap,
    alpineLoaded: typeof window.Alpine,
    interLoaded: document.fonts ? document.fonts.check('600 16px Inter') : null,
    fontFaceCount: document.fonts ? document.fonts.size : null,
  });
})()`);
report.states.push({
  state: 'online (warm)',
  elapsedMs: Date.now() - t0,
  screenshot: await shot('01-online-warm.png'),
  metrics: JSON.parse(online),
});

// ── state 2: offline reload, network blocked ──────────────────────────────
// Block after the document is already cached so the reload starts from the
// local server but every CDN subresource fails.
await send('Network.setBlockedURLs', { urls: ['*://cdn.jsdelivr.net/*', '*://fonts.googleapis.com/*', '*://fonts.gstatic.com/*'] }, sessionId);
const t1 = Date.now();
await send('Page.reload', { ignoreCache: true }, sessionId);

// poll for first readable paint
let firstReadableMs = null;
for (let i = 0; i < 30; i++) {
  await sleep(100);
  const chars = await evalJs(`(document.body.innerText || '').trim().length`);
  if (chars > 0 && firstReadableMs === null) firstReadableMs = Date.now() - t1;
}
await sleep(2500);
const offline = await evalJs(`(() => {
  const body = document.body;
  const text = (body.innerText || '').trim();
  return JSON.stringify({
    bodyChars: text.length,
    firstText: text.slice(0, 160).replace(/\\s+/g, ' '),
    gsapLoaded: typeof window.gsap,
    alpineLoaded: typeof window.Alpine,
    appVisible: !!document.querySelector('#app'),
    xcloakLeft: document.querySelectorAll('[x-cloak]').length,
    interLoaded: document.fonts ? document.fonts.check('600 16px Inter') : null,
  });
})()`);
report.states.push({
  state: 'offline (CDN blocked)',
  firstReadableMs,
  budgetMs: 3000,
  withinBudget: firstReadableMs !== null && firstReadableMs <= 3000,
  screenshot: await shot('02-offline-blocked.png'),
  metrics: JSON.parse(offline),
});

// ── state 3: reduced-motion emulation, pin button flash behaviour ─────────
await send('Network.setBlockedURLs', { urls: [] }, sessionId);
await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] }, sessionId);
await send('Page.reload', {}, sessionId);
await sleep(4000);
const rm = await evalJs(`(() => {
  window.__rmMatches = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  return JSON.stringify({ reducedMotion: window.__rmMatches, gsap: typeof window.gsap });
})()`);
report.states.push({
  state: 'reduced-motion: reduce',
  screenshot: await shot('03-reduced-motion.png'),
  metrics: JSON.parse(rm),
});

writeFileSync(join(OUT, 'offline-probe.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
cleanup();
process.exit(0);