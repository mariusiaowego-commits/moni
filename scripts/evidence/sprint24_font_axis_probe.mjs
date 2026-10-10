/* scripts/evidence/sprint24_font_axis_probe.mjs — prove the VARIABLE AXIS is applied.
 *
 * `CSS.getPlatformFontsForNode` only reports the family name, so it cannot
 * distinguish "Inter at wght 600" from "Inter at wght 400 painted bold".
 * The discriminating signal is the glyph advance width: at wght 400 the
 * 'H' is narrower than at wght 700 for the same font-size. If the range
 * descriptor were ignored (or the file were static), every weight would
 * measure identically and the probe fails.
 *
 * Run: node scripts/evidence/sprint24_font_axis_probe.mjs <base-url> [out-json]
 */
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const BASE = process.argv[2] || 'http://127.0.0.1:8903';
const OUTJSON = process.argv[3] || null;
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9335;
const profile = mkdtempSync(join(tmpdir(), 'sprint24-axis-'));

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
await sleep(4000);

const evaluate = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true }, sessionId);
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 300));
  return r.result?.value;
};

// Wait for the face to be usable before measuring.
await evaluate(`document.fonts.ready.then(() => true)`);

const SAMPLE = 'HHHHHHHHHHHHHHHHHHHH';
const measure = await evaluate(`(() => {
  const cv = document.createElement('canvas');
  const ctx = cv.getContext('2d');
  const SIZE = 200;
  const out = {};
  for (const w of [400, 500, 600, 700]) {
    ctx.font = w + ' ' + SIZE + "px Inter";
    const m = ctx.measureText(${JSON.stringify(SAMPLE)});
    out[w] = { width: m.width, actualFont: ctx.font };
  }
  // Same measurement in the fallback stack, for reference.
  for (const w of [400, 700]) {
    ctx.font = w + ' ' + SIZE + 'px ' + "system-ui";
    out['fallback' + w] = { width: ctx.measureText(${JSON.stringify(SAMPLE)}).width };
  }
  return JSON.stringify(out);
})()`);

const m = JSON.parse(measure);
const widths = { 400: m[400].width, 500: m[500].width, 600: m[600].width, 700: m[700].width };

// A variable axis that is actually applied must produce *distinct* advance
// widths that move monotonically with the requested weight.
//
// Do NOT gate this on a width ratio: for a stem-only letter like 'H',
// Inter's wght 400 -> 700 widens the advance by well under 1%. A ratio
// threshold tuned for a wide glyph (O, W) would report a false negative on
// a perfectly correct variable font. The discriminating signal is
// "four distinct, monotonically increasing widths" — a static 400 file, or
// an ignored range descriptor, yields four identical values.
const ordered = [400, 500, 600, 700].map((w) => widths[w]);
const distinct = new Set(ordered).size;
const monotonic = ordered.every((v, i) => i === 0 || v >= ordered[i - 1]);
const strictlyIncreasing = ordered.every((v, i) => i === 0 || v > ordered[i - 1]);
const w400 = widths[400];
const w700 = widths[700];
const ratio = w400 ? w700 / w400 : 0;

const report = {
  base: BASE,
  capturedAt: new Date().toISOString(),
  sample: SAMPLE,
  fontSizePx: 200,
  widthsPx: widths,
  fallbackWidthsPx: { 400: m.fallback400.width, 700: m.fallback700.width },
  distinctWidths: distinct,
  monotonic,
  strictlyIncreasing,
  ratio700over400: Number(ratio.toFixed(4)),
  interDiffersFromFallback:
    Math.abs(widths[400] - m.fallback400.width) > 0.5,
  variableAxisApplied: distinct === 4 && strictlyIncreasing,
  verdict:
    distinct === 4 && strictlyIncreasing
      ? 'variable wght axis IS applied (4 distinct monotonically increasing widths)'
      : 'widths not distinct -> axis NOT applied (static file or ignored descriptor)',
};

if (OUTJSON) writeFileSync(OUTJSON, JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));

cleanup();
process.exit(report.variableAxisApplied ? 0 : 1);