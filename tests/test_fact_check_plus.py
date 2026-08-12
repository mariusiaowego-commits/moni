"""tests/test_fact_check_plus.py - 事实核查+ (v1.0.22) TDD RED 骨架.

Day 1 (red-fox): 14 个测试骨架, 全部 skip (pytestmark), 只留 imports +
fixture + def test_xxx() + pass. 让 pytest 跑 RED (14 skipped, 0 failed).
Day 2 (gold-tiger): 实现 moni 侧 .py 后移除 ``pytestmark`` 转 GREEN.

Spec: docs/specs/2026-08-11-fact-check-plus-v1.0.22.md:473-492 (section 6).
Fixture 模式: tests/test_fetch_jobs.py:25-33 (fresh_db) +
tests/test_api.py:29-41 (singleton reset).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.llm.jobs import enqueue_job
from moni.web.server import create_app

# Day 1: RED 阶段全部 skip; Day 2 移除本行后跑 GREEN.
pytestmark = pytest.mark.skip(reason="Day 1 TDD RED 骨架, Day 2 实现后移除")


# ---------------------------------------------------------------------------
# Fixtures (Day 2 使用; skip 阶段不会执行)
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    """临时 sqlite DB + 全量 migrate, teardown 关连接并复位 singleton."""
    db_path = tmp_path / "news.db"
    set_db_path(db_path)
    migrate()
    yield db_path
    close_db()
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod

    conn_mod._CON = None
    conn_mod._DB_PATH = None
    mig_mod._CON = None


@pytest.fixture
def client(fresh_db: Path) -> TestClient:
    """HTTP client pointed at the fresh temp DB."""
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# 14 个测试骨架 (spec section 6)
# ---------------------------------------------------------------------------


def test_fact_check_plus_endpoint_202_with_job_id():
    """POST /api/articles/{id}/fact-check-plus -> 202 + job_id + poll_url."""
    pass


def test_fact_check_plus_404_unknown_article():
    """未知 article_id 返 404."""
    pass


def test_fact_check_plus_503_no_active_model():
    """无 llm_settings active model 时返 503."""
    pass


def test_fact_check_plus_409_duplicate_active():
    """同 article 已有 pending/running 返 409 (与 brief 同模式)."""
    pass


def test_fact_check_plus_batch_limit_20():
    """POST /api/fact-check-plus/batch 超过 20 篇返 422."""
    pass


def test_fact_check_plus_evidence_endpoint_returns_list():
    """GET /api/fact-check-plus/evidence/{article_id} 按 evidence_seq 排序."""
    pass


def test_fact_check_plus_evidence_empty_when_not_done():
    """没做过核查返 200 + items=[]."""
    pass


def test_fact_check_plus_method_migration_llm_to_llm_inference():
    """迁移 SQL 把 fact_check_method 'llm' 刷成 'llm_inference' (9 -> 0)."""
    pass


def test_fact_check_plus_worker_writes_has_fact_check():
    """worker 完成写库时 has_fact_check=1 (修隐债)."""
    pass


def test_fact_check_plus_double_gate_domain_whitelist():
    """无白名单域源时【已核实】强制降级为【待核实】."""
    pass


def test_fact_check_plus_evidence_table_insert():
    """fact_check_evidence INSERT 成功 + FK CASCADE 删 article 级联."""
    pass


def test_old_fact_check_endpoint_returns_410():
    """/api/llm/fact-check 废弃死代码返 410."""
    pass


def test_routes_init_registers_fact_check_plus_router():
    """routes/__init__.py 注册 fact_check_plus_router."""
    pass


def test_jobs_task_type_whitelist_includes_plus():
    """enqueue_job('fact_check_plus', ...) 不抛 ValueError."""
    pass
