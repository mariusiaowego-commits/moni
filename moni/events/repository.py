"""moni.events.repository — events 表 CRUD 操作。

原生 SQL 风格，与 moni.db.repository.ArticleRepository 保持一致。
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Sequence

from ..db.connection import get_db

logger = logging.getLogger(__name__)


def _make_event_id(article_id: str, event_date: str | None, subject: str, event_type: str) -> str:
    """生成 event_id = sha256(article_id + event_date + subject + event_type)[:16]"""
    raw = f"{article_id}{event_date or ''}{subject}{event_type}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class EventRepository:
    """Repository for event persistence (events table).

    v1.0.16: 行业事件日历 — 从 articles 中抽取的行业事件。
    """

    # ── 写入 ──────────────────────────────────────────────────────────

    @staticmethod
    def insert_events(
        conn: sqlite3.Connection,
        article_id: str,
        events: Sequence[dict[str, Any]],
        *,
        normalize_version: int = 1,
    ) -> int:
        """批量插入归一化后的事件（同事务内调用）。

        使用 INSERT OR IGNORE + UNIQUE 约束防重。
        调用方负责事务管理（commit / rollback）。

        Args:
            conn: 数据库连接（调用方管理事务）
            article_id: 关联的文章 ID
            events: 归一化后的事件列表（每个 dict 包含 subject, event_date 等）
            normalize_version: 归一化规则版本

        Returns:
            实际插入的行数
        """
        if not events:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        inserted = 0

        for ev in events:
            event_date = ev.get("event_date")
            subject = ev["subject"]
            event_type = ev["event_type"]
            event_id = _make_event_id(article_id, event_date, subject, event_type)

            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO events
                        (event_id, article_id, event_date, event_date_precision,
                         subject, subject_raw, event_type, summary,
                         extraction_confidence, review_status,
                         extraction_status, extraction_attempts,
                         last_error, extracted_at, evidence_snippet,
                         normalize_version, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending',
                            'completed', 1, NULL, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        article_id,
                        event_date,
                        ev.get("event_date_precision", "unknown"),
                        subject,
                        ev.get("subject_raw"),
                        event_type,
                        ev.get("summary"),
                        ev.get("extraction_confidence"),
                        now,
                        ev.get("evidence_snippet"),
                        normalize_version,
                        now,
                    ),
                )
                n = conn.execute("SELECT changes()").fetchone()[0]
                inserted += n
            except Exception as exc:
                logger.warning(
                    "Failed to insert event for article %s (subject=%s): %s",
                    article_id, subject, exc,
                )
                raise

        return inserted

    @staticmethod
    def set_extraction_status(
        conn: sqlite3.Connection,
        article_id: str,
        status: str,
        *,
        error: str | None = None,
        attempts_increment: int = 0,
    ) -> bool:
        """更新某篇文章所有事件行的 extraction_status（同事务内调用）。

        注意：实际只更新 extraction_attempts>0 的行（即已经被抽取过的）。
        对于完全没有事件的文章，不会有 events 行存在，此时返回 False。
        调用方负责 commit。

        Args:
            conn: 数据库连接
            article_id: 文章 ID
            status: 新状态（pending/processing/completed/failed/skipped/normalize_failed）
            error: 错误信息（可选）
            attempts_increment: 尝试次数增量

        Returns:
            是否更新了至少一行
        """
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            """
            UPDATE events
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
        """原子标记某篇文章为 processing（用于调度器抢占）。

        通过 INSERT 一条 extraction_status='processing' 的占位行 + UNIQUE 约束
        来实现"首次处理"的原子抢占。如果文章已有 events 行，则 UPDATE 之。

        简化实现：直接插入一条 pending 行再 UPDATE，或者用单独的 tracking 表。
        这里用 events 表自己的状态：对已有的 events 行直接 UPDATE；
        没有则 INSERT 一条占位（subject='__pending__'，后续会被覆盖）。

        返回 True 表示抢占成功（可以处理），False 表示已有其他 worker 在处理。
        """
        now = datetime.now(timezone.utc).isoformat()

        # 先看有没有 events 行
        row = conn.execute(
            "SELECT 1 FROM events WHERE article_id = ? LIMIT 1",
            (article_id,),
        ).fetchone()

        if row:
            # 已有行 → 检查是否正在处理
            status_row = conn.execute(
                "SELECT extraction_status FROM events WHERE article_id = ? LIMIT 1",
                (article_id,),
            ).fetchone()
            if status_row and status_row["extraction_status"] == "processing":
                return False  # 正在处理中，跳过
            conn.execute(
                "UPDATE events SET extraction_status = 'processing' WHERE article_id = ?",
                (article_id,),
            )
            return True

        # 没有行 → 插入一条 processing 占位行（抢占）
        # event_id 用 article_id + processing 标记
        event_id = _make_event_id(article_id, None, "__processing__", "other")
        try:
            conn.execute(
                """
                INSERT INTO events
                    (event_id, article_id, event_date, event_date_precision,
                     subject, subject_raw, event_type, summary,
                     extraction_confidence, review_status,
                     extraction_status, extraction_attempts,
                     last_error, extracted_at, evidence_snippet,
                     normalize_version, created_at)
                VALUES (?, ?, NULL, 'unknown', '__processing__', NULL, 'other',
                        NULL, NULL, 'pending', 'processing', 1, NULL, ?, NULL, 0, ?)
                """,
                (event_id, article_id, now, now),
            )
            return True
        except Exception:
            # UNIQUE 冲突 → 已经被其他 worker 抢占
            return False

    @staticmethod
    def clear_processing_placeholder(
        conn: sqlite3.Connection,
        article_id: str,
    ) -> None:
        """删除 __processing__ 占位行（在成功插入真实事件后调用）。

        如果真实事件插入成功，占位行就不再需要了。
        """
        conn.execute(
            "DELETE FROM events WHERE article_id = ? AND subject = '__processing__'",
            (article_id,),
        )

    # ── 查询 ──────────────────────────────────────────────────────────

    @staticmethod
    def list_events(
        *,
        year: int | None = None,
        month: int | None = None,
        event_type: str | None = None,
        subject: str | None = None,
        review_status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """按条件筛选事件列表（带分页）。

        过滤逻辑：
        - year/month：基于 event_date 前缀匹配（支持 exact/month/quarter 精度）
        - event_type：精确匹配
        - subject：LIKE 模糊匹配
        - review_status：精确匹配
        """
        conn = get_db()
        where_clauses: list[str] = []
        params: list[Any] = []

        # 排除占位行
        where_clauses.append("subject != '__processing__'")

        if year is not None:
            if month is not None:
                prefix = f"{year:04d}-{month:02d}"
                where_clauses.append("event_date LIKE ?")
                params.append(f"{prefix}%")
            else:
                where_clauses.append("event_date LIKE ?")
                params.append(f"{year:04d}%")

        if event_type:
            where_clauses.append("event_type = ?")
            params.append(event_type)

        if subject:
            where_clauses.append("subject LIKE ?")
            params.append(f"%{subject}%")

        if review_status:
            where_clauses.append("review_status = ?")
            params.append(review_status)

        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        sql = f"""
            SELECT e.*, a.title AS article_title, a.source AS article_source,
                   a.url AS article_url, a.published_at AS article_published_at
            FROM events e
            LEFT JOIN articles a ON a.id = e.article_id
            {where_sql}
            ORDER BY e.event_date DESC, e.created_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def count_events(
        *,
        year: int | None = None,
        month: int | None = None,
        event_type: str | None = None,
        subject: str | None = None,
        review_status: str | None = None,
    ) -> int:
        """统计符合条件的事件总数（用于分页）。"""
        conn = get_db()
        where_clauses: list[str] = []
        params: list[Any] = []

        where_clauses.append("subject != '__processing__'")

        if year is not None:
            if month is not None:
                prefix = f"{year:04d}-{month:02d}"
                where_clauses.append("event_date LIKE ?")
                params.append(f"{prefix}%")
            else:
                where_clauses.append("event_date LIKE ?")
                params.append(f"{year:04d}%")

        if event_type:
            where_clauses.append("event_type = ?")
            params.append(event_type)

        if subject:
            where_clauses.append("subject LIKE ?")
            params.append(f"%{subject}%")

        if review_status:
            where_clauses.append("review_status = ?")
            params.append(review_status)

        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM events {where_sql}", params
        ).fetchone()
        return row["cnt"] if row else 0

    @staticmethod
    def get_by_article(article_id: str) -> list[dict[str, Any]]:
        """获取某篇文章的所有事件（排除占位行）。"""
        conn = get_db()
        rows = conn.execute(
            """
            SELECT * FROM events
            WHERE article_id = ? AND subject != '__processing__'
            ORDER BY event_date DESC, created_at DESC
            """,
            (article_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def list_pending_review(
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """获取 date_precision 为 relative/unknown 的待人工确认事件。"""
        conn = get_db()
        rows = conn.execute(
            """
            SELECT e.*, a.title AS article_title, a.source AS article_source,
                   a.url AS article_url
            FROM events e
            LEFT JOIN articles a ON a.id = e.article_id
            WHERE e.event_date_precision IN ('relative', 'unknown')
              AND e.subject != '__processing__'
              AND e.review_status = 'pending'
            ORDER BY e.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def stats() -> dict[str, Any]:
        """事件统计数据。

        返回：
        - total: 总事件数
        - by_type: {type: count}
        - by_month: [{month, count}] 最近 12 个月
        - pending_review: 待审核数
        """
        conn = get_db()
        total_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM events WHERE subject != '__processing__'"
        ).fetchone()
        total = total_row["cnt"] if total_row else 0

        type_rows = conn.execute(
            """
            SELECT event_type, COUNT(*) AS cnt
            FROM events
            WHERE subject != '__processing__'
            GROUP BY event_type
            ORDER BY cnt DESC
            """
        ).fetchall()
        by_type = {r["event_type"]: r["cnt"] for r in type_rows}

        month_rows = conn.execute(
            """
            SELECT SUBSTR(event_date, 1, 7) AS month, COUNT(*) AS cnt
            FROM events
            WHERE subject != '__processing__'
              AND event_date IS NOT NULL
              AND event_date NOT IN ('relative')
              AND length(event_date) >= 7
            GROUP BY month
            ORDER BY month DESC
            LIMIT 12
            """
        ).fetchall()
        by_month = [
            {"month": r["month"], "count": r["cnt"]}
            for r in reversed(month_rows)
        ]

        pending_row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM events
            WHERE subject != '__processing__'
              AND review_status = 'pending'
            """
        ).fetchone()
        pending_review = pending_row["cnt"] if pending_row else 0

        return {
            "total": total,
            "by_type": by_type,
            "by_month": by_month,
            "pending_review": pending_review,
        }

    # ── 调度用 ────────────────────────────────────────────────────────

    @staticmethod
    def get_pending_articles(batch_size: int = 20) -> list[dict[str, Any]]:
        """获取待提取事件的文章列表（没有 events 记录的新文章）。

        按 fetched_at DESC 排序，取最新的 batch_size 篇。
        调度器用这个来确定每批处理哪些文章。
        """
        conn = get_db()
        rows = conn.execute(
            """
            SELECT a.id, a.title, a.raw_content, a.source, a.published_at, a.fetched_at
            FROM articles a
            LEFT JOIN events e ON e.article_id = a.id
            WHERE e.article_id IS NULL
              AND a.raw_content IS NOT NULL
              AND a.raw_content != ''
            ORDER BY a.fetched_at DESC
            LIMIT ?
            """,
            (batch_size,),
        ).fetchall()
        return [dict(r) for r in rows]
