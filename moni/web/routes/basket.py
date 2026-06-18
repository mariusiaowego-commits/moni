"""routes/basket — 菜篮子 CRUD (v1.0.6.9).

被过滤丢弃的文章暂存区，支持查看、rescue 回 articles、删除。
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from moni.db.connection import get_db
from moni.web.exceptions import NotFoundError
from moni.web.models import BasketItem, BasketListResponse, BasketRescueRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/basket", tags=["basket"])


def _row_to_basket(row: dict) -> BasketItem:
    return BasketItem(
        id=row["id"],
        title=row["title"],
        url=row["url"],
        source=row["source"],
        publisher=row.get("publisher"),
        published_at=row.get("published_at"),
        fetched_at=row["fetched_at"],
        raw_content=row.get("raw_content"),
        dropped_reason=row["dropped_reason"],
        dropped_by=row.get("dropped_by"),
        rescued_at=row.get("rescued_at"),
        created_at=row["created_at"],
    )


@router.get("", response_model=BasketListResponse)
def list_basket(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    source: str | None = None,
    reason: str | None = None,
    rescued: int | None = None,  # 0=未 rescue, 1=已 rescue, None=全部
) -> BasketListResponse:
    """获取菜篮子列表 (分页 + 筛选)."""
    conn = get_db()
    where_clauses = []
    params: list = []

    if source:
        where_clauses.append("source = ?")
        params.append(source)
    if reason:
        where_clauses.append("dropped_reason = ?")
        params.append(reason)
    if rescued is not None:
        if rescued == 0:
            where_clauses.append("rescued_at IS NULL")
        else:
            where_clauses.append("rescued_at IS NOT NULL")

    where = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # total count
    total = conn.execute(
        f"SELECT COUNT(*) FROM basket{where}", params
    ).fetchone()[0]

    # paginated query
    offset = (page - 1) * page_size
    rows = conn.execute(
        f"SELECT * FROM basket{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [page_size, offset],
    ).fetchall()

    return BasketListResponse(
        items=[_row_to_basket(dict(r)) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{basket_id}", response_model=BasketItem)
def get_basket_item(basket_id: str) -> BasketItem:
    """获取单条 basket 详情."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM basket WHERE id = ?", (basket_id,)
    ).fetchone()
    if not row:
        raise NotFoundError(f"basket item {basket_id} not found")
    return _row_to_basket(dict(row))


@router.post("/{basket_id}/rescue")
def rescue_basket_item(basket_id: str, body: BasketRescueRequest) -> dict:
    """将 basket 文章 rescue 到 articles 表.

    流程:
    1. 读 basket 记录
    2. INSERT INTO articles (用 basket 的字段)
    3. INSERT INTO article_tags (用户选择的 tag)
    4. UPDATE basket SET rescued_at = now
    """
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM basket WHERE id = ?", (basket_id,)
    ).fetchone()
    if not row:
        raise NotFoundError(f"basket item {basket_id} not found")

    basket = dict(row)

    # 如果已 rescue，返回提示
    if basket.get("rescued_at"):
        return {
            "ok": False,
            "message": "该文章已被 rescue 过",
            "article_id": None,
        }

    now = datetime.now(timezone.utc).isoformat()

    # 生成 article id (同 repository.py 的逻辑)
    raw_id = f"{basket['source']}{basket['url']}{basket['title']}"
    article_id = hashlib.sha256(raw_id.encode()).hexdigest()[:16]

    # INSERT INTO articles (INSERT OR IGNORE 防冲突)
    try:
        conn.execute(
            """INSERT OR IGNORE INTO articles
               (id, title, url, source, publisher, published_at,
                fetched_at, raw_content)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                article_id,
                basket["title"],
                basket["url"],
                basket["source"],
                basket.get("publisher"),
                basket.get("published_at"),
                now,
                basket.get("raw_content"),
            ),
        )

        # 检查是否真的插入了 (如果已存在会被 IGNORE)
        cursor = conn.execute("SELECT changes()")
        inserted = cursor.fetchone()[0] > 0

        if not inserted:
            # article 已存在，更新 rescued_at 后返回
            conn.execute(
                "UPDATE basket SET rescued_at = ? WHERE id = ?",
                (now, basket_id),
            )
            conn.commit()
            return {
                "ok": True,
                "message": "文章已存在于 articles 表，已标记为 rescued",
                "article_id": article_id,
            }

        # 插入 article_tags
        for tag_name in body.tags:
            conn.execute(
                """INSERT OR IGNORE INTO article_tags
                   (article_id, tag_name, predicted, created_at)
                   VALUES (?, ?, 0, ?)""",
                (article_id, tag_name, now),
            )

        # 更新 basket.rescued_at
        conn.execute(
            "UPDATE basket SET rescued_at = ? WHERE id = ?",
            (now, basket_id),
        )
        conn.commit()

        return {
            "ok": True,
            "message": f"已 rescue 到 articles，关联 {len(body.tags)} 个 tag",
            "article_id": article_id,
        }
    except Exception as exc:
        conn.rollback()
        logger.error("[basket] rescue failed: %s", exc)
        return {"ok": False, "message": f"rescue 失败: {exc}", "article_id": None}


@router.delete("/{basket_id}")
def delete_basket_item(basket_id: str) -> dict:
    """从菜篮子删除."""
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM basket WHERE id = ?", (basket_id,)
    ).fetchone()
    if not row:
        raise NotFoundError(f"basket item {basket_id} not found")

    conn.execute("DELETE FROM basket WHERE id = ?", (basket_id,))
    conn.commit()
    return {"ok": True, "message": "已删除"}
