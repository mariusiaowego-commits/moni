"""routes/author_blocks — X-bot 精选作者屏蔽名单 (sprint-38, v1.0.88)。

三个**独立** REST 端点 (dad GO IMPL 2026-11-12 15:04)::

    POST   /api/config/sources/{source_id}/blocked-authors
    DELETE /api/config/sources/{source_id}/blocked-authors/{handle}
    GET    /api/config/sources/{source_id}/blocked-authors

为什么不走 ``PUT /{source_id}/fetch_config``:

* 名单是 dad 的**个人运营资产**, 而 ``fetch_config`` 是**信源配置**。
  两者放一起 → 改门槛时可能把名单覆盖 (agy §3.2 的一致性顾虑)。
* ``fetch_config`` 会被 ``moni db seed-config`` **整行覆盖**
  (``seed_config.py:507-508`` 的 ``COALESCE`` + excluded 恒非 null) ——
  名单放进去就会丢。独立表 ``source_author_blocks`` 免疫 (AC-3)。

屏蔽 = **写名单 + 物理删除该作者的已有文章**, 是**同一个动作** (AC-6):

* 删除范围精确: 只删 ``source='xbot_feed'`` 且 publisher 对应该 handle 的文章;
* 7 张 FK 引用表靠 ``ON DELETE CASCADE`` 自动清理 (实测生效);
* ``share_tasks.article_ids`` 是 JSON 数组, **没有 FK** → 必须显式清理;
* ``articles_fts`` 是 external-content FTS5, ``articles`` 上**没有 trigger**
  → 删行不会同步索引 → 显式执行 FTS5 的 ``'delete'`` 命令。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Request

from moni.db.connection import get_db
from moni.fetch.sources.xbot_feed import DEFAULT_BLACKLISTED_HANDLES, normalize_handle
from moni.web.exceptions import BadRequestError, NotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config/sources", tags=["config"])

#: 本功能只对 xbot_feed 生效 (AC-7)。
#:
#: handle 是 X 的专属概念 —— 其它信源的 ``articles.publisher`` 是公众号名 /
#: 作者名, **没有 handle**。全局化会误伤。
_XBOT_SOURCE_NAME = "xbot_feed"

#: publisher 的写入格式是 ``"{name} (@{handle})"`` (xbot_feed.py:1018-1020),
#: 所以按 handle 反解用这个正则。200/200 的生产 xbot 文章都能反解出来。
_PUBLISHER_HANDLE_RE = r"@([^)]+)\)"


def _normalize_handle(raw: object) -> str:
    """归一 handle —— 委托给 **全仓唯一实现** :func:`normalize_handle`。

    写入侧 (这里) 与匹配侧 (``xbot_feed.py`` 的 ``_normalize_handles``)
    必须走同一套归一, 否则 dad 手输 ``@JiaMeXyz`` 与 ``jiamemexyz``
    会变成两条记录 (AC-5), 更糟的是「名单里有但屏蔽失效」的静默失效
    (warden 审计 P2-3)。
    """
    return normalize_handle(raw)


def _resolve_source(conn: sqlite3.Connection, source_id: str) -> str:
    """把 ``sources.id`` 解析成 ``sources.name``, 并确认是 xbot_feed。

    表里存的是 ``source_name`` 而不是 ``sources.id`` (warden AC-15):
    fetcher 只有类属性 ``source_name``, 没有 ``self.source_id``;
    存 name 则 fetcher 侧零解析、零额外查询。
    """
    row = conn.execute(
        "SELECT name FROM sources WHERE id = ?", (source_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Source {source_id!r} not found")
    source_name = str(row["name"])
    if source_name != _XBOT_SOURCE_NAME:
        raise BadRequestError(
            message=(
                f"作者屏蔽只支持 {_XBOT_SOURCE_NAME!r} (handle 是 X 的专属概念, "
                f"其它信源的 publisher 没有 handle)"
            ),
            details={"source_id": source_id, "source_name": source_name},
        )
    return source_name


def _article_ids_for_handle(conn: sqlite3.Connection, handle: str) -> list[str]:
    """找出该 handle 在 xbot_feed 下的文章 id (按 publisher 反解, 精确匹配)。

    反解用正则取出 ``(@handle)`` 里那段, 再与目标 handle **精确等值**比较 ——
    不能用 ``LIKE '%handle%'`` (会把 ``jiamemexyz2`` 也捞进来, AC-6.1)。
    """
    import re

    pattern = re.compile(_PUBLISHER_HANDLE_RE)
    rows = conn.execute(
        "SELECT id, publisher FROM articles WHERE source = ?",
        (_XBOT_SOURCE_NAME,),
    ).fetchall()
    out: list[str] = []
    for row in rows:
        publisher = row["publisher"] or ""
        m = pattern.search(publisher)
        if not m:
            continue
        if _normalize_handle(m.group(1)) == handle:
            out.append(str(row["id"]))
    return out


def _purge_share_task_refs(conn: sqlite3.Connection, article_ids: list[str]) -> int:
    """从 ``share_tasks.article_ids`` (JSON 数组) 里剔除已删 id。

    这一列**没有 FK** (``schema.sql:65``, 是 TEXT 存 JSON 数组) →
    ``ON DELETE CASCADE`` 管不到 → 必须显式清理, 否则留下悬空引用 (AC-6.5)。

    **处置口径: 剔除已删 id, 保留该 share_task 行** (warden 建议)。
    理由: 生产库 111 行 share_tasks **全部是终态**
    (done 93 / failed 10 / cancelled 8, 无 pending/running) —— 它们是
    **历史记录** (产物路径 / 失败原因), 删整行等于抹掉 dad 的操作历史。
    剔除后数组变空也保留该行: 下游对空数组是安全的
    (``enrichment.py:273`` ``if not article_ids: return 0``)。

    同一数组里**其它** id 保留 —— 只剔除被删的那些。
    """
    if not article_ids:
        return 0
    target = set(article_ids)
    rows = conn.execute("SELECT id, article_ids FROM share_tasks").fetchall()
    changed = 0
    for row in rows:
        raw = row["article_ids"]
        try:
            ids = json.loads(raw) if isinstance(raw, str) else list(raw or [])
        except (TypeError, ValueError):
            continue
        if not isinstance(ids, list):
            continue
        kept = [i for i in ids if str(i) not in target]
        if len(kept) == len(ids):
            continue
        changed += 1
        # 只剔除已删 id —— **保留整行** (历史记录不抹掉)。
        conn.execute(
            "UPDATE share_tasks SET article_ids = ? WHERE id = ?",
            (json.dumps(kept, ensure_ascii=False), row["id"]),
        )
    return changed


def _purge_fts_index(
    conn: sqlite3.Connection,
    article_ids: list[str],
    fts_rows: list[tuple[Any, Any, Any, Any, Any]] | None = None,
) -> int:
    """清掉 ``articles_fts`` 索引里已删文章的条目 (AC-6.6)。

    ``articles_fts`` 是 external-content FTS5 (``content='articles'``,
    ``content_rowid='rowid'``), 而 ``articles`` 表上**没有 trigger**
    (``schema.sql:34`` 的注释与实际不符 —— 生产库只有 ``cases_fts`` 的
    3 个 trigger) → 删 articles 行**不会**自动同步索引。

    **处置口径: 逐条发 FTS5 的 ``'delete'`` 命令** (warden 建议)。
    external-content 表的标准同步方式就是 ``'delete'`` —— 它**增量**,
    只动被删的那些条目, 不会重建全表 (实测全表 ``'rebuild'`` 在
    5080 行上要 ~520 ms, 每次屏蔽都付这个代价不划算)。

    FTS5 的 ``'delete'`` 需要提供该行的**旧值** (title / publisher /
    raw_content / summary) —— external-content 表不存原文, 索引里只有
    分词结果, 拿不到原值就没法精确删除。所以调用方必须在删 articles 行
    **之前**把旧值查出来 (见 ``_fts_rows_for``)。

    注: ``articles_fts`` 目前**没有业务代码查询** (全仓只有 schema.sql
    引用), 但仍要处理 —— 免得将来有人给它接上查询时踩到脏索引。
    """
    if not article_ids:
        return 0
    rows = fts_rows if fts_rows is not None else _fts_rows_for(conn, article_ids)
    if not rows:
        return 0
    try:
        for rowid, title, publisher, raw_content, summary in rows:
            conn.execute(
                "INSERT INTO articles_fts(articles_fts, rowid, title, publisher, "
                "raw_content, summary) VALUES('delete', ?,?,?,?,?)",
                (rowid, title, publisher, raw_content, summary),
            )
    except sqlite3.Error as exc:  # noqa: BLE001
        logger.warning("[author-blocks] articles_fts delete failed: %s", exc)
        return 0
    return len(rows)


def _fts_rows_for(
    conn: sqlite3.Connection, article_ids: list[str]
) -> list[tuple[Any, Any, Any, Any, Any]]:
    """取待删文章的 FTS 旧值 (rowid + 四个索引列)。

    必须在**删 articles 行之前**调用 —— 行删了就再也取不到原值了,
    FTS5 的 ``'delete'`` 命令也就不准了。
    """
    placeholders = ",".join("?" * len(article_ids))
    try:
        rows = conn.execute(
            f"SELECT rowid, title, publisher, raw_content, summary "
            f"FROM articles WHERE id IN ({placeholders})",
            article_ids,
        ).fetchall()
    except sqlite3.Error as exc:  # noqa: BLE001
        logger.warning("[author-blocks] cannot read fts source rows: %s", exc)
        return []
    return [
        (r["rowid"], r["title"], r["publisher"], r["raw_content"], r["summary"])
        for r in rows
    ]


def _delete_articles_for_handle(
    conn: sqlite3.Connection, handle: str
) -> tuple[list[str], int]:
    """物理删除该 handle 在 xbot_feed 下的文章 + 清理全部关联数据。

    返回 ``(被删 id 列表, share_task 被清理的行数)``。
    """
    article_ids = _article_ids_for_handle(conn, handle)
    if not article_ids:
        return [], 0

    # 1) 先清 share_tasks 的 JSON 引用 (无 FK, 必须在删行**之前**做 ——
    #    删完就再也认不出哪些 share_task 引用了这些 id 了)。
    share_tasks_fixed = _purge_share_task_refs(conn, article_ids)

    # 2) **删行之前**取 FTS 旧值 —— external-content 表的 'delete' 命令需要
    #    原值 (title/publisher/raw_content/summary), 行删了就取不到了。
    fts_rows = _fts_rows_for(conn, article_ids)

    # 3) 删 articles 行。7 张引用表 (article_tags / article_pins /
    #    article_keyword_hits / cases / events / fact_check_evidence /
    #    llm_jobs) 全是 ON DELETE CASCADE → 自动清理, 不留孤儿行。
    #    PRAGMA foreign_keys 是 per-connection 的
    #    (moni/db/connection.py:121) → 走 get_db() 的连接才有效。
    placeholders = ",".join("?" * len(article_ids))
    cursor = conn.execute(
        f"DELETE FROM articles WHERE id IN ({placeholders})", article_ids
    )
    deleted = cursor.rowcount

    # 4) 清 FTS 索引 (external-content 表, 无 trigger → 不自动同步)。
    _purge_fts_index(conn, article_ids, fts_rows=fts_rows)

    return article_ids, share_tasks_fixed


# ── 端点 ────────────────────────────────────────────────────────────────


@router.get("/{source_id}/blocked-authors")
def list_blocked_authors(source_id: str, request: Request) -> dict[str, Any]:
    """列出该源的作者屏蔽名单 (AC-2)。

    返回 ``builtin_handles`` (内置只读黑名单) + ``blocked_authors``
    (dad 自己屏蔽的, 可撤销)。

    带 ``?handle=xxx`` 查询参数时, 额外返回该 handle 当前**已有多少篇**
    xbot 文章 (``existing_count``) —— 详情弹窗的确认框要显示
    「将删除 N 篇已有文章, 不可恢复」(AC-17)。这个数必须来自**库里的全量**
    查询, 不能用前端当前页的文章数组去数 (那只有一页, 会少算)。
    """
    conn = get_db()
    source_name = _resolve_source(conn, source_id)
    rows = conn.execute(
        "SELECT handle, display_name, blocked_at, notes FROM source_author_blocks "
        "WHERE source_name = ? ORDER BY handle",
        (source_name,),
    ).fetchall()
    payload: dict[str, Any] = {
        "source_id": source_id,
        "source_name": source_name,
        "builtin_handles": list(DEFAULT_BLACKLISTED_HANDLES),
        "blocked_authors": [
            {
                "handle": row["handle"],
                "display_name": row["display_name"],
                "blocked_at": row["blocked_at"],
                "notes": row["notes"],
            }
            for row in rows
        ],
    }
    wanted = request.query_params.get("handle")
    if wanted:
        handle = _normalize_handle(wanted)
        payload["handle"] = handle
        payload["existing_count"] = (
            len(_article_ids_for_handle(conn, handle)) if handle else 0
        )
    return payload


@router.post("/{source_id}/blocked-authors")
def block_author(source_id: str, body: dict) -> dict[str, Any]:
    """屏蔽一个作者 = **写名单 + 物理删除该作者的已有文章** (同一个动作)。

    幂等: 重复 POST 同一 handle 不报错、不产生重复行。
    """
    conn = get_db()
    source_name = _resolve_source(conn, source_id)

    handle = _normalize_handle(body.get("handle"))
    if not handle:
        raise BadRequestError(
            message="handle must not be empty",
            details={"source_id": source_id},
        )

    display_name = body.get("display_name")
    notes = body.get("notes")
    display_name = str(display_name) if display_name else None
    notes = str(notes) if notes else None

    # 1) 先删已有文章 (dad 的决定: 屏蔽 = 旧文物理删除, 不可恢复)。
    article_ids, share_tasks_fixed = _delete_articles_for_handle(conn, handle)

    # 2) 再写名单 (幂等 upsert)。
    conn.execute(
        "INSERT INTO source_author_blocks "
        "(source_name, handle, display_name, blocked_at, notes) "
        "VALUES (?,?,?,datetime('now'),?) "
        "ON CONFLICT(source_name, handle) DO UPDATE SET "
        "display_name = COALESCE(excluded.display_name, display_name), "
        "notes = COALESCE(excluded.notes, notes)",
        (source_name, handle, display_name, notes),
    )
    conn.commit()

    logger.info(
        "[author-blocks] blocked @%s on %s — deleted %d article(s), "
        "cleaned %d share_task row(s)",
        handle, source_name, len(article_ids), share_tasks_fixed,
    )

    return {
        "status": "ok",
        "handle": handle,
        "blocked_at": datetime_now_iso(),
        "deleted_articles": len(article_ids),
        "deleted_article_ids": article_ids,
    }


@router.delete("/{source_id}/blocked-authors/{handle}")
def unblock_author(source_id: str, handle: str) -> dict[str, Any]:
    """解除屏蔽 (幂等: 删不存在的 handle 也成功)。

    **只影响以后的入库** —— 之前被物理删除的旧文章**不会恢复** (AC-20)。
    """
    conn = get_db()
    source_name = _resolve_source(conn, source_id)
    normalized = _normalize_handle(handle)
    if not normalized:
        raise BadRequestError(
            message="handle must not be empty",
            details={"source_id": source_id},
        )
    cursor = conn.execute(
        "DELETE FROM source_author_blocks WHERE source_name = ? AND handle = ?",
        (source_name, normalized),
    )
    conn.commit()
    return {"status": "ok", "handle": normalized, "removed": cursor.rowcount}


def datetime_now_iso() -> str:
    """当前 UTC 时间 (ISO8601)。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
