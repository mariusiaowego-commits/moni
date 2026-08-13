"""tests/test_fact_check_state.py - 事实核查状态机 (v1.0.27).

Spec: docs/specs/2026-08-13-fact-check-state-machine-v1.0.27.md §3 / §5
状态机: draft → verified → published → deprecated → archived
        verified 可直达 deprecated/archived; draft→archived 跨档 400.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.web.server import create_app


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
    return TestClient(create_app())


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


def _review(client: TestClient, aid: str, state: str, **kw):
    return client.patch(
        f"/api/fact-check-plus/{aid}/review",
        json={
            "state": state,
            "reviewer": kw.get("reviewer", "zhang.wei"),
            "reason": kw.get("reason", "央行公告已二次核实"),
        },
    )


def test_state_machine_draft_default(fresh_db):
    """seed article → state='draft' 默认."""
    _insert_article("a1")
    row = get_db().execute(
        "SELECT fact_check_state FROM articles WHERE id = 'a1'"
    ).fetchone()
    assert row["fact_check_state"] == "draft"


def test_state_machine_review_draft_to_verified(fresh_db, client):
    """PATCH state=verified → 200 + state='verified' + previous_state='draft'."""
    _insert_article("a1")
    r = _review(client, "a1", "verified")
    assert r.status_code == 200
    body = r.json()
    assert body["article_id"] == "a1"
    assert body["state"] == "verified"
    assert body["previous_state"] == "draft"
    assert get_db().execute(
        "SELECT fact_check_state FROM articles WHERE id = 'a1'"
    ).fetchone()["fact_check_state"] == "verified"


def test_state_machine_review_validates_transition(fresh_db, client):
    """PATCH state=archived (from draft) → 400 (跨档跳)."""
    _insert_article("a1")
    r = _review(client, "a1", "archived")
    assert r.status_code == 400
    assert r.json()["code"] == "bad_request"
    assert get_db().execute(
        "SELECT fact_check_state FROM articles WHERE id = 'a1'"
    ).fetchone()["fact_check_state"] == "draft"


def test_state_machine_review_records_audit_fields(fresh_db, client):
    """PATCH 后 reviewed_by / reviewed_at / review_note 写入."""
    _insert_article("a1")
    r = _review(
        client, "a1", "verified",
        reviewer="zhang.wei",
        reason="央行公告已二次核实, 数字一致",
    )
    assert r.status_code == 200
    body = r.json()
    assert body["reviewed_by"] == "zhang.wei"
    assert body["review_note"] == "央行公告已二次核实, 数字一致"
    assert body["reviewed_at"]
    row = get_db().execute(
        """
        SELECT fact_check_reviewed_by, fact_check_reviewed_at, fact_check_review_note
        FROM articles WHERE id = 'a1'
        """
    ).fetchone()
    assert row["fact_check_reviewed_by"] == "zhang.wei"
    assert row["fact_check_review_note"] == "央行公告已二次核实, 数字一致"
    assert row["fact_check_reviewed_at"]


def test_state_machine_endpoint_404_unknown_article(client):
    """未知 article → 404."""
    r = _review(client, "does-not-exist", "verified")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


def test_state_machine_stats_method_includes_state_dimension(fresh_db, client):
    """看板端点返 by_state 数组 5 档全有."""
    _insert_article("a1", method="llm_inference")
    get_db().execute("UPDATE articles SET has_fact_check = 1 WHERE id = 'a1'")
    get_db().commit()

    r = client.get("/api/stats/fact-check-method?period=all")
    assert r.status_code == 200
    body = r.json()
    assert "by_state" in body
    states = [row["state"] for row in body["by_state"]]
    assert states == [
        "draft", "verified", "published", "deprecated", "archived",
    ]
    assert all("article_count" in row and "percentage" in row
               for row in body["by_state"])
