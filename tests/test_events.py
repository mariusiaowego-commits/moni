"""tests/test_events.py — F5 行业事件日历测试。

覆盖：migration / normalize / pipeline / API / CHECK constraint / UNIQUE 去重 / 事务回滚
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.expanduser("~/dev/moni/wt/t1-db"))

from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.events.normalize import normalize_event
from moni.web.server import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """每个测试都用干净的临时 DB。"""
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    migrate()
    yield
    close_db()
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod
    conn_mod._CON = None
    conn_mod._DB_PATH = None
    mig_mod._CON = None


@pytest.fixture
def client():
    return TestClient(create_app())


def _insert_test_article(conn, **kwargs):
    """插入一篇测试用 article，返回 id。"""
    aid = kwargs.get("id", "test_001")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO articles (id, title, url, source, fetched_at, raw_content, published_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            aid,
            kwargs.get("title", "测试文章"),
            kwargs.get("url", f"https://example.com/{aid}"),
            kwargs.get("source", "test_rss"),
            kwargs.get("fetched_at", now),
            kwargs.get("raw_content", "这是测试文章内容。"),
            kwargs.get("published_at", now),
        ),
    )
    conn.commit()
    return aid


def _mk_raw(subject="测试", event_date="2026-08-07", event_type="regulation", **kw):
    """构造 normalize_event 入参（字段名和 LLM 返回一致）。"""
    raw = {"subject": subject, "event_date": event_date, "event_type": event_type}
    raw.update(kw)
    return raw


# ═══════════════════════════════════════════════════════════════════════════
# T1: Migration 测试
# ═══════════════════════════════════════════════════════════════════════════

class TestMigration:
    def test_events_table_exists(self, fresh_db):
        conn = get_db()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
        ).fetchone()
        assert row is not None

    def test_events_columns(self, fresh_db):
        conn = get_db()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(events)")]
        expected = [
            "event_id", "article_id", "event_date", "event_date_precision",
            "subject", "subject_raw", "event_type", "summary",
            "extraction_confidence", "review_status", "extraction_status",
            "extraction_attempts", "last_error", "extracted_at",
            "evidence_snippet", "normalize_version", "created_at",
        ]
        for c in expected:
            assert c in cols, f"column {c} missing"

    def test_events_indexes(self, fresh_db):
        conn = get_db()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='events'"
        ).fetchall()
        indexes = [r[0] for r in rows]
        expected = [
            "idx_events_event_date", "idx_events_event_type",
            "idx_events_subject", "idx_events_extraction_status",
            "idx_events_review_status", "idx_events_article_id",
        ]
        for idx in expected:
            assert idx in indexes, f"index {idx} missing (found: {indexes})"

    def test_check_constraint_event_type(self, fresh_db):
        conn = get_db()
        _insert_test_article(conn, id="art_001")
        now = datetime.now(timezone.utc).isoformat()
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO events (event_id, article_id, subject, event_type, extraction_status, created_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?)",
                ("bad_001", "art_001", "测试", "invalid_type", now),
            )

    def test_check_constraint_review_status(self, fresh_db):
        conn = get_db()
        _insert_test_article(conn, id="art_002")
        now = datetime.now(timezone.utc).isoformat()
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO events (event_id, article_id, subject, event_type, review_status, created_at) "
                "VALUES (?, ?, ?, 'regulation', ?, ?)",
                ("bad_002", "art_002", "测试", "invalid_status", now),
            )

    def test_schema_version_is_23(self, fresh_db):
        conn = get_db()
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        assert row[0] >= 23


# ═══════════════════════════════════════════════════════════════════════════
# T2: 事件 ID 确定性
# ═══════════════════════════════════════════════════════════════════════════

class TestEventIdDeterminism:
    def test_same_input_same_id(self):
        def eid(aid, date, subj, etype):
            raw = f"{aid}{date or ''}{subj}{etype}"
            return hashlib.sha256(raw.encode()).hexdigest()[:16]
        assert eid("a1", "2026-08-07", "招行", "regulation") == eid("a1", "2026-08-07", "招行", "regulation")
        assert len(eid("a1", "d", "s", "t")) == 16

    def test_different_article_different_id(self):
        def eid(aid, date, subj, etype):
            raw = f"{aid}{date or ''}{subj}{etype}"
            return hashlib.sha256(raw.encode()).hexdigest()[:16]
        assert eid("a1", "d", "s", "t") != eid("a2", "d", "s", "t")


# ═══════════════════════════════════════════════════════════════════════════
# T3-T6: Normalize 规则
# ═══════════════════════════════════════════════════════════════════════════

class TestNormalizeSubject:
    def test_zhaoshang_alias(self):
        r = normalize_event(_mk_raw(subject="招行"))
        assert r["subject"] == "招商银行"
        assert r["subject_raw"] == "招行"

    def test_gonghang_alias(self):
        r = normalize_event(_mk_raw(subject="工行"))
        assert r["subject"] == "工商银行"

    def test_jianshe_alias(self):
        r = normalize_event(_mk_raw(subject="建行"))
        assert r["subject"] == "建设银行"

    def test_jiaotong_alias(self):
        r = normalize_event(_mk_raw(subject="交行"))
        assert r["subject"] == "交通银行"

    def test_unknown_passthrough(self):
        r = normalize_event(_mk_raw(subject="某不知名公司"))
        assert r["subject"] == "某不知名公司"

    def test_full_name_no_change(self):
        r = normalize_event(_mk_raw(subject="招商银行"))
        assert r["subject"] == "招商银行"
        # 全名和规范名一样 → subject_raw 为 None（去重）
        assert r["subject_raw"] is None


class TestNormalizeDate:
    def test_already_iso(self):
        r = normalize_event(_mk_raw(event_date="2026-08-07", date_precision="exact"))
        assert r["event_date"] == "2026-08-07"
        assert r["event_date_precision"] == "exact"

    def test_chinese_year_month(self):
        r = normalize_event(_mk_raw(event_date="2026年8月", date_precision="month"))
        assert r["event_date"] == "2026-08"
        assert r["event_date_precision"] == "month"

    def test_relative_quarter(self):
        r = normalize_event(_mk_raw(event_date="本季度", date_precision="relative"))
        assert r["event_date_precision"] == "relative"

    def test_empty_date(self):
        r = normalize_event(_mk_raw(event_date="", date_precision="unknown"))
        assert r["event_date_precision"] == "unknown"


class TestNormalizeEventType:
    def test_regulation_aliases(self):
        assert normalize_event(_mk_raw(event_type="监管"))["event_type"] == "regulation"
        assert normalize_event(_mk_raw(event_type="新规"))["event_type"] == "regulation"

    def test_product_aliases(self):
        assert normalize_event(_mk_raw(event_type="上线"))["event_type"] == "product"
        assert normalize_event(_mk_raw(event_type="推出"))["event_type"] == "product"

    def test_financing_aliases(self):
        assert normalize_event(_mk_raw(event_type="融资"))["event_type"] == "financing"

    def test_tech_aliases(self):
        assert normalize_event(_mk_raw(event_type="AI"))["event_type"] == "tech"
        assert normalize_event(_mk_raw(event_type="数字化"))["event_type"] == "tech"

    def test_unknown_falls_to_other(self):
        assert normalize_event(_mk_raw(event_type="随机类型"))["event_type"] == "other"

    def test_valid_passthrough(self):
        assert normalize_event(_mk_raw(event_type="regulation"))["event_type"] == "regulation"


class TestNormalizeEvent:
    def test_full_event(self):
        raw = _mk_raw(
            subject="招行", event_date="2026-08-07", event_type="监管",
            summary="招行发布新规", evidence_snippet="原文证据",
            extraction_confidence=90,
        )
        raw["date_precision"] = "exact"
        result = normalize_event(raw)
        assert result is not None
        assert result["subject"] == "招商银行"
        assert result["subject_raw"] == "招行"
        assert result["event_type"] == "regulation"
        assert result["event_date_precision"] == "exact"
        assert result["event_date"] == "2026-08-07"

    def test_empty_subject_returns_none(self):
        assert normalize_event(_mk_raw(subject="")) is None


# ═══════════════════════════════════════════════════════════════════════════
# T7-T10: Pipeline 测试（mock LLM）
# ═══════════════════════════════════════════════════════════════════════════

class TestPipeline:
    def test_successful_extraction(self, fresh_db):
        conn = get_db()
        _insert_test_article(conn, id="art_success", title="测试成功文章")

        mock_reply = json.dumps([
            {
                "subject": "招商银行",
                "event_date": "2026-08-07",
                "date_precision": "exact",
                "event_type": "regulation",
                "summary": "招行发布新规",
                "evidence_snippet": "证据1",
                "confidence": 90,
            },
            {
                "subject": "工商银行",
                "event_date": "2026-08-08",
                "date_precision": "exact",
                "event_type": "product",
                "summary": "工行上线新产品",
                "evidence_snippet": "证据2",
                "confidence": 85,
            },
        ])

        with patch("moni.events.pipeline.call_llm", return_value=mock_reply):
            from moni.events.pipeline import extract_events_for_article
            art_row = dict(conn.execute(
                "SELECT id, title, raw_content, source, published_at FROM articles WHERE id=?",
                ("art_success",),
            ).fetchone())
            # v1.0.35.19: provider/model 缺省走 llm_settings resolve;
            # 测试环境无配置, 显式传假值 (call_llm 已被 mock, 不会真调用)
            result = extract_events_for_article(
                art_row, provider="test-provider", model="test-model",
            )

        assert result["status"] == "completed"
        assert result["event_count"] == 2

        events = conn.execute("SELECT * FROM events WHERE article_id='art_success' AND subject != '__processing__'").fetchall()
        assert len(events) == 2

    def test_llm_failure(self, fresh_db):
        conn = get_db()
        _insert_test_article(conn, id="art_fail", title="测试失败文章")

        with patch("moni.events.pipeline.call_llm", side_effect=Exception("LLM timeout")):
            from moni.events.pipeline import extract_events_for_article
            art_row = dict(conn.execute(
                "SELECT id, title, raw_content, source, published_at FROM articles WHERE id=?",
                ("art_fail",),
            ).fetchone())
            result = extract_events_for_article(art_row)

        assert result["status"] == "failed"
        assert result.get("error") is not None

        count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE article_id='art_fail' AND subject != '__processing__'"
        ).fetchone()[0]
        assert count == 0

    def test_invalid_json_zero_events(self, fresh_db):
        conn = get_db()
        _insert_test_article(conn, id="art_json", title="JSON 失败")

        with patch("moni.events.pipeline.call_llm", return_value="完全不是JSON的废话"):
            from moni.events.pipeline import extract_events_for_article
            art_row = dict(conn.execute(
                "SELECT id, title, raw_content, source, published_at FROM articles WHERE id=?",
                ("art_json",),
            ).fetchone())
            result = extract_events_for_article(art_row)

        assert result["event_count"] == 0

    def test_unique_no_duplicate_on_retry(self, fresh_db):
        conn = get_db()
        _insert_test_article(conn, id="art_unique", title="去重测试")

        mock_reply = json.dumps([
            {
                "subject": "招商银行",
                "event_date": "2026-08-07",
                "date_precision": "exact",
                "event_type": "regulation",
                "summary": "招行发布新规",
                "evidence_snippet": "证据",
                "confidence": 90,
            },
        ])

        with patch("moni.events.pipeline.call_llm", return_value=mock_reply):
            from moni.events.pipeline import extract_events_for_article
            art_row = dict(conn.execute(
                "SELECT id, title, raw_content, source, published_at FROM articles WHERE id=?",
                ("art_unique",),
            ).fetchone())
            # v1.0.35.19: 测试环境无 llm_settings, 显式传假 provider/model
            extract_events_for_article(
                art_row, provider="test-provider", model="test-model",
            )
            count1 = conn.execute(
                "SELECT COUNT(*) FROM events WHERE article_id='art_unique' AND subject != '__processing__'"
            ).fetchone()[0]
            # 再跑一次（模拟重试）
            conn.execute("DELETE FROM events WHERE article_id='art_unique'")
            conn.commit()
            extract_events_for_article(
                art_row, provider="test-provider", model="test-model",
            )
            count2 = conn.execute(
                "SELECT COUNT(*) FROM events WHERE article_id='art_unique' AND subject != '__processing__'"
            ).fetchone()[0]

        assert count1 == 1
        assert count2 == 1


# ═══════════════════════════════════════════════════════════════════════════
# T11-T15: API 测试
# ═══════════════════════════════════════════════════════════════════════════

class TestEventsAPI:
    def _seed(self, conn):
        _insert_test_article(conn, id="a1", title="文章1")
        _insert_test_article(conn, id="a2", title="文章2")
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            ("e1", "a1", "2026-08-07", "exact", "招商银行", "regulation", "已确认", "completed", "confirmed"),
            ("e2", "a1", "2026-08-10", "exact", "招商银行", "product", "产品事件", "completed", "pending"),
            ("e3", "a2", "2026-08-15", "exact", "工商银行", "financing", "融资事件", "completed", "confirmed"),
            ("e4", "a2", None, "unknown", "建设银行", "tech", "日期不确定", "completed", "pending"),
            ("e5", "a2", None, "relative", "农业银行", "other", "近期事件", "completed", "pending"),
        ]
        for eid, aid, date, prec, subj, etype, summary, estatus, rstatus in rows:
            conn.execute(
                "INSERT INTO events (event_id, article_id, event_date, event_date_precision, "
                "subject, event_type, summary, extraction_status, review_status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (eid, aid, date, prec, subj, etype, summary, estatus, rstatus, now),
            )
        conn.commit()

    def test_list_events(self, fresh_db, client):
        self._seed(get_db())
        resp = client.get("/api/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5

    def test_filter_by_type(self, fresh_db, client):
        self._seed(get_db())
        resp = client.get("/api/events?event_type=regulation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["event_type"] == "regulation"

    def test_filter_by_month(self, fresh_db, client):
        self._seed(get_db())
        resp = client.get("/api/events?year=2026&month=8")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3

    def test_by_article(self, fresh_db, client):
        self._seed(get_db())
        resp = client.get("/api/events/by-article/a1")
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    def test_by_article_not_found(self, fresh_db, client):
        resp = client.get("/api/events/by-article/nope")
        assert resp.status_code == 404

    def test_pending(self, fresh_db, client):
        self._seed(get_db())
        resp = client.get("/api/events/pending")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2
        precs = {i["event_date_precision"] for i in items}
        assert "relative" in precs
        assert "unknown" in precs

    def test_stats(self, fresh_db, client):
        self._seed(get_db())
        resp = client.get("/api/events/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert data["total"] == 5
        assert "by_type" in data
        assert "pending_review" in data

    def test_retry_not_found(self, fresh_db, client):
        resp = client.post("/api/events/retry/nope")
        assert resp.status_code == 404

    def test_confirm_event(self, fresh_db, client):
        self._seed(get_db())
        resp = client.post("/api/events/e2/confirm")
        assert resp.status_code == 200
        assert resp.json()["review_status"] == "confirmed"
        # 确认后不再出现在 pending
        resp2 = client.get("/api/events/pending")
        ids = [i["event_id"] for i in resp2.json()["items"]]
        assert "e2" not in ids
        # DB 落库验证
        row = get_db().execute(
            "SELECT review_status FROM events WHERE event_id='e2'"
        ).fetchone()
        assert row["review_status"] == "confirmed"

    def test_reject_event(self, fresh_db, client):
        self._seed(get_db())
        resp = client.post("/api/events/e5/reject")
        assert resp.status_code == 200
        assert resp.json()["review_status"] == "rejected"
        row = get_db().execute(
            "SELECT review_status FROM events WHERE event_id='e5'"
        ).fetchone()
        assert row["review_status"] == "rejected"

    def test_review_event_not_found(self, fresh_db, client):
        resp = client.post("/api/events/nope/confirm")
        assert resp.status_code == 404
        resp2 = client.post("/api/events/nope/reject")
        assert resp2.status_code == 404
