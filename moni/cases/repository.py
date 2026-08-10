"""moni.cases.repository — cases 表 CRUD + state machine 操作。

跟 F5 moni.events.repository 同一范式, 但:
- case_id 是人脑可读 (MN-<2字母>-<YYMM>-<4位序号>)
- 状态机重写: case_lifecycle (draft/verified/published/deprecated/archived)
- 不复用 extraction_status 枚举 (但保留作为 LLM 抽取过程状态)
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Sequence

from ..db.connection import get_db

logger = logging.getLogger(__name__)


def _generate_case_id(case_type: str, case_date: str | None) -> str:
    """生成 case_id = MN-<2字母>-<YYMM>-<4位序号>.

    序号按 (case_type, year-month) 在同事务内 COUNT+1,保证月内不撞号.

    Args:
        case_type: 'AI' / 'PI'
        case_date: 'YYYY-MM-DD' / 'YYYY-MM' / None

    Returns:
        'MN-AI-2608-0042' 风格
    """
    if case_date and len(case_date) >= 7 and case_date[:4].isdigit():
        # YYYY-MM-DD[:7] = YYYY-MM, 然后取 YYYY-MM 后 4 位 (YYMM)
        # 只认合法日期前缀 (YYYY-...) — 'relative'/'unknown' 等非日期串走 else
        yymm = case_date[:7].replace("-", "")[-4:]
    else:
        now = datetime.now(timezone.utc)
        yymm = f"{now.year % 100:02d}{now.month:02d}"

    conn = get_db()
    prefix = f"MN-{case_type}-{yymm}-"
    row = conn.execute(
        "SELECT COUNT(*) FROM cases WHERE case_id LIKE ?",
        (prefix + "%",),
    ).fetchone()
    seq = (row[0] if row else 0) + 1
    return f"{prefix}{seq:04d}"


class CaseRepository:
    """Repository for case persistence (cases table).

    v1.0.17: F6 同业案例库 — 从 articles 中抽取的行业案例。
    """

    # ── 写入 ──────────────────────────────────────────────────────────

    @staticmethod
    def insert_cases(
        conn: sqlite3.Connection,
        article_id: str,
        cases: Sequence[dict[str, Any]],
        *,
        normalize_version: int = 1,
    ) -> int:
        """批量插入归一化后的案例 (同事务内调用).

        Args:
            conn: 数据库连接 (调用方管理事务)
            article_id: 关联的文章 ID
            cases: 归一化后的案例列表

        Returns:
            实际插入的行数
        """
        if not cases:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        inserted = 0

        for cs in cases:
            case_id = _generate_case_id(cs["case_type"], cs.get("case_date"))

            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO cases
                        (case_id, article_id, case_date, case_date_precision,
                         subject, subject_raw, case_type, case_action,
                         counterparty, amount,
                         reference_value, reference_rationale,
                         summary, evidence_snippet,
                         extraction_confidence, extraction_status, review_status,
                         case_lifecycle,
                         extraction_attempts, last_error, extracted_at,
                         normalize_version, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, 'completed', 'pending',
                            'draft',
                            1, NULL, ?, ?, ?, ?)
                    """,
                    (
                        case_id, article_id,
                        cs.get("case_date"), cs.get("case_date_precision"),
                        cs["subject"], cs.get("subject_raw"),
                        cs["case_type"], cs["case_action"],
                        cs.get("counterparty"), cs.get("amount"),
                        cs.get("reference_value"), cs.get("reference_rationale"),
                        cs.get("summary"), cs.get("evidence_snippet"),
                        cs.get("extraction_confidence"),
                        now,
                        normalize_version,
                        now, now,
                    ),
                )
                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    inserted += 1
            except sqlite3.IntegrityError as e:
                logger.warning("插入案例失败 (%s): %s", case_id, e)
                continue

        return inserted

    # ── 状态机 ──────────────────────────────────────────────────────

    @staticmethod
    def update_lifecycle(
        conn: sqlite3.Connection,
        case_id: str,
        lifecycle: str,
    ) -> bool:
        """更新 case_lifecycle.

        Lifecycle: draft → verified → published → deprecated → archived
        (只允许前向流转,不回退)
        """
        if lifecycle not in ("draft", "verified", "published", "deprecated", "archived"):
            raise ValueError(f"Invalid lifecycle: {lifecycle}")

        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            "UPDATE cases SET case_lifecycle = ?, updated_at = ? WHERE case_id = ?",
            (lifecycle, now, case_id),
        )
        return cursor.rowcount > 0

    @staticmethod
    def confirm(conn: sqlite3.Connection, case_id: str) -> bool:
        """确认案例 (review_status pending → confirmed)."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            """UPDATE cases
               SET review_status = 'confirmed', updated_at = ?
               WHERE case_id = ? AND review_status = 'pending'""",
            (now, case_id),
        )
        return cursor.rowcount > 0

    @staticmethod
    def reject(conn: sqlite3.Connection, case_id: str, reason: str | None = None) -> bool:
        """拒绝案例 (review_status pending → rejected)."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            """UPDATE cases
               SET review_status = 'rejected', updated_at = ?,
                   last_error = COALESCE(?, last_error)
               WHERE case_id = ? AND review_status = 'pending'""",
            (now, reason, case_id),
        )
        return cursor.rowcount > 0

    # ── 读取 ────────────────────────────────────────────────────────

    @staticmethod
    def list_cases(
        case_type: str | None = None,
        case_action: str | None = None,
        subject: str | None = None,
        reference_value: str | None = None,
        review_status: str | None = None,
        case_lifecycle: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """案例列表 (带筛选 + 分页)."""
        sql = """
            SELECT c.case_id, c.article_id, a.title AS article_title,
                   a.source AS article_source, a.url AS article_url,
                   a.published_at AS article_published_at,
                   c.case_date, c.case_date_precision,
                   c.subject, c.subject_raw,
                   c.case_type, c.case_action,
                   c.counterparty, c.amount,
                   c.reference_value, c.reference_rationale,
                   c.summary, c.evidence_snippet,
                   c.extraction_confidence, c.review_status,
                   c.extraction_status, c.case_lifecycle,
                   c.extraction_attempts, c.last_error,
                   c.extracted_at, c.normalize_version,
                   c.created_at, c.updated_at
            FROM cases c
            LEFT JOIN articles a ON c.article_id = a.id
            WHERE 1 = 1
        """
        params: list[Any] = []

        if case_type:
            sql += " AND c.case_type = ?"
            params.append(case_type)
        if case_action:
            sql += " AND c.case_action = ?"
            params.append(case_action)
        if subject:
            sql += " AND (c.subject LIKE ? OR c.subject_raw LIKE ?)"
            params.extend([f"%{subject}%", f"%{subject}%"])
        if reference_value:
            sql += " AND c.reference_value = ?"
            params.append(reference_value)
        if review_status:
            sql += " AND c.review_status = ?"
            params.append(review_status)
        if case_lifecycle:
            sql += " AND c.case_lifecycle = ?"
            params.append(case_lifecycle)

        sql += " ORDER BY c.created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        conn = get_db()
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def count_cases(
        case_type: str | None = None,
        case_action: str | None = None,
        subject: str | None = None,
        reference_value: str | None = None,
        review_status: str | None = None,
        case_lifecycle: str | None = None,
    ) -> int:
        """案例总数."""
        sql = "SELECT COUNT(*) FROM cases WHERE 1 = 1"
        params: list[Any] = []
        if case_type:
            sql += " AND case_type = ?"; params.append(case_type)
        if case_action:
            sql += " AND case_action = ?"; params.append(case_action)
        if subject:
            sql += " AND (subject LIKE ? OR subject_raw LIKE ?)"; params.extend([f"%{subject}%", f"%{subject}%"])
        if reference_value:
            sql += " AND reference_value = ?"; params.append(reference_value)
        if review_status:
            sql += " AND review_status = ?"; params.append(review_status)
        if case_lifecycle:
            sql += " AND case_lifecycle = ?"; params.append(case_lifecycle)
        conn = get_db()
        return conn.execute(sql, params).fetchone()[0]

    @staticmethod
    def get_case(case_id: str) -> dict[str, Any] | None:
        """单条案例."""
        sql = """
            SELECT c.*, a.title AS article_title,
                   a.source AS article_source, a.url AS article_url
            FROM cases c
            LEFT JOIN articles a ON c.article_id = a.id
            WHERE c.case_id = ?
        """
        conn = get_db()
        row = conn.execute(sql, (case_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def get_by_article(article_id: str) -> list[dict[str, Any]]:
        """某篇文章的所有案例."""
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM cases WHERE article_id = ? ORDER BY case_type, case_action",
            (article_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def search_fts(query: str, limit: int = 50) -> list[dict[str, Any]]:
        """FTS5 全文检索 (trigram tokenizer, 中文友好).

        Args:
            query: 搜索关键词
            limit: 返回数量上限
        """
        if not query or not query.strip():
            return []
        conn = get_db()
        rows = conn.execute(
            """
            SELECT c.case_id, c.subject, c.case_type, c.case_action,
                   c.case_date, c.summary, c.amount, c.counterparty,
                   c.reference_value,
                   bm25(cases_fts) AS rank
            FROM cases_fts
            JOIN cases c ON cases_fts.case_id = c.case_id
            WHERE cases_fts MATCH ?
            ORDER BY rank LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def stats() -> dict[str, Any]:
        """案例统计数据."""
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]

        by_type = {}
        for row in conn.execute(
            "SELECT case_type, COUNT(*) FROM cases GROUP BY case_type"
        ).fetchall():
            by_type[row[0]] = row[1]

        by_action = {}
        for row in conn.execute(
            "SELECT case_action, COUNT(*) FROM cases GROUP BY case_action"
        ).fetchall():
            by_action[row[0]] = row[1]

        by_reference = {}
        for row in conn.execute(
            "SELECT reference_value, COUNT(*) FROM cases GROUP BY reference_value"
        ).fetchall():
            by_reference[row[0]] = row[1]

        pending_review = conn.execute(
            "SELECT COUNT(*) FROM cases WHERE review_status = 'pending'"
        ).fetchone()[0]

        return {
            "total": total,
            "by_type": by_type,
            "by_action": by_action,
            "by_reference_value": by_reference,
            "pending_review": pending_review,
        }