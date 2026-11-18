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
from moni.share.enrichment import (
    _enqueue_missing_web_fact_checks,
    rebuild_article_content,
    run_enrichment,
)
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


def _run_brief_enrichment(fresh_db: Path, article_ids: list[str], monkeypatch=None) -> dict:
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
    # sprint-11 v1.0.30: sync wait is the new default; these legacy tests
    # assert the *enqueue* contract (not the wait), so short-circuit the
    # poll loop to avoid a real 120s wait in tests.
    import moni.share.enrichment as _enr

    if monkeypatch is not None:
        monkeypatch.setattr(_enr, "_wait_for_jobs", lambda *a, **k: {})
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

    _run_brief_enrichment(fresh_db, ["a1", "a2"], monkeypatch)

    rows = get_db().execute(
        "SELECT article_id, status FROM llm_jobs WHERE task_type = 'fact_check_plus'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["article_id"] == "a2"
    assert rows[0]["status"] == "pending"


def test_one_image_with_deep_fact_check_enqueues_web_check(fresh_db, monkeypatch):
    """v1.0.101: 分享图勾了「深度事实核查」也要走联网档，而不是只留离线推断。"""
    _insert_article("a1", web_checked=False)
    monkeypatch.setattr("moni.llm.jobs.ensure_worker_started", lambda: None)
    manifest = fresh_db.parent / "png-fact-check-manifest.json"
    manifest.write_text(
        json.dumps({"payload": {
            "include_abstract": True,
            "include_fact_check": True,
            "include_business_value": False,
            "brief": False,
        }}),
        encoding="utf-8",
    )
    import moni.share.enrichment as _enr

    monkeypatch.setattr(_enr, "_wait_for_jobs", lambda *a, **k: {})

    run_enrichment(
        str(fresh_db),
        ["a1"],
        manifest_path=str(manifest),
        invoke_hermes=_fake_hermes,
    )

    rows = get_db().execute(
        "SELECT article_id, status FROM llm_jobs WHERE task_type = 'fact_check_plus'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["article_id"] == "a1"


def test_brief_web_mode_skips_articles_already_checked(fresh_db, monkeypatch):
    """2 篇都有 has_web_fact_check -> llm_jobs 不新增."""
    _insert_article("a1", web_checked=True)
    _insert_evidence("a1", seq=0)
    _insert_article("a2", web_checked=True)
    _insert_evidence("a2", seq=0)
    monkeypatch.setattr("moni.llm.jobs.ensure_worker_started", lambda: None)

    _run_brief_enrichment(fresh_db, ["a1", "a2"], monkeypatch)

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

    stats = _run_brief_enrichment(fresh_db, ["a1"], monkeypatch)

    assert isinstance(stats, dict)
    row = get_db().execute(
        "SELECT status FROM llm_jobs WHERE task_type = 'fact_check_plus'"
    ).fetchone()
    assert row is not None
    assert row["status"] == "pending"


# ---------------------------------------------------------------------------
# Sprint-11 (v1.0.30): 简报 worker 同步化 — 同步等待 job 完成 (spec: PLAN §验收)
# 契约: wait=True 时 enqueue 后轮询 llm_jobs.status (2s/次, 总超时 120s);
#       done → evidence 就绪; failed → 该篇 articles.fact_check_note 标'核查失败';
#       超时 → 该篇 articles.fact_check_note 标'核查超时'。三种情况都不阻塞简报。
# 测试不真调 LLM: unittest.mock 替换 time.sleep (完成/失败副作用) 与
# time.monotonic (推进超时), 加速轮询.
# ---------------------------------------------------------------------------


def test_sync_wait_job_done(fresh_db, monkeypatch):
    """wait=True 轮询到 job done: 简报生成时 evidence 就绪 (非下次补全)."""
    _insert_article("a1", web_checked=False)
    monkeypatch.setattr("moni.llm.jobs.ensure_worker_started", lambda: None)

    def _complete_job(_secs: float) -> None:
        # 模拟 worker 完成: 第一次轮询 sleep 时把 job 置 done + 落 evidence.
        conn = get_db()
        row = conn.execute(
            "SELECT id FROM llm_jobs "
            "WHERE article_id = 'a1' AND task_type = 'fact_check_plus'"
        ).fetchone()
        jid = row["id"]
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE llm_jobs SET status = 'done' WHERE id = ?", (jid,))
        conn.execute(
            """
            INSERT INTO fact_check_evidence
                (id, article_id, job_id, query, url, domain, snippet, fetched_at,
                 model, provider, conclusion, rationale, evidence_seq, created_at)
            VALUES (?, 'a1', ?, '央行 金融数据', 'https://www.pbc.gov.cn/notice/1',
                    'pbc.gov.cn', '央行发布 7 月金融统计数据', ?, 'MiniMax-M3',
                    'minimax-cn', '【已核实】', '官方公告', 0, ?)
            """,
            ("ev-sync-done", jid, now, now),
        )
        conn.commit()

    monkeypatch.setattr("time.sleep", _complete_job)

    n = _enqueue_missing_web_fact_checks(
        get_db(), ["a1"],
        db_path=str(fresh_db), provider="minimax-cn", model="MiniMax-M3",
        wait=True,
    )

    assert n == 1
    job = get_db().execute(
        "SELECT status FROM llm_jobs WHERE article_id = 'a1' "
        "AND task_type = 'fact_check_plus'"
    ).fetchone()
    assert job["status"] == "done"
    evidence = get_db().execute(
        "SELECT COUNT(*) FROM fact_check_evidence WHERE article_id = 'a1'"
    ).fetchone()[0]
    assert evidence == 1


def test_sync_job_failed_degrades(fresh_db, monkeypatch):
    """wait=True 且 job failed: 该篇标'核查失败', 简报生成不阻塞."""
    _insert_article("a1", web_checked=False)
    monkeypatch.setattr("moni.llm.jobs.ensure_worker_started", lambda: None)

    def _fail_job(_secs: float) -> None:
        # 模拟 worker 失败: 第一次轮询 sleep 时把 job 置 failed.
        conn = get_db()
        conn.execute(
            "UPDATE llm_jobs SET status = 'failed', error = 'llm error' "
            "WHERE article_id = 'a1' AND task_type = 'fact_check_plus'"
        )
        conn.commit()

    monkeypatch.setattr("time.sleep", _fail_job)

    n = _enqueue_missing_web_fact_checks(
        get_db(), ["a1"],
        db_path=str(fresh_db), provider="minimax-cn", model="MiniMax-M3",
        wait=True,
    )

    assert n == 1  # 调用返回 = 不阻塞简报
    job = get_db().execute(
        "SELECT status, error FROM llm_jobs WHERE article_id = 'a1' "
        "AND task_type = 'fact_check_plus'"
    ).fetchone()
    assert job["status"] == "failed"
    note = get_db().execute(
        "SELECT fact_check_note FROM articles WHERE id = 'a1'"
    ).fetchone()["fact_check_note"]
    assert note and "核查失败" in note  # 契约: 该篇标'核查失败'


def test_sync_timeout_degrades(fresh_db, monkeypatch):
    """wait=True 轮询超过 120s: 该篇标'核查超时', 简报生成不阻塞."""
    _insert_article("a1", web_checked=False)
    monkeypatch.setattr("moni.llm.jobs.ensure_worker_started", lambda: None)
    monkeypatch.setattr("time.sleep", lambda _secs: None)
    ticks = iter([0.0, 0.0, 0.0, 121.0])  # deadline=120, 第 3 轮越过
    monkeypatch.setattr("time.monotonic", lambda: next(ticks))

    n = _enqueue_missing_web_fact_checks(
        get_db(), ["a1"],
        db_path=str(fresh_db), provider="minimax-cn", model="MiniMax-M3",
        wait=True,
    )

    assert n == 1  # 调用返回 = 不阻塞简报
    job = get_db().execute(
        "SELECT status FROM llm_jobs WHERE article_id = 'a1' "
        "AND task_type = 'fact_check_plus'"
    ).fetchone()
    assert job["status"] == "pending"  # job 未完成, 单篇降级
    note = get_db().execute(
        "SELECT fact_check_note FROM articles WHERE id = 'a1'"
    ).fetchone()["fact_check_note"]
    assert note and "核查超时" in note  # 契约: 该篇标'核查超时'
