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
import hashlib
import json
import logging
import re
import uuid
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


def _json_value_at(s: str, idx: int) -> Any | None:
    """从 s[idx] 起用 JSONDecoder.raw_decode, 不怕 keywords 数组里的 ]."""
    try:
        data, _ = json.JSONDecoder().raw_decode(s, idx)
        return data
    except json.JSONDecodeError:
        return None


def _parse_cases_from_reply(raw_reply: str) -> list[dict[str, Any]]:
    """从 LLM 回复中解析案例列表 (三策略 regex parse).

    F5→F6 改进: 加 Strategy 4 = 宽松 JSON 解析 (匹配 {...} 中包含 "case_type" 的对象数组)
    sprint-18: json.loads / raw_decode 保留每条 cs["keywords"] 原样,
    后续 normalize_case 归一后交给 insert_cases.
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

    # Strategy 2: 找 "cases" 键的数组 (raw_decode, 不被 keywords 内 ] 截断)
    m = re.search(r'"cases"\s*:\s*\[', raw_reply)
    if m:
        data = _json_value_at(raw_reply, m.end() - 1)
        if isinstance(data, list) and data:
            return data

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


DEFAULT_PROMPT_VERSION = "v1"
DEFAULT_DAILY_INPUT_TOKEN_BUDGET = 300_000


def _compute_input_hash(article: dict[str, Any]) -> str:
    title = str(article.get("title") or "")
    content = str(article.get("raw_content") or "")[:4000]
    return hashlib.sha256(f"{title}\n{content}".encode("utf-8")).hexdigest()[:16]


def _is_rate_limit_error(err: Any) -> bool:
    if not err:
        return False
    msg = str(err).lower()
    return any(tok in msg for tok in ("429", "rate limit", "ratelimit", "quota"))


def check_daily_token_budget(conn: Any) -> tuple[bool, int, int]:
    """检查当天 case_extract / case_insight 的输入 token 是否超过预算闸.

    Returns:
        (allowed, used_tokens, budget_tokens)
    """
    budget = DEFAULT_DAILY_INPUT_TOKEN_BUDGET
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'case_extract_daily_input_token_budget'"
        ).fetchone()
        if row and row[0] is not None:
            budget = max(0, int(row[0]))
    except Exception:
        pass

    used = 0
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(input_tokens), 0)
            FROM llm_jobs
            WHERE task_type IN ('case_extract', 'case_insight')
              AND created_at >= date('now', 'localtime')
            """
        ).fetchone()
        if row and row[0] is not None:
            used = int(row[0])
    except Exception:
        pass

    return (used < budget, used, budget)


def _record_case_llm_job(
    conn: Any,
    *,
    article_id: str,
    provider: str | None,
    model: str | None,
    status: str,
    usage: dict[str, Any] | None = None,
    error: str | None = None,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> None:
    """将案例抽取的 LLM 用量写入 llm_jobs 记账 (best-effort)."""
    now = datetime_iso()
    u = usage or {}
    try:
        conn.execute(
            """
            INSERT INTO llm_jobs
                (id, task_type, article_id, provider, model, status, error,
                 attempts, created_at, started_at, finished_at,
                 input_tokens, output_tokens, cost_usd, prompt_name)
            VALUES (?, 'case_extract', ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                article_id,
                provider,
                model,
                status,
                error,
                now,
                now,
                now,
                int(u.get("input_tokens") or 0),
                int(u.get("output_tokens") or 0),
                float(u.get("cost_usd") or 0.0),
                prompt_version,
            ),
        )
    except Exception as exc:
        logger.debug("record case llm_jobs skipped: %s", exc)


def extract_cases_for_article(
    article: dict[str, Any],
    *,
    provider: str | None = None,
    model: str | None = None,
    llm_call_fn: Any = None,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> dict[str, Any]:
    """同步抽取一篇文章的案例.

    Args:
        article: 包含 id, title, source, published_at, raw_content 的 dict
        provider: hermes provider name (测试时可注入)
        model: model id (测试时可注入)
        llm_call_fn: 测试用注入函数 (prompt) -> str
        prompt_version: prompt 版本号 (写入 case_extract_runs)

    Returns:
        {
            "status": "completed" | "no_cases" | "failed",
            "case_count": int,
            "error": str | None
        }
    """
    article_id = article["id"]
    input_hash = _compute_input_hash(article)
    conn = get_db()
    usage_info: dict[str, Any] | None = None

    try:
        # 调用 LLM
        prompt = _render_prompt(article)
        if llm_call_fn is not None:
            raw_reply = llm_call_fn(prompt)
        else:
            # v1.0.35.19: provider/model 缺省时从 llm_settings 解析激活模型
            # (此前硬编码 minimax-cn 已 429 配额耗尽, 默认参数必失败)
            if provider is None or model is None:
                from ..llm.client import resolve_active_model
                provider, model = resolve_active_model()
            res = call_llm(provider, model, prompt, usage=True)
            if isinstance(res, tuple):
                raw_reply, usage_info = res[0], (res[1] if isinstance(res[1], dict) else None)
            else:
                raw_reply = res

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
            # v1.0.35.19: 落 skipped 占位行 (此前 UPDATE 0 行, "未提取过"
            # 无法判定 → 批量提取会重复烧 LLM)
            conn.execute("BEGIN")
            try:
                CaseRepository.mark_processing(conn, article_id)
                CaseRepository.set_extraction_status(
                    conn, article_id, "skipped",
                    error="no cases extracted",
                    attempts_increment=1,
                )
                CaseRepository.upsert_extract_run(
                    conn, article_id,
                    prompt_version=prompt_version,
                    input_hash=input_hash,
                    status="no_cases",
                    case_count=0,
                )
                if usage_info is not None:
                    _record_case_llm_job(
                        conn, article_id=article_id, provider=provider, model=model,
                        status="done", usage=usage_info, prompt_version=prompt_version,
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return {"status": "no_cases", "case_count": 0, "error": None}

        # 归一化 + 插入 (同一事务)
        # country 字段经 json.loads 留在 raw 上, normalize_case → geography 列表
        normalized = []
        for raw in raw_cases:
            cs = normalize_case(raw)
            if cs is not None:
                normalized.append(cs)

        if not normalized:
            # 全部被 normalize 过滤 (主体为空/无法判定) — 同样落 skipped 占位
            conn.execute("BEGIN")
            try:
                CaseRepository.mark_processing(conn, article_id)
                CaseRepository.set_extraction_status(
                    conn, article_id, "skipped",
                    error="all cases filtered by normalize",
                    attempts_increment=1,
                )
                CaseRepository.upsert_extract_run(
                    conn, article_id,
                    prompt_version=prompt_version,
                    input_hash=input_hash,
                    status="no_cases",
                    case_count=0,
                )
                if usage_info is not None:
                    _record_case_llm_job(
                        conn, article_id=article_id, provider=provider, model=model,
                        status="done", usage=usage_info, prompt_version=prompt_version,
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return {"status": "no_cases", "case_count": 0, "error": None}

        # 重试会 DELETE cases → FK CASCADE 清 keywords; 先把 manual_user 快照,
        # 插入后再按 (subject, type, action) 贴回, 避免 auto_llm 覆盖人工词.
        manual_rows = conn.execute(
            """
            SELECT c.subject, c.case_type, c.case_action, k.keyword, k.weight
            FROM case_keywords k
            JOIN cases c ON c.case_id = k.case_id
            WHERE c.article_id = ?
              AND k.source_type = 'manual_user'
              AND c.subject != '__processing__'
            """,
            (article_id,),
        ).fetchall()

        # 事务开始
        with conn:
            CaseRepository.mark_processing(conn, article_id)
            # 清理该 article 的旧案例 (重试场景) + 占位行
            conn.execute(
                "DELETE FROM cases WHERE article_id = ?",
                (article_id,),
            )
            inserted = CaseRepository.insert_cases(conn, article_id, normalized)
            CaseRepository.clear_processing_placeholder(conn, article_id)
            CaseRepository.restore_manual_keywords(conn, article_id, manual_rows)
            CaseRepository.upsert_extract_run(
                conn, article_id,
                prompt_version=prompt_version,
                input_hash=input_hash,
                status="done",
                case_count=inserted,
            )
            if usage_info is not None:
                _record_case_llm_job(
                    conn, article_id=article_id, provider=provider, model=model,
                    status="done", usage=usage_info, prompt_version=prompt_version,
                )

        return {
            "status": "completed",
            "case_count": inserted,
            "error": None,
        }

    except Exception as e:
        logger.exception("抽取案例失败 (article=%s): %s", article_id, e)
        try:
            with conn:
                CaseRepository.upsert_extract_run(
                    conn, article_id,
                    prompt_version=prompt_version,
                    input_hash=input_hash,
                    status="failed",
                    case_count=0,
                    error=str(e)[:500],
                )
                if llm_call_fn is None:
                    _record_case_llm_job(
                        conn, article_id=article_id, provider=provider, model=model,
                        status="failed", error=str(e)[:500], prompt_version=prompt_version,
                    )
        except Exception:
            pass
        return {"status": "failed", "case_count": 0, "error": str(e)}


def datetime_iso() -> str:
    """UTC ISO 时间."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def extract_cases_for_article_async(
    article: dict[str, Any],
    *,
    provider: str | None = None,
    model: str | None = None,
    llm_call_fn: Any = None,
) -> dict[str, Any]:
    """异步抽取一篇文章的案例 (带并发限流).

    同步 LLM 调用的 async 包装, 不阻塞主流程.
    v1.0.35.19: 透传 provider/model (此前不传 → 必 ValueError).
    """
    async with _LLM_SEMAPHORE:
        # call_llm 是同步的, 用 run_in_executor 异步执行
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: extract_cases_for_article(
                article, provider=provider, model=model, llm_call_fn=llm_call_fn,
            ),
        )


def extract_cases_batch(
    *,
    batch_size: int = 30,
    provider: str | None = None,
    model: str | None = None,
    llm_call_fn: Any = None,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    check_budget: bool = True,
) -> dict[str, Any]:
    """批量幂等抽取入口 (供 cron 调度器 / 链式触发调用).

    - 检查当天 token 预算闸 (case_extract_daily_input_token_budget)
    - 从 case_extract_runs 账本选待抽取文章
    - 遇 429 / quota 错误立即停止本批, 不把未处理文章计入完成
    """
    conn = get_db()
    if check_budget:
        allowed, used, budget = check_daily_token_budget(conn)
        if not allowed:
            logger.warning(
                "案例抽取日预算已耗尽 (used=%d >= budget=%d), 跳过本批",
                used, budget,
            )
            return {
                "processed": 0,
                "succeeded": 0,
                "failed": 0,
                "skipped": 0,
                "cases_extracted": 0,
                "budget_exhausted": True,
                "rate_limited": False,
            }

    articles = CaseRepository.get_pending_articles(
        batch_size=batch_size,
        prompt_version=prompt_version,
    )
    if not articles:
        return {
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "cases_extracted": 0,
            "budget_exhausted": False,
            "rate_limited": False,
        }

    if llm_call_fn is None and (provider is None or model is None):
        from ..llm.client import resolve_active_model
        provider, model = resolve_active_model()

    results: dict[str, Any] = {
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "cases_extracted": 0,
        "budget_exhausted": False,
        "rate_limited": False,
    }

    for art in articles:
        res = extract_cases_for_article(
            dict(art),
            provider=provider,
            model=model,
            llm_call_fn=llm_call_fn,
            prompt_version=prompt_version,
        )
        results["processed"] += 1
        status = res.get("status")
        if status == "completed":
            results["succeeded"] += 1
            results["cases_extracted"] += int(res.get("case_count") or 0)
        elif status == "no_cases":
            results["skipped"] += 1
        else:
            results["failed"] += 1
            if _is_rate_limit_error(res.get("error")):
                results["rate_limited"] = True
                logger.warning("案例抽取命中 429/配额限制, 立即中止本批")
                break

    return results


def extract_cases_for_articles_batch(
    articles: list[dict[str, Any]],
    *,
    provider: str | None = None,
    model: str | None = None,
    llm_call_fn: Any = None,
) -> list[dict[str, Any]]:
    """批量同步抽取.

    v1.0.35.19: 透传 provider/model (此前不传 → 每篇 ValueError).
    缺省时统一走 resolve_active_model (llm_settings → ark).

    Args:
        articles: 文章列表
        provider: LLM provider (缺省自动解析激活模型)
        model: LLM model id
        llm_call_fn: 测试用注入函数 (prompt) -> str

    Returns:
        每个 article 抽取结果的 list
    """
    if provider is None or model is None:
        from ..llm.client import resolve_active_model
        provider, model = resolve_active_model()
    return [
        extract_cases_for_article(
            a, provider=provider, model=model, llm_call_fn=llm_call_fn,
        )
        for a in articles
    ]