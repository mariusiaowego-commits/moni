"""sprint 86 —— 案例抽取可观测 + 榜单绑定。

钉住的失败模式：
1. case_extract 任务行没有 duration/result，详情页无内容可看。
2. 无 usage 时整条任务不落库（旧 `if usage_info is not None`）。
3. 一次批量抽取的多篇任务无法归到同一批次（batch_id）。
4. 历史 result 为空的任务，详情拿不到对应案例。
5. 看板只有 high 榜，新抽的 medium/low 案例永远不出现。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import moni.cases.pipeline as pipe
import moni.db.connection as conn_mod
import moni.db.migrate as mig_mod
from moni.cases.normalize import normalize_case
from moni.cases.repository import CaseRepository
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate

_BODY = (
    "招商银行今日宣布上线面向零售客户的 AI 投顾服务，并在深圳设立金融科技实验室，"
    "首批接入智能投顾与财富管理场景。该行表示，新服务将覆盖手机银行与线下网点，"
    "后续还将与多家头部大模型厂商合作，探索智能客服、风控与合规审查等应用。"
    "业内人士认为，这一动作标志着银行在数字人民币与跨境支付之外，开始把大模型能力"
    "落到具体业务流程，预计年内完成试点扩容。"
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path: Path):
    set_db_path(tmp_path / "test.db")
    migrate()
    yield get_db()
    close_db()
    conn_mod._CON = None
    conn_mod._DB_PATH = None
    mig_mod._CON = None


def _seed_article(conn, article_id: str = "art_86", title: str = "招商银行上线AI投顾"):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (article_id, title, f"u-{article_id}", "test", now, _BODY),
    )
    conn.commit()


def _article(article_id: str = "art_86") -> dict:
    conn = get_db()
    return dict(
        conn.execute(
            "SELECT id, title, raw_content, source, published_at FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
    )


def _case(**kw) -> dict:
    base = {
        "subject": "招商银行",
        "subject_raw": "招行",
        "case_date": "2026-11-27",
        "case_date_precision": "exact",
        "case_type": "AI",
        "case_action": "launch",
        "amount": None,
        "counterparty": None,
        "reference_value": "high",
        "reference_rationale": "",
        "summary": "招商银行上线AI投顾服务并设立金融科技实验室",
        "evidence_snippet": "",
        "extraction_confidence": 90,
        "keywords": ["智能投顾", "大模型"],
    }
    base.update(kw)
    return base


def _fake_llm(monkeypatch, reply: str, usage_info: dict | None = None):
    def _call(provider, model, prompt, usage=True):
        if usage_info is None:
            return reply
        return reply, usage_info

    monkeypatch.setattr(pipe, "call_llm", _call)


def _jobs(conn) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM llm_jobs WHERE task_type = 'case_extract' "
            "ORDER BY created_at"
        ).fetchall()
    ]


class TestCaseExtractJobAccounting:
    def test_success_records_duration_result_and_tokens(self, fresh_db, monkeypatch):
        _seed_article(fresh_db)
        _fake_llm(
            monkeypatch,
            json.dumps({"cases": [_case()]}, ensure_ascii=False),
            {"input_tokens": 120, "output_tokens": 30, "cost_usd": 0.002},
        )
        r = pipe.extract_cases_for_article(
            _article(), provider="p", model="m", batch_id="cb-test"
        )
        assert r["status"] == "completed"

        rows = _jobs(fresh_db)
        assert len(rows) == 1
        job = rows[0]
        assert job["status"] == "done"
        assert job["batch_id"] == "cb-test"
        assert job["duration_ms"] is not None and job["duration_ms"] >= 0
        assert job["started_at"] and job["finished_at"]
        assert job["input_tokens"] == 120
        payload = json.loads(job["result"])
        assert payload["outcome"] == "completed"
        assert payload["case_count"] == 1
        assert len(payload["case_ids"]) == 1

    def test_no_usage_still_records_job(self, fresh_db, monkeypatch):
        """旧逻辑只在 usage 非空时记账，漏了任务本身。"""
        _seed_article(fresh_db)
        _fake_llm(monkeypatch, json.dumps({"cases": [_case()]}, ensure_ascii=False))
        r = pipe.extract_cases_for_article(_article(), provider="p", model="m")
        assert r["status"] == "completed"
        rows = _jobs(fresh_db)
        assert len(rows) == 1
        assert rows[0]["input_tokens"] == 0
        assert json.loads(rows[0]["result"])["outcome"] == "completed"

    def test_no_cases_records_outcome(self, fresh_db, monkeypatch):
        _seed_article(fresh_db)
        _fake_llm(monkeypatch, json.dumps({"cases": []}))
        r = pipe.extract_cases_for_article(_article(), provider="p", model="m")
        assert r["status"] == "no_cases"
        payload = json.loads(_jobs(fresh_db)[0]["result"])
        assert payload["outcome"] == "no_cases"
        assert payload["case_count"] == 0

    def test_all_filtered_records_dropped_count(self, fresh_db, monkeypatch):
        _seed_article(fresh_db)
        bad = _case(summary="太短", keywords=[])
        _fake_llm(monkeypatch, json.dumps({"cases": [bad]}, ensure_ascii=False))
        r = pipe.extract_cases_for_article(_article(), provider="p", model="m")
        assert r["status"] == "no_cases"
        payload = json.loads(_jobs(fresh_db)[0]["result"])
        assert payload["outcome"] == "filtered"
        assert payload["dropped"] == 1

    def test_failure_records_failed_job(self, fresh_db, monkeypatch):
        _seed_article(fresh_db)

        def _boom(provider, model, prompt, usage=True):
            raise RuntimeError("upstream 500")

        monkeypatch.setattr(pipe, "call_llm", _boom)
        r = pipe.extract_cases_for_article(_article(), provider="p", model="m")
        assert r["status"] == "failed"
        job = _jobs(fresh_db)[0]
        assert job["status"] == "failed"
        assert "upstream 500" in (job["error"] or "")
        assert job["duration_ms"] is not None
        assert json.loads(job["result"])["outcome"] == "failed"

    def test_batch_rows_share_batch_id(self, fresh_db, monkeypatch):
        _seed_article(fresh_db, "art_a", "招商银行上线AI投顾")
        _seed_article(fresh_db, "art_b", "工商银行上线智能客服")
        _fake_llm(monkeypatch, json.dumps({"cases": [_case()]}, ensure_ascii=False))

        result = pipe.extract_cases_batch(
            batch_size=2, provider="p", model="m", check_budget=False
        )
        assert result["processed"] == 2
        assert result["batch_id"].startswith("cb-")
        rows = _jobs(fresh_db)
        assert len(rows) == 2
        assert {r["batch_id"] for r in rows} == {result["batch_id"]}


class TestJobApiSurfaces:
    def test_list_jobs_filters_by_task_type(self, fresh_db):
        from moni.llm.jobs import list_jobs

        _seed_article(fresh_db)
        now = datetime.now(timezone.utc).isoformat()
        for job_id, task_type in (
            ("j-case", "case_extract"),
            ("j-check", "fact_check"),
            ("j-check-plus", "fact_check_plus"),
            ("j-summary", "summary"),
        ):
            fresh_db.execute(
                "INSERT INTO llm_jobs (id, task_type, article_id, status, attempts, created_at) "
                "VALUES (?, ?, 'art_86', 'done', 1, ?)",
                (job_id, task_type, now),
            )
        fresh_db.commit()

        assert [r["id"] for r in list_jobs(task_type="case_extract")] == ["j-case"]
        check = {r["id"] for r in list_jobs(task_type="fact_check,fact_check_plus")}
        assert check == {"j-check", "j-check-plus"}
        assert len(list_jobs()) == 4

    def test_detail_attaches_extracted_cases(self, fresh_db, monkeypatch):
        from moni.web.routes.llm_jobs import get_llm_job

        _seed_article(fresh_db)
        _fake_llm(monkeypatch, json.dumps({"cases": [_case()]}, ensure_ascii=False))
        pipe.extract_cases_for_article(_article(), provider="p", model="m")
        job_id = _jobs(fresh_db)[0]["id"]

        detail = get_llm_job(job_id)
        assert detail["batch_id"] is None
        assert len(detail["extracted_cases"]) == 1
        assert detail["extracted_cases"][0]["subject"] == "招商银行"
        assert detail["extracted_cases"][0]["keywords"] == ["大模型", "智能投顾"]

    def test_detail_falls_back_for_legacy_empty_result(self, fresh_db):
        from moni.web.routes.llm_jobs import get_llm_job

        _seed_article(fresh_db)
        cs = normalize_case(_case())
        with fresh_db:
            CaseRepository.insert_cases(fresh_db, "art_86", [cs])
        now = datetime.now(timezone.utc).isoformat()
        fresh_db.execute(
            "INSERT INTO llm_jobs (id, task_type, article_id, status, attempts, created_at, result) "
            "VALUES ('legacy', 'case_extract', 'art_86', 'done', 1, ?, NULL)",
            (now,),
        )
        fresh_db.commit()

        detail = get_llm_job("legacy")
        assert len(detail["extracted_cases"]) == 1
        assert detail["extracted_cases"][0]["subject"] == "招商银行"


class TestStatsDualRanking:
    def test_recent_cases_and_high_value_limit(self, fresh_db):
        from moni.stats.repository import cases_dashboard_stats

        _seed_article(fresh_db)
        with fresh_db:
            for i in range(55):
                cs = normalize_case(_case(subject=f"机构{i:02d}"))
                CaseRepository.insert_cases(fresh_db, "art_86", [cs])
        fresh_db.execute(
            "UPDATE cases SET extracted_at = '2026-11-01T00:' || "
            "printf('%02d', CAST(substr(subject, 3) AS INTEGER)) || ':00+00:00'"
        )
        fresh_db.commit()

        data = cases_dashboard_stats("all")
        assert len(data["high_value_ranking"]) == 50
        assert len(data["recent_cases"]) == 20
        assert data["recent_cases"][0]["subject"] == "机构54"
        assert data["recent_cases"][0]["extracted_at"] > data["recent_cases"][1]["extracted_at"]
