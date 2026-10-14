/* scripts/evidence/s25_fps_probe.mjs — AC-1 / D-4 FPS baseline + after.
 *
 * Measures the REAL fetch-modal particle field (#particleCanvas), not a
 * synthetic loop: it drives the actual `app()` Alpine component through its
 * own `_initParticleStage` / `_spawnBurst` / `_animateParticles` path.
 *
 * Reports average / P95 / min FPS and dropped-frame count over a continuous
 * sampling window (default 10s), plus the peak live particle count.
 *
 * Requires a dev server on <base>. Chrome debug port is 9342 (9333 is taken
 * by another sprint).
 *
 * Run: node scripts/evidence/s25_fps_probe.mjs <base-url> <seconds> [out.json] [debug-port]
 */
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const BASE = process.argv[2] || 'http://127.0.0.1:8906';
const SECONDS = Number(process.argv[3] || 10);
const OUTJSON = process.argv[4] || null;
const PORT = Number(process.argv[5] || 9342);
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = mkdtempSync(join(tmpdir(), 's25-fps-'));

const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
  // warden 5.4: pin the environment so numbers are reproducible.
  // Without --force-device-scale-factor the DPR depends on the host screen
  // (headless dpr=1 vs dad's Retina dpr=2 => 4x the pixels => 4x the fill
  // cost), so an absolute ms figure would not survive a machine change.
  // --disable-gpu isolates the JS + Canvas2D work from GPU differences.
  '--force-device-scale-factor=1',
  '--disable-gpu',
  '--window-size=1440,900', '--autoplay-policy=no-user-gesture-required',
  'about:blank',
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

const evaluate = async (expr) => {
  const r = await send('Runtime.evaluate', {
    expression: expr, returnByValue: true, awaitPromise: true, timeout: 60000,
  }, sessionId);
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 400));
  return r.result?.value;
};

// Wait for Alpine to attach its data scope instead of sleeping a fixed
// amount: a fixed wait raced app() and produced "no Alpine data on #app"
// runs whose numbers would have been meaningless. Must come AFTER the
// evaluate() definition (TDZ) — an earlier attempt put it above and threw
// ReferenceError before any measurement happened.
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
await sleep(1200); // let init() settle

// Sanity: the real particle stage must exist and the real component must be up.
const pre = await evaluate(`(() => {
  // The particle stage lives inside the fetch modal, which is x-show bound
  // to fetchProgressModal. While closed the stage rect is 0x0 and the canvas
  // keeps its default 300x150 — measuring then would sample nothing real.
  // Open it through the component's own state, the same way clicking
  // 立即抓取 does, and wait a frame so layout settles.
  const appEl = document.getElementById('app');
  // Alpine is bundled as an ES module, so window.Alpine is NOT exposed and
  // Alpine.$data() is unreachable from the page context. Alpine instead
  // hangs the component proxy off the element as _x_dataStack.
  const data = (appEl && appEl._x_dataStack && appEl._x_dataStack[0]) || null;
  if (!data) return 'no _x_dataStack on #app';
  data.fetchProgressModal = true;
  return 'opened';
})()`);

await sleep(900); // let x-show un-hide + a couple of rAF ticks

const pre2 = await evaluate(`(() => {
  const stage = document.getElementById('particleStage');
  const canvas = document.getElementById('particleCanvas');
  const r = stage.getBoundingClientRect();
  const appEl = document.getElementById('app');
  const data = (appEl && appEl._x_dataStack && appEl._x_dataStack[0]) || null;
  // Report the modal's REAL visibility instead of a proxy query: several
  // elements share the .modal-backdrop class, so querySelector picks an
  // unrelated one. Measure the fetch modal by its own child marker.
  const fetchModal = document.querySelector('.modal-backdrop .fetch-modal');
  const backdrop = fetchModal ? fetchModal.closest('.modal-backdrop') : null;
  return JSON.stringify({
    stateFlag: data ? data.fetchProgressModal : null,
    fetchModalFound: !!fetchModal,
    backdropDisplay: backdrop ? getComputedStyle(backdrop).display : null,
    modalVisible: !!(backdrop && getComputedStyle(backdrop).display !== 'none'),
    canvasSizeBeforeInit: canvas ? [canvas.width, canvas.height] : null,
    stageRect: [Math.round(r.width), Math.round(r.height)],
  });
})()`);

// Sample rAF for SECONDS while the REAL particle loop runs.
//
// The component instance is reached through the DOM node Alpine attached the
// x-data scope to (#app). We drive its own _spawnBurst so the measurement
// covers the production code path end to end.
const sample = await evaluate(`(async () => {
  const SECONDS = ${SECONDS};
  // 20ms, not 16.7ms: an ordinary 60Hz frame lands at ~16.67ms, so a 16.7
  // criterion counts healthy frames as long frames (warden P2-3).
  const LONG_FRAME_MS = 20;
  const appEl = document.getElementById('app');
  if (!appEl) return JSON.stringify({ error: 'no #app' });
  const data = (appEl._x_dataStack && appEl._x_dataStack[0]) || null;
  if (!data) return JSON.stringify({ error: 'no Alpine data on #app' });

  // Make sure the particle stage is initialised with a source row, exactly
  // like clicking 立即抓取 does, then keep bursting like the real interval.
  const stage = document.getElementById('particleStage');
  const chain = document.getElementById('sourceChain');
  const moniIcon = document.getElementById('moniIcon');
  const canvas = document.getElementById('particleCanvas');
  if (!stage || !chain || !moniIcon || !canvas) {
    return JSON.stringify({ error: 'particle stage markup absent' });
  }
  const row = document.createElement('div');
  row.className = 'source-row';
  row.innerHTML = '<span class="source-label">probe</span>'
    + '<div class="source-icon"><img src="/static/icons/sources/36kr.png" alt=""></div>';
  chain.appendChild(row);
  data._chainRows = [{ el: row, idx: 0, name: 'probe', dbName: 'probe', color: '#fff' }];
  data._particles = [];
  data._particleRunning = true;
  data._initParticleStage();

  // Mirror the production burst cadence (setInterval 200ms in _initParticleStage)
  const burstTimer = setInterval(() => { data._spawnBurst(0, 6); }, 200);

  // FPS ALONE CANNOT SHOW A REGRESSION HERE.
  //
  // rAF is vsync-locked: if the per-frame JS+canvas work fits inside the
  // 16.7 ms budget, the browser simply waits for the next vsync and reports
  // a clean 60 either way. Measured on this machine the baseline sits at
  // avg 59.9 / p95 59.88 / min 59.5 with zero dropped frames — there is no
  // headroom signal to detect a regression in.
  //
  // The discriminating metric is therefore the WORK DONE PER FRAME: time the
  // particle update+draw block directly. That number has headroom (a frame
  // budget of 16.7 ms) and moves as soon as particle count or per-particle
  // draw calls change — which is exactly what A1-a changes.
  //
  // FPS numbers are still reported (dad asked for them, and they prove the
  // loop keeps running), but they are NOT the gate.
  const frames = [];
  const workSamples = [];
  const liveCounts = [];
  let peak = 0;
  const realAnimate = data._animateParticles.bind(data);

  // Wrap the production loop to time only its update+draw section.
  data._animateParticles = function timed() {
    const w0 = performance.now();
    realAnimate();
    workSamples.push(performance.now() - w0);
    const n = data._particles ? data._particles.length : 0;
    if (n > peak) peak = n;
  };
  data._animFrame = requestAnimationFrame(() => data._animateParticles());

  const t0 = performance.now();
  await new Promise((resolve) => {
    let last = t0;
    const tick = (now) => {
      frames.push(now - last);
      last = now;
      if (data._particles) liveCounts.push(data._particles.length);
      if (now - t0 >= SECONDS * 1000) return resolve();
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });

  clearInterval(burstTimer);
  data._stopParticleAnimation && data._stopParticleAnimation();
  data._animateParticles = realAnimate;
  data._particles = [];

  const sorted = frames.slice().sort((a, b) => a - b);
  const fpsOf = (ms) => (ms > 0 ? 1000 / ms : 0);
  const frameTimes = frames;
  const fpsList = frameTimes.map(fpsOf).filter((f) => isFinite(f));
  const sortedFps = fpsList.slice().sort((a, b) => a - b);
  const pct = (arr, p) => arr[Math.min(arr.length - 1, Math.max(0, Math.floor(arr.length * p)))];
  const wSorted = workSamples.slice().sort((a, b) => a - b);
  const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;

  // Per-particle draw cost, normalised by live count: this is what actually
  // decides whether the frame fits the budget, and it is independent of how
  // many particles happen to be alive.
  const avgLive = liveCounts.length ? mean(liveCounts) : 0;

  return JSON.stringify({
    secondsSampled: SECONDS,
    frameCount: frames.length,
    totalFrames: frames.length,
    averageFps: Number((fpsList.reduce((a, b) => a + b, 0) / fpsList.length).toFixed(2)),
    p95Fps: Number(pct(sortedFps, 0.05).toFixed(2)),
    minFps: Number(sortedFps[0].toFixed(2)),
    medianFps: Number(pct(sortedFps, 0.5).toFixed(2)),
    droppedFrames: frames.filter((f) => f > 33.4).length,
    longFrames: frames.filter((f) => f > LONG_FRAME_MS).length,
    longFrameCriterionMs: LONG_FRAME_MS,
    longFrameRatioPct: Number(((frames.filter((f) => f > LONG_FRAME_MS).length / frames.length) * 100).toFixed(3)),
    overBudgetFrames: frames.filter((f) => f > LONG_FRAME_MS).length,
    overBudgetRatio: Number((frames.filter((f) => f > LONG_FRAME_MS).length / frames.length).toFixed(5)),
    maxFrameMs: Number(Math.max(...frames).toFixed(2)),
    p95FrameMs: Number(pct(sorted, 0.95).toFixed(2)),
    peakParticles: peak,
    avgLiveParticles: Number(avgLive.toFixed(1)),
    particleCap: (typeof data.MAX_PARTICLES !== 'undefined') ? data.MAX_PARTICLES : null,

    // --- the metric that actually discriminates ---
    workPerFrameMs: {
      mean: workSamples.length ? Number(mean(workSamples).toFixed(3)) : null,
      p95: wSorted.length ? Number(pct(wSorted, 0.95).toFixed(3)) : null,
      max: wSorted.length ? Number(Math.max(...workSamples).toFixed(3)) : null,
      samples: workSamples.length,
    },
    budgetMs: 16.7,
    budgetUsagePct: wSorted.length && pct(wSorted, 0.95) !== null
      ? Number(((pct(wSorted, 0.95) / 16.7) * 100).toFixed(1))
      : null,
  });
})()`);

const preObj = JSON.parse(pre2);
const s = JSON.parse(sample);

const env = await evaluate(`(() => ({
  devicePixelRatio: window.devicePixelRatio,
  hardwareConcurrency: navigator.hardwareConcurrency,
}))()`);

const report = {
  base: BASE,
  capturedAt: new Date().toISOString(),
  chromeDebugPort: PORT,
  env,
  stage: preObj,
  note: 'headless rAF; use for before/after RELATIVE comparison (warden R1)',
  ...s,
};

if (OUTJSON) writeFileSync(OUTJSON, JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
cleanup();
process.exit(0);