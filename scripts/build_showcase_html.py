#!/usr/bin/env python3
"""Assemble a single-file offline high-fidelity mockup of the moni web UI."""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import ssl
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "moni" / "web" / "static"
OUT = ROOT / "docs" / "moni-web-showcase.html"
API = "http://127.0.0.1:8900"
CTX = ssl.create_default_context()


def http_get(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": "moni-showcase-builder"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read()


def api_json(path: str, default=None):
    try:
        raw = http_get(API + path, timeout=25)
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        print(f"  ! {path} → {exc}")
        return default if default is not None else {}


def b64_data_uri(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = "application/octet-stream"
        if path.suffix == ".svg":
            mime = "image/svg+xml"
        elif path.suffix == ".png":
            mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def collect_icons() -> dict[str, str]:
    icons: dict[str, str] = {}
    for p in (STATIC / "icons").rglob("*"):
        if p.is_file() and p.suffix.lower() in {".png", ".svg"}:
            rel = p.relative_to(STATIC).as_posix()  # icons/...
            uri = b64_data_uri(p)
            icons["/" + rel] = uri
            icons["/" + "static/" + rel] = uri
            icons[rel] = uri
            icons["static/" + rel] = uri
    return icons


def replace_icon_paths(text: str, icons: dict[str, str]) -> str:
    # Longest keys first so /static/icons/... wins over icons/...
    for key in sorted(icons, key=len, reverse=True):
        text = text.replace(key, icons[key])
    # leftover cache-busters after data URIs (favicon.svg?v=...)
    text = re.sub(r'(data:image/[^"\']+)\?v=[0-9]+', r'\1', text)
    return text


_TOKEN_QS = re.compile(r'(?i)([?&](?:token|poc_token|access_token|key)=)[^&\s"\']+')
_JWT = re.compile(r'eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}')


def redact_value(s: str) -> str:
    s = _TOKEN_QS.sub(r'\1SHOWCASE', s)
    s = _JWT.sub('SHOWCASE_JWT', s)
    s = s.replace('/Users/mt16/dev/moni/', '')
    return s


def redact_tree(obj):
    if isinstance(obj, str):
        return redact_value(obj)
    if isinstance(obj, list):
        return [redact_tree(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_tree(v) for k, v in obj.items()}
    return obj


def fetch_snapshot() -> dict:
    print("Capturing live API snapshot…")
    snap: dict = {}
    snap["service"] = api_json("/api/service/status", {"running": True, "pid": 8900, "uptime_sec": 3600, "port": 8900})
    snap["sources"] = api_json("/api/config/sources", [])
    snap["tags"] = api_json("/api/config/tags", [])
    snap["filters"] = api_json("/api/config/filters", [])
    snap["articles"] = api_json("/api/articles?limit=120&offset=0", [])
    if isinstance(snap["articles"], dict):
        snap["articles"] = snap["articles"].get("articles") or snap["articles"].get("items") or []

    pinned_raw = api_json("/api/articles/pinned?limit=40", {"items": []})
    snap["pinned_raw"] = pinned_raw.get("items") if isinstance(pinned_raw, dict) else (pinned_raw or [])

    snap["llm_jobs"] = api_json("/api/llm/jobs?limit=40", [])
    snap["llm_stats"] = api_json("/api/llm/jobs/stats/summary", {"pending": 0, "running": 0, "done": 0, "failed": 0, "cancelled": 0})
    snap["cron_runs"] = api_json("/api/cron/runs", [])
    snap["events_stats"] = api_json("/api/events/stats", {"total": 0, "by_type": {}})
    snap["events_pending"] = api_json("/api/events/pending?limit=50", {"items": []})
    snap["events"] = {}
    for y, m in ((2026, 6), (2026, 7), (2026, 8), (2026, 9)):
        snap["events"][f"{y}-{m:02d}"] = api_json(f"/api/events?year={y}&month={m}&limit=200", {"items": []})
    snap["cases"] = api_json("/api/cases?limit=80", {"items": []})
    snap["cases_stats"] = api_json("/api/cases/stats", {"total": 0})
    snap["basket"] = api_json("/api/basket?page_size=80", {"items": []})
    snap["share_tasks"] = api_json("/api/share/tasks", {"items": []})
    snap["llm_providers"] = api_json("/api/llm/providers", {"items": []})
    snap["llm_models"] = api_json("/api/llm/models", {"items": []})
    snap["llm_settings"] = api_json("/api/llm/settings", {})
    snap["llm_usage"] = api_json("/api/llm/usage?days=30&group_by=module", {})
    snap["stats"] = {}
    for period in ("7d", "30d", "90d"):
        snap["stats"][period] = {
            "overview": api_json(f"/api/stats/overview?period={period}", {}),
            "ts_fetch": api_json(f"/api/stats/timeseries?metric=fetch&period={period}&granularity=day", {}),
            "ts_llm": api_json(f"/api/stats/timeseries?metric=llm&period={period}&granularity=day", {}),
            "method": api_json(f"/api/stats/fact-check-method?period={period}", {}),
            "llm_usage": api_json(f"/api/stats/llm-usage?period={period}", {}),
        }
    snap["recent_batch"] = api_json("/api/fetch/jobs/batch/recent", {})
    bid = (snap["recent_batch"] or {}).get("batch_id")
    snap["recent_batch_detail"] = api_json(f"/api/fetch/jobs/batch/{bid}", {}) if bid else {}

    # fetch_config for each source (used by 抓取配置 modal)
    snap["fetch_configs"] = {}
    for s in snap["sources"] or []:
        sid = s.get("id")
        if sid:
            snap["fetch_configs"][sid] = api_json(f"/api/config/sources/{sid}/fetch_config", {})

    # article details (content for modal)
    ids = [a.get("id") for a in (snap["articles"] or []) if a.get("id")][:40]
    details: dict = {}

    def one(aid: str):
        return aid, api_json(f"/api/articles/{aid}", None)

    print(f"  fetching {len(ids)} article details…")
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(one, i) for i in ids]
        for fut in as_completed(futs):
            aid, data = fut.result()
            if data:
                # cap huge bodies
                if isinstance(data.get("content"), str) and len(data["content"]) > 6000:
                    data["content"] = data["content"][:6000] + "\n…（展示页截断）"
                if isinstance(data.get("raw_content"), str) and len(data["raw_content"]) > 4000:
                    data["raw_content"] = data["raw_content"][:4000] + "\n…（展示页截断）"
                details[aid] = data
    snap["article_details"] = details

    # Craft contest-friendly pins from real articles (skip testsrc)
    real = [a for a in snap["articles"] if a.get("source") not in {"testsrc", "test"}]
    pins = []
    for i, a in enumerate(real[:13]):
        item = dict(a)
        item["is_pinned"] = True
        item["pinned_at"] = item.get("created_at") or "2026-08-13T20:00:00+08:00"
        pins.append(item)
    snap["pinned"] = pins
    for a in snap["articles"]:
        a["is_pinned"] = any(p["id"] == a.get("id") for p in pins)

    print(f"  articles={len(snap['articles'])} sources={len(snap['sources'])} events_aug={len((snap['events'].get('2026-08') or {}).get('items') or [])}")
    return redact_tree(snap)


def download_vendor() -> tuple[str, str]:
    cached = Path("/tmp/moni-vendor")
    alpine_p = cached / "alpine.min.js"
    gsap_p = cached / "gsap.min.js"
    if alpine_p.exists() and gsap_p.exists():
        print("Using cached Alpine + GSAP")
        return alpine_p.read_text(encoding="utf-8"), gsap_p.read_text(encoding="utf-8")
    alpine_url = "https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js"
    gsap_url = "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js"
    print("Downloading Alpine + GSAP…")
    alpine = http_get(alpine_url).decode("utf-8")
    gsap = http_get(gsap_url).decode("utf-8")
    return alpine, gsap


def patch_app_js(js: str) -> str:
    js = js.replace("this._fetchPollTimer = setInterval(tick, 5000);", "this._fetchPollTimer = setInterval(tick, 600);")
    # Hash routing so file:// / offline share works
    js = js.replace(
        """    _viewToPath: {
      articles: '/articles',
      calendar: '/calendar',
      crud:     '/crud',
      cron:     '/cron',
      fetch:    '/fetch',
      llmJobs:  '/llmJobs',
      share:    '/share',
      basket:   '/basket',
      config:   '/config',
      llmSettings: '/llmSettings',
    },
    _pathToView: {
      '/articles': 'articles',
      '/calendar': 'calendar',
      '/crud':     'crud',
      '/cron':     'cron',
      '/fetch':    'fetch',
      '/llmJobs':  'llmJobs',
      '/share':    'share',
      '/basket':   'basket',
      '/config':   'config',
      '/llmSettings': 'llmSettings',
    },""",
        """    _viewToPath: {
      articles: '/articles',
      calendar: '/calendar',
      cases:    '/cases',
      stats:    '/stats',
      crud:     '/crud',
      cron:     '/cron',
      fetch:    '/fetch',
      llmJobs:  '/llmJobs',
      share:    '/share',
      basket:   '/basket',
      config:   '/config',
      llmSettings: '/llmSettings',
    },
    _pathToView: {
      '/articles': 'articles',
      '/calendar': 'calendar',
      '/cases':    'cases',
      '/stats':    'stats',
      '/crud':     'crud',
      '/cron':     'cron',
      '/fetch':    'fetch',
      '/llmJobs':  'llmJobs',
      '/share':    'share',
      '/basket':   'basket',
      '/config':   'config',
      '/llmSettings': 'llmSettings',
      '/': 'articles',
    },
    _hashPath() {
      const h = (window.location.hash || '').replace(/^#/, '');
      if (!h || h === '/') return '/articles';
      return h.startsWith('/') ? h : '/' + h;
    },""",
    )
    js = js.replace("window.location.pathname", "this._hashPath()")
    js = re.sub(
        r"window\.history\.pushState\(([^;]+),\s*'',\s*path\);",
        r"window.history.pushState(\1, '', '#' + path);",
        js,
    )
    js = re.sub(
        r"window\.history\.replaceState\(([^;]+),\s*'',\s*path\);",
        r"window.history.replaceState(\1, '', '#' + path);",
        js,
    )
    js = js.replace(
        "window.addEventListener('popstate', (e) => this.onPopState(e));",
        "window.addEventListener('popstate', (e) => this.onPopState(e));\n"
        "      window.addEventListener('hashchange', () => this.onPopState({ state: null }));",
    )
    return js


def mock_js(snap: dict) -> str:
    payload = json.dumps(snap, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")
    return r"""
/* ── moni showcase mock API (offline, no network) ── */
window.__MONI_SHOWCASE__ = true;
window.__MONI_SNAP__ = """ + payload + r""";

(function () {
  const SNAP = window.__MONI_SNAP__;
  const store = {
    articles: (SNAP.articles || []).map((a) => ({ ...a })),
    pinned: (SNAP.pinned || []).map((a) => ({ ...a })),
    details: { ...(SNAP.article_details || {}) },
    sources: (SNAP.sources || []).map((s) => ({ ...s })),
    tags: (SNAP.tags || []).map((t) => ({ ...t })),
    filters: Array.isArray(SNAP.filters) ? SNAP.filters.map((f) => ({ ...f }))
      : ((SNAP.filters && SNAP.filters.items) || []).map((f) => ({ ...f })),
    llmJobs: (Array.isArray(SNAP.llm_jobs) ? SNAP.llm_jobs : (SNAP.llm_jobs.items || [])).map((j) => ({ ...j })),
    llmStats: { ...(SNAP.llm_stats || {}) },
    cronRuns: Array.isArray(SNAP.cron_runs) ? SNAP.cron_runs : (SNAP.cron_runs.items || []),
    events: SNAP.events || {},
    eventsPending: (SNAP.events_pending && SNAP.events_pending.items) || [],
    eventsStats: SNAP.events_stats || { total: 0, by_type: {} },
    cases: (SNAP.cases && SNAP.cases.items) || [],
    casesStats: SNAP.cases_stats || { total: 0 },
    basket: (SNAP.basket && SNAP.basket.items) || SNAP.basket || [],
    shareTasks: (SNAP.share_tasks && SNAP.share_tasks.items) || [],
    llmProviders: (SNAP.llm_providers && SNAP.llm_providers.items) || SNAP.llm_providers || [],
    llmModels: (SNAP.llm_models && SNAP.llm_models.items) || SNAP.llm_models || [],
    llmSettings: SNAP.llm_settings || {},
    llmUsage: SNAP.llm_usage || {},
    fetchConfigs: SNAP.fetch_configs || {},
    recentBatch: SNAP.recent_batch || {},
    recentBatchDetail: SNAP.recent_batch_detail || {},
  };

  const jobs = {};
  const SOURCE_ORDER = [
    'wechat_rss_a', 'wechat_rss_b', 'bestblogs', 'qbitai', 'mpaypass',
    '36kr_rss', 'bestblog-podcast', 'bestblog-x', 'xwatch', 'bestblog-yt', 'huxiu',
  ];
  const DEMO_RESULTS = {
    wechat_rss_a: { fetched: 24, parsed: 24, inserted: 6, deduped: 14, dropped_blacklist: 2, dropped_tag_filter: 2 },
    wechat_rss_b: { fetched: 31, parsed: 31, inserted: 8, deduped: 19, dropped_blacklist: 2, dropped_tag_filter: 2 },
    bestblogs: { fetched: 48, parsed: 48, inserted: 11, deduped: 28, dropped_blacklist: 4, dropped_tag_filter: 5 },
    qbitai: { fetched: 12, parsed: 12, inserted: 3, deduped: 6, dropped_blacklist: 1, dropped_tag_filter: 2 },
    mpaypass: { fetched: 9, parsed: 9, inserted: 4, deduped: 4, dropped_blacklist: 0, dropped_tag_filter: 1 },
    '36kr_rss': { fetched: 18, parsed: 18, inserted: 5, deduped: 9, dropped_blacklist: 2, dropped_tag_filter: 2 },
    'bestblog-podcast': { fetched: 7, parsed: 7, inserted: 2, deduped: 4, dropped_blacklist: 0, dropped_tag_filter: 1 },
    'bestblog-x': { fetched: 16, parsed: 16, inserted: 4, deduped: 9, dropped_blacklist: 1, dropped_tag_filter: 2 },
    xwatch: { fetched: 14, parsed: 14, inserted: 5, deduped: 7, dropped_blacklist: 0, dropped_tag_filter: 2 },
    'bestblog-yt': { fetched: 5, parsed: 5, inserted: 1, deduped: 3, dropped_blacklist: 0, dropped_tag_filter: 1 },
    huxiu: { fetched: 11, parsed: 11, inserted: 3, deduped: 6, dropped_blacklist: 1, dropped_tag_filter: 1 },
  };

  function jsonResp(data, status) {
    return new Response(JSON.stringify(data), {
      status: status || 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  function nowSec() { return Date.now() / 1000; }
  function uid(prefix) {
    return (prefix || 'id') + '-' + Math.random().toString(36).slice(2, 10);
  }
  function qs(url) {
    try { return new URL(url, 'http://showcase.local').searchParams; }
    catch (e) { return new URLSearchParams(); }
  }
  function pathOf(url) {
    try { return new URL(url, 'http://showcase.local').pathname; }
    catch (e) { return String(url).split('?')[0]; }
  }
  function sourceNameByRef(ref) {
    const s = store.sources.find((x) => x.id === ref || x.name === ref);
    return s ? s.name : ref;
  }
  function sourceByRef(ref) {
    return store.sources.find((x) => x.id === ref || x.name === ref) || null;
  }
  function filterArticles(params) {
    let list = store.articles.slice();
    const kw = (params.get('keyword') || '').trim().toLowerCase();
    const src = params.get('source') || '';
    const tag = params.get('tag') || '';
    if (kw) list = list.filter((a) => (a.title || '').toLowerCase().includes(kw) || (a.source || '').toLowerCase().includes(kw));
    if (src) list = list.filter((a) => a.source === src);
    if (tag) list = list.filter((a) => (a.tags || []).some((t) => (t.name || t) === tag));
    return list;
  }
  function pageSlice(list, params, defLimit) {
    const limit = parseInt(params.get('limit') || params.get('page_size') || defLimit, 10) || defLimit;
    const offset = parseInt(params.get('offset') || '0', 10) || 0;
    const page = parseInt(params.get('page') || '1', 10) || 1;
    const off = params.has('offset') ? offset : (page - 1) * limit;
    return list.slice(off, off + limit);
  }
  function batchProgress(job) {
    const elapsed = Math.max(0, nowSec() - job.t0);
    const names = job.sources;
    const total = names.length || 11;
    const doneN = elapsed >= 3 ? total : Math.min(total, Math.max(1, Math.floor(elapsed / 3 * total) + 1));
    const current = names[Math.min(doneN - 1, names.length - 1)] || names[0] || 'wechat_rss_a';
    const results = names.slice(0, doneN).map((n) => {
      const base = DEMO_RESULTS[n] || { fetched: 8, parsed: 8, inserted: 2, deduped: 4, dropped_blacklist: 1, dropped_tag_filter: 1 };
      return { source: n, ...base, error: null, dropped_articles: [] };
    });
    const inserted = results.reduce((s, r) => s + (r.inserted || 0), 0);
    const done = elapsed >= 3;
    return {
      batch_id: job.id,
      status: done ? 'done' : 'running',
      total_sources: total,
      done_sources: done ? total : Math.max(0, doneN - 1),
      failed_sources: 0,
      total_feeds: 86,
      done_feeds: done ? 86 : Math.round(86 * elapsed / 3),
      articles_so_far: inserted,
      current_source: done ? null : current,
      current_feed: done ? null : (current + ' / feed-1'),
      elapsed_seconds: Math.round(elapsed),
      results: done ? results : results.slice(0, Math.max(0, doneN - 1)),
      errors: [],
    };
  }
  function singleProgress(job) {
    const elapsed = Math.max(0, nowSec() - job.t0);
    const name = job.source;
    const base = DEMO_RESULTS[name] || { fetched: 12, parsed: 12, inserted: 3, deduped: 6, dropped_blacklist: 1, dropped_tag_filter: 2 };
    const done = elapsed >= 3;
    const frac = Math.min(1, elapsed / 3);
    return {
      id: job.id,
      job_id: job.id,
      source: name,
      status: done ? 'done' : 'running',
      done_feeds: done ? 8 : Math.max(1, Math.round(8 * frac)),
      total_feeds: 8,
      current_feed: done ? null : (name + ' · page ' + Math.max(1, Math.round(8 * frac))),
      articles_so_far: done ? base.inserted : Math.max(0, Math.round(base.inserted * frac)),
      elapsed_seconds: Math.round(elapsed),
      result: done ? { ...base, error: null } : null,
      error: null,
    };
  }

  const origFetch = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (!url.includes('/api/')) {
      try { return await origFetch(input, init); }
      catch (e) { return jsonResp({ error: 'offline' }, 503); }
    }
    const method = ((init && init.method) || (typeof input !== 'string' && input.method) || 'GET').toUpperCase();
    const path = pathOf(url);
    const params = qs(url);
    let body = {};
    if (init && init.body) {
      try { body = typeof init.body === 'string' ? JSON.parse(init.body) : init.body; } catch (e) { body = {}; }
    }

    // ── service
    if (path === '/api/service/status') {
      return jsonResp({ running: true, pid: (SNAP.service && SNAP.service.pid) || 8900, uptime_sec: 12880, port: 8900, source: 'showcase' });
    }
    if (path === '/api/service/restart' && method === 'POST') {
      return jsonResp({ ok: true, pid: 8900, message: '已重启（展示页模拟）' });
    }

    // ── config
    if (path === '/api/config/sources') return jsonResp(store.sources);
    if (path === '/api/config/tags') return jsonResp(store.tags);
    if (path === '/api/config/filters') {
      const sid = params.get('source_id');
      const list = sid ? store.filters.filter((f) => String(f.source_id) === String(sid)) : store.filters;
      return jsonResp(list);
    }
    let m;
    if ((m = path.match(/^\/api\/config\/sources\/([^/]+)\/tags$/))) {
      const s = sourceByRef(decodeURIComponent(m[1]));
      if (method === 'GET') return jsonResp(s ? (s.tags || []) : []);
      if (s) s.tags = body.tags || body || [];
      return jsonResp({ ok: true, tags: s ? s.tags : [] });
    }
    if ((m = path.match(/^\/api\/config\/sources\/([^/]+)\/fetch_config$/))) {
      const sid = decodeURIComponent(m[1]);
      if (method === 'GET') {
        return jsonResp(store.fetchConfigs[sid] || { source_id: sid, source_name: sid, merged: {}, defaults: {}, has_custom: false });
      }
      store.fetchConfigs[sid] = { ...(store.fetchConfigs[sid] || {}), merged: body, has_custom: true };
      return jsonResp({ ok: true });
    }
    if ((m = path.match(/^\/api\/config\/sources\/([^/]+)$/))) {
      const sid = decodeURIComponent(m[1]);
      const s = sourceByRef(sid);
      if (method === 'DELETE') {
        store.sources = store.sources.filter((x) => x.id !== sid && x.name !== sid);
        return jsonResp({ ok: true });
      }
      if (s) Object.assign(s, body);
      return jsonResp(s || body);
    }
    if (path === '/api/config/sources' && method === 'POST') {
      const row = { id: body.id || uid('src'), is_active: true, tags: [], fetch_interval_min: 30, ...body };
      store.sources.push(row);
      return jsonResp(row, 201);
    }
    if ((m = path.match(/^\/api\/config\/tags\/([^/]+)$/))) {
      const name = decodeURIComponent(m[1]);
      if (method === 'DELETE') {
        store.tags = store.tags.filter((t) => t.name !== name);
        return jsonResp({ ok: true });
      }
      const t = store.tags.find((x) => x.name === name);
      if (t) Object.assign(t, body);
      return jsonResp(t || body);
    }
    if (path === '/api/config/tags' && method === 'POST') {
      const row = { is_active: true, ...body };
      store.tags.push(row);
      return jsonResp(row, 201);
    }
    if ((m = path.match(/^\/api\/config\/filters\/([^/]+)$/))) {
      const id = m[1];
      if (method === 'DELETE') {
        store.filters = store.filters.filter((f) => String(f.id) !== String(id));
        return jsonResp({ ok: true });
      }
      const f = store.filters.find((x) => String(x.id) === String(id));
      if (f) Object.assign(f, body);
      return jsonResp(f || body);
    }
    if (path === '/api/config/filters' && method === 'POST') {
      const row = { id: Date.now(), is_active: true, ...body };
      store.filters.push(row);
      return jsonResp(row, 201);
    }

    // ── articles
    if (path === '/api/articles' && method === 'GET') {
      const list = filterArticles(params);
      return jsonResp(pageSlice(list, params, 50));
    }
    if (path === '/api/articles' && method === 'POST') {
      const row = {
        id: uid('art'),
        created_at: new Date().toISOString(),
        tags: [],
        is_pinned: false,
        ...body,
      };
      store.articles.unshift(row);
      store.details[row.id] = row;
      return jsonResp(row, 201);
    }
    if (path === '/api/articles/pinned') {
      const list = store.pinned.slice();
      const items = pageSlice(list, params, 50);
      return jsonResp({ items, total: list.length });
    }
    if (path === '/api/articles/pins/bulk-unpin' && method === 'POST') {
      const ids = new Set(body.ids || body.article_ids || []);
      store.pinned = store.pinned.filter((a) => !ids.has(a.id));
      store.articles.forEach((a) => { if (ids.has(a.id)) a.is_pinned = false; });
      return jsonResp({ ok: true, count: ids.size });
    }
    if ((m = path.match(/^\/api\/articles\/([^/]+)\/pin$/))) {
      const id = decodeURIComponent(m[1]);
      const on = params.get('on') !== 'false' && params.get('on') !== '0';
      const a = store.articles.find((x) => x.id === id);
      if (a) a.is_pinned = on;
      if (on && a && !store.pinned.find((x) => x.id === id)) {
        store.pinned.unshift({ ...a, is_pinned: true, pinned_at: new Date().toISOString() });
      }
      if (!on) store.pinned = store.pinned.filter((x) => x.id !== id);
      return jsonResp({ ok: true, is_pinned: on });
    }
    if ((m = path.match(/^\/api\/articles\/([^/]+)\/summary$/)) && method === 'POST') {
      const jobId = uid('job');
      const art = store.details[decodeURIComponent(m[1])] || store.articles.find((x) => x.id === decodeURIComponent(m[1]));
      store.llmJobs.unshift({
        id: jobId, task_type: 'summary', article_id: m[1], article_title: art && art.title,
        model: 'MiniMax-M3', status: 'running', result: null, error: null,
        duration_ms: null, attempts: 1, created_at: new Date().toISOString(),
      });
      store.llmStats.running = (store.llmStats.running || 0) + 1;
      setTimeout(() => {
        const j = store.llmJobs.find((x) => x.id === jobId);
        if (j) {
          j.status = 'done';
          j.duration_ms = 1800;
          j.result = '【展示摘要】本文梳理同业最新动态：产品上新、监管口径与落地节奏。对银行数字化与支付生态有直接参考价值。';
        }
        store.llmStats.running = Math.max(0, (store.llmStats.running || 1) - 1);
        store.llmStats.done = (store.llmStats.done || 0) + 1;
        if (art) art.summary = j ? j.result : '';
      }, 1600);
      return jsonResp({ job_id: jobId }, 202);
    }
    if ((m = path.match(/^\/api\/articles\/([^/]+)\/fact-check-plus$/)) && method === 'POST') {
      const jobId = uid('job');
      store.llmJobs.unshift({
        id: jobId, task_type: 'fact_check_plus', article_id: m[1],
        model: 'MiniMax-M3', status: 'running', created_at: new Date().toISOString(),
        attempts: 1, result: null, error: null, duration_ms: null,
      });
      setTimeout(() => {
        const j = store.llmJobs.find((x) => x.id === jobId);
        if (j) { j.status = 'done'; j.duration_ms = 2200; j.result = '联网核查完成：核心事实与公开报道一致。'; }
        const art = store.details[decodeURIComponent(m[1])];
        if (art) {
          art.has_web_fact_check = true;
          art.has_fact_check = true;
          art.fact_check_conclusion = '核心事实成立，细节与公开来源一致。';
          art.fact_checked_at = new Date().toISOString();
        }
      }, 1800);
      return jsonResp({ job_id: jobId }, 202);
    }
    if ((m = path.match(/^\/api\/articles\/([^/]+)\/fact-check$/)) && method === 'POST') {
      const jobId = uid('job');
      store.llmJobs.unshift({
        id: jobId, task_type: 'fact_check', article_id: m[1],
        model: 'MiniMax-M3', status: 'running', created_at: new Date().toISOString(),
        attempts: 1, result: null, error: null,
      });
      setTimeout(() => {
        const j = store.llmJobs.find((x) => x.id === jobId);
        if (j) { j.status = 'done'; j.result = '事实核查：标题与正文关键数据可交叉验证，无明显矛盾。'; j.duration_ms = 1500; }
        const art = store.details[decodeURIComponent(m[1])];
        if (art) { art.has_fact_check = true; art.fact_check_conclusion = j.result; }
      }, 1500);
      return jsonResp({ job_id: jobId }, 202);
    }
    if ((m = path.match(/^\/api\/articles\/([^/]+)\/import-fulltext$/))) {
      return jsonResp({ ok: true, message: '展示页已模拟导入全文' });
    }
    if ((m = path.match(/^\/api\/articles\/([^/]+)$/))) {
      const id = decodeURIComponent(m[1]);
      if (method === 'DELETE') {
        store.articles = store.articles.filter((a) => a.id !== id);
        store.pinned = store.pinned.filter((a) => a.id !== id);
        return jsonResp({ ok: true });
      }
      if (method === 'PUT' || method === 'PATCH') {
        const a = store.articles.find((x) => x.id === id);
        if (a) Object.assign(a, body);
        if (store.details[id]) Object.assign(store.details[id], body);
        return jsonResp(a || body);
      }
      const detail = store.details[id] || store.articles.find((x) => x.id === id) || {};
      const merged = { content: (detail.title || '') + '\n\n这是展示页内置的正文摘要。原文已在本地新闻库落库，可继续生成摘要、事实核查或提取同业案例。', ...detail };
      if (!merged.content) merged.content = merged.raw_content || '（无内容）';
      return jsonResp(merged);
    }

    // ── fetch
    if (path === '/api/fetch/all/run' && method === 'POST') {
      const id = uid('batch');
      const names = store.sources.filter((s) => s.is_active).map((s) => s.name);
      names.sort((a, b) => (SOURCE_ORDER.indexOf(a) < 0 ? 99 : SOURCE_ORDER.indexOf(a)) - (SOURCE_ORDER.indexOf(b) < 0 ? 99 : SOURCE_ORDER.indexOf(b)));
      jobs[id] = { id, kind: 'batch', t0: nowSec(), sources: names.length ? names : SOURCE_ORDER.slice() };
      return jsonResp({ batch_id: id, sources_total: jobs[id].sources.length });
    }
    if ((m = path.match(/^\/api\/fetch\/sources\/([^/]+)\/run$/)) && method === 'POST') {
      const ref = decodeURIComponent(m[1]);
      const name = sourceNameByRef(ref);
      const id = uid('job');
      jobs[id] = { id, kind: 'single', t0: nowSec(), source: name };
      return jsonResp({ job_id: id, source: name });
    }
    if ((m = path.match(/^\/api\/fetch\/sources\/([^/]+)\/ping$/)) && method === 'POST') {
      const name = sourceNameByRef(decodeURIComponent(m[1]));
      return jsonResp({ ok: true, source: name, latency_ms: 128, status: 'reachable', message: '连通正常（展示）' });
    }
    if (path === '/api/fetch/jobs/batch/recent') {
      if (store.recentBatch && store.recentBatch.batch_id) return jsonResp(store.recentBatch);
      return jsonResp({ batch_id: 'demo-recent' });
    }
    if ((m = path.match(/^\/api\/fetch\/jobs\/batch\/([^/]+)$/))) {
      const id = m[1];
      if (jobs[id]) return jsonResp(batchProgress(jobs[id]));
      if (id === 'demo-recent' || (store.recentBatchDetail && store.recentBatchDetail.batch_id === id)) {
        const d = store.recentBatchDetail || {};
        return jsonResp({
          batch_id: id,
          status: d.status || 'done',
          total_sources: d.total_sources || 11,
          done_sources: d.done_sources || 11,
          failed_sources: d.failed_sources || 0,
          articles_so_far: d.articles_so_far || 42,
          elapsed_seconds: d.elapsed_seconds || 88,
          results: d.results || Object.keys(DEMO_RESULTS).map((n) => ({ source: n, ...DEMO_RESULTS[n], error: null })),
          errors: d.errors || [],
          current_source: null,
          current_feed: null,
        });
      }
      return jsonResp({ batch_id: id, status: 'done', total_sources: 11, done_sources: 11, failed_sources: 0, results: [], errors: [] });
    }
    if (path === '/api/fetch/jobs') {
      return jsonResp([]);
    }
    if ((m = path.match(/^\/api\/fetch\/jobs\/([^/]+)\/cancel$/)) && method === 'POST') {
      const id = decodeURIComponent(m[1]);
      if (jobs[id]) jobs[id].cancelled = true;
      return jsonResp({ ok: true, status: 'cancelled' });
    }
    if ((m = path.match(/^\/api\/fetch\/jobs\/([^/]+)$/))) {
      const id = decodeURIComponent(m[1]);
      if (jobs[id]) return jsonResp(singleProgress(jobs[id]));
      return jsonResp({ id, status: 'done', result: { fetched: 10, parsed: 10, inserted: 3 }, elapsed_seconds: 3 });
    }

    // ── basket
    if (path === '/api/basket') {
      let list = store.basket.slice();
      const src = params.get('source');
      const reason = params.get('reason') || params.get('dropped_reason');
      const rescued = params.get('rescued');
      if (src) list = list.filter((b) => b.source === src);
      if (reason) list = list.filter((b) => b.dropped_reason === reason);
      if (rescued === '1') list = list.filter((b) => b.rescued_at);
      if (rescued === '0') list = list.filter((b) => !b.rescued_at);
      const items = pageSlice(list, params, 20);
      return jsonResp({ items, total: list.length, page: parseInt(params.get('page') || '1', 10) });
    }
    if ((m = path.match(/^\/api\/basket\/([^/]+)\/rescue$/)) && method === 'POST') {
      const b = store.basket.find((x) => x.id === m[1]);
      if (b) b.rescued_at = new Date().toISOString();
      return jsonResp({ ok: true, article_id: uid('art') });
    }
    if ((m = path.match(/^\/api\/basket\/([^/]+)$/)) && method === 'DELETE') {
      store.basket = store.basket.filter((x) => x.id !== m[1]);
      return jsonResp({ ok: true });
    }

    // ── calendar / events
    if (path === '/api/events/stats') return jsonResp(store.eventsStats);
    if (path === '/api/events/pending') return jsonResp({ items: store.eventsPending });
    if (path === '/api/events') {
      const y = params.get('year') || '2026';
      const mo = String(params.get('month') || '8').padStart(2, '0');
      const pack = store.events[y + '-' + mo] || { items: [] };
      let items = pack.items || [];
      const typ = params.get('event_type');
      if (typ) items = items.filter((e) => e.event_type === typ);
      return jsonResp({ items });
    }
    if ((m = path.match(/^\/api\/events\/([^/]+)\/(confirm|reject)$/)) && method === 'POST') {
      store.eventsPending = store.eventsPending.filter((e) => e.event_id !== decodeURIComponent(m[1]));
      return jsonResp({ ok: true, status: m[2] });
    }

    // ── cases
    if (path === '/api/cases/stats') return jsonResp(store.casesStats);
    if (path === '/api/cases/search') {
      const q = (params.get('q') || '').toLowerCase();
      const items = store.cases.filter((c) =>
        !q || (c.subject || '').toLowerCase().includes(q) || (c.summary || '').toLowerCase().includes(q)
        || (c.case_id || '').toLowerCase().includes(q) || (c.amount || '').toLowerCase().includes(q)
      );
      return jsonResp({ items });
    }
    if (path === '/api/cases') {
      let items = store.cases.slice();
      const typ = params.get('case_type') || params.get('type');
      const act = params.get('case_action') || params.get('action');
      const refv = params.get('reference_value');
      if (typ) items = items.filter((c) => c.case_type === typ);
      if (act) items = items.filter((c) => c.case_action === act);
      if (refv) items = items.filter((c) => c.reference_value === refv);
      return jsonResp({ items, total: items.length });
    }
    if ((m = path.match(/^\/api\/cases\/retry\/([^/]+)$/)) && method === 'POST') {
      return jsonResp({ status: 'completed', case_count: 1 });
    }

    // ── stats
    if (path === '/api/stats/overview') {
      const p = params.get('period') || '30d';
      return jsonResp((SNAP.stats[p] && SNAP.stats[p].overview) || (SNAP.stats['30d'] && SNAP.stats['30d'].overview) || {});
    }
    if (path === '/api/stats/timeseries') {
      const p = params.get('period') || '30d';
      const metric = params.get('metric') || 'fetch';
      const pack = SNAP.stats[p] || SNAP.stats['30d'] || {};
      return jsonResp(metric === 'llm' ? pack.ts_llm : pack.ts_fetch);
    }
    if (path === '/api/stats/fact-check-method') {
      const p = params.get('period') || '30d';
      return jsonResp((SNAP.stats[p] && SNAP.stats[p].method) || {});
    }
    if (path === '/api/stats/llm-usage') {
      const p = params.get('period') || '30d';
      const raw = (SNAP.stats[p] && SNAP.stats[p].llm_usage) || {};
      return jsonResp({
        period: p,
        totals: { calls: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0, ...(raw.totals || {}) },
        by_model: raw.by_model || [],
        ...raw,
      });
    }

    // ── llm
    if (path === '/api/llm/jobs/stats/summary') return jsonResp(store.llmStats);
    if (path === '/api/llm/jobs') return jsonResp(store.llmJobs);
    if ((m = path.match(/^\/api\/llm\/jobs\/([^/]+)\/cancel$/)) && method === 'POST') {
      const j = store.llmJobs.find((x) => x.id === m[1]);
      if (j) j.status = 'cancelled';
      return jsonResp({ ok: true });
    }
    if ((m = path.match(/^\/api\/llm\/jobs\/([^/]+)$/))) {
      const j = store.llmJobs.find((x) => x.id === m[1]) || { id: m[1], status: 'done', result: '（展示）' };
      return jsonResp(j);
    }
    if (path === '/api/llm/providers') {
      if (method === 'POST') {
        const row = { id: uid('prov'), is_active: 1, ...body };
        store.llmProviders.push(row);
        return jsonResp(row, 201);
      }
      return jsonResp({ items: store.llmProviders });
    }
    if ((m = path.match(/^\/api\/llm\/providers\/([^/]+)\/test$/))) {
      return jsonResp({ ok: true, message: '连通正常（展示）' });
    }
    if ((m = path.match(/^\/api\/llm\/providers\/([^/]+)$/))) {
      const p = store.llmProviders.find((x) => x.id === m[1]);
      if (method === 'DELETE') {
        store.llmProviders = store.llmProviders.filter((x) => x.id !== m[1]);
        return jsonResp({ ok: true });
      }
      if (p) Object.assign(p, body);
      return jsonResp(p || body);
    }
    if (path === '/api/llm/models') {
      if (method === 'POST') {
        const row = { id: uid('model'), enabled: 1, ...body };
        store.llmModels.push(row);
        return jsonResp(row, 201);
      }
      return jsonResp({ items: store.llmModels });
    }
    if ((m = path.match(/^\/api\/llm\/models\/([^/]+)$/))) {
      const md = store.llmModels.find((x) => x.id === m[1]);
      if (md) Object.assign(md, body);
      return jsonResp(md || body);
    }
    if (path === '/api/llm/settings') {
      if (method === 'POST' || method === 'PUT' || method === 'PATCH') {
        Object.assign(store.llmSettings, body);
        return jsonResp(store.llmSettings);
      }
      return jsonResp(store.llmSettings);
    }
    if (path === '/api/llm/usage') return jsonResp(store.llmUsage || {
      summary: { total_requests: 128, total_tokens: 482000, total_cost: 1.26 },
      by_module: [
        { module: 'summary', total_tokens: 180000 },
        { module: 'fact_check', total_tokens: 210000 },
        { module: 'share', total_tokens: 92000 },
      ],
      by_model: [
        { model: 'MiniMax-M3', total_tokens: 320000 },
        { model: 'deepseek-chat', total_tokens: 162000 },
      ],
      by_date: [],
    });

    if ((m = path.match(/^\/api\/fact-check-plus\/evidence\/([^/]+)$/))) {
      return jsonResp({
        items: [
          { query: '原文关键事实交叉核验', url: 'https://www.example.com/news/1', snippet: '公开报道与文章核心表述一致。', domain: 'example.com', fetched_at: '2026-08-17 10:00', model: 'MiniMax-M3' },
          { query: '监管口径是否更新', url: 'https://www.example.com/news/2', snippet: '未见与正文冲突的新口径。', domain: 'example.com', fetched_at: '2026-08-17 10:00', model: 'MiniMax-M3' },
        ],
      });
    }

    // ── share
    if (path === '/api/share/estimate' && method === 'POST') {
      return jsonResp({
        format: body.format || 'html',
        articles_count: (body.article_ids || []).length || store.pinned.length,
        total_chars: 12800,
        model_name: 'MiniMax-M3',
        provider_name: 'minimax-cn',
        input_tokens_est: 6400,
        output_tokens_est: 1800,
        duration_sec_est: 42,
        duration_sec_basis: 6,
        artifact_path_template: 'data/artifacts/brief-YYYYMMDD.html',
      });
    }
    if (path === '/api/share/brief' && method === 'POST') {
      const id = uid('brief');
      const task = {
        id, format: body.format || 'html', article_count: store.pinned.length,
        status: 'running', progress_stage: 'generate', progress_pct: 15,
        progress_message: '正在生成一键简报…', created_at: new Date().toISOString(),
        output_url: null, error: null,
      };
      store.shareTasks.unshift(task);
      let pct = 15;
      const timer = setInterval(() => {
        pct += 28;
        const t = store.shareTasks.find((x) => x.id === id);
        if (!t) { clearInterval(timer); return; }
        if (pct >= 100) {
          t.status = 'done'; t.progress_pct = 100; t.progress_stage = 'done';
          t.progress_message = '已完成'; t.output_url = '#/share';
          clearInterval(timer);
        } else {
          t.progress_pct = pct;
        }
      }, 700);
      return jsonResp(task, 202);
    }
    if (path === '/api/share' && method === 'POST') {
      const id = uid('share');
      const task = {
        id, format: body.format || 'markdown', article_count: (body.article_ids || []).length,
        status: 'pending', created_at: new Date().toISOString(), output_url: null, error: null,
      };
      store.shareTasks.unshift(task);
      return jsonResp(task, 201);
    }
    if (path === '/api/share/tasks') return jsonResp({ items: store.shareTasks });
    if (path === '/api/share/worker/run' && method === 'POST') {
      store.shareTasks.forEach((t) => {
        if (t.status === 'pending') { t.status = 'running'; t.progress_pct = 40; t.progress_stage = 'render'; }
      });
      setTimeout(() => {
        store.shareTasks.forEach((t) => {
          if (t.status === 'running') { t.status = 'done'; t.progress_pct = 100; t.output_url = '#/share'; }
        });
      }, 1800);
      return jsonResp({ started: true, pid: 4242, log_path: '(showcase) worker.log' });
    }
    if ((m = path.match(/^\/api\/share\/tasks\/([^/]+)\/(pause|resume|cancel)$/)) && method === 'POST') {
      const t = store.shareTasks.find((x) => x.id === decodeURIComponent(m[1]));
      if (t) {
        if (m[2] === 'pause') t.status = 'paused';
        if (m[2] === 'resume') t.status = 'running';
        if (m[2] === 'cancel') t.status = 'cancelled';
      }
      return jsonResp(t || { ok: true });
    }

    if (path === '/api/cron/runs') return jsonResp(store.cronRuns);

    console.warn('[showcase] unhandled', method, path);
    return jsonResp({ ok: true, showcase: true });
  };
})();
"""


def assemble(html: str, css: str, scripts: list[str], alpine: str, gsap: str, icons: dict[str, str]) -> str:
    # drop external deps
    html = re.sub(r'<link rel="preload"[^>]*>\s*', '', html)
    html = re.sub(r'<link rel="preconnect"[^>]*>\s*', '', html)
    html = re.sub(r'<link href="https://fonts\.googleapis\.com[^"]*"[^>]*>\s*', '', html)
    html = re.sub(r'<link rel="stylesheet" href="css/main\.css[^"]*">', '<style>\n' + css + '\n</style>', html)
    html = replace_icon_paths(html, icons)
    # favicon already replaced if path matched

    # strip original script tags
    html = re.sub(r'<script defer src="https://cdn\.jsdelivr\.net/npm/alpinejs[^"]*"></script>\s*', '', html)
    html = re.sub(r'<script src="https://cdn\.jsdelivr\.net/npm/gsap[^"]*"></script>\s*', '', html)
    html = re.sub(r'<script src="js/[^"]*"></script>\s*', '', html)

    # start on fetch? keep articles via hash default
    bundle = "\n".join(scripts)
    tail = (
        "<script>\n" + gsap + "\n</script>\n"
        "<script>\n" + bundle + "\n</script>\n"
        "<script>\n" + alpine + "\n</script>\n"
    )
    html = html.replace("</body>", tail + "</body>")
    # title hint
    html = html.replace(
        "<title>瞭望 moni — 同业信源本地新闻库</title>",
        "<title>瞭望 moni — 同业信源本地新闻库（展示页）</title>",
    )
    return html


def main():
    icons = collect_icons()
    print(f"Icons encoded: {len(icons)}")
    alpine, gsap = download_vendor()
    snap = fetch_snapshot()

    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "css" / "main.css").read_text(encoding="utf-8")

    js_files = [
        "toast.js", "modal.js", "calendar.js", "cases.js", "stats.js",
        "theme.js", "app.js", "icons.js", "pin_anim.js",
    ]
    scripts = [mock_js(snap)]
    for name in js_files:
        text = (STATIC / "js" / name).read_text(encoding="utf-8")
        if name == "app.js":
            text = patch_app_js(text)
        text = replace_icon_paths(text, icons)
        scripts.append(f"/* ===== {name} ===== */\n{text}")

    out = assemble(html, css, scripts, alpine, gsap, icons)
    # escape accidental </script> in vendor
    # already handled in JSON; alpine/gsap typically safe
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(out, encoding="utf-8")
    mb = OUT.stat().st_size / (1024 * 1024)
    print(f"Wrote {OUT} ({mb:.2f} MB)")


if __name__ == "__main__":
    main()
