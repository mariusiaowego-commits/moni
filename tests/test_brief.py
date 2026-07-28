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

sys.path.insert(0, os.path.expanduser("~/dev/moni"))

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


def _insert_article(aid: str, title: str, body: str = "正文足够长" * 30) -> None:
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO articles (id, title, url, source, publisher, published_at, fetched_at, raw_content)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (aid, title, f"https://example.com/{aid}", "testsrc", "Pub", now, now, body),
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


class TestEnrichment:
    def test_business_value_writeback(self, fresh_db: Path):
        _insert_article("e1", "银行 AI 助手上线", "支付机构推进智能客服" * 40)
        _insert_article("e2", "短文", "hi")  # thin

        def fake_hermes(prompt: str, timeout_sec: int = 120, extra_args=None):
            import re

            mid = re.search(r"ID: (\S+)", prompt)
            aid = mid.group(1) if mid else "e1"
            if "业务价值" in prompt or "对自身业务" in prompt:
                return (
                    json.dumps(
                        {
                            "id": aid,
                            "score": 72 if aid == "e1" else 20,
                            "note": "对支付侧可对标智能客服。",
                        },
                        ensure_ascii=False,
                    ),
                    0,
                )
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
        # thin content path sets BV without hermes necessarily for e2
        assert rows["e2"][1] is not None  # score
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
