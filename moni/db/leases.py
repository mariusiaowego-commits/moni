"""moni.db.leases — 统一任务租约运行时 (v1.0.42, sprint-22).

解决 moni 三条任务状态机 (fetch_jobs / llm_jobs / share_tasks) 的同型问题:

1. **判死看墙钟不看心跳** — 旧 sweep_zombies 拿 ``started_at`` 判定,
   但 started_at 在 job 跑完后才更新,根本反映不出"卡死/进程被 kill"等
   真僵尸场景。v1.0.35.17 的 ``get_stale_threshold()=max(timeout)+300``
   把阈值追着最慢源跑,补丁打症状不打根因。

2. **判死后原线程还活着** — sweep 标记 'stale' 后,后台线程若没真死,
   接着调 ``mark_done()`` 无条件覆盖 status,产生"假完成"。UI 状态机
   脏读闪烁就是这个机制。

3. **多写者无 fencing** — 双 worker 抢同一行:SELECT→UPDATE 中间有
   TOCTOU 窗口,两个 worker 各发一次 LLM 调,双计费。

设计: 单一原语集 ``acquire / heartbeat / finish / sweep_expired`` 同时
覆盖 fetch_jobs 和 llm_jobs 两表;任何状态写都必须走 finish(fenced),
迟到写者天然被 fencing 挡在门外(rowcount=0)。

时间戳全部用 SQLite 端 ``strftime('%Y-%m-%dT%H:%M:%fZ','now')`` 生成,
跨进程 (8900/8901/CLI) 钟差免疫。

Thread-local connection: 复用 ``moni.db.connection.get_db()`` 的
per-thread 连接(v1.0.37.5 已上线),无额外锁开销。
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any

from .connection import get_db

logger = logging.getLogger(__name__)


# ── 表白名单 (防 f-string SQL 注入) ─────────────────────────────────────────
# 所有 lease 操作的 SQL 都从这张白名单里取表名;新表 = 显式加进来。
_LEASE_TABLES: frozenset[str] = frozenset({"fetch_jobs", "llm_jobs"})

# 默认心跳间隔 / grace。callers 可在 acquire 时覆盖(用于不同任务的
# 调优,但生产通常都用这套)。
DEFAULT_HEARTBEAT_INTERVAL_SEC: int = 30
DEFAULT_GRACE_SEC: int = 90  # = 3 × heartbeat

# ── 数据类 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LeaseHandle:
    """持租者认领句柄 — 后续 finish/heartbeat 必须用同一 owner+fence。"""

    job_id: str
    owner: str
    fence: int
    table: str = ""

    def __str__(self) -> str:
        return f"LeaseHandle(table={self.table or '?'} job={self.job_id[:8]} owner={self.owner} fence={self.fence})"


# ── 原语 1: acquire (原子认领 + 起心跳 + fence 单调递增) ──────────────────────


def acquire(
    table: str,
    *,
    owner: str | None = None,
    heartbeat_interval_sec: int = DEFAULT_HEARTBEAT_INTERVAL_SEC,
    grace_sec: int = DEFAULT_GRACE_SEC,
) -> LeaseHandle | None:
    """认领一张表中任意 status='running' 但心跳已过期的行。

    原子操作: 单语句 UPDATE 同时:
      1. 挑出 heartbeat < now-grace 的 running 行(僵尸),或任意
         status='pending' 行(从未认领),或 status='running' 但心跳新鲜的
         并发活行(失败回 0 行,下次再试)
      2. 写入 owner + heartbeat = now + fence+1
      3. RETURNING id + 新 fence — 调用方拿这个 fence 做后续 finish

    Returns:
        LeaseHandle(持租成功) 或 None (无可认领行)。

    Note:
        * owner 默认 = uuid4().hex,跨进程唯一,避免认领冲突。
        * heartbeat_interval 与 grace_sec 存进 handle 元数据,finish 时
          可核对(但目前只看 SQL 端的 strftime('now'),所以这俩参数主要
          给 sweep_expired 用)。
    """
    if table not in _LEASE_TABLES:
        raise ValueError(f"table {table!r} not in lease whitelist")
    owner = owner or uuid.uuid4().hex
    conn = get_db()
    grace_sql = f"-{grace_sec} seconds"
    row = conn.execute(
        f"""
        UPDATE {table}
        SET status = 'running',
            lease_owner = ?,
            lease_heartbeat_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
            fence = fence + 1,
            started_at = COALESCE(started_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        WHERE id = (
            SELECT id FROM {table}
            WHERE (status = 'pending')
               OR (status = 'running' AND (
                    lease_heartbeat_at IS NULL
                    OR lease_heartbeat_at < strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)
               ))
            ORDER BY started_at ASC
            LIMIT 1
        )
        RETURNING id, fence
        """,
        (owner, grace_sql),
    ).fetchone()
    conn.commit()
    if row is None:
        return None
    return LeaseHandle(job_id=row["id"], owner=owner, fence=int(row["fence"]), table=table)


# ── 原语 2: heartbeat (续约) ────────────────────────────────────────────────


def heartbeat(handle: LeaseHandle) -> bool:
    """续约 — fence + owner 校验必须与 acquire 时一致。

    Returns:
        True = 续约成功 (rowcount==1)。
        False = fence 失配或 owner 失配 (rowcount==0),持租者已被取代或
               sweep 了;调用方应停止工作并写一条错日志(不抢 fence)。
    """
    table = _table(handle)
    conn = get_db()
    cur = conn.execute(
        f"""
        UPDATE {table}
        SET lease_heartbeat_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id = ? AND lease_owner = ? AND fence = ? AND status = 'running'
        """,
        (handle.job_id, handle.owner, handle.fence),
    )
    conn.commit()
    return cur.rowcount > 0


# ── 原语 3: finish (fenced 终态写) ──────────────────────────────────────────


def finish(
    handle: LeaseHandle,
    *,
    status: str,
    extra_fields: dict[str, Any] | None = None,
) -> bool:
    """终态写 — fence 失配直接拒绝(rowcount=0)。

    Args:
        handle: acquire 返回的句柄(owner+fence 必须一致)
        status: 'done' | 'failed' | 'timeout' | 'stale' | 'cancelled'
        extra_fields: 其它要一并写的列,例如 error / finished_at / result_json

    Returns:
        True = 写成功。
        False = fence 失配(rowcount=0) — **这是正常的迟到拒绝**,不要 raise。
    """
    _check_table(handle)
    try:
        table = _table(handle)
    except ValueError:
        return False
    fields = extra_fields or {}
    sets: list[str] = [
        "status = ?",
        "lease_heartbeat_at = NULL",
        "lease_owner = NULL",
    ]
    params: list[Any] = [status]
    for col, val in fields.items():
        sets.append(f"{col} = ?")
        params.append(val)
    params.extend([handle.job_id, handle.owner, handle.fence])
    sql = (
        f"UPDATE {table} SET {', '.join(sets)} "
        "WHERE id = ? AND lease_owner = ? AND fence = ?"
    )
    conn = get_db()
    cur = conn.execute(sql, params)
    conn.commit()
    ok = cur.rowcount > 0
    if not ok:
        logger.info(
            "[leases] finish rejected: table=%s job=%s owner=%s fence=%s → status=%s",
            table, handle.job_id[:8], handle.owner[:8], handle.fence, status,
        )
    return ok


# ── 原语 4: sweep_expired (心跳制 zombie reap) ──────────────────────────────


def sweep_expired(
    table: str,
    *,
    grace_sec: int = DEFAULT_GRACE_SEC,
) -> int:
    """回收 heartbeat < now-grace 的 running 行 → status='stale'。

    与旧 ``sweep_zombies`` 的区别:不再看 ``started_at``,只看心跳。
    阈值与 timeout 预算解耦(60s 心跳间隔配 180s grace,即便 bestblogs
    timeout_sec=1800 也不会被误判)。

    Returns:
        被 sweep 的行数。
    """
    if table not in _LEASE_TABLES:
        raise ValueError(f"table {table!r} not in lease whitelist")
    conn = get_db()
    grace_sql = f"-{grace_sec} seconds"
    cur = conn.execute(
        f"""
        UPDATE {table}
        SET status = 'stale',
            lease_heartbeat_at = NULL,
            lease_owner = NULL,
            finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
            error = COALESCE(
                error,
                '服务进程中断或重启，任务已中止（心跳 {grace_sec}s 未续约）'
            )
        WHERE status = 'running'
          AND (
            lease_heartbeat_at IS NULL
            OR lease_heartbeat_at < strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)
          )
        """,
        (grace_sql,),
    )
    conn.commit()
    swept = cur.rowcount
    if swept:
        logger.warning("[leases] swept %d stale row(s) from %s", swept, table)
    return swept


# ── helpers ────────────────────────────────────────────────────────────────


def _table(handle: LeaseHandle) -> str:
    """从 job_id 反查属于哪张表。出错就抛 ValueError。"""
    if handle.table and handle.table in _LEASE_TABLES:
        return handle.table
    conn = get_db()
    row = conn.execute(
        """
        SELECT 'fetch_jobs' AS t FROM fetch_jobs WHERE id = ?
        UNION ALL
        SELECT 'llm_jobs' AS t FROM llm_jobs WHERE id = ?
        """,
        (handle.job_id, handle.job_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"job {handle.job_id!r} not in fetch_jobs or llm_jobs")
    return row["t"]


def _check_table(handle: LeaseHandle) -> None:
    """调用 heartbeat/finish 时校验 handle 仍有效。"""
    if handle.fence <= 0:
        raise ValueError(f"invalid fence={handle.fence} in {handle}")
