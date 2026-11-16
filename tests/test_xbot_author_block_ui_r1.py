"""agy UI review r1 修复的**行为级**回归测试 (sprint-38, v1.0.88)。

为什么要有这一层 (而不是只在 test_xbot_author_block_ui.py 里加静态断言):
  agy 审出的 P0/P1 都是**浏览器运行时**的缺陷 —— 源码里「有没有那个字符串」
  证明不了行为对不对。例如 P0: `_xbotSourceId()` 在首页返回空串,
  静态 grep 永远抓不到, 只有**真的调用这个方法**才会炸。

所以这里用 node 把 moni 真实的 app.js 加载起来 (vm context, 按 index.html 的
script 顺序加载 theme.js / calendar.js / ... ), 然后直接调用组件方法做断言。

harness 源码: tests/js/sprint38_ui_harness.js (同目录, 由本文件调用)
被测代码:     moni/web/static/js/app.js  (v1.0.88 sprint-38)
证据:         /tmp/moni-sprint38/evidence/agy-r1-node-harness.txt

若环境没有 node → 整个模块 skip (不静默假绿)。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).resolve().parent / "js" / "sprint38_ui_harness.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node 不可用 — 跳过浏览器运行时行为测试"
)


def _run_harness(scenario: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["node", str(HARNESS), scenario],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=120,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def test_harness_exists() -> None:
    assert HARNESS.is_file(), f"harness 缺失: {HARNESS}"


# ── P0: 详情弹窗首次点击「屏蔽作者」抛错「找不到 X-bot 精选信源」 ──────────

def test_p0_source_id_resolves_on_fresh_homepage() -> None:
    """P0 (agy r1): 首页首次点屏蔽作者必须能用。

    复现 agy 的现象: 首页 (默认文章查阅视图) **没开过**配置页、**没开过**
    抓取配置弹窗 → `configSources` 与 `fetchConfigData` 都是空 →
    `_xbotSourceId()` 恒返回 `""` → 前置检查直接报错 return, 流程中断。
    """
    rc, out = _run_harness("p0-source-id")
    assert rc == 0, f"P0 行为断言失败:\n{out}"
    assert "P0: _xbotSourceId() resolves on a fresh homepage" in out
    assert "P0: resolved id is the real xbot source id" in out


# ── P1: 确认弹窗 Esc / 取消关闭后焦点丢到 <body> ──────────────────────────

def test_p1_focus_restored_after_confirm_cancelled() -> None:
    """P1 (agy r1): 确认框被取消后, 焦点必须还给触发按钮。

    Esc / 点「取消」关闭确认框后, 活动焦点重置到 `<body>` →
    键盘用户的 Tab 动线断掉 (触发按钮拿不回焦点)。
    """
    rc, out = _run_harness("p1-focus")
    assert rc == 0, f"P1 行为断言失败:\n{out}"
    assert "P1: focus is restored to the trigger button after cancel/Esc" in out

# ── P2: 深色模式下「已屏蔽作者」中文标题对比度 ────────────────────────────
#
# ⚠ 2026-11-16 改写 (同步 upstream v1.0.77 UI 规范化后):
#   原断言要求「存在一条 `.dark .fetch-config-zh` 覆盖规则」+「必须用
#   var(--foreground)」。upstream 把 `.fetch-config-zh` 的颜色换成了主题
#   token `var(--fg-strong)` (浅色 #0f172a / 深色 #f8fafc) —— **同一个可读性
#   问题被换了一种修法解决**, 覆盖规则本身不存在了。
#
#   所以断言从「长什么样」改成「**解算出来对不对**」:
#   把 .fetch-config-zh 的 color 声明按 :root / .dark 两套变量**解算**成
#   最终 sRGB, 再跟同作用域的 --card 算对比度。
#   → 覆盖规则 / token 两种写法都算通过 (行为对就行);
#   → 真正不可读 (深色下仍是近黑) 依然算失败 (行为错就红)。
#
# 负控: test_p2_dark_mode_heading_contrast_is_readable 里带一个「把解算结果
# 换成近黑 → 必须判不可读」的自检, 证明这个判据会咬人。


def _strip_comments(css: str) -> str:
    """去掉 CSS 注释 (注释里的类名/颜色会骗过解析)。"""
    import re

    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _blocks(text: str, selector: str) -> list[str]:
    """取出「选择器精确等于 selector」的顶层块的声明体。

    自己按花括号配对找块尾 —— 不能用 `find('{')`+`find('}')`: main.css 里
    有 26 个 @media, 里面嵌套着块, 第一个 '}' 往往不是本块的结尾。
    """
    import re

    out: list[str] = []
    pattern = re.compile(re.escape(selector) + r"(?![-\w])\s*\{")
    pos = 0
    while True:
        m = pattern.search(text, pos)
        if m is None:
            return out
        depth = 1
        i = m.end()
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        out.append(text[m.end() : i - 1])
        pos = i


def _vars(body: str) -> dict[str, str]:
    """从声明体里抽出 --custom-property: value。"""
    out: dict[str, str] = {}
    for decl in body.split(";"):
        if ":" in decl:
            key, _, val = decl.partition(":")
            key = key.strip()
            if key.startswith("--"):
                out[key] = val.strip()
    return out


def _oklch_to_rgb(l: float, c: float, h: float) -> tuple[int, int, int]:
    """oklch → sRGB 8bit (够判对比度用)。"""
    import math

    hr = math.radians(h)
    a = c * math.cos(hr)
    b = c * math.sin(hr)
    l_ = l + 0.3963377774 * a + 0.2158037573 * b
    m_ = l - 0.1055613458 * a - 0.0638541728 * b
    s_ = l - 0.0894841775 * a - 1.2914855480 * b
    l3, m3, s3 = l_**3, m_**3, s_**3
    r = 4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3
    g = -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3
    bl = -0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3

    def gamma(v: float) -> float:
        v = max(0.0, min(1.0, v))
        return 12.92 * v if v <= 0.0031308 else 1.055 * (v ** (1 / 2.4)) - 0.055

    return tuple(round(gamma(v) * 255) for v in (r, g, bl))


def _parse_color(value: str) -> tuple[int, int, int] | None:
    """#rgb / #rrggbb / oklch(...) → sRGB 8bit。解析不了返回 None。"""
    import re

    value = value.strip()
    if value.startswith("#"):
        h = value.lstrip("#")
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        if len(h) != 6:
            return None
        try:
            return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
        except ValueError:
            return None
    m = re.match(r"oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\)", value)
    if m:
        return _oklch_to_rgb(*(float(x) for x in m.groups()))
    return None


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def lin(channel: float) -> float:
        c = channel / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _resolve_heading_color(css: str, dark: bool) -> tuple[int, int, int] | None:
    """解算 .fetch-config-zh 的最终颜色。

    步骤: 取规则体 → 找 color 声明 → 沿 var(--x) 链解算 (变量按作用域取:
    dark=True 用 .dark 块优先, 否则 :root 优先) → 解析成 sRGB。
    """
    import re

    text = _strip_comments(css)

    # 该类的规则 (可能有多条, 取最后一条 —— 后写的赢)
    rule_body = None
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        if ".fetch-config-zh" in m.group(1):
            rule_body = m.group(2)
    if rule_body is None:
        return None

    cm = re.search(r"color\s*:\s*([^;]+)", rule_body)
    if cm is None:
        return None
    value = cm.group(1).strip()

    root_vars: dict[str, str] = {}
    for body in _blocks(text, ":root"):
        root_vars.update(_vars(body))
    dark_vars: dict[str, str] = {}
    for body in _blocks(text, ".dark"):
        dark_vars.update(_vars(body))
    # 作用域优先级 (对齐真实 CSS 层叠):
    #   theme.js 把 .dark class 加在 <body> 上, :root 在 <html> 上,
    #   body 是 :root 的后代 → 深色模式下 .dark 的变量赢;
    #   浅色模式 body 没有 .dark class → 只有 :root 的值生效。
    # 先铺低优先级, 再用高优先级盖上去 (顺序反了会让浅色变量赢深色)。
    scope = dict(root_vars if dark else dark_vars)
    scope.update(dark_vars if dark else root_vars)

    seen: set[str] = set()
    while value.startswith("var("):
        name = re.match(r"var\(\s*(--[\w-]+)", value)
        if name is None:
            return None
        token = name.group(1)
        if token in seen:
            return None  # 循环引用
        seen.add(token)
        if token not in scope:
            return None
        value = scope[token].strip()

    return _parse_color(value)


def _card_color(css: str, dark: bool) -> tuple[int, int, int] | None:
    """解算某作用域下的 --card (标题所在卡片底色)。"""
    text = _strip_comments(css)
    root_vars: dict[str, str] = {}
    for body in _blocks(text, ":root"):
        root_vars.update(_vars(body))
    dark_vars: dict[str, str] = {}
    for body in _blocks(text, ".dark"):
        dark_vars.update(_vars(body))
    scope = dict(root_vars if dark else dark_vars)
    scope.update(dark_vars if dark else root_vars)
    return _parse_color(scope.get("--card", ""))


def test_p2_dark_mode_heading_contrast() -> None:
    """P2 (agy r1): `.fetch-config-zh` 标题在**浅色 + 深色**下都必须可读。

    原来硬编码 `color: #0f172a` (近黑) —— 在深色卡片 (#1e1e1e) 上对比度只有
    1.03:1 → 「已屏蔽作者」几乎看不见; 在浅色卡片上是 17.7:1 (本来就正常)。

    现在 upstream 把颜色改成了主题 token (`--fg-strong`): 浅色 #0f172a /
    深色 #f8fafc —— **浅色仍是近黑是对的** (深字配浅底), 关键是深色下解算成
    浅色。所以判据是「同作用域下标题 vs 卡片底的对比度 ≥ 3:1」, 两种模式
    各自跟自己的卡片底比 —— 不关心它用覆盖规则还是用 token 实现。
    """
    css = (REPO / "moni" / "web" / "static" / "css" / "main.css").read_text(
        encoding="utf-8"
    )

    for dark, label in ((False, "浅色"), (True, "深色")):
        fg = _resolve_heading_color(css, dark=dark)
        assert fg is not None, f"{label}模式下解算不出 .fetch-config-zh 的颜色"
        card = _card_color(css, dark=dark)
        assert card is not None, f"{label}模式下解算不出 --card"
        ratio = _contrast_ratio(fg, card)
        assert ratio >= 3.0, (
            f"{label}模式下标题对比度仅 {ratio:.2f}:1 (< 3:1) —— "
            f"fg={fg} card={card}"
        )


def test_p2_dark_mode_heading_contrast_is_readable() -> None:
    """P2 负控: 解算出来的颜色必须真的比卡片底色亮 (对比度 ≥ 3:1)。

    纯文本断言挡不住「解算对了但值写错」—— 这里算一次相对亮度对比度。
    同一个断言同时证明: 近黑一定判不可读 (否则上面那个断言就是假绿)。
    """
    css = (REPO / "moni" / "web" / "static" / "css" / "main.css").read_text(
        encoding="utf-8"
    )
    card_dark = _card_color(css, dark=True)
    assert card_dark is not None, "解析不出深色模式的 --card"

    fg = _resolve_heading_color(css, dark=True)
    assert fg is not None, "深色模式解算不出 .fetch-config-zh 的颜色"
    ratio = _contrast_ratio(fg, card_dark)
    assert ratio >= 3.0, (
        f"深色模式标题对比度仅 {ratio:.2f}:1 (< 3:1) —— fg={fg} card={card_dark}"
    )

    # 负控: 近黑一定判不可读 (证明这个判据会咬人, 不是恒绿)
    near_black = (15, 23, 42)
    assert _contrast_ratio(near_black, card_dark) < 3.0, (
        "判据失效: 近黑色竟然被判为可读 —— 上面的断言就是假绿"
    )
