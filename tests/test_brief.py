"""v1.0.10 one-click brief: API + enrichment + formats."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Point at *this* checkout; ~/dev/moni silently tests main's package when
# the worktree has newer modules.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.share.enrichment import rebuild_article_content, run_enrichment
from moni.share.formats import validate_manifest
from moni.web.server import create_app


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "brief_test.db"
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


def _insert_article(aid: str, title: str, body: str = "正文足够长，用于通过简报就绪校验。" * 20) -> None:
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    # Seed summary + BV + FC so v1.0.15 readiness / exclusion gates pass
    conn.execute(
        """
        INSERT INTO articles (
          id, title, url, source, publisher, published_at, fetched_at, raw_content,
          ai_summary, business_value_score, business_value_note, fact_check_conclusion
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            aid,
            title if len(title) >= 4 else f"标题-{aid}",
            f"https://example.com/{aid}",
            "testsrc",
            "Pub",
            now,
            now,
            body,
            f"{title}的摘要：包含核心事实与可对标结论，长度足够用于简报卡片展示。",
            72,
            "对同业数字化有对标参考价值",
            "无需核查",
        ),
    )
    conn.commit()


def _pin(aid: str) -> None:
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO article_pins (article_id, pinned_at) VALUES (?, ?)",
        (aid, now),
    )
    conn.commit()


class TestBriefSummaryCompact:
    def test_is_long_summary(self):
        from moni.share.enrichment import is_long_summary

        assert not is_long_summary("一句短摘要。")
        assert not is_long_summary("第一句。第二句。")
        assert is_long_summary("第一句。第二句。第三句还很长。")
        assert is_long_summary("x" * 120)

    def test_compact_only_when_long(self, fresh_db: Path):
        from moni.share.enrichment import _ensure_brief_summary, pick_source_summary
        import sqlite3

        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        long_sum = "甲。" * 5 + "这是超长摘要需要压缩处理的内容。"
        conn.execute(
            """
            INSERT INTO articles (id, title, url, source, publisher, published_at, fetched_at, raw_content, ai_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("bs1", "标题", "https://e.com/1", "t", "P", now, now, "正文" * 50, long_sum),
        )
        conn.execute(
            """
            INSERT INTO articles (id, title, url, source, publisher, published_at, fetched_at, raw_content, ai_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("bs2", "短", "https://e.com/2", "t", "P", now, now, "正文" * 50, "就一句够短。"),
        )
        conn.commit()

        def fake_hermes(prompt: str, timeout_sec: int = 120, extra_args=None):
            return (
                '{"id": "bs1", "summary": "压短后的摘要一句。第二句保留要点。"}',
                0,
            )

        import moni.share.enrichment as en

        orig = en._invoke_hermes
        en._invoke_hermes = fake_hermes
        try:
            budget = [120.0]
            c = sqlite3.connect(fresh_db)
            c.row_factory = sqlite3.Row
            r1 = c.execute("SELECT * FROM articles WHERE id='bs1'").fetchone()
            r2 = c.execute("SELECT * FROM articles WHERE id='bs2'").fetchone()
            assert _ensure_brief_summary(c, r1, [], budget)
            assert _ensure_brief_summary(c, r2, [], budget)
            b1 = c.execute("SELECT brief_summary FROM articles WHERE id='bs1'").fetchone()[0]
            b2 = c.execute("SELECT brief_summary FROM articles WHERE id='bs2'").fetchone()[0]
            c.close()
            assert "压短" in b1
            assert b2 == "就一句够短。"
            # full ai_summary untouched
            full = get_db().execute("SELECT ai_summary FROM articles WHERE id='bs1'").fetchone()[0]
            assert full == long_sum
        finally:
            en._invoke_hermes = orig


class TestFormatsBriefFlags:
    def test_brief_flags_accepted(self):
        fmt, payload = validate_manifest(
            {
                "format": "markdown",
                "version": "1.0",
                "payload": {
                    "article_ids": ["a1"],
                    "brief": True,
                    "include_business_value": True,
                    "include_fact_check": True,
                    "include_quality_score": False,
                },
            }
        )
        assert fmt == "markdown"
        assert payload["brief"] is True
        assert payload["include_business_value"] is True


class TestBriefApi:
    def test_brief_no_pins_400(self, client: TestClient):
        r = client.post("/api/share/brief", json={"format": "markdown"})
        assert r.status_code == 400
        body = r.json()
        # BadRequestError shape varies; message should mention no_pins
        text = json.dumps(body, ensure_ascii=False)
        assert "no_pins" in text or "Pin" in text or "pin" in text

    def test_brief_creates_and_launches(self, client: TestClient, fresh_db: Path):
        _insert_article("art-b1", "标题一")
        _insert_article("art-b2", "标题二")
        _pin("art-b1")
        _pin("art-b2")

        fake_launch = MagicMock()
        fake_launch.started = True
        fake_launch.already_running = False
        fake_launch.pid = 4242
        fake_launch.error = None
        fake_launch.log_path = "/tmp/x"

        with patch("moni.web.routes.share.launch_worker", return_value=fake_launch) as lw:
            with patch("moni.web.routes.share.write_manifest", return_value="brief-id-1") as wm:
                # Also need validate to pass — real validate is fine
                r = client.post("/api/share/brief", json={"format": "markdown"})
                # write_manifest is patched so DB row may not exist; still 201
                assert r.status_code == 201, r.text
                data = r.json()
                assert data["share_id"] == "brief-id-1"
                assert data["article_count"] == 2
                assert data["worker"]["started"] is True
                lw.assert_called_once()
                # payload should include both pins + brief flags
                args, kwargs = wm.call_args
                payload = kwargs.get("validated_payload") or (
                    args[1] if len(args) > 1 else None
                )
                # write_manifest(share_id=None, validated_payload=..., format_name=...)
                if payload is None:
                    payload = wm.call_args.kwargs.get("validated_payload")
                assert payload is not None
                assert set(payload["article_ids"]) == {"art-b1", "art-b2"}
                assert payload["brief"] is True
                assert payload["include_business_value"] is True
                assert payload["include_fact_check"] is True

    def test_brief_unready_articles_400(self, client: TestClient, fresh_db: Path):
        """v1.0.15: pins with neither summary nor body → 400 articles_not_ready."""
        _insert_article("thin-a", "空标题A", body="")
        _insert_article("thin-b", "空标题B", body="x")  # still thin < 200
        # overwrite raw to empty
        conn = get_db()
        conn.execute("UPDATE articles SET raw_content='' WHERE id IN ('thin-a','thin-b')")
        conn.commit()
        _pin("thin-a")
        _pin("thin-b")
        r = client.post("/api/share/brief", json={"format": "html_landing"})
        assert r.status_code == 400, r.text
        body = r.json()
        assert body.get("message") == "articles_not_ready"
        assert body.get("details", {}).get("count", 0) >= 1

    def test_brief_duplicate_active_409(self, client: TestClient, fresh_db: Path):
        """v1.0.14: second brief with same pin set while pending → 409 unless force."""
        _insert_article("d1", "A")
        _insert_article("d2", "B")
        _pin("d1")
        _pin("d2")
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO share_tasks (id, format, article_ids, status, created_at)
            VALUES (?, 'html_landing', ?, 'pending', ?)
            """,
            ("dup-task-1", json.dumps(["d1", "d2"]), now),
        )
        conn.commit()

        fake_launch = MagicMock()
        fake_launch.started = True
        fake_launch.already_running = False
        fake_launch.pid = 1
        fake_launch.error = None
        fake_launch.log_path = "/tmp/x"

        with patch("moni.web.routes.share.launch_worker", return_value=fake_launch):
            with patch("moni.web.routes.share.write_manifest", return_value="should-not") as wm:
                r = client.post("/api/share/brief", json={"format": "html_landing"})
                assert r.status_code == 409, r.text
                body = r.json()
                assert body.get("message") == "duplicate_active_brief"
                assert body.get("details", {}).get("share_id") == "dup-task-1"
                wm.assert_not_called()

                r2 = client.post(
                    "/api/share/brief",
                    json={"format": "html_landing", "force": True},
                )
                assert r2.status_code == 201, r2.text
                wm.assert_called_once()


class TestShareTaskControl:
    """v1.0.14: pause / resume / cancel."""

    def _seed_task(self, task_id: str, status: str = "pending") -> None:
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO share_tasks (id, format, article_ids, status, created_at)
            VALUES (?, 'markdown', ?, ?, ?)
            """,
            (task_id, json.dumps(["a1"]), status, now),
        )
        conn.commit()

    def test_pause_pending(self, client: TestClient, fresh_db: Path):
        self._seed_task("t-pause", "pending")
        r = client.post("/api/share/tasks/t-pause/pause")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "paused"

    def test_cancel_pending(self, client: TestClient, fresh_db: Path):
        self._seed_task("t-cancel", "pending")
        r = client.post("/api/share/tasks/t-cancel/cancel")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "cancelled"
        assert "cancelled" in (r.json().get("error") or "")

    def test_resume_paused(self, client: TestClient, fresh_db: Path):
        self._seed_task("t-resume", "paused")
        fake_launch = MagicMock()
        fake_launch.started = True
        fake_launch.already_running = False
        fake_launch.pid = 9
        fake_launch.error = None
        fake_launch.log_path = "/tmp/x"
        with patch("moni.web.routes.share.launch_worker", return_value=fake_launch) as lw:
            r = client.post("/api/share/tasks/t-resume/resume")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "pending"
            lw.assert_called_once()

    def test_cancel_done_409(self, client: TestClient, fresh_db: Path):
        self._seed_task("t-done", "done")
        r = client.post("/api/share/tasks/t-done/cancel")
        assert r.status_code == 409


class TestEnrichment:
    def test_business_value_writeback(self, fresh_db: Path, monkeypatch):
        # v1.0.51 (audit fix): ``run_enrichment`` calls
        # ``_enqueue_missing_web_fact_checks`` WITHOUT passing ``wait``,
        # and that helper's default is ``wait=True`` — so the call
        # polls ``llm_jobs`` for up to **120 s** waiting for a web
        # fact-check that never lands in a unit test. That single
        # line was ~50% of the whole CI run.
        #
        # The in-code comment says the step is "non-blocking ... Never
        # wait for job completion", i.e. the default contradicts the
        # documented intent. We do NOT silently change the product
        # default here (that is a behaviour decision) — we pin
        # ``wait=False`` for this test so it exercises the enqueue
        # logic without the 120 s poll.
        import functools

        import moni.share.enrichment as enrichment

        monkeypatch.setattr(
            enrichment,
            "_enqueue_missing_web_fact_checks",
            functools.partial(
                enrichment._enqueue_missing_web_fact_checks,
                wait=False,
            ),
        )

        _insert_article("e1", "银行 AI 助手上线", "支付机构推进智能客服" * 40)
        _insert_article("e2", "短文", "hi")  # thin

        def fake_hermes(prompt: str, timeout_sec: int = 120, extra_args=None):
            import re

            mid = re.search(r"ID: (\S+)", prompt)
            aid = mid.group(1) if mid else "e1"
            if "取证助手" in prompt:
                return (
                    json.dumps(
                        {
                            "evidence": [
                                {"id": "e1", "quote": "支付机构推进智能客服支付机构推进智能客服"}
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    0,
                )
            if "评分员" in prompt or "上一轮打分没有通过检查" in prompt:
                from moni.scoring.rubric import KEYS

                dims = {
                    key: {
                        "score": 6,
                        "evidence_ids": ["e1"],
                        "reason": "正文写到机构动作与客户对象",
                    }
                    for key in KEYS
                }
                return json.dumps({"dimensions": dims}, ensure_ascii=False), 0
            if "检查员" in prompt:
                return json.dumps({"pass": True, "reasons": []}), 0
            if "事实核查" in prompt:
                return (
                    json.dumps(
                        {
                            "id": aid,
                            "conclusion": "无需核查",
                            "method": "llm_inference",
                            "note": "",
                        },
                        ensure_ascii=False,
                    ),
                    0,
                )
            if "摘要" in prompt:
                return (
                    '```json\n{"id": "%s", "summary": "测试摘要三句话。要点。影响。"}\n```'
                    % aid,
                    0,
                )
            return ("{}", 0)

        # write a mini manifest
        mp = fresh_db.parent / "m.json"
        mp.write_text(
            json.dumps(
                {
                    "payload": {
                        "include_abstract": True,
                        "include_fact_check": True,
                        "include_business_value": True,
                        "brief": True,
                    }
                }
            ),
            encoding="utf-8",
        )

        stats = run_enrichment(
            str(fresh_db),
            ["e1", "e2"],
            manifest_path=str(mp),
            invoke_hermes=fake_hermes,
        )
        assert stats["business_value"] >= 1

        conn = sqlite3.connect(fresh_db)
        rows = {
            r[0]: r
            for r in conn.execute(
                "SELECT id, business_value_score, business_value_note, has_fact_check, ai_summary FROM articles"
            ).fetchall()
        }
        conn.close()
        # thin body → insufficient, score stays NULL (design §6.1)
        assert rows["e2"][1] is None
        assert rows["e1"][1] is not None

        text = rebuild_article_content(str(fresh_db), ["e1", "e2"], brief=True)
        assert "业务价值" in text
        # e1 should appear before e2 if e1 score higher
        if rows["e1"][1] and rows["e2"][1] and rows["e1"][1] > rows["e2"][1]:
            assert text.index("银行 AI") < text.index("短文")

    def test_rebuild_sorts_by_bv(self, fresh_db: Path):
        _insert_article("s1", "低价值", "x" * 300)
        _insert_article("s2", "高价值", "y" * 300)
        conn = get_db()
        conn.execute(
            "UPDATE articles SET business_value_score=?, business_value_note=? WHERE id=?",
            (30, "低", "s1"),
        )
        conn.execute(
            "UPDATE articles SET business_value_score=?, business_value_note=? WHERE id=?",
            (90, "高", "s2"),
        )
        conn.commit()
        text = rebuild_article_content(str(fresh_db), ["s1", "s2"], brief=True)
        assert text.index("高价值") < text.index("低价值")
