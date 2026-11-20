"""tests/test_llm_jobs_contract.py — /api/llm/jobs 的对外契约闸门。

PR-3 给这些路由挂了 ``response_model``，于是 ``/openapi.json`` 成了前后端
之间唯一的一份字段清单。这个文件只做一件事：**比对清单**。

钉住的失败模式（都是 pydantic 默认行为埋的坑）：

1. 模型里没声明的字段会被**静默剥离** —— 前端拿不到 ``article_title`` /
   ``fact_check`` / token 计数却不会报错，只是永远显示空。
2. ``/stats/duration`` 注册在 ``/{job_id}`` 之后就永远走不到（被当成
   job_id），且不会有任何报错。
3. 状态机多一个 phase（如 leases 判死的 stale）却忘了同步到前端词表。
"""

from __future__ import annotations

import re

import pytest

import moni.web.routes.fact_check_plus  # noqa: F401  (import 顺序见 llm_usage_counters)
from moni.llm.phases import PHASES, SLOT_OF_TASK
from moni.web.server import create_app


@pytest.fixture(scope="module")
def schema() -> dict:
    return create_app().openapi()


def _json_kinds(prop: dict) -> set[str]:
    """把 prop 展开成 JSON 类型集合（``anyOf`` 里的 null 也算一种）。"""
    if "anyOf" in prop:
        kinds: set[str] = set()
        for branch in prop["anyOf"]:
            kinds |= _json_kinds(branch)
        return kinds
    if prop.get("type") == "array":
        return {"array"}
    return {str(prop.get("type"))}


def _job_out(schema: dict) -> dict:
    return schema["components"]["schemas"]["JobOut"]


def test_job_out_requires_the_identity_fields(schema: dict):
    job = _job_out(schema)
    assert {"id", "task_type", "article_id", "status", "phase", "created_at"} <= set(job["required"])
    assert {"queue_position", "queue_total", "progress", "fact_check"} <= set(job["properties"])


@pytest.mark.parametrize(
    "field,kinds",
    [
        ("article_title", {"string", "null"}),
        ("input_tokens", {"integer", "null"}),
        ("output_tokens", {"integer", "null"}),
        ("cost_usd", {"number", "null"}),
        ("fact_check", {"object", "null"}),
        ("error", {"string", "null"}),
    ],
)
def test_front_end_read_fields_survive_the_model(schema: dict, field: str, kinds: set[str]):
    """这些字段前端已经在读；模型漏声明就会被静默剥离。"""
    prop = _job_out(schema)["properties"].get(field)
    assert prop is not None, f"JobOut 少了 {field}，pydantic 会把它从响应里剥掉"
    assert _json_kinds(prop) == kinds


def test_progress_contract_is_declared_but_optional(schema: dict):
    job = _job_out(schema)
    assert "progress" in job["properties"]
    assert "progress" not in job["required"], "progress 没有数据时必须允许缺省/为 null"

    prog = schema["components"]["schemas"]["JobProgress"]
    assert prog["required"] == ["stage"]
    stage = prog["properties"]["stage"]
    assert set(stage.get("enum") or []) == {"thinking", "searching", "writing"}


def test_duration_stats_shape(schema: dict):
    stats = schema["components"]["schemas"]["DurationStats"]
    assert stats["required"] == ["n"]
    assert set(stats["properties"]) == {"n", "p50_sec", "p90_sec"}
    assert _json_kinds(stats["properties"]["p50_sec"]) == {"number", "null"}


def test_stats_summary_counts_the_stale_bucket(schema: dict):
    """leases 判死的行也在库里；统计漏了 stale 会让总数对不上。"""
    assert "stale" in schema["components"]["schemas"]["JobsStats"]["properties"]


def test_stats_routes_are_registered_before_the_job_id_route(schema: dict):
    """``/{job_id}`` 是通配符：它先注册就会把 ``/stats/duration`` 吃掉。

    改看 ``openapi["paths"]`` 而不是 ``app.routes``：两者的 key 顺序都等于注册
    顺序，但 openapi 是契约本身的投影（CI 上 ``app.routes`` 的内省结果不稳定，
    实测会莫名其妙少一条）。断言失败时把 llm/jobs 的全部路径打出来。
    """
    paths = list(schema["paths"])
    job_paths = [p for p in paths if "llm/jobs" in p]
    wildcards = [
        i for i, path in enumerate(paths)
        if re.fullmatch(r"/api/llm/jobs/\{[^}]+\}", path)
    ]
    assert wildcards, f"openapi 里没有 /api/llm/jobs/<job_id>；实际注册：{job_paths}"

    for stats_path in ("/api/llm/jobs/stats/duration", "/api/llm/jobs/stats/summary"):
        assert stats_path in paths, f"{stats_path} 没注册；实际注册：{job_paths}"
        assert paths.index(stats_path) < wildcards[0], (
            f"{stats_path} 排在 {paths[wildcards[0]]} 之后，永远走不到"
        )


def test_phase_vocabulary_is_the_one_the_frontend_mirrors():
    """前端 job_view.js 有一份同词表，node 侧再比对一次（见 test_job_view_node）。"""
    assert PHASES == ("none", "queued", "running", "done", "failed", "cancelled", "stale")
    assert SLOT_OF_TASK["fact_check_plus"] == SLOT_OF_TASK["fact_check"] == "fact_check"
