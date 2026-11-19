"""sprint-54 / id 26111703 —— I5 离线规则验证器的护栏。

核心纪律：**没有 dad 的人工标注 ⇒ 不准编造精确率 / 召回率 / 误杀率**。

覆盖：

* 无标注 ⇒ 所有指标字段为 ``None``（**不是 0**）
* 空标注集 ⇒ **无法评估**（分母 0）⇒ 指标仍为 ``None``
  （⚠️ 这**不等于**「误杀率为 0」）
* 合成标注 ⇒ 三条路径（命中 / 漏放 / 误杀）都能走到，且明确标 synthetic
* 真标注 ⇒ 出真实指标
* unsure / unknown verdict ⇒ 排除且计数正确
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "xbot_quality_validate.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("_xbot_quality_validate", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return _load_module()


def _cands(n=3, weak=False):
    return [
        {
            "article_id": f"a{i}",
            "author_handle": "@x",
            "category": "low_value_rant",
            "reason_code": "mod.lowvalue.rant",
            "evidence_tokens": ["only_bare_wechat"],
            "is_weak_only": weak,
            "is_protected": False,
            "protected_kinds": [],
            "protected_article_ids": [],
        }
        for i in range(n)
    ]


# ── 无标注 ⇒ 不报准确率 ─────────────────────────────────────────────────────

def test_no_annotations_reports_none_metrics(mod):
    """⚠️ **核心纪律**：无人工标注 ⇒ 指标一律 ``None``。"""
    res = mod.evaluate(_cands(3), {}, annotations_source="")
    for k in ("precision", "recall", "f1", "false_kill_rate", "miss_rate"):
        assert res["metrics"][k] is None, f"{k} 在无标注时不该有值"
    assert "无人工标注" in res["metrics_status"]


def test_no_annotations_does_not_report_zero_as_metric(mod):
    """⚠️ ``None`` ≠ ``0`` —— 没标不等于「指标是 0」。

    ⇒ 所有指标必须是 ``None``（**未评估**），而不是 ``0``（**评估过且为零**）。
    """
    res = mod.evaluate(_cands(3), {}, annotations_source="")
    for k in ("precision", "recall", "f1", "false_kill_rate", "miss_rate"):
        assert res["metrics"][k] is None, f"{k} 必须是 None（未评估），不能是 0"
        assert res["metrics"][k] != 0, f"{k} 不得被当成 0 报出去"


def test_empty_annotation_set_is_not_zero_false_kill(mod):
    """⚠️ **空标注集 ⇒ 无法评估**，不是「误杀率 0」。"""
    res = mod.evaluate(_cands(3), {}, annotations_source="")
    assert res["metrics"]["false_kill_rate"] is None
    assert "无人工标注" in res["metrics_status"]
    # 未标注候选被单列出来
    assert len(res["unannotated_candidate_ids"]) == 3


def test_all_unsure_annotations_yields_none_metrics(mod):
    """⚠️ 全是 ``unsure`` ⇒ 分母 0 ⇒ **无法评估**。"""
    ann = {"a0": "unsure", "a1": "unsure", "a2": "unsure"}
    res = mod.evaluate(_cands(3), ann, annotations_source="x.json")
    assert res["metrics"]["precision"] is None
    assert res["counts"]["unsure_excluded"] == 3
    assert res["counts"]["true_positive_hits"] == 0


# ── 合成标注 ⇒ 流程跑通 ─────────────────────────────────────────────────────

def test_synthetic_exercises_all_three_paths(mod):
    """⚠️ 合成标注 ⇒ 命中 / 漏放 / 误杀 **三条路径都走到**。"""
    cands = _cands(3)
    ann = mod.build_synthetic_annotations(cands)
    res = mod.evaluate(cands, ann, synthetic=True, annotations_source="synthetic")
    assert res["counts"]["true_positive_hits"] >= 1, "命中路径没走到"
    assert res["counts"]["false_negative_missed"] >= 1, "漏放路径没走到"
    assert res["counts"]["false_positive_false_kills"] >= 1, "误杀路径没走到"


def test_synthetic_is_flagged_not_truth(mod):
    """⚠️ 合成结果必须明确标 **synthetic**，不得冒充真值。"""
    cands = _cands(3)
    res = mod.evaluate(
        cands, mod.build_synthetic_annotations(cands), synthetic=True,
        annotations_source="synthetic",
    )
    assert res["synthetic_annotations"] is True
    assert "不是真值" in res["metrics_status"]


def test_synthetic_marked_in_json_output(mod):
    """⚠️ JSON 输出里也必须带 synthetic 标记。"""
    cands = _cands(2)
    res = mod.evaluate(
        cands, mod.build_synthetic_annotations(cands), synthetic=True,
        annotations_source="synthetic",
    )
    blob = json.dumps(res, ensure_ascii=False)
    assert '"synthetic_annotations": true' in blob


# ── 真标注 ⇒ 出真实指标 ─────────────────────────────────────────────────────

def test_real_annotations_produce_real_metrics(mod):
    """⚠️ 有 dad 标注 ⇒ 出真实指标 + 注明来源。"""
    ann = {
        "a0": "quarantine",   # 规则命中 + dad 判劣质 ⇒ TP
        "a1": "keep",         # 规则命中 + dad 判正常 ⇒ FP (误杀)
        "zzz": "quarantine",  # 规则没标记 + dad 判劣质 ⇒ FN (漏放)
    }
    res = mod.evaluate(_cands(3), ann, annotations_source="dad_labels.json")
    assert res["counts"]["true_positive_hits"] == 1
    assert res["counts"]["false_positive_false_kills"] == 1
    assert res["counts"]["false_negative_missed"] == 1
    assert res["metrics"]["precision"] == 0.5
    assert res["metrics"]["recall"] == 0.5
    assert res["metrics"]["f1"] == 0.5
    assert "dad_labels.json" in res["metrics_status"]


def test_unknown_verdict_is_excluded_and_counted(mod):
    """⚠️ 认不出的 verdict ⇒ 排除并计数，**不当成 good**。"""
    ann = {"a0": "quarantine", "a1": "banana", "a2": "keep"}
    res = mod.evaluate(_cands(3), ann, annotations_source="x")
    assert res["counts"]["unknown_verdict_excluded"] == 1
    # banana 不该被算成 TP 或 FP
    assert res["counts"]["true_positive_hits"] == 1
    assert res["counts"]["false_positive_false_kills"] == 1


def test_verdict_normalization(mod):
    """⚠️ verdict 取值兼容中英文。"""
    assert mod.classify_verdict("quarantine") == "bad"
    assert mod.classify_verdict("劣质") == "bad"
    assert mod.classify_verdict("keep") == "good"
    assert mod.classify_verdict("正常") == "good"
    assert mod.classify_verdict("unsure") == "unsure"
    assert mod.classify_verdict("不确定") == "unsure"
    assert mod.classify_verdict("") == "unknown"
    assert mod.classify_verdict("banana") == "unknown"


# ── 标注集解析 ──────────────────────────────────────────────────────────────

def test_extract_annotations_from_template_shape(mod):
    """⚠️ 能从 I4 的 ``annotation-template.json`` 形状里取出标注。"""
    payload = {
        "rows": [
            {"article_id": "a0", "dad_verdict": "quarantine"},
            {"article_id": "a1", "dad_verdict": ""},
        ]
    }
    out = mod.extract_annotations(payload)
    assert out == {"a0": "quarantine", "a1": ""}


def test_extract_annotations_from_flat_shape(mod):
    """⚠️ 兼容扁平 ``{article_id: verdict}``。"""
    out = mod.extract_annotations({"a0": "keep", "a1": "quarantine"})
    assert out["a0"] == "keep"
    assert out["a1"] == "quarantine"


def test_extract_candidates_both_shapes(mod):
    """⚠️ 兼容带 meta 包装与裸 list 两种候选形状。"""
    assert len(mod.extract_candidates({"candidates": _cands(2)})) == 2
    assert len(mod.extract_candidates(_cands(3))) == 3


# ── 只读边界 ────────────────────────────────────────────────────────────────

def test_validator_source_has_no_write_sql():
    """⚠️ **静态断言**：验证器只读 JSON, **不**写库。"""
    src = _SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ", "sqlite3"):
        assert forbidden not in src, f"验证器不该出现 {forbidden!r}"


def test_validator_does_not_touch_db(tmp_path):
    """⚠️ 验证器**只**读文件 —— 跑完不产生任何 DB 副作用。"""
    import sqlite3

    db = tmp_path / "x.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t (a INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()

    before = db.read_bytes()

    cands = tmp_path / "c.json"
    cands.write_text(json.dumps({"candidates": _cands(2)}), encoding="utf-8")

    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, str(_SCRIPT), "--candidates", str(cands), "--json"],
        capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/Users/mt16"},
    )
    assert r.returncode == 0, r.stderr[-500:]
    assert db.read_bytes() == before, "验证器动了 DB"
