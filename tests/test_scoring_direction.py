"""Unit tests for direction-specific rubrics (design §10, Sprint 67)."""

from __future__ import annotations

import json

import pytest

from moni.scoring import direction
from moni.scoring.bv_pipeline import score_general_bv
from moni.scoring.direction import (
    DIRECTION_PROMPT_ID,
    brief_reject_reason,
    build_research_prompt,
    label_reject_reason,
    parse_research,
    research_to_rubric,
    usable_research,
)
from moni.scoring.rubric import RUBRIC_V1, WEIGHT_TOTAL
from moni.scoring.snapshot import ArticleSnapshot

BODY = (
    "2024 年 5 月 10 日，微信支付发布新的商户准入规则，要求服务商在 2024 年 12 月 31 日"
    "前完成商户资料补录，涉及系统改造预算约 3000 万元。腾讯 2024 年 3 月上线小程序"
    "代发权益，面向代发客户发放立减金。服务商需改造商户准入系统与清算接口，满足新的"
    "报备要求。未按期整改的机构将被暂停新增商户，并处以罚款。"
    "公众号运营方需要在 2024 年 9 月 30 日前完成主体信息核对，视频号小店同步调整"
    "保证金规则，单个类目保证金上限提高到 20 万元。银行零售部门可据此评估代发与"
    "支付结算的联合方案。"
)


def _payload() -> dict:
    return {
        "scope": "只考微信生态里能落地的动作",
        "out_of_scope": "与微信生态无关的内容一律记 0",
        "dimensions": {
            dim.key: {
                "question": f"{dim.label}在微信生态里改变我方什么",
                "anchors": {
                    "0": "正文没有写到微信生态。",
                    "3": "只出现「微信」两个字。",
                    "6": "有微信生态里的主体和动作。",
                    "10": "有主体、动作、日期和金额。",
                },
                "template": "{机构}{动作}{日期}",
            }
            for dim in RUBRIC_V1.dimensions
        },
    }


def _row(version: int, status: str, payload: dict | None = None, **overrides) -> dict:
    row = {
        "research_version": version,
        "status": status,
        "label_snapshot": "微信生态",
        "brief_snapshot": "微信支付、公众号、小程序、视频号里的银行动作",
        "prompt_id": DIRECTION_PROMPT_ID,
        "scope": "只考微信生态里能落地的动作",
        "out_of_scope": "与微信生态无关的内容一律记 0",
        "rubric_json": json.dumps(payload or _payload(), ensure_ascii=False),
    }
    row.update(overrides)
    return row


# ── prompt ────────────────────────────────────────────────────────────


def test_prompt_contains_the_five_hard_rules():
    prompt = build_research_prompt("微信生态", "微信支付、小程序")
    assert "保留原 key 的意思" in prompt
    assert "从银行或支付机构角度问" in prompt
    assert "锚点只写正文可见的事实" in prompt
    assert "不得评价写作" in prompt
    assert "范围外内容一律记 0" in prompt
    assert "深度、可读、文笔、观点、洞察" in prompt


def test_prompt_inputs_are_label_and_brief_only():
    prompt = build_research_prompt("微信生态", "微信支付、公众号、小程序")
    assert "微信生态" in prompt
    assert "微信支付、公众号、小程序" in prompt
    # 不能把稿件正文带进研究提示词
    assert "标题：" not in prompt
    assert "导语：" not in prompt
    # 基准十维必须整体给模型看，否则无法保留原意
    for dim in RUBRIC_V1.dimensions:
        assert dim.key in prompt


# ── parse / validate ──────────────────────────────────────────────────


def test_valid_payload_yields_ten_dimensions():
    research, error = parse_research(_payload())
    assert error is None
    assert research is not None
    assert tuple(d.key for d in research.dimensions) == RUBRIC_V1.keys
    assert [d.label for d in research.dimensions] == [d.label for d in RUBRIC_V1.dimensions]
    assert sum(d.weight for d in research.dimensions) == WEIGHT_TOTAL == 100
    assert research.scope
    assert research.out_of_scope


def test_json_string_is_accepted_and_payload_round_trips():
    raw = json.dumps(_payload(), ensure_ascii=False)
    research, error = parse_research(raw)
    assert error is None and research is not None
    again, error2 = parse_research(research.to_payload())
    assert error2 is None and again is not None
    assert [d.question for d in again.dimensions] == [d.question for d in research.dimensions]
    assert [d.anchors for d in again.dimensions] == [d.anchors for d in research.dimensions]


def test_missing_key_is_rejected():
    payload = _payload()
    payload["dimensions"].pop("downside")
    research, error = parse_research(payload)
    assert research is None
    assert error and "维度缺失" in error and "downside" in error


def test_extra_key_is_rejected():
    payload = _payload()
    payload["dimensions"]["extra_dim"] = payload["dimensions"]["rule_delta"]
    research, error = parse_research(payload)
    assert research is None
    assert error and "维度键多余" in error


def test_three_anchors_are_rejected():
    payload = _payload()
    anchors = payload["dimensions"]["rule_delta"]["anchors"]
    anchors.pop("10")
    research, error = parse_research(payload)
    assert research is None
    assert error and "锚点" in error


def test_anchor_list_is_rejected():
    """A positional list can swap two levels invisibly -- reject it."""
    payload = _payload()
    payload["dimensions"]["rule_delta"]["anchors"] = ["无", "少", "有", "全"]
    research, error = parse_research(payload)
    assert research is None
    assert error and "锚点格式不对" in error


def test_anchor_extra_level_is_rejected():
    payload = _payload()
    payload["dimensions"]["rule_delta"]["anchors"]["7"] = "多出来的一档"
    research, error = parse_research(payload)
    assert research is None
    assert error and "档位多余" in error


def test_banned_word_in_question_is_rejected():
    payload = _payload()
    payload["dimensions"]["rule_delta"]["question"] = "这件事如何赋能我方"
    research, error = parse_research(payload)
    assert research is None
    assert error and "禁词命中" in error and "赋能" in error


def test_writing_word_in_anchor_is_rejected():
    payload = _payload()
    payload["dimensions"]["named_peer_move"]["anchors"]["10"] = "文章洞察很到位"
    research, error = parse_research(payload)
    assert research is None
    assert error and "洞察" in error


def test_banned_word_in_scope_is_rejected():
    payload = _payload()
    payload["scope"] = "只考微信生态里的底层逻辑"
    research, error = parse_research(payload)
    assert research is None
    assert error and "禁词命中" in error


def test_banned_word_in_out_of_scope_is_rejected():
    payload = _payload()
    payload["out_of_scope"] = "与微信生态无关的深度分析一律记 0"
    research, error = parse_research(payload)
    assert research is None
    assert error and "禁词命中" in error and "深度" in error


def test_question_over_thirty_chars_is_rejected():
    payload = _payload()
    payload["dimensions"]["rule_delta"]["question"] = "问" * 31
    research, error = parse_research(payload)
    assert research is None
    assert error and "30" in error


def test_non_string_question_is_rejected():
    """``str(True)`` would sneak a non-question through as "True"."""
    payload = _payload()
    payload["dimensions"]["rule_delta"]["question"] = True
    research, error = parse_research(payload)
    assert research is None
    assert error and "问题为空" in error


def test_non_string_anchor_is_rejected():
    payload = _payload()
    payload["dimensions"]["rule_delta"]["anchors"]["6"] = 6
    research, error = parse_research(payload)
    assert research is None
    assert error and "锚点有空档" in error


def test_template_without_placeholder_is_rejected():
    payload = _payload()
    payload["dimensions"]["rule_delta"]["template"] = "机构动作日期"
    research, error = parse_research(payload)
    assert research is None
    assert error and "占位符" in error


def test_empty_scope_and_out_of_scope_are_rejected():
    payload = _payload()
    payload["scope"] = "  "
    research, error = parse_research(payload)
    assert research is None and error and "scope" in error

    payload = _payload()
    payload["out_of_scope"] = ""
    research, error = parse_research(payload)
    assert research is None and error and "out_of_scope" in error


def test_non_json_is_rejected():
    research, error = parse_research("这不是 JSON")
    assert research is None
    assert error == "输出不是 JSON 对象"


def test_fenced_json_is_accepted():
    raw = "```json\n" + json.dumps(_payload(), ensure_ascii=False) + "\n```"
    research, error = parse_research(raw)
    assert error is None and research is not None


def test_label_and_brief_come_from_the_caller_not_the_model():
    """The prompt never asks for a direction name -- the model may not invent one."""
    payload = _payload()
    payload["label"] = "模型自己编的方向"
    payload["brief"] = "模型自己编的说明"
    research, error = parse_research(payload, label="微信生态", brief="微信里的银行动作")
    assert error is None and research is not None
    assert research.label == "微信生态"
    assert research.brief == "微信里的银行动作"


def test_label_and_brief_default_to_empty_when_not_given():
    research, error = parse_research(_payload())
    assert error is None and research is not None
    assert research.label == ""
    assert research.brief == ""


# ── rubric / pipeline ─────────────────────────────────────────────────


def test_research_to_rubric_keeps_keys_and_numbers():
    research, _ = parse_research(_payload())
    rubric = research_to_rubric(research, 3)
    assert rubric.id == "bv_direction_v3"
    assert research.rubric_id == "bv_direction_v0"
    assert rubric.keys == RUBRIC_V1.keys
    assert [d.weight for d in rubric.dimensions] == [
        d.weight for d in RUBRIC_V1.dimensions
    ]
    assert rubric.prompt_block().count("权重") == 10


def test_research_to_rubric_rejects_a_non_positive_version():
    research, _ = parse_research(_payload())
    with pytest.raises(ValueError):
        research_to_rubric(research, 0)
    with pytest.raises(ValueError):
        research_to_rubric(research, -1)
    with pytest.raises(ValueError):
        research_to_rubric(research)


def test_research_to_rubric_rejects_a_tampered_weight():
    """A direction may rewrite questions, never the weights."""
    from dataclasses import replace

    research, _ = parse_research(_payload())
    broken = replace(
        research,
        dimensions=(
            replace(research.dimensions[0], weight=999),
            *research.dimensions[1:],
        ),
    )
    with pytest.raises(ValueError):
        research_to_rubric(broken, 1)


def test_direction_rubric_scores_through_pipeline():
    research, _ = parse_research(_payload())
    rubric = research_to_rubric(research, 1)
    quote = "微信支付发布新的商户准入规则"

    def llm(prompt: str, timeout: float):
        if "取证助手" in prompt:
            return json.dumps({"evidence": [{"id": "e1", "quote": quote}]}), 0
        if "评分员" in prompt:
            dims = {
                key: {"score": 10, "evidence_ids": ["e1"], "reason": "有事实"}
                for key in rubric.keys
            }
            return json.dumps({"dimensions": dims}), 0
        if "检查员" in prompt:
            return json.dumps({"pass": True, "reasons": []}), 0
        return "{}", 0

    snap = ArticleSnapshot(id="a1", title="微信支付新规", raw_content=BODY)
    result = score_general_bv(snap, rubric=rubric, llm=llm)
    assert result.status == "ready"
    assert result.total == 100
    assert result.breakdown["rubric"] == "bv_direction_v1"


# ── gating ────────────────────────────────────────────────────────────


LABEL = "微信生态"
BRIEF = "微信支付、公众号、小程序、视频号里的银行动作"


def test_usable_research_accepts_latest_passed():
    rows = [
        _row(1, "expired"),
        _row(2, "passed"),
    ]
    research, error = usable_research(rows, label=LABEL, brief=BRIEF)
    assert error is None and research is not None
    assert research.version == 2
    assert research.rubric_id == "bv_direction_v2"


def test_usable_research_rejects_latest_running():
    rows = [_row(1, "passed"), _row(2, "running")]
    research, error = usable_research(rows, label=LABEL, brief=BRIEF)
    assert research is None
    assert error and "running" in error


def test_usable_research_failed_never_falls_back():
    rows = [_row(1, "passed"), _row(2, "failed")]
    research, error = usable_research(rows, label=LABEL, brief=BRIEF)
    assert research is None
    assert error and "failed" in error


def test_usable_research_rejects_snapshot_mismatch():
    rows = [_row(1, "passed", brief_snapshot="旧的方向说明")]
    research, error = usable_research(rows, label=LABEL, brief=BRIEF)
    assert research is None
    assert error and "不一致" in error

    rows = [_row(1, "passed", label_snapshot="旧方向")]
    research, error = usable_research(rows, label=LABEL, brief=BRIEF)
    assert research is None
    assert error and "不一致" in error


def test_usable_research_without_rows():
    research, error = usable_research([], label=LABEL, brief=BRIEF)
    assert research is None
    assert error


def test_usable_research_rejects_broken_snapshot_json():
    rows = [_row(1, "passed", rubric_json="{oops")]
    research, error = usable_research(rows, label=LABEL, brief=BRIEF)
    assert research is None
    assert error and "JSON" in error


def test_usable_research_rejects_another_prompt_version():
    """Research produced by an older prompt must be re-run, not reused."""
    rows = [_row(1, "passed", prompt_id="bv_direction_research_v0")]
    research, error = usable_research(rows, label=LABEL, brief=BRIEF)
    assert research is None
    assert error and "提示词版本不匹配" in error


def test_usable_research_rejects_a_corrupt_version_without_falling_back():
    """A broken newest row must not be silently ignored in favour of an older one."""
    rows = [_row(1, "passed"), _row("x", "passed")]
    research, error = usable_research(rows, label=LABEL, brief=BRIEF)
    assert research is None
    assert error and "损坏" in error


def test_usable_research_rejects_a_zero_version():
    rows = [_row(1, "passed"), _row(0, "passed")]
    research, error = usable_research(rows, label=LABEL, brief=BRIEF)
    assert research is None
    assert error and "损坏" in error


def test_usable_research_rejects_a_float_version():
    rows = [_row(1, "passed"), _row(1.5, "passed")]
    research, error = usable_research(rows, label=LABEL, brief=BRIEF)
    assert research is None
    assert error and "损坏" in error


def test_usable_research_filters_by_direction_id():
    """Two directions in one table -- a row of another direction may not be used."""
    rows = [
        _row(3, "passed", direction_id=2, label_snapshot="别的方向"),
        _row(2, "passed", direction_id=1),
    ]
    research, error = usable_research(
        rows, label=LABEL, brief=BRIEF, direction_id=1
    )
    assert error is None and research is not None
    assert research.version == 2


def test_usable_research_without_that_direction():
    rows = [_row(3, "passed", direction_id=2)]
    research, error = usable_research(rows, label=LABEL, brief=BRIEF, direction_id=1)
    assert research is None
    assert error and "该方向" in error


# ── config validation ─────────────────────────────────────────────────


def test_label_rules():
    assert label_reject_reason("微信生态") is None
    assert label_reject_reason("") is not None
    assert label_reject_reason("七" * 7) is not None
    assert label_reject_reason("赋能") is not None
    assert label_reject_reason("深度") is not None


def test_brief_is_required():
    assert brief_reject_reason("微信支付、公众号") is None
    assert brief_reject_reason("   ") is not None


def test_status_constants_cover_the_state_machine():
    assert set(direction.RESEARCH_STATUSES) == {
        "running",
        "passed",
        "failed",
        "expired",
    }
