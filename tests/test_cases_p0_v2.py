"""P0 案例库 2.0 止血验收测试 (migrations 062-064, metrics, FTS v2, cron/ledger/budget)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from moni.cases.normalize import normalize_case, parse_metric
from moni.cases.pipeline import (
    check_daily_token_budget,
    extract_cases_batch,
    extract_cases_for_article,
)
from moni.cases.repository import CaseRepository
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, current_version, migrate
from moni.web.server import create_app


@pytest.fixture()
def tmp_db(tmp_path: Path):
    db_file = tmp_path / "test_p0.db"
    set_db_path(db_file)
    migrate()
    conn = get_db()
    yield conn
    close_db()


def _seed_article(conn: sqlite3.Connection, aid: str, *, title: str = "测试文章", body: str = "x" * 200) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO articles (id, title, url, source, published_at, fetched_at, raw_content)
        VALUES (?, ?, ?, 'mpaypass', ?, ?, ?)
        """,
        (aid, title, f"https://example.com/{aid}", now, now, body),
    )
    conn.commit()


class TestMigrations062To064:
    def test_schema_version_and_tables(self, tmp_db: sqlite3.Connection):
        assert _CURRENT_VERSION >= 63
        assert current_version(tmp_db) == _CURRENT_VERSION

        tables = {
            r[0]
            for r in tmp_db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert {"case_metrics", "case_extract_runs", "cases_fts"} <= tables

        case_cols = {
            r[1] for r in tmp_db.execute("PRAGMA table_info(cases)").fetchall()
        }
        assert {"raw_json", "case_action_source", "search_text"} <= case_cols

        cron_ids = {
            r[0]
            for r in tmp_db.execute(
                "SELECT id FROM cron_jobs WHERE func_path LIKE '%extract_cases%'"
            ).fetchall()
        }
        assert cron_ids == {"extract_cases_noon_post", "extract_cases_demo"}

        budget = tmp_db.execute(
            "SELECT value FROM app_settings WHERE key = 'case_extract_daily_input_token_budget'"
        ).fetchone()
        assert budget is not None and int(budget[0]) == 300000


class TestParseMetricAndActionSource:
    def test_five_core_metric_samples(self):
        m1 = parse_metric("互动日活3000万")
        assert m1 is not None
        assert m1["metric_type"] == "dau"
        assert m1["value_num"] == 30_000_000.0
        assert m1["unit"] == "user"
        assert m1["period_type"] == "daily"

        m2 = parse_metric("2.5亿美元", "承诺投入2.5亿美元")
        assert m2 is not None
        assert m2["unit"] == "USD"
        assert m2["value_num"] == 250_000_000.0
        assert m2["metric_type"] == "investment"

        m3 = parse_metric("9.9万亿韩元")
        assert m3 is not None
        assert m3["unit"] == "KRW"
        assert m3["value_num"] == pytest.approx(9.9e12)

        m4 = parse_metric("6000万欧元", "收购股权价值6000万欧元")
        assert m4 is not None
        assert m4["unit"] == "EUR"
        assert m4["value_num"] == 60_000_000.0
        assert m4["metric_type"] == "deal_value"

        m5 = parse_metric("预算金额（元）：3950000")
        assert m5 is not None
        assert m5["metric_type"] == "contract_value"
        assert m5["unit"] == "CNY"
        assert m5["value_num"] == 3_950_000.0

    def test_context_currency_does_not_leak_into_bare_count(self):
        m = parse_metric("10 个", "另据披露，该机构管理资产规模达800亿美元")
        assert m is not None
        assert m["unit"] != "USD"

    def test_country_is_not_institution(self):
        m = parse_metric("178个", "覆盖178个国家和地区")
        assert m is not None
        assert m["unit"] == "country"

    def test_duration_is_not_a_metric(self):
        assert parse_metric("6个月募资") is None

    def test_month_in_date_is_not_a_duration(self):
        # 「月」出现在日期里（2025年11月）不代表时长，指标本身要保留。
        m = parse_metric("1.2亿", "2025年11月交易额1.2亿")
        assert m is not None
        assert m["metric_type"] == "txn_volume"

    def test_normalize_case_populates_metrics_raw_json_and_action_source(self):
        cs = normalize_case({
            "subject": "微信支付",
            "case_type": "PI",
            "case_action": "pilot",
            "amount": "互动日活3000万",
            "summary": "小微试运行互动新能力",
            "evidence_snippet": "互动日活3000万",
            "keywords": ["小程序"],
        })
        assert cs is not None
        assert cs["case_action"] == "pilot"
        assert cs["case_action_source"] == "llm_enum"
        assert json.loads(cs["raw_json"])["amount"] == "互动日活3000万"
        assert len(cs["metrics"]) == 1
        assert cs["metrics"][0]["metric_type"] == "dau"
        assert cs["metrics"][0]["unit"] == "user"

        cs_default = normalize_case({
            "subject": "招商银行",
            "case_type": "AI",
            "case_action": "",
            "summary": "招商银行开展新探索",
        })
        assert cs_default is not None
        assert cs_default["case_action"] == "launch"
        assert cs_default["case_action_source"] == "default"

    def test_insert_and_read_metrics_via_api(self, tmp_db: sqlite3.Connection):
        _seed_article(tmp_db, "art_m1", title="微信小微披露日活数据")
        res = extract_cases_for_article(
            {"id": "art_m1", "title": "微信小微披露日活数据", "source": "mpaypass", "published_at": "2026-11-01", "raw_content": "互动日活3000万" * 20},
            llm_call_fn=lambda _p: json.dumps({
                "cases": [{
                    "subject": "微信支付",
                    "case_type": "PI",
                    "case_action": "scale",
                    "amount": "互动日活3000万",
                    "summary": "微信支付规模化推广互动能力",
                    "evidence_snippet": "互动日活3000万",
                    "extraction_confidence": 90,
                    "keywords": ["小程序", "移动支付"],
                    "country": "中国",
                }]
            }),
        )
        assert res["status"] == "completed"
        app = create_app()
        client = TestClient(app)
        resp = client.get("/api/cases")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["case_action_source"] == "llm_enum"
        assert len(items[0]["metrics"]) == 1
        assert items[0]["metrics"][0]["metric_type"] == "dau"
        assert items[0]["metrics"][0]["unit"] == "user"


class TestSearchFtsV2:
    def test_keyword_title_short_term_and_special_chars(self, tmp_db: sqlite3.Connection):
        _seed_article(tmp_db, "art_s1", title="工行小程序银行新进展")
        cs = normalize_case({
            "subject": "工商银行",
            "case_type": "PI",
            "case_action": "launch",
            "summary": "工商银行上线移动服务",
            "evidence_snippet": "支持跨境结算与数字钱包",
            "keywords": ["移动服务"],
        })
        with tmp_db:
            CaseRepository.insert_cases(tmp_db, "art_s1", [cs])
        case_id = CaseRepository.get_by_article("art_s1")[0]["case_id"]

        # 1) 文章标题命中 (>=3 字 trigram)
        hits_title = CaseRepository.search_fts("小程序")
        assert [h["case_id"] for h in hits_title] == [case_id]
        assert hits_title[0]["article_title"] == "工行小程序银行新进展"

        # 2) <3 字短词走 search_text LIKE 兜底 (旧版 trigram 必 0)
        hits_short = CaseRepository.search_fts("跨境")
        assert [h["case_id"] for h in hits_short] == [case_id]

        # 3) 新增人工关键词后立即可搜 (case_keywords trigger)
        CaseRepository.add_keyword(case_id, "智能风控")
        hits_kw = CaseRepository.search_fts("智能风控")
        assert [h["case_id"] for h in hits_kw] == [case_id]

        # 4) FTS 特殊字符不抛 500
        app = create_app()
        client = TestClient(app)
        for bad_q in ("C++", '"unclosed', "AND", "a-b"):
            r = client.get("/api/cases/search", params={"q": bad_q})
            assert r.status_code == 200
            assert r.json()["items"] == []

    def test_archived_hidden_from_search(self, tmp_db: sqlite3.Connection):
        _seed_article(tmp_db, "art_a1", title="招商银行归档案例")
        cs = normalize_case({
            "subject": "招商银行",
            "case_type": "PI",
            "case_action": "launch",
            "summary": "招商银行上线新服务",
            "evidence_snippet": "招商银行发布公告，支持跨境结算",
            "keywords": ["归档测试"],
        })
        with tmp_db:
            CaseRepository.insert_cases(tmp_db, "art_a1", [cs])
        case_id = CaseRepository.get_by_article("art_a1")[0]["case_id"]
        assert [h["case_id"] for h in CaseRepository.search_fts("招商银行")] == [case_id]
        assert [h["case_id"] for h in CaseRepository.search_fts("跨境")] == [case_id]

        with tmp_db:
            tmp_db.execute(
                "UPDATE cases SET case_lifecycle = 'archived' WHERE case_id = ?", (case_id,)
            )
        assert CaseRepository.search_fts("招商银行") == []
        assert CaseRepository.search_fts("跨境") == []
        assert CaseRepository.get_by_article("art_a1") == []


class TestCronLedgerBudgetAndRateLimit:
    def test_batch_idempotent_via_ledger(self, tmp_db: sqlite3.Connection):
        _seed_article(tmp_db, "art_b1", title="文章1")
        _seed_article(tmp_db, "art_b2", title="文章2")
        calls = []

        def fake_llm(prompt: str) -> str:
            calls.append(1)
            if "文章1" in prompt:
                return json.dumps({"cases": [{
                    "subject": "招商银行",
                    "case_type": "AI",
                    "case_action": "pilot",
                    "summary": "招商银行启动智能投顾试点",
                    "keywords": ["智能投顾", "大模型"],
                    "extraction_confidence": 85,
                }]})
            return json.dumps({"cases": []})

        r1 = extract_cases_batch(batch_size=10, llm_call_fn=fake_llm)
        assert r1["processed"] == 2
        assert r1["succeeded"] == 1
        assert r1["skipped"] == 1
        assert len(calls) == 2

        # 第二次运行应完全跳过 (0 次 LLM 调用)
        r2 = extract_cases_batch(batch_size=10, llm_call_fn=fake_llm)
        assert r2["processed"] == 0
        assert len(calls) == 2

        statuses = dict(
            tmp_db.execute("SELECT article_id, status FROM case_extract_runs").fetchall()
        )
        assert statuses == {"art_b1": "done", "art_b2": "no_cases"}

    def test_batch_stops_on_429(self, tmp_db: sqlite3.Connection):
        _seed_article(tmp_db, "art_r1", title="文章1")
        _seed_article(tmp_db, "art_r2", title="文章2")
        calls = 0

        def fake_llm_429(_prompt: str) -> str:
            nonlocal calls
            calls += 1
            raise RuntimeError("HTTP 429 rate limit exceeded")

        r = extract_cases_batch(batch_size=10, llm_call_fn=fake_llm_429)
        assert r["rate_limited"] is True
        assert r["processed"] == 1
        assert r["failed"] == 1
        assert calls == 1

    def test_429_does_not_burn_retry_budget(self, tmp_db: sqlite3.Connection):
        _seed_article(tmp_db, "art_r3", title="文章3")

        def fake_429(_prompt: str) -> str:
            raise RuntimeError("HTTP 429 rate limit exceeded")

        extract_cases_batch(batch_size=10, llm_call_fn=fake_429)
        row = tmp_db.execute(
            "SELECT status, attempts FROM case_extract_runs WHERE article_id = 'art_r3'"
        ).fetchone()
        assert row[0] == "failed"
        assert row[1] == 0
        pending = CaseRepository.get_pending_articles(batch_size=10)
        assert any(p["id"] == "art_r3" for p in pending)

    def test_daily_token_budget_gate(self, tmp_db: sqlite3.Connection):
        _seed_article(tmp_db, "art_t1", title="文章1")
        tmp_db.execute(
            "UPDATE app_settings SET value = '100' WHERE key = 'case_extract_daily_input_token_budget'"
        )
        now = datetime.now(timezone.utc).isoformat()
        tmp_db.execute(
            """
            INSERT INTO llm_jobs (id, task_type, article_id, status, attempts, created_at, input_tokens)
            VALUES ('j1', 'case_extract', 'art_t1', 'done', 1, ?, 150)
            """,
            (now,),
        )
        tmp_db.commit()

        allowed, used, budget = check_daily_token_budget(tmp_db)
        assert allowed is False
        assert used == 150 and budget == 100

        called = []
        r = extract_cases_batch(batch_size=10, llm_call_fn=lambda p: called.append(p) or "{}")
        assert r["budget_exhausted"] is True
        assert r["processed"] == 0
        assert called == []

    def test_budget_gate_counts_local_day_even_in_early_morning(self, tmp_db: sqlite3.Connection):
        """闸门按本地自然日结算，但记录存的是 UTC ISO。

        本地 00:00-08:00 时 UTC 还是前一天，用 date('now','localtime')
        去比会把当天已花掉的 token 整段漏算，闸门形同虚设。
        """
        from moni.cases.pipeline import _local_day_start_utc_iso

        _seed_article(tmp_db, "art_x", title="预算文章")
        tmp_db.execute(
            "UPDATE app_settings SET value = '1000' WHERE key = 'case_extract_daily_input_token_budget'"
        )
        day_start = datetime.fromisoformat(_local_day_start_utc_iso())
        tmp_db.execute(
            """
            INSERT INTO llm_jobs (id, task_type, article_id, status, attempts, created_at, input_tokens)
            VALUES ('j_in', 'case_extract', 'art_x', 'done', 1, ?, 300)
            """,
            ((day_start + timedelta(seconds=1)).isoformat(),),
        )
        tmp_db.execute(
            """
            INSERT INTO llm_jobs (id, task_type, article_id, status, attempts, created_at, input_tokens)
            VALUES ('j_out', 'case_extract', 'art_x', 'done', 1, ?, 900)
            """,
            ((day_start - timedelta(seconds=1)).isoformat(),),
        )
        tmp_db.commit()

        allowed, used, _ = check_daily_token_budget(tmp_db)
        assert used == 300  # 昨天那笔不算，今天的算
        assert allowed is True

    def test_cron_extract_cases_pending_and_lock(self, tmp_db: sqlite3.Connection):
        import moni.cron.jobs as cron_jobs

        _seed_article(tmp_db, "art_c1", title="cron 文章")
        res = cron_jobs.extract_cases_pending(
            batch_size=5,
            llm_call_fn=lambda _p: json.dumps({
                "cases": [{
                    "subject": "工商银行",
                    "case_type": "AI",
                    "case_action": "launch",
                    "summary": "工商银行上线智能客服系统",
                    "keywords": ["智能客服", "大模型"],
                    "extraction_confidence": 85,
                }]
            }),
        )
        assert res is not None and res["succeeded"] == 1
        row = tmp_db.execute(
            "SELECT job_id, success, new_articles, sources_run FROM cron_runs WHERE job_id = 'extract_cases_pending'"
        ).fetchone()
        assert row is not None
        assert row[1] == 1 and row[2] == 1 and row[3] == 1

        cron_jobs._CASE_LOCK.acquire()
        try:
            busy = cron_jobs.extract_cases_pending(batch_size=5)
            assert busy["skipped"] == "busy"
        finally:
            cron_jobs._CASE_LOCK.release()
