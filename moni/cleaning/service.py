"""Preview / apply / audit for deterministic content cleaning.

Every write is behind a dry-run: ``preview`` scans and records a run but never
touches ``articles``; ``apply_cleaning_run`` re-scans and writes only rows whose
cleaned text actually differs.  A row whose body would become empty is skipped
and counted as ``emptied`` — that guard exists because the old cleaner could
destroy plain text that merely contained ``<ENV_ID>``.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from moni.db.connection import get_db
from moni.fetch.content import CLEANER_ID, SHORT_CHARS, apply_cleaning, same_visible_text

logger = logging.getLogger(__name__)

CONTRACTS = ("full_text", "snippet", "short_form", "title_only")
DEFAULT_CONTRACT = "full_text"
SAMPLE_LIMIT = 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _infer_contract(name: str, url: str | None) -> str:
    if name.startswith("bestblog") or name == "qbitai":
        return "snippet"
    if name in ("xbot_feed", "xwatch", "follow_builders"):
        return "short_form"
    if url and "36kr.com" in url:
        return "short_form"
    return DEFAULT_CONTRACT


def _contract_map(conn) -> dict[str, str]:
    rows = conn.execute("SELECT name, url, content_contract FROM sources").fetchall()
    return {
        r["name"]: (r["content_contract"] or _infer_contract(r["name"], r["url"]))
        for r in rows
    }


def _is_issue(contract: str, empty: int, short: int) -> int:
    return empty + short if contract == "full_text" else 0


def _run_payload(row) -> dict[str, Any]:
    details = {}
    if row["details_json"]:
        try:
            details = json.loads(row["details_json"])
        except (TypeError, json.JSONDecodeError):
            details = {}
    return {
        "id": row["id"],
        "mode": row["mode"],
        "scope": row["scope"],
        "status": row["status"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "scanned": row["scanned"],
        "changed": row["changed"],
        "backfilled": row["backfilled"],
        "emptied": row["emptied"],
        "content_empty": row["content_empty"],
        "content_short": row["content_short"],
        "chars_removed": row["chars_removed"],
        "archived_test_rows": row["archived_test_rows"],
        "error": row["error"],
        "details": details,
    }


def overview(conn=None) -> dict[str, Any]:
    """Cheap snapshot for the cleaning page (no re-scan of bodies)."""
    conn = conn or get_db()
    total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    states = {
        r["content_state"] or "unknown": r["n"]
        for r in conn.execute(
            "SELECT content_state, COUNT(*) AS n FROM articles GROUP BY content_state"
        ).fetchall()
    }
    pending = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE content_cleaner IS NULL OR content_cleaner != ?",
        (CLEANER_ID,),
    ).fetchone()[0]
    flags = {}
    for flag in (
        "boilerplate",
        "entities",
        "zero_width",
        "replacement",
        "frontmatter",
        "frontmatter_embedded",
        "html",
    ):
        flags[flag] = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE content_reasons LIKE ?",
            (f'%"{flag}"%',),
        ).fetchone()[0]
    test_rows = conn.execute(
        """
        SELECT COUNT(*) FROM articles a
        LEFT JOIN sources s ON s.name = a.source
        WHERE a.source IN ('testsrc', 's') AND s.name IS NULL
        """
    ).fetchone()[0]

    contracts = _contract_map(conn)
    sources = []
    rows = conn.execute(
        """
        SELECT source,
               COUNT(*) AS total,
               SUM(CASE WHEN content_state = 'empty' THEN 1 ELSE 0 END) AS empty,
               SUM(CASE WHEN content_state = 'short' THEN 1 ELSE 0 END) AS short,
               SUM(CASE WHEN content_state = 'ok' THEN 1 ELSE 0 END) AS ok,
               SUM(CASE WHEN content_state IS NULL THEN 1 ELSE 0 END) AS unknown
          FROM articles
         GROUP BY source
         ORDER BY total DESC
        """
    ).fetchall()
    for r in rows:
        contract = contracts.get(r["source"], DEFAULT_CONTRACT)
        sources.append({
            "source": r["source"],
            "contract": contract,
            "total": r["total"],
            "empty": r["empty"],
            "short": r["short"],
            "ok": r["ok"],
            "unknown": r["unknown"],
            "issues": _is_issue(contract, r["empty"], r["short"]),
        })

    runs = [
        _run_payload(r)
        for r in conn.execute(
            "SELECT * FROM cleaning_runs ORDER BY started_at DESC LIMIT 10"
        ).fetchall()
    ]
    return {
        "total": total,
        "states": states,
        "pending": pending,
        "flags": flags,
        "test_rows": test_rows,
        "sources": sources,
        "runs": runs,
        "cleaner": CLEANER_ID,
    }


def _scan(conn) -> dict[str, Any]:
    """Normalize every body in memory.  Writes nothing to ``articles``."""
    rows = conn.execute(
        "SELECT id, title, source, raw_content, content_cleaner FROM articles"
    ).fetchall()

    updates: list[dict[str, Any]] = []
    empties: list[dict[str, Any]] = []
    flags: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    weak_samples: list[dict[str, Any]] = []
    chars_removed = 0
    text_changed = 0
    whitespace_only = 0
    backfilled = 0
    emptied = 0
    empty = 0
    short = 0

    for row in rows:
        old = row["raw_content"] or ""
        art: dict[str, Any] = {"title": row["title"], "raw_content": old}
        cleaned, verdict = apply_cleaning(art)
        new = art["content"]

        if verdict.state == "empty":
            empty += 1
        elif verdict.state == "short":
            short += 1

        for flag in cleaned.flags:
            flags[flag] = flags.get(flag, 0) + 1

        changed = new != old or row["content_cleaner"] != CLEANER_ID
        if not changed:
            continue

        if new.strip() == "" and len(old.strip()) >= SHORT_CHARS:
            # Safety net: never let cleaning empty a substantial body.
            # Short bodies (16-char CTAs) are allowed to collapse to empty.
            emptied += 1
            empties.append({
                "content_state": art["content_state"],
                "content_chars": art["content_chars"],
                "content_reasons": art["content_reasons"],
                "content_cleaner": art["content_cleaner"],
                "content_cleaned_at": art["content_cleaned_at"],
                "id": row["id"],
            })
            continue

        visible_changed = not same_visible_text(new, old)
        if new != old:
            text_changed += 1
            if not visible_changed:
                whitespace_only += 1
        else:
            backfilled += 1
        chars_removed += max(0, len(old) - len(new))
        updates.append({
            "text_changed": new != old,
            # Whitespace-only rewrites must not invalidate summaries.
            "visible_changed": visible_changed,
            "old_text": old,
            "raw_content": new,
            "content_state": art["content_state"],
            "content_chars": art["content_chars"],
            "content_reasons": art["content_reasons"],
            "content_cleaner": art["content_cleaner"],
            "content_cleaned_at": art["content_cleaned_at"],
            "id": row["id"],
        })
        removed = len(old) - len(new)
        if len(samples) < SAMPLE_LIMIT or len(weak_samples) < SAMPLE_LIMIT:
            before, after = _sample_pair(old, new)
            sample = {
                "id": row["id"],
                "source": row["source"],
                "title": row["title"],
                "before": before,
                "after": after,
                "flags": list(cleaned.flags),
            }
            # Prefer visible changes: a whitespace-only diff reads as a no-op
            # in the preview panel and the Markdown report.
            if removed >= 10:
                if len(samples) < SAMPLE_LIMIT:
                    samples.append(sample)
            elif len(weak_samples) < SAMPLE_LIMIT:
                weak_samples.append(sample)

    test_row_ids = [
        r["id"]
        for r in conn.execute(
            """
            SELECT a.id FROM articles a
            LEFT JOIN sources s ON s.name = a.source
            WHERE a.source IN ('testsrc', 's') AND s.name IS NULL
            """
        ).fetchall()
    ]

    return {
        "scanned": len(rows),
        "updates": updates,
        "empties": empties,
        "changed": len(updates),
        "text_changed": text_changed,
        "whitespace_only": whitespace_only,
        "backfilled": backfilled,
        "emptied": emptied,
        "content_empty": empty,
        "content_short": short,
        "chars_removed": chars_removed,
        "flags": flags,
        "samples": samples or weak_samples,
        "test_row_ids": test_row_ids,
    }


def _record_run(conn, mode: str, scope: str, scan: dict[str, Any],
                status: str = "done", error: str | None = None,
                run_id: str | None = None) -> str:
    run_id = run_id or uuid.uuid4().hex[:16]
    details = {
        "flags": scan["flags"],
        "samples": scan["samples"],
        "test_rows": len(scan["test_row_ids"]),
    }
    conn.execute(
        """
        INSERT INTO cleaning_runs
            (id, mode, scope, status, started_at, finished_at, scanned,
             changed, backfilled, emptied, content_empty, content_short,
             chars_removed, archived_test_rows, details_json, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id, mode, scope, status, _now(), _now(), scan["scanned"],
            scan["text_changed"], scan["backfilled"], scan["emptied"],
            scan["content_empty"], scan["content_short"], scan["chars_removed"], 0,
            json.dumps(details, ensure_ascii=False), error,
        ),
    )
    conn.commit()
    return run_id


def preview(conn=None, scope: str = "all") -> dict[str, Any]:
    conn = conn or get_db()
    scan = _scan(conn)
    run_id = _record_run(conn, "preview", scope, scan)
    return _load_run(conn, run_id)


def _load_run(conn, run_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM cleaning_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(run_id)
    return _run_payload(row)


def _sample_pair(old: str, new: str, limit: int = 160) -> tuple[str, str]:
    """Head of the body, or a window around the first difference.

    Most edits (a footer line, a CTA tail) sit past the first 160 chars; a
    plain head-to-head preview then looks identical on both sides.
    """
    common = 0
    for a, b in zip(old, new):
        if a != b:
            break
        common += 1
    if common <= 120:
        return old[:limit], new[:limit]
    start = max(0, common - 60)
    return f"…{old[start:start + limit]}", f"…{new[start:start + limit]}"


def load_run(run_id: str, conn=None) -> dict[str, Any]:
    return _load_run(conn or get_db(), run_id)


def list_runs(limit: int = 20, conn=None) -> list[dict[str, Any]]:
    rows = (conn or get_db()).execute(
        "SELECT * FROM cleaning_runs ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_run_payload(r) for r in rows]


def _archive_test_rows(conn, article_ids: list[str]) -> int:
    """Move orphan-source rows to basket before deleting them (reversible)."""
    archived = 0
    now = _now()
    for aid in article_ids:
        row = conn.execute("SELECT * FROM articles WHERE id = ?", (aid,)).fetchone()
        if row is None:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO basket
                (id, title, url, source, publisher, published_at, fetched_at,
                 raw_content, dropped_reason, dropped_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'test_source', 'orphan_source', ?)
            """,
            (
                row["id"], row["title"], row["url"], row["source"],
                row["publisher"], row["published_at"], row["fetched_at"],
                row["raw_content"], now,
            ),
        )
        conn.execute("DELETE FROM articles WHERE id = ?", (aid,))
        archived += 1
    return archived


def apply_cleaning_run(conn=None, run_id: str | None = None) -> dict[str, Any]:
    """Re-scan and write.  Returns the recorded apply run."""
    conn = conn or get_db()
    scan = _scan(conn)
    started = _now()
    running_id = run_id or uuid.uuid4().hex[:16]
    conn.execute(
        """
        INSERT INTO cleaning_runs
            (id, mode, scope, status, started_at, scanned)
        VALUES (?, 'apply', 'all', 'running', ?, 0)
        """,
        (running_id, started),
    )
    conn.commit()

    try:
        for u in scan["updates"]:
            conn.execute(
                """
                UPDATE articles
                   SET raw_content = :raw_content,
                       content_state = :content_state,
                       content_chars = :content_chars,
                       content_reasons = :content_reasons,
                       content_cleaner = :content_cleaner,
                       content_cleaned_at = :content_cleaned_at
                 WHERE id = :id AND COALESCE(raw_content, '') = :old_text
                """,
                u,
            )
            if u["text_changed"] and conn.execute("SELECT changes()").fetchone()[0] == 0:
                continue
            if u["visible_changed"]:
                conn.execute(
                    """
                    UPDATE articles
                       SET summary = NULL, summary_at = NULL,
                           ai_summary = NULL, ai_summary_at = NULL,
                           has_ai_summary = 0,
                           brief_summary = NULL, brief_summary_at = NULL,
                           brief_body_md = NULL, brief_body_at = NULL
                     WHERE id = :id
                    """,
                    {"id": u["id"]},
                )
        for e in scan["empties"]:
            conn.execute(
                """
                UPDATE articles
                   SET content_state = :content_state,
                       content_chars = :content_chars,
                       content_reasons = :content_reasons,
                       content_cleaner = :content_cleaner,
                       content_cleaned_at = :content_cleaned_at
                 WHERE id = :id
                """,
                e,
            )
        archived = _archive_test_rows(conn, scan["test_row_ids"])
        conn.execute(
            """
            UPDATE cleaning_runs
               SET status = 'done', finished_at = ?, scanned = ?, changed = ?,
                   backfilled = ?, emptied = ?, content_empty = ?, content_short = ?,
                   chars_removed = ?, archived_test_rows = ?, details_json = ?
             WHERE id = ?
            """,
            (
                _now(), scan["scanned"], scan["text_changed"], scan["backfilled"],
                scan["emptied"],
                scan["content_empty"], scan["content_short"],
                scan["chars_removed"], archived,
                json.dumps(
                    {
                        "flags": scan["flags"],
                        "samples": scan["samples"],
                        "test_rows": len(scan["test_row_ids"]),
                        "whitespace_only": scan["whitespace_only"],
                    },
                    ensure_ascii=False,
                ),
                running_id,
            ),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        conn.execute(
            "UPDATE cleaning_runs SET status='failed', finished_at=?, error=? WHERE id=?",
            (_now(), f"{type(exc).__name__}: {exc}", running_id),
        )
        conn.commit()
        raise

    touched = [u["id"] for u in scan["updates"]]
    if touched:
        try:
            from moni.share.enrichment import apply_local_body_layout_for_ids

            # Bodies that only gained metadata do not need a layout rebuild.
            apply_local_body_layout_for_ids(
                [u["id"] for u in scan["updates"] if u["visible_changed"]]
            )
        except Exception as exc:
            logger.warning("layout after cleaning failed: %s", exc)
        from moni.scoring import recompute_quality_if_pinned

        for aid in touched:
            recompute_quality_if_pinned(conn, aid)

    return _load_run(conn, running_id)


def audit(conn=None) -> dict[str, Any]:
    data = overview(conn)
    return {
        "total": data["total"],
        "states": data["states"],
        "sources": data["sources"],
        "test_rows": data["test_rows"],
        "pending": data["pending"],
    }


def report_markdown(run: dict[str, Any]) -> str:
    details = run.get("details") or {}
    flags = details.get("flags") or {}
    samples = details.get("samples") or []
    lines = [
        f"# 数据清洗报告 · {run['started_at'][:10]}",
        "",
        f"- 模式：{'预演' if run['mode'] == 'preview' else '执行'}",
        f"- 扫描：{run['scanned']} 篇",
        f"- 文本变化：{run['changed']} 篇 · 仅补状态：{run['backfilled']} 篇",
        f"- 正文净减少：{run['chars_removed']} 字（含空白压缩）",
        f"- 安全跳过（会清空长正文）：{run['emptied']} 篇",
        f"- 空正文：{run['content_empty']} 篇 · 短稿：{run['content_short']} 篇",
        f"- 清理测试数据：{run['archived_test_rows']} 条",
        "",
        "## 杂质类型",
        "",
    ]
    labels = {
        "boilerplate": "公众号尾巴",
        "entities": "HTML 实体",
        "zero_width": "零宽字符",
        "replacement": "乱码 U+FFFD",
        "frontmatter": "YAML frontmatter",
        "frontmatter_embedded": "frontmatter 内嵌正文（未自动处理）",
        "html": "HTML 残留",
    }
    if flags:
        for key, label in labels.items():
            if flags.get(key):
                lines.append(f"- {label}：{flags[key]} 篇")
    else:
        lines.append("- 无")

    if samples:
        lines += ["", "## 前后对照", ""]
        for s in samples[:5]:
            lines += [
                f"### {s['title']}（{s['source']}）",
                f"- 清洗前：{s['before']}",
                f"- 清洗后：{s['after']}",
                "",
            ]
    lines += ["", "> 由 moni 数据清洗生成，可重复执行（幂等）。"]
    return "\n".join(lines)


__all__ = [
    "CONTRACTS",
    "DEFAULT_CONTRACT",
    "apply_cleaning_run",
    "audit",
    "list_runs",
    "load_run",
    "overview",
    "preview",
    "report_markdown",
]
