"""Unit tests for the business-value harness (design doc §6, §9)."""
from __future__ import annotations

import json

from moni.scoring import bv_pipeline, rubric
from moni.scoring.bv_pipeline import score_general_bv, short_llm_error
from moni.scoring.snapshot import ArticleSnapshot

BODY = (
    "正文句子一：2024 年 5 月 10 日，人民银行发布《非银行支付机构监督管理条例实施细则》，"
    "要求支付机构在 2024 年 12 月 31 日前完成备案整改，涉及系统改造预算约 3000 万元。"
    "正文句子二：招商银行 2024 年 3 月上线企业微信代发权益，面向代发客户发放立减金。"
    "正文句子三：收单机构需改造商户准入系统与清算接口，满足新的报备要求。"
    "正文句子四：未按期整改的机构将被暂停新增商户，并处以罚款。"
)

QUOTE_1 = "人民银行发布《非银行支付机构监督管理条例实施细则》"
QUOTE_2 = "招商银行 2024 年 3 月上线企业微信代发权益"
QUOTE_3 = "收单机构需改造商户准入系统与清算接口"


def _snap(body: str = BODY) -> ArticleSnapshot:
    return ArticleSnapshot(id="a1", title="某支付新规", raw_content=body, publisher="某报")


def _all(score: int, ids: list[str]) -> dict:
    return {
        key: {"score": score, "evidence_ids": ids, "reason": f"{key} 有事实"}
        for key in rubric.KEYS
    }


def make_llm(
    scores: dict | None = None,
    evidence: list[dict] | None = None,
    audit_pass: bool = True,
    rescore: dict | None = None,
):
    evidence = evidence or [
        {"id": "e1", "quote": QUOTE_1},
        {"id": "e2", "quote": QUOTE_2},
        {"id": "e3", "quote": QUOTE_3},
    ]

    def llm(prompt: str, timeout: float):
        if "上一轮打分没有通过检查" in prompt:
            return json.dumps({"dimensions": rescore or scores or {}}), 0
        if "取证助手" in prompt:
            return json.dumps({"evidence": evidence}), 0
        if "评分员" in prompt:
            return json.dumps({"dimensions": scores or {}}), 0
        if "检查员" in prompt:
            return json.dumps({"pass": audit_pass, "reasons": [] if audit_pass else ["理由空"]}), 0
        return "{}", 0

    return llm


def test_short_body_is_insufficient_without_calls():
    result = score_general_bv(_snap("短" * 199), llm=make_llm())
    assert result.status == "insufficient"
    assert result.total is None
    assert result.calls_used == 0
    assert "正文过短" in result.breakdown["note"]
    assert "素材不足" not in result.breakdown["note"]


def test_no_llm_is_budget_skipped():
    result = score_general_bv(_snap(), llm=None)
    assert result.status == "budget_skipped"
    assert result.total is None


def test_model_total_is_discarded():
    llm = make_llm(scores=_all(10, ["e1"]))

    def with_total(prompt: str, timeout: float):
        out, code = llm(prompt, timeout)
        if "评分员" in prompt:
            data = json.loads(out)
            data["score"] = 99
            data["total"] = 99
            return json.dumps(data), code
        return out, code

    result = score_general_bv(_snap(), llm=with_total)
    assert result.status == "ready"
    assert result.total == 100


def test_all_zero_is_ready_zero():
    llm = make_llm(scores=_all(0, []), evidence=[{"id": "e1", "quote": QUOTE_1}])
    result = score_general_bv(_snap(), llm=llm)
    assert result.status == "ready"
    assert result.total == 0
    assert result.note == "已评 0 分：10 问均无对应内容"


def test_missing_key_fails_audit():
    scores = _all(4, ["e1"])
    scores.pop("adoption_cost")
    result = score_general_bv(_snap(), llm=make_llm(scores=scores))
    assert result.status == "needs_review"
    dim = next(d for d in result.breakdown["dimensions"] if d["key"] == "adoption_cost")
    assert dim["cap_reason"] == "missing"


def test_no_evidence_caps_at_three():
    scores = _all(8, [])
    result = score_general_bv(_snap(), llm=make_llm(scores=scores))
    dim = result.breakdown["dimensions"][0]
    assert dim["score"] <= 3
    assert dim["cap_reason"] == "no_valid_evidence"


def test_non_substring_and_short_quotes_are_dropped():
    evidence = [
        {"id": "e1", "quote": "这句话原文里根本没有出现过"},
        {"id": "e2", "quote": "支付"},
        {"id": "e3", "quote": QUOTE_1},
    ]
    result = score_general_bv(_snap(), llm=make_llm(scores=_all(5, ["e1"]), evidence=evidence))
    reasons = {d["reason"] for d in result.breakdown["dropped_evidence"]}
    assert "not_substring" in reasons
    assert "too_short" in reasons


def test_non_integer_score_is_zero_and_review():
    scores = _all(4, ["e1"])
    scores["rule_delta"] = {"score": 7.5, "evidence_ids": ["e1"], "reason": "小数"}
    result = score_general_bv(_snap(), llm=make_llm(scores=scores))
    dim = next(d for d in result.breakdown["dimensions"] if d["key"] == "rule_delta")
    assert dim["score"] == 0
    assert dim["cap_reason"] == "non_integer"
    assert result.status == "needs_review"


def test_banned_reason_never_reaches_note():
    scores = _all(8, ["e1"])
    for key in scores:
        scores[key]["reason"] = "凸显了某行的领先地位"
    result = score_general_bv(_snap(), llm=make_llm(scores=scores))
    assert result.status == "needs_review"
    assert "凸显" not in result.note
    assert "分" in result.note


def test_all_six_uses_no_dimension_sentence():
    result = score_general_bv(_snap(), llm=make_llm(scores=_all(6, ["e1"])))
    assert result.status == "ready"
    assert result.note.startswith("各维均未达 7 分")
    assert "未涉及" not in result.note


def test_audit_failure_triggers_one_rescore():
    calls = {"n": 0}
    base = make_llm(scores=_all(3, ["e1"]), audit_pass=False)
    good = make_llm(scores=_all(4, ["e1"]), audit_pass=True)

    def llm(prompt: str, timeout: float):
        calls["n"] += 1
        if "上一轮打分没有通过检查" in prompt:
            return good(prompt, timeout)
        return base(prompt, timeout)

    result = score_general_bv(_snap(), llm=llm)
    assert result.status == "ready"
    assert result.calls_used <= 5


def test_weights_and_single_dimension_totals():
    assert rubric.WEIGHT_TOTAL == 100
    only_rule = _all(0, [])
    only_rule["rule_delta"] = {"score": 10, "evidence_ids": ["e1"], "reason": "文件与日期齐"}
    result = score_general_bv(_snap(), llm=make_llm(scores=only_rule))
    assert result.total == 14

    only_cost = _all(0, [])
    only_cost["adoption_cost"] = {"score": 10, "evidence_ids": ["e1"], "reason": "系统与预算齐"}
    result = score_general_bv(_snap(), llm=make_llm(scores=only_cost))
    assert result.total == 10


def test_evidence_normalization_folds_punctuation():
    assert bv_pipeline.normalize("，。；：！？") == ",.;:!?"
    assert bv_pipeline.normalize(" 中 文　空 格 ") == "中文空格"


def test_custom_rubric_replaces_questions_and_id():
    """V2 will plug a direction rubric in without touching the pipeline."""
    from moni.scoring.rubric import Dimension, Rubric

    dim = Dimension(
        "only_question",
        "唯一",
        100,
        "这件事改变我方什么？",
        ("没有事实。", "只有口号。", "有主体无日期。", "主体、动作与日期齐全。"),
        "{机构}{动作}{日期}",
    )
    custom = Rubric("bv_rubric_test", (dim,))

    def llm(prompt: str, timeout: float):
        if "取证助手" in prompt:
            return json.dumps({"evidence": [{"id": "e1", "quote": QUOTE_1}]}), 0
        if "评分员" in prompt:
            return json.dumps(
                {
                    "dimensions": {
                        "only_question": {
                            "score": 10,
                            "evidence_ids": ["e1"],
                            "reason": "文件与日期齐",
                        }
                    }
                }
            ), 0
        if "检查员" in prompt:
            return json.dumps({"pass": True, "reasons": []}), 0
        return "{}", 0

    result = score_general_bv(_snap(), custom, llm=llm)
    assert result.status == "ready"
    assert result.total == 100
    assert result.breakdown["rubric"] == "bv_rubric_test"
    assert [d["key"] for d in result.breakdown["dimensions"]] == ["only_question"]


def test_call_count_never_exceeds_five():
    """topup + rescore + audit must not add up to a sixth call (design §4.6)."""
    calls = {"n": 0}
    bad = _all(3, ["e1"])
    bad["rule_delta"]["reason"] = "凸显了某行的领先地位"  # forces a rescore

    def llm(prompt: str, timeout: float):
        calls["n"] += 1
        if "取证助手" in prompt:
            return json.dumps({"evidence": [{"id": "e1", "quote": QUOTE_1}]}), 0
        if "找不到" in prompt:  # top-up prompt
            return json.dumps(
                {"evidence": [{"id": "e1", "quote": QUOTE_2}]}
            ), 0
        if "上一轮打分没有通过检查" in prompt:
            return json.dumps({"dimensions": _all(4, ["e1"])}), 0
        if "评分员" in prompt:
            return json.dumps({"dimensions": bad}), 0
        if "检查员" in prompt:
            return json.dumps({"pass": False, "reasons": ["理由空"]}), 0
        return "{}", 0

    result = score_general_bv(_snap(), llm=llm)
    assert result.calls_used <= 5
    assert calls["n"] <= 5
    assert result.status == "needs_review"


def test_topup_evidence_ids_are_unique():
    def llm(prompt: str, timeout: float):
        if "取证助手" in prompt:
            return json.dumps({"evidence": [{"id": "e1", "quote": QUOTE_1}]}), 0
        if "找不到" in prompt:
            return json.dumps(
                {
                    "evidence": [
                        {"id": "e1", "quote": QUOTE_2},
                        {"id": "e2", "quote": QUOTE_3},
                    ]
                }
            ), 0
        if "评分员" in prompt:
            return json.dumps({"dimensions": _all(4, ["e1", "e2", "e3"])}), 0
        if "检查员" in prompt:
            return json.dumps({"pass": True, "reasons": []}), 0
        return "{}", 0

    result = score_general_bv(_snap(), llm=llm)
    evidence = result.breakdown["evidence"]
    ids = [e["id"] for e in evidence]
    assert ids == [f"e{i}" for i in range(1, len(ids) + 1)]
    assert len({e["quote"] for e in evidence}) == len(evidence)


def test_dates_with_spaces_are_parsed():
    assert bv_pipeline._dates_in(["2026 年 12 月 1 日截止"]) == ["2026-12-01"]
    assert bv_pipeline._dates_in(["2026年12月1日截止"]) == ["2026-12-01"]


def test_long_body_windows_keep_the_tail():
    head = "开头段落。" * 200
    tail = "尾段：2026 年 12 月 1 日截止，预算 3000 万元。"
    middle = "\n\n".join(["中间段落。" * 100] * 20)
    body = "\n\n".join([head, middle, tail])
    assert len(body) > 6000
    sampled = bv_pipeline._windows(body)
    assert tail in sampled
    assert head[:40] in sampled
    assert len(sampled) < len(body)


def test_windows_keep_head_when_a_paragraph_exactly_fills_a_window():
    head = "首" * 2999
    dense = "2026 年 12 月 1 日预算 3000 万元" * 40  # ~800 字，含日期与数字
    body = "\n\n".join([head, *([dense] * 9), "尾段：2026 年 12 月 1 日截止。"])
    assert len(bv_pipeline._windows(body)) < len(body)  # >4 窗，确实走了筛选分支
    sampled = bv_pipeline._windows(body)
    assert not sampled.startswith("\n\n")
    assert head[:50] in sampled


def test_short_llm_error_drops_the_prompt_payload():
    """The provider exception embeds the whole prompt; it must not reach the DB."""
    raw = (
        "hermes failed (failover exhausted): __profile__: TimeoutExpired: "
        "Command '['hermes', '-z', '这里是整篇文章正文，很长很长']' timed out after 90 seconds"
    )
    short = short_llm_error(raw)
    assert "这里是整篇文章正文" not in short
    assert "超时" in short and "90" in short


def test_short_llm_error_keeps_a_short_reason():
    assert "配额" in short_llm_error("provider 配额不足")
    assert short_llm_error("") == ""


def test_failure_reason_reaches_the_result():
    """A timeout must say "超时", not a bare "打分失败"."""
    def llm(prompt: str, timeout: float):
        return "", 1, "所有候选模型都失败：模型调用超时（90 秒）"

    result = score_general_bv(_snap(), llm=llm)
    assert result.status == "failed"
    assert "超时" in (result.error or "")


def test_two_tuple_llm_still_works():
    """Older callers return ``(text, code)``; the reason is optional."""
    def llm(prompt: str, timeout: float):
        return "", 1

    result = score_general_bv(_snap(), llm=llm)
    assert result.status == "failed"
    assert result.error == "取证失败"
