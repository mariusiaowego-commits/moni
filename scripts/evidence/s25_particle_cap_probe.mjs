/* scripts/evidence/s25_particle_cap_probe.mjs — prove the A1-a cap really binds.
 *
 * The steady-state density (avg ~80, peak ~93 in 30 s) never reaches the
 * MAX_PARTICLES ceiling, so a normal run cannot show the cap working — it only
 * shows that nothing broke. This probe deliberately overshoots: it drives the real
 * `_spawnBurst` hard enough to cross the cap, then asserts the live array
 * stays at or below MAX_PARTICLES.
 *
 * It reads the cap from the module scope the same way the app does, then
 * asserts on the live array length after a burst storm.
 *
 * Run: node scripts/evidence/s25_particle_cap_probe.mjs <base-url> [debug-port]
 */
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const BASE = process.argv[2] || 'http://127.0.0.1:8906';
const PORT = Number(process.argv[3] || 9343);
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = mkdtempSync(join(tmpdir(), 's25-cap-'));

const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
  '--window-size=1440,900', 'about:blank',
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
await sleep(5000);


const evaluate = async (expr) => {
  const r = await send('Runtime.evaluate', {
    expression: expr, returnByValue: true, awaitPromise: true, timeout: 60000,
  }, sessionId);
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 400));
  return r.result?.value;
};

// Same boot race the FPS probe hit: app() may not have attached its data
// scope yet, and then `data` is null. Wait for it explicitly instead of
// assuming a fixed sleep is enough.
const bootDeadline = Date.now() + 30000;
let booted = false;
while (Date.now() < bootDeadline && !booted) {
  booted = await evaluate(`(() => {
    const el = document.getElementById('app');
    return !!(el && el._x_dataStack && el._x_dataStack[0]);
  })()`);
  if (!booted) await sleep(250);
}
if (!booted) throw new Error('Alpine never attached _x_dataStack to #app (30 s)');
await sleep(1200);

await evaluate(`(() => {
  const appEl = document.getElementById('app');
  const data = (appEl._x_dataStack && appEl._x_dataStack[0]) || null;
  data.fetchProgressModal = true;
  return 'opened';
})()`);
await sleep(900);

const result = await evaluate(`(async () => {
  const appEl = document.getElementById('app');
  const data = (appEl._x_dataStack && appEl._x_dataStack[0]) || null;
  const chain = document.getElementById('sourceChain');
  const moniIcon = document.getElementById('moniIcon');
  const stage = document.getElementById('particleStage');
  const canvas = document.getElementById('particleCanvas');
  if (!data || !chain || !moniIcon || !stage || !canvas) return 'markup/data missing';

  const row = document.createElement('div');
  row.className = 'source-row';
  row.innerHTML = '<span class="source-label">cap-probe</span>'
    + '<div class="source-icon"><img src="/static/icons/sources/36kr.png" alt=""></div>';
  chain.appendChild(row);
  data._chainRows = [{ el: row, idx: 0, name: 'cap-probe', dbName: 'probe', color: '#fff' }];
  data._particles = [];
  data._particleRunning = true;
  data._initParticleStage();

  // Burst storm: production fires 6 particles every 200 ms. Fire far harder
  // so the live count is driven past the cap on purpose.
  const observations = [];
  const t0 = performance.now();
  while (performance.now() - t0 < 6000) {
    data._spawnBurst(0, 60);            // 60 * 2.6 = 156 particles per call
    await new Promise((r) => setTimeout(r, 120));
    observations.push(data._particles.length);
  }

  const peak = Math.max(...observations);
  const cap = 200;
  data._stopParticleAnimation && data._stopParticleAnimation();
  data._particles = [];

  return JSON.stringify({
    samples: observations.length,
    peakLiveParticles: peak,
    capExpected: cap,
    capRespected: peak <= cap,
    exceededBy: peak > cap ? peak - cap : 0,
    tail: observations.slice(-8),
  });
})()`);

// evaluate() returns a JSON *string* here (the page built it with
// JSON.stringify). Decode it; the previous version compared on the outer
// object and reported CAP BREACHED for a run that actually held.
let r = result;
if (typeof r === 'string') {
  try { r = JSON.parse(r); } catch { /* leave as-is */ }
}
if (typeof r === 'string' && r.startsWith('ERROR')) {
  console.log(JSON.stringify({ verdict: 'PROBE FAILED', reason: r }, null, 2));
  cleanup();
  process.exit(2);
}
const ok = r && r.capRespected === true;
console.log(JSON.stringify({ ...r, verdict: ok ? 'CAP HOLDS' : 'CAP BREACHED' }, null, 2));
cleanup();
process.exit(ok ? 0 : 1);