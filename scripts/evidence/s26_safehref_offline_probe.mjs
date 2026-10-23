/* scripts/evidence/s26_safehref_offline_probe.mjs — v1.0.48 browser verification.
 *
 * Verifies in real headless Chrome (CDP):
 *  1. offline: with the CDN blocked, Alpine + GSAP still load from
 *     /vendor/ and x-cloak is fully cleared
 *  2. the href guard: Alpine's :href binding removes the attribute when
 *     safeHref() returns null (javascript: / data: / //evil.com), and
 *     keeps a real URL
 *
 * Run: node scripts/evidence/s26_safehref_offline_probe.mjs <base-url>
 *
 * The dev server must be running on that port:
 *   /usr/local/bin/python3 -c "import uvicorn; from moni.web.server import create_app; uvicorn.run(create_app(), host='127.0.0.1', port=8901, log_level='warning')"
 */
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const BASE = process.argv[2] || 'http://127.0.0.1:8901';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9345;
const profile = mkdtempSync(join(tmpdir(), 'moni-safehref-'));

const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
  '--disable-gpu', '--hide-scrollbars', '--window-size=1440,900', 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const cleanup = () => {
  try { chrome.kill('SIGKILL'); } catch { /* gone */ }
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* busy */ }
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

const evalJs = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true }, sessionId);
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result?.value;
};

const results = { base: BASE, checks: [] };
const check = (name, pass, detail) => {
  results.checks.push({ name, pass: !!pass, detail });
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
};

// ── block the CDN up front: everything must come from the local server ───
await send('Network.setBlockedURLs', {
  urls: ['*://cdn.jsdelivr.net/*', '*://fonts.googleapis.com/*', '*://fonts.gstatic.com/*'],
}, sessionId);

await send('Page.navigate', { url: `${BASE}/` }, sessionId);
await sleep(4000);

const offline = JSON.parse(await evalJs(`(() => JSON.stringify({
  alpine: typeof window.Alpine,
  gsap: typeof window.gsap,
  xcloak: document.querySelectorAll('[x-cloak]').length,
  bodyChars: (document.body.innerText || '').trim().length,
  safeHrefGlobal: typeof window.safeHref,
  isSafeUrlGlobal: typeof window.isSafeUrl,
  vendorAlpine: !!document.querySelector('script[src*="vendor/alpine"]'),
  vendorGsap: !!document.querySelector('script[src*="vendor/gsap"]'),
  cdnLinks: document.querySelectorAll('script[src^="http"], link[href^="http"]').length,
}))()`));

check('offline: Alpine loaded from local vendor/', offline.alpine === 'object', `typeof window.Alpine = ${offline.alpine}`);
check('offline: GSAP loaded from local vendor/', offline.gsap === 'object', `typeof window.gsap = ${offline.gsap}`);
check('offline: x-cloak fully cleared', offline.xcloak === 0, `${offline.xcloak} left`);
check('offline: page rendered', offline.bodyChars > 0, `${offline.bodyChars} chars of text`);
check('offline: no CDN script/link tags remain', offline.cdnLinks === 0, `${offline.cdnLinks} external tags`);
check('globals exposed: safeHref / isSafeUrl', offline.safeHrefGlobal === 'function' && offline.isSafeUrlGlobal === 'function',
  `safeHref=${offline.safeHrefGlobal} isSafeUrl=${offline.isSafeUrlGlobal}`);

// ── the href guard: Alpine must drop the attribute when safeHref -> null ──
const guard = JSON.parse(await evalJs(`(async () => {
  const host = document.createElement('div');
  host.setAttribute('x-data', '{}');
  host.innerHTML = [
    '<a id="g-js"    :href="safeHref(\\'javascript:alert(1)\\')">js</a>',
    '<a id="g-data"  :href="safeHref(\\'data:text/html,<script>1<\\/script>\\')">data</a>',
    '<a id="g-proto" :href="safeHref(\\'//evil.com/x\\')">proto</a>',
    '<a id="g-ok"    :href="safeHref(\\'https://36kr.com/p/1\\')">ok</a>',
    '<a id="g-rel"   :href="safeHref(\\'/artifacts/x.html\\')">rel</a>',
  ].join('');
  document.body.appendChild(host);
  await new Promise((r) => setTimeout(r, 400));
  const read = (id) => {
    const el = host.querySelector('#' + id);
    return { has: el.hasAttribute('href'), value: el.getAttribute('href') };
  };
  const out = {
    js: read('g-js'), data: read('g-data'), proto: read('g-proto'),
    ok: read('g-ok'), rel: read('g-rel'),
    blockedClass: !!host.querySelector('#g-js').className,
  };
  host.remove();
  return JSON.stringify(out);
})()`));

check('guard: javascript: URL has NO href attribute', !guard.js.has, `href=${JSON.stringify(guard.js.value)}`);
check('guard: data: URL has NO href attribute', !guard.data.has, `href=${JSON.stringify(guard.data.value)}`);
check('guard: protocol-relative URL has NO href attribute', !guard.proto.has, `href=${JSON.stringify(guard.proto.value)}`);
check('guard: https URL KEEPS href', guard.ok.has && guard.ok.value === 'https://36kr.com/p/1', `href=${JSON.stringify(guard.ok.value)}`);
check('guard: site-relative path KEEPS href', guard.rel.has && guard.rel.value === '/artifacts/x.html', `href=${JSON.stringify(guard.rel.value)}`);

const failed = results.checks.filter((c) => !c.pass);
console.log(`\n${results.checks.length - failed.length}/${results.checks.length} checks passed`);
cleanup();
process.exit(failed.length ? 1 : 0);
