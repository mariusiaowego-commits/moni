"""tests/test_frontend_worker_modal.py — worker 弹窗的"如实上报"契约 (v1.0.49).

背景：分享队列 worker 跑完空队列后会**正常退出 0**，而且从不抢锁。
旧代码把这种结局报成"启动失败"，弹窗标题写"启动失败"，用户于是再点
一次"立即处理队列" —— 于是又起了一个什么活都不干的 worker。

这个文件锁住两处：

1. ``app.js`` 的 ``workerModalTitle`` getter 必须有 ``nothing_to_do``
   分支，且排在"启动失败"兜底之前。
2. ``index.html`` 的状态 chip 必须区分"队列已处理完"和"失败"。

第 1 条不是 grep 了事 —— getter 会被**原样抽出来丢进 node 里真求值**，
用一组 (响应体, 期望标题) 矩阵断言。grep 只能证明"字符串出现过"，
求值才能证明"分支顺序正确"（把 nothing_to_do 分支挪到兜底后面，
矩阵会立刻红）。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "moni" / "web" / "static"
INDEX_HTML = STATIC / "index.html"
APP_JS = STATIC / "js" / "app.js"


def _extract_getter(source: str, name: str) -> str:
    """Pull a `get <name>() { ... }` block out of app.js by brace matching."""
    match = re.search(rf"get\s+{re.escape(name)}\s*\(\s*\)\s*\{{", source)
    assert match, f"app.js 里找不到 getter {name}()"
    start = match.start()
    # brace matching from the opening '{'
    depth = 0
    for i in range(match.end() - 1, len(source)):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"getter {name}() 的大括号没配平")


MATRIX = [
    # (worker 响应体, 期望标题)
    (None, "处理队列"),
    ({"started": True}, "处理队列 - 已启动 worker"),
    ({"started": False, "already_running": True}, "处理队列 - 已有 worker 在跑"),
    (
        {"started": False, "already_running": False, "nothing_to_do": True},
        "处理队列 - 队列已处理完",
    ),
    (
        {"started": False, "already_running": False, "nothing_to_do": False},
        "处理队列 - 启动失败",
    ),
]


def test_worker_modal_title_matrix_via_node():
    """把 app.js 里真实的 getter 抽出来在 node 里求值一遍。"""
    node = subprocess.run(
        ["node", "--version"], capture_output=True, text=True, check=False
    )
    if node.returncode != 0:
        pytest.skip("node not available on this machine")

    getter_src = _extract_getter(APP_JS.read_text(encoding="utf-8"), "workerModalTitle")
    payload = {"matrix": MATRIX}
    script = """
const payload = JSON.parse(process.argv[1]);
// 把 `get workerModalTitle() { ... }` 转成可调用的函数表达式
const getterSrc = payload.getterSrc;
// 方法简写只在对象字面量里合法，所以包一层 {} 再取 getter
const holder = eval('({' + getterSrc + '})');
const fn = Object.getOwnPropertyDescriptor(holder, 'workerModalTitle').get;
const failures = [];
for (const [resp, expected] of payload.matrix) {
  const host = { workerModalResult: resp };
  let got;
  try {
    got = fn.call(host);
  } catch (err) {
    failures.push([JSON.stringify(resp), 'threw: ' + err.message]);
    continue;
  }
  if (got !== expected) {
    failures.push([JSON.stringify(resp), 'expected ' + expected + ' got ' + got]);
  }
}
console.log(JSON.stringify(failures));
process.exit(failures.length ? 1 : 0);
"""
    proc = subprocess.run(
        ["node", "-e", script, json.dumps({**payload, "getterSrc": getter_src})],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        "workerModalTitle matrix failed:\n%s\n%s" % (proc.stdout, proc.stderr)
    )


def test_index_html_status_chip_distinguishes_nothing_to_do():
    """弹窗里的状态 chip 不能把"队列已处理完"显示成"失败"。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "nothing_to_do" in html, "index.html 里没有 nothing_to_do 分支"
    assert "队列已处理完" in html, "index.html 缺少'队列已处理完'文案"
    # 失败文案还在（真的失败时要用），但不能是唯一分支
    assert "'失败'" in html


def test_nothing_to_do_branch_precedes_the_failure_fallback():
    """分支顺序：nothing_to_do 必须在"启动失败"兜底之前。"""
    getter_src = _extract_getter(APP_JS.read_text(encoding="utf-8"), "workerModalTitle")
    nothing_at = getter_src.index("nothing_to_do")
    # 认准真正的那条 return，而不是注释里出现的同名词
    fallback_at = getter_src.index("return '处理队列 - 启动失败'")
    assert nothing_at < fallback_at, (
        "nothing_to_do 分支必须排在'启动失败'兜底之前，"
        "否则空队列仍会显示成启动失败"
    )

