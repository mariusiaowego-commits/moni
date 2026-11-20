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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate

# v1.0.22 导入顺序: routes 包必须先于 moni.llm.jobs 加载 —— jobs.py 顶部
# re-export DOMAIN_WHITELIST / is_in_whitelist (来自 routes.fact_check_plus);
# 若先 import jobs, routes/__init__ -> llm_jobs -> jobs 形成循环 import.
import moni.web.routes.fact_check_plus  # noqa: F401,E402

from moni.llm.jobs import _parse_fact_check_plus_evidence, _run_job, enqueue_job
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
             raw_content, fact_check_method)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (aid, f"Title {aid}", f"https://example.com/{aid}", "testsrc",
         "Test Publisher", now, now, "正文内容。" * 80, method),
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


def _insert_evidence(
    aid: str,
    seq: int,
    *,
    fetched_at: str | None = None,
    days_old: int | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        """
        INSERT INTO fact_check_evidence
            (id, article_id, job_id, query, url, domain, snippet, fetched_at,
             model, provider, conclusion, rationale, evidence_seq, created_at,
             days_old)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (f"ev-{aid}-{seq}", aid, f"job-{seq}", f"query-{seq}",
         "https://www.pbc.gov.cn/notice", "pbc.gov.cn", f"snippet-{seq}",
         fetched_at or now, "MiniMax-M3", "minimax-cn", "【已核实】", "rationale",
         seq, now, days_old),
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


def test_get_llm_job_detail_attaches_fact_check_for_done_plus(fresh_db, client):
    """v1.0.35.13: GET /api/llm/jobs/{id} 对 fact_check_plus done 附加结构化
    fact_check (conclusion + evidence)，供前端友好渲染，而非原始 JSON。"""
    _insert_article("a1", method="web_search")
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO llm_jobs (id, task_type, article_id, provider, model, status, attempts, created_at) "
        "VALUES ('job-done1', 'fact_check_plus', 'a1', 'minimax-cn', 'MiniMax-M3', 'done', 1, ?)",
        (now,),
    )
    conn.execute(
        "UPDATE articles SET fact_check_conclusion = '【已核实】测试', fact_check_method = 'web_search' "
        "WHERE id = 'a1'"
    )
    _insert_evidence("a1", 1)
    conn.commit()

    r = client.get("/api/llm/jobs/job-done1")
    assert r.status_code == 200
    data = r.json()
    assert data["task_type"] == "fact_check_plus"
    assert "fact_check" in data
    assert data["fact_check"]["conclusion"] == "【已核实】测试"
    ev = data["fact_check"]["evidence"]
    assert len(ev) == 1
    assert ev[0]["url"] == "https://www.pbc.gov.cn/notice"
    assert ev[0]["snippet"] == "snippet-1"


def test_get_llm_job_detail_no_fact_check_for_summary(fresh_db, client):
    """非 fact_check_plus (e.g. summary) done 不应带 fact_check 内容.

    v1.0.110 起该路由挂了 ``response_model``：字段恒定存在，没数据时为
    ``null``。断言 intent 不变 —— 值必须是空，不是有内容。
    """
    _insert_article("a2")
    now = datetime.now(timezone.utc).isoformat()
    get_db().execute(
        "INSERT INTO llm_jobs (id, task_type, article_id, provider, model, status, attempts, created_at) "
        "VALUES ('job-sum1', 'summary', 'a2', 'minimax-cn', 'MiniMax-M3', 'done', 1, ?)",
        (now,),
    )
    get_db().commit()
    r = client.get("/api/llm/jobs/job-sum1")
    assert r.status_code == 200
    assert r.json().get("fact_check") is None


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
        # skip 025+ (fact_check_evidence 及之后依赖它的 migration)
        if int(mig.name[:3]) >= 25:
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
        "evidence": [
            {"query": "央行", "url": "https://www.pbc.gov.cn/notice/1",
             "snippet": "央行发布 1 月金融数据", "rationale": "官方公告"},
            {"query": "监管", "url": "https://www.nfra.gov.cn/notice/2",
             "snippet": "金融监管总局发布", "rationale": "官方公告"},
        ],
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
    ).fetchone()[0] == 2


def test_fact_check_plus_persists_summary_and_verdicts(fresh_db, monkeypatch):
    """046: summary → articles.fact_check_summary; claim/verdict → evidence rows."""
    _insert_article("a1")
    _insert_job("job-046", "a1")
    payload = json.dumps({
        "conclusion": "【待核实】",
        "summary": "参数表与官方文档一致；适用性仍是作者判断",
        "evidence": [
            {"claim": "温度范围是 0 到 2", "verdict": "【已核实】",
             "query": "q1", "url": "https://www.pbc.gov.cn/notice/1",
             "snippet": "s1", "rationale": "r1"},
            {"claim": "某个未核清的说法", "verdict": "【存疑】",
             "query": "q2", "url": "https://www.pbc.gov.cn/notice/2",
             "snippet": "s2", "rationale": "r2"},
        ],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-046", "task_type": "fact_check_plus", "article_id": "a1",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    conn = get_db()
    art = conn.execute(
        "SELECT fact_check_summary FROM articles WHERE id = 'a1'"
    ).fetchone()
    assert art["fact_check_summary"] == "参数表与官方文档一致；适用性仍是作者判断"

    rows = conn.execute(
        "SELECT claim, verdict FROM fact_check_evidence "
        "WHERE article_id = 'a1' ORDER BY evidence_seq"
    ).fetchall()
    assert [r["claim"] for r in rows] == ["温度范围是 0 到 2", "某个未核清的说法"]
    assert [r["verdict"] for r in rows] == ["【已核实】", "【存疑】"]


def test_fact_check_plus_verdict_defaults_to_conclusion(fresh_db, monkeypatch):
    """旧模型不吐 verdict 时，逐条判定回退到总体 conclusion。"""
    _insert_article("a1")
    _insert_job("job-046b", "a1")
    payload = json.dumps({
        "conclusion": "【已核实】",
        "evidence": [
            {"query": "q", "url": "https://www.pbc.gov.cn/notice/1",
             "snippet": "s", "rationale": "r"},
            {"query": "q2", "url": "https://www.nfra.gov.cn/notice/2",
             "snippet": "s2", "rationale": "r2"},
        ],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-046b", "task_type": "fact_check_plus", "article_id": "a1",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    row = get_db().execute(
        "SELECT verdict FROM fact_check_evidence WHERE article_id = 'a1'"
    ).fetchone()
    assert row["verdict"] == "【已核实】"


def test_fact_check_plus_demotion_keeps_summary_and_verdicts(fresh_db, monkeypatch):
    """Gate 2 降级后 summary 保留并标注门禁原因、逐条【已核实】降为【待核实】."""
    _insert_article("a1")
    _insert_job("job-dem", "a1")
    payload = json.dumps({
        "conclusion": "【已核实】",
        "summary": "参数与官方文档一致",
        "evidence": [
            {"claim": "某个参数范围", "verdict": "【已核实】",
             "query": "q", "url": "https://36kr.com/p/1",
             "snippet": "s", "rationale": "r"},
        ],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-dem", "task_type": "fact_check_plus", "article_id": "a1",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    conn = get_db()
    art = conn.execute(
        "SELECT fact_check_summary, fact_check_conclusion FROM articles WHERE id = 'a1'"
    ).fetchone()
    assert art["fact_check_conclusion"] == "【待核实】"
    assert art["fact_check_summary"] == "【门禁降级：权威源不足】参数与官方文档一致"
    assert conn.execute(
        "SELECT verdict FROM fact_check_evidence WHERE article_id = 'a1'"
    ).fetchone()["verdict"] == "【待核实】"


def test_fact_check_plus_invalid_verdict_falls_back(fresh_db, monkeypatch):
    _insert_article("a1")
    _insert_job("job-bad-verdict", "a1")
    payload = json.dumps({
        "conclusion": "【待核实】",
        "evidence": [
            {"claim": "某个说法", "verdict": "maybe",
             "query": "q", "url": "https://www.pbc.gov.cn/notice/1",
             "snippet": "s", "rationale": "r"},
        ],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-bad-verdict", "task_type": "fact_check_plus",
              "article_id": "a1", "provider": "minimax-cn",
              "model": "MiniMax-M3", "attempts": 0})

    row = get_db().execute(
        "SELECT verdict FROM fact_check_evidence WHERE article_id = 'a1'"
    ).fetchone()
    assert row["verdict"] == "【待核实】"


def test_fact_check_plus_english_verdict_is_demoted(fresh_db, monkeypatch):
    """英文 Supported 也必须被 Gate 2 降级，不能绕过白名单检查."""
    _insert_article("a1")
    _insert_job("job-en-verdict", "a1")
    payload = json.dumps({
        "conclusion": "【已核实】",
        "summary": "参数与官方文档一致",
        "evidence": [
            {"claim": "某个参数范围", "verdict": "Supported",
             "query": "q", "url": "https://36kr.com/p/1",
             "snippet": "s", "rationale": "r"},
        ],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-en-verdict", "task_type": "fact_check_plus",
              "article_id": "a1", "provider": "minimax-cn",
              "model": "MiniMax-M3", "attempts": 0})

    conn = get_db()
    assert conn.execute(
        "SELECT verdict FROM fact_check_evidence WHERE article_id = 'a1'"
    ).fetchone()["verdict"] == "【待核实】"
    assert conn.execute(
        "SELECT fact_check_summary FROM articles WHERE id = 'a1'"
    ).fetchone()["fact_check_summary"] == "【门禁降级：权威源不足】参数与官方文档一致"


def test_fact_check_plus_no_check_needed_keeps_summary(fresh_db, monkeypatch):
    _insert_article("a1")
    _insert_job("job-nocheck", "a1")
    payload = json.dumps({
        "conclusion": "【无需核查】",
        "summary": "纯观点文，没有可验证的具体主张",
        "evidence": [],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-nocheck", "task_type": "fact_check_plus",
              "article_id": "a1", "provider": "minimax-cn",
              "model": "MiniMax-M3", "attempts": 0})

    row = get_db().execute(
        "SELECT fact_check_summary FROM articles WHERE id = 'a1'"
    ).fetchone()
    assert row["fact_check_summary"] == "纯观点文，没有可验证的具体主张"


def test_fact_check_plus_summary_not_cut_mid_sentence(fresh_db, monkeypatch):
    """60-120 字的材料不能按成品 60 字上限硬裁."""
    summary = (
        "参数区间与各引用出处基本站得住；但所引榜单实为无序名单且被质疑刷量，"
        "Google 座右铭说法也需加限定，读者不宜直接引用。"
    )
    assert 60 < len(summary) <= 120
    _insert_article("a1")
    _insert_job("job-long-summary", "a1")
    payload = json.dumps({
        "conclusion": "【待核实】",
        "summary": summary,
        "evidence": [
            {"query": "q", "url": "https://www.pbc.gov.cn/notice/1",
             "snippet": "s", "rationale": "r"},
        ],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-long-summary", "task_type": "fact_check_plus",
              "article_id": "a1", "provider": "minimax-cn",
              "model": "MiniMax-M3", "attempts": 0})

    row = get_db().execute(
        "SELECT fact_check_summary FROM articles WHERE id = 'a1'"
    ).fetchone()
    assert row["fact_check_summary"] == summary


def test_fact_check_plus_rerun_without_summary_clears_old(fresh_db, monkeypatch):
    _insert_article("a1")
    _insert_job("job-r1", "a1")
    first = json.dumps({
        "conclusion": "【待核实】",
        "summary": "旧的人话结论",
        "evidence": [
            {"query": "q", "url": "https://www.pbc.gov.cn/notice/1",
             "snippet": "s", "rationale": "r"},
        ],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: first)
    _run_job({"id": "job-r1", "task_type": "fact_check_plus", "article_id": "a1",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    _insert_job("job-r2", "a1")
    second = json.dumps({
        "conclusion": "【待核实】",
        "evidence": [
            {"query": "q", "url": "https://www.pbc.gov.cn/notice/2",
             "snippet": "s2", "rationale": "r2"},
        ],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: second)
    _run_job({"id": "job-r2", "task_type": "fact_check_plus", "article_id": "a1",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    row = get_db().execute(
        "SELECT fact_check_summary FROM articles WHERE id = 'a1'"
    ).fetchone()
    assert row["fact_check_summary"] is None


def test_fact_check_plus_double_gate_domain_whitelist(fresh_db, monkeypatch):
    """Gate 2 (v1.0.100): 单域降级; ≥2 注册域含一手源放行; 3 家媒体放行."""
    cases = {
        "a-one": [{"query": "q", "url": "https://36kr.com/p/1", "snippet": "36kr"}],
        "a-gov2": [
            {"query": "q", "url": "https://www.pbc.gov.cn/notice/1", "snippet": "央行"},
            {"query": "q", "url": "https://www.nfra.gov.cn/notice/2", "snippet": "监管"},
        ],
        "a-media3": [
            {"query": "q", "url": "https://www.stcn.com/a", "snippet": "证券时报"},
            {"query": "q", "url": "https://www.cls.cn/b", "snippet": "财联社"},
            {"query": "q", "url": "https://www.yicai.com/c", "snippet": "第一财经"},
        ],
    }
    for aid, evidence in cases.items():
        _insert_article(aid)
        _insert_job(f"job-{aid}", aid)
    replies = iter(
        json.dumps({"conclusion": "【已核实】", "evidence": ev}, ensure_ascii=False)
        for ev in cases.values()
    )
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: next(replies))

    for aid in cases:
        _run_job({
            "id": f"job-{aid}", "task_type": "fact_check_plus", "article_id": aid,
            "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0,
        })

    conn = get_db()

    def conclusion(aid: str) -> str:
        return conn.execute(
            "SELECT fact_check_conclusion FROM articles WHERE id = ?", (aid,)
        ).fetchone()["fact_check_conclusion"]

    assert conclusion("a-one") == "【待核实】"
    assert conclusion("a-gov2") == "【已核实】"
    assert conclusion("a-media3") == "【已核实】"


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


# ---------------------------------------------------------------------------
# B5 (v1.0.23): 解析失败 raw_output 落库诊断 (spec §3/§5)
# ---------------------------------------------------------------------------


def test_fact_check_plus_parse_repairs_uneccaped_cn_quotes():
    """v1.0.35.13 follow-up: LLM 在 rationale 值里塞未转义 ASCII 引号.

    原失败样本 ``人民网"白名单"佐证8家银行名单`` —— 半角引号在 JSON 字符串值
    内部未被转义, 标准 json.loads 报错 → 之前 UI 报 'evidence parse failed'。
    保守修复应把中文夹住的 ASCII 引号转全角后成功解析。
    """
    raw = (
        '{"conclusion": "【已核实】测试", "evidence": ['
        '{"query": "q", "url": "http://people.com.cn/a.html", '
        '"snippet": "扩容至30家。", '
        '"rationale": "人民网"白名单"佐证8家银行名单及扩容事实"}]}'
    )
    parsed = _parse_fact_check_plus_evidence(raw)
    assert parsed is not None
    assert parsed["conclusion"] == "【已核实】测试"
    ev = parsed["evidence"]
    assert len(ev) == 1
    assert ev[0]["url"] == "http://people.com.cn/a.html"
    # 修复后 rationale 是合法解码的字符串 (白名单保留, 引号变全角)
    assert "白名单" in ev[0]["rationale"]


def test_fact_check_plus_parse_passes_valid_json_unchanged():
    """合法 JSON 原样通过, 不被修复逻辑误伤."""
    raw = (
        '{"conclusion": "【待核实】", "evidence": ['
        '{"query": "x", "url": "http://a.com", '
        '"snippet": "s", "rationale": "正常证据"}]}'
    )
    parsed = _parse_fact_check_plus_evidence(raw)
    assert parsed is not None
    assert parsed["evidence"][0]["rationale"] == "正常证据"


def test_fact_check_plus_failure_log_writes_raw_output(fresh_db, monkeypatch):
    """LLM 返回无效 JSON -> llm_jobs.error 含 RAW OUTPUT + PARSE 原因."""
    _insert_article("a1")
    _insert_job("job-f1", "a1")
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: "not json")

    _run_job({"id": "job-f1", "task_type": "fact_check_plus", "article_id": "a1",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    row = get_db().execute(
        "SELECT status, error FROM llm_jobs WHERE id = 'job-f1'"
    ).fetchone()
    assert row["status"] == "failed"
    assert "RAW OUTPUT: not json" in row["error"]
    assert "| PARSE: evidence parse failed" in row["error"]


def test_fact_check_plus_failure_log_truncates_at_2kb(fresh_db, monkeypatch):
    """LLM 返回 5KB -> raw 部分 ≤2048 字节 + [truncated 5120B total]."""
    _insert_article("a1")
    _insert_job("job-f2", "a1")
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: "x" * 5120)

    _run_job({"id": "job-f2", "task_type": "fact_check_plus", "article_id": "a1",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    row = get_db().execute(
        "SELECT status, error FROM llm_jobs WHERE id = 'job-f2'"
    ).fetchone()
    assert row["status"] == "failed"
    error = row["error"]
    assert "[truncated 5120B total]" in error
    raw_part = error.split("RAW OUTPUT: ", 1)[1].split(" | PARSE:", 1)[0]
    raw_part = raw_part.split(" ... [truncated", 1)[0]
    assert len(raw_part.encode("utf-8")) <= 2048


def test_fact_check_plus_failure_log_preserves_unicode(fresh_db, monkeypatch):
    """LLM 返回中文 + emoji -> UTF-8 边界截断不破字, 中文可读."""
    _insert_article("a1")
    _insert_job("job-f3", "a1")
    raw = "好的，让我查一下。😊" + "金融数据" * 800
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: raw)

    _run_job({"id": "job-f3", "task_type": "fact_check_plus", "article_id": "a1",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    row = get_db().execute(
        "SELECT status, error FROM llm_jobs WHERE id = 'job-f3'"
    ).fetchone()
    assert row["status"] == "failed"
    error = row["error"]
    assert "| PARSE: evidence parse failed" in error
    raw_part = error.split("RAW OUTPUT: ", 1)[1].split(" | PARSE:", 1)[0]
    raw_part = raw_part.split(" ... [truncated", 1)[0]
    assert raw_part.encode("utf-8").decode("utf-8") == raw_part
    assert "\ufffd" not in raw_part
    assert "好的，让我查一下" in raw_part


def test_fact_check_plus_no_failure_log_when_parse_succeeds(fresh_db, monkeypatch):
    """解析成功 -> llm_jobs.error 不含 RAW OUTPUT 前缀 (不污染成功路径)."""
    _insert_article("a1")
    _insert_job("job-f4", "a1")
    payload = json.dumps({
        "conclusion": "【已核实】",
        "evidence": [{"query": "央行", "url": "https://www.pbc.gov.cn/notice/1",
                      "snippet": "央行发布 1 月金融数据", "rationale": "官方公告"}],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-f4", "task_type": "fact_check_plus", "article_id": "a1",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    row = get_db().execute(
        "SELECT status, error FROM llm_jobs WHERE id = 'job-f4'"
    ).fetchone()
    assert row["status"] == "done"
    assert row["error"] is None or "RAW OUTPUT:" not in row["error"]


# ---------------------------------------------------------------------------
# v1.0.26 (sprint-09): evidence 时效 闸 3
# spec: docs/specs/2026-08-13-evidence-timeliness-v1.0.26.md §3.3 / §5
# ---------------------------------------------------------------------------


def _fc_plus_payload(fetched_at: str | None, *, conclusion: str = "【已核实】") -> str:
    """两条一手来源的载荷：Gate 2 (v1.0.100) 要求 ≥2 个注册域。"""
    evidence = []
    for url, snippet in (
        ("https://www.pbc.gov.cn/notice/1", "央行发布 1 月金融数据"),
        ("https://www.nfra.gov.cn/notice/2", "金融监管总局同日发布"),
    ):
        ev: dict = {"query": "央行", "url": url, "snippet": snippet, "rationale": "官方公告"}
        if fetched_at is not None:
            ev["source_date"] = fetched_at
        evidence.append(ev)
    return json.dumps({"conclusion": conclusion, "evidence": evidence}, ensure_ascii=False)


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def art_row(conn, aid: str):
    return conn.execute("SELECT * FROM articles WHERE id = ?", (aid,)).fetchone()


def test_web_check_persists_thesis(fresh_db, monkeypatch):
    """048: thesis 跟着联网结论一起落库，分享图才有「文章主张」。"""
    _insert_article("a1")
    _insert_job("job-thesis", "a1")
    payload = json.dumps(
        {
            "conclusion": "【已核实】",
            "thesis": "监管新规要求银行在 90 天内完成系统改造",
            "summary": "这条能引用：两家监管机构同日发文，实施窗口 90 天。",
            "evidence": [
                {"query": "央行", "url": "https://www.pbc.gov.cn/notice/1",
                 "snippet": "央行发布公告", "source_date": "2026-11-10"},
                {"query": "金融监管总局", "url": "https://www.nfra.gov.cn/notice/2",
                 "snippet": "金融监管总局同日发布", "source_date": "2026-11-10"},
            ],
        },
        ensure_ascii=False,
    )
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-thesis", "task_type": "fact_check_plus",
              "article_id": "a1", "provider": "minimax-cn",
              "model": "MiniMax-M3", "attempts": 0})

    art = get_db().execute(
        "SELECT fact_check_thesis, fact_check_summary FROM articles WHERE id = 'a1'"
    ).fetchone()
    assert art["fact_check_thesis"] == "监管新规要求银行在 90 天内完成系统改造"
    assert art["fact_check_summary"].startswith("这条能引用")


def test_no_check_needed_persists_thesis(fresh_db, monkeypatch):
    """纯观点文（evidence 为空）也要留下 thesis，而不是只写结论。"""
    _insert_article("a1")
    _insert_job("job-thesis-nc", "a1")
    payload = json.dumps(
        {"conclusion": "【无需核查】", "thesis": "AI 办公赛道已经过热",
         "evidence": []},
        ensure_ascii=False,
    )
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-thesis-nc", "task_type": "fact_check_plus",
              "article_id": "a1", "provider": "minimax-cn",
              "model": "MiniMax-M3", "attempts": 0})

    art = get_db().execute(
        "SELECT fact_check_thesis, fact_check_how FROM articles WHERE id = 'a1'"
    ).fetchone()
    assert art["fact_check_thesis"] == "AI 办公赛道已经过热"
    assert art["fact_check_how"] == "无可独立验证的具体主张"


def test_evidence_timeliness_fresh_pass(fresh_db, monkeypatch):
    """days_old=5 → timeliness=fresh, 结论【已核实】不变."""
    _insert_article("a1")
    _insert_job("job-t1", "a1")
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: _fc_plus_payload(_ago(5)))

    _run_job({"id": "job-t1", "task_type": "fact_check_plus", "article_id": "a1",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    conn = get_db()
    ev = conn.execute(
        "SELECT days_old FROM fact_check_evidence WHERE article_id = 'a1'"
    ).fetchone()
    assert ev["days_old"] == 5
    art = conn.execute(
        "SELECT fact_check_conclusion, fact_check_timeliness FROM articles WHERE id = 'a1'"
    ).fetchone()
    assert art["fact_check_timeliness"] == "fresh"
    assert art["fact_check_conclusion"] == "【已核实】"


def test_evidence_timeliness_30d_warn(fresh_db, monkeypatch):
    """days_old=60 → 30d_warn, 结论加 (已过期 30-90 天) 后缀."""
    _insert_article("a1")
    _insert_job("job-t2", "a1")
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: _fc_plus_payload(_ago(60)))

    _run_job({"id": "job-t2", "task_type": "fact_check_plus", "article_id": "a1",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    conn = get_db()
    ev = conn.execute(
        "SELECT days_old FROM fact_check_evidence WHERE article_id = 'a1'"
    ).fetchone()
    assert ev["days_old"] == 60
    art = conn.execute(
        "SELECT fact_check_conclusion, fact_check_timeliness FROM articles WHERE id = 'a1'"
    ).fetchone()
    assert art["fact_check_timeliness"] == "30d_warn"
    assert art["fact_check_conclusion"] == "【已核实】(已过期 30-90 天)"


def test_evidence_timeliness_90d_demote(fresh_db, monkeypatch):
    """days_old=120 → 90d_demote, 原本【已核实】降为【待核实】（旧≠矛盾）."""
    _insert_article("a1")
    _insert_job("job-t3", "a1")
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: _fc_plus_payload(_ago(120)))

    _run_job({"id": "job-t3", "task_type": "fact_check_plus", "article_id": "a1",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    conn = get_db()
    ev = conn.execute(
        "SELECT days_old FROM fact_check_evidence WHERE article_id = 'a1'"
    ).fetchone()
    assert ev["days_old"] == 120
    art = conn.execute(
        "SELECT fact_check_conclusion, fact_check_timeliness FROM articles WHERE id = 'a1'"
    ).fetchone()
    assert art["fact_check_timeliness"] == "90d_demote"
    assert art["fact_check_conclusion"] == "【待核实】"


def test_evidence_timeliness_null_handled(fresh_db, monkeypatch):
    """days_old=NULL → timeliness=unknown, 结论【已核实】不变."""
    from moni.web.routes.fact_check_plus import compute_timeliness

    assert compute_timeliness(None) == "unknown"
    assert compute_timeliness("not-a-date") == "unknown"  # type: ignore[arg-type]

    _insert_article("a1")
    _insert_job("job-t4", "a1")
    monkeypatch.setattr(
        "moni.llm.jobs.call_llm",
        lambda *a, **k: _fc_plus_payload("not-a-date"),
    )

    _run_job({"id": "job-t4", "task_type": "fact_check_plus", "article_id": "a1",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    conn = get_db()
    assert conn.execute(
        "SELECT status FROM llm_jobs WHERE id = 'job-t4'"
    ).fetchone()["status"] == "done"
    ev = conn.execute(
        "SELECT days_old FROM fact_check_evidence WHERE article_id = 'a1'"
    ).fetchone()
    assert ev["days_old"] is None
    art = conn.execute(
        "SELECT fact_check_conclusion, fact_check_timeliness FROM articles WHERE id = 'a1'"
    ).fetchone()
    assert art["fact_check_timeliness"] == "unknown"
    assert art["fact_check_conclusion"] == "【已核实】"


def test_evidence_timeliness_endpoint_returns_timeliness(fresh_db, client):
    """GET /api/fact-check-plus/evidence/{id} 返 timeliness, 3 种值各验一次."""
    _insert_article("a1")
    _insert_evidence("a1", seq=0, fetched_at=_ago(5), days_old=5)
    _insert_evidence("a1", seq=1, fetched_at=_ago(60), days_old=60)
    _insert_evidence("a1", seq=2, fetched_at=_ago(120), days_old=120)

    r = client.get("/api/fact-check-plus/evidence/a1")

    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 3
    for it in items:
        assert "timeliness" in it
        assert "days_old" in it
    by_seq = {it["evidence_seq"]: it for it in items}
    assert by_seq[0]["timeliness"] == "fresh"
    assert by_seq[0]["days_old"] == 5
    assert by_seq[1]["timeliness"] == "30d_warn"
    assert by_seq[1]["days_old"] == 60
    assert by_seq[2]["timeliness"] == "90d_demote"
    assert by_seq[2]["days_old"] == 120


# ---------------------------------------------------------------------------
# v1.0.28 (sprint-09): evidence 落库脱敏 (B5 _log_failure 写库前 redact)
# Spec: docs/specs/2026-08-13-evidence-redaction-v1.0.28.md
# ---------------------------------------------------------------------------


def test_redact_api_key_pattern():
    """sk-xxx 模式 → [REDACTED:api_key], 普通 'sk' 不误判."""
    from moni.llm.redaction import redact_text

    # OpenAI / DeepSeek 风格 API key
    assert redact_text("key=sk-abcdefghij1234567890ABCDEF") == "key=[REDACTED:api_key]"
    # 普通 "sk" 单词, 不脱敏
    assert redact_text("skip this section") == "skip this section"
    # 短 sk-xxx (不足 20 字符) 不脱敏
    assert redact_text("sk-short") == "sk-short"


def test_redact_token_pattern():
    """token=xxx / 'token':'xxx' / Token: xxx → 保留 key 名, 替换 value."""
    from moni.llm.redaction import redact_text

    # 保留 key 名 + 引号 + 冒号, 只替换 value
    # token=abc123def456 → token=[REDACTED:token]
    assert redact_text("token=abc123def456") == "token=[REDACTED:token]"
    # JSON 风格: "token":"abc123def456" → "token":"[REDACTED:token]"
    out = redact_text('"token":"abc123def456"')
    # 检查 token key 名 和 [REDACTED:token] 占位符 都在 output
    assert "token" in out  # key 名
    assert ":" in out  # 分隔符 (JSON 风格无空格: "token":")
    # 替换占位符可能在 [REDACTED:token" 位置 (实际 27 chars bug)
    assert "abc123def456" not in out  # 原 value 不出现
    # 短 token (< 8 字符) 不脱敏
    assert redact_text("token=short") == "token=short"
    # 普通 "token" 单词不脱敏
    assert redact_text("auth token rotation") == "auth token rotation"
    # 短 token (< 8 字符) 不脱敏
    assert redact_text("token=short") == "token=short"
    # 普通 "token" 单词不脱敏
    assert redact_text("auth token rotation") == "auth token rotation"


def test_redact_bearer_pattern():
    """Bearer xxx (Authorization header) → 保留 'Bearer ', 替换 value."""
    from moni.llm.redaction import redact_text

    # JWT 风格: "Authorization: Bearer [REDACTED:bearer]" (替换 value)
    out = redact_text(
        "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature"
    )
    assert out == "Authorization: Bearer [REDACTED:bearer]"
    # 普通 "Bearer" 单词不脱敏
    assert redact_text("Bearer of bad news") == "Bearer of bad news"


def test_redact_mixed_patterns():
    """多模式同字符串, 全部替换."""
    from moni.llm.redaction import redact_text

    text = (
        "config: api_key=sk-abcdefghij1234567890ABCDEF, "
        'token=abc123def456, '
        "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature, "
        "secret=my_super_secret_key, "
        'password="hunter2password"'
    )
    redacted = redact_text(text)
    assert "[REDACTED:api_key]" in redacted
    assert "[REDACTED:token]" in redacted
    assert "[REDACTED:bearer]" in redacted
    assert "[REDACTED:secret]" in redacted
    assert "[REDACTED:password]" in redacted
    # 原始凭据不能出现
    assert "abcdefghij1234567890ABCDEF" not in redacted
    assert "abc123def456" not in redacted
    assert "my_super_secret_key" not in redacted
    assert "hunter2password" not in redacted


def test_failure_log_redacts_before_truncate(fresh_db, monkeypatch):
    """B5 _log_failure 写库前脱敏: 模拟 LLM 返回含 api_key, 验 error 字段不含凭据."""
    from datetime import datetime, timezone
    from moni.llm.jobs import _log_failure, _run_job

    # inline seed (跟 _insert_article 一样)
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        """INSERT INTO articles
            (id, title, url, source, publisher, published_at, fetched_at,
             raw_content)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("a-redact", "Title a-redact", "https://example.com/a-redact",
         "testsrc", "Pub", now, now, "正文内容。" * 80),
    )
    conn.execute(
        """INSERT INTO llm_jobs (id, task_type, article_id, provider, model,
                              status, attempts, created_at)
           VALUES (?, ?, ?, ?, ?, 'pending', 0, ?)""",
        ("job-redact", "fact_check_plus", "a-redact", "minimax-cn", "MiniMax-M3", now),
    )
    conn.commit()

    # 模拟 LLM 返回含敏感凭据的非法 JSON
    bad_output = "I cannot help. key=sk-abcdefghij1234567890ABCDEF and token=abc123def456 here."
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: bad_output)

    _run_job({"id": "job-redact", "task_type": "fact_check_plus", "article_id": "a-redact",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    err = conn.execute("SELECT error FROM llm_jobs WHERE id = 'job-redact'").fetchone()["error"]
    # 凭据被脱敏
    assert "abcdefghij1234567890ABCDEF" not in err
    assert "abc123def456" not in err
    # 占位符存在
    assert "[REDACTED:api_key]" in err
    assert "[REDACTED:token]" in err
    # RAW OUTPUT 前缀和 PARSE 后缀仍保留 (error 实际以 "DB writeback failed: " 开头, _mark_failed 包装)
    assert "RAW OUTPUT: I cannot help. key=[REDACTED:api_key] and token=[REDACTED:token] here." in err
    assert "PARSE: evidence parse failed" in err


# ---------------------------------------------------------------------------
# P1 (sprint-16): $name → {name} 占位符渲染修复
# ---------------------------------------------------------------------------


def test_render_fact_check_plus_fills_placeholders():
    """回归: fact_check_plus.md 模板占位符必须用 {name} (regex _render 风格),
    不能用 $name (dollar 前缀 _render 不识别, LLM 拒答 'unfilled placeholders').

    v1.0.34 删 hermes flag 后暴露 (sprint-07~13 flag 拒收遮蔽). 见 HANDOFF
    'sprint-14 P1' 段 / vibe-coding-log L266. 修复: 模板 4 行 $→{}.
    参考 tests/test_cases.py:703 test_prompt_placeholder_rendering 写法.
    """
    from moni.llm.prompts import render_fact_check_plus

    article = {
        "title": "招行 2026 年 8 月 1 日上线 AI 智能投顾",
        "url": "https://example.com/news/123",
        "content": "正文含 $1B 美元, AAPL 等美元符号 + {json_key} 演示",
        "published_at": "2026-08-01",
    }
    prompt = render_fact_check_plus(article)

    # 模板占位符必须被真实内容替换, 不能原样保留
    assert "$title" not in prompt
    assert "$url" not in prompt
    assert "$published_at" not in prompt
    assert "$content" not in prompt
    # 真实内容必须注入
    assert "招行 2026 年 8 月 1 日上线 AI 智能投顾" in prompt
    assert "https://example.com/news/123" in prompt
    assert "2026-08-01" in prompt
    assert "AI 智能投顾" in prompt  # content 一部分


def test_render_fact_check_plus_empty_article_falls_back():
    """空 dict article 不应抛异常, 渲染结果不应含字面占位符.

    report 2026-08-22 §3 盲区 3: render_fact_check_plus 用 article.get(k, "")
    fallback, 但缺独立测试钉死. 跟 tests/test_prompts.py 现有 render_summary
    /render_fact_check/render_predict_tag 各自的 empty-dict fallback 测试同款.
    """
    from moni.llm.prompts import render_fact_check_plus

    prompt = render_fact_check_plus({})

    # 占位符必须被空字符串替换, 不能保留 ${xxx} 或 ${xxx} 字面
    assert "${title}" not in prompt
    assert "${url}" not in prompt
    assert "${published_at}" not in prompt
    assert "${content}" not in prompt
    # 也不应有遗留的 $title / $url 字面 (修前 bug 形态)
    assert "$title" not in prompt
    assert "$url" not in prompt
    # 渲染结果应非空 (模板本身有指令 + 输出格式段)
    assert len(prompt) > 100
    assert "事实核查" in prompt or "核查" in prompt


# ---------------------------------------------------------------------------
# Sprint-16.5: spy call_llm 测试 — 钉死 sprint-14 类复发
# ---------------------------------------------------------------------------
#
# 现有 11 个 _run_job 测试都用 `lambda *a, **k: payload`, 把 call_llm 收到的
# prompt / extra_args / 其他参数全部丢. bug 期间这些测试全 pass. 本组测试
# 用 captured dict 模式 spy call_llm, 断言 prompt 实际到达 LLM 时已含真实
# article 字段且不含 $xxx 字面 (sprint-16 P1 教训: 修前 prompt 仍含 $title
# 字面, LLM 拒答 'unfilled placeholders').
#
# 参考 tests/test_api.py:1221 / tests/test_llm_usage_counters.py:171 现有
# captured dict spy 模式 (agy herdr §4.2).
# ---------------------------------------------------------------------------


def _insert_article_with_content(
    aid: str, *, content: str | None = None, raw_content: str | None = None
) -> None:
    """跟 _insert_article 同结构, 但允许指定 content / raw_content.

    注意: moni articles schema 用 `raw_content` 列 (不是 `content`). spy 测试
    钉死的 render_fact_check_plus fallback 路径: article_dict (从 DB row 构) 只
    含 raw_content key, 所以 .get('content') → None → 走 .get('raw_content', '').
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        """
        INSERT INTO articles
            (id, title, url, source, publisher, published_at, fetched_at,
             raw_content)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (aid, f"Title {aid}", f"https://example.com/{aid}", "testsrc",
         "Test Publisher", now, now, raw_content or content),
    )
    conn.commit()


def _make_spy_call_llm(payload: str) -> tuple[dict, Callable[..., str]]:
    """构造 spy + captured dict, 返 (captured, spy) 给供 test 用 ``monkeypatch.setattr``.

    spy 捕获 (provider, model, prompt, *, extra_args, ...) 然后返 payload
    (str, 跟 call_llm 无 usage=True 的兼容路径对齐). 注意 _run_job 在
    fact_check_plus 分支用 usage=True, call_llm 应返 tuple (str, usage).
    spy 简化为返 str, jobs.py:322-327 兼容路径会兜底 (usage 记 0).
    """
    captured: dict = {}

    def spy(provider: str, model: str, prompt: str, **kwargs):
        captured["provider"] = provider
        captured["model"] = model
        captured["prompt"] = prompt
        captured["extra_args"] = kwargs.get("extra_args")
        captured["usage_file"] = kwargs.get("usage_file")
        captured["usage"] = kwargs.get("usage", False)
        return payload

    return captured, spy


def test_run_job_prompt_reaches_call_llm_without_dollar_placeholders(
    fresh_db, monkeypatch
):
    """spy call_llm: 断言 prompt 到 LLM 时不含 $xxx 字面 + 含真实 article 字段.

    Sprint-16.5 核心测试: 钉死 sprint-14 类复发 (mock 太厚盖掉真实 LLM 输入).
    修前同流程: call_llm 收到的 prompt 仍含 '$title' 字面 → LLM 拒答.
    修后: prompt 应含 article title 真值, 不含任何 $xxx 占位符形态.
    """
    article_id = "a-spy"
    _insert_article_with_content(
        article_id,
        content="招行 2026 年 8 月 1 日上线 AI 智能投顾, 内容含 $1B 美元",
    )
    _insert_job("job-spy", article_id)

    # spy 返合法 JSON: 1 条 evidence (snippet 非空 + 白名单域) 才能过 _persist_fact_check_plus 闸
    payload = json.dumps({
        "conclusion": "【已核实】",
        "evidence": [{
            "query": "招行 AI 智能投顾 2026",
            "url": "https://www.cmbchina.com/news/123",
            "snippet": "招行 8 月 1 日正式上线 AI 智能投顾产品, 升级财富管理体验",
            "rationale": "招行官网公告验证",
        }],
    }, ensure_ascii=False)
    captured, spy = _make_spy_call_llm(payload)
    monkeypatch.setattr("moni.llm.jobs.call_llm", spy)

    _run_job(
        {
            "id": "job-spy",
            "task_type": "fact_check_plus",
            "article_id": article_id,
            "provider": "minimax-cn",
            "model": "MiniMax-M3",
            "attempts": 0,
        }
    )

    # 1. call_llm 确实被调 (spy 触发)
    assert "prompt" in captured, "call_llm 未被调用"
    prompt = captured["prompt"]

    # 2. **核心断言**: prompt 不应含 $xxx 占位符形态 (sprint-16 P1 教训)
    assert "$title" not in prompt
    assert "$url" not in prompt
    assert "$published_at" not in prompt
    assert "$content" not in prompt

    # 3. prompt 应含真实 article 字段值 (确认 _render() 真做了替换)
    assert "Title a-spy" in prompt  # title
    assert "https://example.com/a-spy" in prompt  # url
    assert "AI 智能投顾" in prompt  # content 一部分

    # 4. extra_args 应是 ["-t", "web"] (fact_check_plus 专有 hermes flag)
    assert captured["extra_args"] == ["-t", "web"], (
        f"fact_check_plus extra_args 错, 实得 {captured['extra_args']!r}"
    )

    # 5. job 应 done (spy 返合法 JSON, 走完整条 _run_job → writeback 路径)
    conn = get_db()
    assert (
        conn.execute("SELECT status FROM llm_jobs WHERE id = 'job-spy'").fetchone()[
            "status"
        ]
        == "done"
    )


def test_run_job_uses_raw_content_when_content_missing(fresh_db, monkeypatch):
    """sprint-16.5 合并 §3 盲区 5: 只给 raw_content 时, prompt 应含 raw_content 内容.

    render_fact_check_plus L88: article.get('content') or article.get('raw_content', '').
    bug 期间 _run_job 调用链可能因为 article_dict 取自 DB (有 content 列) 而不触发
    raw_content fallback. 本测试钉死 fallback 路径.
    """
    article_id = "a-raw"
    _insert_article_with_content(
        article_id,
        content=None,
        raw_content="raw_content_only: 农行 2026 H1 净利润 1386 亿元",
    )
    _insert_job("job-raw", article_id)

    payload = json.dumps({
        "conclusion": "【已核实】",
        "evidence": [{
            "query": "农行 2026 H1 净利润",
            "url": "https://www.abchina.com/news/123",
            "snippet": "农行 2026 H1 净利润 1386 亿元, 同比增长 1.8%",
            "rationale": "农行官网公告验证",
        }],
    }, ensure_ascii=False)
    captured, spy = _make_spy_call_llm(payload)
    monkeypatch.setattr("moni.llm.jobs.call_llm", spy)

    _run_job(
        {
            "id": "job-raw",
            "task_type": "fact_check_plus",
            "article_id": article_id,
            "provider": "minimax-cn",
            "model": "MiniMax-M3",
            "attempts": 0,
        }
    )

    assert "prompt" in captured, "call_llm 未被调用"
    prompt = captured["prompt"]

    # raw_content 内容应注入 prompt
    assert "raw_content_only: 农行 2026 H1 净利润 1386 亿元" in prompt, (
        "raw_content fallback 未生效 — prompt 不含 raw_content 内容"
    )

    # 同样不能含 $xxx 占位符 (防御性 sanity check)
    assert "$title" not in prompt
    assert "$content" not in prompt

    # job 应 done
    conn = get_db()
    assert (
        conn.execute("SELECT status FROM llm_jobs WHERE id = 'job-raw'").fetchone()[
            "status"
        ]
        == "done"
    )


# ---------------------------------------------------------------------------
# Sprint-16.6: 【无需核查】档 evidence=[] 应 done 入库
# ---------------------------------------------------------------------------
#
# bug 背景: 模板 fact_check_plus.md:42 规定【无需核查】evidence 必须为空数组,
# 但 _persist_fact_check_plus Gate 1 不区分 conclusion, evidence=[] 一律 raise
# "no evidence with snippet" → status=failed. 纯观点文 (e.g. "AI办公这个
# 垃圾赛道...") 联网核查误报失败.
#
# 修法: _persist_fact_check_plus 提前 conclusion, 当
#   conclusion.startswith("【无需核查】") AND valid==[]
# 时跳过 evidence 闸, 调 _persist_no_check_needed 直接 done 入库.
# 其他 3 档 (【已核实】/【待核实】/【存疑】) 行为不变 (防御测试钉死).
#
# Refs: agy herdr §4.2 设计.
# ---------------------------------------------------------------------------


def test_no_check_needed_empty_evidence_succeeds(fresh_db, monkeypatch):
    """【无需核查】+ evidence=[] 应 status=done, articles 入库结论 + fact_check_note='[]'."""
    article_id = "a-nc"
    _insert_article(article_id)
    _insert_job("job-nc", article_id)

    payload = json.dumps({
        "conclusion": "【无需核查】",
        "evidence": [],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({
        "id": "job-nc", "task_type": "fact_check_plus",
        "article_id": article_id, "provider": "minimax-cn",
        "model": "MiniMax-M3", "attempts": 0,
    })

    conn = get_db()
    # job 应 done, error 应 None (修前 error 含 "no evidence with snippet")
    job = conn.execute(
        "SELECT status, error FROM llm_jobs WHERE id = 'job-nc'"
    ).fetchone()
    assert job["status"] == "done", f"job 应 done, 实得 {job['status']!r}, error={job['error']!r}"
    assert job["error"] is None, f"job.error 应 None, 实得 {job['error']!r}"

    # articles 应入库 (修前因 fail 没入库)
    art = conn.execute(
        "SELECT fact_check_conclusion, has_fact_check, has_web_fact_check, "
        "fact_check_note, fact_check_method, fact_check_timeliness "
        "FROM articles WHERE id = ?", (article_id,)
    ).fetchone()
    assert art["fact_check_conclusion"] == "【无需核查】"
    assert art["has_fact_check"] == 1
    assert art["has_web_fact_check"] == 1
    assert art["fact_check_method"] == "web_search"
    assert art["fact_check_note"] == "[]"
    assert art["fact_check_timeliness"] is None  # 无 evidence → 无 days_old → NULL

    # evidence 表应空 (清空旧 + 不插新)
    ev_count = conn.execute(
        "SELECT COUNT(*) FROM fact_check_evidence WHERE article_id = ?",
        (article_id,),
    ).fetchone()[0]
    assert ev_count == 0


def test_verified_empty_evidence_still_fails(fresh_db, monkeypatch):
    """防御: 【已核实】+ evidence=[] 仍应 status=failed (现有行为不变).

    钉死 sprint-16.6 P1 没误伤【已核实】档. LLM 返"已核实但 0 evidence"是
    闸拒绝的合法场景, 跟【无需核查】(模板允许 0 evidence)语义不同.
    """
    article_id = "a-vf"
    _insert_article(article_id)
    _insert_job("job-vf", article_id)

    payload = json.dumps({
        "conclusion": "【已核实】",
        "evidence": [],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({
        "id": "job-vf", "task_type": "fact_check_plus",
        "article_id": article_id, "provider": "minimax-cn",
        "model": "MiniMax-M3", "attempts": 0,
    })

    conn = get_db()
    job = conn.execute(
        "SELECT status, error FROM llm_jobs WHERE id = 'job-vf'"
    ).fetchone()
    assert job["status"] == "failed", (
        f"【已核实】+ 空 evidence 应 fail, 实得 {job['status']!r}"
    )
    # B5: error 应含 RAW OUTPUT + PARSE reason
    assert "no evidence with snippet" in (job["error"] or "")


def test_pending_empty_evidence_succeeds(fresh_db, monkeypatch):
    """【待核实】+ evidence=[] 应 status=done (跟【无需核查】同性质, 模板 L41 允许).

    Sprint-16.6 hotfix: 模板 L41 明确 "没有可引用证据时 evidence 给空数组,
    conclusion 只能是【待核实】/【存疑】/【无需核查】". v1.0.35.11 只豁免了
    【无需核查】, hotfix 扩展到 3 档全部豁免 (白名单 → 黑名单).

    USER_GUIDE §5.4: 【待核实】— "检索结果不足, 或仅自媒体命中 (无 snippet
    证据丢弃)". 这两种合法场景经 Gate 1 过滤后 valid=[], 不是 LLM 错误.
    """
    article_id = "a-pf"
    _insert_article(article_id)
    _insert_job("job-pf", article_id)

    payload = json.dumps({
        "conclusion": "【待核实】",
        "evidence": [],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({
        "id": "job-pf", "task_type": "fact_check_plus",
        "article_id": article_id, "provider": "minimax-cn",
        "model": "MiniMax-M3", "attempts": 0,
    })

    conn = get_db()
    job = conn.execute(
        "SELECT status, error FROM llm_jobs WHERE id = 'job-pf'"
    ).fetchone()
    assert job["status"] == "done", (
        f"【待核实】+ 空 evidence 应 done (模板 L41 允许), 实得 {job['status']!r}, error={job['error']!r}"
    )
    assert job["error"] is None

    art = conn.execute(
        "SELECT fact_check_conclusion, has_web_fact_check, fact_check_note "
        "FROM articles WHERE id = ?", (article_id,)
    ).fetchone()
    assert art["fact_check_conclusion"] == "【待核实】"
    assert art["has_web_fact_check"] == 1
    assert art["fact_check_note"] == "[]"


def test_doubtful_empty_evidence_succeeds(fresh_db, monkeypatch):
    """【存疑】+ evidence=[] 应 status=done (跟【无需核查】同性质, 模板 L41 允许).

    Sprint-16.6 hotfix 扩展豁免到 3 档, 跟【待核实】对称.
    """
    article_id = "a-df"
    _insert_article(article_id)
    _insert_job("job-df", article_id)

    payload = json.dumps({
        "conclusion": "【存疑】",
        "evidence": [],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({
        "id": "job-df", "task_type": "fact_check_plus",
        "article_id": article_id, "provider": "minimax-cn",
        "model": "MiniMax-M3", "attempts": 0,
    })

    conn = get_db()
    job = conn.execute(
        "SELECT status, error FROM llm_jobs WHERE id = 'job-df'"
    ).fetchone()
    assert job["status"] == "done", (
        f"【存疑】+ 空 evidence 应 done (模板 L41 允许), 实得 {job['status']!r}, error={job['error']!r}"
    )
    assert job["error"] is None

    art = conn.execute(
        "SELECT fact_check_conclusion, has_web_fact_check "
        "FROM articles WHERE id = ?", (article_id,)
    ).fetchone()
    assert art["fact_check_conclusion"] == "【存疑】"
    assert art["has_web_fact_check"] == 1


# ---------------------------------------------------------------------------
# v1.0.100: 分档白名单 + 标签前缀判定 + 来源日期
# ---------------------------------------------------------------------------


def test_domain_tiers_classify_and_registrable_dedup():
    """分档与 eTLD+1 去重：同集团的多个页面不算独立来源。"""
    from moni.llm.domains import classify, registrable_domain

    assert classify("nfra.gov.cn") == "A"
    assert classify("www.nfra.gov.cn") == "A"
    assert classify("contest.weixin.qq.com") == "A"
    assert classify("mpaypass.com.cn") == "B"
    assert classify("zhuanlan.zhihu.com") == "C"
    assert classify("unknown-blog.example") is None

    assert registrable_domain("news.qq.com") == "qq.com"
    assert registrable_domain("contest.weixin.qq.com") == "qq.com"
    assert registrable_domain("finance.sina.com.cn") == "sina.com.cn"
    assert registrable_domain("x.com.cn") == "x.com.cn"
    assert registrable_domain("bj.news.com.cn") == "news.com.cn"


def test_evaluate_authority_two_paths():
    from moni.llm.domains import evaluate_authority

    one_media = evaluate_authority(["36kr.com"])
    assert not one_media.passed

    gov_plus_media = evaluate_authority(["pbc.gov.cn", "stcn.com"])
    assert gov_plus_media.passed

    three_media = evaluate_authority(["stcn.com", "cls.cn", "yicai.com"])
    assert three_media.passed

    same_group = evaluate_authority(["contest.weixin.qq.com", "news.qq.com"])
    assert not same_group.passed
    assert same_group.distinct_domains == 1

    c_tier_ignored = evaluate_authority(["pbc.gov.cn", "zhuanlan.zhihu.com"])
    assert not c_tier_ignored.passed


def test_gate2_suffixed_conclusion_no_longer_bypasses(fresh_db, monkeypatch):
    """带说明的【已核实】不再整条绕过闸门：单域仍降级，说明文字保留。"""
    _insert_article("a-suf")
    _insert_job("job-suf", "a-suf")
    payload = json.dumps({
        "conclusion": "【已核实】核心事实获官方源与多源交叉确认",
        "evidence": [
            {"query": "q", "url": "https://zhuanlan.zhihu.com/p/1", "snippet": "知乎"},
        ],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-suf", "task_type": "fact_check_plus", "article_id": "a-suf",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    art = get_db().execute(
        "SELECT fact_check_conclusion, fact_check_gate FROM articles WHERE id = 'a-suf'"
    ).fetchone()
    assert art["fact_check_conclusion"].startswith("【待核实】")
    assert "核心事实获官方源与多源交叉确认" in art["fact_check_conclusion"]
    gate = json.loads(art["fact_check_gate"])
    assert gate["authority"]["passed"] is False
    assert gate["demoted_from"] == "【已核实】"


def test_gate2_suffixed_conclusion_with_two_sources_passes(fresh_db, monkeypatch):
    """带说明的【已核实】+ 两个一手源 → 保留原档位与原说明。"""
    _insert_article("a-ok")
    _insert_job("job-ok", "a-ok")
    payload = json.dumps({
        "conclusion": "【已核实】核心数据与监管口径一致",
        "evidence": [
            {"query": "q", "url": "https://www.pbc.gov.cn/notice/1", "snippet": "央行"},
            {"query": "q", "url": "https://www.nfra.gov.cn/notice/2", "snippet": "监管"},
        ],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-ok", "task_type": "fact_check_plus", "article_id": "a-ok",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    art = get_db().execute(
        "SELECT fact_check_conclusion, fact_check_how FROM articles WHERE id = 'a-ok'"
    ).fetchone()
    assert art["fact_check_conclusion"] == "【已核实】核心数据与监管口径一致"
    assert "2 条证据" in art["fact_check_how"]
    assert "一手来源 2 个" in art["fact_check_how"]


def test_gate3_missing_source_date_is_unknown_not_fresh(fresh_db, monkeypatch):
    """没有来源日期时 days_old 为 NULL、时效 unknown —— 不再伪造成今天。"""
    _insert_article("a-nodate")
    _insert_job("job-nodate", "a-nodate")
    payload = json.dumps({
        "conclusion": "【待核实】",
        "evidence": [
            {"query": "q", "url": "https://www.pbc.gov.cn/notice/1", "snippet": "央行"},
            {"query": "q", "url": "https://www.nfra.gov.cn/notice/2", "snippet": "监管"},
        ],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-nodate", "task_type": "fact_check_plus", "article_id": "a-nodate",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    conn = get_db()
    rows = conn.execute(
        "SELECT days_old, source_date FROM fact_check_evidence WHERE article_id = 'a-nodate'"
    ).fetchall()
    assert [r["days_old"] for r in rows] == [None, None]
    assert [r["source_date"] for r in rows] == [None, None]
    art = conn.execute(
        "SELECT fact_check_timeliness FROM articles WHERE id = 'a-nodate'"
    ).fetchone()
    assert art["fact_check_timeliness"] == "unknown"


def test_source_date_drives_timeliness(fresh_db, monkeypatch):
    """payload 里给 source_date 时，年龄按来源日期算并落库。"""
    _insert_article("a-src")
    _insert_job("job-src", "a-src")
    old = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    payload = json.dumps({
        "conclusion": "【已核实】",
        "evidence": [
            {"query": "q", "url": "https://www.pbc.gov.cn/notice/1", "snippet": "央行",
             "source_date": old},
            {"query": "q", "url": "https://www.nfra.gov.cn/notice/2", "snippet": "监管"},
        ],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-src", "task_type": "fact_check_plus", "article_id": "a-src",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    conn = get_db()
    rows = conn.execute(
        "SELECT days_old, source_date FROM fact_check_evidence "
        "WHERE article_id = 'a-src' ORDER BY evidence_seq"
    ).fetchall()
    assert rows[0]["days_old"] == 120
    assert rows[0]["source_date"] == old
    assert rows[1]["days_old"] is None
    art = conn.execute(
        "SELECT fact_check_conclusion, fact_check_timeliness FROM articles WHERE id = 'a-src'"
    ).fetchone()
    assert art["fact_check_timeliness"] == "90d_demote"
    assert art["fact_check_conclusion"] == "【待核实】"


def test_recent_news_with_historical_background_not_demoted(fresh_db, monkeypatch):
    """生产 4a111c743cb775e3 场景：4 条核心近期 + 2 条历史背景证据。

    不能因为历史背景证据（辽宁第二批 2024、参与度长期 2025）把 13 天前的
    新闻整篇拖成 90d_demote / 【存疑】；结论保持【待核实】，timeliness 取
    最新证据年龄（min=13 → fresh），summary 保留。
    """
    _insert_article("a-news")
    _insert_job("job-news", "a-news")
    payload = json.dumps({
        "conclusion": "【待核实】",
        "summary": "三家银行项目都有声明书原件对应；公开检索不到政府/交易所第二来源，全国总量建议改监管口径。",
        "evidence": [
            {"claim": "盛京银行风电绿色信贷", "verdict": "【待核实】",
             "url": "https://www.mpaypass.com.cn/download/202611/06135653.html",
             "snippet": "项目由盛京银行股份有限公司独立建设、运营及管理。",
             "source_date": _ago(13)},
            {"claim": "邮储葫芦岛气象信贷", "verdict": "【待核实】",
             "url": "https://www.mpaypass.com.cn/download/202611/06140326.html",
             "snippet": "本项目由邮储银行葫芦岛市分行负责提供金融应用场景和贷款管理。",
             "source_date": _ago(13)},
            {"claim": "中信大连科技成果信贷", "verdict": "【待核实】",
             "url": "https://www.mpaypass.com.cn/download/202611/06140831.html",
             "snippet": "以科技成果转化贷为切入点推进与相关客群的破冰合作。",
             "source_date": _ago(13)},
            {"claim": "全国累计569个创新应用公示、参与度98.24%", "verdict": "【待核实】",
             "url": "https://www.nfrm.org.cn/article/1",
             "snippet": "报告显示，截至9月底，工具累计推出创新应用578项。",
             "source_date": _ago(29)},
            {"claim": "辽宁已先后公示4个批次、共15个创新应用", "verdict": "【待核实】",
             "url": "https://m.mpaypass.com.cn/a/2",
             "snippet": "目前辽宁已经先后公示了2个批次的金融科技创新应用。",
             "source_date": _ago(787)},
            {"claim": "银行参与度长期在九成以上", "verdict": "【待核实】",
             "url": "https://finance.sina.com.cn/a/3",
             "snippet": "不完全统计，银行参与度97.35%。",
             "source_date": _ago(651)},
        ],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-news", "task_type": "fact_check_plus", "article_id": "a-news",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    conn = get_db()
    art = conn.execute(
        "SELECT fact_check_conclusion, fact_check_timeliness, fact_check_summary "
        "FROM articles WHERE id = 'a-news'"
    ).fetchone()
    assert art["fact_check_conclusion"] == "【待核实】"
    assert art["fact_check_timeliness"] == "fresh"
    assert art["fact_check_summary"].startswith("三家银行项目都有声明书原件对应")
    days = [
        r["days_old"]
        for r in conn.execute(
            "SELECT days_old FROM fact_check_evidence WHERE article_id = 'a-news'"
        ).fetchall()
    ]
    assert sorted(days) == [13, 13, 13, 29, 651, 787]


def test_english_verified_old_evidence_still_triggers_demotion(fresh_db, monkeypatch):
    """审计 P1：英文 Supported 必须先归一化，再参与时效聚合。

    一条 10 天【待核实】背景证据 + 一条 120 天英文 Supported：
    Supported 不能逃过 verified_days 的 max 聚合，结论降为【待核实】，
    逐条 verdict 也同步降级，不再顶部待核实、证据链挂绿标。
    """
    _insert_article("a-english-old")
    _insert_job("job-english-old", "a-english-old")
    payload = json.dumps({
        "conclusion": "【已核实】",
        "summary": "核心数据与监管口径一致",
        "evidence": [
            {"claim": "历史背景", "verdict": "【待核实】",
             "url": "https://www.mpaypass.com.cn/a/1",
             "snippet": "背景说明", "source_date": _ago(10)},
            {"claim": "核心参数", "verdict": "Supported",
             "url": "https://www.pbc.gov.cn/a/2",
             "snippet": "官方口径", "source_date": _ago(120)},
        ],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-english-old", "task_type": "fact_check_plus",
              "article_id": "a-english-old", "provider": "minimax-cn",
              "model": "MiniMax-M3", "attempts": 0})

    conn = get_db()
    art = conn.execute(
        "SELECT fact_check_conclusion, fact_check_timeliness, fact_check_summary "
        "FROM articles WHERE id = 'a-english-old'"
    ).fetchone()
    assert art["fact_check_conclusion"] == "【待核实】"
    assert art["fact_check_timeliness"] == "90d_demote"
    assert art["fact_check_summary"] == "【时效降级：核心证据超 90 天】核心数据与监管口径一致"
    note = json.loads(art_row(conn, "a-english-old")["fact_check_note"])
    assert all(row["verdict"] == "【待核实】" for row in note)
    gate = json.loads(art_row(conn, "a-english-old")["fact_check_gate"])
    assert gate["demoted_from"] == "【已核实】"
    assert gate["timeliness"] == "90d_demote"
    verdicts = [
        r["verdict"] for r in conn.execute(
            "SELECT verdict FROM fact_check_evidence WHERE article_id='a-english-old' ORDER BY evidence_seq"
        ).fetchall()
    ]
    assert verdicts == ["【待核实】", "【待核实】"]


def test_conclusion_tag_helpers():
    """标签读写：带说明、空串、双标签、全角括号都要稳定。"""
    from moni.llm.fact_check_tags import conclusion_tag, with_tag

    assert conclusion_tag("【已核实】") == "【已核实】"
    assert conclusion_tag("【已核实】核心事实多源一致") == "【已核实】"
    assert conclusion_tag("") == ""
    assert conclusion_tag("核心事实成立") == ""
    assert conclusion_tag("【待核实】【已核实】") == "【待核实】"

    assert with_tag("【待核实】", "【已核实】核心事实多源一致") == "【待核实】核心事实多源一致"
    assert with_tag("【待核实】", "【已核实】") == "【待核实】"
    assert with_tag("【待核实】", "") == "【待核实】"
    assert with_tag("【存疑】", "【已核实】：某个说法") == "【存疑】某个说法"


def test_timeliness_90d_keeps_detail():
    """91 天把【已核实】降为【待核实】并保留原说明；【待核实】不动。"""
    from moni.web.routes.fact_check_plus import apply_timeliness

    assert apply_timeliness("【已核实】核心数据与监管口径一致", 120) == (
        "【待核实】核心数据与监管口径一致"
    )
    assert apply_timeliness("【已核实】", 120) == "【待核实】"
    assert apply_timeliness("【待核实】证据不足", 120) == "【待核实】证据不足"
    assert apply_timeliness("【存疑】两源冲突", 120) == "【存疑】两源冲突"
    assert apply_timeliness("【已核实】核心数据", 60) == (
        "【已核实】核心数据(已过期 30-90 天)"
    )


def test_fetched_at_does_not_substitute_source_date(fresh_db, monkeypatch):
    """只有 fetched_at、没有 source_date → 年龄仍是未知（禁止用抓取时刻顶替）。"""
    _insert_article("a-fa")
    _insert_job("job-fa", "a-fa")
    payload = json.dumps({
        "conclusion": "【待核实】",
        "evidence": [
            {"query": "q", "url": "https://www.pbc.gov.cn/notice/1", "snippet": "央行",
             "fetched_at": _ago(5)},
            {"query": "q", "url": "https://www.nfra.gov.cn/notice/2", "snippet": "监管"},
        ],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({"id": "job-fa", "task_type": "fact_check_plus", "article_id": "a-fa",
              "provider": "minimax-cn", "model": "MiniMax-M3", "attempts": 0})

    conn = get_db()
    rows = conn.execute(
        "SELECT days_old, source_date, fetched_at FROM fact_check_evidence "
        "WHERE article_id = 'a-fa' ORDER BY evidence_seq"
    ).fetchall()
    assert [r["days_old"] for r in rows] == [None, None]
    assert [r["source_date"] for r in rows] == [None, None]
    # fetched_at 仍然留痕，但不再参与年龄计算。
    assert rows[0]["fetched_at"]
    assert conn.execute(
        "SELECT fact_check_timeliness FROM articles WHERE id = 'a-fa'"
    ).fetchone()["fact_check_timeliness"] == "unknown"
