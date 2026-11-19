"""tests/test_source_hidden.py — 信源隐藏开关 (is_hidden) 的 API 契约。

失败模式（先列清楚，再写断言）：
  1. 迁移没跑 → sources 表没有 is_hidden 列 → 读写直接炸。
  2. PUT 传了 is_hidden 但 UPDATE 语句漏了这一列 → 前端点了没反应。
  3. 隐藏时被顺手把 is_active 也关了 → 信源不再同步（隐藏≠停用）。
  4. 列表接口不返回 is_hidden → 前端无法判断该不该显示。
  5. 新建源时 is_hidden 丢失 → 默认值被覆盖。
"""
from __future__ import annotations

import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not os.path.isdir(os.path.join(_REPO_ROOT, "moni")):
    _REPO_ROOT = os.path.expanduser("~/dev/moni")
sys.path.insert(0, _REPO_ROOT)

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.web.server import create_app


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    migrate()
    yield
    close_db()
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod
    conn_mod._CON = None
    conn_mod._DB_PATH = None
    mig_mod._CON = None


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _seed_source(conn, sid="src-hidden-1", name="hidden_src"):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO sources
            (id, name, display_name, source_type, url,
             fetch_interval_min, is_active, fetch_config, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (sid, name, "隐藏测试源", "rss", "https://example.com/feed.xml",
         60, 1, json.dumps({}), now, now),
    )
    conn.commit()


def test_migration_adds_is_hidden_column_default_zero():
    conn = get_db()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sources)").fetchall()}
    assert "is_hidden" in cols
    _seed_source(conn)
    row = conn.execute("SELECT is_hidden FROM sources WHERE id = ?",
                       ("src-hidden-1",)).fetchone()
    assert row["is_hidden"] == 0


def test_update_source_persists_is_hidden(client):
    conn = get_db()
    _seed_source(conn)
    r = client.put("/api/config/sources/src-hidden-1", json={"is_hidden": True})
    assert r.status_code == 200
    assert r.json()["is_hidden"] is True

    row = conn.execute(
        "SELECT is_hidden, is_active FROM sources WHERE id = ?", ("src-hidden-1",)
    ).fetchone()
    assert row["is_hidden"] == 1
    # 隐藏只是「不显示」—— 不能顺手把源停用掉
    assert row["is_active"] == 1


def test_list_sources_returns_is_hidden_flag(client):
    conn = get_db()
    _seed_source(conn)
    client.put("/api/config/sources/src-hidden-1", json={"is_hidden": True})
    items = client.get("/api/config/sources").json()
    by_id = {s["id"]: s for s in items}
    assert by_id["src-hidden-1"]["is_hidden"] is True


def test_create_source_can_start_hidden(client):
    r = client.post("/api/config/sources", json={
        "id": "src-new-hidden",
        "name": "new_hidden",
        "display_name": "新建就隐藏",
        "source_type": "rss",
        "url": "https://example.com/rss",
        "is_hidden": True,
    })
    assert r.status_code == 201
    assert r.json()["is_hidden"] is True
    row = get_db().execute("SELECT is_hidden FROM sources WHERE id = ?",
                           ("src-new-hidden",)).fetchone()
    assert row["is_hidden"] == 1


def test_unhide_roundtrip(client):
    conn = get_db()
    _seed_source(conn)
    client.put("/api/config/sources/src-hidden-1", json={"is_hidden": True})
    r = client.put("/api/config/sources/src-hidden-1", json={"is_hidden": False})
    assert r.status_code == 200
    assert r.json()["is_hidden"] is False
    row = conn.execute("SELECT is_hidden FROM sources WHERE id = ?",
                       ("src-hidden-1",)).fetchone()
    assert row["is_hidden"] == 0
