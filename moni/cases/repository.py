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

    v1.0.35.19 fix: 非日期串 (如 normalize_date 返 '2025-上半年' / '4 年底')
    走 prefix 拼接会产出非法 case_id 含中文+空格. 强制要求 YYYY-MM 前缀,
    否则回退当前年月. 加全数字白名单防 prefix 残留脏字符.
    """
    import re as _re

    if case_date and _re.match(r"^\d{4}-\d{2}", case_date):
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
        auto_verify_threshold: int = 80,
    ) -> int:
        """批量插入归一化后的案例 (同事务内调用).

        Args:
            conn: 数据库连接 (调用方管理事务)
            article_id: 关联的文章 ID
            cases: 归一化后的案例列表
            normalize_version: 当前归一化 schema 版本
            auto_verify_threshold: v1.0.35.19 agy v3 评审: extraction_confidence
                ≥ 该阈值的案例默认入库为 review_status='confirmed' +
                case_lifecycle='verified' (开箱即显真成果); 否则保持 pending/draft
                走人工复核漏斗. 阈值 = 0 关闭自动核验, =100 仅 100 分置信才放行.

        Returns:
            实际插入的行数
        """
        if not cases:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        inserted = 0

        for cs in cases:
            case_id = _generate_case_id(cs["case_type"], cs.get("case_date"))

            # v1.0.35.19: 置信度阈值分流, 自动核验高质案例 (agy v3 评审 1.1)
            conf = int(cs.get("extraction_confidence") or 0)
            if conf >= auto_verify_threshold:
                review_status = "confirmed"
                case_lifecycle = "verified"
            else:
                review_status = "pending"
                case_lifecycle = "draft"

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
                            ?, ?, ?, ?, ?, 'completed', ?, ?,
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
                        review_status, case_lifecycle,
                        now, normalize_version, now, now,
                    ),
                )
                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    inserted += 1
                    CaseRepository._insert_auto_keywords(
                        conn, case_id, cs.get("keywords") or [], now=now,
                    )
            except sqlite3.IntegrityError as e:
                logger.warning("插入案例失败 (%s): %s", case_id, e)
                continue

        return inserted

    @staticmethod
    def _insert_auto_keywords(
        conn: sqlite3.Connection,
        case_id: str,
        keywords: Sequence[Any],
        *,
        now: str | None = None,
    ) -> int:
        """同事务写入 auto_llm 关键词.

        UNIQUE(case_id, keyword) + INSERT OR IGNORE: 已有 manual_user 同行
        不会被覆盖 (source_type 保持人工). list_keywords 仍按 manual_user 优先.
        """
        if not keywords:
            return 0
        ts = now or datetime.now(timezone.utc).isoformat()
        n = 0
        for kw in keywords:
            token = str(kw).strip() if kw is not None else ""
            if not token:
                continue
            cur = conn.execute(
                """INSERT OR IGNORE INTO case_keywords
                       (case_id, keyword, source_type, weight, created_at)
                   VALUES (?, ?, 'auto_llm', 1, ?)""",
                (case_id, token, ts),
            )
            if cur.rowcount > 0:
                n += 1
        return n

    @staticmethod
    def restore_manual_keywords(
        conn: sqlite3.Connection,
        article_id: str,
        snapshot: Sequence[Any],
    ) -> int:
        """重抽后把 snapshot 里的 manual_user 贴回匹配案例.

        ON CONFLICT(case_id, keyword) 把 auto_llm 同行改回 manual_user,
        保证人工词不被覆盖.
        """
        if not snapshot:
            return 0
        ts = datetime.now(timezone.utc).isoformat()
        n = 0
        for rec in snapshot:
            subject = rec["subject"] if not isinstance(rec, tuple) else rec[0]
            case_type = rec["case_type"] if not isinstance(rec, tuple) else rec[1]
            case_action = rec["case_action"] if not isinstance(rec, tuple) else rec[2]
            keyword = rec["keyword"] if not isinstance(rec, tuple) else rec[3]
            weight = rec["weight"] if not isinstance(rec, tuple) else rec[4]
            row = conn.execute(
                """
                SELECT case_id FROM cases
                WHERE article_id = ? AND subject = ? AND case_type = ?
                  AND case_action = ? AND subject != '__processing__'
                """,
                (article_id, subject, case_type, case_action),
            ).fetchone()
            if row is None:
                continue
            case_id = row["case_id"] if not isinstance(row, tuple) else row[0]
            conn.execute(
                """
                INSERT INTO case_keywords
                    (case_id, keyword, source_type, weight, created_at)
                VALUES (?, ?, 'manual_user', ?, ?)
                ON CONFLICT(case_id, keyword) DO UPDATE SET
                    source_type = 'manual_user',
                    weight = excluded.weight
                """,
                (case_id, keyword, int(weight or 1), ts),
            )
            n += 1
        return n

    # ── 状态机 ──────────────────────────────────────────────────────

    @staticmethod
    def set_extraction_status(
        conn: sqlite3.Connection,
        article_id: str,
        status: str,
        *,
        error: str | None = None,
        attempts_increment: int = 0,
    ) -> bool:
        """更新某篇文章所有案例行的 extraction_status（同事务内调用）。

        v1.0.35.19: 从 events 范式移植。cases 侧此前无占位机制,
        no_cases 分支 UPDATE 0 行, 导致"未提取过"无法判定, 批量重复烧 LLM。
        """
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            """
            UPDATE cases
            SET extraction_status = ?,
                last_error = ?,
                extraction_attempts = extraction_attempts + ?,
                extracted_at = ?
            WHERE article_id = ?
            """,
            (status, error, attempts_increment, now, article_id),
        )
        return cur.rowcount > 0

    @staticmethod
    def mark_processing(
        conn: sqlite3.Connection,
        article_id: str,
    ) -> bool:
        """原子标记某篇文章为 processing（占位行抢占, 防重复处理）。

        v1.0.35.19: 照 events.mark_processing 范式。无案例行时插入
        subject='__processing__' 占位行, 依赖 UNIQUE(article_id, subject,
        case_type, case_action) 防并发重复抢占。真实案例插入后由
        clear_processing_placeholder 删除。
        """
        now = datetime.now(timezone.utc).isoformat()

        row = conn.execute(
            "SELECT 1 FROM cases WHERE article_id = ? LIMIT 1",
            (article_id,),
        ).fetchone()

        if row:
            status_row = conn.execute(
                "SELECT extraction_status FROM cases WHERE article_id = ? LIMIT 1",
                (article_id,),
            ).fetchone()
            if status_row and status_row["extraction_status"] == "processing":
                return False  # 正在处理中
            conn.execute(
                "UPDATE cases SET extraction_status = 'processing' WHERE article_id = ?",
                (article_id,),
            )
            return True

        # 无行 → 插入 processing 占位行
        case_id = _generate_case_id("AI", None)
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO cases
                    (case_id, article_id, case_date, case_date_precision,
                     subject, subject_raw, case_type, case_action,
                     counterparty, amount, reference_value,
                     summary, evidence_snippet, extraction_confidence,
                     extraction_status, review_status, case_lifecycle,
                     extraction_attempts, last_error, extracted_at,
                     normalize_version, created_at, updated_at)
                VALUES (?, ?, NULL, 'unknown', '__processing__', NULL, 'AI',
                        'launch', NULL, NULL, 'medium', NULL, NULL, NULL,
                        'processing', 'pending', 'draft',
                        1, NULL, ?, 1, ?, ?)
                """,
                (case_id, article_id, now, now, now),
            )
            return conn.execute("SELECT changes()").fetchone()[0] > 0
        except Exception:
            return False

    @staticmethod
    def clear_processing_placeholder(
        conn: sqlite3.Connection,
        article_id: str,
    ) -> None:
        """删除 __processing__ 占位行（成功插入真实案例后调用）。"""
        conn.execute(
            "DELETE FROM cases WHERE article_id = ? AND subject = '__processing__'",
            (article_id,),
        )

    @staticmethod
    def get_pending_articles(
        batch_size: int = 20,
        *,
        source: str | None = None,
        min_content_len: int = 100,
        after_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """获取待提取案例的文章列表（无任何 cases 行的新文章）。

        v1.0.35.19: CLI 批量提取选文入口。占位行 (skipped/processing) 也是
        cases 行, 因此已处理过 (含无案例) 的文章天然被排除, 不重复烧 LLM。

        Args:
            batch_size: 每批文章数
            source: 只选指定信源
            min_content_len: raw_content 最短长度 (过滤无正文文章)
            after_id: resume 游标 (只取 id > after_id 的文章)
        """
        conn = get_db()
        sql = """
            SELECT a.id, a.title, a.raw_content, a.source, a.published_at, a.fetched_at
            FROM articles a
            LEFT JOIN cases c ON c.article_id = a.id
            WHERE c.article_id IS NULL
              AND a.raw_content IS NOT NULL
              AND length(a.raw_content) >= ?
        """
        params: list[Any] = [min_content_len]
        if source:
            sql += " AND a.source = ?"
            params.append(source)
        if after_id:
            sql += " AND a.id > ?"
            params.append(after_id)
        sql += " ORDER BY a.fetched_at DESC LIMIT ?"
        params.append(batch_size)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

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

    # ── 编辑 / 软删除 (v1.0.35.19 agy v3 评审需求1) ────────────────

    # 允许人工编辑的字段白名单 (防任意列注入)
    EDITABLE_FIELDS = (
        "subject", "subject_raw", "case_type", "case_action", "counterparty",
        "amount", "reference_value", "reference_rationale", "summary",
        "evidence_snippet", "case_date", "case_date_precision",
    )

    @staticmethod
    def update_case(case_id: str, updates: dict[str, Any]) -> bool:
        """更新案例可编辑字段 (白名单校验).

        软删除/状态机走 update_lifecycle / confirm / reject, 不走这里.
        """
        allowed = {k: v for k, v in updates.items() if k in CaseRepository.EDITABLE_FIELDS}
        if not allowed:
            return False
        now = datetime.now(timezone.utc).isoformat()
        sets = ", ".join(f"{k} = ?" for k in allowed)
        params: list[Any] = list(allowed.values()) + [now, case_id]
        conn = get_db()
        cur = conn.execute(
            f"UPDATE cases SET {sets}, updated_at = ? WHERE case_id = ?",
            params,
        )
        conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def soft_delete_case(case_id: str) -> bool:
        """软删除: case_lifecycle draft/verified → archived (agy 评审 1.2).

        列表/看板聚合默认过滤 archived (list_cases + count_cases 加条件).
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = get_db()
        cur = conn.execute(
            """UPDATE cases SET case_lifecycle = 'archived', updated_at = ?
               WHERE case_id = ? AND case_lifecycle != 'archived'""",
            (now, case_id),
        )
        conn.commit()
        return cur.rowcount > 0

    # ── 案例关键词 (v1.0.35.19 agy v3 评审需求3, 方案 B) ────────────

    @staticmethod
    def list_keywords(case_id: str) -> list[dict[str, Any]]:
        """某案例的关键词列表 (人工优先排序)."""
        conn = get_db()
        rows = conn.execute(
            """SELECT id, keyword, source_type, weight, created_at
               FROM case_keywords WHERE case_id = ?
               ORDER BY (source_type = 'manual_user') DESC, weight DESC, id""",
            (case_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def add_keyword(case_id: str, keyword: str, *, source_type: str = "manual_user", weight: int = 1) -> int | None:
        """添加关键词 (返回新行 id; 已存在升级 source_type).

        v1.0.36 sprint-18: 改为 INSERT ... ON CONFLICT DO UPDATE, 已存在的
        auto_llm 同 keyword 升级为 manual_user (保证人工标签不被覆盖).
        旧实现 INSERT OR IGNORE 会导致 add_keyword 静默失败 (auto_llm 优先).
        """
        if not keyword or not keyword.strip():
            return None
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn = get_db()
        try:
            cur = conn.execute(
                """INSERT INTO case_keywords
                       (case_id, keyword, source_type, weight, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(case_id, keyword) DO UPDATE SET
                       source_type = excluded.source_type,
                       weight = excluded.weight""",
                (case_id, keyword.strip(), source_type, weight, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id, source_type FROM case_keywords WHERE case_id = ? AND keyword = ?",
                (case_id, keyword.strip()),
            ).fetchone()
            return row["id"] if row else None
        except Exception:
            return None

    @staticmethod
    def remove_keyword(case_id: str, keyword: str) -> bool:
        """删除关键词 (仅 manual_user 可删, 防误删 auto_llm 底层标签)."""
        conn = get_db()
        cur = conn.execute(
            """DELETE FROM case_keywords
               WHERE case_id = ? AND keyword = ? AND source_type = 'manual_user'""",
            (case_id, keyword),
        )
        conn.commit()
        return cur.rowcount > 0

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