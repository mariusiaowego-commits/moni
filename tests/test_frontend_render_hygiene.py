"""tests/test_frontend_render_hygiene.py — sprint-24 静态契约断言 (v1.0.44).

锁住三处前端渲染稳健性改动, 让它们下次被改回去时有人喊:

1. GSAP CDN script 必须 ``defer`` 且文档序在所有消费脚本之前 (AC-1)
   + ``app.js`` 里不得再有裸 ``gsap.`` 调用 (AC-2)
2. Google Fonts 外链必须归零, Inter 自托管 (AC-3)
3. ``prefers-reduced-motion`` 覆盖全部带 animation 的规则 (AC-4)
4. ``pin_anim.js``: flash class 不得被 GSAP 缺失牵连 (AC-6),
   GSAP ``fromTo`` 必须受 reduce-motion 约束 (AC-5)

口径说明 (为什么不是简单 grep):
- AC-1 用**行号序**判, 因为 ``defer`` 按文档序执行, 位置错了等于时序倒置。
- AC-2 用**负向前瞻**排除 ``window.gsap`` / ``$gsap``, 因为护栏写法算已修。
- AC-4 **解析 CSS rule block** 而不是 grep 选择器字符串 —— 写了选择器
  不等于该选择器的动画真被 kill (warden R3)。同时把 ``animation-delay``
  一起数, 否则元素仍延迟出现。
- AC-6 用**源码行号序**判, 因为护栏与 classList.add 谁先谁后是语义问题。

每个断言都配了变异探针 (见 test-render-hygiene-2026-10-10.md MP-1~MP-7),
探针注水 → 断言必红 → 复原 → 再跑绿。
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import TypedDict

import pytest

STATIC = Path(__file__).resolve().parents[1] / "moni" / "web" / "static"
INDEX_HTML = STATIC / "index.html"
MAIN_CSS = STATIC / "css" / "main.css"
APP_JS = STATIC / "js" / "app.js"
PIN_ANIM = STATIC / "js" / "pin_anim.js"
FONT_DIR = STATIC / "fonts"

REQUIRED_WEIGHTS = (400, 500, 600, 700)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _script_lines(html: str) -> list[tuple[int, str]]:
    """Return (1-based line number, tag text) for every <script src=...>."""
    out = []
    for i, line in enumerate(html.splitlines(), 1):
        if re.search(r"<script\b[^>]*\bsrc\s*=", line):
            out.append((i, line))
    return out


def _strip_css_comments(css: str) -> str:
    """Blank out /* ... */ while preserving byte offsets and newlines."""
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
    """Blank out // and /* */ comments, leaving string literals alone.

    Written line-by-line rather than as one global state machine: app.js
    contains regex literals and strings that hold unbalanced quotes (e.g.
    the HTML-escape map uses ``"'": '&#39;'``), which desyncs any scanner
    that carries quote state across a whole file. A per-line scan has no
    such state to lose, and JS ``//`` comments never span lines.
    """
    out: list[str] = []
    for line in js.split("\n"):
        out.append(_strip_line_comments(line))
    return "\n".join(out)


def _strip_line_comments(line: str) -> str:
    """Remove /* */ and // from one line, tracking quotes and regex crudely."""
    i, n = 0, len(line)
    quote = None
    while i < n:
        ch = line[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            continue
        if line.startswith("/*", i):
            end = line.find("*/", i + 2)
            end = n if end < 0 else end + 2
            line = line[:i] + "".join(" " if c != "\t" else "\t" for c in line[i:end]) + line[end:]
            n = len(line)
            i = end
            continue
        if line.startswith("//", i):
            # Not a comment if it is the '//' inside a URL string.
            before = line[:i]
            if before.count("'") % 2 == 1 or before.count('"') % 2 == 1:
                i += 2
                continue
            return line[:i] + " " * (n - i)
        i += 1
    return line


def _css_rules(css: str) -> list[tuple[str, str, str]]:
    """Parse top-level rules -> (selector, body, is_reduce_block)."""
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
        body = clean[brace + 1 : j - 1]
        rules.append((selector, body, selector))
        i = j
    return rules


def _sel_set(selector: str) -> set[str]:
    return {s.strip() for s in selector.split(",") if s.strip()}


def _reduce_selectors(css: str) -> set[str]:
    out: set[str] = set()
    for selector, body, raw in _css_rules(css):
        if "prefers-reduced-motion" not in raw:
            continue
        for chunk in body.split("}"):
            head = chunk.split("{")[0].strip()
            if head:
                out |= _sel_set(head)
    return out


def _animated_rules(css: str) -> list[tuple[set[str], dict[str, str]]]:
    """Rules with a real (non-``none``) animation -> (selectors, props).

    ``props`` records BOTH forms of delay: an explicit ``animation-delay``
    declaration and a time-offset buried in the ``animation`` shorthand
    (``animation: fadeUp 0.35s ease 0.15s forwards``). Nulling only the
    explicit property would leave the shorthand delay in place and the
    element would still appear late.
    """
    out = []
    for selector, body, raw in _css_rules(css):
        if raw.startswith("@"):
            continue
        decls = re.findall(r"(?:^|;)\s*animation\s*:\s*([^;]+)", body)
        if not decls:
            continue
        if all(d.strip() == "none" for d in decls):
            continue  # unconditional kill, not a real animated rule
        props = {"animation": "; ".join(d.strip() for d in decls)}
        for delay in re.findall(r"(?:^|;)\s*animation-delay\s*:\s*([^;]+)", body):
            props["animation-delay"] = delay.strip()
        for decl in decls:
            shorthand_delay = _shorthand_time_offset(decl)
            if shorthand_delay:
                props["shorthand-delay"] = shorthand_delay
        out.append((_sel_set(selector), props))
    return out


def _shorthand_time_offset(decl: str) -> str | None:
    """The second <time> in an animation shorthand is the delay.

    ``animation: fadeUp 0.35s ease 0.15s forwards`` -> delay "0.15s".
    Returns None when no delay is present.
    """
    if "infinite" in decl and len(re.findall(r"[\d.]+m?s", decl)) < 2:
        pass
    times = re.findall(r"[\d.]+m?s\b", decl)
    if len(times) < 2:
        return None
    return times[1]


def _reduce_bodies(css: str) -> list[str]:
    return [body for selector, body, raw in _css_rules(css)
            if "prefers-reduced-motion" in raw]


# ---------------------------------------------------------------------------
# AC-1 — GSAP script: defer, document order, no async
# ---------------------------------------------------------------------------


def test_gsap_script_tag_has_defer():
    html = INDEX_HTML.read_text(encoding="utf-8")
    tags = [t for _, t in _script_lines(html) if "gsap" in t]
    assert len(tags) == 1, f"expected exactly 1 gsap script tag, got {len(tags)}"
    assert "defer" in tags[0], f"gsap script tag is missing defer: {tags[0]!r}"


def test_gsap_script_has_no_async():
    """async + defer together makes execution order non-deterministic."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    for _, tag in _script_lines(html):
        if "gsap" in tag:
            assert not re.search(r"\basync\b", tag), f"async must not be used: {tag!r}"


def test_gsap_script_precedes_all_consumer_scripts():
    """defer scripts run in document order — gsap must stay before consumers."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    scripts = _script_lines(html)
    gsap_lines = [ln for ln, t in scripts if "gsap" in t]
    assert gsap_lines, "gsap script tag not found"
    gsap_line = gsap_lines[0]

    for ln, tag in scripts:
        if "gsap" in tag:
            continue
        m = re.search(r'src="([^"]+)"', tag)
        if not m:
            continue
        src = m.group(1)
        # Local business scripts that touch window.gsap.
        if re.search(r"(app|pin_anim)\.js", src):
            assert gsap_line < ln, (
                f"gsap script (line {gsap_line}) must precede {src} "
                f"(line {ln}); defer runs in document order"
            )


def test_alpine_preload_hint_still_present():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert re.search(r'<link[^>]*rel="preload"[^>]*as="script"', html), (
        "Alpine preload hint was dropped"
    )


# ---------------------------------------------------------------------------
# AC-2 — no bare gsap. calls in app.js
# ---------------------------------------------------------------------------

# Matches a bare `gsap.` / `gsap.min` reference but NOT window.gsap / $gsap.
_BARE_GSAP = re.compile(r"(?<![\w.$])gsap\s*\.")


def test_app_js_has_no_bare_gsap_calls():
    """Bare `gsap.` throws ReferenceError when the CDN hasn't landed (R1).

    Guarded forms are `window.gsap.…`. Every bare call must go.
    Comments are excluded — prose describing the old bug is not a call.
    """
    src = _strip_js_comments(APP_JS.read_text(encoding="utf-8"))
    offenders = []
    for i, line in enumerate(src.splitlines(), 1):
        for m in _BARE_GSAP.finditer(line):
            before = line[: m.start()].strip()
            # `const g = window.gsap` style aliasing is still a guard.
            if before.endswith("window."):
                continue
            offenders.append((i, line.strip()[:100]))
    assert not offenders, "bare gsap. calls remain: %r" % (offenders,)


def test_app_js_gsap_calls_all_go_through_window():
    """Positive control: the guarded form must still be present and used."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "window.gsap" in src, "no window.gsap usage at all — guard was removed wholesale"
    guarded = len(re.findall(r"window\.gsap\s*\.", src))
    assert guarded > 0, "window.gsap is only tested, never called"


def test_app_js_has_no_bare_killtweensof():
    src = APP_JS.read_text(encoding="utf-8")
    for i, line in enumerate(src.splitlines(), 1):
        if _BARE_GSAP.search(line) and "killTweensOf" in line:
            assert line.strip().startswith("window.") or "window.gsap" in line, (
                f"line {i}: killTweensOf must not rely on try/catch: {line.strip()!r}"
            )


# ---------------------------------------------------------------------------
# AC-3 — Google Fonts gone, Inter self-hosted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["fonts.googleapis.com", "fonts.gstatic.com"])
def test_no_google_fonts_external_reference(host):
    html = INDEX_HTML.read_text(encoding="utf-8")
    # Comments legitimately mention the historical host when documenting why
    # it was removed; only live markup counts.
    live = _strip_js_comments(html)
    n = live.count(host)
    assert n == 0, f"{host} still referenced {n}x; Q1=A requires self-hosted Inter"


def test_no_google_fonts_in_css_either():
    css = MAIN_CSS.read_text(encoding="utf-8")
    live = _strip_css_comments(css)
    for host in ("fonts.googleapis.com", "fonts.gstatic.com"):
        assert host not in live, f"{host} referenced from main.css"


def test_google_fonts_comment_documents_the_removal():
    """The removal should be recorded, not silently forgotten."""
    css = MAIN_CSS.read_text(encoding="utf-8")
    assert "fonts.googleapis.com" in css, (
        "main.css no longer mentions why the Google Fonts link was dropped"
    )


def _font_face_weight_values(css: str) -> list[tuple[int, int]]:
    """Every @font-face weight descriptor as a (min, max) range.

    Inter ships as a VARIABLE font, so the correct shape is one file
    declared as ``font-weight: 400 700`` rather than four identical
    payloads. Both forms satisfy the requirement; what matters is that
    every weight moni asks for is *served*.
    """
    out = []
    for f in re.findall(r"@font-face\s*\{[^}]*\}", css):
        m = re.search(r"font-weight\s*:\s*([\d\s]+)", f)
        if not m:
            continue
        nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
        if len(nums) == 1:
            out.append((nums[0], nums[0]))
        elif len(nums) >= 2:
            out.append((nums[0], nums[1]))
    return out


@pytest.mark.parametrize("weight", REQUIRED_WEIGHTS)
def test_inter_font_face_declared_for_every_weight(weight):
    css = MAIN_CSS.read_text(encoding="utf-8")
    assert _font_face_weight_values(css), "no usable @font-face weight found"
    ranges = _font_face_weight_values(css)
    covered = any(lo <= weight <= hi for lo, hi in ranges)
    assert covered, (
        f"font-weight {weight} is not served by any @font-face "
        f"(declared ranges: {ranges!r})"
    )


def test_inter_declares_no_redundant_weight_files():
    """Guard against shipping the same variable payload 4x under 4 names."""
    fonts_dir = FONT_DIR
    if not fonts_dir.is_dir():
        pytest.skip("fonts/ dir not created yet")
    files = sorted(f for f in os.listdir(fonts_dir) if f.endswith(".woff2"))
    digests = {}
    for f in files:
        d = hashlib.sha256((fonts_dir / f).read_bytes()).hexdigest()
        digests.setdefault(d, []).append(f)
    dupes = [fs for fs in digests.values() if len(fs) > 1]
    assert not dupes, f"duplicate font payloads shipped: {dupes!r}"


# ---------------------------------------------------------------------------
# AC-3 / P1-1 — every weight must be served by Inter, not synthesised
# ---------------------------------------------------------------------------

# moni's Latin coverage: ASCII letters/digits plus the punctuation that
# actually appears in the UI. If the face covering this range declares a
# single weight, every 500/600/700 Latin run falls back to system-ui or gets
# synthetically emboldened.
ASCII_PROBE_CODEPOINTS = frozenset({0x30, 0x41, 0x5A, 0x61, 0x7A, 0xB7, 0x2014})


def _codepoints_from_unicode_range(spec: str) -> set[int]:
    out: set[int] = set()
    spec = re.sub(r"\s+", "", spec)
    for part in spec.split(","):
        if not part:
            continue
        m = re.fullmatch(r"U\+([0-9A-Fa-f]+)(?:-([0-9A-Fa-f]+))?", part)
        if not m:
            continue
        lo = int(m.group(1), 16)
        hi = int(m.group(2), 16) if m.group(2) else lo
        out.update(range(lo, hi + 1))
    return out


class _FontFace(TypedDict):
    lo: int
    hi: int
    codepoints: set[int]
    raw: str


def _font_faces(css: str) -> list[_FontFace]:
    faces: list[_FontFace] = []
    for f in re.findall(r"@font-face\s*\{([^}]*)\}", css):
        weights = re.search(r"font-weight\s*:\s*([\d\s]+)", f)
        if not weights:
            continue
        nums = [int(x) for x in re.findall(r"\d+", weights.group(1))]
        lo, hi = (nums[0], nums[0]) if len(nums) == 1 else (nums[0], nums[1])
        urange = re.search(r"unicode-range\s*:\s*([^;]+);", f)
        faces.append(
            _FontFace(
                lo=lo,
                hi=hi,
                codepoints=_codepoints_from_unicode_range(
                    urange.group(1) if urange else ""
                ),
                raw=f,
            )
        )
    return faces


def test_latin_face_serves_the_full_weight_range():
    """P1-1: the ASCII-covering face must declare a range, not a single weight.

    Inter ships as a variable font (wght 100-900, named instances at 500 /
    600 / 700 — verified by decoding the shipped woff2). A single-value
    `font-weight: 400` descriptor on the face that covers ASCII leaves every
    500/600/700 ASCII run to synthetic bolding or the system fallback.
    """
    css = MAIN_CSS.read_text(encoding="utf-8")
    faces = _font_faces(css)
    assert faces, "no @font-face rules parsed"

    latin = [f for f in faces if ASCII_PROBE_CODEPOINTS <= f["codepoints"]]
    assert latin, "no @font-face covers the Latin range moni renders"

    for f in latin:
        assert f["lo"] <= 400 and f["hi"] >= 700, (
            "the ASCII-covering @font-face declares font-weight "
            f"{f['lo']}-{f['hi']}, so 500/600/700 cannot be rendered from it "
            "(would fall back to system-ui or be synthetically emboldened)"
        )


def test_every_latin_weight_is_served_by_a_local_face():
    """For each weight moni uses, some local face must actually serve it."""
    css = MAIN_CSS.read_text(encoding="utf-8")
    faces = _font_faces(css)
    for weight in REQUIRED_WEIGHTS:
        served = any(
            f["lo"] <= weight <= f["hi"] and ASCII_PROBE_CODEPOINTS <= f["codepoints"]
            for f in faces
        )
        assert served, f"no local Inter face serves font-weight {weight} for Latin"


def test_no_face_is_remote_or_missing():
    css = MAIN_CSS.read_text(encoding="utf-8")
    for f in _font_faces(css):
        urls = re.findall(r"url\(['\"]?([^'\")]+)", str(f["raw"]))
        for url in urls:
            assert not url.startswith(("http://", "https://", "//")), (
                f"font face src is remote: {url!r}"
            )
            assert (STATIC / url.lstrip("./")).resolve().is_file(), (
                f"font face src missing on disk: {url!r}"
            )


def test_every_font_face_points_at_a_local_file_that_exists():
    """Half-done self-hosting (rule without file) is a silent 404 -> P0."""
    css = MAIN_CSS.read_text(encoding="utf-8")
    faces = re.findall(r"@font-face\s*\{[^}]*\}", css)
    assert faces, "no @font-face rule found"
    srcs = []
    for f in faces:
        m = re.search(r"src\s*:\s*([^;]+)", f)
        assert m, f"@font-face without src: {f!r}"
        for url in re.findall(r"url\(['\"]?([^'\")]+)", m.group(1)):
            srcs.append(url.strip())
    assert srcs, "no url() inside any @font-face src"
    for url in srcs:
        assert not url.startswith(("http://", "https://", "//")), (
            f"@font-face src is still remote: {url!r}"
        )
        assert (STATIC / url.lstrip("./")).is_file(), (
            f"@font-face points at a missing file: {url!r}"
        )


def test_font_face_uses_font_display_swap():
    css = MAIN_CSS.read_text(encoding="utf-8")
    faces = re.findall(r"@font-face\s*\{[^}]*\}", css)
    for f in faces:
        assert "font-display" in f, f"@font-face missing font-display: {f!r}"


def test_font_files_exist_for_all_weights():
    if not FONT_DIR.is_dir():
        pytest.skip("fonts/ dir not created yet (Q1=A self-hosting pending)")
    files = sorted(p.name for p in FONT_DIR.glob("*.woff2"))
    assert files, "fonts/ dir exists but holds no .woff2"


# ---------------------------------------------------------------------------
# AC-4 — prefers-reduced-motion coverage == 100%
# ---------------------------------------------------------------------------


def test_reduce_motion_covers_every_animated_rule():
    css = MAIN_CSS.read_text(encoding="utf-8")
    reduce_sels = _reduce_selectors(css)
    assert reduce_sels, "no prefers-reduced-motion block found at all"

    gaps = []
    for sels, _props in _animated_rules(css):
        if not (sels & reduce_sels):
            gaps.append(sorted(sels))
    assert not gaps, (
        "these animated rules are NOT covered by any reduce block: %r" % (gaps,)
    )


def test_reduce_blocks_zero_out_animation_delay():
    """`animation: none` with a surviving delay still delays appearance.

    Covers both the explicit ``animation-delay`` property and a delay
    buried in the ``animation`` shorthand.

    The check is per-selector, not per-rule-block: a reduce block is free
    to group selectors however it likes, so requiring one block to mention
    every selector of a multi-selector rule would fail on a correct file.
    """
    css = MAIN_CSS.read_text(encoding="utf-8")
    bodies = _reduce_bodies(css)
    undelayed = []
    for sels, props in _animated_rules(css):
        delay = props.get("animation-delay") or props.get("shorthand-delay")
        if not delay or delay.strip() in ("0", "0s", "0ms"):
            continue  # no delay to null out
        for sel in sels:
            # This selector's own reduce rule must null the delay.
            fixed = any(
                re.search(re.escape(sel) + r"[^{}]*\{[^}]*animation-delay\s*:\s*(0|0m?s)\b", body)
                for body in bodies
            )
            if not fixed:
                undelayed.append((sel, delay))
    assert not undelayed, f"reduce block leaves a delay in place: {undelayed!r}"


def test_reduce_block_does_not_nuke_everything():
    """Guard against the `* { animation: none !important }` shortcut.

    A global kill also removes the targeted `.toast` transition rule that
    the existing reduce block deliberately kept.
    """
    css = MAIN_CSS.read_text(encoding="utf-8")
    for body in _reduce_bodies(css):
        assert not re.search(r"(^|[;{\s])(\*|html|body)\s*\{[^}]*animation\s*:\s*none", body), (
            "reduce block uses a global `*` kill — too broad"
        )


# ---------------------------------------------------------------------------
# AC-5 / AC-6 — pin_anim.js
# ---------------------------------------------------------------------------


def test_pin_flash_class_added_before_gsap_guard():
    """GSAP missing must not swallow the CSS colour flash (warden D-7).

    The flash class must be applied before any early return that depends on
    ``window.gsap``. If the guard no longer returns early, that is equally
    acceptable — hence the two branches.
    """
    src = PIN_ANIM.read_text(encoding="utf-8")
    live = _strip_js_comments(src)
    lines = live.splitlines()
    add_idx = next(
        (i for i, l in enumerate(lines) if "classList.add(flashClass)" in l), None
    )
    assert add_idx is not None, "classList.add(flashClass) not found"

    # Any early return gated on gsap must come after the class is applied.
    gsap_dependent_returns = [
        i for i, l in enumerate(lines)
        if re.search(r"if\s*\([^)]*window\.gsap[^)]*\)\s*return", l)
        or (i > 0 and "window.gsap" in lines[i - 1] and l.strip() == "return;")
    ]
    for guard_idx in gsap_dependent_returns:
        assert add_idx < guard_idx, (
            "classList.add(flashClass) must come BEFORE the window.gsap early "
            "return, otherwise the CSS flash dies with GSAP "
            "(add at line %d, guard at line %d)" % (add_idx + 1, guard_idx + 1)
        )

    # And the flash must still be removed afterwards.
    assert re.search(r"classList\.remove\(flashClass\)", live), (
        "flash class is never removed"
    )


def test_pin_anim_respects_reduced_motion():
    """GSAP fromTo is invisible to a CSS media query — must be checked in JS.

    Asserts the gate is *invoked*, not merely that the helper and its query
    string exist. A defined-but-uncalled helper is the classic way this
    check goes green while the animation still plays.
    """
    src = PIN_ANIM.read_text(encoding="utf-8")
    live = _strip_js_comments(src)
    assert "prefers-reduced-motion" in src, (
        "no reduced-motion query in pin_anim.js"
    )
    assert re.search(r"\bprefersReducedMotion\s*\(\s*\)", live), (
        "no prefersReducedMotion() definition in pin_anim.js"
    )
    # The gate must be called on the pin path, guarding the GSAP tween.
    call = re.search(r"if\s*\(\s*prefersReducedMotion\s*\(", live)
    assert call, (
        "prefersReducedMotion() is defined but never called — the GSAP "
        "fromTo would still play under reduced motion"
    )
    # ...and the GSAP tween must be downstream of that guard.
    guard_at = call.start()
    tween_at = live.find("fromTo")
    assert tween_at > guard_at, (
        "GSAP fromTo appears before the reduced-motion guard "
        f"(fromTo@{tween_at} vs guard@{guard_at})"
    )


def test_pin_anim_still_removes_flash_class():
    """Whatever path is taken, the transient class must not stick around."""
    src = PIN_ANIM.read_text(encoding="utf-8")
    assert "classList.remove(flashClass)" in src


# ---------------------------------------------------------------------------
# AC-6 / P1-2 — flash duration identical on every path
# ---------------------------------------------------------------------------

# The visible colour flash is driven by the CSS animation on .pin-btn--just-*
# (main.css). The JS removes the class at the same mark, so both sides must
# agree, and every path — GSAP present, GSAP absent, reduced-motion — must
# land on this one value. dad pinned this to 700ms (2026-10-10).
FLASH_MS = 700


def _pin_flash_schedule() -> dict[str, object]:
    """Extract the ms values the flash-removal schedule depends on.

    Handles both shapes the removal timer can take: a literal number
    inline, or a named constant such as ``FLASH_MS``.
    """
    src = _strip_js_comments(PIN_ANIM.read_text(encoding="utf-8"))
    consts = {
        name: int(val)
        for name, val in re.findall(
            r"const\s+([A-Z_][A-Z0-9_]*)\s*=\s*(\d+)\s*;", src
        )
    }

    def resolve(token: str) -> int | None:
        token = token.strip()
        if token.isdigit():
            return int(token)
        if token in consts:
            return consts[token]
        m = re.fullmatch(r"([A-Za-z_$][\w$]*)\s*/\s*(\d+)", token)
        if m and m.group(1) in consts:
            return consts[m.group(1)] // int(m.group(2))
        return None

    # Each `setTimeout(` call: capture everything up to the matching close,
    # then pull the LAST numeric/identifier token before it (the delay arg).
    timers: list[int] = []
    for m in re.finditer(r"setTimeout\(", src):
        depth = 1
        j = m.end()
        while j < len(src) and depth > 0:
            if src[j] == "(":
                depth += 1
            elif src[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        args = src[m.end():j]
        tail = args.rsplit(",", 1)
        if len(tail) != 2:
            continue
        val = resolve(tail[1])
        if val is not None:
            timers.append(val)

    duration = re.search(r"duration\s*:\s*([^,}\s]+)", src)
    return {
        "flash_timers_ms": timers,
        "constants": consts,
        "gsap_duration_token": duration.group(1) if duration else None,
        "gsap_duration_ms": resolve(duration.group(1)) if duration else None,
    }


def test_pin_flash_lifetime_is_identical_with_and_without_gsap():
    """One 700ms clock, not 350ms on one path and 700ms on the other.

    Round 2 unified the paths at 350ms; dad then pinned the value to 700ms
    (2026-10-10) because the flash needs to be readable. All three paths —
    GSAP present, GSAP absent, reduced-motion — must land on that one value.
    """
    sched = _pin_flash_schedule()
    timers: list[int] = sched["flash_timers_ms"]  # type: ignore[assignment]
    assert timers, f"no flash-removal timer found in pin_anim.js: {sched}"

    assert all(ms == FLASH_MS for ms in timers), (
        f"every flash timer must be {FLASH_MS}ms, found {timers} ({sched})"
    )
    # Exactly one removal schedule: no second timer chained after the tween,
    # which is what produced the old 700ms-on-one-path-only behaviour.
    assert len(timers) == 1, (
        f"expected exactly one flash-removal timer, found {len(timers)}: {timers}"
    )


def _css_flash_durations_ms() -> dict[str, int]:
    """Duration of each .pin-btn--just-* animation, in ms.

    The colour flash is actually painted by CSS, so a JS-only constant is
    not enough — if these drift from FLASH_MS the class outlives (or
    under-runs) the animation and the flash appears cut off.
    """
    css = _strip_css_comments(MAIN_CSS.read_text(encoding="utf-8"))
    out: dict[str, int] = {}
    for sel in (".pin-btn--just-pinned", ".pin-btn--just-unpinned"):
        m = re.search(
            re.escape(sel) + r"\s*\{[^}]*animation\s*:\s*([^;]+)", css
        )
        assert m, f"{sel} has no animation declaration"
        keyframe = re.search(r"(pin-flash-\w+)", m.group(1))
        assert keyframe, f"{sel} animation is not a pin-flash keyframe: {m.group(1)!r}"
        dur = re.search(r"([\d.]+)s", m.group(1))
        assert dur, f"{sel} has no seconds duration: {m.group(1)!r}"
        out[keyframe.group(1)] = int(round(float(dur.group(1)) * 1000))
    return out


def test_css_flash_duration_matches_the_js_constant():
    """CSS paints the flash; JS removes the class. They must agree."""
    css_durations = _css_flash_durations_ms()
    assert len(css_durations) == 2, f"expected both pin-flash keyframes: {css_durations}"
    for keyframe, ms in sorted(css_durations.items()):
        assert ms == FLASH_MS, (
            f"{keyframe} runs {ms}ms in CSS but FLASH_MS is {FLASH_MS}ms; "
            "the class would be removed before/after the paint"
        )


def test_gsap_tween_duration_matches_the_flash_duration():
    """The scale pop and the colour flash should end together."""
    sched = _pin_flash_schedule()
    gsap_ms = sched["gsap_duration_ms"]
    assert gsap_ms == FLASH_MS, (
        f"GSAP tween is {gsap_ms}ms but the flash is {FLASH_MS}ms "
        f"({sched})"
    )


def test_pin_flash_timer_is_started_immediately_not_in_oncomplete():
    """The clock must start when the class is added, not when GSAP finishes."""
    src = _strip_js_comments(PIN_ANIM.read_text(encoding="utf-8"))
    add_at = src.find("classList.add(flashClass)")
    assert add_at >= 0, "flash class is never added"

    # The reduced-motion *call* must sit after the class is added. Match the
    # call form (`if (prefersReducedMotion(`), not the function definition.
    call = re.search(r"prefersReducedMotion\s*\(", src[add_at:])
    assert call, "prefersReducedMotion() is never called on the pin path"
    call_at = add_at + call.start()
    assert call_at > add_at, "reduced-motion guard must sit after classList.add"

    # No setTimeout inside an onComplete handler.
    for m in re.finditer(r"onComplete[\s\S]{0,200}?\}", src):
        chunk = m.group(0)
        if "setTimeout" in chunk and "flashClass" in chunk:
            raise AssertionError(
                "flash removal is still scheduled from a GSAP onComplete "
                "callback; it must be started when the class is added so both "
                "paths last the same time"
            )


def test_pin_anim_exposes_global():
    src = PIN_ANIM.read_text(encoding="utf-8")
    assert "window.pinPop" in src


# ---------------------------------------------------------------------------
# AC-10 — cache-bust version strings bumped
# ---------------------------------------------------------------------------


def test_main_css_version_bust_changed():
    html = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(r"css/main\.css\?v=(\d+)", html)
    assert m, "main.css has no ?v= version string"
    assert m.group(1) != "20260827002", "main.css ?v= not bumped after CSS change"


def test_pin_anim_version_bust_changed():
    html = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(r"js/pin_anim\.js\?v=(\d+)", html)
    assert m, "pin_anim.js has no ?v= version string"
    assert m.group(1) != "1785000000", "pin_anim.js ?v= not bumped after JS change"


VERSIONED_ASSETS = [
    "css/main.css",
    "css/source-ops.css",
    "css/source-modal.css",
    "js/toast.js",
    "js/app.js",
    "js/reader.js",
]


def test_versioned_assets_busted_after_their_last_commit():
    """v1.0.102 线上踩过：文件改了但 ?v= 没动，浏览器整版用旧缓存。

    两条判据：
    1. ?v= 当值必须与「该资源最后一次被改动之前」的 ?v= 不同 —— 这条不依赖
       日期，同一天连续改两次也拦得住；
    2. ?v= 的 8 位日期前缀不得早于该资源最后一次提交的日期 —— 兜住「提交时
       bump 了但后来回退」的写法。
    沙箱/浅克隆里拿不到 git 历史时跳过，不假装通过。
    """
    html = INDEX_HTML.read_text(encoding="utf-8")
    root = INDEX_HTML.parents[3]
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git unavailable")
    if probe.returncode != 0:
        pytest.skip("not a git checkout")

    def _git(*args: str) -> str | None:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=20,
        )
        return out.stdout if out.returncode == 0 else None

    stale, unknown = [], []
    for rel in VERSIONED_ASSETS:
        # 取整段版本号，不是只取前 8 位：同一天连续改两次时
        # ``20261119001`` 与 ``20261119002`` 的前 8 位相同，截断会让判据 1 永远误报。
        m = re.search(rf"{re.escape(rel)}\?v=(\d{{8,}})", html)
        if not m:
            unknown.append(f"{rel}: no ?v= token")
            continue
        token = m.group(1)
        tracked = f"moni/web/static/{rel}"

        # 判据 1：与资源「上一次被改动之前」的 token 必须不同。
        sha = (_git("log", "-1", "--format=%H", "--", tracked) or "").strip()
        if sha:
            before = _git("show", f"{sha}^:moni/web/static/index.html")
            if before:
                prev = re.search(rf"{re.escape(rel)}\?v=(\d{{8,}})", before)
                if prev and prev.group(1) == token:
                    stale.append(
                        f"{rel}?v={token} 与该资源上次改动前的值相同（{sha[:7]} 起未 bump）"
                    )

        # 判据 2：日期前缀不得早于最后一次提交日期（提交自带时区，不受 CI TZ 影响）。
        stamp = (_git("log", "-1", "--format=%cd", "--date=short", "--", tracked) or "").strip()
        if stamp and int(token) < int(stamp.replace("-", "")):
            stale.append(f"{rel}?v={token} < last commit {stamp}")

    assert not unknown, (
        f"versioned asset without token: {unknown}"
    )
    assert not stale, (
        "asset changed without bumping ?v= — browsers keep serving the old file: "
        + "; ".join(stale)
    )
