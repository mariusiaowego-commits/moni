"""tests/test_llm_job_invariants.py — LLM 任务链路的静态不变量（AST 级）。

这三条是「代码看起来没改坏、但语义已经漏了」的典型，字符串 grep 抓不住，
所以用 AST / 结构化扫描：

1. ``_mark_failed(...)`` 必须带 ``handle=``。不带 handle 的写入不做 fence，
   任务被 sweep 成 stale 后旧线程还能把行改回 failed —— v1.0.42 修的就是
   这个双写竞态。
2. ``enqueueLlmJob`` 里不许再 ``navigate(...)``。v1.0.110 之前点完按钮会被
   拽到「LLM 任务」面板，用户失去正在读的文章。
3. 按钮的等待/禁用态只能用 ``:disabled``，不许用 ``x-show`` 把按钮藏起来
   （藏起来就没有取消/重跑的入口）。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOBS_PY = ROOT / "moni" / "llm" / "jobs.py"
APP_JS = ROOT / "moni" / "web" / "static" / "js" / "app.js"
INDEX_HTML = ROOT / "moni" / "web" / "static" / "index.html"


def test_every_mark_failed_call_passes_a_lease_handle():
    tree = ast.parse(JOBS_PY.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_mark_failed"
    ]
    assert calls, "jobs.py 里一条 _mark_failed 都没找到，扫描逻辑失效了"
    missing = [c.lineno for c in calls if not any(kw.arg == "handle" for kw in c.keywords)]
    assert not missing, (
        f"这些 _mark_failed 调用没带 handle=（不做 fence，会把 stale 行改回 failed）：行 {missing}"
    )


def test_enqueue_no_longer_hijacks_the_reader():
    src = APP_JS.read_text(encoding="utf-8")
    start = src.index("enqueueLlmJob(taskType, jobId, articleId) {")
    body = src[start : src.index("\n    },", start)]
    assert "navigate(" not in body, "点完按钮还跳转到任务面板，用户会离开正在读的文章"
    assert "/api/llm/jobs/stats/summary" in body, "没有刷新任务统计，面板计数会落后"


def _method_body(src: str, signature: str) -> str:
    start = src.index(signature)
    return src[start : src.index("\n    },", start)]


def test_wait_state_is_expressed_with_disabled_not_x_show():
    html = INDEX_HTML.read_text(encoding="utf-8")
    start = html.index('class="reader-dock-main"')
    dock = html[start : html.index('class="reader-dock-tools"', start)]
    for name in ("generateSummary()", "generateFactCheck()", "enqueueLlmJobFactCheckPlus("):
        line = next(l for l in dock.splitlines() if name in l)
        assert "readerJobBusy(" in line, f"{name} 按钮没接槽位忙碌态"
        assert ':disabled=' in line, f"{name} 按钮没用 :disabled 表达禁用"
        assert "readerJobBusy" not in (line.split("x-show=", 1)[1] if "x-show=" in line else ""), (
            f"{name} 按钮把等待态写进了 x-show —— 按钮会被藏起来"
        )


def test_job_view_is_loaded_before_app_js():
    """app.js 的方法体在运行期读 window.moniJobView；脚本顺序错会静默空转。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert html.index("js/job_view.js") < html.index("js/app.js"), "job_view.js 必须在 app.js 之前加载"
