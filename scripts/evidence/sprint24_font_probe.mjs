/* scripts/evidence/sprint24_font_probe.mjs — real behavioural check for the @font-face
 * weight descriptor (P1-1), run under headless Chrome via CDP.
 *
 * Why this exists: a static regex over main.css cannot tell whether the
 * browser actually renders `font-weight: 600` in Inter. The shipped Inter
 * files are variable fonts (wght 100-900, verified with fontTools), so a
 * single-value `font-weight: 400` descriptor would leave ASCII 500/600/700
 * to synthetic bolding or to the system fallback. Only the browser knows
 * which one happened — `CSS.getPlatformFontsForNode` reports the actual
 * rendered font, so that is what we assert on.
 *
 * Run:  node scripts/evidence/sprint24_font_probe.mjs <base-url>
 * Exit: 0 = all weights rendered in Inter, 1 = at least one weight is not.
 */
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const BASE = process.argv[2] || 'http://127.0.0.1:8902';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9333;
const profile = mkdtempSync(join(tmpdir(), 'sprint24-font-'));

const chrome = spawn(CHROME, [
  '--headless=new',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check',
  '--disable-gpu', '--hide-scrollbars',
  '--window-size=1200,900',
  'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function wsUrl() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      const j = await r.json();
      if (j.webSocketDebuggerUrl) return j.webSocketDebuggerUrl;
    } catch { /* not up yet */ }
    await sleep(250);
  }
  throw new Error('chrome devtools endpoint never came up');
}

function cdp(ws) {
  let id = 0;
  const pending = new Map();
  ws.addEventListener('message', (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
    }
  });
  return (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const mid = ++id;
    pending.set(mid, { resolve, reject });
    ws.send(JSON.stringify({ id: mid, method, params, sessionId }));
  });
}

const cleanup = () => {
  try { chrome.kill('SIGKILL'); } catch { /* already gone */ }
  // Chrome keeps writing to its profile for a moment after SIGKILL, so a
  // single rmSync races and throws ENOTEMPTY. Retry briefly instead.
  for (let i = 0; i < 10; i++) {
    try { rmSync(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 }); return; }
    catch { /* still being written; retry */ }
  }
};
process.on('exit', cleanup);

const wsUrl_ = await wsUrl();
const ws = new WebSocket(wsUrl_);
await new Promise((r, j) => { ws.addEventListener('open', r); ws.addEventListener('error', j); });
const send = cdp(ws);

const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
await send('Page.enable', {}, sessionId);
await send('DOM.enable', {}, sessionId);
await send('CSS.enable', {}, sessionId);

await send('Page.navigate', { url: `${BASE}/` }, sessionId);
await sleep(3500); // let @font-face load + first paint

const { root } = await send('DOM.getDocument', { depth: -1 }, sessionId);

const PROBE_HTML = `
<section id="w400" style="font-family:'Inter',system-ui,sans-serif;font-weight:400;font-size:34px">Hamburgefons 0123</section>
<section id="w500" style="font-family:'Inter',system-ui,sans-serif;font-weight:500;font-size:34px">Hamburgefons 0123</section>
<section id="w600" style="font-family:'Inter',system-ui,sans-serif;font-weight:600;font-size:34px">Hamburgefons 0123</section>
<section id="w700" style="font-family:'Inter',system-ui,sans-serif;font-weight:700;font-size:34px">Hamburgefons 0123</section>
<section id="w600cjk" style="font-family:'Inter',system-ui,sans-serif;font-weight:600;font-size:34px">中文标题测试</section>
`;
await send('Runtime.evaluate', {
  expression: `document.body.insertAdjacentHTML('beforeend', ${JSON.stringify(PROBE_HTML)})`,
}, sessionId);
await sleep(1800); // fonts must settle before we ask what actually rendered

const WEIGHTS = ['400', '500', '600', '700'];
const results = {};
const details = [];

for (const w of WEIGHTS) {
  const { nodeId } = await send('DOM.querySelector', { nodeId: root.nodeId, selector: `#w${w}` }, sessionId);
  if (!nodeId) { results[w] = { ok: false, reason: 'node not found' }; continue; }
  try {
    const fonts = await send('CSS.getPlatformFontsForNode', { nodeId }, sessionId);
    const fams = fonts.fonts.map((f) => ({
      familyName: f.familyName,
      glyphCount: f.glyphCount,
      isCustomFont: f.isCustomFont,
    }));
    // The weight that actually rendered any Latin glyph must be Inter.
    const latin = fams.find((f) => f.glyphCount > 0 && /inter/i.test(f.familyName));
    const painted = fams.filter((f) => f.glyphCount > 0);
    results[w] = {
      ok: !!latin,
      rendered: painted.map((p) => p.familyName),
      interGlyphs: latin ? latin.glyphCount : 0,
      syntheticHint: painted.some((p) => !/inter/i.test(p.familyName)),
    };
  } catch (e) {
    results[w] = { ok: false, reason: String(e).slice(0, 160) };
  }
}

// CJK must NOT claim to be Inter (it is not in the shipped subsets).
let cjk = { ok: true, rendered: [], note: 'not probed' };
try {
  const { nodeId } = await send('DOM.querySelector', { nodeId: root.nodeId, selector: '#w600cjk' }, sessionId);
  const fonts = await send('CSS.getPlatformFontsForNode', { nodeId }, sessionId);
  cjk = {
    ok: !fonts.fonts.some((f) => f.glyphCount > 0 && /inter/i.test(f.familyName)),
    rendered: fonts.fonts.filter((f) => f.glyphCount > 0).map((f) => f.familyName),
    note: 'CJK is not in the shipped subsets; it must fall back, matching pre-change behaviour',
  };
} catch (e) { cjk = { ok: false, rendered: [], note: String(e).slice(0, 120) }; }

details.push({ selector: '#w400', expectation: 'Inter (latin subset)', ...results['400'] });
details.push({ selector: '#w500', expectation: 'Inter (variable wght 500)', ...results['500'] });
details.push({ selector: '#w600', expectation: 'Inter (variable wght 600)', ...results['600'] });
details.push({ selector: '#w700', expectation: 'Inter (variable wght 700)', ...results['700'] });
details.push({ selector: '#w600cjk', expectation: 'NOT Inter (falls back)', ...cjk });

const out = { base: BASE, capturedAt: new Date().toISOString(), details };
const outPath = process.argv[3] || null;
if (outPath) writeFileSync(outPath, JSON.stringify(out, null, 2));

console.log(JSON.stringify(out, null, 2));

const failures = WEIGHTS.filter((w) => !results[w].ok);
cleanup();
process.exit(failures.length || !cjk.ok ? 1 : 0);