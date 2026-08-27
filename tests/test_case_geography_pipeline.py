"""tests/test_case_geography_pipeline.py — country 透传到 case_geography."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from moni.db.connection import set_db_path, get_db, close_db
import moni.db.connection as conn_mod
import moni.db.migrate as mig_mod
from moni.db.migrate import migrate


@pytest.fixture(autouse=True)
def fresh_db(tmp_path: Path):
    set_db_path(tmp_path / "test.db")
    migrate()
    yield get_db()
    close_db()
    conn_mod._CON = None
    conn_mod._DB_PATH = None
    mig_mod._CON = None


def _seed_article(conn, article_id: str = "art_geo") -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (article_id, "t", "u", "test", now, "raw"),
    )
    conn.commit()


def _art(article_id: str = "art_geo") -> dict:
    conn = get_db()
    return dict(conn.execute(
        "SELECT id, title, raw_content, source, published_at FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone())


def _case(**kw):
    base = {
        "subject": "某未知科技公司XYZ",
        "case_type": "AI",
        "case_action": "launch",
        "case_date": "2026-08-07",
        "reference_value": "high",
        "summary": "s",
        "evidence_snippet": "",
        "extraction_confidence": 90,
        "keywords": ["智能投顾"],
    }
    base.update(kw)
    return base


def _geo_rows(article_id: str = "art_geo"):
    conn = get_db()
    return conn.execute(
        """SELECT g.* FROM case_geography g
           JOIN cases c ON c.case_id = g.case_id
           WHERE c.article_id = ? AND c.subject != '__processing__'
           ORDER BY g.is_primary DESC, g.id""",
        (article_id,),
    ).fetchall()


class TestPipelineGeo:
    def test_pipeline_writes_geo(self, fresh_db):
        from moni.cases.pipeline import extract_cases_for_article
        _seed_article(fresh_db)
        mock = json.dumps({"cases": [_case(country="美国")]}, ensure_ascii=False)
        r = extract_cases_for_article(_art(), llm_call_fn=lambda _p: mock)
        assert r["status"] == "completed"
        rows = _geo_rows()
        assert len(rows) == 1
        assert rows[0]["country_code"] == "US"
        assert rows[0]["region"] == "NAM"
        assert rows[0]["is_primary"] == 1
        assert rows[0]["source_type"] == "auto_llm"

    def test_pipeline_subject_geo_default(self, fresh_db):
        from moni.cases.pipeline import extract_cases_for_article
        _seed_article(fresh_db)
        mock = json.dumps({"cases": [_case(subject="招商银行")]}, ensure_ascii=False)
        extract_cases_for_article(_art(), llm_call_fn=lambda _p: mock)
        rows = _geo_rows()
        assert len(rows) == 1
        assert rows[0]["country_code"] == "CN"
        assert rows[0]["source_type"] == "auto_rule"

    def test_pipeline_no_country(self, fresh_db):
        from moni.cases.pipeline import extract_cases_for_article
        _seed_article(fresh_db)
        mock = json.dumps({"cases": [_case()]}, ensure_ascii=False)
        r = extract_cases_for_article(_art(), llm_call_fn=lambda _p: mock)
        assert r["status"] == "completed"
        assert r["case_count"] == 1
        assert _geo_rows() == []

    def test_pipeline_multi_country(self, fresh_db):
        from moni.cases.pipeline import extract_cases_for_article
        _seed_article(fresh_db)
        mock = json.dumps(
            {"cases": [_case(country=["中国", "美国"])]}, ensure_ascii=False,
        )
        extract_cases_for_article(_art(), llm_call_fn=lambda _p: mock)
        rows = _geo_rows()
        assert [r["country_code"] for r in rows] == ["CN", "US"]
        assert rows[0]["is_primary"] == 1
        assert rows[1]["is_primary"] == 0

    def test_pipeline_overwrite_idempotent(self, fresh_db):
        from moni.cases.pipeline import extract_cases_for_article
        _seed_article(fresh_db)
        mock = json.dumps({"cases": [_case(subject="招商银行", country="中国")]}, ensure_ascii=False)
        extract_cases_for_article(_art(), llm_call_fn=lambda _p: mock)
        extract_cases_for_article(_art(), llm_call_fn=lambda _p: mock)
        rows = _geo_rows()
        assert len(rows) == 1
        assert rows[0]["country_code"] == "CN"
