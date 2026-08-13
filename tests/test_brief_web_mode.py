"""tests/test_brief_web_mode.py - v1.0.24 简报 web 模式 (事实核查证据嵌入简报).

Spec: docs/specs/2026-08-12-brief-web-mode-v1.0.24.md §5 (7 个测试).
v1.0.24 实现未落地 (spec draft) — 测试按 §3/§4 数据契约写, 实现落地后转 GREEN.
入口:
  * run_enrichment           — 简报富化时给缺联网核查的文章 enqueue fact_check_plus
  * rebuild_article_content  — 简报 MD 内容 (证据段)
  * render_brief_html        — 简报 HTML 内容 (证据段)
  * POST /api/share/brief    — 简报生成后写回 articles.evidence_rendered_at
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate

# v1.0.22 导入顺序: routes 包必须先于 moni.llm.jobs 加载 (jobs.py 顶部
# re-export 白名单常量; 否则 monkeypatch "moni.llm.jobs.*" 触发循环 import).
import moni.web.routes.fact_check_plus  # noqa: F401,E402

from moni.share.brief_html import render_brief_html
from moni.share.enrichment import rebuild_article_content, run_enrichment
from moni.web.server import create_app


# ---------------------------------------------------------------------------
# Fixtures (同 tests/test_fact_check_plus.py 模式)
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


def _insert_article(
    aid: str,
    *,
    web_checked: bool = False,
    conclusion: str = "无需核查",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    # DB 无 has_web_fact_check 列 (那是前端字段); 联网核查状态 = has_fact_check=1
    # + fact_check_method='web_search' + fact_check_evidence 行.
    method = "web_search" if web_checked else None
    conn.execute(
        """
        INSERT INTO articles (
            id, title, url, source, publisher, published_at, fetched_at,
            raw_content, ai_summary, business_value_score, business_value_note,
            fact_check_conclusion, has_fact_check, fact_check_method
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            aid,
            f"标题-{aid}",
            f"https://example.com/{aid}",
            "testsrc",
            "Pub",
            now,
            now,
            f"{aid} 的正文足够长，用于通过简报就绪校验。" * 10,
            f"{aid} 的摘要：包含核心事实与可对标结论，长度足够用于简报卡片展示。",
            72,
            "对同业数字化有对标参考价值",
            conclusion,
            1 if web_checked else 0,
            method,
        ),
    )
    conn.commit()


def _insert_evidence(
    aid: str, seq: int, *, url: str = "https://www.pbc.gov.cn/notice/1"
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        """
        INSERT INTO fact_check_evidence
            (id, article_id, job_id, query, url, domain, snippet, fetched_at,
             model, provider, conclusion, rationale, evidence_seq, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (f"ev-{aid}-{seq}", aid, f"job-{seq}", f"query-{seq}", url, "pbc.gov.cn",
         f"snippet-{seq}", now, "MiniMax-M3", "minimax-cn", "【已核实】", "r", seq, now),
    )
    conn.commit()


def _pin(aid: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    get_db().execute(
        "INSERT INTO article_pins (article_id, pinned_at) VALUES (?, ?)",
        (aid, now),
    )
    get_db().commit()


def _fake_hermes(prompt: str, **kwargs) -> tuple[str, int]:
    """通用 mock: 让 run_enrichment 全流程快速完成, 不真调 LLM."""
    if "核查" in prompt:
        return (
            json.dumps(
                {"id": "x", "conclusion": "无需核查",
                 "method": "llm_inference", "note": ""},
                ensure_ascii=False,
            ),
            0,
        )
    if "摘要" in prompt:
        return ('```json\n{"id": "x", "summary": "测试摘要三句话。要点。影响。"}\n```', 0)
    return ("{}", 0)


def _run_brief_enrichment(fresh_db: Path, article_ids: list[str]) -> dict:
    manifest = fresh_db.parent / "brief-web-mode-manifest.json"
    manifest.write_text(
        json.dumps({"payload": {
            "include_abstract": True,
            "include_fact_check": True,
            "include_business_value": True,
            "brief": True,
        }}),
        encoding="utf-8",
    )
    return run_enrichment(
        str(fresh_db),
        article_ids,
        manifest_path=str(manifest),
        invoke_hermes=_fake_hermes,
    )


# ---------------------------------------------------------------------------
# 7 个测试 (spec §5)
# ---------------------------------------------------------------------------


def test_brief_web_mode_enqueues_missing_fact_check_plus(fresh_db, monkeypatch):
    """简报含 2 篇, 1 篇已联网核查 / 1 篇没有 -> 只 enqueue 1 个 fact_check_plus."""
    _insert_article("a1", web_checked=True)
    _insert_evidence("a1", seq=0)
    _insert_article("a2", web_checked=False)
    monkeypatch.setattr("moni.llm.jobs.ensure_worker_started", lambda: None)

    _run_brief_enrichment(fresh_db, ["a1", "a2"])

    rows = get_db().execute(
        "SELECT article_id, status FROM llm_jobs WHERE task_type = 'fact_check_plus'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["article_id"] == "a2"
    assert rows[0]["status"] == "pending"


def test_brief_web_mode_skips_articles_already_checked(fresh_db, monkeypatch):
    """2 篇都有 has_web_fact_check -> llm_jobs 不新增."""
    _insert_article("a1", web_checked=True)
    _insert_evidence("a1", seq=0)
    _insert_article("a2", web_checked=True)
    _insert_evidence("a2", seq=0)
    monkeypatch.setattr("moni.llm.jobs.ensure_worker_started", lambda: None)

    _run_brief_enrichment(fresh_db, ["a1", "a2"])

    count = get_db().execute(
        "SELECT COUNT(*) FROM llm_jobs WHERE task_type = 'fact_check_plus'"
    ).fetchone()[0]
    assert count == 0


def test_brief_web_mode_renders_evidence_in_md_template(fresh_db):
    """简报 MD 含 '事实核查证据' 段 + evidence URL + rollup."""
    _insert_article("a1", web_checked=True, conclusion="【已核实】")
    _insert_evidence("a1", seq=0, url="https://www.pbc.gov.cn/notice/1")

    text = rebuild_article_content(str(fresh_db), ["a1"], brief=True)

    assert "事实核查证据" in text
    assert "已联网核查" in text
    assert "https://www.pbc.gov.cn/notice/1" in text


def test_brief_web_mode_renders_evidence_in_html_template(fresh_db):
    """简报 HTML 含 '事实核查证据' 段 + evidence URL."""
    _insert_article("a1", web_checked=True, conclusion="【已核实】")
    _insert_evidence("a1", seq=0, url="https://www.pbc.gov.cn/notice/1")

    html = render_brief_html(str(fresh_db), ["a1"], title="同业简报 — test")

    assert "事实核查证据" in html
    assert "已联网核查" in html
    assert "https://www.pbc.gov.cn/notice/1" in html


def test_brief_web_mode_failure_articles_marked_as_unverified(fresh_db):
    """evidence 解析失败/无证据 -> 该篇标'未联网核查' + rollup 不阻塞."""
    _insert_article("a1", web_checked=True)
    _insert_evidence("a1", seq=0)
    _insert_article("a2", web_checked=True)  # 有标记但无可用 evidence 行

    text = rebuild_article_content(str(fresh_db), ["a1", "a2"], brief=True)

    assert "事实核查证据" in text
    assert "已联网核查" in text
    assert "未联网核查" in text


def test_brief_web_mode_writes_evidence_rendered_at(fresh_db, client, monkeypatch):
    """POST /api/share/brief 生成简报后 -> articles.evidence_rendered_at 非 NULL."""
    _insert_article("a1", web_checked=True)
    _insert_evidence("a1", seq=0)
    _insert_article("a2", web_checked=True)
    _insert_evidence("a2", seq=0)
    _pin("a1")
    _pin("a2")
    fake_launch = MagicMock()
    fake_launch.started = True
    fake_launch.already_running = False
    fake_launch.pid = 4242
    fake_launch.error = None
    fake_launch.log_path = "/tmp/x"
    monkeypatch.setattr("moni.web.routes.share.launch_worker", lambda: fake_launch)
    monkeypatch.setattr(
        "moni.web.routes.share.write_manifest",
        lambda **kw: "brief-web-mode-test-id",
    )

    r = client.post("/api/share/brief", json={"format": "markdown", "force": True})

    assert r.status_code == 201, r.text
    rows = get_db().execute(
        "SELECT evidence_rendered_at FROM articles ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert all(row["evidence_rendered_at"] for row in rows)


def test_brief_web_mode_does_not_block_on_fact_check_plus_pending(fresh_db, monkeypatch):
    """enqueue 后简报继续生成, 不等 fact_check_plus done (job 保持 pending)."""
    _insert_article("a1", web_checked=False)
    monkeypatch.setattr("moni.llm.jobs.ensure_worker_started", lambda: None)

    stats = _run_brief_enrichment(fresh_db, ["a1"])

    assert isinstance(stats, dict)
    row = get_db().execute(
        "SELECT status FROM llm_jobs WHERE task_type = 'fact_check_plus'"
    ).fetchone()
    assert row is not None
    assert row["status"] == "pending"
