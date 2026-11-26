"""Sprint 83 — 案例质量闸 / 按需抽取 / 可见性口径 / 回填加固。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from moni.cases.backfill_common import backup_database
from moni.cases.backfill_taxonomy import run_backfill
from moni.cases.gate import pre_gate, validate_case
from moni.cases.normalize import normalize_case
from moni.cases.pipeline import extract_cases_batch, extract_cases_for_article
from moni.cases.repository import CaseRepository, active_case_clause
from moni.cron import jobs as cron_jobs
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate


@pytest.fixture()
def db(tmp_path: Path):
    set_db_path(tmp_path / "sprint83.db")
    migrate()
    conn = get_db()
    yield conn
    close_db()


def _seed_article(
    conn: sqlite3.Connection,
    aid: str,
    *,
    title: str = "测试文章",
    body: str = "x" * 500,
    source: str = "mpaypass",
    tags: tuple[str, ...] = (),
    fetched_at: str | None = None,
) -> None:
    now = fetched_at or datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO articles (id, title, url, source, published_at, fetched_at, raw_content)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (aid, title, f"https://example.com/{aid}", source, now, now, body),
    )
    for tag in tags:
        conn.execute(
            "INSERT OR IGNORE INTO article_tags (article_id, tag_name, created_at) "
            "VALUES (?, ?, ?)",
            (aid, tag, now),
        )
    conn.commit()


class TestPreGate:
    def test_hard_noise_blocked(self):
        assert pre_gate({"title": "招聘：产品经理", "raw_content": "x" * 800}) is not None
        assert pre_gate({"title": "抽奖活动", "raw_content": "x" * 800}) is not None

    def test_xbot_chatter_blocked_but_signal_passes(self):
        chatter = {"title": "评论区没人喷我了", "source": "xbot_feed", "raw_content": "今天天气不错"}
        assert pre_gate(chatter) is not None
        signal = {
            "title": "某银行上线数字人民币钱包",
            "source": "bank_feed",
            "raw_content": "某银行今日上线数字人民币钱包，首批覆盖 10 个城市。",
        }
        assert pre_gate(signal) is None

    def test_weekly_review_with_business_signal_passes(self):
        art = {
            "title": "【出海周报】央行发布跨境支付新规",
            "source": "mpaypass",
            "raw_content": "本周央行发布跨境支付新规，多家支付机构上线相关产品。" + "x" * 200,
        }
        assert pre_gate(art) is None

    def test_short_bank_feed_without_signal_blocked(self):
        art = {"title": "内部通知", "source": "bank_feed", "raw_content": "x" * 100}
        assert pre_gate(art) is not None

    def test_validate_case_thresholds(self):
        ok = {
            "subject": "招商银行",
            "summary": "招商银行正式上线智能投顾服务",
            "extraction_confidence": 80,
            "keywords": ["智能投顾", "大模型"],
        }
        assert validate_case(ok) is None
        assert validate_case({**ok, "summary": "太短"}) is not None
        assert validate_case({**ok, "extraction_confidence": 59}) is not None
        assert validate_case({**ok, "keywords": ["智能投顾"]}) is not None


class TestModuleScope:
    def test_clause_shape(self):
        assert active_case_clause("") == "subject != '__processing__' AND case_lifecycle != 'archived'"
        assert "archived" not in active_case_clause("c", include_archived=True)


class TestPendingQueue:
    def test_xbot_only_attribution_excluded(self, db: sqlite3.Connection):
        db.execute("INSERT OR IGNORE INTO tag_definitions (name, created_at) VALUES ('支付', datetime('now'))")
        db.commit()
        _seed_article(db, "a_xbot_only", source="xbot_feed", tags=("xbot-scope",))
        _seed_article(db, "a_xbot_biz", source="xbot_feed", tags=("xbot-scope", "支付"))
        ids = [a["id"] for a in CaseRepository.get_pending_articles(batch_size=10)]
        assert ids == ["a_xbot_biz"]


class TestBatchSemantics:
    def test_gated_items_do_not_consume_batch_size(self, db: sqlite3.Connection, monkeypatch):
        for i in range(8):
            # 噪声排在最前：证明 skipped_gate 不占 batch_size 配额。
            _seed_article(
                db,
                f"noise_{i}",
                title=f"招聘信息 {i}",
                body="x" * 600,
                fetched_at=f"2026-11-26T12:00:0{i}+00:00",
            )
        for i in range(3):
            _seed_article(
                db,
                f"good_{i}",
                title=f"某银行上线新支付产品 {i}",
                body="某银行今日上线新支付产品，覆盖 10 个城市。" + "x" * 200,
                fetched_at=f"2026-11-26T10:00:0{i}+00:00",
            )

        calls: list[str] = []

        def fake_extract(article, **kwargs):
            calls.append(article["id"])
            return {"status": "completed", "case_count": 0, "error": None}

        monkeypatch.setattr("moni.cases.pipeline.extract_cases_for_article", fake_extract)
        res = extract_cases_batch(
            batch_size=3, check_budget=False, provider="test", model="test"
        )

        assert res["gated"] == 8
        assert res["processed"] == 3
        assert sorted(calls) == ["good_0", "good_1", "good_2"]
        placeholders = db.execute(
            "SELECT COUNT(*) FROM cases WHERE subject = '__processing__'"
        ).fetchone()[0]
        assert placeholders == 0
        skipped = db.execute(
            "SELECT COUNT(*) FROM case_extract_runs WHERE status = 'skipped_gate'"
        ).fetchone()[0]
        assert skipped == 8

    def test_no_cases_writes_ledger_not_placeholder(self, db: sqlite3.Connection):
        _seed_article(db, "a_none", title="某银行上线新支付产品", body="某银行上线新支付产品。" * 30)
        res = extract_cases_for_article(
            {
                "id": "a_none",
                "title": "某银行上线新支付产品",
                "source": "mpaypass",
                "published_at": datetime.now(timezone.utc).isoformat(),
                "raw_content": "某银行上线新支付产品。" * 30,
            },
            llm_call_fn=lambda prompt: "[]",
        )
        assert res["status"] == "no_cases"
        assert db.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 0
        status = db.execute(
            "SELECT status FROM case_extract_runs WHERE article_id = 'a_none'"
        ).fetchone()[0]
        assert status == "no_cases"

    def test_budget_rechecked_inside_batch(self, db: sqlite3.Connection, monkeypatch):
        for i in range(2):
            _seed_article(db, f"budget_{i}", title="某银行上线新支付产品", body="某银行上线新支付产品。" * 30)
        allowed = {"n": 0}

        def fake_budget(conn):
            allowed["n"] += 1
            return (allowed["n"] == 1, 0, 300000)

        monkeypatch.setattr("moni.cases.pipeline.check_daily_token_budget", fake_budget)
        monkeypatch.setattr(
            "moni.cases.pipeline.extract_cases_for_article",
            lambda article, **kw: {"status": "completed", "case_count": 0, "error": None},
        )
        res = extract_cases_batch(batch_size=2, provider="test", model="test")
        assert res["budget_exhausted"] is True
        assert res["processed"] == 0

    def test_low_quality_case_is_dropped_individually(self, db: sqlite3.Connection):
        _seed_article(db, "a_mix", title="工商银行上线智能客服", body="工商银行上线智能客服系统。" * 30)
        reply = {
            "cases": [
                {
                    "subject": "工商银行",
                    "case_type": "AI",
                    "case_action": "launch",
                    "summary": "工商银行上线智能客服系统",
                    "keywords": ["智能客服", "大模型"],
                    "extraction_confidence": 88,
                },
                {
                    "subject": "工商银行",
                    "case_type": "AI",
                    "case_action": "launch",
                    "summary": "短",
                    "keywords": ["智能客服", "大模型"],
                    "extraction_confidence": 88,
                },
            ]
        }
        import json as _json

        res = extract_cases_for_article(
            {
                "id": "a_mix",
                "title": "工商银行上线智能客服",
                "source": "mpaypass",
                "published_at": datetime.now(timezone.utc).isoformat(),
                "raw_content": "工商银行上线智能客服系统。" * 30,
            },
            llm_call_fn=lambda prompt: _json.dumps(reply, ensure_ascii=False),
        )
        assert res["status"] == "completed"
        assert res["case_count"] == 1
        assert db.execute("SELECT COUNT(*) FROM cases WHERE article_id = 'a_mix'").fetchone()[0] == 1


class TestTaxonomyWriteThrough:
    def test_new_case_writes_four_tables(self, db: sqlite3.Connection):
        _seed_article(db, "a_tax", title="工商银行上线智能客服", body="工商银行上线智能客服系统。" * 30)
        reply = {
            "cases": [{
                "subject": "工商银行",
                "case_type": "AI",
                "case_action": "launch",
                "summary": "工商银行上线智能客服系统",
                "keywords": ["智能客服", "大模型"],
                "extraction_confidence": 90,
            }]
        }
        import json as _json

        res = extract_cases_for_article(
            {
                "id": "a_tax",
                "title": "工商银行上线智能客服",
                "source": "mpaypass",
                "published_at": datetime.now(timezone.utc).isoformat(),
                "raw_content": "工商银行上线智能客服系统。" * 30,
            },
            llm_call_fn=lambda prompt: _json.dumps(reply, ensure_ascii=False),
        )
        assert res["status"] == "completed"
        row = db.execute(
            "SELECT case_id, subject_enterprise_id, case_category FROM cases WHERE article_id = 'a_tax'"
        ).fetchone()
        assert row["subject_enterprise_id"] == "ent_icbc"
        assert row["case_category"]
        assert db.execute(
            "SELECT COUNT(*) FROM case_enterprises WHERE case_id = ?", (row["case_id"],)
        ).fetchone()[0] >= 1
        assert db.execute(
            "SELECT COUNT(*) FROM case_scenario_links WHERE case_id = ?", (row["case_id"],)
        ).fetchone()[0] >= 1


class TestDemoJob:
    def test_cap_at_ten(self, db: sqlite3.Connection, monkeypatch):
        captured: dict[str, int] = {}

        def fake_pending(**kwargs):
            captured["batch_size"] = kwargs["batch_size"]
            captured["job_id"] = kwargs["job_id"]
            return {}

        monkeypatch.setattr(cron_jobs, "extract_cases_pending", fake_pending)
        cron_jobs.extract_cases_demo(batch_size=50)
        assert captured == {"batch_size": 10, "job_id": "extract_cases_demo"}

    def test_busy_reports_failure_not_silent_success(self, db: sqlite3.Connection):
        assert cron_jobs._CASE_LOCK.acquire(blocking=False)
        try:
            res = cron_jobs.extract_cases_demo(batch_size=10)
        finally:
            cron_jobs._CASE_LOCK.release()
        assert res["skipped"] == "busy"
        assert res.get("error")
        row = db.execute(
            "SELECT success, error FROM cron_runs WHERE job_id = 'extract_cases_demo' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None and row["success"] == 0 and row["error"]

    def test_case_chain_defaults_off(self, db: sqlite3.Connection):
        assert cron_jobs._case_chain_enabled() is False
        db.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) "
            "VALUES ('case_chain_enabled', '1', NULL)"
        )
        db.commit()
        assert cron_jobs._case_chain_enabled() is True


class TestMigration070:
    def test_daily_job_and_demo_job(self, db: sqlite3.Connection):
        rows = {
            r["id"]: dict(r)
            for r in db.execute(
                "SELECT * FROM cron_jobs WHERE func_path LIKE '%extract_cases%'"
            ).fetchall()
        }
        assert set(rows) == {"extract_cases_noon_post", "extract_cases_demo"}
        day = rows["extract_cases_noon_post"]
        assert (day["hour"], day["minute"], day["day_of_week"], day["is_active"]) == (13, 30, "*", 0)
        demo = rows["extract_cases_demo"]
        assert demo["trigger_type"] == "manual"
        assert demo["is_active"] == 0
        assert '"batch_size": 10' in demo["func_kwargs"]

        rev = int(
            db.execute("SELECT value FROM cron_meta WHERE key = 'schedule_revision'").fetchone()[0]
        )
        assert rev >= 2


class TestArchivedScopeMatrix:
    @pytest.fixture()
    def archived_case(self, db: sqlite3.Connection) -> str:
        _seed_article(db, "a_arch", title="招商银行上线智能投顾", body="招商银行上线智能投顾。" * 20)
        cs = normalize_case(
            {
                "subject": "招商银行",
                "case_type": "AI",
                "case_action": "launch",
                "case_date": "2026-09-01",
                "summary": "招商银行上线智能投顾",
                "evidence_snippet": "招商银行上线智能投顾",
                "keywords": ["智能投顾", "大模型"],
                "reference_value": "high",
                "extraction_confidence": 90,
            }
        )
        assert cs is not None
        with db:
            CaseRepository.insert_cases(db, "a_arch", [cs])
        case_id = db.execute("SELECT case_id FROM cases WHERE article_id = 'a_arch'").fetchone()[0]
        db.execute(
            "INSERT OR IGNORE INTO case_geography (case_id, country_code, country_name, region, created_at) "
            "VALUES (?, 'CN', '中国', 'APAC', datetime('now'))",
            (case_id,),
        )
        db.commit()
        assert CaseRepository.soft_delete_case(case_id)
        return case_id

    def test_every_read_path_excludes_archived(self, db: sqlite3.Connection, archived_case: str):
        from moni.share.manifest import _dedupe_case_article_ids
        from moni.stats.repository import (
            cases_dashboard_stats,
            data_funnel_stats,
            extract_layer_stats,
        )

        assert CaseRepository.list_cases() == []
        assert CaseRepository.count_cases() == 0
        assert CaseRepository.search_fts("招商银行") == []
        assert CaseRepository.search_fts("招行") == []
        assert CaseRepository.get_by_article("a_arch") == []

        dash = cases_dashboard_stats(period="all")
        assert dash["total_cases"] == 0
        assert dash["high_value_ranking"] == []
        assert dash["keyword_options"] == []
        assert dash["geo_distribution"] == {"by_region": [], "by_country": []}

        assert data_funnel_stats()["cases_total"] == 0
        assert extract_layer_stats(period="90d")["raw"]["cases_total"] == 0
        assert _dedupe_case_article_ids(db, [archived_case]) == []


class TestBackfillHardening:
    def _seed_case(self, db: sqlite3.Connection) -> None:
        _seed_article(db, "a_bf", title="工商银行上线新支付产品", body="工商银行上线新支付产品。" * 20)
        cs = normalize_case(
            {
                "subject": "工商银行",
                "case_type": "PI",
                "case_action": "launch",
                "summary": "工商银行上线新支付产品",
                "evidence_snippet": "工商银行上线新支付产品",
                "keywords": ["支付"],
                "reference_value": "medium",
                "extraction_confidence": 80,
            }
        )
        assert cs is not None
        with db:
            CaseRepository.insert_cases(db, "a_bf", [cs])

    def test_dry_run_works_on_read_only_file(self, db: sqlite3.Connection, tmp_path: Path):
        self._seed_case(db)
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        target = tmp_path / "readonly.db"
        src = Path(db.execute("PRAGMA database_list").fetchone()[2])
        target.write_bytes(src.read_bytes())
        target.chmod(0o444)
        res = run_backfill(target, apply=False)
        assert res["total"] == 1

    def test_backup_is_openable_and_quick_check_ok(self, db: sqlite3.Connection, tmp_path: Path):
        self._seed_case(db)
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        src = Path(db.execute("PRAGMA database_list").fetchone()[2])
        target = tmp_path / "news.db"
        target.write_bytes(src.read_bytes())
        dest = backup_database(target, tag="test")
        check = sqlite3.connect(str(dest))
        try:
            assert check.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            check.close()

    def test_manual_user_rows_survive_backfill(self, db: sqlite3.Connection, tmp_path: Path):
        self._seed_case(db)
        case_id = db.execute("SELECT case_id FROM cases WHERE article_id = 'a_bf'").fetchone()[0]
        db.execute(
            "INSERT OR REPLACE INTO case_enterprises "
            "(case_id, enterprise_id, role, raw_name, source_type, created_at) "
            "VALUES (?, 'ent_icbc', 'subject', '工商银行', 'manual_user', datetime('now'))",
            (case_id,),
        )
        db.commit()
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        src = Path(db.execute("PRAGMA database_list").fetchone()[2])
        target = tmp_path / "news_manual.db"
        target.write_bytes(src.read_bytes())

        run_backfill(target, apply=True)
        check = sqlite3.connect(str(target))
        check.row_factory = sqlite3.Row
        try:
            rows = check.execute(
                "SELECT source_type FROM case_enterprises WHERE case_id = ? AND role = 'subject'",
                (case_id,),
            ).fetchall()
        finally:
            check.close()
        assert [r["source_type"] for r in rows] == ["manual_user"]
