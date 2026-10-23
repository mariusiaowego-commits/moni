/* Real-mouse click A/B for the single-file showcase artifact link.
 *
 * Usage:
 *   node scripts/evidence/pr147_showcase_click_probe.mjs <showcase.html>
 *
 * The probe opens the same file twice in headless Chrome:
 *   1. file://  — the offline delivery mode
 *   2. http://  — a control origin serving the same bytes
 *
 * In both modes it seeds the share queue with a normal artifact URL and a
 * javascript: URL, then uses CDP Input.dispatchMouseEvent at the link's text
 * rect. A pass means the normal artifact click opens the demo modal, while
 * the javascript: row remains blocked, has no javascript: href, and cannot
 * open the modal.
 */
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { extname, join, resolve } from 'node:path';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const showcasePath = resolve(process.argv[2] || '');
if (!process.argv[2]) {
  console.error('usage: node scripts/evidence/pr147_showcase_click_probe.mjs <showcase.html>');
  process.exit(2);
}

const showcaseHtml = readFileSync(showcasePath);
const profile = mkdtempSync(join(tmpdir(), 'moni-showcase-click-'));
const port = 9400 + Math.floor(Math.random() * 400);
const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
  '--disable-gpu', '--hide-scrollbars', '--window-size=1440,1200',
  '--allow-file-access-from-files', 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let server;
let ws;
const cleanup = () => {
  try { ws?.close(); } catch { /* gone */ }
  try { server?.close(); } catch { /* gone */ }
  try { chrome.kill('SIGKILL'); } catch { /* gone */ }
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* busy */ }
};
process.on('exit', cleanup);

async function devtoolsEndpoint() {
  for (let i = 0; i < 100; i++) {
    try {
      const info = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
      if (info.webSocketDebuggerUrl) return info.webSocketDebuggerUrl;
    } catch { /* not ready */ }
    await sleep(200);
  }
  throw new Error('Chrome devtools endpoint never came up');
}

ws = new WebSocket(await devtoolsEndpoint());
await new Promise((res, rej) => {
  ws.addEventListener('open', res);
  ws.addEventListener('error', rej);
});

let seq = 0;
const pending = new Map();
ws.addEventListener('message', (event) => {
  const msg = JSON.parse(event.data);
  if (!msg.id || !pending.has(msg.id)) return;
  const { resolve: done, reject } = pending.get(msg.id);
  pending.delete(msg.id);
  msg.error ? reject(new Error(JSON.stringify(msg.error))) : done(msg.result);
});
const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
  const id = ++seq;
  pending.set(id, { resolve, reject });
  ws.send(JSON.stringify({ id, method, params, sessionId }));
});

function startHttpServer() {
  return new Promise((resolveServer) => {
    server = createServer((req, res) => {
      if (req.url === '/' || req.url === '/showcase.html') {
        res.writeHead(200, {
          'content-type': 'text/html; charset=utf-8',
          'cache-control': 'no-store',
        });
        res.end(showcaseHtml);
        return;
      }
      res.writeHead(404);
      res.end('not found');
    });
    server.listen(0, '127.0.0.1', () => {
      resolveServer(`http://127.0.0.1:${server.address().port}/showcase.html`);
    });
  });
}

async function openPage(url) {
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
  await send('Page.enable', {}, sessionId);
  await send('Runtime.enable', {}, sessionId);
  await send('Page.navigate', { url }, sessionId);
  await sleep(5500);

  const evaluate = async (expression) => {
    const out = await send('Runtime.evaluate', {
      expression, returnByValue: true, awaitPromise: true,
    }, sessionId);
    if (out.exceptionDetails) throw new Error(JSON.stringify(out.exceptionDetails));
    return out.result?.value;
  };

  const seed = await evaluate(`(() => {
    const rows = [
      {id:'safe', output_url:'/artifacts/demo-safe-artifact.html', output_path:'/tmp/demo-safe-artifact.html',
       status:'done', created_at:'2026-10-24T10:00:00', progress_pct:100, article_count:4, format:'html_landing'},
    ];
    for (const root of document.querySelectorAll('[x-data]')) {
      try {
        const data = Alpine.$data(root);
        if (data && 'shareQueue' in data) {
          data.shareQueue = rows;
          data.currentView = 'share';
          return true;
        }
      } catch { /* not an Alpine root */ }
    }
    return false;
  })()`);
  if (!seed) throw new Error('shareQueue Alpine root not found');
  await sleep(1800);

  // Independent danger probe: same bindings the production row uses, but with
  // a javascript: URL. If the guard regresses, the real click opens the modal.
  await evaluate(`(() => {
    const host = document.createElement('div');
    host.id = 'probe-danger-host';
    host.setAttribute('x-data', '{}');
    host.innerHTML = '<a id="probe-danger" class="link"'
      + ' :class="{\\'link-blocked\\': isBlockedUrl(\\'javascript:alert(1)\\')}"'
      + ' :href="safeHref(\\'javascript:alert(1)\\')"'
      + ' @click="window.__showcaseShow(\\'artifact\\')">probe-danger-artifact.html</a>';
    document.body.appendChild(host);
    Alpine.initTree(host);
  })()`);
  await sleep(500);

  const api = await evaluate(`JSON.stringify({
    origin: location.origin,
    safeHrefJs: window.safeHref('javascript:alert(1)'),
    blockedJs: window.isBlockedUrl('javascript:alert(1)'),
    safeHrefArtifact: window.safeHref('/artifacts/x.html'),
  })`).then(JSON.parse);

  const info = async (label) => await evaluate(`(() => {
    const anchor = [...document.querySelectorAll('a')].find(
      (a) => (a.textContent || '').trim() === ${JSON.stringify(label)}
    );
    if (!anchor) return {found:false};
    anchor.scrollIntoView({block:'center'});
    const range = document.createRange();
    range.selectNodeContents(anchor);
    const rect = [...range.getClientRects()].find((r) => r.width > 4 && r.height > 4)
      || range.getBoundingClientRect();
    const x = rect.x + rect.width / 2;
    const y = rect.y + rect.height / 2;
    const style = getComputedStyle(anchor);
    const hit = document.elementFromPoint(x, y);
    return {
      found:true, x, y, href:anchor.getAttribute('href'), cls:anchor.className,
      pe:style.pointerEvents, td:style.textDecorationLine,
      hitIsAnchor:hit === anchor, hitTag:hit ? hit.tagName : null,
    };
  })()`);

  const click = async (point) => {
    await send('Input.dispatchMouseEvent', {
      type:'mouseMoved', x:point.x, y:point.y, button:'none', clickCount:0,
    }, sessionId);
    await sleep(120);
    await send('Input.dispatchMouseEvent', {
      type:'mousePressed', x:point.x, y:point.y, button:'left', clickCount:1,
    }, sessionId);
    await sleep(80);
    await send('Input.dispatchMouseEvent', {
      type:'mouseReleased', x:point.x, y:point.y, button:'left', clickCount:1,
    }, sessionId);
    await sleep(900);
  };

  const modal = async () => JSON.parse(await evaluate(`JSON.stringify({
    display: document.getElementById('showcaseDemo')?.style.display ?? null,
    text: document.getElementById('showcaseDemoText')?.textContent || '',
  })`));
  const hide = () => evaluate(`window.__showcaseHide && window.__showcaseHide()`);

  const safePoint = await info('demo-safe-artifact.html');
  const beforeSafe = await modal();
  if (safePoint.found) await click(safePoint);
  const afterSafe = await modal();
  await hide();

  const dangerPoint = await info('probe-danger-artifact.html');
  await hide();
  const beforeDanger = await modal();
  if (dangerPoint.found) await click(dangerPoint);
  const afterDanger = await modal();

  return { api, safePoint, beforeSafe, afterSafe, dangerPoint, beforeDanger, afterDanger };
}

const checks = [];
const check = (name, ok, detail = '') => {
  checks.push({ name, ok: !!ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
};

try {
  const httpUrl = await startHttpServer();
  const fileUrl = `file://${showcasePath}`;
  for (const [label, url] of [['file', fileUrl], ['http', httpUrl]]) {
    console.log(`\n## ${label}  ${url}`);
    const r = await openPage(url);
    check(`${label}: safe artifact link is not blocked`,
      r.safePoint.found && !/\blink-blocked\b/.test(r.safePoint.cls) && r.safePoint.pe !== 'none',
      JSON.stringify({ cls:r.safePoint.cls, pe:r.safePoint.pe }));
    check(`${label}: real mouse hit lands on the anchor`,
      r.safePoint.hitIsAnchor === true,
      `hit=${r.safePoint.hitTag}`);
    check(`${label}: artifact click opens the demo modal`,
      r.afterSafe.display === 'flex' && /演示页/.test(r.afterSafe.text),
      JSON.stringify(r.afterSafe));
    check(`${label}: javascript: has no safe href`,
      r.api.safeHrefJs === null && r.api.blockedJs === true,
      JSON.stringify({ safeHref:r.api.safeHrefJs, blocked:r.api.blockedJs }));
    check(`${label}: javascript: row stays blocked and unclickable`,
      r.dangerPoint.found
        && /\blink-blocked\b/.test(r.dangerPoint.cls)
        && r.dangerPoint.pe === 'none'
        && !String(r.dangerPoint.href || '').toLowerCase().startsWith('javascript:')
        && r.afterDanger.display !== 'flex',
      JSON.stringify({
        found:r.dangerPoint.found, cls:r.dangerPoint.cls, pe:r.dangerPoint.pe,
        href:r.dangerPoint.href,
        modal:r.afterDanger.display,
      }));
    if (label === 'file') {
      check('file: demo origin keeps /artifacts links same-origin',
        r.api.safeHrefArtifact === '/artifacts/x.html',
        JSON.stringify({ origin:r.api.origin, safeHref:r.api.safeHrefArtifact }));
    }
  }
} catch (error) {
  console.error('PROBE ERROR:', error.stack || error);
  checks.push({ name:'probe completed', ok:false });
} finally {
  cleanup();
}

const failed = checks.filter((c) => !c.ok);
console.log(`\n${checks.length - failed.length}/${checks.length} checks passed`);
process.exit(failed.length ? 1 : 0);
