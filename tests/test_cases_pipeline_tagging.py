"""moni.cases.pipeline — 关键词 LLM 打标端到端测试 (v1.0.36 sprint-18).

覆盖:
- LLM 回复含 keywords → cases + case_keywords 同事务落库
- LLM 无 keywords → 不报错, 仅 cases 落库
- manual_user 关键词不被 auto_llm 覆盖 (重试场景)
- keywords 别名归一在 pipeline 层生效
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from moni.db.connection import set_db_path, get_db, close_db
import moni.db.connection as conn_mod
import moni.db.migrate as mig_mod
from moni.db.migrate import migrate


@pytest.fixture(autouse=True)
def fresh_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    migrate()
    yield get_db()
    close_db()
    conn_mod._CON = None
    conn_mod._DB_PATH = None
    mig_mod._CON = None


_BODY = (
    "招商银行今日宣布上线面向零售客户的 AI 投顾服务，并在深圳设立金融科技实验室，"
    "首批接入智能投顾与财富管理场景。该行表示，新服务将覆盖手机银行与线下网点，"
    "后续还将与多家头部大模型厂商合作，探索智能客服、风控与合规审查等应用。"
    "业内人士认为，这一动作标志着银行在数字人民币与跨境支付之外，开始把大模型能力"
    "落到具体业务流程，预计年内完成试点扩容。"
)


def _seed_article(conn, article_id: str = "art_001") -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            article_id,
            "招商银行上线AI投顾并设立金融科技实验室",
            "u",
            "test",
            now,
            _BODY,
        ),
    )
    conn.commit()


def _get_article(article_id: str = "art_001") -> dict:
    conn = get_db()
    return dict(conn.execute(
        "SELECT id, title, raw_content, source, published_at FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone())


class TestPipelineExtractsKeywords:
    """LLM 回复含 keywords → cases + case_keywords 同事务落库."""

    def test_basic_keywords_insert(self, fresh_db):
        import json
        from moni.cases.pipeline import extract_cases_for_article

        _seed_article(fresh_db)
        mock_reply = json.dumps({
            "cases": [{
                "subject": "招商银行", "subject_raw": "招行",
                "case_date": "2026-08-07", "case_date_precision": "exact",
                "case_type": "AI", "case_action": "launch",
                "amount": None, "counterparty": None,
                "reference_value": "high", "reference_rationale": "",
                "summary": "招行上线AI投顾并设立金融科技实验室", "evidence_snippet": "",
                "extraction_confidence": 90,
                "keywords": ["智能投顾", "财富管理"],
            }],
        }, ensure_ascii=False)

        def fake_llm(prompt: str) -> str:
            return mock_reply

        art = _get_article()
        result = extract_cases_for_article(art, llm_call_fn=fake_llm)

        assert result["status"] == "completed"
        assert result["case_count"] == 1

        # cases 1 条
        cases_rows = fresh_db.execute("SELECT * FROM cases").fetchall()
        assert len(cases_rows) == 1

        # case_keywords 2 条 (auto_llm)
        kw_rows = fresh_db.execute(
            "SELECT keyword, source_type FROM case_keywords ORDER BY keyword"
        ).fetchall()
        assert len(kw_rows) == 2
        keywords = {r["keyword"]: r["source_type"] for r in kw_rows}
        assert keywords == {"智能投顾": "auto_llm", "财富管理": "auto_llm"}

    def test_no_keywords_dropped_by_gate(self, fresh_db):
        """LLM 无 keywords → 质量闸丢弃该条, 不落 cases, 不报错."""
        import json
        from moni.cases.pipeline import extract_cases_for_article

        _seed_article(fresh_db)
        mock_reply = json.dumps({
            "cases": [{
                "subject": "招商银行", "subject_raw": "招行",
                "case_date": "2026-08-07", "case_date_precision": "exact",
                "case_type": "AI", "case_action": "launch",
                "amount": None, "counterparty": None,
                "reference_value": "medium", "reference_rationale": "",
                "summary": "招行上线AI投顾服务并设立实验室", "evidence_snippet": "",
                "extraction_confidence": 90,
                # 注意: 无 keywords 字段
            }],
        }, ensure_ascii=False)

        def fake_llm(prompt: str) -> str:
            return mock_reply

        art = _get_article()
        result = extract_cases_for_article(art, llm_call_fn=fake_llm)
        assert result["status"] == "no_cases"
        assert result["case_count"] == 0
        assert result["error"] is None
        assert fresh_db.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 0
        assert fresh_db.execute("SELECT COUNT(*) FROM case_keywords").fetchone()[0] == 0


class TestManualUserNotOverwritten:
    """重抽后 manual_user 关键词不被 auto_llm 覆盖."""

    def test_pipeline_doesnt_overwrite_manual(self, fresh_db):
        import json
        from moni.cases.pipeline import extract_cases_for_article
        from moni.cases.repository import CaseRepository

        _seed_article(fresh_db)
        # 第一轮 pipeline 提取 (auto_llm "智能投顾")
        mock_reply_1 = json.dumps({
            "cases": [{
                "subject": "招商银行", "subject_raw": "招行",
                "case_date": "2026-08-07", "case_date_precision": "exact",
                "case_type": "AI", "case_action": "launch",
                "amount": None, "counterparty": None,
                "reference_value": "high", "reference_rationale": "",
                "summary": "招行上线AI投顾服务并设立实验室", "evidence_snippet": "",
                "extraction_confidence": 90,
                "keywords": ["智能投顾", "财富管理"],
            }],
        }, ensure_ascii=False)

        def fake_llm_1(prompt: str) -> str:
            return mock_reply_1

        art = _get_article()
        extract_cases_for_article(art, llm_call_fn=fake_llm_1)

        case_id = fresh_db.execute("SELECT case_id FROM cases").fetchone()[0]
        # 用户手工加 "智能投顾" 关键词 (upsert → 改 source_type='manual_user')
        CaseRepository.add_keyword(case_id, "智能投顾")

        # 验证是 manual_user (而非 auto_llm)
        kw = fresh_db.execute(
            "SELECT source_type FROM case_keywords WHERE keyword = ?", ("智能投顾",)
        ).fetchone()
        assert kw["source_type"] == "manual_user"

        # 第二轮 pipeline 重抽 (auto_llm 也输出 "智能投顾")
        mock_reply_2 = json.dumps({
            "cases": [{
                "subject": "招商银行", "subject_raw": "招行",
                "case_date": "2026-08-07", "case_date_precision": "exact",
                "case_type": "AI", "case_action": "launch",
                "amount": None, "counterparty": None,
                "reference_value": "high", "reference_rationale": "",
                "summary": "招行上线AI投顾服务并设立实验室", "evidence_snippet": "",
                "extraction_confidence": 90,
                "keywords": ["智能投顾", "财富管理"],
            }],
        }, ensure_ascii=False)

        def fake_llm_2(prompt: str) -> str:
            return mock_reply_2

        extract_cases_for_article(art, llm_call_fn=fake_llm_2)

        # 验证: 仍是 manual_user (auto_llm 没覆盖)
        kw = fresh_db.execute(
            "SELECT source_type FROM case_keywords WHERE keyword = ?", ("智能投顾",)
        ).fetchone()
        assert kw["source_type"] == "manual_user", (
            f"manual_user 被 auto_llm 覆盖! source_type={kw['source_type']}"
        )

    def test_restore_manual_keywords_skips_archived(self, fresh_db):
        """重抽贴回人工词时不落到已归档案例上."""
        from moni.cases.normalize import normalize_case
        from moni.cases.repository import CaseRepository

        _seed_article(fresh_db, "art_arch")
        cs = normalize_case({
            "subject": "招商银行",
            "case_type": "AI",
            "case_action": "launch",
            "summary": "招行案例",
            "evidence_snippet": "",
            "keywords": [],
        })
        with fresh_db:
            CaseRepository.insert_cases(fresh_db, "art_arch", [cs])
        case_id = fresh_db.execute("SELECT case_id FROM cases").fetchone()[0]
        assert CaseRepository.soft_delete_case(case_id) is True

        restored = CaseRepository.restore_manual_keywords(
            fresh_db,
            "art_arch",
            [{
                "subject": "招商银行", "case_type": "AI", "case_action": "launch",
                "keyword": "智能投顾", "weight": 3,
            }],
        )
        assert restored == 0
        assert fresh_db.execute("SELECT COUNT(*) FROM case_keywords").fetchone()[0] == 0


class TestKeywordsNormalizePipeline:
    """关键词别名归一在 pipeline 层生效 (跨境清算 → 跨境支付)."""

    def test_alias_collapsed_in_pipeline(self, fresh_db):
        import json
        from moni.cases.pipeline import extract_cases_for_article

        _seed_article(fresh_db)
        mock_reply = json.dumps({
            "cases": [{
                "subject": "招商银行", "subject_raw": "招行",
                "case_date": "2026-08-07", "case_date_precision": "exact",
                "case_type": "AI", "case_action": "launch",
                "amount": None, "counterparty": None,
                "reference_value": "high", "reference_rationale": "",
                "summary": "招行上线跨境支付服务并设立实验室", "evidence_snippet": "",
                "extraction_confidence": 90,
                "keywords": ["跨境清算", "跨境结算", "跨境支付", "智能投顾"],
            }],
        }, ensure_ascii=False)

        def fake_llm(prompt: str) -> str:
            return mock_reply

        art = _get_article()
        extract_cases_for_article(art, llm_call_fn=fake_llm)

        # 3 个 alias 词归一为 1 个 "跨境支付"，"智能投顾" 保留
        kws = [r["keyword"] for r in fresh_db.execute(
            "SELECT keyword FROM case_keywords ORDER BY keyword"
        ).fetchall()]
        assert kws == ["智能投顾", "跨境支付"]


class TestParseKeywordsWithNestedBrackets:
    """Strategy 2 raw_decode 修复: keywords 数组内的 ] 不再截断解析 (grok 防 bug)."""

    def test_keywords_with_brackets_in_summary(self, fresh_db):
        import json
        from moni.cases.pipeline import extract_cases_for_article

        _seed_article(fresh_db)
        # summary 含 ] 字符 + keywords 含 [智能] 形态 → 不能误截断
        mock_reply = json.dumps({
            "cases": [{
                "subject": "招商银行", "subject_raw": "招行",
                "case_date": "2026-08-07", "case_date_precision": "exact",
                "case_type": "AI", "case_action": "launch",
                "amount": None, "counterparty": None,
                "reference_value": "high", "reference_rationale": "",
                "summary": "招行数字人民币[跨境支付]案例", "evidence_snippet": "",
                "extraction_confidence": 90,
                "keywords": ["智能投顾", "财富管理"],
            }],
        }, ensure_ascii=False)

        def fake_llm(prompt: str) -> str:
            return mock_reply

        art = _get_article()
        result = extract_cases_for_article(art, llm_call_fn=fake_llm)
        assert result["status"] == "completed"
        # keywords 完整提取 (策略 2 raw_decode 没被 summary 内 ] 截断)
        assert fresh_db.execute("SELECT COUNT(*) FROM case_keywords").fetchone()[0] == 2
