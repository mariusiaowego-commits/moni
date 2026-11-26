"""案例深度解读（case_insights）—— 一次 LLM 调用产出 做法 / 风险 / 可复制点.

只喂结构化字段（不重读原文），证据不足时允许模型返回 ``insufficient``，
避免为了凑字数编造。写入 ``case_insights``，并把 ``cases.insight_id`` 指到最新一条。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from moni.llm.client import call_llm

PROMPT_VERSION = "v1"

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")

PROMPT = """你是金融科技同业案例的拆解助手。只依据下面这条案例的文本作答，不得补充外部事实，不得编造文本里没有的数字；文本不足以支撑某项时宁可不写。

输出纯 JSON（不要 markdown 代码块）：
{{
  "what": "一句话：这家机构做了什么（<=40 字）",
  "how": ["具体做法 1", "具体做法 2"],
  "risks": ["落地风险 1", "落地风险 2"],
  "reusable": ["同行可复制的点 1"],
  "fit": "适用条件（<=40 字）"
}}
若文本不足以判断做法（只有标题/一句话），返回 {{"insufficient": true, "reason": "..."}}。

主体: {subject}
对手方: {counterparty}
行业/类型: {industry} / {category}
业务场景: {scenario}
金额: {amount}
摘要: {summary}
原文证据: {evidence}
已有指标: {metrics}
原文节选: {article}
"""


def _clean(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _text(value: Any, limit: int) -> str:
    """只接受字符串，避免 dict/list 被 str() 成脏文本存库."""
    return _clean(value, limit) if isinstance(value, str) else ""


def build_prompt(case: dict[str, Any], *, max_article_chars: int = 1200) -> str:
    """结构化字段 → prompt；截断防长文超预算。"""
    metrics = case.get("metrics") or []
    metric_text = "; ".join(
        f"{m.get('metric_type')} {m.get('value_num')}{m.get('unit') or ''}".strip()
        for m in metrics[:4]
    )
    return PROMPT.format(
        subject=_clean(case.get("subject"), 60),
        counterparty=_clean(case.get("counterparty"), 60),
        industry=_clean(case.get("industry_l1"), 20) or "未归类",
        category=_clean(case.get("category_name"), 20) or "未分类",
        scenario=_clean(case.get("scenario_name"), 20) or "其他业务场景",
        amount=_clean(case.get("amount"), 40) or "未披露",
        summary=_clean(case.get("summary"), 600),
        evidence=_clean(case.get("evidence_snippet"), 400),
        metrics=metric_text or "无",
        article=_clean(case.get("article_text"), max_article_chars) or "无",
    )


def input_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def parse_insight(raw: str | dict[str, Any]) -> dict[str, Any]:
    """解析模型回复；非法/空 → {}，保留 insufficient 标记。"""
    if isinstance(raw, dict):
        body = raw
    elif isinstance(raw, str):
        text = _FENCE_RE.sub("", (raw or "").strip())
        # Hermes CLI 会在 JSON 后追加进度行（如 "tq-s-select · 2% · ~"），
        # 只取第一个完整 JSON 对象，忽略尾部噪声。
        if text.lstrip().startswith("["):
            return {}
        start = text.find("{")
        if start < 0:
            return {}
        try:
            body, _ = json.JSONDecoder().raw_decode(text[start:])
        except (json.JSONDecodeError, ValueError):
            return {}
    else:
        return {}
    if not isinstance(body, dict):
        return {}
    flag = body.get("insufficient")
    if flag is True or (isinstance(flag, str) and flag.strip().lower() in {"true", "1", "yes"}):
        return {"insufficient": True, "reason": _text(body.get("reason"), 120)}
    what = _text(body.get("what"), 120)
    if not what:
        return {}
    cleaned: dict[str, Any] = {"what": what}
    for key in ("how", "risks", "reusable"):
        values = body.get(key)
        if isinstance(values, list):
            cleaned[key] = [v for v in (_text(x, 120) for x in values[:5]) if v]
        else:
            cleaned[key] = []
    cleaned["fit"] = _text(body.get("fit"), 120)
    return cleaned


def generate_insight(
    case: dict[str, Any],
    provider: str,
    model: str,
    *,
    timeout: int = 300,
    max_article_chars: int = 1200,
    with_usage: bool = False,
) -> dict[str, Any] | tuple[dict[str, Any], dict[str, Any]]:
    """单条生成；返回解析后的 body（可能带 insufficient）。"""
    prompt = build_prompt(case, max_article_chars=max_article_chars)
    if not with_usage:
        return parse_insight(call_llm(provider, model, prompt, timeout=timeout))
    raw, usage = call_llm(provider, model, prompt, timeout=timeout, usage=True)
    return parse_insight(raw), usage


def save_insight(
    conn: sqlite3.Connection,
    case_id: str,
    body: dict[str, Any],
    *,
    provider: str | None,
    model: str | None,
    prompt_hash: str,
    prompt_version: str = PROMPT_VERSION,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> int:
    """写入并回填 cases.insight_id；同 (case, version, hash) 幂等覆盖."""
    if not body:
        raise ValueError("refusing to save empty insight body")
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(body, ensure_ascii=False)
    with conn:
        conn.execute(
            """INSERT INTO case_insights
                   (case_id, prompt_version, input_hash, provider, model,
                    status, body_json, created_at, input_tokens, output_tokens)
               VALUES (?, ?, ?, ?, ?, 'done', ?, ?, ?, ?)
               ON CONFLICT(case_id, prompt_version, input_hash)
               DO UPDATE SET body_json = excluded.body_json,
                             provider = excluded.provider,
                             model = excluded.model,
                             status = 'done',
                             error = NULL,
                             input_tokens = excluded.input_tokens,
                             output_tokens = excluded.output_tokens""",
            (case_id, prompt_version, prompt_hash, provider, model, payload, now,
             input_tokens, output_tokens),
        )
        conn.execute(
            """UPDATE case_insights SET status = 'stale'
                WHERE case_id = ? AND NOT (prompt_version = ? AND input_hash = ?)
                  AND status = 'done'""",
            (case_id, prompt_version, prompt_hash),
        )
        row = conn.execute(
            "SELECT id FROM case_insights WHERE case_id = ? AND prompt_version = ? AND input_hash = ?",
            (case_id, prompt_version, prompt_hash),
        ).fetchone()
        insight_id = int(row[0]) if row else 0
        if insight_id:
            cur = conn.execute(
                "UPDATE cases SET insight_id = ?, updated_at = ? WHERE case_id = ?",
                (insight_id, now, case_id),
            )
            if cur.rowcount == 0:
                raise ValueError(f"case not found: {case_id}")
    return insight_id


def record_failure(
    conn: sqlite3.Connection,
    case_id: str,
    *,
    provider: str | None,
    model: str | None,
    prompt_hash: str,
    error: str,
    prompt_version: str = PROMPT_VERSION,
) -> None:
    """把失败落成 status='failed'，便于事后回溯；下次非 refresh 运行会重试."""
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            """INSERT INTO case_insights
                   (case_id, prompt_version, input_hash, provider, model,
                    status, error, created_at)
               VALUES (?, ?, ?, ?, ?, 'failed', ?, ?)
               ON CONFLICT(case_id, prompt_version, input_hash)
               DO UPDATE SET status = 'failed',
                             error = excluded.error,
                             provider = excluded.provider,
                             model = excluded.model
               WHERE case_insights.status <> 'done'""",
            (case_id, prompt_version, prompt_hash, provider, model, error[:500], now),
        )
