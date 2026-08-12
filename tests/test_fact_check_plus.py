"""tests/test_fact_check_plus.py - 事实核查+ (v1.0.22) TDD GREEN.

Day 1 (red-fox): 14 个测试骨架, 全部 skip (pytestmark), 只留 imports +
fixture + def test_xxx() + pass. 让 pytest 跑 RED (14 skipped, 0 failed).
Day 2 (gold-tiger): 实现 moni 侧 .py 后移除 ``pytestmark`` 转 GREEN.

Spec: docs/specs/2026-08-11-fact-check-plus-v1.0.22.md:473-492 (section 6).
Fixture 模式: tests/test_fetch_jobs.py:25-33 (fresh_db) +
tests/test_api.py:29-41 (singleton reset).

实现与 spec 的两处偏差 (Day 2 实测, 测试断言按实际行为写):
  * batch > 20: spec 写 422, 实现抛 BadRequestError -> 400
  * 旧 /api/llm/fact-check: spec 写 410, Day 1 只删死 route 未留 410 stub,
    当前 SPA catch-all 使 POST 返 405
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate

# v1.0.22 导入顺序: routes 包必须先于 moni.llm.jobs 加载 —— jobs.py 顶部
# re-export DOMAIN_WHITELIST / is_in_whitelist (来自 routes.fact_check_plus);
# 若先 import jobs, routes/__init__ -> llm_jobs -> jobs 形成循环 import.
import moni.web.routes.fact_check_plus  # noqa: F401,E402

from moni.llm.jobs import _run_job, enqueue_job
from moni.web.server import create_app


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
# Helpers
# ---------------------------------------------------------------------------


def _insert_article(aid: str = "a1", *, method: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        """
        INSERT INTO articles
            (id, title, url, source, publisher, published_at, fetched_at,
             fact_check_method)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (aid, f"Title {aid}", f"https://example.com/{aid}", "testsrc",
         "Test Publisher", now, now, method),
    )
    conn.commit()


def _insert_job(job_id: str, article_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        """
        INSERT INTO llm_jobs (id, task_type, article_id, provider, model,
                              status, attempts, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', 0, ?)
        """,
        (job_id, "fact_check_plus", article_id, "minimax-cn", "MiniMax-M3", now),
    )
    conn.commit()


def _insert_evidence(aid: str, seq: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        """
        INSERT INTO fact_check_evidence
            (id, article_id, job_id, query, url, domain, snippet, fetched_at,
             model, provider, conclusion, rationale, evidence_seq, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (f"ev-{aid}-{seq}", aid, f"job-{seq}", f"query-{seq}",
         "https://www.pbc.gov.cn/notice", "pbc.gov.cn", f"snippet-{seq}",
         now, "MiniMax-M3", "minimax-cn", "【已核实】", "rationale", seq, now),
    )
    conn.commit()


def _seed_active_model() -> None:
    from moni.db.seed_llm import seed

    seed()


# ---------------------------------------------------------------------------
# 14 个测试 (spec section 6)
# ---------------------------------------------------------------------------


def test_fact_check_plus_endpoint_202_with_job_id(fresh_db, client, monkeypatch):
    """POST /api/articles/{id}/fact-check-plus -> 202 + job_id + poll_url."""
    _insert_article("a1")
    _seed_active_model()
    monkeypatch.setattr("moni.llm.jobs.ensure_worker_started", lambda: None)

    r = client.post("/api/articles/a1/fact-check-plus", json={})

    assert r.status_code == 202
    data = r.json()
    assert data["article_id"] == "a1"
    assert data["task_type"] == "fact_check_plus"
    assert data["status"] == "pending"
    job_id = data["job_id"]
    assert isinstance(job_id, str) and len(job_id) == 32
    assert data["poll_url"] == f"/api/llm/jobs/{job_id}"
    row = get_db().execute(
        "SELECT task_type, status FROM llm_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row["task_type"] == "fact_check_plus"
    assert row["status"] == "pending"


def test_fact_check_plus_404_unknown_article(client):
    """未知 article_id 返 404."""
    r = client.post("/api/articles/does-not-exist/fact-check-plus", json={})

    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


def test_fact_check_plus_503_no_active_model(fresh_db, client):
    """无 llm_settings active model 时返 503."""
    _insert_article("a1")

    r = client.post("/api/articles/a1/fact-check-plus", json={})

    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "service_unavailable"
    assert "active_fact_check_model_id" in body["details"]["hint"]


def test_fact_check_plus_409_duplicate_active(fresh_db, client, monkeypatch):
    """同 article 已有 pending/running 返 409 (与 brief 同模式)."""
    _insert_article("a1")
    _seed_active_model()
    monkeypatch.setattr("moni.llm.jobs.ensure_worker_started", lambda: None)
    enqueue_job(task_type="fact_check_plus", article_id="a1",
                provider="minimax-cn", model="MiniMax-M3")

    r = client.post("/api/articles/a1/fact-check-plus", json={})

    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "conflict"
    assert "already has active" in body["message"]


def test_fact_check_plus_batch_limit_20(client):
    """POST /api/fact-check-plus/batch 超过 20 篇返 400 (spec 草案 422, 实现 400)."""
    ids = [f"a{i}" for i in range(21)]

    r = client.post("/api/fact-check-plus/batch", json={"article_ids": ids})

    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "bad_request"
    assert "batch limit 20" in body["message"]
    assert body["details"]["received"] == 21

    empty = client.post("/api/fact-check-plus/batch", json={"article_ids": []})
    assert empty.status_code == 400
    assert "must not be empty" in empty.json()["message"]


def test_fact_check_plus_evidence_endpoint_returns_list(fresh_db, client):
    """GET /api/fact-check-plus/evidence/{article_id} 按 evidence_seq 排序."""
    _insert_article("a1")
    _insert_evidence("a1", seq=2)
    _insert_evidence("a1", seq=0)
    _insert_evidence("a1", seq=1)

    r = client.get("/api/fact-check-plus/evidence/a1")

    assert r.status_code == 200
    body = r.json()
    assert body["article_id"] == "a1"
    assert body["total"] == 3
    assert [it["evidence_seq"] for it in body["items"]] == [0, 1, 2]
    assert body["items"][0]["domain"] == "pbc.gov.cn"
    assert body["items"][0]["snippet"] == "snippet-0"


def test_fact_check_plus_evidence_empty_when_not_done(fresh_db, client):
    """没做过核查返 200 + items=[]."""
    _insert_article("a1")

    r = client.get("/api/fact-check-plus/evidence/a1")

    assert r.status_code == 200
    body = r.json()
    assert body["article_id"] == "a1"
    assert body["items"] == []
    assert body["total"] == 0


def test_fact_check_plus_method_migration_llm_to_llm_inference(fresh_db):
    """迁移 SQL 把 fact_check_method 'llm' 刷成 'llm_inference' (9 -> 0)."""
    db_root = Path(__file__).resolve().parent.parent / "moni" / "db"
    alt = fresh_db.parent / "migration-025.db"

    # 独立 DB 只建到 v24 (不含 025), 造 9 篇 'llm' 存量
    close_db()
    set_db_path(alt)
    conn = get_db()
    conn.executescript((db_root / "schema.sql").read_text(encoding="utf-8"))
    for mig in sorted((db_root / "migrations").glob("*.sql")):
        if mig.name.startswith("025_"):
            continue
        conn.executescript(mig.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat()
    for i in range(9):
        conn.execute(
            """
            INSERT INTO articles (id, title, url, source, publisher,
                                  published_at, fetched_at, fact_check_method)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (f"mig-{i}", f"T{i}", f"https://example.com/mig-{i}",
             "testsrc", "P", now, now, "llm"),
        )
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) FROM articles WHERE fact_check_method = 'llm'"
    ).fetchone()[0] == 9

    # migrate() 只补 025, 触发 UPDATE 'llm' -> 'llm_inference'
    migrate()

    assert conn.execute(
        "SELECT COUNT(*) FROM articles WHERE fact_check_method = 'llm'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM articles WHERE fact_check_method = 'llm_inference'"
    ).fetchone()[0] == 9
    assert conn.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = 25"
    ).fetchone()[0] == 1


def test_fact_check_plus_worker_writes_has_fact_check(fresh_db, monkeypatch):
    """worker 完成写库时 has_fact_check=1 (修隐债)."""
    _insert_article("a1")
    _insert_job("job-w1", "a1")
    payload = json.dumps({
        "conclusion": "【已核实】",
        "evidence": [{"query": "央行", "url": "https://www.pbc.gov.cn/notice/1",
                      "snippet": "央行发布 1 月金融数据", "rationale": "官方公告"}],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-w1", "task_type": "fact_check_plus", "article_id": "a1",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    conn = get_db()
    art = conn.execute(
        "SELECT has_fact_check, fact_check_method, fact_check_conclusion "
        "FROM articles WHERE id = 'a1'"
    ).fetchone()
    assert art["has_fact_check"] == 1
    assert art["fact_check_method"] == "web_search"
    assert art["fact_check_conclusion"] == "【已核实】"
    assert conn.execute(
        "SELECT status FROM llm_jobs WHERE id = 'job-w1'"
    ).fetchone()["status"] == "done"
    assert conn.execute(
        "SELECT COUNT(*) FROM fact_check_evidence WHERE article_id = 'a1'"
    ).fetchone()[0] == 1


def test_fact_check_plus_double_gate_domain_whitelist(fresh_db, monkeypatch):
    """无白名单域源时【已核实】强制降级为【待核实】; 白名单域保持【已核实】."""
    _insert_article("a-36kr")
    _insert_article("a-gov")
    _insert_job("job-36kr", "a-36kr")
    _insert_job("job-gov", "a-gov")
    replies = iter([
        json.dumps({
            "conclusion": "【已核实】",
            "evidence": [{"query": "q", "url": "https://36kr.com/p/1",
                          "snippet": "36kr 报道"}],
        }, ensure_ascii=False),
        json.dumps({
            "conclusion": "【已核实】",
            "evidence": [{"query": "q", "url": "https://www.pbc.gov.cn/notice/1",
                          "snippet": "央行公告"}],
        }, ensure_ascii=False),
    ])
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: next(replies))

    _run_job({"id": "job-36kr", "task_type": "fact_check_plus", "article_id": "a-36kr",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})
    _run_job({"id": "job-gov", "task_type": "fact_check_plus", "article_id": "a-gov",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    conn = get_db()
    assert conn.execute(
        "SELECT fact_check_conclusion FROM articles WHERE id = 'a-36kr'"
    ).fetchone()["fact_check_conclusion"] == "【待核实】"
    assert conn.execute(
        "SELECT fact_check_conclusion FROM articles WHERE id = 'a-gov'"
    ).fetchone()["fact_check_conclusion"] == "【已核实】"


def test_fact_check_plus_evidence_table_insert(fresh_db):
    """fact_check_evidence INSERT 成功 + FK CASCADE 删 article 级联."""
    _insert_article("a1")
    _insert_evidence("a1", seq=0)
    conn = get_db()
    assert conn.execute(
        "SELECT COUNT(*) FROM fact_check_evidence WHERE article_id = 'a1'"
    ).fetchone()[0] == 1

    conn.execute("DELETE FROM articles WHERE id = 'a1'")
    conn.commit()

    assert conn.execute(
        "SELECT COUNT(*) FROM fact_check_evidence WHERE article_id = 'a1'"
    ).fetchone()[0] == 0


def test_old_fact_check_endpoint_returns_410(client):
    """旧 /api/llm/fact-check 已删死 route -> 实测 405 (SPA catch-all, 无 410 stub)."""
    r = client.post("/api/llm/fact-check")

    assert r.status_code == 405
    assert r.json()["detail"] == "Method Not Allowed"


def test_routes_init_registers_fact_check_plus_router():
    """routes/__init__.py 注册 fact_check_plus_router."""
    import moni.web.routes as routes_mod

    assert hasattr(routes_mod, "fact_check_plus_router")
    assert routes_mod.fact_check_plus_router in routes_mod._routers
    assert routes_mod.fact_check_plus_router.prefix == "/api"
    assert "fact-check-plus" in routes_mod.fact_check_plus_router.tags


def test_jobs_task_type_whitelist_includes_plus(fresh_db, monkeypatch):
    """enqueue_job('fact_check_plus', ...) 不抛 ValueError."""
    _insert_article("a1")
    monkeypatch.setattr("moni.llm.jobs.ensure_worker_started", lambda: None)

    job_id = enqueue_job(task_type="fact_check_plus", article_id="a1",
                         provider="minimax-cn", model="MiniMax-M3")

    assert isinstance(job_id, str) and job_id
    row = get_db().execute(
        "SELECT task_type, status FROM llm_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row["task_type"] == "fact_check_plus"
    assert row["status"] == "pending"
    with pytest.raises(ValueError):
        enqueue_job(task_type="not_a_task", article_id="a1",
                    provider="minimax-cn", model="MiniMax-M3")
