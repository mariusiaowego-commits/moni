"""moni.share.enrichment — brief-mode article enrichment with DB writeback.

v1.0.10 one-click brief pipeline (per article, skip if already present):
1. ai_summary          (reuses summary_gen.generate_and_writeback)
2. fact_check_*        (structured writeback; sets has_fact_check=1)
3. business_value_*    (score 0-100 + note)
4. quality_score       (only if bb_score missing and flag on)

Also rebuilds article content blocks for the format prompt (re-read after enrich).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any

from moni.share.summary_gen import _invoke_hermes, generate_and_writeback

log = logging.getLogger(__name__)

# Wall-clock budget for all enrichment hermes calls on one task (seconds).
ENRICH_BUDGET_SEC = int(os.environ.get("MONI_ENRICH_BUDGET_SEC", "480"))
STEP_TIMEOUT_SEC = int(os.environ.get("MONI_ENRICH_STEP_TIMEOUT", "90"))
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
               quality_score, bb_score, bb_summary, bb_one_sentence,
               brief_summary, source, url, publisher, published_at
        FROM articles WHERE id IN ({ph})
        ORDER BY CASE id {order_cases} END
    """
    try:
        return list(conn.execute(sql, (*article_ids, *article_ids)).fetchall())
    except sqlite3.OperationalError:
        # Pre-migration 021 / 020 fallbacks
        sql_fallback = f"""
            SELECT id, title, raw_content, ai_summary, summary,
                   fact_check_conclusion, fact_check_note, has_fact_check,
                   business_value_score, business_value_note,
                   quality_score, bb_score, bb_summary, NULL as bb_one_sentence,
                   NULL as brief_summary, source, url, publisher, published_at
            FROM articles WHERE id IN ({ph})
        """
        try:
            return list(conn.execute(sql_fallback, (*article_ids, *article_ids)).fetchall())
        except sqlite3.OperationalError:
            sql_min = f"""
                SELECT id, title, raw_content, ai_summary, NULL as summary,
                       fact_check_conclusion, fact_check_note, has_fact_check,
                       NULL as business_value_score, NULL as business_value_note,
                       NULL as quality_score, NULL as bb_score, NULL as bb_summary,
                       NULL as bb_one_sentence, NULL as brief_summary,
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
) -> bool:
    score = row["business_value_score"]
    note = row["business_value_note"]
    if score is not None and note and str(note).strip():
        return True
    content = (row["raw_content"] or "")[:3000]
    raw_len = len((row["raw_content"] or "").strip())
    if raw_len < THIN_CONTENT_CHARS:
        now = _now()
        conn.execute(
            "UPDATE articles SET business_value_score=?, business_value_note=?, "
            "business_value_at=? WHERE id=?",
            (25, "素材不足（正文过短），无法可靠评估业务价值。", now, row["id"]),
        )
        conn.commit()
        return True
    if budget_left[0] <= 0:
        return False
    prompt = BUSINESS_VALUE_PROMPT.format(
        article_id=row["id"],
        title=row["title"] or "",
        content=content,
    )
    timeout = min(STEP_TIMEOUT_SEC, max(15, int(budget_left[0])))
    out, code = _invoke_hermes(prompt, timeout_sec=timeout, extra_args=extra_args)
    budget_left[0] -= timeout
    if code != 0:
        log.warning("enrichment: business_value hermes exit %s for %s", code, row["id"])
        return False
    data = _parse_json_obj(out) or {}
    try:
        sc = int(data.get("score", 0))
    except (TypeError, ValueError):
        sc = 0
    sc = max(0, min(100, sc))
    bv_note = (data.get("note") or "").strip() or "（无说明）"
    now = _now()
    try:
        conn.execute(
            "UPDATE articles SET business_value_score=?, business_value_note=?, "
            "business_value_at=? WHERE id=?",
            (sc, bv_note, now, row["id"]),
        )
        conn.commit()
    except sqlite3.OperationalError as exc:
        log.error("enrichment: BV write failed (migration 020 missing?): %s", exc)
        return False
    return True


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
    if row["quality_score"] is not None:
        return True
    if row["bb_score"] is not None:
        # Mirror BB score into quality_score for uniform column
        now = _now()
        try:
            conn.execute(
                "UPDATE articles SET quality_score=?, quality_score_at=? WHERE id=?",
                (int(row["bb_score"]), now, row["id"]),
            )
            conn.commit()
            return True
        except (sqlite3.OperationalError, TypeError, ValueError):
            pass

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
                ok = _ensure_business_value(conn, row, extra_args, budget_left)
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
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = _article_rows(conn, article_ids)
    finally:
        conn.close()

    if brief:
        def sort_key(r: sqlite3.Row) -> tuple:
            sc = r["business_value_score"]
            # None last
            return (0 if sc is not None else 1, -(sc or 0))

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

        # quality: bb preferred, else quality_score, else heuristic on the fly
        q = row["bb_score"] if row["bb_score"] is not None else row["quality_score"]
        if q is None:
            q = compute_heuristic_quality(row)
        q_label = "BestBlogs 评分" if row["bb_score"] is not None else "质量/注意力分"
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

    return "\n\n---\n\n".join(parts)


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
