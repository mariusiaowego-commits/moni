"""v1.0.141: default share titles 同业简报-YYMMDD-<格式>-NN."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.share.titles import allocate_title, title_prefix
from moni.web.server import create_app


@pytest.fixture
def fresh_db(tmp_path: Path):
    db_path = tmp_path / "titles.db"
    set_db_path(db_path)
    migrate()
    yield db_path
    close_db()
    import moni.db.connection as conn_mod

    conn_mod._CON = None
    conn_mod._DB_PATH = None


@pytest.fixture
def client(fresh_db: Path) -> TestClient:
    return TestClient(create_app())


def _insert_task(fmt: str, title: str | None, *, extra: bool = True) -> str:
    conn = get_db()
    task_id = f"t-{title or 'untitled'}-{fmt}".replace(" ", "")
    doc = json.dumps({"title": title, "features": []}) if extra else None
    conn.execute(
        "INSERT INTO share_tasks (id, format, article_ids, status, created_at, extra) "
        "VALUES (?, ?, '[]', 'pending', ?, ?)",
        (task_id, fmt, datetime.now(timezone.utc).isoformat(), doc),
    )
    conn.commit()
    return task_id


def test_allocate_starts_at_01_and_increments(fresh_db: Path) -> None:
    conn = get_db()
    first = allocate_title(conn, "html_landing")
    assert first == f"{title_prefix('html_landing')}01"
    _insert_task("html_landing", first)
    assert allocate_title(conn, "html_landing").endswith("-02")


def test_sequences_are_per_format(fresh_db: Path) -> None:
    conn = get_db()
    _insert_task("html_landing", allocate_title(conn, "html_landing"))
    assert allocate_title(conn, "pdf").endswith("-01")
    assert allocate_title(conn, "info_graphic_png").endswith("-01")


def test_untitled_rows_do_not_reuse_numbers(fresh_db: Path) -> None:
    conn = get_db()
    _insert_task("html_landing", None)
    assert allocate_title(conn, "html_landing").endswith("-02")


def test_next_title_api(client: TestClient, fresh_db: Path) -> None:
    r = client.get("/api/share/next-title", params={"format": "html_landing"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["title"].startswith("同业简报-")
    assert data["title"].endswith("-网页-01")
    assert data["label"] == "网页"
    assert data["seq"] == 1

    _insert_task("html_landing", data["title"])
    again = client.get("/api/share/next-title", params={"format": "html_landing"}).json()
    assert again["title"].endswith("-网页-02")

    assert client.get("/api/share/next-title", params={"format": "nope"}).status_code == 400


def test_custom_share_without_title_gets_default(
    client: TestClient, fresh_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("moni.share.manifest._queue_dir", lambda: tmp_path)
    r = client.post(
        "/api/share",
        json={
            "format": "info_graphic_png",
            "version": "1.0",
            "payload": {"article_ids": ["a1"]},
        },
    )
    assert r.status_code == 201, r.text
    row = get_db().execute(
        "SELECT extra FROM share_tasks WHERE id = ?", (r.json()["share_id"],)
    ).fetchone()
    extra = json.loads(row["extra"])
    assert extra["title"].endswith("-信息图-01")
    assert extra["features"][0]["label"] == "生图·信息图"
    assert extra["features"][0]["kind"] == "approach"


def test_preview_does_not_take_a_write_lock(fresh_db: Path) -> None:
    conn = get_db()
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    try:
        title = allocate_title(conn, "pdf", preview=True)
    finally:
        conn.set_trace_callback(None)
    assert title.endswith("-PDF-01")
    assert not any("BEGIN IMMEDIATE" in sql.upper() for sql in seen)
    assert conn.in_transaction is False


def test_titles_from_previous_days_do_not_increment(fresh_db: Path) -> None:
    conn = get_db()
    yesterday = datetime.now() - timedelta(days=1)
    conn.execute(
        "INSERT INTO share_tasks (id, format, article_ids, status, created_at, extra) "
        "VALUES ('old-row', 'html_landing', '[]', 'pending', ?, ?)",
        (
            yesterday.astimezone(timezone.utc).isoformat(),
            json.dumps({"title": f"{title_prefix('html_landing', now=yesterday)}03"}),
        ),
    )
    conn.commit()
    assert allocate_title(conn, "html_landing").endswith("-网页-01")
