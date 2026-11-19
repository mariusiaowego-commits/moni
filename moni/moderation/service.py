"""Preview / apply / review for content moderation labels.

Same shape as ``moni.cleaning.service``: preview never writes a label, apply
writes only rows whose payload actually changed (so a second apply reports
``new_rows=0``), and human decisions are never overwritten.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from moni.db.connection import get_db
from moni.fetch.moderation import (
    LABEL_RULE_VERSION,
    Category,
    Label,
    _GRAY_TERMS,
    _MARKET_TERMS,
    _NAME_STUFFING_TERMS,
    _NSFW_TERMS,
    evaluate_labels,
)

logger = logging.getLogger(__name__)

CATEGORIES: tuple[str, ...] = (
    "nsfw_gamble",
    "fin_crypto_ad",
    "spam_service",
    "pure_ad",
    "engagement_bait",
    "low_value_rant",
)

#: 简报表里不出现的类别（dad 2026-11-19 拍板）。
GATED_CATEGORIES: tuple[str, ...] = (
    "nsfw_gamble",
    "fin_crypto_ad",
    "spam_service",
    "pure_ad",
)

#: 闸门开关的配置键（存在 ``app_settings`` 表里，改配置不再需要发版）。
GATE_SETTING_KEY = "moderation.gate_enabled"

#: 缺省值：关。标注先跑一段时间给人看，确认不误杀再在页面上打开。
GATE_DEFAULT = False

_RULE_COUNTS = {
    "nsfw_gamble": len(_NSFW_TERMS),
    "fin_crypto_ad": len(_MARKET_TERMS),
    "spam_service": len(_GRAY_TERMS) + len(_NAME_STUFFING_TERMS),
    "pure_ad": 1,
    "engagement_bait": 5,
    "low_value_rant": 1,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def gate_enabled(conn=None) -> bool:
    """闸门是否打开。读 ``app_settings``；缺表/缺键一律按关闭处理。"""
    try:
        row = (conn or get_db()).execute(
            "SELECT value FROM app_settings WHERE key = ?", (GATE_SETTING_KEY,)
        ).fetchone()
    except Exception:  # noqa: BLE001 - 老库/只读场景不能因此报错
        logger.debug("app_settings unavailable; gate defaults to off", exc_info=True)
        return GATE_DEFAULT
    if row is None:
        return GATE_DEFAULT
    return str(row[0]).strip().lower() in ("1", "true", "on", "yes")


def set_gate_enabled(enabled: bool, conn=None) -> bool:
    """写闸门开关。页面上的开关直接调这里，不需要发版。"""
    db = conn or get_db()
    db.execute(
        """
        INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE
           SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (GATE_SETTING_KEY, "1" if enabled else "0", _now()),
    )
    db.commit()
    return enabled


def _load_run(conn, run_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM label_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(run_id)
    return _run_payload(row)


def _run_payload(row) -> dict[str, Any]:
    try:
        by_category = json.loads(row["by_category"] or "{}")
    except (TypeError, ValueError):
        by_category = {}
    return {
        "id": row["id"],
        "mode": row["mode"],
        "status": row["status"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "scanned": row["scanned"],
        "labeled": row["labeled"],
        "new_rows": row["new_rows"],
        "kept_manual": row["kept_manual"],
        "by_category": by_category,
        "rule_version": row["rule_version"],
        "error": row["error"],
    }


def load_run(run_id: str, conn=None) -> dict[str, Any]:
    return _load_run(conn or get_db(), run_id)


def list_runs(limit: int = 20, conn=None) -> list[dict[str, Any]]:
    rows = (conn or get_db()).execute(
        "SELECT * FROM label_runs ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_run_payload(r) for r in rows]


def _existing(conn, article_id: str, category: str):
    return conn.execute(
        "SELECT * FROM content_labels WHERE article_id = ? AND category = ?",
        (article_id, category),
    ).fetchone()


def _write_labels(
    conn,
    article_id: str,
    labels: Sequence[Label],
    now: str,
) -> tuple[int, int]:
    """Write one article's labels.  Returns ``(written_rows, kept_manual)``."""
    written = 0
    kept_manual = 0
    seen: set[str] = set()
    for label in labels:
        seen.add(label.category)
        row = _existing(conn, article_id, label.category)
        evidences = json.dumps(list(label.evidences), ensure_ascii=False)
        if row is not None:
            if row["source"] == "manual" or row["review_state"] != "pending":
                kept_manual += 1
                continue
            same = (
                row["severity"] == label.severity
                and float(row["confidence"]) == float(label.confidence)
                and row["evidences"] == evidences
                and row["rule_version"] == LABEL_RULE_VERSION
            )
            if same:
                continue
            conn.execute(
                """
                UPDATE content_labels
                   SET severity = ?, confidence = ?, evidences = ?,
                       rule_version = ?, labeled_at = ?
                 WHERE article_id = ? AND category = ?
                """,
                (
                    label.severity,
                    label.confidence,
                    evidences,
                    LABEL_RULE_VERSION,
                    now,
                    article_id,
                    label.category,
                ),
            )
            written += 1
            continue
        conn.execute(
            """
            INSERT INTO content_labels
                (article_id, category, severity, confidence, evidences,
                 rule_version, source, review_state, labeled_at)
            VALUES (?, ?, ?, ?, ?, ?, 'auto', 'pending', ?)
            """,
            (
                article_id,
                label.category,
                label.severity,
                label.confidence,
                evidences,
                LABEL_RULE_VERSION,
                now,
            ),
        )
        written += 1

    # 规则不再命中的旧 auto 行要撤掉，否则标签只增不减。
    stale = conn.execute(
        """
        SELECT category FROM content_labels
         WHERE article_id = ? AND source = 'auto' AND review_state = 'pending'
        """,
        (article_id,),
    ).fetchall()
    for row in stale:
        if row["category"] not in seen:
            conn.execute(
                "DELETE FROM content_labels WHERE article_id = ? AND category = ?",
                (article_id, row["category"]),
            )
            written += 1
    return written, kept_manual


def label_and_store(
    article_id: str,
    title: str,
    content: str,
    conn=None,
) -> int:
    """入库后标注：异常只记日志，绝不影响文章入库。"""
    try:
        own = conn is None
        db = conn or get_db()
        labels = evaluate_labels(title=title or "", content=content or "")
        written, _ = _write_labels(db, article_id, labels, _now())
        if own:
            db.commit()
        return written
    except Exception:  # noqa: BLE001 - 入库不能因为标注失败而回滚
        logger.warning("moderation labelling failed for %s", article_id, exc_info=True)
        return 0


def relabel_article(article_id: str, conn=None) -> int:
    """正文变化后重算一条（导入全文 / 编辑正文 / 重抓替换）。"""
    db = conn or get_db()
    row = db.execute(
        "SELECT title, raw_content FROM articles WHERE id = ?", (article_id,)
    ).fetchone()
    if row is None:
        return 0
    labels = evaluate_labels(title=row["title"] or "", content=row["raw_content"] or "")
    written, _ = _write_labels(db, article_id, labels, _now())
    db.commit()
    return written


def _scan(conn, category: str | None, limit: int | None):
    sql = "SELECT id, title, raw_content FROM articles"
    params: list[Any] = []
    if category:
        sql = (
            "SELECT a.id, a.title, a.raw_content FROM articles a "
            "JOIN content_labels l ON l.article_id = a.id AND l.category = ?"
        )
        params.append(category)
    sql += " ORDER BY id"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def _run(
    mode: str,
    conn=None,
    category: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    db = conn or get_db()
    run_id = uuid.uuid4().hex
    now = _now()
    db.execute(
        """
        INSERT INTO label_runs
            (id, mode, status, started_at, rule_version)
        VALUES (?, ?, 'running', ?, ?)
        """,
        (run_id, mode, now, LABEL_RULE_VERSION),
    )
    db.commit()

    try:
        rows = _scan(db, category, limit)
        labeled = 0
        new_rows = 0
        kept_manual = 0
        by_category: dict[str, int] = {name: 0 for name in CATEGORIES}
        for row in rows:
            labels = evaluate_labels(
                title=row["title"] or "", content=row["raw_content"] or ""
            )
            if labels:
                labeled += 1
            for label in labels:
                by_category[label.category] = by_category.get(label.category, 0) + 1
            if mode == "apply":
                written, kept = _write_labels(db, row["id"], labels, _now())
                new_rows += written
                kept_manual += kept
        db.commit()
        finished = _now()
        db.execute(
            """
            UPDATE label_runs
               SET status = 'done', finished_at = ?, scanned = ?, labeled = ?,
                   new_rows = ?, kept_manual = ?, by_category = ?
             WHERE id = ?
            """,
            (
                finished,
                len(rows),
                labeled,
                new_rows,
                kept_manual,
                json.dumps(by_category, ensure_ascii=False),
                run_id,
            ),
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        db.execute(
            "UPDATE label_runs SET status = 'failed', finished_at = ?, error = ? WHERE id = ?",
            (_now(), str(exc)[:500], run_id),
        )
        db.commit()
        raise
    return _load_run(db, run_id)


def preview(conn=None, category: str | None = None, limit: int | None = None) -> dict[str, Any]:
    return _run("preview", conn=conn, category=category, limit=limit)


def apply_labels(
    conn=None, category: str | None = None, limit: int | None = None
) -> dict[str, Any]:
    return _run("apply", conn=conn, category=category, limit=limit)


def summary(conn=None) -> dict[str, Any]:
    db = conn or get_db()
    counts: dict[str, dict[str, int]] = {}
    for name in CATEGORIES:
        rows = db.execute(
            "SELECT review_state, COUNT(*) AS n FROM content_labels "
            "WHERE category = ? GROUP BY review_state",
            (name,),
        ).fetchall()
        states = {r["review_state"]: r["n"] for r in rows}
        counts[name] = {
            "total": sum(states.values()),
            "pending": states.get("pending", 0),
            "confirmed": states.get("confirmed", 0),
            "dismissed": states.get("dismissed", 0),
            "rules": _RULE_COUNTS.get(name, 0),
        }
    last = db.execute(
        "SELECT * FROM label_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return {
        "rule_version": LABEL_RULE_VERSION,
        "gate_enabled": gate_enabled(db),
        "categories": counts,
        "last_run": _run_payload(last) if last else None,
    }


def _mask(title: str) -> str:
    if not title:
        return "***"
    if len(title) <= 4:
        return title[0] + "***"
    return f"{title[:2]}***{title[-2:]}"


def samples(
    category: str,
    limit: int = 20,
    reveal: bool = False,
    conn=None,
) -> list[dict[str, Any]]:
    if category not in CATEGORIES:
        raise ValueError(f"unknown category: {category}")
    db = conn or get_db()
    rows = db.execute(
        """
        SELECT l.article_id, l.category, l.severity, l.confidence, l.evidences,
               l.review_state, a.title, a.source
          FROM content_labels l
          JOIN articles a ON a.id = l.article_id
         WHERE l.category = ?
         ORDER BY l.confidence DESC, l.labeled_at DESC
         LIMIT ?
        """,
        (category, max(1, min(limit, 100))),
    ).fetchall()
    out = []
    for row in rows:
        try:
            evidences = json.loads(row["evidences"] or "[]")
        except (TypeError, ValueError):
            evidences = []
        title = row["title"] or ""
        if category == "nsfw_gamble" and not reveal:
            title = _mask(title)
        out.append(
            {
                "article_id": row["article_id"],
                "category": row["category"],
                "severity": row["severity"],
                "confidence": row["confidence"],
                "evidences": evidences,
                "review_state": row["review_state"],
                "title": title,
                "source": row["source"],
            }
        )
    return out


def review_label(
    article_id: str,
    category: str,
    decision: str,
    note: str = "",
    conn=None,
) -> dict[str, Any]:
    if category not in CATEGORIES:
        raise ValueError(f"unknown category: {category}")
    if decision not in ("confirmed", "dismissed"):
        raise ValueError("decision must be confirmed or dismissed")
    db = conn or get_db()
    now = _now()
    cur = db.execute(
        """
        UPDATE content_labels
           SET source = 'manual', review_state = ?, reviewed_at = ?, review_note = ?
         WHERE article_id = ? AND category = ?
        """,
        (decision, now, note[:200], article_id, category),
    )
    db.commit()
    if cur.rowcount == 0:
        raise KeyError(f"{article_id}/{category}")
    return {
        "article_id": article_id,
        "category": category,
        "review_state": decision,
        "reviewed_at": now,
    }


def gated_map(conn, article_ids: Iterable[str]) -> dict[str, str]:
    """被闸门拦下的文章 → 命中的类别（人工 dismissed 的不算）。"""
    if not gate_enabled(conn):
        return {}
    ids = [i for i in article_ids if i]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    cats = ",".join("?" for _ in GATED_CATEGORIES)
    rows = (conn or get_db()).execute(
        f"""
        SELECT article_id, category
          FROM content_labels
         WHERE article_id IN ({placeholders})
           AND category IN ({cats})
           AND review_state != 'dismissed'
        """,
        (*ids, *GATED_CATEGORIES),
    ).fetchall()
    return {r["article_id"]: r["category"] for r in rows}


def report_markdown(run: dict[str, Any]) -> str:
    lines = [
        "# 内容分级标注报告",
        "",
        f"- 运行：`{run['id']}`（{run['mode']}）",
        f"- 规则版本：`{run['rule_version']}`",
        f"- 扫描：{run['scanned']} 篇，命中：{run['labeled']} 篇",
        f"- 写入：{run['new_rows']} 行，人工保留：{run['kept_manual']} 行",
        "",
        "| 类别 | 命中 |",
        "| --- | ---: |",
    ]
    for name, count in run["by_category"].items():
        label = "S 类" if name == "nsfw_gamble" else name
        lines.append(f"| {label} | {count} |")
    lines.append("")
    lines.append("> 本报告只含数量，不含任何样本标题或原文。")
    return "\n".join(lines)
