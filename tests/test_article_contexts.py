"""Article-level user contexts: snapshot API, renderers and share flags."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.share.context_pack import (
    PER_ARTICLE_CHARS,
    format_lines,
    html_block,
    inject_html_section,
    markdown_section,
    standalone_html_section,
)
from moni.share.formats import VALID_FORMATS, validate_manifest


@pytest.fixture(autouse=True)
def fresh_db(tmp_path: Path):
    set_db_path(tmp_path / "contexts.db")
    migrate()
    yield
    close_db()
    import moni.db.connection as conn_mod

    conn_mod._CON = None
    conn_mod._DB_PATH = None


@pytest.fixture
def client() -> TestClient:
    from moni.web.server import create_app

    return TestClient(create_app())


def _now(offset_min: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=offset_min)).isoformat()


def _article(aid: str, title: str = "测试文章") -> None:
    conn = get_db()
    now = _now()
    conn.execute(
        "INSERT INTO articles (id, title, url, source, raw_content, fetched_at, published_at) "
        "VALUES (?, ?, ?, 'testsrc', ?, ?, ?)",
        (aid, title, f"https://example.com/{aid}", "正文内容。" * 40, now, now),
    )
    conn.commit()


def _conversation(cid: str, aid: str) -> None:
    conn = get_db()
    conn.execute(
        "INSERT INTO reader_conversations (id, article_id, title, unread, created_at, updated_at) "
        "VALUES (?, ?, '会话', 0, ?, ?)",
        (cid, aid, _now(-5), _now(-5)),
    )
    conn.commit()


def _message(
    mid: str,
    cid: str,
    *,
    role: str = "assistant",
    content: str = "这是回答。",
    status: str = "done",
    offset_min: int = 0,
) -> None:
    conn = get_db()
    conn.execute(
        "INSERT INTO reader_messages "
        "(id, conversation_id, role, content, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (mid, cid, role, content, status, _now(offset_min), _now(offset_min)),
    )
    conn.commit()


def _seed_message(article_id: str = "a1", conv: str = "c1", msg: str = "m1") -> None:
    _article(article_id)
    _conversation(conv, article_id)
    _message(f"{conv}-q", conv, role="user", content="这篇文章的关键判断是什么？", offset_min=-1)
    _message(msg, conv, content="关键判断有三条。", offset_min=0)


def _insert_context(aid: str, cid: str, body: str, *, kind: str = "chat", title: str = "") -> None:
    conn = get_db()
    now = _now()
    conn.execute(
        "INSERT INTO article_contexts "
        "(id, article_id, kind, title, body, sort_order, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
        (cid, aid, kind, title, body, now, now),
    )
    conn.commit()


# ── manifest flag ───────────────────────────────────────────────────────────


def test_share_flag_defaults_off_for_every_format() -> None:
    for fmt in sorted(VALID_FORMATS):
        manifest = {
            "format": fmt,
            "version": "1.0",
            "payload": {"article_ids": ["a1"]},
        }
        if fmt in ("html_landing", "info_graphic_png", "pdf"):
            manifest["payload"]["title"] = "标题"
        _, payload = validate_manifest(manifest)
        assert payload["include_user_context"] is False, fmt

        manifest["payload"]["include_user_context"] = True
        _, payload = validate_manifest(manifest)
        assert payload["include_user_context"] is True, fmt


# ── renderers ───────────────────────────────────────────────────────────────


def test_format_lines_dedupes_title_and_truncates() -> None:
    items = [
        {"kind": "chat", "title": "判断", "body": "判断：正文已经带了标题。"},
        {"kind": "note", "title": "", "body": "x" * (PER_ARTICLE_CHARS * 2)},
    ]
    lines = format_lines(items, budget=PER_ARTICLE_CHARS)
    assert lines[0] == "- [AI 对话] 判断：正文已经带了标题。"
    assert len(lines[1]) <= PER_ARTICLE_CHARS
    assert lines[1].endswith("…")


def test_markdown_section_groups_by_article() -> None:
    _article("a1", "第一篇文章")
    _article("a2", "第二篇文章")
    _insert_context("a1", "ctx1", "第一条上下文。")
    _insert_context("a2", "ctx2", "第二条上下文。")
    text = markdown_section(_db_path(), ["a1", "a2"])
    assert text.startswith("## 用户上下文（用户整理，非核查结论）")
    assert "> 以下内容由用户在阅读时保存" in text
    assert "### 文章 1: 第一篇文章" in text
    assert "### 文章 2: 第二篇文章" in text
    assert "- [AI 对话] 第一条上下文。" in text


def test_markdown_section_empty_without_contexts() -> None:
    _article("a1")
    assert markdown_section(_db_path(), ["a1"]) == ""


def test_html_block_escapes_and_skips_empty_body() -> None:
    block = html_block(
        [
            {"kind": "chat", "title": "<判断>", "body": "<script>x</script>"},
            {"kind": "note", "title": "空", "body": "   "},
        ]
    )
    assert "&lt;script&gt;" in block
    assert "<script>" not in block
    assert "&lt;判断&gt;" in block
    assert block.count("ctx-item") == 1
    assert html_block([]) == ""


def test_standalone_section_and_injection() -> None:
    _article("a1", "标题 A")
    _insert_context("a1", "ctx1", "上下文内容。")
    section = standalone_html_section(_db_path(), ["a1"])
    assert "moni-user-contexts" in section
    assert "上下文内容。" in section

    doc = "<html><body><p>x</p></body></html>"
    out = inject_html_section(doc, section)
    assert out.index(section) < out.index("</body>")

    no_body = "<div>fragment</div>"
    assert inject_html_section(no_body, section).startswith(no_body)


def _db_path() -> str:
    from moni.db.connection import get_db_path as _get_db_path

    return str(_get_db_path())


# ── API ─────────────────────────────────────────────────────────────────────


def test_save_answer_snapshots_question_and_is_idempotent(client: TestClient) -> None:
    _seed_message()
    first = client.post("/api/articles/a1/contexts", json={"message_id": "m1"})
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["created"] is True
    ctx = body["context"]
    assert ctx["body"] == "关键判断有三条。"
    assert ctx["source_question"] == "这篇文章的关键判断是什么？"
    assert ctx["kind"] == "chat"
    assert ctx["title"] == "关键判断有三条。"

    second = client.post("/api/articles/a1/contexts", json={"message_id": "m1"})
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["context"]["id"] == ctx["id"]

    listed = client.get("/api/articles/a1/contexts").json()
    assert listed["count"] == 1
    assert listed["contexts"][0]["id"] == ctx["id"]


def test_rejects_non_answer_and_cross_article(client: TestClient) -> None:
    _seed_message()
    _article("a2")
    _conversation("c2", "a2")
    _message("m2", "c2", content="另一篇的回答。")
    _message("pending", "c1", status="pending")

    assert client.post(
        "/api/articles/a1/contexts", json={"message_id": "c1-q"}
    ).status_code == 400
    assert client.post(
        "/api/articles/a1/contexts", json={"message_id": "pending"}
    ).status_code == 400
    assert client.post(
        "/api/articles/a1/contexts", json={"message_id": "m2"}
    ).status_code == 400
    assert client.post(
        "/api/articles/a1/contexts", json={"message_id": "missing"}
    ).status_code == 404


def test_delete_conversation_keeps_snapshot(client: TestClient) -> None:
    _seed_message()
    ctx = client.post("/api/articles/a1/contexts", json={"message_id": "m1"}).json()["context"]
    get_db().execute("DELETE FROM reader_conversations WHERE id = 'c1'")
    get_db().commit()
    listed = client.get("/api/articles/a1/contexts").json()
    assert [c["id"] for c in listed["contexts"]] == [ctx["id"]]


def test_delete_article_cascades_contexts(client: TestClient) -> None:
    _seed_message()
    client.post("/api/articles/a1/contexts", json={"message_id": "m1"})
    get_db().execute("DELETE FROM articles WHERE id = 'a1'")
    get_db().commit()
    row = get_db().execute(
        "SELECT COUNT(*) FROM article_contexts WHERE article_id = 'a1'"
    ).fetchone()
    assert row[0] == 0


def test_delete_context_and_summary(client: TestClient) -> None:
    _seed_message()
    _article("a2")
    ctx = client.post("/api/articles/a1/contexts", json={"message_id": "m1"}).json()["context"]
    _insert_context("a2", "ctx2", "第二篇的上下文。")
    get_db().execute(
        "INSERT INTO article_pins (article_id, pinned_at) VALUES ('a2', ?)", (_now(),)
    )
    get_db().commit()

    summary = client.get("/api/article-contexts/summary").json()
    assert summary["counts"] == {"a1": 1, "a2": 1}
    assert summary["total"] == 2
    assert summary["pinned_total"] == 1

    deleted = client.delete(f"/api/articles/a1/contexts/{ctx['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get("/api/articles/a1/contexts").json()["count"] == 0


def test_manifest_accepts_share_options() -> None:
    manifest = {
        "format": "markdown",
        "version": "1.0",
        "payload": json.loads(
            json.dumps({"article_ids": ["a1"], "include_user_context": True})
        ),
    }
    _, payload = validate_manifest(manifest)
    assert payload["include_user_context"] is True
