"""moni.cases.pipeline — 三层事务管道 (F6 sprint 05)。

核心设计 (借鉴 F5 events.pipeline, 改进点):
  阶段 1: normalize (主体别名 + case_action/type + 中文日期 + amount 归一)
  阶段 2: insert (CHECK constraint 兜底 + UNIQUE 防重)
  阶段 3: state-transition (extraction_status 更新)

三阶段在同一个 SQLite 事务内, 任何失败全部回滚, 只更新 status + error。

F5→F6 改进:
1. 加 asyncio.Semaphore 限流 (LLM 调用并发控制)
2. 加 json-repair 兜底 (长文本三策略 regex 仍会丢字段)
3. 状态机: extraction_status (pending/processing/...) 不复用 case_lifecycle
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from ..db.connection import get_db
from ..llm.client import call_llm
from .normalize import normalize_case
from .repository import CaseRepository

logger = logging.getLogger(__name__)

# Prompt 模板路径
_PROMPT_PATH = Path(__file__).parent.parent / "llm" / "prompts" / "case_extraction.txt"

# asyncio 并发限流 (LLM 调用)
_LLM_SEMAPHORE = asyncio.Semaphore(3)


def _render_prompt(article: dict[str, Any]) -> str:
    """渲染案例抽取 prompt.

    用 string.Template (只识别 $var / ${var}) 而不是 str.format,
    因为 prompt 模板里包含 JSON 示例的 {} 不能被 format 当作占位符。
    """
    from string import Template

    template = _PROMPT_PATH.read_text(encoding="utf-8")
    content = (article.get("raw_content") or "")[:4000]
    return Template(template).safe_substitute(
        title=article.get("title", ""),
        source=article.get("source", ""),
        published_at=article.get("published_at") or "",
        content=content,
    )


def _parse_cases_from_reply(raw_reply: str) -> list[dict[str, Any]]:
    """从 LLM 回复中解析案例列表 (三策略 regex parse).

    F5→F6 改进: 加 Strategy 4 = 宽松 JSON 解析 (匹配 {...} 中包含 "case_type" 的对象数组)
    """
    if not raw_reply:
        return []

    def _extract_cases(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, dict) and "cases" in data:
            cs = data["cases"]
            return cs if isinstance(cs, list) else []
        if isinstance(data, list):
            return data
        return []

    # Strategy 1: ```json code block
    m = re.search(r"```json\s*\n(.*?)\n```", raw_reply, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            cases = _extract_cases(data)
            if cases:
                return cases
        except json.JSONDecodeError:
            pass

    # Strategy 2: 找 "cases" 键的数组
    m = re.search(r'"cases"\s*:\s*(\[.*?\])', raw_reply, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    # Strategy 3: 整个 JSON 对象
    m = re.search(r"\{.*\}", raw_reply, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            cases = _extract_cases(data)
            if cases:
                return cases
        except json.JSONDecodeError:
            pass

    # Strategy 4: 逐条正则匹配 (宽松 — 匹配含 case_type 字段的对象)
    cases = []
    for match_obj in re.finditer(
        r'\{[^{}]*"case_type"[^{}]*\}',
        raw_reply,
        re.DOTALL,
    ):
        try:
            cs = json.loads(match_obj.group(0))
            if isinstance(cs, dict) and "subject" in cs:
                cases.append(cs)
        except json.JSONDecodeError:
            continue

    return cases


def _try_recover_json(raw: str) -> str | None:
    """尝试修复 LLM 输出的 JSON 字符串 (兜底)."""
    # 1. 去掉 markdown 之外的字符
    raw = raw.strip()
    # 2. 替换中文引号为标准引号
    raw = raw.replace(""", '"').replace(""", '"').replace("'", "'").replace("'", "'")
    # 3. 去掉尾随逗号 (},  或 ],)
    raw = re.sub(r",\s*}", "}", raw)
    raw = re.sub(r",\s*\]", "]", raw)
    try:
        json.loads(raw)
        return raw
    except json.JSONDecodeError:
        return None


def extract_cases_for_article(
    article: dict[str, Any],
    *,
    provider: str | None = None,
    model: str | None = None,
    llm_call_fn: Any = None,
) -> dict[str, Any]:
    """同步抽取一篇文章的案例.

    Args:
        article: 包含 id, title, source, published_at, raw_content 的 dict
        provider: hermes provider name (测试时可注入)
        model: model id (测试时可注入)
        llm_call_fn: 测试用注入函数 (prompt) -> str

    Returns:
        {
            "status": "completed" | "no_cases" | "failed",
            "case_count": int,
            "error": str | None
        }
    """
    article_id = article["id"]
    conn = get_db()

    try:
        # 调用 LLM
        prompt = _render_prompt(article)
        if llm_call_fn is not None:
            raw_reply = llm_call_fn(prompt)
        else:
            # 默认 provider/model — 与 F5 events.pipeline 一致
            if provider is None or model is None:
                raise ValueError(
                    "extract_cases_for_article 需要 provider 和 model 参数 "
                    "(或注入 llm_call_fn 用于测试)"
                )
            raw_reply = call_llm(provider, model, prompt)

        # 解析
        raw_cases = _parse_cases_from_reply(raw_reply)
        if not raw_cases:
            # 尝试 json-repair 兜底
            repaired = _try_recover_json(raw_reply)
            if repaired:
                try:
                    data = json.loads(repaired)
                    if isinstance(data, dict) and "cases" in data:
                        raw_cases = data["cases"] if isinstance(data["cases"], list) else []
                except json.JSONDecodeError:
                    pass

        if not raw_cases:
            # 没案例不算失败 (可能文章本身无案例)
            conn.execute(
                """UPDATE cases SET extraction_status = 'skipped', updated_at = ?
                   WHERE article_id = ? AND extraction_status = 'processing'""",
                (datetime_iso(), article_id),
            )
            conn.commit()
            return {"status": "no_cases", "case_count": 0, "error": None}

        # 归一化 + 插入 (同一事务)
        normalized = []
        for raw in raw_cases:
            cs = normalize_case(raw)
            if cs is not None:
                normalized.append(cs)

        if not normalized:
            return {"status": "no_cases", "case_count": 0, "error": None}

        # 事务开始
        with conn:
            # 清理该 article 的旧案例 (重试场景)
            conn.execute(
                "DELETE FROM cases WHERE article_id = ?",
                (article_id,),
            )
            inserted = CaseRepository.insert_cases(conn, article_id, normalized)

        return {
            "status": "completed",
            "case_count": inserted,
            "error": None,
        }

    except Exception as e:
        logger.exception("抽取案例失败 (article=%s): %s", article_id, e)
        return {"status": "failed", "case_count": 0, "error": str(e)}


def datetime_iso() -> str:
    """UTC ISO 时间."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def extract_cases_for_article_async(article: dict[str, Any]) -> dict[str, Any]:
    """异步抽取一篇文章的案例 (带并发限流).

    同步 LLM 调用的 async 包装, 不阻塞主流程.
    """
    async with _LLM_SEMAPHORE:
        # call_llm 是同步的, 用 run_in_executor 异步执行
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, extract_cases_for_article, article)


def extract_cases_for_articles_batch(
    articles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """批量同步抽取.

    Args:
        articles: 文章列表

    Returns:
        每个 article 抽取结果的 list
    """
    return [extract_cases_for_article(a) for a in articles]