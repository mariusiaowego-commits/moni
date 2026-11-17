"""Business-value harness: evidence -> ten scores -> audit -> rescore.

Pure pipeline.  The caller injects an ``llm`` callable and owns persistence.
The model never supplies the total: it is computed here from the weights.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable

from .rubric import (
    MAX_EVIDENCE,
    MIN_BODY_CHARS,
    MIN_QUOTE_CHARS,
    NO_EVIDENCE_CAP,
    NOTE_MAX_CHARS,
    RUBRIC_V1,
    Rubric,
    banned_hits,
)
from .snapshot import ArticleSnapshot

LLM = Callable[[str, float], tuple[str, int]]

STATUS_INSUFFICIENT = "insufficient"
STATUS_READY = "ready"
STATUS_REVIEW = "needs_review"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "budget_skipped"

_MIN_CALL_BUDGET = 15.0
_PUNCT_MAP = str.maketrans(
    {
        "。": ".",
        "、": ",",
        "；": ";",
        "：": ":",
        "！": "!",
        "？": "?",
        "「": '"',
        "」": '"',
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
        "《": "<",
        "》": ">",
        "　": " ",
    }
)
_DATE_RE = re.compile(r"(20\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})")
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

#: 长文取证窗口：≤3000 字/窗，首尾各一 + 数字/日期密度最高的两个。
WINDOW_CHARS = 3000
WINDOW_LIMIT = 4
LONG_BODY_CHARS = 6000


@dataclass(frozen=True)
class BvResult:
    status: str
    total: int | None = None
    note: str = ""
    breakdown: dict | None = None
    error: str | None = None
    content_hash: str = ""
    calls_used: int = 0


def normalize(text: str) -> str:
    """NFKC + punctuation fold + strip all whitespace."""
    folded = unicodedata.normalize("NFKC", text or "").translate(_PUNCT_MAP)
    return re.sub(r"\s+", "", folded)


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def _today() -> str:
    return datetime.now(timezone.utc).astimezone().date().isoformat()


def parse_json_object(raw: str) -> dict | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None
    return None


class _Caller:
    def __init__(
        self,
        llm: LLM | None,
        budget_s: float,
        step_timeout: float,
        max_calls: int = 5,
    ):
        self._llm = llm
        self.budget = budget_s
        self.step = step_timeout
        self.calls = 0
        self.max_calls = max_calls

    def can_call(self) -> bool:
        return (
            self._llm is not None
            and self.budget >= _MIN_CALL_BUDGET
            and self.calls < self.max_calls
        )

    def call(self, prompt: str) -> dict | None:
        if not self.can_call():
            return None
        timeout = max(_MIN_CALL_BUDGET, min(self.step, self.budget))
        started = time.monotonic()
        out, code = self._llm(prompt, timeout)
        self.calls += 1
        self.budget -= max(time.monotonic() - started, 0.0)
        if code != 0:
            return None
        return parse_json_object(out)


def _windows(body: str) -> str:
    """Cheap long-body sampling for the evidence pass (design §6.2).

    Packs paragraphs into ≤3000-char chunks, then keeps the first, the last
    and the two densest (numbers + dates) middle chunks.  The selection is
    concatenated into the *same* evidence call, never a second one.
    """
    if len(body) <= LONG_BODY_CHARS:
        return body
    chunks: list[str] = []
    current = ""
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para:
            continue
        if len(para) > WINDOW_CHARS:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(para[i : i + WINDOW_CHARS] for i in range(0, len(para), WINDOW_CHARS))
            continue
        if len(current) + len(para) + 2 <= WINDOW_CHARS:
            current = f"{current}\n\n{para}" if current else para
        else:
            if current:
                chunks.append(current)
            current = para
    if current:
        chunks.append(current)
    if len(chunks) <= WINDOW_LIMIT:
        return "\n\n".join(chunks)

    def density(text: str) -> int:
        return len(_NUM_RE.findall(text)) + 2 * len(_DATE_RE.findall(text))

    middle = list(enumerate(chunks[1:-1], start=1))
    middle.sort(key=lambda item: (-density(item[1]), item[0]))
    keep = {0, len(chunks) - 1} | {idx for idx, _ in middle[: WINDOW_LIMIT - 2]}
    return "\n\n".join(chunks[i] for i in sorted(keep))


def _evidence_prompt(snap: ArticleSnapshot) -> str:
    return (
        "你是 moni 取证助手。只从下面正文中摘录原句，不要改写、不要总结、不要编造。\n"
        f"最多 {MAX_EVIDENCE} 句，每句必须是正文里连续出现、至少 {MIN_QUOTE_CHARS} 个字的原句。\n"
        "优先摘能回答这十类问题的句子：规则与文件、机构动作、产品规则、系统实现、客群动作、"
        "资金路径、损失与处罚、适用范围、日期与截止、牌照与预算。\n"
        '严格输出 JSON：{"evidence":[{"id":"e1","quote":"原句"}]}\n\n'
        f"标题：{snap.title}\n正文：\n{_windows(snap.body)}"
    )


def _quote_lines(evidence: list[dict], limit: int = 300) -> str:
    """Bound quote length in every prompt: one huge quote must not eat the budget."""
    return "\n".join(f"[{e['id']}] {e['quote'][:limit]}" for e in evidence)


def _topup_prompt(snap: ArticleSnapshot, dropped: list[dict]) -> str:
    missing = "\n".join(f"- {d['quote'][:60]}" for d in dropped[:8])
    return (
        "上一次给的这些句子在原文里找不到，请重新只从正文摘录原句，不要改写：\n"
        f"{missing}\n\n"
        f"最多 {MAX_EVIDENCE} 句，必须是正文连续原句且至少 {MIN_QUOTE_CHARS} 字。\n"
        '严格输出 JSON：{"evidence":[{"id":"e1","quote":"原句"}]}\n\n'
        f"标题：{snap.title}\n正文：\n{_windows(snap.body)}"
    )


def _score_prompt(snap: ArticleSnapshot, evidence: list[dict], rb: Rubric) -> str:
    ev = _quote_lines(evidence)
    return (
        "你是 moni 业务价值评分员。读者是银行/支付数字化从业者。\n"
        "对下面十维各打 0-10 的整数分。每维必须给出 evidence_ids，且只能引用下面列出的 id。\n"
        "正文没写到的维度给 0，不要为了凑分牵强关联。0/3/6/10 是锚点，中间分允许。\n"
        "理由用一句中文，≤30 字，只写正文事实。禁止这些词：值得关注、赋能、闭环、抓手、"
        "底层逻辑、深刻、凸显；禁止「不是X而是Y」句式。\n\n"
        f"{rb.prompt_block()}\n\n"
        f"可用引文：\n{ev}\n\n"
        '严格输出 JSON：{"dimensions":{"rule_delta":{"score":0,"evidence_ids":[],'
        '"reason":"…"}}}\n\n'
        f"标题：{snap.title}\n导语：{snap.body[:400]}"
    )


def _rescore_prompt(
    snap: ArticleSnapshot, evidence: list[dict], reasons: list[str], rb: Rubric
) -> str:
    fail = "\n".join(f"- {r}" for r in reasons)
    return (
        "上一轮打分没有通过检查，原因如下：\n"
        f"{fail}\n\n"
        "请只修正这些问题，重新给十维打分。规则与上一轮相同：整数 0-10、每维引用可用 id、"
        "正文没写到就给 0。\n\n"
        f"{rb.prompt_block()}\n\n"
        f"可用引文：\n"
        + _quote_lines(evidence)
        + '\n\n严格输出 JSON：{"dimensions":{"rule_delta":{"score":0,"evidence_ids":[],'
        '"reason":"…"}}}\n\n'
        f"标题：{snap.title}\n导语：{snap.body[:400]}"
    )


def _audit_prompt(dimensions: list[dict], rb: Rubric, evidence: list[dict]) -> str:
    rows = "\n".join(
        f"- {d['key']}（{d['label']}）：{d['score']} 分；理由：{d['reason']}"
        for d in dimensions
    )
    quotes = _quote_lines(evidence) if evidence else "（本篇没有可用引文）"
    return (
        "你是 moni 打分检查员。只判断下面十维分数是否与正文事实一致，不要重写分数、不要给总分。\n"
        "判不通过的标准：理由没有事实、分数与锚点明显不符、出现空话或禁用词。\n"
        "正文未涉及某维且给 0，是合法结果，不要因此判失败。\n"
        f"{rb.prompt_block()}\n\n"
        f"可用引文（正文原句）：\n{quotes}\n\n"
        f"待检查：\n{rows}\n\n"
        '严格输出 JSON：{"pass":true,"reasons":[]}'
    )


def _valid_evidence(quotes: list[dict], normalized_body: str) -> tuple[list[dict], list[dict]]:
    valid: list[dict] = []
    dropped: list[dict] = []
    seen: set[str] = set()
    for item in quotes:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "").strip()
        if not quote:
            continue
        norm = normalize(quote)
        if len(norm) < MIN_QUOTE_CHARS:
            dropped.append({"quote": quote, "reason": "too_short"})
            continue
        if norm not in normalized_body:
            dropped.append({"quote": quote, "reason": "not_substring"})
            continue
        if norm in seen:
            continue
        seen.add(norm)
        valid.append({"id": f"e{len(valid) + 1}", "quote": quote, "valid": True})
    return valid, dropped


def _coerce_dimensions(
    raw: dict, valid_ids: set[str], rb: Rubric
) -> tuple[list[dict], list[str]]:
    dims: list[dict] = []
    fail_reasons: list[str] = []
    source = raw.get("dimensions") if isinstance(raw.get("dimensions"), dict) else raw
    for key in rb.keys:
        dim = rb.by_key[key]
        item = source.get(key) if isinstance(source, dict) else None
        if not isinstance(item, dict):
            dims.append(
                {
                    "key": key,
                    "label": dim.label,
                    "weight": dim.weight,
                    "score": 0,
                    "capped": False,
                    "cap_reason": "missing",
                    "evidence_ids": [],
                    "reason": "",
                    "contribution": 0.0,
                }
            )
            fail_reasons.append(f"{dim.label} 缺少打分")
            continue
        raw_score = item.get("score")
        invalid = isinstance(raw_score, bool) or not isinstance(raw_score, int)
        score = 0 if invalid else max(0, min(10, raw_score))
        ids = [str(x) for x in (item.get("evidence_ids") or []) if str(x) in valid_ids]
        reason = str(item.get("reason") or "").strip()
        capped = False
        cap_reason = None
        if invalid:
            cap_reason = "non_integer"
            fail_reasons.append(f"{dim.label} 分数不是整数")
        elif score > 0 and not ids:
            score = min(score, NO_EVIDENCE_CAP)
            capped = True
            cap_reason = "no_valid_evidence"
        hits = banned_hits(reason)
        if hits:
            fail_reasons.append(f"{dim.label} 理由含禁用词：{'、'.join(hits)}")
        dims.append(
            {
                "key": key,
                "label": dim.label,
                "weight": dim.weight,
                "score": score,
                "capped": capped,
                "cap_reason": cap_reason,
                "evidence_ids": ids,
                "reason": reason,
                "contribution": round(score / 10 * dim.weight, 1),
                "_invalid": invalid,
            }
        )
    return dims, fail_reasons


def _total(dims: list[dict]) -> int:
    return max(0, min(100, round(sum(d["contribution"] for d in dims))))


def _safe_reason(dim: dict) -> str:
    reason = dim["reason"].strip()
    if not reason or banned_hits(reason):
        return f"{dim['label']} {dim['score']} 分"
    return reason


def _compose_note(dims: list[dict], total: int) -> str:
    if total == 0 and all(d["score"] == 0 for d in dims):
        return "已评 0 分：10 问均无对应内容"
    strong = [d for d in dims if d["score"] >= 7]
    if not strong:
        best = max(dims, key=lambda d: (d["score"], d["weight"]))
        return f"各维均未达 7 分；最高{best['label']} {best['score']} 分"
    strong.sort(key=lambda d: (-d["weight"], -d["score"]))
    picked = strong[:2]
    joined = "；".join(_safe_reason(d) for d in picked)
    return joined[:NOTE_MAX_CHARS]


def _dates_in(quotes: list[str]) -> list[str]:
    found: list[str] = []
    for quote in quotes:
        for year, month, day in _DATE_RE.findall(quote):
            try:
                iso = date(int(year), int(month), int(day)).isoformat()
            except ValueError:
                continue
            if iso not in found:
                found.append(iso)
    return found


def score_general_bv(
    snap: ArticleSnapshot,
    rubric: Rubric = RUBRIC_V1,
    llm: LLM | None = None,
    budget_s: float = 90.0,
    step_timeout: float = 90.0,
) -> BvResult:
    body = snap.body
    digest = content_hash(body)
    if len(body) < MIN_BODY_CHARS:
        return BvResult(
            status=STATUS_INSUFFICIENT,
            breakdown={
                "rubric": rubric.id,
                "status": STATUS_INSUFFICIENT,
                "total": None,
                "note": "正文过短，未打业务价值分",
            },
            content_hash=digest,
        )
    if llm is None:
        return BvResult(status=STATUS_SKIPPED, content_hash=digest)

    caller = _Caller(llm, budget_s, step_timeout)
    normalized = normalize(body)

    raw_evidence = caller.call(_evidence_prompt(snap))
    if raw_evidence is None:
        return BvResult(
            status=STATUS_SKIPPED if caller.calls == 0 else STATUS_FAILED,
            error="取证失败",
            content_hash=digest,
            calls_used=caller.calls,
        )
    quotes = raw_evidence.get("evidence") or []
    valid, dropped = _valid_evidence(quotes if isinstance(quotes, list) else [], normalized)

    if len(valid) < 3 and caller.can_call():
        topup = caller.call(_topup_prompt(snap, dropped))
        if topup:
            more, more_dropped = _valid_evidence(
                topup.get("evidence") or [], normalized
            )
            dropped.extend(more_dropped)
            seen = {normalize(e["quote"]) for e in valid}
            for item in more:
                if normalize(item["quote"]) in seen:
                    continue
                seen.add(normalize(item["quote"]))
                valid.append(item)
    valid = valid[:MAX_EVIDENCE]
    # Re-number after the merge: both passes start at e1, so a naive extend
    # would hand the scorer two different quotes under the same id.
    for i, item in enumerate(valid, 1):
        item["id"] = f"e{i}"
    valid_ids = {e["id"] for e in valid}

    raw_scores = caller.call(_score_prompt(snap, valid, rubric))
    if raw_scores is None:
        return BvResult(
            status=STATUS_FAILED,
            error="打分失败",
            content_hash=digest,
            calls_used=caller.calls,
        )
    dims, fail_reasons = _coerce_dimensions(raw_scores, valid_ids, rubric)

    if fail_reasons and caller.can_call():
        rescored = caller.call(_rescore_prompt(snap, valid, fail_reasons, rubric))
        if rescored:
            dims, fail_reasons = _coerce_dimensions(rescored, valid_ids, rubric)

    if not fail_reasons and caller.can_call():
        audit = caller.call(_audit_prompt(dims, rubric, valid))
        if audit is not None and not audit.get("pass", True):
            reasons = [str(r) for r in (audit.get("reasons") or []) if str(r).strip()]
            fail_reasons = reasons or ["检查未通过"]
            if caller.can_call():
                rescored = caller.call(
                    _rescore_prompt(snap, valid, fail_reasons, rubric)
                )
                if rescored:
                    dims, fail_reasons = _coerce_dimensions(
                        rescored, valid_ids, rubric
                    )

    total = _total(dims)
    status = STATUS_REVIEW if fail_reasons else STATUS_READY
    window_quotes = [
        e["quote"]
        for d in dims
        if d["key"] == "decision_window"
        for e in valid
        if e["id"] in d["evidence_ids"]
    ]
    breakdown = {
        "rubric": rubric.id,
        "status": status,
        "total": total,
        "total_source": "code",
        "note": _compose_note(dims, total),
        "note_source": "top_weights",
        "content_hash": digest,
        "scored_on": _today(),
        "evidence": valid,
        "dropped_evidence": dropped,
        "dimensions": [
            {k: v for k, v in d.items() if not k.startswith("_")} for d in dims
        ],
        "auditor": {
            "passed": not fail_reasons,
            "rounds": caller.calls,
            "fail_reasons": fail_reasons,
        },
        "decision_window_dates": _dates_in(window_quotes),
        "error": None,
    }
    return BvResult(
        status=status,
        total=total,
        note=breakdown["note"],
        breakdown=breakdown,
        content_hash=digest,
        calls_used=caller.calls,
    )
