"""tests/test_frontend_microinteraction.py — sprint-25 B 组契约断言 (v1.0.45).

覆盖 PLAN §4 Phase 2 的 P0 / P1 批次:

  2a 无障碍    B01 全局焦点环 / B04 pin 键盘焦点
  2b 性能卫生  B03 去常驻 will-change / B19 触屏 hover 守卫
  2c 主题一致  B07 表格 hover 走 token
  2d 反馈对称  B12-B17
  2e 结构转场  B09 批量栏进入 / B10 视图切换

每条都遵守 sprint-24 立的规矩: 新增的 motion 规则必须落进 main.css 末尾
那一个 prefers-reduced-motion 汇总块, 不新开块。焦点环是**功能**不是装饰,
所以 reduced-motion 下必须保留 —— 这条单独钉住。
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "moni" / "web" / "static"
MAIN_CSS = STATIC / "css" / "main.css"
INDEX_HTML = STATIC / "index.html"
APP_JS = STATIC / "js" / "app.js"


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
    out: list[str] = []
    i, n = 0, len(js)
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


def _css_rules(css: str) -> list[tuple[str, str]]:
    clean = _strip_css_comments(css)
    rules: list[tuple[str, str]] = []
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
        rules.append((selector, clean[brace + 1:j - 1]))
        i = j
    return rules


def _sel_set(s: str) -> set[str]:
    return {x.strip() for x in s.split(",") if x.strip()}


def _consolidated_reduce_block() -> tuple[str, str] | None:
    """The LAST prefers-reduced-motion block (the sprint-24 consolidated one)."""
    blocks = [
        (sel, body) for sel, body in _css_rules(MAIN_CSS.read_text(encoding="utf-8"))
        if "prefers-reduced-motion" in sel
    ]
    if not blocks:
        return None
    return blocks[-1]


def _reduce_selectors() -> set[str]:
    blk = _consolidated_reduce_block()
    if not blk:
        return set()
    out: set[str] = set()
    for chunk in blk[1].split("}"):
        head = chunk.split("{")[0].strip()
        if head:
            out |= _sel_set(head)
    return out


# ---------------------------------------------------------------------------
# 2a 无障碍 — B01 / B04 焦点环 (P0)
# ---------------------------------------------------------------------------


def test_global_focus_visible_ring_exists():
    """Keyboard focus must be visible on every interactive control.

    Uses :focus-visible so a mouse click does not leave a ring behind, with a
    :focus fallback for engines that lack it.
    """
    css = _strip_css_comments(MAIN_CSS.read_text(encoding="utf-8"))
    assert ":focus-visible" in css, "no :focus-visible ring defined"

    # Must be the PRIMARY rule (a real selector list), not the @supports
    # fallback block — the fallback uses :focus and would keep this green
    # after the real ring was deleted.
    #
    # P2-2: match the control EXACTLY. A plain `".btn" in sel` substring test
    # is also satisfied by `.btn-primary` / `.btn-secondary` / …, so renaming
    # only the `.btn` entry left this green — which is why MP-5 could not bite
    # here even though the sibling test (focus_ring_covers) went red on the
    # same mutation. Use a boundary-anchored pattern and require the bare
    # `.btn` selector to be present in its own right.
    primary = []
    for sel, body in _css_rules(css):
        if sel.startswith("@") or ":focus-visible" not in sel:
            continue
        # split the comma list and look at each selector on its own
        for part in (x.strip() for x in sel.split(",")):
            if not part:
                continue
            # `.btn` exactly — not `.btn-primary`, not `.btn:focus`
            if re.fullmatch(r"\.btn:focus-visible", part):
                primary.append(body)
                break
    assert primary, (
        "no top-level rule contains the bare `.btn:focus-visible` selector "
        "(only variants like .btn-primary, or the @supports fallback)"
    )
    outlines = re.findall(r"outline\s*:\s*([^;}]+)", " ".join(primary))
    assert outlines, ":focus-visible rule has no outline declaration"
    for val in outlines:
        assert "none" not in val, f"focus ring must not be outline: {val}"


def test_mouse_click_leaves_no_focus_ring():
    """点击就出紫框是回归过的问题（#186 的全局 token 把 :focus 也挂上了环）。

    失败模式：全局焦点规则里出现裸 `:focus` 选择器 —— 鼠标点任何按钮都会
    留一圈描边，看起来像选中态；正确做法是只挂 :focus-visible。
    """
    css = _strip_css_comments(MAIN_CSS.read_text(encoding="utf-8"))
    globals_ = [sel for sel, _ in _css_rules(css) if ":where(" in sel and "focus" in sel]
    assert globals_, "global :where(...):focus-visible token not found"
    bare = [p.strip() for sel in globals_ for p in sel.split(",") if p.strip().endswith(":focus")]
    assert not bare, (
        f"global focus rule targets bare :focus {bare} — mouse clicks would "
        "leave a ring behind"
    )


def test_focus_ring_is_not_accent_coloured():
    """焦点环用中性色；accent(浅紫藤萝) 当描边和选中态撞脸。"""
    css = _strip_css_comments(MAIN_CSS.read_text(encoding="utf-8"))
    offenders = [
        sel.strip()
        for sel, body in _css_rules(css)
        if "focus" in sel and re.search(r"outline\s*:\s*[^;}]*var\(--accent\)", body)
    ]
    assert not offenders, f"focus rings still use --accent: {offenders}"


def test_focus_ring_covers_the_interactive_controls():
    css = _strip_css_comments(MAIN_CSS.read_text(encoding="utf-8"))
    selectors_needed = [".btn", ".pin-btn", ".link-btn", ".tag-chip", ".subtab"]
    missing = []
    for sel in selectors_needed:
        found = any(
            re.search(rf"(?:^|[\s,]){re.escape(sel)}(?=[\s,{{:.>\[]|$)", s)
            and "focus-visible" in s
            for s, _ in _css_rules(css)
        )
        if not found:
            missing.append(sel)
    assert not missing, f"no :focus-visible rule for: {missing}"


def test_focus_ring_survives_reduced_motion():
    """Focus visibility is accessibility, not decoration — keep it under reduce.

    Guards against a future blanket `* { outline: none }` inside the reduce
    block, which would silently break keyboard navigation again.
    """
    blk = _consolidated_reduce_block()
    assert blk is not None, "consolidated reduced-motion block not found"
    body = blk[1]
    assert not re.search(r"(^|[;{\s])(\*|html|body|:focus|:focus-visible)\s*\{?[^}]*outline\s*:\s*none", body), (
        "reduced-motion block removes focus outlines — that breaks keyboard a11y"
    )
    assert not re.search(r"outline\s*:\s*(none|0)\s*;?\s*\}", body), (
        "reduced-motion block zeroes outlines globally"
    )


# ---------------------------------------------------------------------------
# 2b 性能卫生 — B03 will-change / B19 hover 守卫
# ---------------------------------------------------------------------------


def test_no_permanent_will_change_on_buttons():
    """Every `.btn` permanently owning a compositor layer costs memory.

    A table with N buttons created N layers. B03: drop it; the transform
    transition already promotes on demand.
    """
    css = _strip_css_comments(MAIN_CSS.read_text(encoding="utf-8"))
    btn_blocks = [
        body
        for sel, body in _css_rules(css)
        if not sel.startswith("@") and re.search(r"\.btn\b", sel)
    ]
    offenders = [b for b in btn_blocks if "will-change" in b]
    assert not offenders, (
        f"{len(offenders)} .btn rule(s) still set will-change: "
        f"{offenders[:1]!r}"
    )


def test_hover_lifts_are_guarded_by_hover_hover():
    """On touch devices a :hover lift sticks after the tap.

    B19: wrap the translateY hover rules in @media (hover: hover).
    """
    css = _strip_css_comments(MAIN_CSS.read_text(encoding="utf-8"))
    hover_media = re.search(
        r"@media\s*\(hover\s*:\s*hover\)\s*\{", css
    )
    assert hover_media, (
        "no @media (hover: hover) guard — hover lifts stick on touch devices"
    )

    # Every :hover rule declaring translateY must live inside that media block.
    blocks = _css_rules(css)
    in_hover_media: set[int] = set()
    idx = 0
    for sel, body in blocks:
        idx += 1
        if "hover: hover" in sel:
            in_hover_media.add(idx)

    offenders = []
    for i, (sel, body) in enumerate(blocks, 1):
        if sel.startswith("@") or i in in_hover_media:
            continue
        if ":hover" in sel and re.search(r"translateY\(\s*-\d", body):
            offenders.append(sel[:70])
    assert not offenders, (
        f"hover lifts outside @media (hover: hover): {offenders}"
    )


# ---------------------------------------------------------------------------
# 2c 主题一致 — B07 表格 hover 走 token
# ---------------------------------------------------------------------------


def test_table_hover_uses_token_not_hardcoded_hex():
    """Hardcoded hex breaks imported themes; the rest of the file uses tokens."""
    css = _strip_css_comments(MAIN_CSS.read_text(encoding="utf-8"))
    offenders = []
    for sel, body in _css_rules(css):
        if sel.startswith("@"):
            continue
        if "tbody tr:hover" not in sel:
            continue
        for m in re.finditer(r"background\s*:\s*([^;}]+)", body):
            val = m.group(1).strip()
            if re.fullmatch(r"#[0-9A-Fa-f]{3,8}", val):
                offenders.append(f"{sel[:50]} -> {val}")
    assert not offenders, f"table hover still hardcodes hex: {offenders}"


def test_row_hover_token_is_defined_in_root():
    css = _strip_css_comments(MAIN_CSS.read_text(encoding="utf-8"))
    root = re.search(r":root\s*\{([^}]*)\}", css)
    assert root, ":root block not found"
    assert re.search(r"--row-hover\s*:", root.group(1)), (
        "--row-hover token missing from :root"
    )


# ---------------------------------------------------------------------------
# 2d 反馈对称 — B12~B17
# ---------------------------------------------------------------------------


def test_fetch_button_pending_uses_labelled_width():
    """Crossfading '全部抓取' -> '抓取中...' must not make the button resize."""
    css = _strip_css_comments(MAIN_CSS.read_text(encoding="utf-8"))
    assert re.search(r"\.btn-primary\s*\{[^}]*min-width", css), (
        "fetch button has no min-width — swapping label text will jitter it"
    )


def test_toast_exit_is_symmetric_with_entry():
    """Entering has a transform; leaving must mirror it, not just fade."""
    css = _strip_css_comments(MAIN_CSS.read_text(encoding="utf-8"))
    toast = re.search(r"\.toast\s*\{([^}]*)\}", css)
    assert toast, ".toast base rule missing"
    assert "translateY" in toast.group(1), "toast has no entry offset to mirror"
    assert re.search(r"\.toast\.show\s*\{[^}]*translateY\(0\)", css), (
        ".toast.show must land at translateY(0)"
    )


def test_modal_has_exit_transition():
    """v1.0.44 only animated the entrance; a close with no exit reads as a jump."""
    css = _strip_css_comments(MAIN_CSS.read_text(encoding="utf-8"))
    assert re.search(r"\.modal-content\.fetch-modal[^{]*\{[^}]*transition", css), (
        "fetch modal has no transition property — exit cannot animate"
    )
    assert re.search(r"\.modal-backdrop\s*\{[^}]*transition", css), (
        "backdrop has no transition — it cannot fade out"
    )


def test_elapsed_readout_is_tabular():
    """Seconds ticking 9->10 must not shift the layout."""
    css = _strip_css_comments(MAIN_CSS.read_text(encoding="utf-8"))
    m = re.search(r"\.pixel-elapsed\s*\{([^}]*)\}", css)
    assert m is not None, "no .pixel-elapsed rule found"
    assert "tabular-nums" in m.group(1), (
        ".pixel-elapsed must use font-variant-numeric: tabular-nums"
    )


# ---------------------------------------------------------------------------
# 2e 结构转场 — B09 批量栏 / B10 视图切换
# ---------------------------------------------------------------------------


def test_bulk_bar_has_enter_animation():
    """Restrict to non-@media rules.

    A naive substring search also matches `.bulk-bar` inside the reduced-motion
    block (`animation: none`), so deleting the real enter rule left the test
    green — a mutation probe caught it.
    """
    css = _strip_css_comments(MAIN_CSS.read_text(encoding="utf-8"))
    # Explicit, readable form:
    found = False
    for sel, body in _css_rules(css):
        if sel.startswith("@"):
            continue
        if re.search(r"(?:^|[\s,])\.bulk-bar(?:\s*$|[\s,{:.>\[])", sel):
            if re.search(r"animation\s*:", body):
                found = True
                break
    assert found, ".bulk-bar appears instantly — no enter transition"


def test_bulk_bar_enter_is_reduced():
    assert ".bulk-bar" in _reduce_selectors(), (
        ".bulk-bar animates but is absent from the consolidated reduce block"
    )


def test_view_transition_api_is_used_and_reduce_gated():
    """B10 uses the native View Transitions API (no GPU, no dependency).

    It must be gated on prefers-reduced-motion in JS: the CSS pseudo-elements
    alone cannot skip the snapshot work.
    """
    js = _strip_js_comments(APP_JS.read_text(encoding="utf-8"))
    m = re.search(r"navigate\s*\([^)]*\)\s*\{", js)
    assert m, "navigate() not found"
    body = js[m.start() : m.start() + 3000]
    assert "startViewTransition" in body, (
        "navigate() does not use document.startViewTransition"
    )
    # The gate must be USED, not merely computed: removing `!wantsReduced &&`
    # while leaving the (now dead) query would otherwise pass.
    gate = re.search(r"const\s+canTransition\s*=\s*([^;]+);", body)
    assert gate, "canTransition not found in navigate()"
    assert "wantsReduced" in gate.group(1), (
        "canTransition ignores the reduced-motion result — the transition "
        f"would run even when the user asked for less motion: {gate.group(1)!r}"
    )
    assert "prefers-reduced-motion" in body, (
        "view transition is not gated on prefers-reduced-motion in JS"
    )


def test_no_new_reduce_blocks():
    """sprint-24 left 4 real @media blocks; new motion goes into the last one.

    Counts @media RULES, not raw substring hits: prose inside a comment that
    names the query would otherwise inflate the count and fail this for the
    wrong reason.
    """
    css = _strip_css_comments(MAIN_CSS.read_text(encoding="utf-8"))
    blocks = [s for s, _ in _css_rules(css) if "prefers-reduced-motion" in s]
    assert len(blocks) == 4, (
        f"{len(blocks)} prefers-reduced-motion blocks (baseline 4): {blocks}"
    )


def test_view_transition_pseudos_are_defined():
    css = _strip_css_comments(MAIN_CSS.read_text(encoding="utf-8"))
    assert "::view-transition-old(" in css or "::view-transition-new(" in css, (
        "view-transition pseudo-elements are never styled — the API would "
        "produce a default cross-fade with no easing"
    )
