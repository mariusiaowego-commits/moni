"""Tests for moni.db.leases — 统一租约运行时 (v1.0.42, sprint-22).

Covers:
  * acquire 原子认领, fence 单调递增
  * heartbeat 续约 fence/owner 校验
  * finish(fenced) 拒绝迟到写者
  * sweep_expired 心跳制 (不耦合 timeout 预算)
  * 表白名单拒绝未授权表名
  * 多 worker 抢同一表不会双计费
  * 时间戳 SQLite 端生成 (跨进程钟差免疫)
"""
from __future__ import annotations

import os
import re
import threading
import uuid

import pytest

from moni.db.connection import get_db, set_db_path

# Force a per-test sqlite file so thread-local connections inside
# TestClient / anyio workers don't leak between tests.
#
# v1.0.52 cleanup: use tmp_path per test rather than a fixed /tmp file
# so parallel test runners do not suffer disk I/O collision.


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Fresh DB per test, schema up-to-date."""
    test_db = str(tmp_path / "moni-test-leases.db")
    monkeypatch.setenv("MONI_DB_PATH", test_db)
    set_db_path(None)  # force re-resolve
    # Force this thread's connection to drop so the new path is honoured.
    from moni.db.connection import close_db
    close_db()
    # Recreate DB
    try:
        os.unlink(test_db)
    except FileNotFoundError:
        pass
    from moni.db.migrate import migrate
    migrate()
    yield
    # Teardown: drop connection so next test rebuilds fresh.
    close_db()


def _insert_fetch(source: str = "test_src", status: str = "pending") -> str:
    c = get_db()
    jid = str(uuid.uuid4())
    c.execute(
        "INSERT INTO fetch_jobs (id, source, status, started_at, total_feeds, done_feeds, articles_so_far, elapsed_seconds) "
        "VALUES (?, ?, ?, '2026-09-10T00:00:00+00:00', 0, 0, 0, 0)",
        (jid, source, status),
    )
    c.commit()
    return jid


def _insert_llm(article_id: str, status: str = "pending") -> str:
    c = get_db()
    jid = str(uuid.uuid4())
    c.execute(
        "INSERT INTO llm_jobs (id, task_type, article_id, provider, model, status, created_at, attempts) "
        "VALUES (?, 'summary', ?, 'ark', 'ark-code-latest', ?, '2026-09-10T00:00:00+00:00', 0)",
        (jid, article_id, status),
    )
    c.commit()
    return jid


def _make_article() -> str:
    c = get_db()
    aid = str(uuid.uuid4())
    # url 加 uuid 后缀避免 UNIQUE(source,url) 冲突
    c.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
        "VALUES (?, 'lease-test', ?, 'lease-test', '2026-09-10T00:00:00+00:00', '')",
        (aid, f"http://x/y/{aid}"),
    )
    c.commit()
    return aid


# ── acquire ──────────────────────────────────────────────────────────────


def test_acquire_returns_first_pending_row():
    from moni.db import leases

    _insert_fetch()
    h = leases.acquire("fetch_jobs", grace_sec=90)
    assert h is not None
    assert h.fence >= 1
    assert h.owner


def test_acquire_returns_none_when_empty():
    from moni.db import leases

    assert leases.acquire("fetch_jobs", grace_sec=90) is None


def test_acquire_rejects_unknown_table():
    from moni.db import leases

    with pytest.raises(ValueError, match="not in lease whitelist"):
        leases.acquire("evil_table", grace_sec=90)


def test_acquire_writes_sqlite_strftime_heartbeat():
    from moni.db import leases

    _insert_fetch()
    h = leases.acquire("fetch_jobs", grace_sec=90)
    row = get_db().execute(
        "SELECT lease_heartbeat_at FROM fetch_jobs WHERE id = ?", (h.job_id,)
    ).fetchone()
    assert row["lease_heartbeat_at"] is not None
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$",
        row["lease_heartbeat_at"],
    ), row["lease_heartbeat_at"]


# ── heartbeat ────────────────────────────────────────────────────────────


def test_heartbeat_renews_within_grace():
    from moni.db import leases

    _insert_fetch()
    h = leases.acquire("fetch_jobs", grace_sec=90)
    assert leases.heartbeat(h) is True


def test_heartbeat_fails_after_finish():
    """心跳 after finish → rowcount=0 (租赁已释放,不应再续约)。"""
    from moni.db import leases

    _insert_fetch()
    h = leases.acquire("fetch_jobs", grace_sec=90)
    assert leases.finish(h, status="done") is True
    assert leases.heartbeat(h) is False


def test_heartbeat_fails_with_wrong_fence():
    from moni.db import leases

    _insert_fetch()
    h = leases.acquire("fetch_jobs", grace_sec=90)
    tampered = leases.LeaseHandle(job_id=h.job_id, owner=h.owner, fence=h.fence + 999)
    assert leases.heartbeat(tampered) is False


# ── finish (fencing) ──────────────────────────────────────────────────────


def test_finish_with_correct_fence_writes_status():
    from moni.db import leases

    _insert_fetch()
    h = leases.acquire("fetch_jobs", grace_sec=90)
    assert leases.finish(h, status="done", extra_fields={"elapsed_seconds": 42}) is True
    row = get_db().execute(
        "SELECT status, lease_owner, lease_heartbeat_at, elapsed_seconds "
        "FROM fetch_jobs WHERE id = ?",
        (h.job_id,),
    ).fetchone()
    assert row["status"] == "done"
    assert row["lease_owner"] is None  # released
    assert row["lease_heartbeat_at"] is None
    assert row["elapsed_seconds"] == 42


def test_finish_rejects_late_writer_with_wrong_fence():
    """迟到写者(原线程复活)带原 fence → rowcount=0;新写者 fence 不同。

    模拟:持有者 h_a 心跳过期了,sweep 还没跑,但我们手工模拟"新持有者
    抢到同一行"场景:直接 UPDATE 让行 fence+1,新持有者 h_new 用新 fence
    finish;h_a 用旧 fence finish 被拒。
    """
    from moni.db import leases

    _insert_fetch()
    h_a = leases.acquire("fetch_jobs", grace_sec=90)
    # 模拟 sweep 抢占:把 heartbeat 推后 + fence+1 + owner 改成新持有者
    new_owner = "new_owner_" + h_a.owner[:4]
    get_db().execute(
        "UPDATE fetch_jobs SET lease_heartbeat_at = '2099-01-01T00:00:00.000000Z', "
        "fence = ?, lease_owner = ? WHERE id = ?",
        (h_a.fence + 1, new_owner, h_a.job_id),
    )
    get_db().commit()
    # 旧 handle fence 失配 → finish 拒绝
    assert leases.finish(h_a, status="done") is False
    # 新 fence + 新 owner 持有者能 finish
    h_new = leases.LeaseHandle(job_id=h_a.job_id, owner=new_owner, fence=h_a.fence + 1)
    assert leases.finish(h_new, status="done") is True
    row = get_db().execute(
        "SELECT status FROM fetch_jobs WHERE id=?", (h_a.job_id,)
    ).fetchone()
    assert row["status"] == "done"


def test_finish_rejects_late_writer_with_wrong_owner():
    from moni.db import leases

    _insert_fetch()
    h = leases.acquire("fetch_jobs", grace_sec=90)
    # 同一 fence 但不同 owner (worker 替代)
    imposter = leases.LeaseHandle(job_id=h.job_id, owner="imposter", fence=h.fence)
    assert leases.finish(imposter, status="failed") is False


# ── sweep_expired ────────────────────────────────────────────────────────


def test_sweep_marks_zombie_as_stale():
    """心跳已过期的 running 行 → status='stale' (不耦合 timeout 预算)。"""
    from moni.db import leases

    _insert_fetch(status="running")
    c = get_db()
    c.execute(
        "UPDATE fetch_jobs SET lease_owner = ?, "
        "lease_heartbeat_at = '2020-01-01T00:00:00.000000Z', fence = 5 "
        "WHERE source = 'test_src'",
        ("ghost_owner",),
    )
    c.commit()
    swept = leases.sweep_expired("fetch_jobs", grace_sec=90)
    assert swept == 1
    row = c.execute(
        "SELECT status, lease_owner FROM fetch_jobs WHERE source='test_src'"
    ).fetchone()
    assert row["status"] == "stale"
    assert row["lease_owner"] is None
    assert "服务进程中断" in c.execute(
        "SELECT error FROM fetch_jobs WHERE source='test_src'"
    ).fetchone()["error"]


def test_sweep_keeps_fresh_heartbeat_rows():
    from moni.db import leases

    _insert_fetch(status="running")
    # 强行写一个未来时间心跳,模拟刚 acquire 完的活行
    c = get_db()
    c.execute(
        "UPDATE fetch_jobs SET lease_owner = ?, "
        "lease_heartbeat_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), fence = 5 "
        "WHERE source = 'test_src'",
        ("live_owner",),
    )
    c.commit()
    swept = leases.sweep_expired("fetch_jobs", grace_sec=90)
    assert swept == 0
    row = c.execute(
        "SELECT status FROM fetch_jobs WHERE source='test_src'"
    ).fetchone()
    assert row["status"] == "running"


def test_sweep_reaps_pending_rows_with_expired_heartbeat():
    """Queued batch rows carry a heartbeat; an orphaned queue must be reaped."""
    from moni.db import leases

    jid = _insert_fetch(status="pending")
    c = get_db()
    c.execute(
        "UPDATE fetch_jobs SET lease_owner = 'batch_worker', "
        "lease_heartbeat_at = '2020-01-01T00:00:00.000000Z' WHERE id = ?",
        (jid,),
    )
    c.commit()

    assert leases.sweep_expired("fetch_jobs", grace_sec=90) == 1
    row = c.execute(
        "SELECT status FROM fetch_jobs WHERE id = ?", (jid,)
    ).fetchone()
    assert row["status"] == "stale"


def test_sweep_keeps_pending_rows_without_heartbeat():
    """Pending rows with no heartbeat are still claimable — never reaped."""
    from moni.db import leases

    jid = _insert_fetch(status="pending")
    assert leases.sweep_expired("fetch_jobs", grace_sec=90) == 0
    row = get_db().execute(
        "SELECT status FROM fetch_jobs WHERE id = ?", (jid,)
    ).fetchone()
    assert row["status"] == "pending"


def test_sweep_never_touches_llm_pending_rows():
    """The queued-row branch is fetch-specific; llm_jobs keeps its semantics."""
    from moni.db import leases

    c = get_db()
    c.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at) "
        "VALUES ('llm-art-1', 't', 'https://example.com/llm-1', 'llm_test_src', "
        "'2026-09-10T00:00:00+00:00')"
    )
    jid = str(uuid.uuid4())
    c.execute(
        "INSERT INTO llm_jobs (id, task_type, article_id, status, created_at, "
        "lease_owner, lease_heartbeat_at) "
        "VALUES (?, 'summary', 'llm-art-1', 'pending', '2026-09-10T00:00:00+00:00', "
        "'owner', '2020-01-01T00:00:00.000000Z')",
        (jid,),
    )
    c.commit()

    assert leases.sweep_expired("llm_jobs", grace_sec=90) == 0
    row = c.execute(
        "SELECT status FROM llm_jobs WHERE id = ?", (jid,)
    ).fetchone()
    assert row["status"] == "pending"


def test_sweep_works_on_llm_jobs_too():
    from moni.db import leases

    aid = _make_article()
    _insert_llm(aid, status="running")
    c = get_db()
    c.execute(
        "UPDATE llm_jobs SET lease_owner = ?, "
        "lease_heartbeat_at = '2020-01-01T00:00:00.000000Z', fence = 7 "
        "WHERE article_id = ?",
        ("ghost", aid),
    )
    c.commit()
    swept = leases.sweep_expired("llm_jobs", grace_sec=90)
    assert swept == 1


# ── multi-worker concurrency ─────────────────────────────────────────────


def test_two_workers_get_distinct_jobs_no_double_billing():
    """模拟 2 个 worker 抢同一张表 → 必须拿不同行,不能同一行被 2 个 worker 都拿到。"""
    from moni.db import leases

    for i in range(5):
        _insert_fetch(source=f"src_{i}")

    handles = []
    for w in ("worker_a", "worker_b", "worker_c"):
        for _ in range(3):
            h = leases.acquire("fetch_jobs", grace_sec=90, owner=w)
            if h is None:
                break
            handles.append(h)

    job_ids = [h.job_id for h in handles]
    assert len(set(job_ids)) == len(job_ids), "duplicate claim detected"


def test_finish_blocks_cross_owner_attack():
    """worker A 拿的 lease,worker B 不能 finish。"""
    from moni.db import leases

    _insert_fetch()
    h_a = leases.acquire("fetch_jobs", grace_sec=90, owner="worker_a")
    imposter = leases.LeaseHandle(
        job_id=h_a.job_id, owner="worker_b", fence=h_a.fence
    )
    assert leases.finish(imposter, status="failed") is False
    assert leases.finish(h_a, status="done") is True


# ── retry queue (llm_jobs retry 路径) ─────────────────────────────────────


def test_llm_acquire_after_release_re_enters_queue():
    """llm_jobs pending → running (acquire) → finish → requeue (back to pending)。

    模拟 llm_jobs._run_job 失败路径:把 status 重置回 pending 让下次 acquire 还能拿到。
    """
    from moni.db import leases

    aid = _make_article()
    jid = _insert_llm(aid, status="pending")

    h1 = leases.acquire("llm_jobs", grace_sec=90)
    assert h1 is not None and h1.job_id == jid
    # finish 失败路径,不动 fence;后续 acquire 时 fence+1
    assert leases.finish(h1, status="pending", extra_fields={"attempts": 1}) is True
    # 再次 acquire → fence 应 +1
    h2 = leases.acquire("llm_jobs", grace_sec=90)
    assert h2 is not None
    assert h2.job_id == jid
    # fence 单调递增
    assert h2.fence > h1.fence


def test_fence_monotonic_increases_per_acquire():
    from moni.db import leases

    _insert_fetch()
    fences = []
    for _ in range(5):
        h = leases.acquire("fetch_jobs", grace_sec=90)
        if h is None:
            break
        fences.append(h.fence)
        # finish + 释放 (lease_* 清零, fence 列上保留作为审计)
        assert leases.finish(h, status="pending") is True
        # 重置 status 回 pending 让下次 acquire 还能拿到这行
        get_db().execute(
            "UPDATE fetch_jobs SET status='pending', lease_owner=NULL, "
            "lease_heartbeat_at=NULL WHERE id=?",
            (h.job_id,),
        )
        get_db().commit()
    # 每次 acquire 都应 fence+1
    for i in range(1, len(fences)):
        assert fences[i] == fences[i - 1] + 1, fences
