"""tests/test_frontend_modal_scroll_lock.py — 弹窗滚动锁 (v1.0.110).

背景：`index.html` 里的 15 个 `.modal-backdrop` + 1 个 `.modal-backdrop-dim`
全部由 Alpine `x-show` 开关，从不调用 `moniModal.open()`。滚动锁只写在
`open()` 里，于是一个都没生效 —— 弹窗打开时滚轮还能滚背后的页面。

这个文件锁两件事：

1. `_anyOverlayVisible()` 的判定语义（真在 node 里求值，不是 grep）。
2. 滚动锁只有一个写者：`_syncScrollLock()`，且由 MutationObserver 驱动。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "moni" / "web" / "static"
MODAL_JS = STATIC / "js" / "modal.js"
INDEX_HTML = STATIC / "index.html"


def _extract_function(source: str, name: str) -> str:
    """Pull a `function <name>(...) { ... }` block out by brace matching."""
    match = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", source)
    assert match, f"modal.js 里找不到 function {name}()"
    depth = 0
    for i in range(match.end() - 1, len(source)):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : i + 1]
    raise AssertionError(f"{name}() 的大括号没配平")


# (openStack 长度, 各浮层是否可见, 期望)
MATRIX = [
    (0, [], False),
    (0, [False, False], False),
    (0, [False, True], True),
    (0, [True], True),
    (1, [], True),          # moniModal.open 登记的 modal 即使没有 backdrop 也算
    (1, [False], True),
]


def test_any_overlay_visible_matrix_via_node():
    src = MODAL_JS.read_text(encoding="utf-8")
    fn_src = _extract_function(src, "_anyOverlayVisible")
    node = subprocess.run(["node", "--version"], capture_output=True, text=True, check=False)
    if node.returncode != 0:
        pytest.skip("node not available on this machine")

    script = """
const payload = JSON.parse(process.argv[1]);
const failures = [];
for (const [stackLen, visibles, expected] of payload.matrix) {
  const _openStack = new Array(stackLen).fill({});
  const document = {
    querySelectorAll: () => visibles.map(v => ({ getClientRects: () => (v ? [{}] : []) })),
  };
  const fn = new Function('_openStack', 'document',
    payload.fnSrc + '\\nreturn _anyOverlayVisible();');
  let got;
  try {
    got = fn(_openStack, document);
  } catch (err) {
    failures.push([JSON.stringify([stackLen, visibles]), 'threw: ' + err.message]);
    continue;
  }
  if (got !== expected) {
    failures.push([JSON.stringify([stackLen, visibles]), 'expected ' + expected + ' got ' + got]);
  }
}
console.log(JSON.stringify(failures));
process.exit(failures.length ? 1 : 0);
"""
    payload = json.dumps({"matrix": MATRIX, "fnSrc": fn_src}, ensure_ascii=False)
    proc = subprocess.run(
        ["node", "-e", script, payload], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, f"node 断言失败: {proc.stdout} {proc.stderr}"
    assert json.loads(proc.stdout or "[]") == []


def test_scroll_lock_has_a_single_writer():
    src = MODAL_JS.read_text(encoding="utf-8")
    assert src.count("moni-modal-open") == 1, "滚动锁类名只能由 _syncScrollLock 写一处"
    assert "classList.toggle('moni-modal-open', want)" in src
    assert "document.body.style.overflow = want ? 'hidden' : ''" in src
    assert "classList.add('moni-modal-open')" not in src
    assert "classList.remove('moni-modal-open')" not in src


def test_scroll_lock_is_driven_by_overlay_visibility():
    src = MODAL_JS.read_text(encoding="utf-8")
    assert "new MutationObserver(_scheduleScrollLock)" in src, "浮层开关没有触发重算"
    assert "attributeFilter: ['style', 'class']" in src, "x-show 改的是行内 style，必须监听"
    assert ".modal-backdrop, .modal-backdrop-dim" in src, "漏了 .modal-backdrop-dim"
    assert src.count("_syncScrollLock();") >= 4, "open/close/confirm 都要走同一个写者"
    assert re.search(r"_watchOverlays\(\);\s*\}\)\(\);", src), "初始化时没有挂监听"


def test_every_overlay_in_index_is_covered():
    html = INDEX_HTML.read_text(encoding="utf-8")
    backdrops = re.findall(r'class="([^"]*modal-backdrop[^"]*)"', html)
    assert len(backdrops) >= 15, f"浮层数量异常: {len(backdrops)}"
    assert any("modal-backdrop-dim" in b for b in backdrops)
    # 浮层必须由 x-show 驱动，否则 observer 看不到状态变化。
    overlay_tags = re.findall(r"<div[^>]*class=\"[^\"]*modal-backdrop[^\"]*\"[^>]*>", html)
    missing = [t for t in overlay_tags if "x-show" not in t]
    assert not missing, f"有浮层不是 x-show 驱动: {missing[:2]}"
