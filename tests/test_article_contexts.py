"""Article-level user contexts: migration, share flag, prompt block."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.share.enrichment import (
    USER_CONTEXT_PER_ARTICLE_CHARS,
    _load_flags,
    rebuild_article_content,
)
from moni.share.formats import validate_manifest


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "contexts.db"
    set_db_path(db_path)
    migrate()
    yield db_path
    close_db()
    import moni.db.connection as conn_mod

    conn_mod._CON = None
    conn_mod._DB_PATH = None


def _insert_article(aid: str = "a1") -> None:
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO articles (
          id, title, url, source, publisher, published_at, fetched_at,
          raw_content, ai_summary
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            aid,
            "测试文章",
            f"https://example.com/{aid}",
            "testsrc",
            "Pub",
            now,
            now,
            "正文内容。" * 40,
            "摘要内容。",
        ),
    )
    conn.commit()


def _insert_context(aid: str, body: str, *, kind: str = "chat", title: str = "") -> None:
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO article_contexts
          (id, article_id, kind, title, body, sort_order, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (f"ctx-{kind}-{len(body)}", aid, kind, title, body, now, now),
    )
    conn.commit()


def test_share_flag_defaults_off_and_accepts_on() -> None:
    base = {"format": "markdown", "version": "1.0", "payload": {"article_ids": ["a1"]}}
    _, payload = validate_manifest(base)
    assert payload["include_user_context"] is False

    base["payload"]["include_user_context"] = True
    _, payload = validate_manifest(base)
    assert payload["include_user_context"] is True


def test_rebuild_includes_context_only_when_flag_on(fresh_db: Path) -> None:
    _insert_article()
    _insert_context("a1", "这条结论值得跟同事同步。", title="判断")
    _insert_context("a1", "补一条手记。", kind="note")

    with_context = rebuild_article_content(
        str(fresh_db), ["a1"], brief=True, include_user_context=True
    )
    assert "用户上下文（用户整理，非核查结论）" in with_context
    assert "AI 对话" in with_context
    assert "这条结论值得跟同事同步。" in with_context
    assert "手记" in with_context

    without_context = rebuild_article_content(
        str(fresh_db), ["a1"], brief=True, include_user_context=False
    )
    assert "用户上下文" not in without_context


def test_rebuild_does_not_repeat_title_in_body(fresh_db: Path) -> None:
    _insert_article()
    _insert_context("a1", "核心观点：正文已经带了标题。", title="核心观点")

    text = rebuild_article_content(
        str(fresh_db), ["a1"], brief=True, include_user_context=True
    )
    assert "- [AI 对话] 核心观点：正文已经带了标题。" in text
    assert "核心观点: 核心观点：" not in text


def test_rebuild_truncates_long_context(fresh_db: Path) -> None:
    _insert_article()
    _insert_context("a1", "x" * (USER_CONTEXT_PER_ARTICLE_CHARS * 3))

    text = rebuild_article_content(
        str(fresh_db), ["a1"], brief=True, include_user_context=True
    )
    context_line = next(
        line for line in text.splitlines() if line.startswith("- [AI 对话]")
    )
    assert len(context_line) <= USER_CONTEXT_PER_ARTICLE_CHARS + 20
    assert context_line.endswith("…")


def test_load_flags_reads_user_context(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"payload": {"include_user_context": True}}), encoding="utf-8"
    )
    assert _load_flags(str(manifest))["include_user_context"] is True

    manifest.write_text(json.dumps({"payload": {}}), encoding="utf-8")
    assert _load_flags(str(manifest))["include_user_context"] is False
