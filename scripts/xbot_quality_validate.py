#!/usr/bin/env python3
"""sprint-54 / id 26111703 —— I5：xbot 内容规则的**离线验证器**。

⚠️ **只读**：本脚本**只**执行 ``SELECT``（读标注集 JSON + 读候选 JSON）。

## 它做什么

把**候选清单**与**人工标注集**对照，算出三类计数：

| 指标 | 含义 |
|---|---|
| **命中 (hit / TP)** | 规则判为劣质，dad 也判为劣质 |
| **漏放 (miss / FN)** | 规则判为**正常**，dad 判为**劣质** ⇒ 规则太松 |
| **误杀 (false positive)** | 规则判为**劣质**，dad 判为**正常** ⇒ 规则太狠 |

## ⚠️ 最重要的一条纪律

**没有 dad 的人工标注 ⇒ 不准编造精确率 / 召回率 / 误杀率。**

本脚本因此分两种运行模式：

- ``--annotations`` **给了**人工标注 ⇒ 输出真实 precision / recall / F1 /
  误杀率（并标明标注集大小与来源）。
- **没给**人工标注（只给候选，或标注集为空）⇒ **只输出计数与流程演示**，
  并且所有准确率字段一律为 ``None``（**不是 0，也不是猜的**），
  同时在 ``metrics_status`` 里写明「无人工标注 ⇒ 无准确率」。

⚠️ **空标注集与「全部规则都没命中」是两回事** ——
空标注集 ⇒ **无法评估**（分母为 0），不是「误杀率为 0」。

## 流程可测（合成标注集）

``--synthetic`` 用**规则自身**造一份合成标注集，**只用于演示流程跑通**
（命中 / 漏放 / 误杀三条路径都能走到）。
合成集的每个样本都会标 ``synthetic=True``，且输出的
``metrics_status`` 会写明「**合成标注集 —— 不是真值**」。

用法::

    # 1) 无标注 ⇒ 只出计数, 准确率字段为 None
    python3 scripts/xbot_quality_validate.py --candidates artifacts/.../candidates.json

    # 2) 合成标注 ⇒ 演示流程跑通 (明确标注 synthetic)
    python3 scripts/xbot_quality_validate.py --candidates ... --synthetic

    # 3) dad 人工标注 ⇒ 出真实指标
    python3 scripts/xbot_quality_validate.py --candidates ... --annotations my_labels.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: dad 判定为「劣质」的 verdict 取值
BAD_VERDICTS = {"quarantine", "bad", "keep_out", "劣质"}
#: dad 判定为「正常」的 verdict 取值
GOOD_VERDICTS = {"keep", "good", "normal", "正常"}
#: dad 判定为「不确定」
UNSURE_VERDICTS = {"unsure", "unknown", "不确定"}


def _norm_verdict(v) -> str:
    return str(v or "").strip().lower()


def classify_verdict(v) -> str:
    """把 dad 的 verdict 归成 ``bad`` / ``good`` / ``unsure`` / ``unknown``。"""
    n = _norm_verdict(v)
    if not n:
        return "unknown"
    if n in BAD_VERDICTS:
        return "bad"
    if n in GOOD_VERDICTS:
        return "good"
    if n in UNSURE_VERDICTS:
        return "unsure"
    return "unknown"


def load_json(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"文件不存在: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def extract_candidates(payload: dict) -> list[dict]:
    """从候选产物里取出候选列表（兼容两种形状）。"""
    if isinstance(payload, dict) and "candidates" in payload:
        return list(payload["candidates"])
    if isinstance(payload, list):
        return list(payload)
    raise SystemExit("无法识别的候选 JSON 形状")


def extract_annotations(payload: dict) -> dict[str, str]:
    """从标注集里取出 ``{article_id: verdict}``。

    兼容 ``annotation-template.json`` 的形状 (``rows`` 里有 ``article_id``
    与 ``dad_verdict``），也兼容 ``{article_id: verdict}`` 扁平形状。
    """
    rows = None
    if isinstance(payload, dict):
        rows = payload.get("rows")
        if rows is None and "annotations" in payload:
            rows = payload["annotations"]
        if rows is None:
            # ⚠️ 扁平形状 ``{article_id: verdict}`` —— 排除 meta 之类的元数据键
            flat = {
                str(k): str(val)
                for k, val in payload.items()
                if isinstance(val, str)
            }
            if flat:
                return flat
    if rows is None and isinstance(payload, list):
        rows = payload
    out: dict[str, str] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        aid = r.get("article_id")
        verdict = r.get("dad_verdict", r.get("verdict"))
        if aid is None:
            continue
        out[str(aid)] = str(verdict or "")
    return out


def build_synthetic_annotations(candidates: list[dict]) -> dict[str, str]:
    """造一份**合成**标注集 —— 只为演示流程跑通。

    规则：

    - 弱特征命中 ⇒ 标 ``unsure``（dad 大概率也拿不准）
    - 强信号命中 ⇒ 标 ``bad``
    - 加上**故意**的漏放与误杀样本，让三条路径都能走到

    ⚠️ 产出全部标 ``synthetic=True`` ⇒ 下游**不得**当真值。
    """
    out: dict[str, str] = {}
    for i, c in enumerate(candidates):
        if c.get("is_weak_only"):
            out[str(c["article_id"])] = "unsure"
        else:
            out[str(c["article_id"])] = "bad"
    # 注入漏放样本: 一条规则判为正常 (不在候选里) 的文章
    out["__SYNTHETIC_MISSED__"] = "bad"
    # 注入误杀样本: 一条规则判为劣质 但 dad 认为是正常的
    if candidates:
        out[str(candidates[0]["article_id"])] = "keep"
    return out


def evaluate(
    candidates: list[dict],
    annotations: dict[str, str],
    *,
    synthetic: bool = False,
    annotations_source: str = "",
) -> dict:
    """对照候选与标注, 算三类计数 + （有真值时）准确率。"""
    flagged_ids = {str(c["article_id"]) for c in candidates}

    # 规则判定: 在候选里 ⇒ flagged(劣质); 不在 ⇒ missed(规则放行)
    # ⚠️ 标注集覆盖不到的文章 ⇒ 不计入 (无法评估)
    tp = fp = tn = fn = 0
    unsure = 0
    unknown_verdict = 0
    unannotated = 0

    detail = {
        "hits": [],       # TP
        "false_positives": [],  # FP (误杀)
        "misses": [],     # FN (漏放)
        "correct_negatives": [],  # TN
    }

    for aid, verdict in annotations.items():
        cls = classify_verdict(verdict)
        if cls == "unknown":
            if verdict.strip():
                unknown_verdict += 1
            continue
        if cls == "unsure":
            unsure += 1
            continue
        if aid == "__SYNTHETIC_MISSED__":
            # 规则没抓到 ⇒ 漏放 (FN)
            fn += 1
            detail["misses"].append({"article_id": aid, "verdict": verdict,
                                      "rule": "not_flagged"})
            continue
        flagged = aid in flagged_ids
        if flagged and cls == "bad":
            tp += 1
            detail["hits"].append({"article_id": aid, "verdict": verdict})
        elif flagged and cls == "good":
            fp += 1
            detail["false_positives"].append({"article_id": aid, "verdict": verdict})
        elif not flagged and cls == "bad":
            fn += 1
            detail["misses"].append({"article_id": aid, "verdict": verdict,
                                      "rule": "not_flagged"})
        else:
            tn += 1
            detail["correct_negatives"].append({"article_id": aid, "verdict": verdict})

    # 候选里**没有**标注的文章 ⇒ 无法评估, 单列
    unannotated_ids = sorted(flagged_ids - set(annotations))

    has_truth = bool(annotations) and not synthetic and (tp + fp + tn + fn) > 0

    result = {
        "counts": {
            "candidates": len(candidates),
            "flagged_by_rule": len(flagged_ids),
            "annotated_total": len([a for a in annotations if a != "__SYNTHETIC_MISSED__"]),
            "true_positive_hits": tp,
            "false_positive_false_kills": fp,
            "false_negative_missed": fn,
            "true_negative": tn,
            "unsure_excluded": unsure,
            "unknown_verdict_excluded": unknown_verdict,
            "unannotated_candidates": len(unannotated_ids),
        },
        "metrics": {
            "precision": None,
            "recall": None,
            "f1": None,
            "false_kill_rate": None,
            "miss_rate": None,
        },
        "metrics_status": "",
        "annotations_source": annotations_source,
        "synthetic_annotations": bool(synthetic),
        "detail": detail,
        "unannotated_candidate_ids": unannotated_ids,
    }

    if synthetic:
        result["metrics_status"] = (
            "合成标注集 —— **不是真值**；本节数字只演示流程跑通，"
            "**不得**当精确率/召回率/误杀率使用"
        )
        # 流程演示: 也算一遍, 但明确标注 synthetic
        if tp + fp:
            result["metrics"]["precision"] = round(tp / (tp + fp), 4)
        if tp + fn:
            result["metrics"]["recall"] = round(tp / (tp + fn), 4)
        if result["metrics"]["precision"] and result["metrics"]["recall"]:
            p, r = result["metrics"]["precision"], result["metrics"]["recall"]
            result["metrics"]["f1"] = round(2 * p * r / (p + r), 4) if (p + r) else None
        if tp + tn + fp:
            result["metrics"]["false_kill_rate"] = round(fp / (fp + tn), 4)
        if tp + fn:
            result["metrics"]["miss_rate"] = round(fn / (tp + fn), 4)
    elif not annotations:
        result["metrics_status"] = (
            "**无人工标注** ⇒ 不报精确率/召回率/误杀率。"
            "候选清单只是 agent 草标，不是真值。"
        )
    elif not has_truth:
        result["metrics_status"] = (
            "标注集为空或全为 unsure/unknown ⇒ 分母为 0 ⇒ **无法评估**"
            "（这**不等于**误杀率为 0）"
        )
    else:
        result["metrics_status"] = (
            f"基于人工标注（来源: {annotations_source or '未注明'}，"
            f"有效样本 {tp + fp + tn + fn} 条）"
        )
        if tp + fp:
            result["metrics"]["precision"] = round(tp / (tp + fp), 4)
        if tp + fn:
            result["metrics"]["recall"] = round(tp / (tp + fn), 4)
        p, r = result["metrics"]["precision"], result["metrics"]["recall"]
        if p and r:
            result["metrics"]["f1"] = round(2 * p * r / (p + r), 4)
        if fp + tn:
            result["metrics"]["false_kill_rate"] = round(fp / (fp + tn), 4)
        if tp + fn:
            result["metrics"]["miss_rate"] = round(fn / (tp + fn), 4)

    return result


def render_text(result: dict) -> str:
    c = result["counts"]
    m = result["metrics"]
    lines = [
        "=" * 72,
        "xbot 内容规则 —— 离线验证（I5）",
        "=" * 72,
        f"候选文章数            : {c['candidates']}",
        f"规则标记为劣质        : {c['flagged_by_rule']}",
        f"标注集条目            : {c['annotated_total']}",
        "",
        "── 计数 ──",
        f"  命中 (TP, 规则与 dad 一致判劣质) : {c['true_positive_hits']}",
        f"  误杀 (FP, 规则判劣质 dad 判正常) : {c['false_positive_false_kills']}",
        f"  漏放 (FN, 规则放行 dad 判劣质)   : {c['false_negative_missed']}",
        f"  正确放行 (TN)                     : {c['true_negative']}",
        f"  排除: unsure {c['unsure_excluded']} / unknown {c['unknown_verdict_excluded']}",
        f"  未标注候选 (无法评估)              : {c['unannotated_candidates']}",
        "",
        "── 指标 ──",
    ]
    for k in ("precision", "recall", "f1", "false_kill_rate", "miss_rate"):
        v = m[k]
        lines.append(f"  {k:16s}: {v if v is not None else 'None（无真值 ⇒ 不报）'}")
    lines += [
        "",
        f"⚠️ {result['metrics_status']}",
    ]
    if result["synthetic_annotations"]:
        lines.append("⚠️ 本次用的是**合成标注集** —— 数字只演示流程，**不是真值**。")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidates", required=True, help="候选 JSON (I4 产物)")
    ap.add_argument(
        "--annotations",
        default=None,
        help="dad 人工标注 JSON；**不给** ⇒ 不报任何准确率",
    )
    ap.add_argument(
        "--synthetic",
        action="store_true",
        help="用**合成**标注集演示流程跑通（明确标注 synthetic，非真值）",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    candidates = extract_candidates(load_json(args.candidates))

    if args.synthetic:
        annotations = build_synthetic_annotations(candidates)
        source = "synthetic"
    elif args.annotations:
        annotations = extract_annotations(load_json(args.annotations))
        source = args.annotations
    else:
        annotations = {}
        source = ""

    result = evaluate(
        candidates, annotations, synthetic=args.synthetic, annotations_source=source
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
