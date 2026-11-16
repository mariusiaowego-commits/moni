// Node harness: load moni's real app.js (Alpine component) and exercise the
// sprint-38 P0/P1 code paths **behaviourally** — not by string-matching the
// source. This is what actually catches "first click on homepage 100% errors".
//
// Why node and not pytest-only: the bug is in browser JS. A static grep
// assertion ("the file contains 'src-xbot-feed'") proves nothing about
// behaviour; calling the real method with a fresh-homepage state does.
//
// Usage: node harness.js <scenario>
//   scenarios: p0-source-id, p1-focus, all
const fs = require('fs');
const path = require('path');

const JS_DIR = '/Users/mt16/dev/moni-worktrees/xbot-author-block-v1.0.75/moni/web/static/js';

function stubEnv() {
  const noop = () => {};
  const el = () => ({
    style: {}, classList: { add: noop, remove: noop, contains: () => false, toggle: noop },
    appendChild: noop, removeChild: noop, addEventListener: noop,
    removeEventListener: noop, querySelector: () => null, querySelectorAll: () => [],
    setAttribute: noop, getAttribute: () => null, focus: noop, click: noop,
    getBoundingClientRect: () => ({ width: 100, height: 20, top: 0, left: 0 }),
    offsetWidth: 100, offsetHeight: 20, dataset: {}, innerHTML: '', textContent: '',
    parentNode: null, contains: () => false, dispatchEvent: noop, remove: noop,
  });
  global.document = {
    addEventListener: noop, removeEventListener: noop, querySelector: () => null,
    querySelectorAll: () => [], body: el(), documentElement: el(),
    getElementById: () => null, createElement: el, createTextNode: () => ({}),
    fonts: { ready: Promise.resolve(), load: () => Promise.resolve() },
  };
  global.window = {
    addEventListener: noop, removeEventListener: noop, matchMedia: () => ({
      matches: false, addEventListener: noop, removeEventListener: noop,
      addListener: noop, removeListener: noop,
    }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
    location: { href: 'http://localhost/', pathname: '/', search: '' },
    history: { pushState: noop, replaceState: noop },
    requestAnimationFrame: (cb) => setTimeout(cb, 0),
    setTimeout, clearTimeout, setInterval, clearInterval,
    requestIdleCallback: (cb) => setTimeout(cb, 0),
  };
  global.localStorage = global.window.localStorage;
  global.requestAnimationFrame = global.window.requestAnimationFrame;
  global.matchMedia = global.window.matchMedia;
  global.getComputedStyle = global.window.getComputedStyle;
  global.fetch = async () => ({ ok: true, status: 200, json: async () => ({}), text: async () => '' });
  global.MutationObserver = class { observe() {} disconnect() {} };
  global.ResizeObserver = class { observe() {} disconnect() {} };
  global.IntersectionObserver = class { observe() {} disconnect() {} };
  global.CSS = { escape: (s) => String(s) };
}

let _ctx = null;
function loadApp() {
  stubEnv();
  const vm = require('vm');
  // The vm context is built once (scripts evaluated once — re-evaluating
  // theme.js would redeclare its consts). Each call returns a FRESH
  // component instance so scenarios don't leak state into each other.
  const theme = fs.readFileSync(path.join(JS_DIR, 'theme.js'), 'utf8');
  const appSrc = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
  // Run both files in ONE shared vm context so `themeMixin` (theme.js) is
  // visible to app.js. Separate eval() calls would clash on `const`/`function`
  // declarations across eval scopes.
  if (!_ctx) {
    _ctx = vm.createContext(global);
    // Load the same non-vendor scripts index.html loads, in the same order,
    // so app.js's mixin dependencies (themeMixin / calendarModule / ...) exist.
    const FILES = ['toast.js', 'modal.js', 'safe_url.js', 'calendar.js', 'cases.js',
                   'stats.js', 'theme.js', 'app.js'];
    for (const f of FILES) {
      const code = fs.readFileSync(path.join(JS_DIR, f), 'utf8');
      vm.runInContext(code + '\n', _ctx, { filename: f });
    }
  }
  const appFn = vm.runInContext('app', _ctx);
  return appFn();
}

const results = [];
function check(name, cond, detail) {
  results.push({ name, ok: !!cond, detail: detail === undefined ? '' : String(detail) });
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail !== undefined ? '  :: ' + detail : ''}`);
}

async function scenarioP0() {
  const comp = loadApp();
  // Fresh homepage: configSources (config tab) and fetchConfigData (fetch modal)
  // are BOTH unloaded — this is exactly the state agy reported.
  comp.configSources = [];
  comp.fetchConfigData = {};
  comp.sources = [];

  const id = comp._xbotSourceId();
  check('P0: _xbotSourceId() resolves on a fresh homepage (no config tab visited)',
    !!id, `got ${JSON.stringify(id)}`);
  check('P0: resolved id is the real xbot source id',
    id === 'src-xbot-feed', `got ${JSON.stringify(id)}`);

  // It must ALSO work when the lists ARE loaded (regression guard).
  comp.configSources = [{ id: 'src-xbot-feed', name: 'xbot_feed' }];
  check('P0: still resolves when configSources is loaded',
    comp._xbotSourceId() === 'src-xbot-feed', comp._xbotSourceId());
}

async function scenarioP1() {
  const comp = loadApp();
  // Stub the modal helper to simulate "user pressed Escape / Cancel".
  let confirmCalled = 0;
  global.moniModal = {
    confirm: async () => { confirmCalled++; return false; },  // cancelled
  };
  // A fake trigger button that records focus() calls.
  let focused = 0;
  const trigger = {
    focus() { focused++; },
    tagName: 'BUTTON', id: 'block-btn',
  };
  // Simulate: the trigger button had focus when the confirm opened.
  global.document.activeElement = trigger;

  comp.configSources = [{ id: 'src-xbot-feed', name: 'xbot_feed' }];
  comp.articleDetail = { source: 'xbot_feed', author: '阿拾 (@jiamemexyz)' };
  comp._notify = () => {};
  comp.configLoadBlockedAuthors = () => {};

  // index.html 现在是 `@click="confirmBlockAuthor($event)"` —— 传事件进来,
  // 与真实调用点一致 (P1 修复用 evt.currentTarget 定位触发按钮)。
  await comp.confirmBlockAuthor({ currentTarget: trigger });
  check('P1: confirm dialog was opened', confirmCalled === 1, `confirmCalled=${confirmCalled}`);
  check('P1: focus is restored to the trigger button after cancel/Esc',
    focused > 0, `focus() calls=${focused}`);

  // 确认 (ok=true) 时: 详情弹窗会被 closeArticleDetail() 关掉, 触发按钮
  // 随之消失 → **不该**再去 focus 一个已脱离文档的节点。
  let focused2 = 0;
  global.moniModal = { confirm: async () => true };
  comp2 = loadApp();
  comp2.configSources = [{ id: 'src-xbot-feed', name: 'xbot_feed' }];
  comp2.articleDetail = { source: 'xbot_feed', author: '阿拾 (@jiamemexyz)' };
  comp2._notify = () => {};
  comp2.configLoadBlockedAuthors = () => {};
  comp2.closeArticleDetail = () => {};
  comp2.loadAll = () => {};
  global.fetch = async () => ({ ok: true, status: 200, json: async () => ({
    status: 'ok', handle: 'jiamemexyz', deleted_articles: 2 }) });
  const trig2 = { focus() { focused2++; } };
  global.document.activeElement = trig2;
  await comp2.confirmBlockAuthor({ currentTarget: trig2 });
  check('P1: focus is NOT restored after confirming (trigger is gone)',
    focused2 === 0, `focus() calls=${focused2}`);
}

async function main() {
  const scenario = process.argv[2] || 'all';
  if (scenario === 'p0-source-id' || scenario === 'all') await scenarioP0();
  if (scenario === 'p1-focus' || scenario === 'all') await scenarioP1();
  const failed = results.filter(r => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  process.exit(failed.length ? 1 : 0);
}

main().catch(e => { console.error('HARNESS ERROR:', e && e.stack || e); process.exit(2); });
