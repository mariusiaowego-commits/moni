#!/usr/bin/env python3
"""Assemble a single-file offline high-fidelity mockup of the moni web UI."""

from __future__ import annotations

import base64
import io
import json
import mimetypes
import re
import ssl
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from typing import Any

from PIL import Image

# v1.0.35: oklch → hex 转换 (Chrome 105 等老浏览器无 oklch 支持)
# 在 assemble() 写 HTML 前预算, 避免 file:// 分享给老 Chrome 全白
def _oklch_to_srgb(L: float, C: float, h_deg: float, alpha: float = 1.0) -> tuple[int, int, int, float]:
    """CSS oklch (L: 0-1, C: 0-0.4, h: 0-360) → sRGB (0-255, alpha 0-1) per CSS Color 4."""
    import math
    h = math.radians(h_deg)
    a = C * math.cos(h); b = C * math.sin(h)
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l = l_ ** 3; m = m_ ** 3; s = s_ ** 3
    r = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    b2 = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s

    def to_srgb(x):
        x = max(0.0, min(1.0, x))
        if x <= 0.0031308: return 12.92 * x
        return 1.055 * (x ** (1 / 2.4)) - 0.055

    return (round(255 * to_srgb(r)), round(255 * to_srgb(g)),
            round(255 * to_srgb(b2)), alpha)

_OKLCH_PARSE_RE = re.compile(r'^oklch\(\s*([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)(?:\s*/\s*([0-9.]+))?\s*\)$')
_OKLCH_FROM_RE = re.compile(
    r'^oklch\(\s*from\s+var\(--([\w-]+)\)\s+([^\s/]+(?:\s+[^\s/]+)*?)(?:\s*/\s*([0-9.]+))?\s*\)$'
)

def _oklch_static_to_hex(s: str) -> str:
    p = _OKLCH_PARSE_RE.match(s)
    if not p: return None
    L, C, h = float(p.group(1)), float(p.group(2)), float(p.group(3))
    a = float(p.group(4)) if p.group(4) else 1.0
    R, G, B, _ = _oklch_to_srgb(L, C, h, a)
    if a < 1.0:
        return f"#{R:02X}{G:02X}{B:02X}{round(255*a):02X}"
    return f"#{R:02X}{G:02X}{B:02X}"

def _oklch_from_var_to_rgba(s: str, var_map: dict[str, str]) -> str:
    """oklch(from var(--accent) calc(l + 0.05) c h [/ alpha])"""
    m = _OKLCH_FROM_RE.match(s)
    if not m: return None
    var_name = m.group(1)
    expr = m.group(2).strip()
    alpha = float(m.group(3)) if m.group(3) else 1.0
    base_hex = var_map.get(var_name)
    if not base_hex: return None

    # 简化版: 只识别 calc(l +/- X), 其他 (c/h) 当无变化
    delta = 0.0
    delta_match = re.search(r'calc\(l\s*\+\s*([0-9.]+)\)', expr)
    if delta_match:
        delta = float(delta_match.group(1))
    else:
        delta_match = re.search(r'calc\(l\s*-\s*([0-9.]+)\)', expr)
        if delta_match:
            delta = -float(delta_match.group(1))

    h = base_hex.lstrip('#')[:6]
    r = int(h[0:2], 16); g = int(h[2:4], 16); b = int(h[4:6], 16)
    if delta > 0:
        r = min(255, int(r + 255 * delta))
        g = min(255, int(g + 255 * delta))
        b = min(255, int(b + 255 * delta))
    elif delta < 0:
        r = max(0, int(r + 255 * delta))
        g = max(0, int(g + 255 * delta))
        b = max(0, int(b + 255 * delta))

    if alpha < 1.0:
        return f"rgba({r},{g},{b},{alpha:g})"
    return f"#{r:02X}{g:02X}{b:02X}"

_VAR_DECL_RE = re.compile(r'--([\w-]+)\s*:\s*([^;,}\n]+)')
_ROOT_BLOCK_RE = re.compile(r':root\s*\{([^}]*)\}', re.S)

def _strip_oklch(css: str, var_map: dict[str, str] | None = None) -> tuple[str, dict[str, str]]:
    """将 CSS 中所有 oklch(...) 转 hex. 返回 (new_css, full_var_map).

    v1.0.35.16 fix: 只扫 ``:root { ... }`` 块内的 --xxx 声明. demo HTML 渲染浅色
    主题, 不需要 .dark 块; 之前的 ``_VAR_DECL_RE.finditer(css)`` 全文扫描会把
    ``.dark`` 块的同名暗色变量覆盖 ``:root`` 浅色变量, 导致 ``oklch(from var(--muted) ...)``
    反算出暗色 hex, 出现「一行深棕底色」诡异渲染。
    """
    # 步骤 1: 只在 :root { ... } 块内找变量声明, 避免 .dark 块覆盖
    var_map = dict(var_map or {})
    root_block_match = _ROOT_BLOCK_RE.search(css)
    if root_block_match:
        root_css = root_block_match.group(1)
        for m in _VAR_DECL_RE.finditer(root_css):
            name, val = m.group(1), m.group(2).strip()
            # 跳过 calc() / from var() 派生色 (要 hex map 后再处理)
            if val.startswith('calc(') or val.startswith('var(') or val.startswith('from'):
                continue
            hex_val = _oklch_static_to_hex(val)
            if hex_val:
                # 第一次出现即覆盖: :root 内若同名重声明取最后一个
                var_map[name] = hex_val

    # 步骤 2: 替换派生色 oklch(from var(--xxx) ...) → hex/rgba
    css2 = re.sub(
        r'oklch\(\s*from\s+var\(--[\w-]+\)[^)]*\)',
        lambda m: _oklch_from_var_to_rgba(m.group(0), var_map) or m.group(0),
        css,
    )

    # 步骤 3: 替换剩余静态 oklch(...) → hex
    css3 = re.sub(
        r'oklch\([^)]*\)',
        lambda m: _oklch_static_to_hex(m.group(0)) or m.group(0),
        css2,
    )

    # 统计
    remaining = re.findall(r'oklch\(', css3)
    if remaining:
        print(f"  ! {len(remaining)} oklch remain (解析失败的复杂表达式)")

    return css3, var_map


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


def knockout_black_bg(png_bytes: bytes, thresh: int = 36) -> bytes:
    """Flood-fill near-black from the image edge so baked-in plates become transparent."""
    im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    w, h = im.size
    px = im.load()

    def is_bg(x: int, y: int) -> bool:
        r, g, b, a = px[x, y]
        return a > 0 and r <= thresh and g <= thresh and b <= thresh

    seen = [[False] * h for _ in range(w)]
    q: deque[tuple[int, int]] = deque()
    for x in range(w):
        q.append((x, 0))
        q.append((x, h - 1))
    for y in range(h):
        q.append((0, y))
        q.append((w - 1, y))
    while q:
        x, y = q.popleft()
        if x < 0 or y < 0 or x >= w or y >= h or seen[x][y]:
            continue
        seen[x][y] = True
        if not is_bg(x, y):
            continue
        px[x, y] = (0, 0, 0, 0)
        q.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))

    # soften anti-aliased fringe next to knocked-out pixels
    for x in range(w):
        for y in range(h):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            dark = (r + g + b) / 3
            if dark >= 80:
                continue
            touch = False
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and px[nx, ny][3] == 0:
                    touch = True
                    break
            if touch:
                px[x, y] = (r, g, b, max(0, int(a * dark / 80)))

    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def collect_icons() -> dict[str, str]:
    icons: dict[str, str] = {}
    knock_names = {"moni_v3.png", "moni_v2.png"}
    for p in (STATIC / "icons").rglob("*"):
        if not p.is_file() or p.suffix.lower() not in {".png", ".svg"}:
            continue
        rel = p.relative_to(STATIC).as_posix()  # icons/...
        raw = p.read_bytes()
        if p.suffix.lower() == ".png" and (
            p.parent.name == "sources" or p.name in knock_names
        ):
            try:
                raw = knockout_black_bg(raw)
            except Exception as exc:
                print(f"  ! knockout {rel}: {exc}")
        mime = "image/png" if p.suffix.lower() == ".png" else "image/svg+xml"
        uri = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
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

    # v1.0.35: 给文章重新分配 tag, 让 6 个 tag 都有覆盖 (不是单一 banking)
    snap["articles"] = polish_articles_tags(snap["articles"])
    snap["pinned"] = polish_articles_tags(snap["pinned"])

    snap["llm_jobs"] = polish_llm_jobs(snap["llm_jobs"])
    snap["llm_stats"] = recount_llm_stats(snap["llm_jobs"])

    print(f"  articles={len(snap['articles'])} sources={len(snap['sources'])} events_aug={len((snap['events'].get('2026-08') or {}).get('items') or [])}")
    return redact_tree(snap)


DEMO_SUMMARY = (
    "【演示摘要】本文梳理同业最新动态：产品上新、监管口径与落地节奏。"
    "对银行数字化、支付与财富管理条线有直接参考价值。"
)
DEMO_FACTCHECK = (
    "【演示事实核查】结论：核心事实成立。\n"
    "- 标题与正文关键数据可交叉验证\n"
    "- 未发现与公开报道明显矛盾的表述\n"
    "- 建议关注后续监管细则落地时间表"
)

# v1.0.35.16: deterministic fact_check fixture —— 让 demo 重建触发生产 v1.0.35.14 的
# 友好 fact_check modal (核查结论 badge + 证据链卡片 + 可折叠原始 JSON)。
# 主题围绕「数字人民币运营机构扩容 30 家」这种 done 率高、证据丰富的文章。
# 不复用 live 11 条 evidence (grok 明确建议不用 live 数据), 自包含稳定可重现。
DEMO_FACT_CHECK_FIXTURE: dict[str, Any] = {
    "conclusion": "【已核实】核心数据与事件均获多源一致支持",
    "evidence": [
        {
            "query": "人民银行 数字人民币 运营机构 扩容 30家",
            "url": "http://paper.people.com.cn/rmrb/pc/content/202608/18/content_30175613.html",
            "domain": "paper.people.com.cn",
            "snippet": "目前,数字人民币业务运营机构已扩容至30家。新增机构将在完成业务、技术准备后开展数字人民币业务。",
            "rationale": "人民日报逐字核对「30家」总数与新增8家银行名单, 与文章核心数字一致",
            "fetched_at": "2026-08-24T07:29:10.609307+00:00",
            "model": "ark-code-latest",
            "provider": "ark",
            "conclusion": "【已核实】核心数据与事件均获多源一致支持",
            "evidence_seq": 0,
        },
        {
            "query": "央行 新增 平安银行 恒丰银行 渤海银行 数字人民币",
            "url": "http://finance.people.com.cn/n1/2026/0818/c1004-40781318.html",
            "domain": "finance.people.com.cn",
            "snippet": "人民银行新增平安银行、恒丰银行、渤海银行、上海银行、杭州银行、徽商银行、长沙银行、广西北部湾银行成为银行类数字人民币业务运营机构。",
            "rationale": "人民网财经完整列出8家新成员, 名称逐字一致",
            "fetched_at": "2026-08-24T07:29:10.609307+00:00",
            "model": "ark-code-latest",
            "provider": "ark",
            "conclusion": "【已核实】核心数据与事件均获多源一致支持",
            "evidence_seq": 1,
        },
        {
            "query": "数字人民币 运营机构 扩容 30家 十五五规划",
            "url": "https://finance.eastmoney.com/a/202608173843181562.html",
            "domain": "finance.eastmoney.com",
            "snippet": "下一步, 中国人民银行将按照市场化、法治化原则有序推进运营机构扩容。",
            "rationale": "东方财富转引央行口径, 佐证扩容进程与政策方向",
            "fetched_at": "2026-08-24T07:29:10.609307+00:00",
            "model": "ark-code-latest",
            "provider": "ark",
            "conclusion": "【已核实】核心数据与事件均获多源一致支持",
            "evidence_seq": 2,
        },
        {
            "query": "数字人民币 运营机构 首次扩容 12家 中信 光大 2026年4月",
            "url": "https://www.news.cn/money/20260818/c9541e5d105d40ac95aef81e8b0a94e5/c.html",
            "domain": "news.cn",
            "snippet": "2026年4月, 数字人民币业务运营机构新增12家, 达到22家。中信银行、光大银行、华夏银行、民生银行、广发银行、浦发银行、浙商银行等。",
            "rationale": "新华网佐证4月首次扩容12家达22家, 25家演进链条成立",
            "fetched_at": "2026-08-24T07:29:10.609307+00:00",
            "model": "ark-code-latest",
            "provider": "ark",
            "conclusion": "【已核实】核心数据与事件均获多源一致支持",
            "evidence_seq": 3,
        },
    ],
}

# v1.0.35.16: deterministic fact_check fixture for cancelled/failed —— 空证据档
DEMO_FACT_CHECK_EMPTY: dict[str, Any] = {
    "conclusion": "【无需核查】纯观点评论",
    "evidence": [],
}


def polish_articles_tags(articles):
    """v1.0.35: 重新分配 tag 分布, 让 6 个 tag 都有覆盖 (生产真实比例)

    生产真实比例 (按 D8 8/17 跨源统计):
      banking 30% / ai-fintech 25% / regulation 15% / wechat 15%
      / miniprogram 8% / payment 5% / general-tech 2%
    """
    if not articles:
        return articles
    # 6 个 tag 真实呈现比例
    TAG_PROFILE = [
        ("banking", 0.30),
        ("ai-fintech", 0.25),
        ("regulation", 0.15),
        ("wechat", 0.15),
        ("miniprogram", 0.08),
        ("payment", 0.05),
        ("general-tech", 0.02),
    ]
    # 累积区间
    cumulative = []
    acc = 0.0
    for name, p in TAG_PROFILE:
        acc += p
        cumulative.append((acc, name))
    # 给每篇文章按 hash 分桶
    out = []
    for i, a in enumerate(articles):
        a2 = dict(a)
        # 基于 article id hash 稳定分类
        import hashlib
        if a.get("id"):
            h = int(hashlib.md5(a["id"].encode("utf-8")).hexdigest(), 16) / (16**32)
        else:
            h = (i % 100) / 100.0
        # 选主 tag
        chosen = "banking"
        for threshold, name in cumulative:
            if h <= threshold:
                chosen = name
                break
        # v1.0.35: ~30% 文章命中多 tag (更真实): 2 tag 20% + 3 tag 10%
        # 用 hash 的第二位小数决定是否多 tag
        h2 = (h * 1000) % 1.0
        chosen_list = [chosen]
        if h2 < 0.10 and len(TAG_PROFILE) >= 3:
            # 3 个 tag: 主 tag + 2 个其他
            others = [n for n, _ in TAG_PROFILE if n != chosen]
            # 按 hash 选另外 2 个
            idx1 = int((h * 31) % len(others))
            idx2 = int((h * 47) % (len(others) - 1))
            if idx2 >= idx1:
                idx2 += 1
            chosen_list.extend([others[idx1], others[idx2]])
        elif h2 < 0.30:
            # 2 个 tag: 主 tag + 1 个其他
            others = [n for n, _ in TAG_PROFILE if n != chosen]
            idx = int((h * 13) % len(others))
            chosen_list.append(others[idx])

        tags = []
        for tn in chosen_list:
            tags.append({"name": tn, "display_name": {
                "banking": "银行", "ai-fintech": "AI×金融", "regulation": "监管",
                "wechat": "微信生态", "miniprogram": "小程序", "payment": "支付",
                "general-tech": "通用科技",
            }.get(tn, tn), "color": {
                "banking": "#2563EB", "ai-fintech": "#DC2626", "regulation": "#EA580C",
                "wechat": "#7C3AED", "miniprogram": "#10B981", "payment": "#16A34A",
                "general-tech": "#6B7280",
            }.get(tn, "#6B7280"), "keyword_hits": []})
        a2["tags"] = tags
        out.append(a2)
    return out


def polish_llm_jobs(jobs):
    """Contest-facing mix: mostly done with rich fact_check evidence, one running,
    one cancelled. v1.0.35.16: fact_check_plus tasks now attach structured
    ``fact_check`` field so production's v1.0.35.14 friendly modal (核查结论
    badge + 证据链卡片 + 可折叠原始 JSON) renders correctly inside the demo.

    result 改成同构 JSON 字符串 (而不是散文) 以匹配生产 v1.0.35.13+ 的
    call_llm 输出形态; friendly modal 走 ``fact_check`` 字段优先, 原始
    result 仅作为兜底 / 可折叠区展示.
    """
    if isinstance(jobs, dict):
        jobs = jobs.get("items") or []
    jobs = [dict(j) for j in (jobs or [])]
    if not jobs:
        return jobs
    n = len(jobs)
    # 至少 1/3 是 fact_check_plus done, 触发 friendly modal
    target_done_plus = max(8, n // 3)
    plus_count = 0
    for i, j in enumerate(jobs):
        title = j.get("article_title") or "同业动态"
        if i == 0:
            j["status"] = "running"
            j["error"] = None
            j["result"] = None
            j["duration_ms"] = None
            # v1.0.35.16: running 任务也带 fact_check field (生产 mock 一致)
            j["model"] = "ark-code-latest"
            j["provider"] = "ark"
            j["fact_check"] = None
            continue
        # leave a single cancelled row if we have enough, otherwise all done
        if i == n - 1 and n >= 8:
            j["status"] = "cancelled"
            j["error"] = None
            j["result"] = None
            j["model"] = "ark-code-latest"
            j["provider"] = "ark"
            j["fact_check"] = None
            continue
        j["status"] = "done"
        j["error"] = None
        j["model"] = "ark-code-latest"
        j["provider"] = "ark"
        if j.get("task_type") == "summary":
            # summary result keeps narrative format (production path)
            j["result"] = DEMO_SUMMARY
            j["fact_check"] = None
        else:
            # v1.0.35.16: fact_check_plus done → 结构化 fact_check + result 同构 JSON 字符串
            if plus_count < target_done_plus:
                fixture = deepcopy(DEMO_FACT_CHECK_FIXTURE)
                # 补一个时间戳变体,让不同任务看起来有时间差
                base_ts = j.get("created_at") or "2026-08-24T07:29:10"
                j["result"] = json.dumps(fixture, ensure_ascii=False)
                j["fact_check"] = fixture
                plus_count += 1
            else:
                j["result"] = json.dumps(DEMO_FACT_CHECK_EMPTY, ensure_ascii=False)
                j["fact_check"] = deepcopy(DEMO_FACT_CHECK_EMPTY)
        if not j.get("duration_ms") or j["duration_ms"] < 400:
            j["duration_ms"] = 12000 + (i * 137) % 8000
        j["article_title"] = title
    return jobs


def recount_llm_stats(jobs):
    stats = {"pending": 0, "running": 0, "done": 0, "failed": 0, "cancelled": 0}
    for j in jobs or []:
        st = j.get("status") or "done"
        if st in stats:
            stats[st] += 1
        else:
            stats["done"] += 1
    return stats


def download_vendor() -> tuple[str, str]:
    """Read the self-hosted Alpine + GSAP builds (v1.0.47).

    v1.0.47: 原来这里是 CDN 下载 —— 离线 / 内网构建时直接失败, 而且
    跟 index.html 里的 script 标签版本可能对不上。现在直接读仓库里
    自托管的两份文件, 单一事实来源, 构建不再依赖外网。
    """
    vendor_dir = STATIC / "vendor"
    alpine_p = vendor_dir / "alpine-3.14.1.min.js"
    gsap_p = vendor_dir / "gsap-3.12.5.min.js"
    missing = [str(p) for p in (alpine_p, gsap_p) if not p.is_file()]
    if missing:
        raise FileNotFoundError(
            "vendor assets missing: %s — 自托管资源必须先入库" % ", ".join(missing)
        )
    print("Using self-hosted Alpine + GSAP from %s" % vendor_dir)
    return alpine_p.read_text(encoding="utf-8"), gsap_p.read_text(encoding="utf-8")


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
      const tagNames = (detail.tags || []).map((t) => (typeof t === 'string' ? t : (t.name || '')));
      const _mkBody = (lead, source) => {
        const pool = {
          banking: ['文章梳理近期银行业重点动态：存款利率市场化定价机制运行整体平稳，净息差收窄背景下各行加大财富管理、AUM与中收业务投入。信贷投放方面，制造业、普惠小微、绿色金融仍是主要投向，对公房地产风险化解稳步推进。', '财报季观察：六大行资产质量保持稳健，不良率边际改善，拨备覆盖率维持高位。零售端按揭需求处于恢复通道，信用卡不良可控。'],
          'ai-fintech': ['大模型应用持续从概念验证走向落地。头部股份制银行已完成开源模型私有化部署，应用于信贷报告生成、合规审查、智能客服等场景。监管侧对生成式AI在金融场景的使用提出可解释性、数据安全、模型审计等多项要求。', '智能投顾业务进入合规重塑期。监管明确投顾业务持牌经营，多家银行暂停非持牌AI投顾服务，转向人工+AI辅助混合模式。'],
          regulation: ['近期监管机构发布多项新政，重点关注资产管理、跨境数据流动、平台经济治理三大领域。征求意见稿发布后通常预留6个月过渡期，机构需提前布局合规改造。', '金融监管强调功能监管理念，对同类金融业务按实质重于形式原则实施统一监管。'],
          wechat: ['微信生态商业化路径持续深化。公众号、视频号、小程序、企业微信形成内容+服务闭环。私域运营成为零售消费品牌标配，公众号+视频号+企微三层漏斗体系覆盖400+品牌。', '微信读书、视频号直播、问一问等内容生态加速融合。AI搜索与微信搜索对接，传统关键词流量再次被激活。'],
          'general-tech': ['OpenAI、Anthropic等头部实验室持续推进模型迭代。长上下文、多模态模型在企业级RAG场景中表现稳健。开源侧Qwen、DeepSeek、Llama等中文/英文模型生态成熟。', '大模型应用从Chatbot形态向Agent形态演进。Computer Use、Tool Use、Memory成为新焦点。LangChain、LangGraph、AutoGen等编排框架快速迭代。'],
          payment: ['支付行业进入合规深水区。条码支付、刷脸支付、数字人民币与境外卡组织互联互通仍在持续推进。聚合支付服务商面临断直连后的二次合规改造。', '二维码支付跨境试点扩展至更多东盟国家。游客出境使用云闪付、Alipay+ 钱包付款体验持续优化。'],
          miniprogram: ['微信小程序生态进入成熟期。MAU保持稳定增长，电商、本地生活、企业服务三大类目占据主导。微信小程序已加挂AI能力，商家可一键集成智能客服与商品推荐。', '小程序搜索结果在微信主页权重提升。SAA化与低代码工具降低小程序开发门槛。中长尾小程序流量获取成本上升，差异化运营成为突围关键。'],
        };
        const keys = tagNames.length ? tagNames.filter((k) => pool[k]) : ['general-tech'];
        const main = keys[0] || 'general-tech';
        const src = pool[main] || pool['general-tech'];
        const para = src[Math.floor(Math.random() * src.length)];
        return `${lead}\n\n${para}\n\n本文来源：${source}。本段落为展示页内置的正文摘要（原文已落库，可继续生成简报、事实核查或同业案例提取）。`;
      };
      const lead = (detail.title || '').trim();
      const fake_content = _mkBody(lead, detail.source || 'moni');
      const merged = { content: detail.content || fake_content, ...detail };
      if (!merged.content || merged.content.length < 30) merged.content = fake_content;
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


SHOWCASE_CSS = """
/* showcase: icon plates follow theme, not baked-in black */
.source-row .source-icon,
.source-row .source-icon img,
.moni-icon,
.moni-icon img {
  background: var(--stage-bg) !important;
}
.fetch-sources-section td img,
table td img[alt=""],
.td-actions img {
  background: transparent !important;
  object-fit: contain;
}
.source-row .source-icon,
.source-row.focus .source-icon,
.moni-icon {
  border: none !important;
  box-shadow: none !important;
}
"""


SHOWCASE_BOOT = r"""
(function () {
  const TIPS = {
    artifact: '本次为演示页，分享产物没有打包进这个离线 HTML，无法打开真实文件。正式环境会在这里下载或预览简报 / 信息图。',
    article: '本次为演示，无法跳转到该篇文章详情。正式环境会打开对应文章。',
  };
  window.__showcaseShow = function (kind) {
    const el = document.getElementById('showcaseDemo');
    const p = document.getElementById('showcaseDemoText');
    if (p) p.textContent = TIPS[kind] || TIPS.artifact;
    if (el) el.style.display = 'flex';
  };
  window.__showcaseHide = function () {
    const el = document.getElementById('showcaseDemo');
    if (el) el.style.display = 'none';
  };
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') window.__showcaseHide();
  });
  function wrapApp() {
    const inner = window.app;
    if (typeof inner !== 'function' || inner.__showcaseWrapped) return;
    const wrapped = function () {
      const o = inner();
      o.showcaseDemo = function (kind) { window.__showcaseShow(kind); };
      return o;
    };
    wrapped.__showcaseWrapped = true;
    window.app = wrapped;
  }
  wrapApp();
  document.addEventListener('click', function (e) {
    const a = e.target.closest && e.target.closest('a');
    if (!a) return;
    const href = a.getAttribute('href') || '';
    if (
      href.indexOf('/artifacts/') !== -1 ||
      /info_graphic|report_png|html_landing|\.png$|\.html$/i.test(href)
    ) {
      if (href.indexOf('#/') === 0) return;
      e.preventDefault();
      e.stopPropagation();
      window.__showcaseShow('artifact');
    }
  }, true);
})();
"""

SHOWCASE_MODAL = """
    <div id="showcaseDemo" class="modal-backdrop" style="display:none;z-index:200;"
         onclick="if(event.target===this)window.__showcaseHide()">
      <div class="modal-content" style="max-width:440px;">
        <div class="modal-header">
          <h3>演示提示</h3>
          <button class="modal-close" onclick="window.__showcaseHide()" aria-label="关闭">&times;</button>
        </div>
        <div class="modal-body">
          <p id="showcaseDemoText" style="line-height:1.7;"></p>
        </div>
        <div class="modal-footer">
          <button class="btn-primary" onclick="window.__showcaseHide()">知道了</button>
        </div>
      </div>
    </div>
"""


# v1.0.35.16: 演示物料 modal (info_graphic + html_landing) — 从 docs/moni-web-showcase.html
# v1.0.35.1 时代手工 inline 抽回, 现在由生成器注入. assets 来自 scripts/showcase_assets/.
SHOWCASE_ARTIFACTS_DIR = Path(__file__).resolve().parent / "showcase_assets"


def load_showcase_assets() -> dict[str, str]:
    """v1.0.35.16: 读取演示物料真实数据 (info_graphic + html_landing).

    失败时返回空 dict — assemble() 会判断后用占位模式注入。
    """
    out: dict[str, str] = {}
    img_path = SHOWCASE_ARTIFACTS_DIR / "info_graphic.webp"
    html_path = SHOWCASE_ARTIFACTS_DIR / "html_landing.html"
    if img_path.exists():
        b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
        out["info_graphic_png_data"] = f"data:image/webp;base64,{b64}"
    if html_path.exists():
        out["html_landing"] = html_path.read_text(encoding="utf-8")
    return out


def render_showcase_artifacts_modal() -> str:
    """v1.0.35.16: 演示物料 modal HTML (info_graphic + html_landing).

    由 assemble() 在 <div class="toast"> 前注入 demo HTML.
    """
    return """\
    <!-- v1.0.35.16: 演示物料 modal — info_graphic -->
    <div class="modal-backdrop" x-show="showcaseArtifactOpen === 'info_graphic'" x-cloak @click.self="closeShowcaseArtifact()" @keydown.escape.window="closeShowcaseArtifact()">
      <div class="modal-content" style="max-width:880px;height:80vh;display:flex;flex-direction:column;">
        <div class="modal-header">
          <h3>演示物料 · 信息图</h3>
          <span class="muted" style="font-size:0.8em;">格式: <code>info_graphic_png</code></span>
          <button class="modal-close" @click="closeShowcaseArtifact()" aria-label="关闭">&times;</button>
        </div>
        <div class="modal-body" style="overflow:auto;display:flex;align-items:center;justify-content:center;background:var(--muted,#f5f5f4);">
<img id="img-info-graphic" data-img-id="info_graphic_png_data" alt="信息图" style="max-width:100%; height:auto; display:block; margin:0 auto;">
        </div>
        <div class="modal-footer" style="justify-content:space-between;">
          <p class="muted" style="font-size:0.82em;margin:0;">源文件: info_graphic_png (2.4MB 原图, webp 128KB)</p>
          <button class="btn-secondary" @click="closeShowcaseArtifact()">关闭</button>
        </div>
      </div>
    </div>
    <!-- v1.0.35.16: 演示物料 modal — html_landing -->
    <div class="modal-backdrop" x-show="showcaseArtifactOpen === 'html_landing'" x-cloak @click.self="closeShowcaseArtifact()" @keydown.escape.window="closeShowcaseArtifact()">
      <div class="modal-content" style="max-width:920px;height:85vh;display:flex;flex-direction:column;">
        <div class="modal-header">
          <h3>演示物料 · 同业简报 HTML</h3>
          <span class="muted" style="font-size:0.8em;">格式: <code>html_landing</code></span>
          <button class="modal-close" @click="closeShowcaseArtifact()" aria-label="关闭">&times;</button>
        </div>
        <div class="modal-body" style="overflow:hidden;padding:0;background:#fff;">
          <iframe id="iframe-html-landing" data-srcdoc-id="html_landing" style="width:100%; height:100%; border:0; display:block;"></iframe>
        </div>
        <div class="modal-footer">
          <p class="muted" style="font-size:0.82em;margin:0;">2026-08-14 一键简报产物 (单文件 HTML, 离线可读)</p>
          <button class="btn-secondary" @click="closeShowcaseArtifact()">关闭</button>
        </div>
      </div>
    </div>
"""


# v1.0.35.16: 给 app.js bundle 末尾注入 showcaseArtifact state + methods (v1.0.35.1 时代 inline 抽回).
# 让 demo 重建保留原 v1.0.35.1 的演示物料 modal 行为, 而不污染主分支 app.js.
SHOWCASE_ARTIFACT_JS_PATCH = """
// v1.0.35.16: 演示物料 modal state + method (从 docs/moni-web-showcase.html inline 抽回)
;(function(){
  if (!window.__MONI_SNAP__) return;
  const app = Alpine.$data(document.body);
  if (!app) return;
  app.showcaseArtifactOpen = null;
  app.showcaseArtifact = function(formatId) {
    this.showcaseArtifactOpen = formatId;
    this.$nextTick(() => {
      const data = (this._deferredArtifacts ??= (() => {
        try { return JSON.parse(document.getElementById('deferred-artifacts').textContent); }
        catch(e){ return {}; }
      })());
      if (formatId === 'html_landing' && data.html_landing) {
        const iframe = document.getElementById('iframe-html-landing');
        if (iframe && !iframe.srcdoc) iframe.srcdoc = data.html_landing;
      } else if (formatId === 'info_graphic' && data.info_graphic_png_data) {
        const img = document.getElementById('img-info-graphic');
        if (img && !img.src) img.src = data.info_graphic_png_data;
      }
    });
  };
  app.closeShowcaseArtifact = function() { this.showcaseArtifactOpen = null; };
  // v1.0.35.6: 演示模式直接弹演示物料, 不调生产 output_url 流程
  const origOpen = app.openBriefTaskDetail;
  app.openBriefTaskDetail = function(task) {
    const fmt = task && task.brief_format;
    if (fmt === 'info_graphic_png') { this.showcaseArtifact('info_graphic'); return; }
    if (fmt === 'html_landing')    { this.showcaseArtifact('html_landing'); return; }
    if (typeof origOpen === 'function') return origOpen.call(this, task);
  };
  // v1.0.35.6: share output_url 按钮 (打开产物) 重定向到演示物料
  document.querySelectorAll("a.btn-primary").forEach((el) => {
    if (el.textContent.trim() === '打开产物') {
      el.addEventListener('click', (e) => {
        e.preventDefault();
        // 默认演示两个真物料: info_graphic + html_landing
        const fmt = (window.__MONI_SNAP__.share_tasks?.items?.[0]?.brief_format) || 'html_landing';
        const mapped = fmt === 'info_graphic_png' ? 'info_graphic' : 'html_landing';
        this.showcaseArtifact(mapped);
      });
    }
  });
}).call({});
"""


def assemble(html: str, css: str, scripts: list[str], alpine: str, gsap: str, icons: dict[str, str], css_with_var_map: tuple[str, dict[str, str]] | None = None) -> str:
    css = css + "\n" + SHOWCASE_CSS
    # v1.0.35: assemble 拼接后再 strip (SHOWCASE_CSS 里也有 oklch)
    css, _var_map = _strip_oklch(css, (css_with_var_map[1] if css_with_var_map else None))
    # drop external deps
    html = re.sub(r'<link rel="preload"[^>]*>\s*', '', html)
    html = re.sub(r'<link rel="preconnect"[^>]*>\s*', '', html)
    html = re.sub(r'<link href="https://fonts\.googleapis\.com[^"]*"[^>]*>\s*', '', html)
    html = re.sub(r'<link rel="stylesheet" href="css/main\.css[^"]*">', '<style>\n' + css + '\n</style>', html)
    html = replace_icon_paths(html, icons)
    html = html.replace(
        'style="width:24px;height:24px;border-radius:6px;display:block;object-fit:cover;"',
        'style="width:24px;height:24px;border-radius:6px;display:block;object-fit:contain;background:transparent;"',
    )

    # demo-safe clicks
    # v1.0.47: 原来这里做的是整标签精确匹配 —— index.html 加上 safeHref
    # 护栏之后属性顺序/内容都变了, 整标签匹配会静默失配 (换成了危险链接
    # 却没被替换, 演示页点开就是 javascript:)。改成属性级替换, 只盯住
    # 我们真正要改的那一个属性。
    html = html.replace(
        '<a href="#" @click.prevent="navigate(\'articles\')"\n'
        '                     x-text="(job.article_title || job.article_id).slice(0, 50)"></a>',
        '<a href="#" @click.prevent="showcaseDemo(\'article\')"\n'
        '                     x-text="(job.article_title || job.article_id).slice(0, 50)"></a>',
    )
    html = re.sub(
        r':href="(?:safeHref\()?q\.output_url(?:\))?"',
        'href="#" @click.prevent="showcaseDemo(\'artifact\')"',
        html,
    )
    html = html.replace(
        "x-text=\"q.output_url.split('/').pop()\"",
        "x-text=\"(q.output_url || '').split('/').pop()\"",
    )
    html = html.replace(
        ':title="q.output_path || q.output_url"',
        ':title="\'演示产物 · 点击查看说明\'"',
    )
    html = re.sub(
        r':href="(?:safeHref\()?activeBriefTask\.output_url(?:\))?"',
        'href="#" @click.prevent="showcaseDemo(\'artifact\')"',
        html,
    )
    if html.count("showcaseDemo('artifact')") < 1:
        print("  ! share output_url template not patched")
    if "showcaseDemo('article')" not in html:
        print("  ! llm job article link not patched")

    html = html.replace(
        '<div class="toast"',
        SHOWCASE_MODAL + render_showcase_artifacts_modal() + '\n    <div class="toast"',
        1,
    )

    # v1.0.35.16: 演示物料 deferred JSON (info_graphic + html_landing) 注入到 </body> 前
    assets = load_showcase_assets()
    if assets:
        assets_json = json.dumps(assets, ensure_ascii=False)
        # v1.0.35.16.2: html_landing 是完整 HTML 文档, 内部含 </script> ——
        # JSON 里原样保留会提前终止外层 <script type="application/json">,
        # 导致 html_landing 的 </head><body><div class="wrap">... 泄漏成裸 DOM
        # 平铺在每个页面下方. 与 mock_js() 同款, 把 </ 转义成 <\/ (JSON 合法,
        # script 块不早闭).
        assets_json = assets_json.replace("</", "<\\/")
        deferred_block = (
            '\n<script type="application/json" id="deferred-artifacts">'
            + assets_json
            + '</script>'
        )
    else:
        deferred_block = '\n<!-- v1.0.35.16: showcase assets not found, run scripts/extract_showcase_assets.py -->'

    # strip original script tags
    # v1.0.47: Alpine / GSAP 改成自托管 (vendor/)，原来这两条正则匹配的是
    # cdn.jsdelivr.net —— 不改的话 CDN 的 <script defer> 标签会残留在
    # 单文件 showcase 里, 既重复加载又依赖外网。
    html = re.sub(r'<script defer src="vendor/alpine[^"]*"></script>\s*', '', html)
    html = re.sub(r'<script defer src="vendor/gsap[^"]*"></script>\s*', '', html)
    html = re.sub(r'<script src="js/[^"]*"></script>\s*', '', html)

    # start on fetch? keep articles via hash default
    bundle = "\n".join(scripts)
    tail = (
        "<script>\n" + gsap + "\n</script>\n"
        "<script>\n" + bundle + SHOWCASE_ARTIFACT_JS_PATCH + "\n</script>\n"
        "<script>\n" + alpine + "\n</script>\n"
        + deferred_block
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

    # v1.0.35: Chrome 105 等老浏览器无 oklch 支持 — 全部预算成 sRGB hex
    # 两次跑 strip: 第一次扫 CSS 拿到 var_map, 第二次把 var_map 传到整 HTML 处理 JS theme dict
    var_map: dict[str, str] = {}
    before_oklch = css.count("oklch(")
    css, var_map = _strip_oklch(css, var_map)
    print(f"oklch stripped (CSS): {before_oklch} → {css.count('oklch(')} (var_map size={len(var_map)})")

    js_files = [
        "toast.js", "modal.js", "safe_url.js", "calendar.js", "cases.js",
        "stats.js", "theme.js", "app.js", "icons.js", "pin_anim.js",
    ]
    scripts = [mock_js(snap)]
    for name in js_files:
        text = (STATIC / "js" / name).read_text(encoding="utf-8")
        if name == "app.js":
            text = patch_app_js(text)
        text = replace_icon_paths(text, icons)
        scripts.append(f"/* ===== {name} ===== */\n{text}")
    scripts.append("/* ===== showcase boot ===== */\n" + SHOWCASE_BOOT)

    out = assemble(html, css, scripts, alpine, gsap, icons, (css, var_map))
    # v1.0.35: 处理 mock_js snapshot + JS theme dict (line 8049+) 里的 oklch
    # 这些是 JS string 'oklch(...)', 不在 CSS 路径里. var_map 累计传过去
    js_oklch = out.count("oklch(")
    if js_oklch:
        out, _ = _strip_oklch(out, var_map)
        js_after = out.count("oklch(")
        print(f"  oklch in assembled HTML (JS): {js_oklch} → {js_after}")
    # escape accidental </script> in vendor
    # already handled in JSON; alpine/gsap typically safe
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(out, encoding="utf-8")
    mb = OUT.stat().st_size / (1024 * 1024)
    print(f"Wrote {OUT} ({mb:.2f} MB)")


if __name__ == "__main__":
    main()
