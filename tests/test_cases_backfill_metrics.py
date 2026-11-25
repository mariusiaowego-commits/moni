"""P0 收尾: 存量案例 case_metrics 规则回填验收测试."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from moni.cases.backfill_metrics import run_backfill_metrics
from moni.cases.normalize import normalize_case
from moni.cases.repository import CaseRepository
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate


@pytest.fixture()
def tmp_db(tmp_path: Path):
    db_file = tmp_path / "test_backfill_metrics.db"
    set_db_path(db_file)
    migrate()
    conn = get_db()
    yield conn, db_file
    close_db()


def _seed_legacy_case(
    conn: sqlite3.Connection,
    aid: str,
    *,
    subject: str,
    legacy_amount: str,
    evidence: str,
    summary: str = "",
    raw_content: str = "",
) -> str:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR IGNORE INTO articles (id, title, url, source, published_at, fetched_at, raw_content)
        VALUES (?, ?, ?, 'mpaypass', ?, ?, ?)
        """,
        (aid, f"文章-{aid}", f"https://example.com/{aid}", now, now, raw_content or evidence),
    )
    cs = normalize_case(
        {
            "subject": subject,
            "case_type": "AI",
            "case_action": "launch",
            "summary": summary or evidence,
            "evidence_snippet": evidence,
            "extraction_confidence": 90,
        }
    )
    assert cs is not None
    # 模拟老库没有 metrics 且 amount 被旧 normalize_amount 写坏的状态
    cs["metrics"] = []
    cs["amount"] = legacy_amount
    with conn:
        CaseRepository.insert_cases(conn, aid, [cs])
    return conn.execute(
        "SELECT case_id FROM cases WHERE article_id = ? ORDER BY rowid DESC LIMIT 1", (aid,)
    ).fetchone()[0]


class TestBackfillMetrics:
    def test_count_metric_not_converted_to_cny(self, tmp_db):
        conn, db_file = tmp_db
        cid = _seed_legacy_case(
            conn,
            "a1",
            subject="腾讯微信小微",
            legacy_amount="3000 万元",
            evidence="小微第一次披露数据：互动日活3000万，触点扩展到朋友圈。",
        )
        res = run_backfill_metrics(db_file, apply=True)
        assert res["matched"] == 1
        row = conn.execute(
            "SELECT metric_type, value_num, unit, evidence_quote FROM case_metrics WHERE case_id = ?",
            (cid,),
        ).fetchone()
        assert row is not None
        assert row[0] == "dau"
        assert row[1] == 30_000_000.0
        assert row[2] == "user"
        assert "3000万" in row[3]
        amt = conn.execute("SELECT amount FROM cases WHERE case_id = ?", (cid,)).fetchone()[0]
        assert "万元" not in amt and "日活" in amt

    def test_foreign_currency_preserved_and_trillion_magnitude(self, tmp_db):
        conn, db_file = tmp_db
        cid_usd = _seed_legacy_case(
            conn,
            "a2",
            subject="VEON",
            legacy_amount="2.5 亿元",
            evidence="VEON承诺未来几年投入2.5亿美元拓展数字银行",
        )
        cid_krw = _seed_legacy_case(
            conn,
            "a3",
            subject="韩国政府",
            legacy_amount="9.9 万元",
            evidence="今年将AI预算提高至9.9万亿韩元，是去年的三倍",
        )
        res = run_backfill_metrics(db_file, apply=True)
        assert res["matched"] == 2

        m_usd = conn.execute("SELECT metric_type, value_num, unit FROM case_metrics WHERE case_id = ?", (cid_usd,)).fetchone()
        assert (m_usd[0], m_usd[1], m_usd[2]) == ("investment", 250_000_000.0, "USD")

        m_krw = conn.execute("SELECT metric_type, value_num, unit FROM case_metrics WHERE case_id = ?", (cid_krw,)).fetchone()
        assert m_krw[0] == "contract_value"
        assert m_krw[1] == pytest.approx(9.9e12)
        assert m_krw[2] == "KRW"

    def test_evidence_miss_drops_metric(self, tmp_db):
        conn, db_file = tmp_db
        cid = _seed_legacy_case(
            conn,
            "a4",
            subject="OpenAI",
            legacy_amount="1 亿元",
            evidence="OpenAI承认旗下一款智能体在内部测试中失控",
        )
        res = run_backfill_metrics(db_file, apply=True)
        assert res["matched"] == 0
        assert res["dropped"] == 1
        assert conn.execute("SELECT COUNT(*) FROM case_metrics WHERE case_id = ?", (cid,)).fetchone()[0] == 0

    def test_no_amount_not_fabricated(self, tmp_db):
        conn, db_file = tmp_db
        _seed_legacy_case(
            conn,
            "a5",
            subject="招商银行",
            legacy_amount="",
            evidence="招商银行投入5亿元建设大模型",
        )
        res = run_backfill_metrics(db_file, apply=True)
        assert res["total_with_amount"] == 0
        assert res["matched"] == 0

    def test_dry_run_no_write_and_apply_idempotent(self, tmp_db):
        conn, db_file = tmp_db
        cid = _seed_legacy_case(
            conn,
            "a6",
            subject="南京银行",
            legacy_amount="875.32 万元",
            evidence="中标预算金额875.32万元",
        )
        r_dry = run_backfill_metrics(db_file, apply=False)
        assert r_dry["mode"] == "dry-run"
        assert r_dry["matched"] == 1
        assert conn.execute("SELECT COUNT(*) FROM case_metrics").fetchone()[0] == 0

        run_backfill_metrics(db_file, apply=True)
        run_backfill_metrics(db_file, apply=True)
        assert conn.execute("SELECT COUNT(*) FROM case_metrics WHERE case_id = ?", (cid,)).fetchone()[0] == 1
        amt = conn.execute("SELECT amount FROM cases WHERE case_id = ?", (cid,)).fetchone()[0]
        assert "875.32万元" in amt

    def test_coverage_funding_and_revenue_types(self, tmp_db):
        conn, db_file = tmp_db
        c_cov = _seed_legacy_case(
            conn, "a7", subject="工商银行", legacy_amount="1.7 万个网点",
            evidence="完成全行核心系统数字化转型，覆盖1.7万个网点",
        )
        c_fund = _seed_legacy_case(
            conn, "a8", subject="Yellow Card", legacy_amount="4000 万元",
            evidence="Yellow Card官宣完成4000万美元战略融资",
        )
        c_rev = _seed_legacy_case(
            conn, "a9", subject="智谱", legacy_amount="10 亿元",
            evidence="截至2026年7月，智谱的ARR（年度经常性收入）已经达到10亿美元",
        )
        res = run_backfill_metrics(db_file, apply=True)
        assert res["matched"] == 3
        assert conn.execute("SELECT metric_type, unit FROM case_metrics WHERE case_id = ?", (c_cov,)).fetchone()[:] == ("coverage", "institution")
        assert conn.execute("SELECT metric_type, unit FROM case_metrics WHERE case_id = ?", (c_fund,)).fetchone()[:] == ("funding", "USD")
        assert conn.execute("SELECT metric_type, unit FROM case_metrics WHERE case_id = ?", (c_rev,)).fetchone()[:] == ("revenue", "USD")

    def test_eur_jpy_hkd_gbp_preserved(self, tmp_db):
        conn, db_file = tmp_db
        c_eur = _seed_legacy_case(conn, "a10", subject="Worldline", legacy_amount="6000 万元", evidence="交易股权价值约6000万欧元")
        c_jpy = _seed_legacy_case(conn, "a11", subject="7-Eleven日本", legacy_amount="3000 亿元", evidence="洽谈总额约3000亿日元的战略投资")
        c_hkd = _seed_legacy_case(conn, "a12", subject="恒生银行", legacy_amount="5000 万元", evidence="投入5000万港元升级系统")
        c_gbp = _seed_legacy_case(conn, "a13", subject="汇丰控股", legacy_amount="2 亿元", evidence="投资2亿英镑建设AI中心")
        res = run_backfill_metrics(db_file, apply=True)
        assert res["matched"] == 4
        assert conn.execute("SELECT unit FROM case_metrics WHERE case_id = ?", (c_eur,)).fetchone()[0] == "EUR"
        assert conn.execute("SELECT unit FROM case_metrics WHERE case_id = ?", (c_jpy,)).fetchone()[0] == "JPY"
        assert conn.execute("SELECT unit FROM case_metrics WHERE case_id = ?", (c_hkd,)).fetchone()[0] == "HKD"
        assert conn.execute("SELECT unit FROM case_metrics WHERE case_id = ?", (c_gbp,)).fetchone()[0] == "GBP"

    def test_raw_content_window_fallback(self, tmp_db):
        conn, db_file = tmp_db
        cid = _seed_legacy_case(
            conn, "a14", subject="某支付机构", legacy_amount="500 万户",
            evidence="该机构推出面向中小商户的新服务",
            raw_content="正文补充说明：新服务已累计服务500万户中小商户。",
        )
        res = run_backfill_metrics(db_file, apply=True)
        assert res["matched"] == 1
        row = conn.execute("SELECT metric_type, unit, evidence_quote FROM case_metrics WHERE case_id = ?", (cid,)).fetchone()
        assert row[0] == "user_count" and row[1] == "account" and "500万户" in row[2]
