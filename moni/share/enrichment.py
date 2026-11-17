"""moni.share.enrichment — brief-mode article enrichment with DB writeback.

v1.0.10 one-click brief pipeline (per article, skip if already present):
1. ai_summary          (reuses summary_gen.generate_and_writeback)
2. fact_check_*        (structured writeback; sets has_fact_check=1)
3. business_value_*    (score 0-100 + note)
4. quality_score       (only if bb_score missing and flag on)

v1.0.24 brief web mode (non-blocking):
5. missing has_web_fact_check → enqueue fact_check_plus (async, no wait)
6. stamp articles.evidence_rendered_at after brief enrich
7. rebuild / HTML templates embed 事实核查证据 section + rollup

Also rebuilds article content blocks for the format prompt (re-read after enrich).
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moni.share.summary_gen import _invoke_hermes, generate_and_writeback

log = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Wall-clock budget for all enrichment hermes calls on one task (seconds).
# Body layout adds hermes calls; default budget covers ~10 articles offline HTML.
ENRICH_BUDGET_SEC = int(os.environ.get("MONI_ENRICH_BUDGET_SEC", "900"))
STEP_TIMEOUT_SEC = int(os.environ.get("MONI_ENRICH_STEP_TIMEOUT", "90"))
# Per-article share of the run budget (design §4.6): 120s cap, below 60s skip.
BV_ARTICLE_BUDGET_SEC = 120.0
MIN_BV_ARTICLE_BUDGET = 60.0
THIN_CONTENT_CHARS = 200

FACT_CHECK_PROMPT = """你是 moni 事实核查助手。只基于文内声明，不编造。

要求（短）：
- 最多 2 条 claim；纯观点 → conclusion="无需核查"，note 空
- 标签只用：【已核实】|【待核实】|【存疑】（禁英文）
- conclusion ≤20 字；每条 claim 半句，直说
- 禁「不是X而是Y」、禁赋能/凸显了/值得注意

严格 JSON：
```json
{{"id": "{article_id}", "conclusion": "…", "method": "llm_inference", "note": "claim1 【已核实】: …; claim2 【待核实】: …"}}
```

标题: {title}
ID: {article_id}
正文:
{content}
"""

BUSINESS_VALUE_PROMPT = """你是 moni 同业情报助手。读者：银行/支付数字化从业者。

只评「对己方业务」：监管、对标、可借鉴、合规、优先级。

硬要求：
- score: 0-100，同批拉开档次，禁止全挤 70-85
- note: **恰好 1 句中文**，≤50 字，点名「对我方意味什么」
- 正文过短：score≤40，note 仅写「素材不足」
- 禁止：值得关注、赋能、闭环、不是X而是Y、凸显了、深刻

严格 JSON：
```json
{{"id": "{article_id}", "score": 0, "note": "一句…"}}
```

标题: {title}
ID: {article_id}
正文:
{content}
"""

QUALITY_PROMPT = """你是 moni 内容质量评估助手。给文章打 0-100 质量分（信息密度、可操作性、来源可靠、是否软文）。

严格 JSON：
```json
{{"id": "{article_id}", "score": 0}}
```

标题: {title}
正文:
{content}
"""

# Long summary → brief: max 2 sentences, style-guarded rewrite only when needed.
BRIEF_SUMMARY_MAX_SENTS = 2
BRIEF_SUMMARY_MAX_CHARS = 100

COMPACT_SUMMARY_PROMPT = """你是 moni 摘要压缩助手。下面是一篇已有摘要（可能偏长）。请压成简报用短摘要。

硬要求：
- 最多 2 句中文，合计 ≤100 字
- 保留关键主体、动作、数字；删空评与重复
- 禁止：值得关注、赋能、闭环、不是X而是Y、凸显了、深刻洞察、灵魂叩问
- 写得像 2022 年前人类短讯，不要 AI 腔
- 不要发明原文没有的事实

严格 JSON：
```json
{{"id": "{article_id}", "summary": "…"}}
```

文章标题: {title}
ID: {article_id}
原摘要:
{summary}
"""

BODY_LAYOUT_PROMPT = """你是 moni 离线阅读排版助手。任务：把抓取到的正文整理成**结构清晰的 Markdown**，方便人阅读。

硬规则（违反即失败）：
1. **只排版，不改写事实**：专有名词、数字、引语、机构名必须与原文一致；禁止扩写、禁止加评论、禁止「总结陈词」。
2. **删抓取噪音**：导航（日报/专栏/热点）、分享按钮文案、「在看」「推荐阅读」、纯广告链接行、重复的来源页脚。
3. **结构**：
   - 用 `##` / `###` 标出明显小节（若原文有「1. 2. 3.」或主题转折）
   - 段落之间空一行；列表用 `- ` 或 `1. `
   - 金句/引语用 `>` 引用
4. **输出纯 Markdown 正文**，不要用 ``` 代码围栏包住全文，不要 YAML frontmatter。
5. 中文输出；若原文极短（不足 80 字），原样返回并补自然分段即可。

标题: {title}
ID: {article_id}

—— 原始正文 ——
{content}
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── v1.0.24 brief web mode: evidence enqueue + render ─────────────────────


def _article_has_web_fact_check(conn: sqlite3.Connection, article_id: str) -> bool:
    """True if article already has web-search fact check (column / method / evidence)."""
    try:
        row = conn.execute(
            "SELECT fact_check_method, has_web_fact_check FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = conn.execute(
            "SELECT fact_check_method FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
    if row is None:
        return False
    try:
        if int(row["has_web_fact_check"] or 0) == 1:
            return True
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    if (row["fact_check_method"] or "") == "web_search":
        return True
    try:
        ev = conn.execute(
            "SELECT 1 FROM fact_check_evidence WHERE article_id = ? LIMIT 1",
            (article_id,),
        ).fetchone()
        return ev is not None
    except sqlite3.OperationalError:
        return False


def _has_active_fact_check_job(conn: sqlite3.Connection, article_id: str) -> bool:
    try:
        row = conn.execute(
            """
            SELECT 1 FROM llm_jobs
            WHERE article_id = ?
              AND task_type IN ('fact_check', 'fact_check_plus')
              AND status IN ('pending', 'running')
            LIMIT 1
            """,
            (article_id,),
        ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False


def _resolve_fact_check_provider_model(
    conn: sqlite3.Connection,
    *,
    extra_args: list[str] | None = None,
    manifest_path: str | None = None,
) -> tuple[str, str]:
    """Resolve (provider, model) for fact_check_plus enqueue.

    Priority: manifest payload → extra_args → llm_settings → safe default.
    """
    provider: str | None = None
    model: str | None = None
    if manifest_path:
        try:
            with open(manifest_path, encoding="utf-8") as f:
                p = json.load(f).get("payload") or {}
            if p.get("provider"):
                provider = str(p["provider"])
            if p.get("model"):
                model = str(p["model"])
        except Exception:
            pass
    args = extra_args or []
    if not provider and "--provider" in args:
        i = args.index("--provider")
        if i + 1 < len(args):
            provider = args[i + 1]
    if not model and "--model" in args:
        i = args.index("--model")
        if i + 1 < len(args):
            model = args[i + 1]
    if not provider or not model:
        try:
            active = conn.execute(
                "SELECT value FROM llm_settings WHERE key = 'active_fact_check_model_id'"
            ).fetchone()
            if active and active["value"]:
                mrow = conn.execute(
                    """
                    SELECT m.model_id, p.hermes_provider
                    FROM llm_models m
                    JOIN llm_providers p ON p.id = m.provider_id
                    WHERE m.id = ?
                    """,
                    (active["value"],),
                ).fetchone()
                if mrow:
                    provider = provider or mrow["hermes_provider"]
                    model = model or mrow["model_id"]
        except sqlite3.OperationalError:
            pass
    return provider or "minimax-cn", model or "MiniMax-M3"


def _enqueue_missing_web_fact_checks(
    conn: sqlite3.Connection,
    article_ids: list[str],
    *,
    db_path: str,
    provider: str,
    model: str,
    wait: bool = True,
    timeout: float = 120.0,
    max_concurrent: int = 3,
) -> int:
    """Enqueue fact_check_plus for articles lacking web check. Non-blocking by
    default unless ``wait=True`` (sprint-11 v1.0.30).

    Inserts ``llm_jobs`` rows via the enrichment connection (same db_path).
    Does **not** start the llm worker thread here — avoids races with test
    teardown / subprocess lifecycle; the web process worker drains pending
    jobs.

    With ``wait=True``: after enqueue, poll ``llm_jobs.status`` every 2s
    (bounded by ``timeout``, at most ``max_concurrent`` jobs concurrently)
    until each job is done/failed — so the brief can render fresh evidence.
    On timeout the caller degrades per-article (does not block brief).

    Returns number of jobs enqueued.
    """
    if not article_ids:
        return 0
    import uuid

    n = 0
    now = _now()
    job_ids: list[str] = []
    for aid in article_ids:
        if _article_has_web_fact_check(conn, aid):
            continue
        if _has_active_fact_check_job(conn, aid):
            continue
        try:
            job_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO llm_jobs
                    (id, task_type, article_id, provider, model,
                     status, attempts, created_at)
                VALUES (?, 'fact_check_plus', ?, ?, ?, 'pending', 0, ?)
                """,
                (job_id, aid, provider, model, now),
            )
            n += 1
            job_ids.append(job_id)
        except Exception as exc:
            log.warning(
                "enrichment: fact_check_plus enqueue failed for %s: %s", aid, exc
            )
    if n:
        try:
            conn.commit()
        except Exception as exc:
            log.warning("enrichment: commit fact_check_plus jobs failed: %s", exc)
        # Best-effort: nudge worker if moni web process is live. Swallow errors
        # so brief generation never blocks (and tests don't start a thread).
        try:
            from moni.db.connection import set_db_path
            from moni.llm.jobs import ensure_worker_started

            set_db_path(Path(db_path))
            # Only start when env opts in — default off for test isolation.
            if os.environ.get("MONI_ENRICH_START_WORKER", "").strip() in (
                "1",
                "true",
                "yes",
            ):
                ensure_worker_started()
        except Exception:
            pass
        if wait and job_ids:
            result = _wait_for_jobs(conn, job_ids, timeout=timeout, max_concurrent=max_concurrent)
            _apply_wait_results(conn, job_ids, result)
    return n


def _apply_wait_results(
    conn: sqlite3.Connection,
    job_ids: list[str],
    result: dict[str, str],
) -> None:
    """Write per-article degrade notes after sync wait (sprint-11 v1.0.30).

    Contract (tests/test_brief_web_mode.py): failed → article.fact_check_note
    contains "核查失败"; timeout → "核查超时".  Never raises — brief
    generation must not block on these best-effort writes.
    """
    try:
        placeholders = ",".join("?" for _ in job_ids)
        rows = conn.execute(
            f"SELECT id, article_id FROM llm_jobs WHERE id IN ({placeholders})",
            job_ids,
        ).fetchall()
        for row in rows:
            status = result.get(row["id"])
            if status == "timeout":
                mark = "核查超时, 未含证据, 请人工复核"
            elif status == "failed":
                mark = "核查失败, 证据解析失败, 请人工复核"
            else:
                continue
            try:
                conn.execute(
                    "UPDATE articles SET fact_check_note = ? WHERE id = ?",
                    (mark, row["article_id"]),
                )
            except Exception as exc:
                log.warning("enrichment: fact_check_note degrade write failed: %s", exc)
        conn.commit()
    except Exception as exc:
        log.warning("enrichment: _apply_wait_results failed: %s", exc)


def _wait_for_jobs(
    conn: sqlite3.Connection,
    job_ids: list[str],
    *,
    timeout: float = 120.0,
    max_concurrent: int = 3,
    poll_interval: float = 2.0,
) -> dict[str, str]:
    """Poll llm_jobs.status until done/failed or timeout (sprint-11 v1.0.30).

    Watches at most ``max_concurrent`` jobs concurrently; returns a mapping
    {job_id: "done" | "failed" | "timeout"} for the caller to degrade per
    article. Never raises — brief generation must not block on LLM.
    """
    import time as _time

    remaining = dict.fromkeys(job_ids, "pending")
    done_ids: set[str] = set()
    deadline = _time.monotonic() + timeout
    while remaining and _time.monotonic() < deadline:
        _time.sleep(poll_interval)
        try:
            placeholders = ",".join("?" for _ in remaining)
            rows = conn.execute(
                f"SELECT id, status FROM llm_jobs WHERE id IN ({placeholders})",
                list(remaining.keys()),
            ).fetchall()
        except Exception as exc:
            log.warning("enrichment: _wait_for_jobs query failed: %s", exc)
            break
        for row in rows:
            status = row["status"]
            if status in ("done", "failed", "cancelled"):
                remaining.pop(row["id"], None)
                if status == "done":
                    done_ids.add(row["id"])
    result: dict[str, str] = {}
    for jid in job_ids:
        if jid in remaining:
            result[jid] = "timeout"
        elif jid in done_ids:
            result[jid] = "done"
        else:
            result[jid] = "failed"
    return result


def _write_evidence_rendered_at(
    conn: sqlite3.Connection, article_ids: list[str]
) -> None:
    """Stamp evidence_rendered_at for articles used in this brief (v1.0.24)."""
    if not article_ids:
        return
    now = _now()
    for aid in article_ids:
        try:
            conn.execute(
                "UPDATE articles SET evidence_rendered_at = ? WHERE id = ?",
                (now, aid),
            )
        except sqlite3.OperationalError as exc:
            log.warning("enrichment: evidence_rendered_at write failed (mig 026?): %s", exc)
            return
    conn.commit()


def load_evidence_context(
    conn: sqlite3.Connection, article_ids: list[str]
) -> dict[str, Any]:
    """Build evidence rollup + per-article evidence for brief templates (v1.0.24).

    Failure degradation (Q4): article without usable evidence rows is marked
    unverified; brief still renders.
    """
    articles_out: list[dict[str, Any]] = []
    checked = 0
    failed = 0
    if not article_ids:
        return {
            "total": 0,
            "checked": 0,
            "failed": 0,
            "method": "web_search (hermes -t web 实时检索)",
            "snapshot_at": _now(),
            "articles": [],
        }

    ph = ",".join("?" * len(article_ids))
    order = " ".join(f"WHEN ? THEN {i}" for i in range(len(article_ids)))
    try:
        rows = list(
            conn.execute(
                f"""
                SELECT id, title, fact_check_conclusion, fact_check_method,
                       has_web_fact_check
                FROM articles WHERE id IN ({ph})
                ORDER BY CASE id {order} END
                """,
                (*article_ids, *article_ids),
            ).fetchall()
        )
    except sqlite3.OperationalError:
        rows = list(
            conn.execute(
                f"""
                SELECT id, title, fact_check_conclusion, fact_check_method,
                       0 as has_web_fact_check
                FROM articles WHERE id IN ({ph})
                ORDER BY CASE id {order} END
                """,
                (*article_ids, *article_ids),
            ).fetchall()
        )

    for row in rows:
        aid = row["id"]
        title = (row["title"] or "").strip() or aid
        conclusion = (row["fact_check_conclusion"] or "").strip() or "【待核实】"
        evidence: list[dict[str, Any]] = []
        try:
            ev_rows = conn.execute(
                """
                SELECT url, domain, snippet, evidence_seq, conclusion
                FROM fact_check_evidence
                WHERE article_id = ?
                ORDER BY evidence_seq ASC
                """,
                (aid,),
            ).fetchall()
            for e in ev_rows:
                evidence.append(
                    {
                        "url": (e["url"] or "").strip(),
                        "domain": (e["domain"] or "").strip() or "source",
                        "snippet": (e["snippet"] or "").strip(),
                        "seq": int(e["evidence_seq"] or 0),
                        "conclusion": (e["conclusion"] or "").strip(),
                    }
                )
        except sqlite3.OperationalError:
            evidence = []

        usable = [e for e in evidence if e.get("url") and e.get("snippet")]
        if usable:
            checked += 1
            articles_out.append(
                {
                    "id": aid,
                    "title": title,
                    "conclusion": conclusion,
                    "ok": True,
                    "evidence": usable,
                    "fail_reason": "",
                }
            )
        else:
            failed += 1
            # Q4: parse/empty failure → 未联网核查, brief continues
            reason = "证据解析失败，请人工复核" if evidence else "证据解析失败，请人工复核"
            articles_out.append(
                {
                    "id": aid,
                    "title": title,
                    "conclusion": conclusion,
                    "ok": False,
                    "evidence": [],
                    "fail_reason": reason,
                }
            )

    return {
        "total": len(articles_out),
        "checked": checked,
        "failed": failed,
        "method": "web_search (hermes -t web 实时检索)",
        "snapshot_at": _now(),
        "articles": articles_out,
    }


def build_evidence_section_md(ctx: dict[str, Any]) -> str:
    """Render 事实核查证据 MD section (mirrors templates/brief_md.j2)."""
    total = int(ctx.get("total") or 0)
    checked = int(ctx.get("checked") or 0)
    failed = int(ctx.get("failed") or 0)
    method = str(ctx.get("method") or "web_search")
    snapshot = str(ctx.get("snapshot_at") or _now())
    fail_bit = f" ({failed} 篇失败降级)" if failed else ""
    lines = [
        "## 事实核查证据 (本简报)",
        "",
        f"- ✅ 已联网核查: {checked}/{total} 篇{fail_bit}",
        f"- 方法: {method}",
        f"- 时效: {snapshot} 检索快照",
        "",
    ]
    for i, a in enumerate(ctx.get("articles") or [], 1):
        title = a.get("title") or a.get("id") or f"文章{i}"
        lines.append(f"### 文章 {i}: {title}")
        lines.append("")
        if a.get("ok"):
            lines.append(f"- 结论: {a.get('conclusion') or '【待核实】'}")
            lines.append("- 来源:")
            for j, e in enumerate(a.get("evidence") or [], 1):
                lines.append(f"  - [{j}] {e.get('domain') or 'source'}: {e.get('url') or ''}")
            ev0 = (a.get("evidence") or [{}])[0]
            snip = (ev0.get("snippet") or "").strip()
            if snip:
                lines.append(f"- 引用片段: {snip}")
        else:
            reason = a.get("fail_reason") or "证据解析失败，请人工复核"
            lines.append(f"- （未联网核查，{reason}）")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_evidence_section_html(ctx: dict[str, Any]) -> str:
    """Render 事实核查证据 HTML section (mirrors templates/brief_html.j2)."""
    def esc(s: Any) -> str:
        return html.escape("" if s is None else str(s), quote=True)

    total = int(ctx.get("total") or 0)
    checked = int(ctx.get("checked") or 0)
    failed = int(ctx.get("failed") or 0)
    method = esc(ctx.get("method") or "web_search")
    snapshot = esc(ctx.get("snapshot_at") or _now())
    fail_bit = f" ({failed} 篇失败降级)" if failed else ""
    items: list[str] = []
    for a in ctx.get("articles") or []:
        title = esc(a.get("title") or a.get("id") or "")
        if a.get("ok"):
            lis = []
            for j, e in enumerate(a.get("evidence") or [], 1):
                url = esc(e.get("url") or "#")
                domain = esc(e.get("domain") or "source")
                snip = esc(e.get("snippet") or "")
                lis.append(
                    f'<li><a href="{url}" target="_blank" rel="noopener">'
                    f"[{j}] {domain}</a>"
                    f'<p class="snippet">{snip}</p></li>'
                )
            items.append(
                f"<li><h4>{title}</h4>"
                f'<p class="conclusion">{esc(a.get("conclusion") or "")}</p>'
                f'<ol class="evidence-list">{"".join(lis)}</ol></li>'
            )
        else:
            reason = esc(a.get("fail_reason") or "证据解析失败，请人工复核")
            items.append(
                f"<li><h4>{title}</h4>"
                f'<p class="unverified">（未联网核查，{reason}）</p></li>'
            )
    return (
        '<section class="fact-check-evidence" aria-label="事实核查证据">'
        "<h3>事实核查证据 (本简报)</h3>"
        '<div class="rollup">'
        f'<span class="badge web_search">已联网核查: {checked}/{total} 篇{fail_bit}</span>'
        f'<span class="meta">方法: {method} · 时效: {snapshot}</span>'
        "</div>"
        f'<ol class="evidence-articles">{"".join(items)}</ol>'
        "</section>"
    )


def _parse_json_obj(output: str) -> dict[str, Any] | None:
    if not output:
        return None
    m = re.search(r"```json\s*\n(.*?)\n```", output, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", output, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return None


def _update_progress(
    conn: sqlite3.Connection,
    task_id: str | None,
    stage: str,
    pct: int,
    message: str,
) -> None:
    if not task_id:
        return
    try:
        conn.execute(
            "UPDATE share_tasks SET progress_stage=?, progress_pct=?, progress_message=? WHERE id=?",
            (stage, pct, message, task_id),
        )
        conn.commit()
    except Exception as exc:
        log.warning("enrichment: progress update failed: %s", exc)


def _load_flags(manifest_path: str | None) -> dict[str, Any]:
    defaults = {
        "include_abstract": True,
        "include_fact_check": False,
        "include_business_value": False,
        "include_quality_score": False,
        "brief": False,
        "bb_summary_source": "moni_ai",
    }
    if not manifest_path:
        return defaults
    try:
        with open(manifest_path, encoding="utf-8") as f:
            doc = json.load(f)
        p = doc.get("payload") if isinstance(doc.get("payload"), dict) else {}
        for k in list(defaults):
            if k in p and p[k] is not None:
                defaults[k] = p[k]
        # brief mode implies enrichment defaults
        if defaults.get("brief"):
            defaults["include_abstract"] = True if p.get("include_abstract") is None else p.get("include_abstract", True)
            if "include_fact_check" not in p:
                defaults["include_fact_check"] = True
            if "include_business_value" not in p:
                defaults["include_business_value"] = True
            if "include_quality_score" not in p:
                defaults["include_quality_score"] = True
    except Exception as exc:
        log.warning("enrichment: bad manifest %s: %s", manifest_path, exc)
    return defaults


def _format_date(raw: str | None) -> str:
    """Normalize ISO/datetime-ish strings to YYYY-MM-DD for brief display."""
    if not raw or not str(raw).strip():
        return ""
    s = str(raw).strip()
    # 2026-06-23T12:14:00+08:00 / 2026-06-23 12:14:00
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    return s[:10] if len(s) >= 10 else s


def _sentence_count(text: str) -> int:
    parts = re.split(r"[。！？.!?]+", (text or "").strip())
    return len([p for p in parts if p.strip()])


def is_long_summary(
    text: str | None,
    *,
    max_sents: int = BRIEF_SUMMARY_MAX_SENTS,
    max_chars: int = BRIEF_SUMMARY_MAX_CHARS,
) -> bool:
    """True when summary should be AI-compressed for brief (option 2)."""
    if not text or not str(text).strip():
        return False
    t = str(text).strip()
    return _sentence_count(t) > max_sents or len(t) > max_chars


def pick_source_summary(row: sqlite3.Row | dict) -> tuple[str, str]:
    """Pick best full summary text for brief. Returns (text, origin_label)."""

    def g(key: str) -> str:
        try:
            v = row[key]
        except (KeyError, IndexError, TypeError):
            return ""
        return (v or "").strip() if v is not None else ""

    # Prefer editorial BB, then moni AI, then legacy summary
    for key, label in (
        ("bb_summary", "bestblogs"),
        ("ai_summary", "ai"),
        ("summary", "original"),
        ("bb_one_sentence", "bb_one"),
    ):
        t = g(key)
        if t:
            return t, label
    return "", "none"


def compute_heuristic_quality(row: sqlite3.Row | dict) -> int:
    """Local quality/attention score 0-100 when bb_score / LLM score missing.

    Prefer ``bb_score`` if present. Heuristic uses content richness + metadata.
    """
    def g(key: str, default=None):
        try:
            return row[key]
        except (KeyError, IndexError, TypeError):
            return default

    bb = g("bb_score")
    if bb is not None:
        try:
            return max(0, min(100, int(bb)))
        except (TypeError, ValueError):
            pass
    qs = g("quality_score")
    if qs is not None:
        try:
            return max(0, min(100, int(qs)))
        except (TypeError, ValueError):
            pass

    score = 45
    raw = (g("raw_content") or "") or ""
    n = len(raw.strip())
    if n >= 5000:
        score += 25
    elif n >= 2000:
        score += 18
    elif n >= 800:
        score += 10
    elif n >= THIN_CONTENT_CHARS:
        score += 4
    else:
        score -= 20  # thin / empty

    if g("ai_summary") and str(g("ai_summary")).strip():
        score += 8
    if g("fact_check_conclusion") and str(g("fact_check_conclusion")).strip():
        score += 7
    if g("publisher") and str(g("publisher")).strip():
        score += 5
    if g("published_at") and str(g("published_at")).strip():
        score += 3
    # Channel prior (not the display publisher)
    src = (g("source") or "") or ""
    if src.startswith("bestblog"):
        score += 2
    elif src in ("mpaypass", "wechat_rss_a", "wechat_rss_b"):
        score += 1

    return max(0, min(100, score))


def _article_rows(conn: sqlite3.Connection, article_ids: list[str]) -> list[sqlite3.Row]:
    if not article_ids:
        return []
    ph = ",".join("?" * len(article_ids))
    # Preserve pin/caller order via CASE
    order_cases = " ".join(f"WHEN ? THEN {i}" for i in range(len(article_ids)))
    sql = f"""
        SELECT id, title, raw_content, ai_summary, summary,
               fact_check_conclusion, fact_check_note, has_fact_check,
               business_value_score, business_value_note,
               business_value_status, business_value_hash, business_value_rubric,
               quality_score, quality_formula, bb_score, bb_summary, bb_one_sentence,
               brief_summary, brief_body_md, source, url, publisher, published_at
        FROM articles WHERE id IN ({ph})
        ORDER BY CASE id {order_cases} END
    """
    try:
        return list(conn.execute(sql, (*article_ids, *article_ids)).fetchall())
    except sqlite3.OperationalError:
        # Pre-migration 022 / 021 / 020 fallbacks
        sql_fallback = f"""
            SELECT id, title, raw_content, ai_summary, summary,
                   fact_check_conclusion, fact_check_note, has_fact_check,
                   business_value_score, business_value_note,
                   NULL as business_value_status, NULL as business_value_hash,
                   NULL as business_value_rubric,
                   quality_score, NULL as quality_formula, bb_score, bb_summary, NULL as bb_one_sentence,
                   NULL as brief_summary, NULL as brief_body_md,
                   source, url, publisher, published_at
            FROM articles WHERE id IN ({ph})
        """
        try:
            return list(conn.execute(sql_fallback, (*article_ids, *article_ids)).fetchall())
        except sqlite3.OperationalError:
            sql_min = f"""
                SELECT id, title, raw_content, ai_summary, NULL as summary,
                       fact_check_conclusion, fact_check_note, has_fact_check,
                       NULL as business_value_score, NULL as business_value_note,
                       NULL as business_value_status, NULL as business_value_hash,
                       NULL as business_value_rubric,
                       NULL as quality_score, NULL as quality_formula, NULL as bb_score, NULL as bb_summary,
                       NULL as bb_one_sentence, NULL as brief_summary, NULL as brief_body_md,
                       source, url, publisher, published_at
                FROM articles WHERE id IN ({ph})
            """
            try:
                return list(conn.execute(sql_min, article_ids).fetchall())
            except sqlite3.OperationalError:
                return []


def _ensure_fact_check(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    extra_args: list[str],
    budget_left: list[float],
) -> bool:
    """Returns True if wrote or already present; False on skip/fail."""
    if row["fact_check_conclusion"] and str(row["fact_check_conclusion"]).strip():
        return True
    content = (row["raw_content"] or "")[:3000]
    if not content.strip():
        now = _now()
        conn.execute(
            "UPDATE articles SET fact_check_conclusion=?, fact_check_method=?, "
            "fact_checked_at=?, fact_check_note=?, has_fact_check=1 WHERE id=?",
            ("无需核查", "llm_inference", now, "无正文", row["id"]),
        )
        conn.commit()
        return True
    if budget_left[0] <= 0:
        return False
    prompt = FACT_CHECK_PROMPT.format(
        article_id=row["id"],
        title=row["title"] or "",
        content=content,
    )
    timeout = min(STEP_TIMEOUT_SEC, max(15, int(budget_left[0])))
    out, code = _invoke_hermes(prompt, timeout_sec=timeout, extra_args=extra_args)
    budget_left[0] -= timeout  # approximate
    if code != 0:
        log.warning("enrichment: fact_check hermes exit %s for %s", code, row["id"])
        return False
    data = _parse_json_obj(out) or {}
    conclusion = (data.get("conclusion") or "").strip() or "待人工复核"
    method = (data.get("method") or "llm_inference").strip()
    note = (data.get("note") or "").strip()
    now = _now()
    conn.execute(
        "UPDATE articles SET fact_check_conclusion=?, fact_check_method=?, "
        "fact_checked_at=?, fact_check_note=?, has_fact_check=1 WHERE id=?",
        (conclusion, method, now, note, row["id"]),
    )
    conn.commit()
    return True


def _ensure_business_value(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    extra_args: list[str],
    budget_left: list[float],
    article_budget: float | None = None,
) -> bool:
    """Run the v1 harness.  Returns False only when the article must be retried."""
    from moni.scoring import (
        BvResult,
        content_hash,
        from_row,
        needs_bv_rescore,
        score_general_bv,
        write_bv,
    )
    from moni.scoring.bv_pipeline import STATUS_SKIPPED

    snap = from_row(row)
    if not needs_bv_rescore(row, snap):
        return True
    alloc = budget_left[0] if article_budget is None else min(article_budget, budget_left[0])
    if alloc < MIN_BV_ARTICLE_BUDGET:
        # Persist the skip so the brief can say「业务价值未完成」instead of
        # showing a stale score, and so the next run retries it.
        try:
            write_bv(
                conn,
                snap,
                BvResult(
                    status=STATUS_SKIPPED,
                    error="预算不足",
                    content_hash=content_hash(snap.body),
                ),
            )
            conn.commit()
        except sqlite3.OperationalError as exc:
            log.error("enrichment: BV skip write failed (migration 045 missing?): %s", exc)
        return False

    def llm(prompt: str, timeout: float) -> tuple[str, int]:
        started = time.monotonic()
        out, code = _invoke_hermes(prompt, timeout_sec=timeout, extra_args=extra_args)
        # Charge actual wall time, not the requested timeout: a fast hermes
        # must not spend the whole run budget (§4.6).
        budget_left[0] -= max(time.monotonic() - started, 0.0)
        return out, code

    elapsed = time.monotonic()
    result = score_general_bv(
        snap,
        llm=llm,
        budget_s=alloc,
        step_timeout=min(STEP_TIMEOUT_SEC, max(15.0, alloc)),
    )
    elapsed = time.monotonic() - elapsed
    try:
        write_bv(conn, snap, result)
        conn.commit()
    except sqlite3.OperationalError as exc:
        log.error("enrichment: BV write failed (migration 045 missing?): %s", exc)
        return False
    log.info(
        "scoring bv article=%s status=%s calls=%s elapsed=%.1fs budget_left=%.1fs",
        row["id"],
        result.status,
        result.calls_used,
        elapsed,
        budget_left[0],
    )
    if result.status in ("ready", "needs_review", "insufficient"):
        return True
    log.warning(
        "enrichment: business_value %s for %s (%s)",
        result.status,
        row["id"],
        result.error or "",
    )
    return False


def _ensure_quality(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    extra_args: list[str],
    budget_left: list[float],
    *,
    use_llm: bool = False,
) -> bool:
    """Ensure quality_score is set. Default: free heuristic (no LLM).

    bb_score is display-preferred but we still store a quality_score when
    missing so brief templates never show empty 「—」.
    """
    # 045: quality_score now belongs to the Pin column (pin_heuristic_v2).
    if "quality_formula" in row.keys() and row["quality_formula"]:
        return True
    if row["quality_score"] is not None:
        return True

    # Fast path: heuristic (always works, no budget)
    sc = compute_heuristic_quality(row)
    if use_llm and budget_left[0] > 30:
        content = (row["raw_content"] or "")[:2000]
        if content.strip():
            prompt = QUALITY_PROMPT.format(
                article_id=row["id"], title=row["title"] or "", content=content
            )
            timeout = min(STEP_TIMEOUT_SEC, max(15, int(budget_left[0])))
            out, code = _invoke_hermes(prompt, timeout_sec=timeout, extra_args=extra_args)
            budget_left[0] -= timeout
            if code == 0:
                data = _parse_json_obj(out) or {}
                try:
                    sc = max(0, min(100, int(data.get("score", sc))))
                except (TypeError, ValueError):
                    pass
    now = _now()
    try:
        conn.execute(
            "UPDATE articles SET quality_score=?, quality_score_at=? WHERE id=?",
            (sc, now, row["id"]),
        )
        conn.commit()
    except sqlite3.OperationalError:
        return False
    return True


def _needs_body_layout(raw: str | None) -> bool:
    """Heuristic: plain/noisy fetch bodies benefit from agent layout."""
    if not raw or not str(raw).strip():
        return False
    t = str(raw).strip()
    if len(t) < 120:
        return False
    # already well-structured markdown
    if t.count("\n\n") >= 4 and (t.count("## ") + t.count("# ") >= 1):
        return False
    # wall of text or mid-line ## or frontmatter dump
    if "\n\n" not in t and len(t) > 400:
        return True
    if re.search(r"。\s*##\s", t):
        return True
    if t.lstrip().startswith("---") and "title:" in t[:200]:
        return True
    # few line breaks relative to length
    if len(t) > 600 and t.count("\n") < max(3, len(t) // 400):
        return True
    return True  # default: layout once for brief offline reading


def _parse_body_md(output: str) -> str:
    """Extract markdown body from hermes output (strip fences / chatter)."""
    if not output:
        return ""
    text = output.strip()
    # fenced block
    m = re.search(r"```(?:markdown|md)?\s*\n(.*?)```", text, re.DOTALL | re.I)
    if m:
        return m.group(1).strip()
    # drop leading hermes chrome lines
    lines = text.splitlines()
    start = 0
    for i, ln in enumerate(lines[:12]):
        if re.match(r"^#+\s+", ln) or (ln.strip() and not ln.startswith("╭") and "Hermes" not in ln):
            if "Session:" in ln or "Duration:" in ln or "Resume this" in ln:
                continue
            start = i
            break
    body = "\n".join(lines[start:]).strip()
    # cut trailing session footer
    body = re.split(r"\nSession:\s|\nResume this session", body)[0].strip()
    return body


def local_body_layout(raw: str, title: str = "") -> str:
    """Offline heuristic layout when hermes times out or body is huge.

    Only structure: strip frontmatter/noise, paragraphize, promote 1. 2. headings.
    """
    t = raw.strip()
    if t.lstrip().startswith("---"):
        m = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", t, re.DOTALL)
        if m:
            t = m.group(1).strip()
    # drop common chrome tokens on their own lines
    noise = re.compile(
        r"^(日报|专栏|热点|国际|活动|在看|推荐阅读|REVIEW|◆|点这里|分享到|返回顶部)\s*$"
    )
    lines = []
    for ln in t.replace("\r\n", "\n").split("\n"):
        s = ln.strip()
        if not s or noise.match(s):
            continue
        lines.append(s)
    t = "\n".join(lines)
    # mid-line headings
    t = re.sub(r"([。！？；;])\s*(#{1,3}\s+)", r"\1\n\n\2", t)
    t = re.sub(r"([。！？])\s*((?:\d+)[.、．]\s*)", r"\1\n\n## \2", t)
    # wall of text → paragraphs
    if "\n\n" not in t and len(t) > 280:
        t = re.sub(r"([。！？])(?=[^\n]{12,})", r"\1\n\n", t)
    # numbered list lines as headings if look like section titles
    out_lines = []
    for ln in t.split("\n"):
        s = ln.strip()
        if not s:
            out_lines.append("")
            continue
        m = re.match(r"^(\d+)[.、．]\s*(.+)$", s)
        if m and len(m.group(2)) < 40 and not s.endswith("。"):
            out_lines.append(f"## {m.group(1)}. {m.group(2)}")
        else:
            out_lines.append(s)
    body = "\n".join(out_lines).strip()
    # collapse 3+ blank lines
    body = re.sub(r"\n{3,}", "\n\n", body)
    if title and not body.startswith("#"):
        body = f"## {title.strip()}\n\n{body}"
    return body


def _ensure_brief_body(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    extra_args: list[str],
    budget_left: list[float],
) -> bool:
    """Agent layout: raw_content → brief_body_md (structure only, no rewrite)."""
    try:
        existing = row["brief_body_md"]
    except (KeyError, IndexError, TypeError):
        existing = None
    if existing and str(existing).strip():
        return True

    raw = (row["raw_content"] or "").strip()
    if not raw:
        return True

    now = _now()

    def _write(md: str) -> bool:
        try:
            conn.execute(
                "UPDATE articles SET brief_body_md=?, brief_body_at=? WHERE id=?",
                (md, now, row["id"]),
            )
            conn.commit()
            return True
        except sqlite3.OperationalError as exc:
            log.warning("enrichment: brief_body_md write failed (mig 022?): %s", exc)
            return False

    if not _needs_body_layout(raw):
        cleaned = raw
        if cleaned.lstrip().startswith("---"):
            m = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", cleaned, re.DOTALL)
            if m:
                cleaned = m.group(1).strip()
        return _write(cleaned)

    # Super-long bodies: local layout first (hermes often times out at 15k+)
    if len(raw) > 9000 or budget_left[0] <= 30:
        return _write(local_body_layout(raw, row["title"] or ""))

    content = raw[:7000]
    prompt = BODY_LAYOUT_PROMPT.format(
        article_id=row["id"],
        title=row["title"] or "",
        content=content,
    )
    timeout = min(150, max(40, int(min(STEP_TIMEOUT_SEC + 30, budget_left[0]))))
    out, code = _invoke_hermes(prompt, timeout_sec=timeout, extra_args=extra_args)
    budget_left[0] -= timeout
    if code != 0:
        log.warning(
            "enrichment: body layout hermes exit %s for %s — local fallback",
            code,
            row["id"],
        )
        return _write(local_body_layout(raw, row["title"] or ""))
    md = _parse_body_md(out)
    if len(md) < 40:
        log.warning("enrichment: body layout too short for %s — local fallback", row["id"])
        return _write(local_body_layout(raw, row["title"] or ""))
    return _write(md)


def apply_local_body_layout_for_ids(article_ids: list[str]) -> dict[str, int]:
    """Fast path: fill brief_body_md with local layout for newly ingested articles.

    No hermes. Safe to call from fetch hot path after insert.
    Returns {scanned, updated, skipped, failed}.
    """
    stats = {"scanned": 0, "updated": 0, "skipped": 0, "failed": 0}
    if not article_ids:
        return stats
    from moni.db.connection import get_db

    conn = get_db()
    conn.row_factory = sqlite3.Row
    for aid in article_ids:
        stats["scanned"] += 1
        try:
            row = conn.execute(
                "SELECT id, title, raw_content, brief_body_md FROM articles WHERE id=?",
                (aid,),
            ).fetchone()
        except sqlite3.OperationalError:
            stats["failed"] += 1
            continue
        if row is None:
            stats["skipped"] += 1
            continue
        existing = row["brief_body_md"]
        if existing and str(existing).strip():
            stats["skipped"] += 1
            continue
        raw = row["raw_content"] or ""
        title = row["title"] or ""
        if not str(raw).strip():
            stats["skipped"] += 1
            continue
        md = local_body_layout(str(raw), title)
        try:
            conn.execute(
                "UPDATE articles SET brief_body_md=?, brief_body_at=? WHERE id=?",
                (md, _now(), aid),
            )
            stats["updated"] += 1
        except sqlite3.OperationalError:
            stats["failed"] += 1
    try:
        conn.commit()
    except Exception:
        stats["failed"] += 1
    return stats


def layout_pending_bodies(
    *,
    limit: int = 50,
    use_agent: bool = False,
) -> dict[str, int]:
    """Backfill / upgrade bodies missing brief_body_md.

    - use_agent=False: local layout only (default for cron/CLI)
    - use_agent=True: hermes layout with local fallback (slower)
    """
    from moni.db.connection import get_db

    conn = get_db()
    stats = {"scanned": 0, "updated": 0, "skipped": 0, "failed": 0}
    try:
        rows = conn.execute(
            """
            SELECT id FROM articles
            WHERE (brief_body_md IS NULL OR TRIM(brief_body_md) = '')
              AND raw_content IS NOT NULL AND TRIM(raw_content) != ''
            ORDER BY fetched_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return stats
    ids = [r[0] for r in rows]
    if not use_agent:
        return apply_local_body_layout_for_ids(ids)

    budget = [float(ENRICH_BUDGET_SEC)]
    conn.row_factory = sqlite3.Row
    for aid in ids:
        stats["scanned"] += 1
        row = conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()
        if row is None:
            stats["skipped"] += 1
            continue
        ok = _ensure_brief_body(conn, row, [], budget)
        if ok:
            stats["updated"] += 1
        else:
            stats["failed"] += 1
    return stats


def _ensure_brief_summary(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    extra_args: list[str],
    budget_left: list[float],
) -> bool:
    """If source summary is long, AI-compress into brief_summary (keep full fields).

    Short summaries are stored as-is into brief_summary so rebuild is uniform.
    """
    existing = None
    try:
        existing = row["brief_summary"]
    except (KeyError, IndexError, TypeError):
        existing = None
    if existing and str(existing).strip():
        # Already compacted; skip unless source grew? keep cache.
        return True

    full, _origin = pick_source_summary(row)
    if not full:
        return True  # nothing to do

    if not is_long_summary(full):
        # Short enough: cache verbatim as brief_summary
        now = _now()
        try:
            conn.execute(
                "UPDATE articles SET brief_summary=?, brief_summary_at=? WHERE id=?",
                (full, now, row["id"]),
            )
            conn.commit()
        except sqlite3.OperationalError as exc:
            log.warning("enrichment: brief_summary write failed (mig 021?): %s", exc)
            return False
        return True

    if budget_left[0] <= 0:
        return False

    prompt = COMPACT_SUMMARY_PROMPT.format(
        article_id=row["id"],
        title=row["title"] or "",
        summary=full[:2000],
    )
    timeout = min(STEP_TIMEOUT_SEC, max(15, int(budget_left[0])))
    out, code = _invoke_hermes(prompt, timeout_sec=timeout, extra_args=extra_args)
    budget_left[0] -= timeout
    if code != 0:
        log.warning("enrichment: compact summary hermes exit %s for %s", code, row["id"])
        return False
    data = _parse_json_obj(out) or {}
    compact = (data.get("summary") or "").strip()
    if not compact:
        return False
    # Soft enforce length + avoid mid-sentence cutoffs ("相当于5.")
    if is_long_summary(compact):
        parts = re.split(r"(?<=[。！？.!?])", compact)
        parts = [p for p in parts if p.strip()]
        compact = "".join(parts[:2]).strip() or compact[:BRIEF_SUMMARY_MAX_CHARS]
    compact = compact.strip()
    if compact and compact[-1] not in "。！？.!?…」』\"'）)":
        # drop trailing incomplete clause after last full stop
        m = re.search(r"^(.*[。！？.!?])", compact, re.DOTALL)
        if m and m.group(1).strip():
            compact = m.group(1).strip()
        elif len(compact) > BRIEF_SUMMARY_MAX_CHARS:
            compact = compact[: BRIEF_SUMMARY_MAX_CHARS - 1].rstrip("，,、；;：:") + "…"
    now = _now()
    try:
        conn.execute(
            "UPDATE articles SET brief_summary=?, brief_summary_at=? WHERE id=?",
            (compact, now, row["id"]),
        )
        conn.commit()
    except sqlite3.OperationalError as exc:
        log.warning("enrichment: brief_summary write failed: %s", exc)
        return False
    return True


def run_enrichment(
    db_path: str,
    article_ids: list[str],
    *,
    task_id: str | None = None,
    manifest_path: str | None = None,
    invoke_hermes: Any = None,
) -> dict[str, Any]:
    """Run enrichment pipeline. Returns stats dict.

    ``invoke_hermes`` injectable for tests: (prompt, timeout, extra) -> (stdout, code)
    """
    global _invoke_hermes  # noqa: PLW0603 — test injection via param preferred
    if invoke_hermes is not None:
        # Patch summary_gen + local references by temporary replace
        import moni.share.summary_gen as sg
        import moni.share.enrichment as self_mod

        orig_sg = sg._invoke_hermes
        orig_en = self_mod._invoke_hermes
        sg._invoke_hermes = invoke_hermes  # type: ignore[assignment]
        self_mod._invoke_hermes = invoke_hermes  # type: ignore[assignment]
    else:
        orig_sg = orig_en = None
        sg = None
        self_mod = None

    flags = _load_flags(manifest_path)
    stats: dict[str, Any] = {
        "summary": 0,
        "fact_check": 0,
        "business_value": 0,
        "quality": 0,
        "brief_summary": 0,
        "brief_body": 0,
        "fact_check_plus_enqueued": 0,
        "failed": [],
        "flags": flags,
    }
    budget_left = [float(ENRICH_BUDGET_SEC)]

    extra_args: list[str] = []
    if manifest_path:
        try:
            with open(manifest_path, encoding="utf-8") as f:
                p = json.load(f).get("payload") or {}
            if p.get("provider"):
                extra_args.extend(["--provider", str(p["provider"])])
            if p.get("model"):
                extra_args.extend(["--model", str(p["model"])])
        except Exception:
            pass

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # 1) summaries
        if flags.get("include_abstract"):
            _update_progress(conn, task_id, "enrich_summary", 25, "正在生成/复用 AI 摘要 ...")
            try:
                generated = generate_and_writeback(db_path, article_ids, manifest_path)
                stats["summary"] = len(generated)
            except Exception as exc:
                log.exception("enrichment: summary failed: %s", exc)
                stats["failed"].append(f"summary:{exc}")

        # reload rows after summary write
        rows = _article_rows(conn, article_ids)
        n = max(len(rows), 1)

        # 2) fact check
        if flags.get("include_fact_check"):
            for i, row in enumerate(rows):
                _update_progress(
                    conn,
                    task_id,
                    "enrich_fact_check",
                    40,
                    f"事实核查 ({i + 1}/{n}) ...",
                )
                ok = _ensure_fact_check(conn, row, extra_args, budget_left)
                if ok:
                    stats["fact_check"] += 1
                else:
                    stats["failed"].append(f"fact_check:{row['id']}")

        rows = _article_rows(conn, article_ids)

        # 3) business value
        if flags.get("include_business_value"):
            for i, row in enumerate(rows):
                _update_progress(
                    conn,
                    task_id,
                    "enrich_business_value",
                    55,
                    f"业务价值评估 ({i + 1}/{n}) ...",
                )
                share = budget_left[0] / max(len(rows) - i, 1)
                ok = _ensure_business_value(
                    conn,
                    row,
                    extra_args,
                    budget_left,
                    article_budget=min(BV_ARTICLE_BUDGET_SEC, share),
                )
                if ok:
                    stats["business_value"] += 1
                else:
                    stats["failed"].append(f"business_value:{row['id']}")

        rows = _article_rows(conn, article_ids)

        # 4) quality score — brief always on; heuristic by default (no LLM cost)
        if flags.get("include_quality_score") or flags.get("brief"):
            _update_progress(conn, task_id, "enrich_summary", 58, "计算质量/注意力分 ...")
            for row in rows:
                if _ensure_quality(conn, row, extra_args, budget_left, use_llm=False):
                    stats["quality"] += 1

        # 5) brief summary compact — only long ones AI-rewrite (option 2)
        if flags.get("brief") or flags.get("include_abstract"):
            rows = _article_rows(conn, article_ids)
            n = max(len(rows), 1)
            for i, row in enumerate(rows):
                _update_progress(
                    conn,
                    task_id,
                    "enrich_summary",
                    62,
                    f"压缩超长摘要 ({i + 1}/{n}) ...",
                )
                if _ensure_brief_summary(conn, row, extra_args, budget_left):
                    stats["brief_summary"] += 1
                else:
                    stats["failed"].append(f"brief_summary:{row['id']}")

        # 6) agent body layout for offline HTML (structure only)
        if flags.get("brief"):
            rows = _article_rows(conn, article_ids)
            n = max(len(rows), 1)
            for i, row in enumerate(rows):
                _update_progress(
                    conn,
                    task_id,
                    "enrich_summary",
                    64,
                    f"正文排版 ({i + 1}/{n}) ...",
                )
                if _ensure_brief_body(conn, row, extra_args, budget_left):
                    stats["brief_body"] += 1
                else:
                    stats["failed"].append(f"brief_body:{row['id']}")

        # 7) v1.0.24 brief web mode: enqueue missing fact_check_plus (non-blocking)
        #    then stamp evidence_rendered_at. Never wait for job completion.
        if flags.get("brief"):
            _update_progress(
                conn, task_id, "enrich_fact_check", 66, "检查联网核查覆盖 ..."
            )
            provider, model = _resolve_fact_check_provider_model(
                conn, extra_args=extra_args, manifest_path=manifest_path
            )
            try:
                enq = _enqueue_missing_web_fact_checks(
                    conn,
                    article_ids,
                    db_path=db_path,
                    provider=provider,
                    model=model,
                )
                stats["fact_check_plus_enqueued"] = enq
            except Exception as exc:
                log.warning("enrichment: web fact_check enqueue step failed: %s", exc)
                stats["failed"].append(f"fact_check_plus_enqueue:{exc}")
            try:
                _write_evidence_rendered_at(conn, article_ids)
            except Exception as exc:
                log.warning("enrichment: evidence_rendered_at stamp failed: %s", exc)

        _update_progress(conn, task_id, "building_prompt", 65, "富化完成，准备拼装简报 ...")
    finally:
        conn.close()
        if invoke_hermes is not None and sg is not None and self_mod is not None:
            sg._invoke_hermes = orig_sg  # type: ignore[assignment]
            self_mod._invoke_hermes = orig_en  # type: ignore[assignment]

    return stats


def rebuild_article_content(
    db_path: str,
    article_ids: list[str],
    *,
    brief: bool = False,
    bb_summary_source: str = "moni_ai",
) -> str:
    """Re-read articles and build prompt body blocks (post-enrichment).

    Brief mode: sort by business_value_score DESC, then original order.
    v1.0.24: append 事实核查证据 section (MD) when brief=True.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = _article_rows(conn, article_ids)
        evidence_md = ""
        if brief and article_ids:
            try:
                ctx = load_evidence_context(conn, article_ids)
                evidence_md = build_evidence_section_md(ctx)
            except Exception as exc:
                log.warning("enrichment: evidence section build failed: %s", exc)
                evidence_md = (
                    "## 事实核查证据 (本简报)\n\n"
                    "- ✅ 已联网核查: 0/0 篇\n"
                    "- （未联网核查，证据解析失败，请人工复核）\n"
                )
    finally:
        conn.close()

    if brief:
        def sort_key(r: sqlite3.Row) -> tuple:
            sc = r["business_value_score"]
            try:
                status = r["business_value_status"]
            except (KeyError, IndexError):
                status = None
            if status == "ready":
                tier = 0
            elif status is None:
                tier = 0 if sc is not None else 3
            elif status == "needs_review":
                tier = 2
            else:
                tier = 3
            return (tier, -(sc or 0))

        rows = sorted(rows, key=sort_key)

    parts: list[str] = []
    for row in rows:
        content = (row["raw_content"] or "").strip()
        if not content:
            content = "（无正文）"
        publisher = (row["publisher"] or "").strip()
        channel = (row["source"] or "").strip()
        # Display: 发布方 first (e.g. 极客公园), channel secondary (bestblogs)
        if publisher and channel:
            source_display = f"{publisher}（采集通道: {channel}）"
        elif publisher:
            source_display = publisher
        else:
            source_display = channel or "未知来源"
        pub_date = _format_date(row["published_at"]) or "日期不详"

        block = (
            f"### 文章: {row['title']}\n"
            f"ID: {row['id']}\n"
            f"发布方: {publisher or '（无）'}\n"
            f"采集通道: {channel or '（无）'}\n"
            f"来源展示: {source_display}\n"
            f"发布日期: {pub_date}\n"
        )
        if row["url"]:
            block += f"链接: {row['url']}\n"
        block += f"\n{content}"

        # reading score: BestBlogs if present, else the local reading heuristic.
        # quality_score now belongs to the Pin column and must not leak here.
        from moni.scoring import from_row as _snap_from_row, reading_heuristic

        if row["bb_score"] is not None:
            q = row["bb_score"]
            q_label = "BestBlogs 评分"
        else:
            q = reading_heuristic(_snap_from_row(row))
            q_label = "阅读分"
        block += f"\n\n**{q_label}**: {q}/100"

        bv = row["business_value_score"]
        if bv is not None:
            block += f"\n\n**业务价值分**: {bv}/100"
        if row["business_value_note"] and str(row["business_value_note"]).strip():
            block += f"\n\n**业务价值评判**: {row['business_value_note']}"

        # Brief prefers brief_summary (short or AI-compressed); keep origin for audit
        brief_sum = ""
        try:
            brief_sum = (row["brief_summary"] or "").strip()
        except (KeyError, IndexError, TypeError):
            brief_sum = ""
        full_sum, origin = pick_source_summary(row)
        if brief_sum:
            block += f"\n\n**简报摘要** *(压短或原文够短，供正文使用)*: {brief_sum}"
            if full_sum and full_sum != brief_sum:
                block += f"\n\n**原摘要备查** *({origin}，勿扩写进正文)*: {full_sum[:200]}{'…' if len(full_sum) > 200 else ''}"
        else:
            show_ai = bb_summary_source in ("moni_ai", "both") and row["ai_summary"]
            show_bb = bb_summary_source in ("bestblogs", "both") and row["bb_summary"]
            if show_ai and str(row["ai_summary"]).strip():
                block += f"\n\n**AI 摘要**: {row['ai_summary']}"
            if show_bb and str(row["bb_summary"]).strip():
                block += f"\n\n**BestBlogs 摘要**: {row['bb_summary']}"

        if row["fact_check_conclusion"] and str(row["fact_check_conclusion"]).strip():
            block += f"\n\n**事实核查结论**: {row['fact_check_conclusion']}"
            if row["fact_check_note"] and str(row["fact_check_note"]).strip():
                # normalize english tags if any slipped through
                note = str(row["fact_check_note"])
                note = (
                    note.replace("[supported]", "【已核实】")
                    .replace("[Supported]", "【已核实】")
                    .replace("[unverified]", "【待核实】")
                    .replace("[Unverified]", "【待核实】")
                    .replace("[disputed]", "【存疑】")
                    .replace("[Disputed]", "【存疑】")
                )
                block += f"\n**事实核查明细**: {note}"

        parts.append(block)

    body = "\n\n---\n\n".join(parts)
    if brief and evidence_md:
        # v1.0.24: 文末脚注式「事实核查证据」段 (银行 PDF 阅读习惯, Q2=A)
        if body:
            body = body + "\n\n---\n\n" + evidence_md
        else:
            body = evidence_md
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="moni share enrichment")
    parser.add_argument("--db", required=True, help="Path to news.db")
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--manifest", default=None)
    parser.add_argument(
        "--article-ids",
        required=True,
        help='JSON array of article ids, e.g. ["a","b"]',
    )
    parser.add_argument(
        "--print-content",
        action="store_true",
        help="After enrich, print rebuild_article_content to stdout",
    )
    args = parser.parse_args(argv)

    try:
        ids = json.loads(args.article_ids)
    except json.JSONDecodeError:
        import ast

        ids = ast.literal_eval(args.article_ids)
    if not isinstance(ids, list):
        print("article-ids must be a list", file=sys.stderr)
        return 2

    stats = run_enrichment(
        args.db,
        ids,
        task_id=args.task_id,
        manifest_path=args.manifest,
    )
    print(json.dumps(stats, ensure_ascii=False), file=sys.stderr)

    if args.print_content:
        flags = _load_flags(args.manifest)
        text = rebuild_article_content(
            args.db,
            ids,
            brief=bool(flags.get("brief")),
            bb_summary_source=str(flags.get("bb_summary_source") or "moni_ai"),
        )
        print(text)
    return 0


if __name__ == "__main__":
    # Prefer: python3 -m moni.share.enrichment ...
    raise SystemExit(main())
