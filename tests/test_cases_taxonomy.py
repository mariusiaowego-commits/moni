"""P1-1 企业/行业/场景/8类 分类体系与回填验收测试 (migrations 065-066)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moni.cases.backfill_taxonomy import (
    classify_category,
    classify_scenario,
    load_enterprise_alias_map,
    resolve_enterprise,
    run_backfill,
)
from moni.cases.normalize import normalize_case
from moni.cases.repository import CaseRepository
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, current_version, migrate
from moni.web.server import create_app


@pytest.fixture()
def tmp_db(tmp_path: Path):
    db_file = tmp_path / "test_p1.db"
    set_db_path(db_file)
    migrate()
    conn = get_db()
    yield conn, db_file
    close_db()


def _seed_case(
    conn: sqlite3.Connection,
    aid: str,
    *,
    subject: str,
    summary: str,
    evidence: str = "",
    case_type: str = "AI",
    keywords: list[str] | None = None,
) -> str:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR IGNORE INTO articles (id, title, url, source, published_at, fetched_at, raw_content)
        VALUES (?, ?, ?, 'mpaypass', ?, ?, ?)
        """,
        (aid, f"标题-{aid}", f"https://example.com/{aid}", now, now, summary + " " + evidence),
    )
    cs = normalize_case(
        {
            "subject": subject,
            "case_type": case_type,
            "case_action": "launch",
            "summary": summary,
            "evidence_snippet": evidence,
            "keywords": keywords or [],
            "extraction_confidence": 90,
        }
    )
    assert cs is not None
    # 模拟历史遗留未清洗 subject (如 中信银行银行信用卡中心)
    cs["subject"] = subject
    with conn:
        CaseRepository.insert_cases(conn, aid, [cs])
    return conn.execute("SELECT case_id FROM cases WHERE article_id = ?", (aid,)).fetchone()[0]


class TestMigrations065To066:
    def test_migration_idempotent_and_version_65(self, tmp_db):
        conn, _ = tmp_db
        assert _CURRENT_VERSION >= 65
        assert current_version(conn) == _CURRENT_VERSION

        # 二次执行 migrate 必须幂等
        migrate()
        assert current_version(conn) == _CURRENT_VERSION

        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert {
            "enterprises",
            "enterprise_aliases",
            "case_enterprises",
            "case_categories",
            "case_scenarios",
            "case_scenario_links",
        } <= tables

    def test_seed_counts_for_categories_scenarios_and_industries(self, tmp_db):
        conn, _ = tmp_db
        cat_cnt = conn.execute("SELECT COUNT(*) FROM case_categories").fetchone()[0]
        scn_cnt = conn.execute("SELECT COUNT(*) FROM case_scenarios").fetchone()[0]
        assert cat_cnt == 8
        assert scn_cnt == 10

        industries = {
            r[0] for r in conn.execute("SELECT DISTINCT industry_l1 FROM enterprises").fetchall()
        }
        assert {"银行", "保险", "证券", "互联网", "其他"} <= industries


class TestEnterpriseAliasResolution:
    def test_longest_match_and_ascii_boundary(self, tmp_db):
        conn, _ = tmp_db
        alias_map = load_enterprise_alias_map(conn)

        eid, name, ind = resolve_enterprise("中信证券", alias_map)
        assert (eid, name, ind) == ("ent_citic_sec", "中信证券", "证券")

        eid2, name2, ind2 = resolve_enterprise("中信银行银行信用卡中心", alias_map)
        assert (eid2, name2, ind2) == ("ent_citic_bank", "中信银行", "银行")

        eid3, name3, ind3 = resolve_enterprise("交通银行上海市分行", alias_map)
        assert (eid3, name3, ind3) == ("ent_bocom", "交通银行", "银行")

        # ASCII 边界保护: Meta 不应匹配 Metaverse
        eid4, _, ind4 = resolve_enterprise("Metaverse Lab", alias_map)
        assert eid4 is None and ind4 == "其他"

    def test_unmatched_falls_back_to_other_without_guessing(self, tmp_db):
        conn, _ = tmp_db
        alias_map = load_enterprise_alias_map(conn)

        eid, name, ind = resolve_enterprise("某未知地方小微科技公司", alias_map)
        assert eid is None
        assert name is None
        assert ind == "其他"

        # 历史脏串 "土耳其中国人民银行" 不得中间截胡成中国人民银行
        eid2, _, ind2 = resolve_enterprise("土耳其中国人民银行", alias_map)
        assert eid2 is None and ind2 == "其他"


class TestCategoryAndScenarioRules:
    def test_eight_categories_classified(self):
        assert classify_category(summary="某银行智能风控项目中标公示，预算金额395万元") == "procurement"
        assert classify_category(summary="因违规被罚55万元") == "compliance_risk"
        assert classify_category(summary="完成4000万美元战略融资") == "capital"
        assert classify_category(summary="宣布未来两年裁员约20%") == "org_strategy"
        assert classify_category(summary="与Corpay达成战略合作") == "partnership"
        assert classify_category(summary="完成全行核心系统分布式架构改造") == "digital_infra"
        assert classify_category(summary="上线企业级AI大模型智能客服") == "ai_adoption"
        assert classify_category(summary="推出全新跨境信用卡服务", case_type="PI") == "product_launch"

    def test_scenarios_classified_and_fallback_other(self):
        assert classify_scenario(keywords=["数字人民币", "跨境支付"]) == "ecny_stablecoin"
        assert classify_scenario(summary="Visa推出欧洲跨境清算新方案") == "cross_border_pay"
        assert classify_scenario(summary="上线大模型智能体") == "llm_agent"
        assert classify_scenario(summary="某企业发布年度报告") == "other"


class TestBackfillAndAggregation:
    def test_dry_run_does_not_write_db(self, tmp_db):
        conn, db_file = tmp_db
        cid = _seed_case(conn, "art_d1", subject="招商银行", summary="招行上线AI智能投顾", keywords=["智能投顾"])

        res = run_backfill(db_file, apply=False)
        assert res["mode"] == "dry-run"
        assert res["total"] == 1
        assert res["enterprise_matched"] == 1

        row = conn.execute(
            "SELECT subject_enterprise_id, case_category FROM cases WHERE case_id = ?", (cid,)
        ).fetchone()
        assert row[0] is None and row[1] is None
        assert conn.execute("SELECT COUNT(*) FROM case_enterprises").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM case_scenario_links").fetchone()[0] == 0

    def test_apply_idempotent_and_aggregations_plus_stats_api(self, tmp_db):
        conn, db_file = tmp_db
        _seed_case(conn, "art_a1", subject="招商银行", summary="招行上线AI智能投顾", keywords=["智能投顾"])
        _seed_case(conn, "art_a2", subject="中信证券", summary="中信证券与华为战略合作", keywords=["大模型应用"])
        _seed_case(conn, "art_a3", subject="未知科技有限公司", summary="完成A轮融资1亿元")
        cid_arch = _seed_case(conn, "art_a4", subject="中国平安集团", summary="平安上线智能风控")
        CaseRepository.soft_delete_case(cid_arch)

        res1 = run_backfill(db_file, apply=True)
        res2 = run_backfill(db_file, apply=True)
        assert res1["enterprise_matched"] == 3
        assert res1["enterprise_unmatched"] == 1
        assert res2["enterprise_matched"] == 3

        by_ent = CaseRepository.aggregate_cases_by_enterprise()
        ent_ids = {r["enterprise_id"] for r in by_ent}
        assert {"ent_cmb", "ent_citic_sec", "other"} <= ent_ids
        assert "ent_pingan" not in ent_ids  # archived 被排除

        by_ind = {r["industry_l1"]: r["count"] for r in CaseRepository.aggregate_cases_by_industry()}
        assert by_ind == {"银行": 1, "证券": 1, "其他": 1}

        by_scn = {r["scenario_code"]: r["count"] for r in CaseRepository.aggregate_cases_by_scenario()}
        assert by_scn["wealth_advisory"] == 1
        assert by_scn["llm_agent"] == 1
        assert by_scn["credit_supply_chain"] == 1

        by_cat = {r["category_code"]: r["count"] for r in CaseRepository.aggregate_cases_by_category()}
        assert by_cat == {"ai_adoption": 1, "partnership": 1, "capital": 1}

        client = TestClient(create_app())
        resp = client.get("/api/cases/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert {"total", "by_type", "by_action", "by_reference_value", "pending_review",
                "by_enterprise", "by_industry", "by_scenario", "by_category"} <= set(body.keys())
        assert isinstance(body["by_enterprise"], list)
        assert isinstance(body["by_industry"], list)
        assert isinstance(body["by_scenario"], list)
        assert isinstance(body["by_category"], list)
