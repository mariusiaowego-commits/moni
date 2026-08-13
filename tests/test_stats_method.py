"""tests/test_stats_method.py - v1.0.25 看板 method 分布测试.

Spec: docs/specs/2026-08-12-stats-method-v1.0.25.md §5 (5 个测试).
v1.0.25 实现未落地 (spec draft) — 测试按 §3/§4 数据契约写, 实现落地后转 GREEN.
入口:
  * moni.stats.repository.fact_check_method_layer_stats(period)  — 聚合层
  * GET /api/stats/fact-check-method?period=7d|30d|all            — 端点
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.stats import repository as stats_repo
from moni.web.server import create_app


# ---------------------------------------------------------------------------
# Fixtures (同 tests/test_fact_check_plus.py 模式)
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
# Helpers
# ---------------------------------------------------------------------------


def _insert_article(
    aid: str,
    *,
    method: str | None = None,
    has_fact_check: int = 0,
    checked_at: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        """
        INSERT INTO articles (
            id, title, url, source, publisher, published_at, fetched_at,
            fact_check_method, has_fact_check, fact_checked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            aid,
            f"标题-{aid}",
            f"https://example.com/{aid}",
            "testsrc",
            "Pub",
            now,
            now,
            method,
            has_fact_check,
            checked_at,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 5 个测试 (spec §5)
# ---------------------------------------------------------------------------


def test_fact_check_method_layer_stats_returns_3_methods(fresh_db):
    """seed 3 种 method 各 5 篇 -> by_method 长度 3 + 数量/比例正确."""
    for method in ("llm_inference", "web_search", "human"):
        for i in range(5):
            _insert_article(f"{method}-{i}", method=method, has_fact_check=1)

    data = stats_repo.fact_check_method_layer_stats("all")

    assert data["period"] == "all"
    assert data["total_articles_with_fact_check"] == 15
    assert len(data["by_method"]) == 3
    counts = {row["method"]: row["article_count"] for row in data["by_method"]}
    assert counts == {"llm_inference": 5, "web_search": 5, "human": 5}
    assert all(row["percentage"] == 33.3 for row in data["by_method"])


def test_fact_check_method_layer_stats_7d_filters_correctly(fresh_db):
    """8d 前 5 篇 + 7d 内 5 篇 -> 7d 只返 7d 内的 5 篇."""
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=8)).isoformat()
    recent = now.isoformat()
    for i in range(5):
        _insert_article(f"old-{i}", method="llm_inference",
                        has_fact_check=1, checked_at=old)
        _insert_article(f"new-{i}", method="web_search",
                        has_fact_check=1, checked_at=recent)

    data = stats_repo.fact_check_method_layer_stats("7d")

    assert data["period"] == "7d"
    assert data["total_articles_with_fact_check"] == 5
    rows = {row["method"]: row["article_count"] for row in data["by_method"]}
    assert rows == {"web_search": 5}


def test_fact_check_method_layer_stats_all_no_filter(fresh_db):
    """period=all -> 不分时间窗, 返全部 method 分布."""
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=60)).isoformat()
    _insert_article("old1", method="llm_inference", has_fact_check=1, checked_at=old)
    _insert_article("old2", method="llm_inference", has_fact_check=1, checked_at=old)
    _insert_article("new1", method="web_search", has_fact_check=1, checked_at=now.isoformat())

    data = stats_repo.fact_check_method_layer_stats("all")

    assert data["period"] == "all"
    assert data["total_articles_with_fact_check"] == 3
    counts = {row["method"]: row["article_count"] for row in data["by_method"]}
    assert counts == {"llm_inference": 2, "web_search": 1}


def test_fact_check_method_layer_stats_unfact_total_correct(fresh_db):
    """10 篇已核查 + 5 篇未核查 -> unfact_checked_total=5."""
    for i in range(10):
        _insert_article(f"fc-{i}", method="llm_inference", has_fact_check=1)
    for i in range(5):
        _insert_article(f"nofc-{i}", method=None, has_fact_check=0)

    data = stats_repo.fact_check_method_layer_stats("all")

    assert data["total_articles_with_fact_check"] == 10
    assert data["unfact_checked_total"] == 5


def test_fact_check_method_endpoint_200_with_data(fresh_db, client):
    """端点返 200 + JSON 含 by_method / total / unfact 总数."""
    _insert_article("a1", method="llm_inference", has_fact_check=1)
    _insert_article("a2", method="web_search", has_fact_check=1)
    _insert_article("a3", method=None, has_fact_check=0)

    r = client.get("/api/stats/fact-check-method?period=all")

    assert r.status_code == 200
    body = r.json()
    assert body["period"] == "all"
    assert body["total_articles_with_fact_check"] == 2
    assert body["unfact_checked_total"] == 1
    assert len(body["by_method"]) == 2
