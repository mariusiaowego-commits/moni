"""Read-only UI sweep for moni: routes, data, dashboard, desensitize lab, responsive.

Usage:
    MONI_BASE=http://127.0.0.1:8901 \
    MONI_EVIDENCE_DIR=docs/moni-screenshots-verify-moni-20261125 \
    /usr/local/bin/python3 .codex/skills/verify-moni/scripts/verify_moni.py

Exit 1 when any check fails. SKIPPED checks never count as passed.
"""

import json
import os
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("MONI_BASE", "http://127.0.0.1:8901").rstrip("/")
OUT = Path(
    os.environ.get(
        "MONI_EVIDENCE_DIR",
        f"docs/moni-screenshots-verify-moni-{date.today():%Y%m%d}",
    )
)
OUT.mkdir(parents=True, exist_ok=True)

ROUTES = [
    ("/", "首页"),
    ("/fetch", "信源管理"),
    ("/articles", "文章查阅"),
    ("/calendar", "行业日历"),
    ("/cases", "案例库"),
    ("/stats", "量化看板"),
    ("/crud", "数据维护"),
    ("/cleaning", "数据清洗"),
    ("/llmJobs", "LLM任务"),
    ("/share", "分享"),
    ("/basket", "菜篮子"),
    ("/config", "配置管理"),
]

NOISE = re.compile(r"Transition was skipped")

# Known-baseline console errors, reproducible on production 8900 on 2026-11-25.
# Anything outside this set fails the route. Re-check the baseline when it drifts.
KNOWN_BASELINE = {
    "/basket": {
        "Failed to load resource: the server responded with a status of 422 (Unprocessable Entity)"
    },
}

results = []


def rec(kind, name, passed, detail, evidence=None):
    results.append(
        {
            "kind": kind,
            "name": name,
            "status": "PASS" if passed else "FAIL",
            "detail": detail,
            "evidence": str(evidence) if evidence else None,
        }
    )


def skip(name, reason):
    results.append({"kind": "skipped", "name": name, "status": "SKIPPED", "reason": reason})


def norm(text):
    return re.sub(r"\s+", " ", re.sub(r"\d+", "N", text)).strip()[:180]


def api_json(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode())


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1000})

    console = []
    page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console.append("pageerror: " + str(e)))

    def errors_seen():
        real = [e for e in console if not NOISE.search(e)]
        return real, [e for e in console if NOISE.search(e)]

    for path, name in ROUTES:
        console.clear()
        try:
            resp = page.goto(BASE + path, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)
            nav = page.locator(".topnav-links a").count()
            body = page.inner_text("body")[:300]
            bad = [m for m in ("加载失败", "is not defined", "Failed to fetch") if m in body]
            shot = OUT / f"page_{(path.strip('/') or 'root').replace('/', '_')}.png"
            page.screenshot(path=str(shot))
            real, noise = errors_seen()
            unexpected = [e for e in real if e not in KNOWN_BASELINE.get(path, set())]
            ok = bool(resp and resp.status == 200) and nav >= 10 and not bad and not unexpected
            rec(
                "route",
                f"{name} {path}",
                ok,
                f"http={resp.status if resp else '?'} nav_links={nav} "
                f"console_real={len(real)} console_unexpected={len(unexpected)} "
                f"console_noise={len(noise)} body_bad={bad}",
                shot,
            )
        except Exception as exc:
            rec("route", f"{name} {path}", False, f"exception: {exc}")

    # 文章列表有真实数据
    console.clear()
    try:
        page.goto(BASE + "/articles", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2500)
        rows = page.locator("table tbody tr").count()
        shot = OUT / "articles_rows.png"
        page.screenshot(path=str(shot))
        rec("data", "文章查阅有数据", rows > 0, f"tbody_rows={rows}", shot)
    except Exception as exc:
        rec("data", "文章查阅有数据", False, f"exception: {exc}")

    # 量化看板 LLM 用量卡片必须与 API 一致
    console.clear()
    try:
        page.goto(BASE + "/stats", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(6000)
        card = page.locator("#stats-llm-usage")
        rendered = card.locator(".stats-module-primary").inner_text().strip()
        totals = api_json("/api/stats/llm-usage?period=30d")["totals"]
        expected = f"{totals['calls']:,}"
        shot = OUT / "stats_llm_usage.png"
        page.screenshot(path=str(shot))
        real, _ = errors_seen()
        rec(
            "data",
            "LLM 用量卡片与接口一致",
            rendered.startswith(expected) and not real,
            f"card={rendered!r} api_calls={totals['calls']} console_real={len(real)}",
            shot,
        )
    except Exception as exc:
        rec("data", "LLM 用量卡片与接口一致", False, f"exception: {exc}")

    # 脱敏演练台：四栏 + 本地预览（不出网）
    console.clear()
    try:
        page.goto(BASE + "/desens-lab.html", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(1200)
        cols = page.locator(".cols .col").count()
        page.click("#sample")
        page.click("#preview")
        page.wait_for_function(
            "document.getElementById('status').textContent.includes('遮蔽')", timeout=20000
        )
        c1, c2, c3 = (page.inner_text(s) for s in ("#c1", "#c2", "#c3"))
        status = page.inner_text("#status")
        pill = page.inner_text("#switchState")
        shot = OUT / "desens_preview.png"
        page.screenshot(path=str(shot))
        ok = (
            cols == 4
            and "13800138000" in c1
            and "13800138000" not in c2
            and "{{" in c2
            and c3.strip() == "—"
            and "总开关 关" in pill
        )
        rec(
            "desens",
            "演练台四栏 + 本地预览",
            ok,
            f"cols={cols} c1_plain={'13800138000' in c1} c2_plain={'13800138000' in c2} "
            f"c2_placeholder={'{{' in c2} c3={c3.strip()[:12]} status={status} pill={pill}",
            shot,
        )
    except Exception as exc:
        rec("desens", "演练台四栏 + 本地预览", False, f"exception: {exc}")

    for width in (1600, 1280, 1100, 820):
        try:
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(350)
            sw = page.evaluate("document.documentElement.scrollWidth")
            vw = page.evaluate("window.innerWidth")
            shot = OUT / f"resp_{width}.png"
            page.screenshot(path=str(shot))
            rec("responsive", f"演练台 {width}px", sw <= vw + 1, f"scrollWidth={sw} innerWidth={vw}", shot)
        except Exception as exc:
            rec("responsive", f"演练台 {width}px", False, f"exception: {exc}")

    browser.close()

skip("文章摘要 / 事实核查 / 简报（真实模型）", "花模型额度并写库；用 scripts/real_model_sample.py 并在当轮获得授权")
skip("主界面 1024px 窄屏表格行为", "未覆盖：只测了演练台断点")

summary = {
    "total": len(results),
    "pass": sum(1 for r in results if r["status"] == "PASS"),
    "fail": sum(1 for r in results if r["status"] == "FAIL"),
    "skipped": sum(1 for r in results if r["status"] == "SKIPPED"),
}
(OUT / "result.json").write_text(
    json.dumps({"base": BASE, "summary": summary, "results": results}, ensure_ascii=False, indent=1),
    encoding="utf-8",
)
print(json.dumps(summary, ensure_ascii=False))
print("EVIDENCE_DIR " + str(OUT))
sys.exit(1 if summary["fail"] else 0)
