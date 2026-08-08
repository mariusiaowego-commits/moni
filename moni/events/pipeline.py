"""moni.events.pipeline — 三层事务管道。

核心设计：
  阶段 1: normalize（主体别名统一 + 中文日期归一 + type 映射）
  阶段 2: insert（CHECK constraint 兜底）
  阶段 3: state-transition（extraction_status 更新）

三阶段在同一个 SQLite 事务内，任何失败全部回滚，只更新 status + error。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from ..db.connection import get_db
from ..llm.client import call_llm
from .normalize import normalize_event
from .repository import EventRepository

logger = logging.getLogger(__name__)

# Prompt 模板路径
_PROMPT_PATH = Path(__file__).parent.parent / "llm" / "prompts" / "event_extraction.txt"


def _render_prompt(article: dict[str, Any]) -> str:
    """渲染事件抽取 prompt。

    Args:
        article: 包含 id, title, source, published_at, raw_content 的 dict

    Returns:
        渲染后的 prompt 字符串
    """
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    content = (article.get("raw_content") or "")[:4000]
    return template.format(
        title=article.get("title", ""),
        source=article.get("source", ""),
        published_at=article.get("published_at") or "",
        content=content,
    )


def _parse_events_from_reply(raw_reply: str) -> list[dict[str, Any]]:
    """从 LLM 回复中解析事件列表（三策略 regex parse）。

    参考 summary_gen.py 的三策略模式，应对 ~20% 的 JSON 解析失败率。

    策略 1：```json ... ``` 代码块
    策略 2：找到 {"events": [...]} 或 [...]
    策略 3：逐条正则匹配 event 对象
    """
    if not raw_reply:
        return []

    # Strategy 1: ```json code block
    m = re.search(r"```json\s*\n(.*?)\n```", raw_reply, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            events = _extract_events_from_json(data)
            if events:
                return events
        except json.JSONDecodeError:
            pass

    # Strategy 2: find {...} with "events" key, or plain [...]
    # 先找 "events" 键
    m = re.search(r'"events"\s*:\s*(\[.*?\])', raw_reply, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    # 找整个 JSON 对象
    m = re.search(r"\{.*\}", raw_reply, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            events = _extract_events_from_json(data)
            if events:
                return events
        except json.JSONDecodeError:
            pass

    # Strategy 3: 逐条正则匹配 event 对象
    events = []
    # 匹配包含 subject 字段的对象
    for match_obj in re.finditer(
        r'\{[^}]*"subject"\s*:\s*"([^"]*)"[^}]*\}',
        raw_reply,
        re.DOTALL,
    ):
        obj_str = match_obj.group(0)
        try:
            obj = json.loads(obj_str)
            events.append(obj)
        except json.JSONDecodeError:
            # 退而求其次，手动提取字段
            ev = _regex_extract_event(obj_str)
            if ev:
                events.append(ev)

    return events


def _extract_events_from_json(data: Any) -> list[dict[str, Any]]:
    """从各种 JSON 结构中提取事件列表。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "events" in data and isinstance(data["events"], list):
            return data["events"]
        # 单个事件对象
        if "subject" in data:
            return [data]
    return []


def _regex_extract_event(obj_str: str) -> dict[str, Any] | None:
    """用正则从残缺 JSON 字符串中提取事件字段。"""
    result: dict[str, Any] = {}

    def _extract_field(name: str) -> str | None:
        m = re.search(
            rf'"{re.escape(name)}"\s*:\s*"((?:[^"\\]|\\.)*)"',
            obj_str,
        )
        return m.group(1) if m else None

    subject = _extract_field("subject")
    if not subject:
        return None
    result["subject"] = subject
    for field in ("subject_raw", "event_date", "event_date_precision",
                  "event_type", "summary", "evidence_snippet"):
        val = _extract_field(field)
        if val is not None:
            result[field] = val

    conf_m = re.search(r'"extraction_confidence"\s*:\s*(\d+)', obj_str)
    if conf_m:
        result["extraction_confidence"] = int(conf_m.group(1))

    return result


def extract_events_for_article(
    article: dict[str, Any],
    *,
    provider: str = "minimax-cn",
    model: str = "MiniMax-M3",
    llm_call_fn: Any | None = None,
) -> dict[str, Any]:
    """为单篇文章提取事件（三层事务管道）。

    Args:
        article: 文章 dict，至少包含 id, title, raw_content
        provider: LLM provider name
        model: LLM model id
        llm_call_fn: 可选的 LLM 调用函数（用于测试 mock）
            签名：fn(prompt: str) -> str

    Returns:
        dict with keys:
        - article_id
        - status: completed / failed / skipped / normalize_failed
        - event_count: 抽取到的事件数
        - error: 错误信息（失败时）
    """
    article_id = article["id"]
    conn = get_db()

    try:
        # 1. LLM 调用
        prompt = _render_prompt(article)
        if llm_call_fn is not None:
            raw_reply = llm_call_fn(prompt)
        else:
            raw_reply = call_llm(provider, model, prompt)

        # 2. 解析 JSON
        raw_events = _parse_events_from_reply(raw_reply)
        if not raw_events:
            # 没有抽取到事件 → 标记为 skipped
            conn.execute("BEGIN")
            try:
                # 先确保有占位行
                EventRepository.mark_processing(conn, article_id)
                EventRepository.set_extraction_status(
                    conn, article_id, "skipped",
                    error="no events extracted",
                    attempts_increment=1,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return {
                "article_id": article_id,
                "status": "skipped",
                "event_count": 0,
                "error": None,
            }

        # 3. 阶段 1：normalize
        normalized = []
        for raw_ev in raw_events:
            ev = normalize_event(raw_ev)
            if ev is not None:
                normalized.append(ev)

        if not normalized:
            # 全部被 normalize 过滤掉了（例如主体为空）
            conn.execute("BEGIN")
            try:
                EventRepository.mark_processing(conn, article_id)
                EventRepository.set_extraction_status(
                    conn, article_id, "normalize_failed",
                    error="all events filtered by normalize (empty subject)",
                    attempts_increment=1,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return {
                "article_id": article_id,
                "status": "normalize_failed",
                "event_count": 0,
                "error": "all events filtered by normalize",
            }

        # 4. 阶段 2+3：insert + state-transition（同事务）
        conn.execute("BEGIN")
        try:
            # 先确保 processing 占位行存在
            EventRepository.mark_processing(conn, article_id)

            # 插入真实事件
            inserted = EventRepository.insert_events(
                conn, article_id, normalized,
            )

            # 清除占位行
            EventRepository.clear_processing_placeholder(conn, article_id)

            # 更新 status 为 completed（所有真实事件行）
            # 注意：insert_events 已经设置为 completed，这里确保一下
            now = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat()
            conn.execute(
                """
                UPDATE events
                SET extraction_status = 'completed',
                    extraction_attempts = extraction_attempts + 0,
                    extracted_at = ?
                WHERE article_id = ? AND subject != '__processing__'
                """,
                (now, article_id),
            )

            conn.commit()
            return {
                "article_id": article_id,
                "status": "completed",
                "event_count": inserted,
                "error": None,
            }
        except Exception:
            conn.rollback()
            raise

    except Exception as exc:
        # 任何失败 → 更新 status + error
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.warning("Event extraction failed for article %s: %s", article_id, error_msg)
        try:
            conn.execute("BEGIN")
            EventRepository.mark_processing(conn, article_id)
            EventRepository.set_extraction_status(
                conn, article_id, "failed",
                error=error_msg,
                attempts_increment=1,
            )
            conn.commit()
        except Exception as inner_exc:
            conn.rollback()
            logger.warning(
                "Failed to write failure status for article %s: %s",
                article_id, inner_exc,
            )
        return {
            "article_id": article_id,
            "status": "failed",
            "event_count": 0,
            "error": error_msg,
        }


def extract_events_batch(
    *,
    batch_size: int = 20,
    provider: str = "minimax-cn",
    model: str = "MiniMax-M3",
    llm_call_fn: Any | None = None,
) -> dict[str, Any]:
    """批量提取事件（调度器调用入口）。

    从待处理队列中取 batch_size 篇文章，逐个处理。

    Args:
        batch_size: 每批处理的文章数
        provider: LLM provider
        model: LLM model
        llm_call_fn: 可选的 mock LLM 函数

    Returns:
        dict with keys: processed, succeeded, failed, skipped, events_extracted
    """
    articles = EventRepository.get_pending_articles(batch_size=batch_size)
    if not articles:
        return {
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "events_extracted": 0,
        }

    results = {
        "processed": len(articles),
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "events_extracted": 0,
    }

    for art in articles:
        result = extract_events_for_article(
            dict(art),
            provider=provider,
            model=model,
            llm_call_fn=llm_call_fn,
        )
        status = result["status"]
        if status == "completed":
            results["succeeded"] += 1
            results["events_extracted"] += result["event_count"]
        elif status == "failed":
            results["failed"] += 1
        else:
            results["skipped"] += 1

    return results
