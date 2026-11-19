"""HTTP surface of ``/api/moderation/labels/*`` (sprint-58)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.web.server import create_app


@pytest.fixture
def client(tmp_path):
    set_db_path(tmp_path / "test.db")
    migrate()
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO articles (id, title, url, source, fetched_at, raw_content)
        VALUES ('a1', '露骨标题示例', 'https://e.com/1', 'qbitai', ?, ?)
        """,
        (now, "加微信，博彩平台内部渠道，日入三百。"),
    )
    conn.commit()
    yield TestClient(create_app())
    close_db()
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod

    conn_mod._CON = None  # type: ignore[attr-defined]
    conn_mod._DB_PATH = None  # type: ignore[attr-defined]
    mig_mod._CON = None  # type: ignore[attr-defined]


def test_preview_then_apply_is_idempotent(client):
    preview = client.post("/api/moderation/labels/preview").json()
    assert preview["new_rows"] == 0
    assert preview["labeled"] == 1

    first = client.post("/api/moderation/labels/apply").json()
    assert first["new_rows"] == 1
    second = client.post("/api/moderation/labels/apply").json()
    assert second["new_rows"] == 0


def test_samples_are_masked_until_reveal(client):
    client.post("/api/moderation/labels/apply")
    masked = client.get(
        "/api/moderation/labels/samples", params={"category": "nsfw_gamble"}
    ).json()
    assert masked and "***" in masked[0]["title"]
    plain = client.get(
        "/api/moderation/labels/samples",
        params={"category": "nsfw_gamble", "reveal": "1"},
    ).json()
    assert plain[0]["title"] == "露骨标题示例"
    assert "raw_content" not in plain[0]


def test_review_persists_and_report_has_no_titles(client):
    client.post("/api/moderation/labels/apply")
    reviewed = client.post(
        "/api/moderation/labels/a1/nsfw_gamble/review",
        json={"decision": "dismissed", "note": "测试"},
    ).json()
    assert reviewed["review_state"] == "dismissed"

    runs = client.get("/api/moderation/labels/runs").json()
    report = client.get(f"/api/moderation/labels/runs/{runs[0]['id']}/report.md").text
    assert "露骨标题示例" not in report
    assert "S 类" in report


def test_label_apply_never_touches_articles(client, tmp_path):
    before = get_db().execute(
        "SELECT raw_content FROM articles WHERE id='a1'"
    ).fetchone()[0]
    client.post("/api/moderation/labels/apply")
    after = get_db().execute(
        "SELECT raw_content FROM articles WHERE id='a1'"
    ).fetchone()[0]
    assert before == after
    with sqlite3.connect(tmp_path / "test.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1
