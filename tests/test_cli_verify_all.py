"""moni.cli.cases verify-all 测试 (v1.0.36 sprint-18).

跑 'moni cases verify-all --threshold=N' 一键审核 pending 案例:
- confidence >= 阈值的 → confirmed + verified
- 反之保持 pending
- 排除 archived / __processing__ 占位
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from moni.db.connection import set_db_path, get_db, close_db
import moni.db.connection as conn_mod
import moni.db.migrate as mig_mod
from moni.db.migrate import migrate


@pytest.fixture(autouse=True)
def fresh_db(tmp_path: Path, monkeypatch):
    """每个测试用独立 tmp DB. moni.cases.tests / fresh_db 范式."""
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    migrate()
    yield get_db()
    close_db()
    conn_mod._CON = None
    conn_mod._DB_PATH = None
    mig_mod._CON = None


def _seed_case(conn, *, case_id: str, confidence: int, status: str = "pending") -> None:
    """塞一条独立 article + 一条 case."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO articles (id, title, url, source, fetched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"art_{case_id}", f"t-{case_id}", f"u-{case_id}", "test", now),
    )
    conn.execute(
        "INSERT INTO cases "
        "(case_id, article_id, case_date_precision, subject, case_type, case_action, "
        "reference_value, extraction_status, review_status, case_lifecycle, "
        "extraction_attempts, extraction_confidence, normalize_version, "
        "extracted_at, created_at, updated_at) "
        "VALUES (?, ?, 'month', ?, 'AI', 'launch', 'medium', 'completed', ?, 'draft', "
        "1, ?, 1, ?, ?, ?)",
        (case_id, f"art_{case_id}", f"主体-{case_id}", status, confidence, now, now, now),
    )


def test_verify_all_above_threshold(fresh_db):
    """confidence >= 阈值的 pending → confirmed + verified."""
    conn = fresh_db
    _seed_case(conn, case_id="MN-AI-2608-0001", confidence=85)
    _seed_case(conn, case_id="MN-AI-2608-0002", confidence=95)
    _seed_case(conn, case_id="MN-AI-2608-0003", confidence=70)  # < 80 保留 pending
    conn.commit()

    # 复用 tmp_db db_path 跑 CLI (moni CLI 默认用 cwd 的 data/news.db, 这里直接调用函数)
    from moni.cli.main import cmd_cases_verify_all
    from types import SimpleNamespace

    args = SimpleNamespace(threshold=80, dry_run=False)
    rc = cmd_cases_verify_all(args)
    assert rc == 0

    rows = {
        r["case_id"]: (r["review_status"], r["case_lifecycle"])
        for r in conn.execute("SELECT case_id, review_status, case_lifecycle FROM cases").fetchall()
    }
    assert rows["MN-AI-2608-0001"] == ("confirmed", "verified")
    assert rows["MN-AI-2608-0002"] == ("confirmed", "verified")
    assert rows["MN-AI-2608-0003"] == ("pending", "draft")  # 未达阈值


def test_verify_all_dry_run_no_write(fresh_db):
    """--dry-run 不修改 DB."""
    conn = fresh_db
    _seed_case(conn, case_id="MN-AI-2608-0010", confidence=95)
    conn.commit()

    from moni.cli.main import cmd_cases_verify_all
    from types import SimpleNamespace

    args = SimpleNamespace(threshold=80, dry_run=True)
    rc = cmd_cases_verify_all(args)
    assert rc == 0

    row = conn.execute(
        "SELECT review_status, case_lifecycle FROM cases WHERE case_id = 'MN-AI-2608-0010'"
    ).fetchone()
    assert (row["review_status"], row["case_lifecycle"]) == ("pending", "draft")


def test_verify_all_idempotent(fresh_db):
    """第二次跑不会重复 verified 已 verified 的 (仍保持 confirmed)."""
    conn = fresh_db
    _seed_case(conn, case_id="MN-AI-2608-0020", confidence=85)
    conn.commit()

    from moni.cli.main import cmd_cases_verify_all
    from types import SimpleNamespace

    # 第一次
    cmd_cases_verify_all(SimpleNamespace(threshold=80, dry_run=False))
    # 第二次 (第二次 0 条匹配, 因为 review_status='pending' 已变 confirmed)
    rc = cmd_cases_verify_all(SimpleNamespace(threshold=80, dry_run=False))
    assert rc == 0

    row = conn.execute(
        "SELECT review_status, case_lifecycle FROM cases WHERE case_id = 'MN-AI-2608-0020'"
    ).fetchone()
    assert (row["review_status"], row["case_lifecycle"]) == ("confirmed", "verified")


def test_verify_all_skips_processing_placeholder(fresh_db):
    """__processing__ 占位行不被审核 (review_status 保持 pending)."""
    conn = fresh_db
    # 起点: 一条 pending + subject='__processing__' (模拟 v1.0.35.19 的 mark_processing 占位)
    _seed_case(conn, case_id="MN-AI-2608-0030", confidence=99)  # status='pending' 默认
    conn.execute(
        "UPDATE cases SET subject = '__processing__' WHERE case_id = 'MN-AI-2608-0030'"
    )
    conn.commit()

    from moni.cli.main import cmd_cases_verify_all
    from types import SimpleNamespace

    cmd_cases_verify_all(SimpleNamespace(threshold=80, dry_run=False))

    row = conn.execute(
        "SELECT review_status, case_lifecycle, subject FROM cases WHERE case_id = 'MN-AI-2608-0030'"
    ).fetchone()
    # 占位行不动: review_status 应保持 pending (即使 confidence=99 ≥ 80)
    assert row["review_status"] == "pending", (
        f"__processing__ 占位行被错误核验: status={row['review_status']}"
    )
    assert row["subject"] == "__processing__"  # 没被改