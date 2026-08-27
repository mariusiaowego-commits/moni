"""tests/test_stats_geography_api.py — sprint-19 PR #136 榜单地理字段 + 精确反选."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.stats.repository import cases_dashboard_stats, row_hits_excluded
from moni.web.server import create_app


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
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


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_high_case(
    *,
    case_id: str = "MN-AI-2608-9001",
    article_id: str = "art_geo_1",
    subject: str = "招商银行",
    keywords: list[str] | None = None,
    geos: list[tuple] | None = None,
    confidence: int = 90,
) -> None:
    conn = get_db()
    now = _iso_now()
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, has_ai_summary, raw_content) "
        "VALUES (?, ?, ?, ?, ?, 0, ?)",
        (article_id, "t", f"https://example.com/{article_id}", "test", now, "raw"),
    )
    conn.execute(
        """INSERT INTO cases
           (case_id, article_id, subject, case_type, case_action, reference_value,
            extraction_status, case_lifecycle, review_status, extraction_confidence,
            extracted_at, created_at, updated_at, summary)
           VALUES (?, ?, ?, 'AI', 'launch', 'high',
                   'completed', 'draft', 'pending', ?,
                   ?, ?, ?, ?)""",
        (case_id, article_id, subject, confidence, now, now, now, "跨境支付试点"),
    )
    for kw in keywords or []:
        conn.execute(
            "INSERT INTO case_keywords (case_id, keyword, source_type, created_at) "
            "VALUES (?, ?, 'auto_llm', ?)",
            (case_id, kw, now),
        )
    for i, geo in enumerate(geos or []):
        code, name, region, primary = geo
        conn.execute(
            """INSERT INTO case_geography
               (case_id, country_code, country_name, region, is_primary, source_type, created_at)
               VALUES (?, ?, ?, ?, ?, 'auto_rule', ?)""",
            (case_id, code, name, region, primary, now),
        )
    conn.commit()


class TestDashboardGeoFields:
    def test_ranking_includes_geo_and_keywords(self):
        _seed_high_case(
            keywords=["跨境支付", "数字人民币"],
            geos=[("CN", "中国", "APAC", 1), ("US", "美国", "NAM", 0)],
        )
        d = cases_dashboard_stats("all")
        assert d["high_value_cases"] == 1
        assert len(d["high_value_ranking"]) == 1
        row = d["high_value_ranking"][0]
        assert row["country_code"] == "CN"
        assert row["country_name"] == "中国"
        assert row["region"] == "APAC"
        assert set(row["keywords"]) == {"跨境支付", "数字人民币"}
        assert "跨境支付" in d["keyword_options"]
        assert "数字人民币" in d["keyword_options"]
        geo = d["geo_distribution"]
        assert "by_region" in geo and "by_country" in geo
        regions = {r["region"]: r["count"] for r in geo["by_region"]}
        assert regions["APAC"] == 1
        assert regions["NAM"] == 1
        codes = {c["country_code"]: c for c in geo["by_country"]}
        assert codes["CN"]["country_name"] == "中国"
        assert codes["CN"]["count"] == 1

    def test_ranking_geo_null_without_geography(self):
        _seed_high_case(keywords=["智能投顾"], geos=[])
        row = cases_dashboard_stats("all")["high_value_ranking"][0]
        assert row["country_code"] is None
        assert row["country_name"] is None
        assert row["region"] is None
        assert row["keywords"] == ["智能投顾"]

    def test_primary_geo_wins(self):
        _seed_high_case(
            geos=[("US", "美国", "NAM", 0), ("GB", "英国", "EMEA", 1)],
        )
        row = cases_dashboard_stats("all")["high_value_ranking"][0]
        assert row["country_code"] == "GB"
        assert row["region"] == "EMEA"

    def test_api_returns_geo_fields(self, client):
        _seed_high_case(
            keywords=["开放银行"],
            geos=[("SG", "新加坡", "APAC", 1)],
        )
        r = client.get("/api/stats/cases-dashboard?period=all")
        assert r.status_code == 200
        body = r.json()
        row = body["high_value_ranking"][0]
        assert row["country_code"] == "SG"
        assert row["country_name"] == "新加坡"
        assert row["region"] == "APAC"
        assert "开放银行" in row["keywords"]
        assert "开放银行" in body["keyword_options"]
        assert body["geo_distribution"]["by_country"][0]["country_code"] == "SG"


class TestRowHitsExcluded:
    def _row(self, **kw):
        base = {
            "keywords": ["跨境支付", "数字人民币"],
            "country_code": "CN",
            "country_name": "中国",
            "region": "APAC",
        }
        base.update(kw)
        return base

    def test_empty_excluded_false(self):
        assert row_hits_excluded(self._row(), None) is False
        assert row_hits_excluded(self._row(), {}) is False
        assert row_hits_excluded(
            self._row(), {"keywords": [], "countries": [], "regions": []}
        ) is False

    def test_keyword_exact_hit(self):
        assert row_hits_excluded(
            self._row(), {"keywords": ["跨境支付"]}
        ) is True

    def test_keyword_trim_case(self):
        assert row_hits_excluded(
            self._row(keywords=["Open Banking"]),
            {"keywords": ["  open banking  "]},
        ) is True

    def test_keyword_substring_not_hit(self):
        """K1: 精确 Set, '支付' 不得命中 '跨境支付'."""
        assert row_hits_excluded(
            self._row(), {"keywords": ["支付"]}
        ) is False

    def test_keywords_comma_string(self):
        assert row_hits_excluded(
            self._row(keywords="跨境支付, 数字人民币"),
            {"keywords": ["数字人民币"]},
        ) is True

    def test_country_code_hit(self):
        assert row_hits_excluded(
            self._row(), {"countries": ["CN"]}
        ) is True
        assert row_hits_excluded(
            self._row(), {"countries": ["cn"]}
        ) is True
        assert row_hits_excluded(
            self._row(), {"countries": ["US"]}
        ) is False

    def test_region_hit(self):
        assert row_hits_excluded(
            self._row(), {"regions": ["APAC"]}
        ) is True
        assert row_hits_excluded(
            self._row(), {"regions": ["NAM"]}
        ) is False

    def test_any_dimension_hits(self):
        row = self._row()
        assert row_hits_excluded(
            row, {"keywords": ["无"], "countries": ["US"], "regions": ["APAC"]}
        ) is True
        assert row_hits_excluded(
            row, {"keywords": ["无"], "countries": ["US"], "regions": ["NAM"]}
        ) is False
