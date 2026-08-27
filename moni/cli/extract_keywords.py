"""moni.cli.extract_keywords — 存量案例关键词批量回填 (v1.0.36 sprint-18).

针对已有 cases 但无 case_keywords 的存量记录 (LLM 打标 pipeline 只对
新增 article 生效), 走轻量 LLM 调用 (只读 subject+counterparty+summary+
evidence_snippet, ~200 tokens/篇) 补打 keywords, 写 case_keywords (auto_llm).

用法:
    moni extract keywords [--source NAME] [--limit N] [--dry-run]
                          [--overwrite-auto]
"""

from __future__ import annotations

import json
import re
import signal
import time
from collections import Counter
from datetime import datetime, timezone

from moni.cases.normalize import normalize_keywords
from moni.db.connection import get_db
from moni.llm.client import resolve_active_model, call_llm


# 轻量 prompt: 只读结构化字段, 避免重新读 4000 字原文 (~200 tokens/篇)
_RETRACTION_PROMPT = """你是金融行业同业案例关键词抽取助手. 给定一条已有案例的结构化文本,
从中抽取 3-5 个**细粒度中文业务关键词** (如"智能投顾"/"跨境支付"/"反欺诈").

要求:
- 必须是中文, 长度 >= 2
- 禁泛词: 银行/金融/系统/创新/业务/AI (单独出现即不合格)
- 必须精准体现具体业务子场景 (跨境支付/智能风控/智能体/大模型应用/数字人民币/生物识别 等)
- 返回纯 JSON: {{"keywords": ["词1", "词2", "词3"]}}

主体: {subject}
对手方: {counterparty}
摘要: {summary}
证据: {evidence_snippet}
"""

_LLM_GAP_SEC = 0.3
_RATE_LIMIT_RETRY_SEC = 5.0


def _candidates(*, source: str | None, limit: int | None, include_with_keywords: bool = False) -> list[dict]:
    """选候选案例 (默认: 无 keywords 的非占位). JOIN articles 一次拿 source."""
    conn = get_db()
    sql = """
        SELECT c.case_id, c.subject, c.counterparty, c.summary, c.evidence_snippet,
               a.source AS source,
               (SELECT COUNT(*) FROM case_keywords k WHERE k.case_id = c.case_id) AS kw_n
        FROM cases c
        JOIN articles a ON a.id = c.article_id
        WHERE c.subject != '__processing__'
          AND c.case_lifecycle != 'archived'
    """
    params: list = []
    if source:
        sql += " AND a.source = ?"
        params.append(source)
    if not include_with_keywords:
        sql += " AND NOT EXISTS (SELECT 1 FROM case_keywords k WHERE k.case_id = c.case_id)"
    sql += " ORDER BY c.extracted_at DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _is_rate_limit(exc: BaseException) -> bool:
    name = type(exc).__name__
    if "RateLimit" in name or name == "RateLimitError":
        return True
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "ratelimit" in msg


def _parse_keywords_reply(raw: str) -> list[str]:
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    kws = data.get("keywords") or []
    return normalize_keywords(kws)


def _llm_extract_keywords(provider: str, model: str, case: dict) -> list[str]:
    """单条调用 → 返回 normalize_keywords 归一后的 list.

    每次调用前 0.3s 缓冲; 429 则再 sleep 5s 重试 1 次.
    """
    prompt = _RETRACTION_PROMPT.format(
        subject=case.get("subject") or "",
        counterparty=case.get("counterparty") or "",
        summary=(case.get("summary") or "")[:300],
        evidence_snippet=(case.get("evidence_snippet") or "")[:300],
    )
    last_exc: BaseException | None = None
    for attempt in range(2):
        time.sleep(_LLM_GAP_SEC)
        try:
            raw = call_llm(provider, model, prompt, timeout=120)
            return _parse_keywords_reply(raw)
        except Exception as exc:
            last_exc = exc
            if attempt == 0 and _is_rate_limit(exc):
                time.sleep(_RATE_LIMIT_RETRY_SEC)
                continue
            raise
    raise last_exc  # pragma: no cover


def _replace_auto_keywords(conn, case_id: str, kws: list[str], *, overwrite_auto: bool) -> int:
    """LLM 成功后写库: overwrite 时只删本 case 的 auto_llm, 再 INSERT.

    同连接事务: 失败 rollback, 旧 auto_llm 还在. manual_user 永不删.
    """
    now = datetime.now(timezone.utc).isoformat()
    if overwrite_auto:
        conn.execute(
            "DELETE FROM case_keywords WHERE case_id = ? AND source_type = 'auto_llm'",
            (case_id,),
        )
    n = 0
    for kw in kws:
        cur = conn.execute(
            """INSERT INTO case_keywords
                   (case_id, keyword, source_type, weight, created_at)
               VALUES (?, ?, 'auto_llm', 1, ?)
               ON CONFLICT(case_id, keyword) DO NOTHING""",
            (case_id, kw, now),
        )
        if cur.rowcount > 0:
            n += 1
    return n


def run_extract_keywords(args) -> int:
    """批量回填关键词 CLI 入口."""
    overwrite = bool(getattr(args, "overwrite_auto", False))
    dry_run = bool(getattr(args, "dry_run", False))

    rows = _candidates(
        source=getattr(args, "source", None),
        limit=getattr(args, "limit", None),
        include_with_keywords=overwrite,
    )

    if dry_run:
        by_source = Counter((r.get("source") or "?") for r in rows)
        print(f"[dry-run] 关键词回填候选: {len(rows)} 条案例")
        for s, c in by_source.most_common():
            print(f"  {s}: {c}")
        print(f"  预计 LLM 调用: {len(rows)} 次 (~200 tokens/次, ~1-2s/次)")
        print(f"  粗算时长: {len(rows) * 1.5 / 60:.0f} 分钟")
        return 0

    if not rows:
        print("[extract keywords] 无候选 (案例都已有 keywords)")
        return 0

    provider, model = resolve_active_model()
    stats = {"total": len(rows), "ok": 0, "failed": 0, "inserted": 0}
    t0 = time.time()
    interrupted = {"flag": False}

    def _sigint(_sig, _frame):
        interrupted["flag"] = True
        print("\n[SIGINT] 中断, 处理完当前条后停止...", flush=True)

    signal.signal(signal.SIGINT, _sigint)

    for i, row in enumerate(rows, 1):
        if interrupted["flag"]:
            break
        try:
            kws = _llm_extract_keywords(provider, model, row)
            conn = get_db()
            try:
                n = _replace_auto_keywords(
                    conn, row["case_id"], kws, overwrite_auto=overwrite,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            stats["inserted"] += n
            stats["ok"] += 1
        except Exception as exc:
            stats["failed"] += 1
            print(f"  [{i}] failed: {row['case_id']}: {exc}", flush=True)

        if i % 10 == 0 or i == len(rows) or interrupted["flag"]:
            el = time.time() - t0
            print(
                f"  进度 {i}/{len(rows)} | 完成={stats['ok']} 失败={stats['failed']} "
                f"已插入关键词={stats['inserted']} | {el:.0f}s",
                flush=True,
            )

    el = time.time() - t0
    print(
        f"[extract keywords] 完成: 处理 {stats['total']} 条, {el / 60:.1f} 分钟, "
        f"插入 {stats['inserted']} 个关键词"
    )
    return 0
