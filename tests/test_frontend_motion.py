"""tests/test_frontend_motion.py — sprint-25 静态契约断言 (v1.0.45).

锁住本 sprint 的两条线:

A1-a — 抓取弹窗粒子场 Canvas2D 优化 (Phase 1)
  - 粒子数必须有硬上限 (现状无上限, 长时间停留持续累积)
  - 上限必须是显式常量, 不能是散落的魔数
  - 裁剪必须发生在「出生处」而不是「每帧 filter」, 否则无上限增长仍在
  - 不能因此改动每帧 update+draw 的语义 (视觉不变)

B 组 — 微交互的 reduce-motion 归口 (Phase 2)
  - 任何新增 animation / transition 规则都必须落在 main.css 末尾那一个
    prefers-reduced-motion 汇总块里 (sprint-24 立的规矩)
  - 不得新开 reduce 块 (会让「唯一入口」的约定失效)

刻意不做的事:
  - 不测 FPS。rAF 被 vsync 锁死, 只要每帧活儿塞得进 16.7ms 就恒报 60,
    在 headless 里测不出回归 (warden R1, sprint-25 实测确认)。
    真实门禁是 scripts/evidence/s25_fps_probe.mjs 打的
    workPerFrameMs.p95, 不在这里。
  - 不测像素/观感, 那是 dad 的事。
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "moni" / "web" / "static"
APP_JS = STATIC / "js" / "app.js"
MAIN_CSS = STATIC / "css" / "main.css"

# dad 2026-10-12 拍板的封顶值 (PLAN §2.1): 200, 替换先前的 180。
# warden 实测改前活粒子在 89~173 之间波动, 180 会削掉观测峰值 (属观感改变);
# 200 高于所有观测峰值, 正常使用下不可见, 只封住无上界的情况。
EXPECTED_PARTICLE_CAP = 200


# ---------------------------------------------------------------------------
# helpers — 与 sprint-24 的测试共用同一套解析口径
# ---------------------------------------------------------------------------


def _strip_css_comments(css: str) -> str:
    out: list[str] = []
    i, n, in_comment = 0, len(css), False
    while i < n:
        if not in_comment and css.startswith("/*", i):
            in_comment = True
            out.append("  ")
            i += 2
            continue
        if in_comment:
            if css.startswith("*/", i):
                in_comment = False
                out.append("  ")
                i += 2
                continue
            out.append("\n" if css[i] == "\n" else " ")
            i += 1
            continue
        out.append(css[i])
        i += 1
    return "".join(out)


def _strip_js_comments(js: str) -> str:
    """Blank out JS comments while preserving line structure and byte offsets.

    Must be **stateful across lines**: app.js contains template literals like
    `` `translateY(${targetTop}px) scale(${targetScale})` `` whose
    interpolations hold quotes, and backticks inside // comments. A
    per-line scanner loses its quote state at the first such line and then
    treats the remainder of the FILE as one comment, silently emptying it —
    which makes every downstream regex fail for reasons that have nothing to
    do with the code under test.
    """
    out: list[str] = []
    i, n = 0, len(js)
    # None = code, otherwise the open string delimiter (' " `).
    quote: str | None = None
    while i < n:
        ch = js[i]
        if quote is not None:
            if ch == "\\":
                out.append(js[i : i + 2])
                i += 2
                continue
            if ch == quote:
                quote = None
            out.append(ch)
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if js.startswith("//", i):
            end = js.find("\n", i)
            end = n if end < 0 else end
            out.append(" " * (end - i))
            i = end
            continue
        if js.startswith("/*", i):
            end = js.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append(
                "".join("\n" if c == "\n" else ("\t" if c == "\t" else " ")
                        for c in js[i:end])
            )
            i = end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _css_rules(css: str) -> list[tuple[str, str, str]]:
    clean = _strip_css_comments(css)
    rules: list[tuple[str, str, str]] = []
    i, n = 0, len(clean)
    while i < n:
        brace = clean.find("{", i)
        if brace < 0:
            break
        selector = clean[i:brace].strip()
        depth, j = 1, brace + 1
        while j < n and depth > 0:
            if clean[j] == "{":
                depth += 1
            elif clean[j] == "}":
                depth -= 1
            j += 1
        rules.append((selector, clean[brace + 1:j - 1], selector))
        i = j
    return rules


def _reduce_blocks(css: str) -> list[tuple[str, str]]:
    """(selector_text, body) for every prefers-reduced-motion media block."""
    out = []
    for selector, body, raw in _css_rules(css):
        if "prefers-reduced-motion" in raw:
            out.append((raw, body))
    return out


# ---------------------------------------------------------------------------
# A1-a — particle cap
# ---------------------------------------------------------------------------


def test_particle_cap_constant_exists():
    """The cap must be a named constant, not a literal buried in a loop."""
    src = _strip_js_comments(APP_JS.read_text(encoding="utf-8"))
    m = re.search(r"const\s+(MAX_[A-Z_]*PARTICLES[A-Z_]*)\s*=\s*(\d+)\s*;", src)
    assert m, "no MAX_*PARTICLES constant found in app.js"
    assert int(m.group(2)) == EXPECTED_PARTICLE_CAP, (
        f"particle cap is {m.group(2)}, dad pinned {EXPECTED_PARTICLE_CAP} (PLAN 2.1)"
    )


def test_particle_cap_is_enforced_at_birth():
    """Cap must be applied where particles are pushed.

    If the cap were only enforced in the per-frame filter, the array would
    still grow without bound between frames — which is exactly the leak this
    change is meant to close.
    """
    src = _strip_js_comments(APP_JS.read_text(encoding="utf-8"))
    push = re.search(r"_particles\.push\([^)]*\)\s*;?", src)
    assert push, "_particles.push not found"

    # Find the enclosing function body around the push.
    start = src.rfind("_spawnBurst", 0, push.start())
    assert start != -1, "push is not inside _spawnBurst"
    body = src[start:push.end() + 260]

    assert re.search(r"_particles\.length\s*>=", body), (
        "particle cap is not checked at birth (_spawnBurst); "
        "enforcing it only in the per-frame filter still lets the array grow"
    )
    assert re.search(r"_particles\.(?:splice|shift|pop)\s*\(", body), (
        "no eviction when over cap — the oldest particle must be dropped"
    )


def test_cap_is_not_silently_dropped_by_filter():
    """The per-frame filter must not be the only cap mechanism."""
    src = _strip_js_comments(APP_JS.read_text(encoding="utf-8"))
    filter_line = re.search(r"_particles\s*=\s*this\._particles\.filter\([^;]*;", src)
    assert filter_line, "per-frame alive filter removed — must keep culling dead particles"
    # The filter's job stays narrow: cull by .alive, not by index arithmetic.
    assert "alive" in filter_line.group(0), (
        "the per-frame filter should only drop dead particles; length-based "
        "capping belongs at birth"
    )


def test_particle_burst_multiplier_preserved():
    """A1-a is a density cap, not a visual change — keep the 2.6x burst."""
    src = _strip_js_comments(APP_JS.read_text(encoding="utf-8"))
    m = re.search(r"Math\.ceil\(count\s*\*\s*([\d.]+)\)", src)
    assert m, "burst multiplier expression not found"
    assert abs(float(m.group(1)) - 2.6) < 1e-9, (
        f"burst multiplier changed from 2.6 to {m.group(1)} — that changes "
        "the visual density, not just the ceiling"
    )


def test_per_frame_loop_semantics_unchanged():
    """update+draw per particle must stay a plain forEach (no new work)."""
    src = _strip_js_comments(APP_JS.read_text(encoding="utf-8"))
    m = re.search(
        r"_animateParticles\(\)\s*\{[\s\S]{0,900}?this\._particles\.forEach\("
        r"([\s\S]{0,200}?)\}\s*\)",
        src,
    )
    assert m, "per-particle forEach no longer found in _animateParticles"
    body = m.group(1)
    assert "update" in body and "draw" in body, (
        f"per-particle loop no longer calls both update and draw: {body!r}"
    )
    assert "clearRect" in src, "per-frame clearRect was removed"


# ---------------------------------------------------------------------------
# B 组 — reduced-motion 归口 (sprint-24 的「唯一入口」约定)
# ---------------------------------------------------------------------------


def test_no_new_reduce_blocks_added():
    """sprint-24 consolidated 3 scattered blocks into one tail block.

    Adding a 6th block for the new animations would silently break the
    'exactly one place to add a new animated rule' contract.
    """
    css = MAIN_CSS.read_text(encoding="utf-8")
    blocks = _reduce_blocks(css)
    # Baseline is 5 occurrences: 4 legacy + 1 consolidated tail block.
    assert len(blocks) <= 5, (
        f"found {len(blocks)} prefers-reduced-motion blocks; sprint-24 left 5. "
        "New animations must be added to the consolidated tail block, not a new one"
    )


def test_consolidated_reduce_block_is_the_last_media_query():
    """The consolidated block must stay last so later rules cannot win over it."""
    css = _strip_css_comments(MAIN_CSS.read_text(encoding="utf-8"))
    last_media = None
    for m in re.finditer(r"@media\s+([^{]*)", css):
        last_media = m
    assert last_media is not None, "no @media in main.css"
    assert "prefers-reduced-motion" in last_media.group(1), (
        f"last @media is {last_media.group(1).strip()!r}, not the reduced-motion "
        "block — a later media query could override the reduce rules"
    )


def test_new_microinteraction_selectors_are_reduced():
    """Each B-group selector that declares motion must be killed under reduce.

    Uses the same selector-set comparison as the sprint-24 hygiene test so the
    two cannot drift apart.
    """
    css = MAIN_CSS.read_text(encoding="utf-8")
    rules = _css_rules(css)

    def sel_set(s: str) -> set[str]:
        return {x.strip() for x in s.split(",") if x.strip()}

    reduce_sel: set[str] = set()
    for raw, body, sel in rules:
        if "prefers-reduced-motion" in raw:
            for chunk in body.split("}"):
                head = chunk.split("{")[0].strip()
                if head:
                    reduce_sel |= sel_set(head)

    # B-group selectors that get motion (per PLAN §4 Phase 2 batches).
    motion_selectors = [
        ".bulk-bar",
        ".empty-state",
        ".modal-content.fetch-modal",
    ]

    unmotioned = []
    for sel in motion_selectors:
        has_motion = any(
            re.search(rf"(?:^|[\s,]){re.escape(sel)}(?=[\s,{{:.>\[]|$)", body)
            and re.search(r"(?:^|;)\s*(?:animation|transition)\s*:", body)
            for raw_s, body, raw in rules
            if not raw.startswith("@")
        )
        if has_motion and sel not in reduce_sel:
            unmotioned.append(sel)

    assert not unmotioned, (
        f"these selectors declare motion but are absent from the reduced-motion "
        f"block: {unmotioned!r}"
    )


def test_reduce_block_still_keeps_toast_transition_semantics():
    """Guard the sprint-24 decision against a future '* { transition: none }'."""
    css = MAIN_CSS.read_text(encoding="utf-8")
    for _, body in _reduce_blocks(css):
        assert not re.search(r"(^|[;{\s])(\*|html|body)\s*\{[^}]*transition\s*:\s*none", body), (
            "reduce block uses a global transition kill — too broad"
        )