"""tests/test_job_view_node.py — 用 node 跑前端纯函数，与 Python 词表对表。

为什么不用 jsdom / 浏览器：``job_view.js`` 是**纯函数**，不碰 DOM。把它塞进
浏览器测试只会慢 100 倍，还测不到边界。这里直接 require 同一份文件，
浏览器端加载的也是这个文件，测的就是线上那份实现。

两条闸门：

* C2：JS 的 ``PHASES`` / ``SLOT_OF_TASK`` 必须与 ``moni/llm/phases.py`` 完全
  相等 —— 两边各写一份，迟早会有一边忘了加状态。
* C3：``tests/fixtures/job_view_cases.json`` 表驱动覆盖 7 个 phase，
  外加「迟到回包属于另一篇文章」这条防串态用例。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from moni.llm.phases import PHASES, SLOT_OF_TASK

ROOT = Path(__file__).resolve().parents[1]
JOB_VIEW_JS = ROOT / "moni" / "web" / "static" / "js" / "job_view.js"
CASES = ROOT / "tests" / "fixtures" / "job_view_cases.json"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="需要 node 运行前端纯函数用例")


def _run_node(script: str) -> object:
    proc = subprocess.run(
        [NODE, "-e", script],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"node 执行失败：{proc.stderr.strip()}"
    return json.loads(proc.stdout)


def test_vocabulary_matches_python():
    data = _run_node(
        f"const v=require({json.dumps(str(JOB_VIEW_JS))});"
        "console.log(JSON.stringify({p:v.PHASES,t:v.SLOT_OF_TASK,s:v.STATUS_TO_PHASE}));"
    )
    assert tuple(data["p"]) == PHASES, "job_view.js 的 PHASES 与 moni/llm/phases.py 不一致"
    assert data["t"] == SLOT_OF_TASK, "SLOT_OF_TASK 两边不一致"
    assert data["s"] == {
        "pending": "queued",
        "running": "running",
        "done": "done",
        "failed": "failed",
        "stale": "stale",
        "cancelled": "cancelled",
    }


def _evaluate_cases() -> list[dict]:
    script = """
const fs=require('fs');
const v=require(process.argv[1]);
const fx=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const now=Date.parse(fx.now);
const out=fx.cases.map(c=>{
  const input=Object.assign({}, c.input, {nowMs: now});
  return {name:c.name, got: v.viewFor(input)};
});
console.log(JSON.stringify(out));
"""
    proc = subprocess.run(
        [NODE, "-e", script, str(JOB_VIEW_JS), str(CASES)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"node 执行失败：{proc.stderr.strip()}"
    return json.loads(proc.stdout)


def test_table_driven_cases():
    fixtures = json.loads(CASES.read_text(encoding="utf-8"))
    got = {row["name"]: row["got"] for row in _evaluate_cases()}
    for case in fixtures["cases"]:
        actual = got[case["name"]]
        for field, expected in case["expect"].items():
            assert actual[field] == expected, (
                f"{case['name']}：{field} 期望 {expected!r}，实际 {actual[field]!r}"
            )


def test_fixtures_cover_every_phase():
    fixtures = json.loads(CASES.read_text(encoding="utf-8"))
    covered = {
        phase
        for case in fixtures["cases"]
        for phase in [case["expect"].get("phase")]
        if phase
    }
    assert covered == set(PHASES), f"表驱动用例没有覆盖全部 phase：缺 {set(PHASES) - covered}"


def test_late_reply_from_another_article_is_ignored():
    """换篇之后迟到的回包不能把 B 的槽位写成 A 的任务。"""
    fixtures = json.loads(CASES.read_text(encoding="utf-8"))
    names = [c["name"] for c in fixtures["cases"] if c["expect"].get("ignore") is True]
    assert names, "缺一条「迟到回包属于另一篇文章」的用例"
