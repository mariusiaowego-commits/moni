"""tests/test_cases.py — F6 同业案例库测试。

覆盖：migration / normalize / pipeline / API / CHECK constraint / UNIQUE 去重 / 事务回滚
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.cases.normalize import (
    normalize_subject,
    normalize_case_type,
    normalize_case_action,
    normalize_amount,
    normalize_reference_value,
    normalize_date,
    normalize_case,
)
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
def app():
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


# 准备一条 article 用于 cases 插入 (FK 约束)
@pytest.fixture
def sample_article(tmp_path):
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO articles (id, title, url, source, fetched_at, has_ai_summary, raw_content)
           VALUES (?, ?, ?, ?, ?, 0, ?)""",
        ("art_001", "测试文章", "https://example.com/1", "test_source", now, "招商银行 2026 年 8 月 1 日上线 AI 智能投顾"),
    )
    conn.commit()
    return "art_001"


# ---------------------------------------------------------------------------
# migration 024 — schema 验证
# ---------------------------------------------------------------------------

class TestMigration024:
    """验证 migration 024 已应用."""

    def test_schema_version_is_24(self):
        conn = get_db()
        version = conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]
        assert version >= 24

    def test_cases_table_exists(self):
        conn = get_db()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cases'"
        ).fetchall()
        assert len(rows) == 1

    def test_case_tags_table_exists(self):
        conn = get_db()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='case_tags'"
        ).fetchall()
        assert len(rows) == 1

    def test_cases_fts_uses_trigram_tokenizer(self):
        conn = get_db()
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='cases_fts'"
        ).fetchone()[0]
        assert "trigram" in sql.lower()

    def test_cases_fts_triggers_exist(self):
        conn = get_db()
        triggers = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='cases'"
        ).fetchall()
        names = {t[0] for t in triggers}
        assert "cases_fts_ai" in names
        assert "cases_fts_ad" in names
        assert "cases_fts_au" in names

    def test_cases_check_case_type_constraint(self):
        conn = get_db()
        # FK 限制: 必须先有 article
        conn.execute(
            "INSERT INTO articles (id, title, url, source, fetched_at, has_ai_summary) VALUES ('art_x', 'x', 'http://x', 's', '2026', 0)"
        )
        with pytest.raises(Exception):
            conn.execute(
                """INSERT INTO cases
                   (case_id, article_id, subject, case_type, case_action,
                    extraction_status, case_lifecycle, review_status, created_at, updated_at)
                   VALUES ('X-1', 'art_x', '测试', 'WRONG', 'launch',
                           'completed', 'draft', 'pending', '2026', '2026')"""
            )

    def test_cases_check_case_action_constraint(self):
        conn = get_db()
        conn.execute(
            "INSERT INTO articles (id, title, url, source, fetched_at, has_ai_summary) VALUES ('art_x', 'x', 'http://x', 's', '2026', 0)"
        )
        with pytest.raises(Exception):
            conn.execute(
                """INSERT INTO cases
                   (case_id, article_id, subject, case_type, case_action,
                    extraction_status, case_lifecycle, review_status, created_at, updated_at)
                   VALUES ('X-1', 'art_x', '测试', 'AI', 'WRONG',
                           'completed', 'draft', 'pending', '2026', '2026')"""
            )

    def test_cases_check_lifecycle_constraint(self):
        conn = get_db()
        conn.execute(
            "INSERT INTO articles (id, title, url, source, fetched_at, has_ai_summary) VALUES ('art_x', 'x', 'http://x', 's', '2026', 0)"
        )
        with pytest.raises(Exception):
            conn.execute(
                """INSERT INTO cases
                   (case_id, article_id, subject, case_type, case_action,
                    extraction_status, case_lifecycle, review_status, created_at, updated_at)
                   VALUES ('X-1', 'art_x', '测试', 'AI', 'launch',
                           'completed', 'WRONG', 'pending', '2026', '2026')"""
            )


# ---------------------------------------------------------------------------
# normalize.py — 字段归一
# ---------------------------------------------------------------------------

class TestNormalizeSubject:
    """主体别名归一 (F6 BANK_ALIAS_MAP 100+)."""

    def test_zhaohang_to_zhaoshang(self):
        assert normalize_subject("招行") == "招商银行"

    def test_gonghang_to_gongshang(self):
        assert normalize_subject("工行") == "工商银行"

    def test_mayisheng_tai(self):
        assert normalize_subject("蚂蚁") == "蚂蚁集团"

    def test_unknown_passthrough(self):
        assert normalize_subject("某新公司") == "某新公司"

    def test_empty_returns_none(self):
        assert normalize_subject("") is None
        assert normalize_subject(None) is None

    def test_alias_in_longer_string(self):
        # 关键词匹配 (长字符串中含别名)
        result = normalize_subject("招行某业务部")
        assert "招商银行" in result


class TestNormalizeCaseType:
    """case_type 归一 (2 类: AI / PI)."""

    def test_ai_keywords(self):
        assert normalize_case_type("AI 大模型应用") == "AI"
        assert normalize_case_type("数字化升级") == "AI"
        assert normalize_case_type("智能投顾") == "AI"
        assert normalize_case_type("LLM 风控") == "AI"

    def test_pi_keywords(self):
        assert normalize_case_type("产品创新") == "PI"
        assert normalize_case_type("理财产品") == "PI"
        assert normalize_case_type("新业务上线") == "PI"

    def test_default_ai(self):
        # 默认 AI (LLM 是当前最热)
        assert normalize_case_type("未知类型") == "AI"
        assert normalize_case_type("", "招商银行") == "AI"


class TestNormalizeCaseAction:
    """case_action 归一 (5 类)."""

    def test_launch(self):
        assert normalize_case_action("正式上线") == "launch"
        assert normalize_case_action("推出新产品") == "launch"
        assert normalize_case_action("发布") == "launch"

    def test_pilot(self):
        assert normalize_case_action("试运行") == "pilot"
        assert normalize_case_action("小范围试点") == "pilot"

    def test_scale(self):
        assert normalize_case_action("全面推广") == "scale"
        assert normalize_case_action("规模化") == "scale"

    def test_transform(self):
        assert normalize_case_action("数字化转型") == "transform"
        assert normalize_case_action("核心系统升级") == "transform"

    def test_exit(self):
        assert normalize_case_action("下线") == "exit"
        assert normalize_case_action("关停") == "exit"

    def test_default_launch(self):
        assert normalize_case_action("未知动作") == "launch"
        assert normalize_case_action("") == "launch"


class TestNormalizeAmount:
    """金额归一."""

    def test_yi_yuan(self):
        assert normalize_amount("投入 5 亿元") == "5 亿元"
        assert normalize_amount("融资 30 亿") == "30 亿元"

    def test_wan_yuan(self):
        assert normalize_amount("100 万元") == "100 万元"
        assert normalize_amount("50 万") == "50 万元"

    def test_user_count(self):
        assert normalize_amount("1000 用户") == "1000 用户"
        assert normalize_amount("500 万户") == "500 万户"

    def test_decimal_trim(self):
        assert normalize_amount("5.0 亿元") == "5 亿元"

    def test_no_match_passthrough(self):
        assert normalize_amount("大规模") == "大规模"

    def test_empty_returns_none(self):
        assert normalize_amount("") is None
        assert normalize_amount(None) is None


class TestNormalizeReferenceValue:
    """reference_value 高/中/低."""

    def test_high(self):
        assert normalize_reference_value("高") == "high"
        assert normalize_reference_value("high") == "high"
        assert normalize_reference_value("重要") == "high"

    def test_low(self):
        assert normalize_reference_value("低") == "low"
        assert normalize_reference_value("low") == "low"

    def test_default_medium(self):
        assert normalize_reference_value("中") == "medium"
        assert normalize_reference_value("") == "medium"
        assert normalize_reference_value(None) == "medium"


class TestNormalizeDate:
    """中文日期归一."""

    def test_iso_passthrough(self):
        assert normalize_date("2026-08-07") == ("2026-08-07", "exact")
        assert normalize_date("2026-08") == ("2026-08", "month")

    def test_chinese_full_date(self):
        assert normalize_date("2026年8月7日") == ("2026-08-07", "exact")

    def test_chinese_month(self):
        assert normalize_date("2026年8月") == ("2026-08", "month")

    def test_relative(self):
        assert normalize_date("本季度")[1] == "relative"
        assert normalize_date("近期")[1] == "relative"

    def test_unknown_empty(self):
        result = normalize_date("")
        assert result == (None, "unknown")
        result = normalize_date(None)
        assert result == (None, "unknown")


class TestNormalizeCase:
    """完整 normalize_case 函数 (高层组合)."""

    def test_full_valid_case(self):
        raw = {
            "subject": "招行",
            "case_type": "AI",
            "case_action": "上线",
            "case_date": "2026-08-07",
            "amount": "5 亿元",
            "reference_value": "高",
            "summary": "招行上线 AI 投顾" * 5,
            "evidence_snippet": "...",
            "extraction_confidence": 95,
        }
        result = normalize_case(raw)
        assert result is not None
        assert result["subject"] == "招商银行"  # alias 归一
        assert result["case_type"] == "AI"
        assert result["case_action"] == "launch"
        assert result["case_date"] == "2026-08-07"
        assert result["case_date_precision"] == "exact"
        assert result["amount"] == "5 亿元"
        assert result["reference_value"] == "high"
        # 截断
        assert len(result["summary"]) <= 200

    def test_empty_subject_returns_none(self):
        raw = {"subject": "", "case_type": "AI", "case_action": "launch"}
        assert normalize_case(raw) is None

    def test_missing_subject_returns_none(self):
        assert normalize_case({}) is None


# ---------------------------------------------------------------------------
# Repository — CRUD + state machine
# ---------------------------------------------------------------------------

class TestCaseRepository:
    """CaseRepository CRUD + 状态机."""

    def test_insert_cases_basic(self, sample_article):
        from moni.cases.repository import CaseRepository
        cases = [{
            "subject": "招商银行",
            "subject_raw": "招行",
            "case_date": "2026-08-07",
            "case_date_precision": "exact",
            "case_type": "AI",
            "case_action": "launch",
            "counterparty": None,
            "amount": "5 亿元",
            "reference_value": "high",
            "reference_rationale": "招行标杆",
            "summary": "招行上线 AI 投顾",
            "evidence_snippet": "...",
            "extraction_confidence": 95,
        }]
        conn = get_db()
        inserted = CaseRepository.insert_cases(conn, sample_article, cases)
        assert inserted == 1

    def test_case_id_format_MN_AI_YYMM_NNNN(self, sample_article):
        from moni.cases.repository import CaseRepository
        cases = [{
            "subject": "招商银行",
            "subject_raw": "招行",
            "case_date": "2026-08-07",
            "case_date_precision": "exact",
            "case_type": "AI",
            "case_action": "launch",
            "amount": None, "counterparty": None,
            "reference_value": "medium",
            "reference_rationale": "", "summary": "test",
            "evidence_snippet": "", "extraction_confidence": 80,
        }]
        conn = get_db()
        CaseRepository.insert_cases(conn, sample_article, cases)
        rows = conn.execute("SELECT case_id FROM cases").fetchall()
        case_id = rows[0][0]
        assert case_id.startswith("MN-AI-2608-")
        assert len(case_id) == len("MN-AI-2608-") + 4  # 4 位序号

    def test_unique_constraint_same_article_subject_type_action(self, sample_article):
        from moni.cases.repository import CaseRepository
        cases = [{
            "subject": "招行",
            "subject_raw": "招行",
            "case_date": "2026-08-07",
            "case_date_precision": "exact",
            "case_type": "AI",
            "case_action": "launch",
            "amount": None, "counterparty": None,
            "reference_value": "medium",
            "reference_rationale": "", "summary": "test",
            "evidence_snippet": "", "extraction_confidence": 80,
        }]
        conn = get_db()
        # 第一次插入
        CaseRepository.insert_cases(conn, sample_article, cases)
        # 第二次插入同 article + 同 subject + 同 case_type + 同 case_action — 应被 UNIQUE 拦截
        # 注: subject 在 INSERT 时已经被 normalize 为 "招商银行"
        inserted2 = CaseRepository.insert_cases(conn, sample_article, cases)
        # 应当 0 条插入 (被 UNIQUE 约束去重)
        assert inserted2 == 0

    def test_state_machine_confirm(self, sample_article):
        from moni.cases.repository import CaseRepository
        cases = [{
            "subject": "招行", "subject_raw": "招行",
            "case_date": "2026-08-07", "case_date_precision": "exact",
            "case_type": "AI", "case_action": "launch",
            "amount": None, "counterparty": None,
            "reference_value": "medium", "reference_rationale": "",
            "summary": "test", "evidence_snippet": "", "extraction_confidence": 80,
        }]
        conn = get_db()
        CaseRepository.insert_cases(conn, sample_article, cases)
        conn.commit()
        case_id = conn.execute("SELECT case_id FROM cases").fetchone()[0]
        result = CaseRepository.confirm(conn, case_id)
        assert result is True
        # 二次 confirm 应返回 False (只能从 pending → confirmed)
        result2 = CaseRepository.confirm(conn, case_id)
        assert result2 is False

    def test_state_machine_reject(self, sample_article):
        from moni.cases.repository import CaseRepository
        cases = [{
            "subject": "招行", "subject_raw": "招行",
            "case_date": "2026-08-07", "case_date_precision": "exact",
            "case_type": "AI", "case_action": "launch",
            "amount": None, "counterparty": None,
            "reference_value": "medium", "reference_rationale": "",
            "summary": "test", "evidence_snippet": "", "extraction_confidence": 80,
        }]
        conn = get_db()
        CaseRepository.insert_cases(conn, sample_article, cases)
        conn.commit()
        case_id = conn.execute("SELECT case_id FROM cases").fetchone()[0]
        result = CaseRepository.reject(conn, case_id, reason="不准确")
        assert result is True

    def test_state_machine_lifecycle_update(self, sample_article):
        from moni.cases.repository import CaseRepository
        cases = [{
            "subject": "招行", "subject_raw": "招行",
            "case_date": "2026-08-07", "case_date_precision": "exact",
            "case_type": "AI", "case_action": "launch",
            "amount": None, "counterparty": None,
            "reference_value": "medium", "reference_rationale": "",
            "summary": "test", "evidence_snippet": "", "extraction_confidence": 80,
        }]
        conn = get_db()
        CaseRepository.insert_cases(conn, sample_article, cases)
        conn.commit()
        case_id = conn.execute("SELECT case_id FROM cases").fetchone()[0]

        # 默认 draft
        row = conn.execute("SELECT case_lifecycle FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        assert row[0] == "draft"

        # 推到 verified
        CaseRepository.update_lifecycle(conn, case_id, "verified")
        row = conn.execute("SELECT case_lifecycle FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        assert row[0] == "verified"

        # 无效 lifecycle 应抛错
        with pytest.raises(ValueError):
            CaseRepository.update_lifecycle(conn, case_id, "INVALID")


# ---------------------------------------------------------------------------
# FTS5 search
# ---------------------------------------------------------------------------

class TestFTSSearch:
    """cases_fts 全文检索 (trigram tokenizer, 中文)."""

    def test_chinese_search_finds_subject(self, sample_article):
        from moni.cases.repository import CaseRepository
        cases = [{
            "subject": "招商银行", "subject_raw": "招行",
            "case_date": "2026-08-07", "case_date_precision": "exact",
            "case_type": "AI", "case_action": "launch",
            "amount": "5 亿元", "counterparty": "智能投顾团队",
            "reference_value": "high", "reference_rationale": "标杆",
            "summary": "招商银行全面上线AI智能投顾系统",
            "evidence_snippet": "招行今日宣布...",
            "extraction_confidence": 95,
        }]
        conn = get_db()
        CaseRepository.insert_cases(conn, sample_article, cases)
        conn.commit()

        # 中文搜索: "招商银行" (trigram tokenizer, 至少 3 字符)
        results = CaseRepository.search_fts("招商银行")
        assert len(results) >= 1
        assert "招商银行" in [r["subject"] for r in results]

    def test_fts_auto_sync_on_insert(self, sample_article):
        from moni.cases.repository import CaseRepository
        cases = [{
            "subject": "工商银行", "subject_raw": "工行",
            "case_date": "2026-08-01", "case_date_precision": "exact",
            "case_type": "AI", "case_action": "launch",
            "amount": None, "counterparty": None,
            "reference_value": "medium", "reference_rationale": "",
            "summary": "工行完成核心系统转型",
            "evidence_snippet": "", "extraction_confidence": 80,
        }]
        conn = get_db()
        CaseRepository.insert_cases(conn, sample_article, cases)
        conn.commit()

        # FTS5 应该自动同步
        fts_count = conn.execute("SELECT COUNT(*) FROM cases_fts").fetchone()[0]
        assert fts_count >= 1

    def test_fts_delete_cascade(self, sample_article):
        from moni.cases.repository import CaseRepository
        cases = [{
            "subject": "招商银行", "subject_raw": "招行",
            "case_date": "2026-08-07", "case_date_precision": "exact",
            "case_type": "AI", "case_action": "launch",
            "amount": None, "counterparty": None,
            "reference_value": "medium", "reference_rationale": "",
            "summary": "test", "evidence_snippet": "", "extraction_confidence": 80,
        }]
        conn = get_db()
        CaseRepository.insert_cases(conn, sample_article, cases)
        conn.commit()
        # 删除 article (FK CASCADE 应删 cases)
        conn.execute("DELETE FROM articles WHERE id = ?", (sample_article,))
        conn.commit()
        # cases_fts 应该同步删除
        fts_count = conn.execute("SELECT COUNT(*) FROM cases_fts").fetchone()[0]
        assert fts_count == 0


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------

class TestCaseAPI:
    """cases API 端点集成测试."""

    def test_list_empty(self, client):
        resp = client.get("/api/cases")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_with_filter(self, client, sample_article):
        from moni.cases.repository import CaseRepository
        # 插入 2 条 AI + 1 条 PI
        cases = [
            {
                "subject": "招商银行", "subject_raw": "招行",
                "case_date": "2026-08-07", "case_date_precision": "exact",
                "case_type": "AI", "case_action": "launch",
                "amount": None, "counterparty": None,
                "reference_value": "high", "reference_rationale": "",
                "summary": "AI 案例 1", "evidence_snippet": "", "extraction_confidence": 90,
            },
            {
                "subject": "工商银行", "subject_raw": "工行",
                "case_date": "2026-08-08", "case_date_precision": "exact",
                "case_type": "PI", "case_action": "scale",
                "amount": None, "counterparty": None,
                "reference_value": "medium", "reference_rationale": "",
                "summary": "PI 案例 1", "evidence_snippet": "", "extraction_confidence": 85,
            },
        ]
        conn = get_db()
        CaseRepository.insert_cases(conn, sample_article, cases)
        conn.commit()

        # 筛选 AI
        resp = client.get("/api/cases?case_type=AI")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["case_type"] == "AI"

        # 筛选 PI
        resp = client.get("/api/cases?case_type=PI")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["case_type"] == "PI"

    def test_stats(self, client, sample_article):
        from moni.cases.repository import CaseRepository
        cases = [
            {
                "subject": "招商银行", "subject_raw": "招行",
                "case_date": "2026-08-07", "case_date_precision": "exact",
                "case_type": "AI", "case_action": "launch",
                "amount": None, "counterparty": None,
                "reference_value": "high", "reference_rationale": "",
                "summary": "test", "evidence_snippet": "", "extraction_confidence": 90,
            },
        ]
        conn = get_db()
        CaseRepository.insert_cases(conn, sample_article, cases)
        conn.commit()

        resp = client.get("/api/cases/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert "AI" in data["by_type"]
        assert data["by_type"]["AI"] == 1

    def test_get_by_id(self, client, sample_article):
        from moni.cases.repository import CaseRepository
        cases = [{
            "subject": "招商银行", "subject_raw": "招行",
            "case_date": "2026-08-07", "case_date_precision": "exact",
            "case_type": "AI", "case_action": "launch",
            "amount": "5 亿元", "counterparty": None,
            "reference_value": "high", "reference_rationale": "标杆",
            "summary": "招行上线 AI 投顾", "evidence_snippet": "...", "extraction_confidence": 95,
        }]
        conn = get_db()
        CaseRepository.insert_cases(conn, sample_article, cases)
        conn.commit()
        case_id = conn.execute("SELECT case_id FROM cases").fetchone()[0]

        resp = client.get(f"/api/cases/{case_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["case_id"] == case_id
        assert data["subject"] == "招商银行"

    def test_get_by_id_404(self, client):
        resp = client.get("/api/cases/MN-AI-2099-9999")
        assert resp.status_code == 404

    def test_search(self, client, sample_article):
        from moni.cases.repository import CaseRepository
        cases = [{
            "subject": "招商银行", "subject_raw": "招行",
            "case_date": "2026-08-07", "case_date_precision": "exact",
            "case_type": "AI", "case_action": "launch",
            "amount": None, "counterparty": None,
            "reference_value": "high", "reference_rationale": "",
            "summary": "招商银行全面上线AI智能投顾系统",
            "evidence_snippet": "", "extraction_confidence": 90,
        }]
        conn = get_db()
        CaseRepository.insert_cases(conn, sample_article, cases)
        conn.commit()

        resp = client.get("/api/cases/search?q=招商银行")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert "招商银行" in [item["subject"] for item in data["items"]]

    def test_by_article(self, client, sample_article):
        from moni.cases.repository import CaseRepository
        cases = [{
            "subject": "招商银行", "subject_raw": "招行",
            "case_date": "2026-08-07", "case_date_precision": "exact",
            "case_type": "AI", "case_action": "launch",
            "amount": None, "counterparty": None,
            "reference_value": "high", "reference_rationale": "",
            "summary": "test", "evidence_snippet": "", "extraction_confidence": 90,
        }]
        conn = get_db()
        CaseRepository.insert_cases(conn, sample_article, cases)
        conn.commit()

        resp = client.get(f"/api/cases/by-article/{sample_article}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1

    def test_by_article_404(self, client):
        resp = client.get("/api/cases/by-article/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Pipeline — LLM 抽取 (mock llm_call_fn)
# ---------------------------------------------------------------------------

class TestPipeline:
    """extract_cases_for_article 同步流程 (mock LLM)."""

    def test_prompt_placeholder_rendering(self, sample_article):
        """回归: case_extraction.txt 占位符必须是 $var (Template 风格),
        不能是 {var} (format 风格) — 2026-08-10 修 bug (F6 案例库一直空)。"""
        from moni.cases.pipeline import _render_prompt

        conn = get_db()
        article = dict(conn.execute(
            "SELECT id, title, source, raw_content FROM articles WHERE id = ?",
            (sample_article,),
        ).fetchone())
        article["raw_content"] = "招商银行 2026 年 8 月 1 日正式上线 AI 智能投顾系统"

        prompt = _render_prompt(article)
        # 占位符必须被真实内容替换
        assert "{title}" not in prompt
        assert "{content}" not in prompt
        assert "{source}" not in prompt
        # 真实内容必须注入
        assert article["title"] in prompt
        assert "AI 智能投顾" in prompt

    def test_full_flow_with_mock(self, sample_article):
        from moni.cases.pipeline import extract_cases_for_article

        # 准备 article dict
        conn = get_db()
        article = dict(conn.execute(
            "SELECT id, title, source, raw_content FROM articles WHERE id = ?",
            (sample_article,),
        ).fetchone())

        # Mock LLM 返回合规 JSON
        mock_reply = json.dumps({
            "cases": [
                {
                    "subject": "招行",
                    "case_type": "AI",
                    "case_action": "上线",
                    "case_date": "2026-08-07",
                    "amount": "5 亿元",
                    "reference_value": "high",
                    "summary": "招行 AI 投顾",
                    "evidence_snippet": "...",
                    "extraction_confidence": 90,
                }
            ]
        }, ensure_ascii=False)

        def fake_llm(prompt):
            return mock_reply

        result = extract_cases_for_article(article, llm_call_fn=fake_llm)
        assert result["status"] == "completed"
        assert result["case_count"] == 1
        assert result["error"] is None

    def test_no_cases_returns_no_cases_status(self, sample_article):
        from moni.cases.pipeline import extract_cases_for_article

        conn = get_db()
        article = dict(conn.execute(
            "SELECT id, title, source, raw_content FROM articles WHERE id = ?",
            (sample_article,),
        ).fetchone())

        def fake_llm(prompt):
            return json.dumps({"cases": []}, ensure_ascii=False)

        result = extract_cases_for_article(article, llm_call_fn=fake_llm)
        assert result["status"] == "no_cases"
        assert result["case_count"] == 0

    def test_invalid_json_returns_failed(self, sample_article):
        from moni.cases.pipeline import extract_cases_for_article

        conn = get_db()
        article = dict(conn.execute(
            "SELECT id, title, source, raw_content FROM articles WHERE id = ?",
            (sample_article,),
        ).fetchone())

        def fake_llm(prompt):
            return "完全不是 JSON 的输出"

        result = extract_cases_for_article(article, llm_call_fn=fake_llm)
        assert result["status"] in ("no_cases", "failed")

    def test_parse_three_strategies(self):
        """验证 _parse_cases_from_reply 三策略 + json-repair 兜底."""
        from moni.cases.pipeline import _parse_cases_from_reply

        # Strategy 1: ```json code block
        raw1 = '这是一些文字\n```json\n{"cases": [{"subject": "招行", "case_type": "AI"}]}\n```\n后面还有'
        assert len(_parse_cases_from_reply(raw1)) == 1

        # Strategy 2: 找 "cases" 键
        raw2 = '前面 "cases": [{"subject": "工行", "case_type": "AI"}] 后面'
        assert len(_parse_cases_from_reply(raw2)) == 1

        # Strategy 3: 整个 JSON 对象
        raw3 = '{"cases": [{"subject": "建行", "case_type": "PI"}]}'
        assert len(_parse_cases_from_reply(raw3)) == 1

        # Strategy 4: 逐条正则 (宽松)
        raw4 = '散落多个对象 {"case_type": "AI", "subject": "农行"} {"case_type": "PI", "subject": "中行"}'
        assert len(_parse_cases_from_reply(raw4)) == 2

    def test_empty_subject_drops_case(self, sample_article):
        from moni.cases.pipeline import extract_cases_for_article

        conn = get_db()
        article = dict(conn.execute(
            "SELECT id, title, source, raw_content FROM articles WHERE id = ?",
            (sample_article,),
        ).fetchone())

        # subject 为空应被 normalize 丢弃
        mock_reply = json.dumps({
            "cases": [
                {"subject": "", "case_type": "AI", "case_action": "上线"}
            ]
        }, ensure_ascii=False)

        def fake_llm(prompt):
            return mock_reply

        result = extract_cases_for_article(article, llm_call_fn=fake_llm)
        # subject 空被 normalize_case 返回 None, 整条丢弃
        assert result["case_count"] == 0