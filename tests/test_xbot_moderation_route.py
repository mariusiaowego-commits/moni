"""sprint-54 / id 26111703 —— I8 只读 dry-run 端点的护栏。

端点：``GET /api/moderation/xbot/candidates``

⚠️ 本文件**只读** —— 不写生产库、不改生产配置。
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    """建临时库 + 起 TestClient。"""
    from moni.web.server import create_app

    close_db()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MONI_DATA_DIR", str(data_dir))
    set_db_path(data_dir / "news.db")
    migrate()

    conn = get_db()
    _seed(conn)
    conn.commit()
    close_db()

    app = create_app()
    with TestClient(app) as c:
        yield c
    close_db()


def _seed(conn):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    # ⚠️ ``migrate()`` 已 seed 过 ``xbot_feed`` ⇒ 这里**只**更新 fetch_config,
    #    **不**再 INSERT (sources.name 有 UNIQUE 约束)
    conn.execute(
        "UPDATE sources SET fetch_config = ? WHERE name = 'xbot_feed'",
        (json.dumps({"scope": {}}),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO tag_definitions (name, keywords, is_active, created_at) "
        "VALUES ('wechat', ?, 1, ?)",
        (json.dumps(["微信", "微信小程序", "公众号", "视频号"]), now),
    )
    conn.execute(
        "INSERT OR REPLACE INTO source_tags (source_id, tag_name, created_at) "
        "VALUES ('src-xbot-feed', 'wechat', ?)",
        (now,),
    )
    # 裸词微信 ⇒ 候选
    conn.execute(
        "INSERT OR REPLACE INTO articles (id, title, url, source, publisher, published_at, "
        "fetched_at, raw_content) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("bare01", "聊聊微信", "https://x/1", "xbot_feed", "@a", now, now, "就说一句微信"),
    )
    # 命中「微信小程序」⇒ **不该**判成裸词
    conn.execute(
        "INSERT OR REPLACE INTO articles (id, title, url, source, publisher, published_at, "
        "fetched_at, raw_content) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("mini02", "微信小程序上线", "https://x/2", "xbot_feed", "@b", now, now,
         "我们做了个微信小程序"),
    )
    # 有简报 ⇒ 受保护
    conn.execute(
        "INSERT OR REPLACE INTO articles (id, title, url, source, publisher, published_at, "
        "fetched_at, raw_content, brief_summary) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("prot03", "微信生态", "https://x/3", "xbot_feed", "@c", now, now,
         "深度分析", "简报"),
    )


# ── 基本契约 ────────────────────────────────────────────────────────────────

def test_endpoint_returns_200(app_client):
    """⚠️ 端点存在且返回 200。"""
    r = app_client.get("/api/moderation/xbot/candidates")
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    assert data["scanned_articles"] == 3


def test_response_contains_protected_article_ids_and_is_protected(app_client):
    """⚠️ **C-6**：响应**必含** ``protected_article_ids`` 与每条的 ``is_protected``。"""
    data = app_client.get("/api/moderation/xbot/candidates").json()
    assert "protected_article_ids" in data
    assert "unprotected_candidate_ids" in data
    for c in data["candidates"]:
        assert "is_protected" in c
        assert "protected_kinds" in c
        assert "protected_article_ids" in c
    cands = {c["article_id"]: c for c in data["candidates"]}
    if "prot03" in cands:
        assert cands["prot03"]["is_protected"] is True
        assert "prot03" in data["protected_article_ids"]


def test_response_contains_no_article_body(app_client):
    """⚠️ **AC-PRV-01**：响应**不含正文**。"""
    r = app_client.get("/api/moderation/xbot/candidates")
    for marker in ("深度分析", "就说一句微信", "我们做了个微信小程序"):
        assert marker not in r.text, f"响应里出现了正文: {marker!r}"


def test_response_reports_no_accuracy_metrics(app_client):
    """⚠️ **没有人工标注 ⇒ 不报准确率**。"""
    r = app_client.get("/api/moderation/xbot/candidates")
    data = r.json()
    assert data["metrics_reported"] == []
    for forbidden in ("precision", "recall", "accuracy"):
        assert forbidden not in r.text.lower(), f"响应里有不该有的指标: {forbidden}"


# ── 只读边界 ────────────────────────────────────────────────────────────────

def test_endpoint_does_not_write_to_db(app_client):
    """⚠️ 端点**零写入** —— 跑完所有表行数不变。"""
    conn = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")

    def counts():
        return {
            t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("articles", "articles_fts", "basket", "source_author_blocks",
                      "article_keyword_hits", "article_tags", "article_pins",
                      "cases", "events", "share_tasks")
        }

    before = counts()
    conn.close()

    app_client.get("/api/moderation/xbot/candidates")

    conn = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    after = counts()
    conn.close()
    assert after == before, "只读端点产生了写入"


def _db_path():
    from moni.db.connection import get_db_path

    return get_db_path()


def test_router_has_no_write_methods():
    """⚠️ 本 router **不得**挂载 POST / PUT / DELETE / PATCH。"""
    from moni.web.routes.moderation import router

    allowed = {"GET", "HEAD", "OPTIONS"}
    for route in router.routes:
        methods = getattr(route, "methods", set()) or set()
        assert methods <= allowed, (
            f"只读 router 上出现了写方法: {methods} ({route.path})"
        )


def test_route_source_has_no_write_sql():
    """⚠️ **静态断言**：路由源码里不得出现写库语句。"""
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "moni" / "web" / "routes" / "moderation.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = getattr(node, "body", [])
        if (
            body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            ds = body[0]
            for ln in range(ds.lineno, (ds.end_lineno or ds.lineno) + 1):
                doc_lines.add(ln)
    code_only = "\n".join(
        ln for i, ln in enumerate(src.read_text(encoding="utf-8").splitlines(), 1)
        if i not in doc_lines and not ln.lstrip().startswith("#")
    )
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ", "ALTER "):
        assert forbidden not in code_only, f"只读路由不该出现 {forbidden!r}"


def test_endpoint_does_not_use_article_keyword_hits(app_client):
    """⚠️ 裸词判定**不得**读 ``article_keyword_hits``（它每个 tag 只记首词）。

    ⇒ 反例: 「微信小程序」那篇若读了那张表会被误判成裸词。
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "moni" / "web" / "routes" / "moderation.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = getattr(node, "body", [])
        if (
            body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            ds = body[0]
            for ln in range(ds.lineno, (ds.end_lineno or ds.lineno) + 1):
                doc_lines.add(ln)
    code_only = "\n".join(
        ln for i, ln in enumerate(src.read_text(encoding="utf-8").splitlines(), 1)
        if i not in doc_lines and not ln.lstrip().startswith("#")
    )
    assert "article_keyword_hits" not in code_only, (
        "裸词判定不得读 article_keyword_hits"
    )
    # 行为断言: 命中「微信小程序」的文章不该被判成 only_bare_wechat
    data = app_client.get("/api/moderation/xbot/candidates").json()
    cands = {c["article_id"]: c for c in data["candidates"]}
    if "mini02" in cands:
        assert "only_bare_wechat" not in " ".join(cands["mini02"]["evidence_tokens"])


# ── limit 与缺省 ────────────────────────────────────────────────────────────

def test_limit_truncates_candidates(app_client):
    """⚠️ ``limit`` 生效。"""
    data = app_client.get("/api/moderation/xbot/candidates?limit=1").json()
    assert data["returned"] <= 1
    assert len(data["candidates"]) <= 1


def test_unknown_source_returns_empty_not_error(app_client):
    """⚠️ 信源不存在 ⇒ 返回空清单 + note, **不** 500。"""
    r = app_client.get("/api/moderation/xbot/candidates?source=nope_feed")
    assert r.status_code == 200
    data = r.json()
    assert data["candidate_count"] == 0
    assert "note" in data


def test_protected_article_ids_not_truncated_by_limit(app_client):
    """⚠️ **C-6**：保护清单是**全量**的, 不受 ``limit`` 截断。"""
    data = app_client.get("/api/moderation/xbot/candidates?limit=1").json()
    assert isinstance(data["protected_article_ids"], list)
    assert isinstance(data["unprotected_candidate_ids"], list)
