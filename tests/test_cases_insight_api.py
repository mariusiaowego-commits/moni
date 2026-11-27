"""案例详情返回 insight：优先指针 / 回退最新 done / insufficient / 脏 JSON 降级."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.web.server import create_app


@pytest.fixture()
def seeded(tmp_path: Path):
    db_file = tmp_path / "test_insight_api.db"
    set_db_path(db_file)
    migrate()
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at) VALUES (?,?,?,?,?)",
        ("art_1", "t", "u", "mpaypass", now),
    )
    conn.execute(
        """INSERT INTO cases (case_id, article_id, case_date_precision, subject, case_type,
               case_action, case_action_source, reference_value, extraction_status,
               review_status, case_lifecycle, extraction_attempts, normalize_version,
               summary, evidence_snippet, extracted_at, created_at, updated_at)
           VALUES ('MN-AI-2601-0001', 'art_1', 'month', '招商银行', 'AI', 'launch',
                   'rule_text', 'high', 'completed', 'confirmed', 'verified', 1, 1,
                   '招行上线智能投顾', '', ?, ?, ?)""",
        (now, now, now),
    )
    conn.commit()
    client = TestClient(create_app())
    yield client, conn
    close_db()


def _add_insight(conn, body: dict, *, status: str = "done", prompt_version: str = "v1") -> int:
    with conn:
        cur = conn.execute(
            """INSERT INTO case_insights
                   (case_id, prompt_version, input_hash, provider, model, status, body_json, created_at)
               VALUES ('MN-AI-2601-0001', ?, ?, 'ark', 'ark-code-latest', ?, ?, datetime('now'))""",
            (prompt_version, f"h-{uuid.uuid4().hex[:8]}", status, json.dumps(body, ensure_ascii=False)),
        )
    return int(cur.lastrowid)


class TestCaseInsight:
    def test_detail_returns_insight(self, seeded):
        client, conn = seeded
        iid = _add_insight(conn, {
            "what": "上线智能投顾", "how": ["自研模型", "分层服务"],
            "risks": ["合规"], "reusable": ["分层"], "fit": "零售客群",
        })
        conn.execute("UPDATE cases SET insight_id = ? WHERE case_id = 'MN-AI-2601-0001'", (iid,))
        conn.commit()
        body = client.get("/api/cases/MN-AI-2601-0001").json()
        assert body["insight"]["what"] == "上线智能投顾"
        assert body["insight"]["how"] == ["自研模型", "分层服务"]
        assert body["insight"]["model"] == "ark-code-latest"

    def test_no_insight_is_none(self, seeded):
        client, _ = seeded
        assert client.get("/api/cases/MN-AI-2601-0001").json()["insight"] is None

    def test_stale_pointer_falls_back_to_latest_done(self, seeded):
        client, conn = seeded
        old = _add_insight(conn, {"what": "旧解读"})
        new = _add_insight(conn, {"what": "新解读"})
        conn.execute(
            "UPDATE case_insights SET status = 'stale' WHERE id = ?", (new,)
        )
        conn.execute("UPDATE cases SET insight_id = ? WHERE case_id = 'MN-AI-2601-0001'", (new,))
        conn.commit()
        body = client.get("/api/cases/MN-AI-2601-0001").json()
        assert body["insight"]["what"] == "旧解读"

    def test_pointer_wins_when_both_done(self, seeded):
        client, conn = seeded
        old = _add_insight(conn, {"what": "旧解读"})
        _add_insight(conn, {"what": "新解读"})
        conn.execute("UPDATE cases SET insight_id = ? WHERE case_id = 'MN-AI-2601-0001'", (old,))
        conn.commit()
        body = client.get("/api/cases/MN-AI-2601-0001").json()
        assert body["insight"]["what"] == "旧解读"

    def test_insufficient_passthrough(self, seeded):
        client, conn = seeded
        iid = _add_insight(conn, {"insufficient": True, "reason": "只有标题"})
        conn.execute("UPDATE cases SET insight_id = ? WHERE case_id = 'MN-AI-2601-0001'", (iid,))
        conn.commit()
        body = client.get("/api/cases/MN-AI-2601-0001").json()
        assert body["insight"]["insufficient"] is True

    def test_dirty_json_degrades_to_none(self, seeded):
        client, conn = seeded
        iid = _add_insight(conn, {})
        conn.execute(
            "UPDATE case_insights SET body_json = '{not json' WHERE id = ?", (iid,)
        )
        conn.execute("UPDATE cases SET insight_id = ? WHERE case_id = 'MN-AI-2601-0001'", (iid,))
        conn.commit()
        resp = client.get("/api/cases/MN-AI-2601-0001")
        assert resp.status_code == 200
        assert resp.json()["insight"] is None
