"""案例库 V3 API：统一口径 stats / facets / 六维筛选 / 详情关联。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moni.cases.backfill_taxonomy import run_backfill
from moni.cases.normalize import normalize_case
from moni.cases.repository import CaseRepository
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.web.server import create_app


@pytest.fixture()
def seeded(tmp_path: Path):
    db_file = tmp_path / "test_cases_v3.db"
    set_db_path(db_file)
    migrate()
    conn = get_db()

    rows = [
        ("art_v3_1", "招商银行", "招行上线 AI 智能投顾", ["智能投顾"], "2026-08-01", "high"),
        ("art_v3_2", "招商银行", "招行信用卡接入大模型客服", ["大模型应用"], "2026-09-12", "medium"),
        ("art_v3_3", "中信证券", "中信证券与华为达成战略合作", ["战略合作"], "2026-07-03", "high"),
        ("art_v3_4", "未知科技有限公司", "完成 A 轮融资 1 亿元", ["融资"], "bad-date", "low"),
    ]
    ids: dict[str, str] = {}
    now = datetime.now(timezone.utc).isoformat()
    for aid, subject, summary, keywords, case_date, value in rows:
        conn.execute(
            """
            INSERT INTO articles (id, title, url, source, published_at, fetched_at, raw_content)
            VALUES (?, ?, ?, 'mpaypass', ?, ?, ?)
            """,
            (aid, f"标题-{aid}", f"https://example.com/{aid}", now, now, summary),
        )
        cs = normalize_case(
            {
                "subject": subject,
                "case_type": "AI",
                "case_action": "launch",
                "summary": summary,
                "evidence_snippet": summary,
                "keywords": keywords,
                "reference_value": value,
                "extraction_confidence": 90,
            }
        )
        assert cs is not None
        with conn:
            CaseRepository.insert_cases(conn, aid, [cs])
        cid = conn.execute("SELECT case_id FROM cases WHERE article_id = ?", (aid,)).fetchone()[0]
        conn.execute("UPDATE cases SET case_date = ? WHERE case_id = ?", (case_date, cid))
        ids[aid] = cid

    with conn:
        CaseRepository.soft_delete_case(ids["art_v3_4"])

    run_backfill(db_file, apply=True)
    client = TestClient(create_app())
    yield client, conn, ids
    close_db()


def _cases(client: TestClient, **params) -> dict:
    resp = client.get("/api/cases", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestStatsScope:
    def test_kpis_share_one_scope(self, seeded):
        client, conn, _ = seeded
        body = client.get("/api/cases/stats").json()
        assert body["total"] == 3
        assert body["high_value"] == 2
        assert body["enterprise_count"] == 2
        expected_scenarios = conn.execute(
            "SELECT COUNT(DISTINCT l.scenario_code) FROM case_scenario_links l "
            "JOIN cases c ON c.case_id = l.case_id "
            "WHERE c.subject != '__processing__' AND c.case_lifecycle != 'archived'"
        ).fetchone()[0]
        assert body["scenario_count"] == expected_scenarios
        assert body["category_count"] == 2
        assert sum(body["by_type"].values()) == body["total"]


class TestFilters:
    def test_single_and_multi_dimension(self, seeded):
        client, _, _ = seeded
        assert _cases(client, industry="银行")["total"] == 2
        assert _cases(client, industry="银行,证券")["total"] == 3
        assert _cases(client, enterprise="ent_cmb")["total"] == 2
        assert _cases(client, scenario="wealth_advisory")["total"] == 1

    def test_search_covers_keywords_and_evidence(self, seeded):
        client, _, _ = seeded
        assert _cases(client, q="智能投顾")["total"] == 1
        assert _cases(client, q="大模型")["total"] == 1
        assert _cases(client, q="不存在的关键词")["total"] == 0

    def test_month_ignores_dirty_dates(self, seeded):
        client, _, _ = seeded
        assert _cases(client, month="2026-08")["total"] == 1
        assert _cases(client, month="2026-08,2026-07")["total"] == 2

    def test_pagination_no_duplicates(self, seeded):
        client, _, _ = seeded
        page1 = _cases(client, limit=2, offset=0)
        page2 = _cases(client, limit=2, offset=2)
        ids = [c["case_id"] for c in page1["items"] + page2["items"]]
        assert len(ids) == 3
        assert len(set(ids)) == 3


class TestFacets:
    def test_facet_excludes_own_dimension_only(self, seeded):
        client, _, _ = seeded
        body = client.get("/api/cases/facets", params={"industry": "银行"}).json()
        industries = {o["value"]: o["count"] for o in body["industry"]}
        assert industries == {"银行": 2, "证券": 1}
        enterprises = {o["value"]: o["count"] for o in body["enterprise"]}
        assert enterprises == {"ent_cmb": 2}

    def test_month_facet_drops_dirty_values(self, seeded):
        client, _, _ = seeded
        body = client.get("/api/cases/facets").json()
        months = {o["value"] for o in body["month"]}
        assert "2026-08" in months
        assert all(len(m) == 7 and m[4] == "-" for m in months)


class TestDetail:
    def test_enterprise_industry_scenarios_related(self, seeded):
        client, _, ids = seeded
        body = client.get(f"/api/cases/{ids['art_v3_1']}").json()
        assert body["enterprise_name"] == "招商银行"
        assert body["industry_l1"] == "银行"
        assert body["category_name"]
        assert [s["code"] for s in body["scenarios"]]
        related_ids = {r["case_id"] for r in body["related"]}
        assert ids["art_v3_2"] in related_ids
        assert ids["art_v3_3"] not in related_ids

    def test_missing_case_is_404(self, seeded):
        client, _, _ = seeded
        assert client.get("/api/cases/MN-XX-0000-0000").status_code == 404
