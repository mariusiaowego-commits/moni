"""Sprint 47: article filters, bulk delete, reader prompts, and long-text AI."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

STATIC = Path(__file__).resolve().parents[1] / "moni" / "web" / "static"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    from moni.db.connection import close_db, set_db_path
    from moni.db.migrate import migrate

    set_db_path(tmp_path / "test.db")
    migrate()
    from moni.db.connection import get_db
    get_db().commit()
    monkeypatch.setattr(
        "moni.web.routes.reader.ensure_reader_worker_started", lambda: None
    )
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


def _article(article_id: str, *, source: str, title: str, content: str = "正文", offset: int = 0) -> None:
    from moni.db.connection import get_db

    get_db().execute(
        "INSERT INTO articles (id, title, url, source, raw_content, fetched_at, published_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            article_id,
            title,
            f"https://example.com/{article_id}",
            source,
            content,
            _now(offset),
            _now(offset - 10),
        ),
    )
    get_db().commit()


def _tag(article_id: str, name: str) -> None:
    from moni.db.connection import get_db

    now = _now()
    get_db().execute(
        "INSERT OR IGNORE INTO tag_definitions (name, created_at) VALUES (?, ?)",
        (name, now),
    )
    get_db().execute(
        "INSERT OR IGNORE INTO article_tags (article_id, tag_name, predicted, created_at) "
        "VALUES (?, ?, 0, ?)",
        (article_id, name, now),
    )
    get_db().commit()


def test_migration_should_apply_keeps_history_and_fills_later_holes():
    from moni.db.migrate import migration_should_apply

    assert migration_should_apply(39, 41, {41}) is False
    assert migration_should_apply(43, 41, {41}) is True
    assert migration_should_apply(42, 43, {41, 43}) is True
    assert migration_should_apply(43, 43, {43}) is False
    assert migration_should_apply(1, 0, set()) is True


def test_choose_reader_mode_covers_full_text_without_dumping_it():
    from moni.llm.reader import MAX_FULL_CHARS, choose_reader_mode, group_chunk_texts

    assert choose_reader_mode(text_len=100, prompt_body="总结") == "full"
    assert choose_reader_mode(text_len=MAX_FULL_CHARS + 1, prompt_body="总结") == "map_reduce"
    assert choose_reader_mode(text_len=MAX_FULL_CHARS + 1) == "map_reduce"
    assert choose_reader_mode(text_len=MAX_FULL_CHARS + 1, quote="这一段", has_history=True) == "selection"
    assert choose_reader_mode(text_len=MAX_FULL_CHARS + 1, has_history=True) == "retrieve"

    chunks = [f"块{i}-{'x' * 20}" for i in range(20)]
    groups = group_chunk_texts(chunks, max_groups=8)
    assert 1 < len(groups) <= 8
    assert chunks[0] in groups[0]
    assert chunks[-1] in groups[-1]
    assert "".join(chunks) == "".join(groups).replace("\n\n", "")


def test_article_filters_sort_and_total(client: TestClient):
    _article("a1", source="src-a", title="Alpha", offset=1)
    _article("a2", source="src-b", title="Beta", offset=2)
    _article("a3", source="src-a", title="Gamma", offset=3)
    _tag("a1", "银行")
    _tag("a1", "监管")
    _tag("a2", "银行")

    listed = client.get("/api/articles?sources=src-a,src-b")
    assert listed.status_code == 200
    assert listed.headers["x-total-count"] == "3"
    assert all(item["content"] is None for item in listed.json())

    only_a = client.get("/api/articles?sources=src-a&sort=title_asc")
    assert [item["id"] for item in only_a.json()] == ["a1", "a3"]
    assert only_a.headers["x-total-count"] == "2"

    either = client.get("/api/articles?tags=银行,监管&tag_mode=or")
    assert {item["id"] for item in either.json()} == {"a1", "a2"}

    both = client.get("/api/articles?tags=银行,监管&tag_mode=and&sort=fetched_at_desc")
    assert [item["id"] for item in both.json()] == ["a1"]

    facets = client.get("/api/articles/facets")
    assert facets.status_code == 200
    names = {row["name"] for row in facets.json()["sources"]}
    assert {"src-a", "src-b"} <= names
    bank = next(row for row in facets.json()["tags"] if row["name"] == "银行")
    assert bank["count"] == 2


def test_facets_skip_tag_rows_without_article(client: TestClient):
    """删除文章会留下 article_tags 孤儿行；chip 计数不能把它们算进去。"""
    from moni.db.connection import get_db

    _article("a1", source="src-a", title="Alpha")
    _tag("a1", "银行")
    conn = get_db()
    # Production deletes ran with FK enforcement off and left such rows behind.
    conn.execute("PRAGMA foreign_keys = OFF")
    _tag("gone", "银行")
    conn.execute("PRAGMA foreign_keys = ON")

    assert conn.execute(
        "SELECT COUNT(*) FROM article_tags WHERE tag_name = '银行'"
    ).fetchone()[0] == 2

    facets = client.get("/api/articles/facets").json()
    bank = next(row for row in facets["tags"] if row["name"] == "银行")
    listed = client.get("/api/articles?tags=银行")
    assert bank["count"] == 1
    assert bank["count"] == int(listed.headers["x-total-count"])


def test_prompt_pills_keep_manager_button_outside_the_scroller():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    row = html[html.index('<div class="ai-pills-row">'):]
    scroller_end = row.index("</div>", row.index('<div class="ai-pills">'))
    assert "ai-pill-more" not in row[:scroller_end]
    assert "ai-pill-more" in row[scroller_end:]


def test_image_only_article_gets_a_hint():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "readerBodyEmpty" in html
    assert "这篇原文以图片为主" in html
    js = (STATIC / "js" / "reader.js").read_text(encoding="utf-8")
    assert "plainTextLength" in js
    assert "readerBodyEmpty" in js


def test_bulk_delete_removes_article_and_reader_rows(client: TestClient):
    from moni.db.connection import get_db

    _article("a1", source="src-a", title="留下")
    _article("a2", source="src-a", title="删掉")
    created = client.post("/api/reader/conversations", json={"article_id": "a2"})
    assert created.status_code == 200
    conversation_id = created.json()["conversation"]["id"]
    sent = client.post(
        f"/api/reader/conversations/{conversation_id}/messages",
        json={"text": "这篇文章在说什么"},
    )
    assert sent.status_code == 202

    too_many = client.post("/api/articles/bulk-delete", json={"ids": [f"missing-{i}" for i in range(201)]})
    assert too_many.status_code == 400

    deleted = client.post("/api/articles/bulk-delete", json={"ids": ["a2", "missing"]})
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] == 1
    assert deleted.json()["missing"] == ["missing"]
    assert client.get("/api/articles/a2").status_code == 404
    assert get_db().execute("SELECT COUNT(*) AS n FROM reader_conversations").fetchone()["n"] == 0
    assert get_db().execute("SELECT COUNT(*) AS n FROM llm_jobs WHERE article_id = 'a2'").fetchone()["n"] == 0
    assert client.get("/api/articles/a1").status_code == 200


def test_prompt_library_blocks_builtin_delete_and_restores_body(client: TestClient):
    listed = client.get("/api/reader/prompts")
    assert listed.status_code == 200
    prompts = listed.json()
    assert len(prompts) == 6
    builtin = next(item for item in prompts if item["name"] == "总结要点")
    assert builtin["is_builtin"] is True

    blocked = client.delete(f"/api/reader/prompts/{builtin['id']}")
    assert blocked.status_code == 400

    renamed = client.patch(
        f"/api/reader/prompts/{builtin['id']}",
        json={"body": "被改过的提示词"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["body"] == "被改过的提示词"

    restored = client.post("/api/reader/prompts/restore")
    assert restored.status_code == 200
    again = next(item for item in restored.json() if item["id"] == builtin["id"])
    assert "5 条" in again["body"]

    created = client.post("/api/reader/prompts", json={"name": "自定义", "body": "只列数字"})
    assert created.status_code == 200
    duplicate = client.post("/api/reader/prompts", json={"name": "自定义", "body": "另一份"})
    assert duplicate.status_code == 400
    removed = client.delete(f"/api/reader/prompts/{created.json()['id']}")
    assert removed.status_code == 200


def _fake_stream(prompts: list[str]):
    def run(prompt, *, reasoning="low", on_start=None, on_delta=None, timeout=180):
        prompts.append(prompt)
        if on_delta:
            on_delta("甲" * 10)
            on_delta("乙" * 50)
            on_delta.flush()
        return {
            "text": "甲" * 10 + "乙" * 50,
            "usage": {"input_tokens": 2, "output_tokens": 3},
            "duration_ms": 5,
            "returncode": 0,
        }

    return run


def _run_next_reader_job() -> None:
    from moni.db import leases
    from moni.llm.reader_worker import _run_claimed

    handle = leases.acquire("llm_jobs", grace_sec=120, task_types=("reader_chat",))
    assert handle is not None
    _run_claimed(handle)


def test_reader_message_creates_job_and_streams_result(client: TestClient, monkeypatch):
    from moni.db.connection import get_db

    prompts: list[str] = []
    monkeypatch.setattr("moni.llm.reader_worker.run_reader_stream", _fake_stream(prompts))
    _article("a1", source="src-a", title="短文", content="这是一篇不长的文章。")
    conversation = client.post("/api/reader/conversations", json={"article_id": "a1"}).json()["conversation"]
    sent = client.post(
        f"/api/reader/conversations/{conversation['id']}/messages",
        json={"prompt_name": "总结要点", "reasoning": "low"},
    )
    assert sent.status_code == 202
    job_id = sent.json()["job_id"]
    row = get_db().execute("SELECT task_type, prompt_name, status FROM llm_jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["task_type"] == "reader_chat"
    assert row["prompt_name"] == "总结要点"
    assert row["status"] == "pending"

    _run_next_reader_job()
    done = get_db().execute("SELECT status, input_tokens, output_tokens FROM llm_jobs WHERE id = ?", (job_id,)).fetchone()
    assert done["status"] == "done"
    assert done["input_tokens"] == 2
    assert done["output_tokens"] == 3
    assert len(prompts) == 1
    assert "这是一篇不长的文章" in prompts[0]

    unread = client.get("/api/articles?sources=src-a")
    assert unread.json()[0]["reader_unread"] is True
    assert client.get("/api/reader/unread-count").json()["count"] == 1
    client.post(f"/api/reader/conversations/{conversation['id']}/read")
    assert client.get("/api/articles/a1").json()["reader_unread"] is False

    with client.stream("GET", f"/api/reader/jobs/{job_id}/stream") as response:
        body = "".join(response.iter_text())
    assert "event: snapshot" in body
    assert "event: done" in body
    assert "甲" in body


def test_long_article_prompt_map_reduces_the_whole_text(client: TestClient, monkeypatch):
    prompts: list[str] = []
    monkeypatch.setattr("moni.llm.reader_worker.run_reader_stream", _fake_stream(prompts))
    body = ("段落甲。\n\n" * 4500) + "TAILTOKEN"
    assert len(body) > 20_000
    _article("long", source="src-a", title="长文", content=body)
    conversation = client.post("/api/reader/conversations", json={"article_id": "long"}).json()["conversation"]
    client.post(
        f"/api/reader/conversations/{conversation['id']}/messages",
        json={"prompt_name": "总结要点"},
    )
    _run_next_reader_job()
    assert len(prompts) >= 2
    assert len(prompts) <= 9
    assert any("TAILTOKEN" in prompt for prompt in prompts[:-1])
    assert "合并" in prompts[-1]


def test_long_followup_uses_retrieval_not_another_map(client: TestClient, monkeypatch):
    prompts: list[str] = []
    monkeypatch.setattr("moni.llm.reader_worker.run_reader_stream", _fake_stream(prompts))
    body = "开头必须保留。\n\n" + ("中段。\n\n" * 4000) + "结尾必须保留。"
    _article("long2", source="src-a", title="长文追问", content=body)
    conversation = client.post("/api/reader/conversations", json={"article_id": "long2"}).json()["conversation"]
    client.post(
        f"/api/reader/conversations/{conversation['id']}/messages",
        json={"text": "开头在说什么"},
    )
    _run_next_reader_job()
    first = len(prompts)
    assert first >= 2
    client.post(
        f"/api/reader/conversations/{conversation['id']}/messages",
        json={"text": "那结尾呢"},
    )
    _run_next_reader_job()
    assert len(prompts) == first + 1
    assert "长文追问" in prompts[-1]
    assert "结尾必须保留" in prompts[-1]


def test_cancel_bumps_fence_so_worker_cannot_mark_done(client: TestClient):
    from moni.db import leases
    from moni.db.connection import get_db
    from moni.llm.reader_worker import _run_claimed

    _article("a1", source="src-a", title="可取消", content="正文")
    conversation = client.post("/api/reader/conversations", json={"article_id": "a1"}).json()["conversation"]
    sent = client.post(
        f"/api/reader/conversations/{conversation['id']}/messages",
        json={"text": "问一句"},
    )
    job_id = sent.json()["job_id"]
    handle = leases.acquire("llm_jobs", grace_sec=120, task_types=("reader_chat",))
    assert handle is not None
    cancelled = client.post(f"/api/reader/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200
    _run_claimed(handle)
    status = get_db().execute("SELECT status FROM llm_jobs WHERE id = ?", (job_id,)).fetchone()["status"]
    message = get_db().execute(
        "SELECT status FROM reader_messages WHERE job_id = ?", (job_id,)
    ).fetchone()["status"]
    assert status == "cancelled"
    assert message == "cancelled"


def test_reader_lease_does_not_take_summary_jobs():
    from moni.db import leases

    _article("a1", source="src-a", title="队列", content="正文")
    from moni.db.connection import get_db

    now = _now()
    get_db().execute(
        "INSERT INTO llm_jobs (id, task_type, article_id, provider, model, status, attempts, created_at) "
        "VALUES ('job-summary', 'summary', 'a1', 'profile', '', 'pending', 0, ?)",
        (now,),
    )
    get_db().execute(
        "INSERT INTO llm_jobs (id, task_type, article_id, provider, model, status, attempts, created_at) "
        "VALUES ('job-reader', 'reader_chat', 'a1', 'profile', '', 'pending', 0, ?)",
        (now,),
    )
    get_db().commit()
    summary = leases.acquire("llm_jobs", exclude_task_types=("reader_chat",))
    reader = leases.acquire("llm_jobs", task_types=("reader_chat",))
    assert summary is not None and summary.job_id == "job-summary"
    assert reader is not None and reader.job_id == "job-reader"


def test_frontend_reader_contract():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "js" / "reader.js").read_text(encoding="utf-8")
    css = (STATIC / "css" / "main.css").read_text(encoding="utf-8")
    app = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert 'src="js/reader.js' in html
    assert 'class="reader-body"' in html
    assert 'class="reader-scroll"' in html
    assert 'class="reader-sticky-bar"' in html
    assert 'class="reader-dock-actions"' in html
    assert "readerBankParts" in js and "parseBankMeta" in js
    assert 'x-html="readerArticleHtml()"' in html
    assert 'x-model="articlesSource"' not in html
    assert 'x-model="articlesTag"' not in html
    assert "readerRunPrompt(p)" in html
    assert "llmJobTypeLabel(g.job)" in html
    assert "llmJobTypeLabel(jobDetail)" in html
    assert "function readerRunPrompt" not in js
    assert "readerRunPrompt(prompt)" in js
    assert "this.readerInput = prompt.body" not in js
    assert "getOwnPropertyDescriptors" in app
    # Scraped bodies are text-only in the reader: no <img> in the allowlist and
    # Markdown/HTML image syntax stripped before rendering.
    assert "'img'" not in js
    assert "MD_IMAGE_RE" in js and "HTML_IMAGE_RE" in js
    assert "ai-thinking-label" in html and "ai-caret" in html
    assert "readerCopyMessage" in js
    # AICSS ports: orb (thinking), thought fold (thinking+reasoning),
    # citation chips, and the shared table shell.
    assert "ai-orb-cell" in html and "ai-orb-wave" in css
    assert "ai-thought-body" in html and "ai-thought" in css
    assert "cite-chip" in js and ".cite-footer" in css
    assert "normalizeTables" in js and "ai-table" in js
    assert "readerPushDelta" in js and "readerFlushDelta" in js


def test_reader_stream_skips_gateway_rejected_flags(monkeypatch):
    from moni.llm import reader

    captured: dict[str, list[str]] = {}

    class FakeStream:
        def __iter__(self):
            return iter([
                '{"type":"text","text":"ok"}',
                '{"type":"result","text":"ok","tokens":{"input":3,"output":1}}',
            ])

        def read(self):
            return ""

    class FakeProc:
        def __init__(self, cmd, **_kwargs):
            captured["cmd"] = cmd
            self.stdout = FakeStream()
            self.stderr = FakeStream()
            self.returncode = 0

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(reader.subprocess, "Popen", FakeProc)
    result = reader.run_reader_stream("hi", timeout=5)

    assert result["text"] == "ok"
    cmd = captured["cmd"]
    # The moni profile goes through the hermes gateway, which exits rc=2 on
    # --run-budget; the local watchdog owns the budget instead.
    assert "--run-budget" not in cmd
    assert "--oneshot" in cmd and "stream-json" in cmd


def test_article_list_exposes_has_reader_chat(client: TestClient):
    _article("a1", source="src-a", title="聊过")
    _article("a2", source="src-a", title="没聊过")
    created = client.post("/api/reader/conversations", json={"article_id": "a1"})
    assert created.status_code == 200
    listed = client.get("/api/articles?limit=20").json()
    by_id = {item["id"]: item for item in listed}
    assert by_id["a1"]["has_reader_chat"] is True
    assert by_id["a2"]["has_reader_chat"] is False
    pinned = client.post("/api/articles/a1/pin?on=true")
    assert pinned.status_code == 200
    pinned_row = client.get("/api/articles/pinned").json()["items"][0]
    assert pinned_row["id"] == "a1"
    assert pinned_row["has_reader_chat"] is True


def test_reader_list_title_link_and_chat_mark_markup():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'class="title-link"' in html
    assert "@click=\"showArticleDetail(a)\"" in html
    assert 'class="reader-chat-mark"' in html
    assert "has_reader_chat" in html
    assert 'class="title-tip"' in html
    js = (STATIC / "js" / "reader.js").read_text(encoding="utf-8")
    assert "flashQuotedText" in js
    assert "reader-flash" in js
    assert "dataset.quote" in js
    assert "a.cite-chip, a.cite-ref" in js
    css = (STATIC / "css" / "main.css").read_text(encoding="utf-8")
    assert "structureListMarkdown" in js
    assert "structureRenderedLists" in js
    assert ".ai-structured-list" in css
    assert ".title-link" in css
