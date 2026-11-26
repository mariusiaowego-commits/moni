"""原文证据校验（migration 069）：定位、上下文截取、回填、API 输出."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moni.cases.backfill_evidence import run_backfill
from moni.cases.evidence import build_context, locate_evidence
from moni.cases.normalize import normalize_case
from moni.cases.repository import CaseRepository
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.web.server import create_app


@pytest.fixture()
def tmp_db(tmp_path: Path):
    db_file = tmp_path / "test_evidence.db"
    set_db_path(db_file)
    migrate()
    conn = get_db()
    yield conn
    close_db()


def _seed_article(conn: sqlite3.Connection, aid: str, body: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO articles (id, title, url, source, published_at, fetched_at, raw_content)
        VALUES (?, '证据测试', ?, 'mpaypass', ?, ?, ?)
        """,
        (aid, f"https://example.com/{aid}", now, now, body),
    )
    conn.commit()


def _case(evidence: str | None, **overrides) -> dict:
    cs = normalize_case({
        "subject": "腾讯",
        "case_type": "AI",
        "case_action": "launch",
        "summary": "腾讯上线小微",
        "evidence_snippet": evidence,
    })
    assert cs is not None
    cs.update(overrides)
    return cs


class TestLocateEvidence:
    def test_exact_quote_returns_offset(self):
        raw = "前文。" + "互动日活3000万，触点扩展到30多个。" + "后文。"
        ok, offset, length = locate_evidence("互动日活3000万，触点扩展到30多个。", raw)
        assert ok is True
        assert raw[offset: offset + length] == "互动日活3000万，触点扩展到30多个。"

    def test_whitespace_and_newline_differences_still_match(self):
        raw = "第一段\n\n互动日活  3000万，触点扩展到 30 多个。\n后文"
        ok, offset, _ = locate_evidence("互动日活 3000万，触点扩展到 30 多个。", raw)
        assert ok is True
        assert offset == raw.index("互动")

    def test_paraphrase_is_not_a_quote(self):
        raw = "微信方面表示，小微的互动日活达到三千万。"
        assert locate_evidence("互动日活3000万", raw) == (False, None, 0)

    @pytest.mark.parametrize("evidence,raw", [(None, "正文"), ("", "正文"), ("证据", None), ("证据", "")])
    def test_empty_inputs_are_unverified(self, evidence, raw):
        assert locate_evidence(evidence, raw) == (False, None, 0)


class TestBuildContext:
    def test_clips_before_and_after(self):
        raw = "甲" * 100 + "证据句" + "乙" * 100
        offset = raw.index("证据句")
        ctx = build_context(raw, offset, len("证据句"), before=10, after=10)
        assert ctx["context_before"] == "甲" * 10
        assert ctx["context_after"] == "乙" * 10

    def test_handles_document_boundaries(self):
        raw = "证据句" + "乙" * 50
        ctx = build_context(raw, 0, len("证据句"), before=200, after=200)
        assert ctx["context_before"] == ""
        assert ctx["context_after"] == "乙" * 50

    def test_missing_offset_returns_empty(self):
        assert build_context("正文", None, 3) == {"context_before": "", "context_after": ""}


class TestBackfillEvidence:
    def test_dry_run_counts_without_writing(self, tmp_db: sqlite3.Connection, tmp_path: Path):
        _seed_article(tmp_db, "art_ok", "互动日活3000万，触点扩展到30多个。")
        _seed_article(tmp_db, "art_bad", "微信方面表示小微日活达到三千万。")
        _seed_article(tmp_db, "art_empty", "没有任何证据句的正文。")
        with tmp_db:
            CaseRepository.insert_cases(tmp_db, "art_ok", [_case("互动日活3000万，触点扩展到30多个。")])
            CaseRepository.insert_cases(tmp_db, "art_bad", [_case("互动日活3000万")])
            CaseRepository.insert_cases(tmp_db, "art_empty", [_case(None)])

        stats = run_backfill(tmp_path / "test_evidence.db")
        assert stats == {"total": 2, "verified": 1, "not_found": 1, "missing_article": 0}
        assert tmp_db.execute(
            "SELECT COUNT(*) FROM cases WHERE evidence_verified != 0"
        ).fetchone()[0] == 0

    def test_apply_writes_verified_and_offset(self, tmp_db: sqlite3.Connection, tmp_path: Path):
        raw = "前文。" + "互动日活3000万，触点扩展到30多个。" + "后文。"
        _seed_article(tmp_db, "art_ok", raw)
        with tmp_db:
            CaseRepository.insert_cases(tmp_db, "art_ok", [_case("互动日活3000万，触点扩展到30多个。")])
        case_id = tmp_db.execute("SELECT case_id FROM cases WHERE article_id = 'art_ok'").fetchone()[0]

        stats = run_backfill(tmp_path / "test_evidence.db", apply=True)
        assert stats["verified"] == 1
        verified, offset = tmp_db.execute(
            "SELECT evidence_verified, evidence_offset FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        assert verified == 1
        assert raw[offset: offset + 20] == "互动日活3000万，触点扩展到30多个。"

    def test_missing_article_is_counted_separately(self, tmp_db: sqlite3.Connection, tmp_path: Path):
        _seed_article(tmp_db, "art_gone", "正文。")
        with tmp_db:
            CaseRepository.insert_cases(tmp_db, "art_gone", [_case("互动日活3000万")])
        tmp_db.execute("PRAGMA foreign_keys = OFF")
        with tmp_db:
            tmp_db.execute("DELETE FROM articles WHERE id = 'art_gone'")
        stats = run_backfill(tmp_path / "test_evidence.db")
        assert stats["missing_article"] == 1


class TestApiEvidenceFields:
    def test_verified_case_returns_context(self, tmp_db: sqlite3.Connection):
        raw = "甲" * 60 + "互动日活3000万，触点扩展到30多个。" + "乙" * 60
        _seed_article(tmp_db, "art_api", raw)
        offset = raw.index("互动日活")
        with tmp_db:
            CaseRepository.insert_cases(tmp_db, "art_api", [_case(
                "互动日活3000万，触点扩展到30多个。",
                evidence_verified=1,
                evidence_offset=offset,
            )])
        case_id = tmp_db.execute("SELECT case_id FROM cases WHERE article_id = 'art_api'").fetchone()[0]

        resp = TestClient(create_app()).get(f"/api/cases/{case_id}")
        assert resp.status_code == 200
        item = resp.json()
        assert item["evidence_verified"] == 1
        assert item["evidence_offset"] == offset
        assert item["evidence_context"]["context_before"] == "甲" * 60
        assert item["evidence_context"]["context_after"].startswith("乙")

    def test_unverified_case_has_no_context(self, tmp_db: sqlite3.Connection):
        _seed_article(tmp_db, "art_plain", "正文里没有对应证据。")
        with tmp_db:
            CaseRepository.insert_cases(tmp_db, "art_plain", [_case("互动日活3000万")])
        case_id = tmp_db.execute("SELECT case_id FROM cases WHERE article_id = 'art_plain'").fetchone()[0]

        item = TestClient(create_app()).get(f"/api/cases/{case_id}").json()
        assert item["evidence_verified"] == 0
        assert item["evidence_context"] == {}
