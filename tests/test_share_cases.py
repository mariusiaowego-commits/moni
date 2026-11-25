"""P2-1 案例分享管道测试 (方案 A 融合: formats / manifest / case_brief / POST /api/share/cases)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moni.cases.backfill_taxonomy import run_backfill
from moni.cases.normalize import normalize_case
from moni.cases.repository import CaseRepository
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, current_version, migrate
from moni.share import validate_manifest, write_manifest
from moni.share.artifacts import _ext_for_format
from moni.share.case_brief import render_case_brief_html
from moni.web.exceptions import BadRequestError
from moni.web.server import create_app


def test_worker_case_branch_sets_output_path_before_summary():
    """P0 回归：案例分支必须有 output_path，且排在 AI 摘要之前。

    队列文件里没有 output_path；其他格式都在各自分支内赋值。
    案例分支若晚于摘要生成，还会为来源文章触发真实 LLM 调用。
    """
    text = (
        Path(__file__).resolve().parents[1] / "scripts" / "default_worker.sh"
    ).read_text(encoding="utf-8")
    case_at = text.index('if [[ "$format" == case_* ]]')
    summary_at = text.index("v1.0.5.19: AI summary generation")
    assert case_at < summary_at
    block = text[case_at:summary_at]
    assert 'output_path="$artifacts_dir/${task_id}-case_brief_html.html"' in block


@pytest.fixture()
def share_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "share_cases.db"
    queue_dir = tmp_path / "queue"
    monkeypatch.setenv("MONI_QUEUE_DIR", str(queue_dir))
    monkeypatch.setenv("MONI_DB_PATH", str(db_file))
    set_db_path(db_file)
    migrate()
    conn = get_db()
    yield conn, db_file, queue_dir
    close_db()


def _seed_case(
    conn: sqlite3.Connection,
    aid: str,
    *,
    subject: str,
    summary: str,
    evidence: str,
    amount: str = "",
) -> str:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR IGNORE INTO articles (id, title, url, source, published_at, fetched_at, raw_content)
        VALUES (?, ?, ?, 'mpaypass', ?, ?, ?)
        """,
        (aid, f"文章-{aid}", f"https://example.com/{aid}", now, now, summary + " " + evidence),
    )
    cs = normalize_case(
        {
            "subject": subject,
            "case_type": "PI",
            "case_action": "launch",
            "amount": amount,
            "summary": summary,
            "evidence_snippet": evidence,
            "keywords": ["智能投顾"],
            "extraction_confidence": 92,
        }
    )
    assert cs is not None
    with conn:
        CaseRepository.insert_cases(conn, aid, [cs])
    return conn.execute(
        "SELECT case_id FROM cases WHERE article_id = ? ORDER BY rowid DESC LIMIT 1", (aid,)
    ).fetchone()[0]


class TestShareCasesP2:
    def test_migrations_066_067_and_format_ext(self, share_db):
        conn, _, _ = share_db
        assert _CURRENT_VERSION >= 67
        assert current_version(conn) == _CURRENT_VERSION
        st_cols = {r[1] for r in conn.execute("PRAGMA table_info(share_tasks)").fetchall()}
        assert {"case_ids", "item_kind"} <= st_cols
        assert _ext_for_format("case_brief_html") == "html"

    def test_case_brief_payload_validation_and_old_formats_intact(self):
        fmt, payload = validate_manifest(
            {
                "format": "case_brief_html",
                "version": "1.0",
                "payload": {
                    "case_ids": ["MN-AI-2608-0001"],
                    "title": "测试简报",
                    "group_by": "enterprise",
                },
            }
        )
        assert fmt == "case_brief_html"
        assert payload["include_insight"] is True
        assert payload["include_evidence"] is True
        assert payload["group_by"] == "enterprise"

        with pytest.raises(BadRequestError):
            validate_manifest(
                {
                    "format": "case_brief_html",
                    "version": "1.0",
                    "payload": {"case_ids": ["c1"], "group_by": "invalid_group"},
                }
            )

        # 旧格式不受影响
        fmt_md, pl_md = validate_manifest(
            {"format": "markdown", "version": "1.0", "payload": {"article_ids": ["a1"]}}
        )
        assert fmt_md == "markdown"
        assert pl_md["article_ids"] == ["a1"]

    def test_write_manifest_item_kind_case_and_article_default(self, share_db):
        conn, _, queue_dir = share_db
        cid1 = _seed_case(conn, "art_w1", subject="招商银行", summary="招行上线投顾", evidence="投入5亿元", amount="5亿元")
        cid2 = _seed_case(conn, "art_w1", subject="微信支付", summary="微信支付联动", evidence="日活3000万", amount="互动日活3000万")

        # 1) article 路径默认 item_kind='article', case_ids=NULL
        _, art_pl = validate_manifest({"format": "markdown", "version": "1.0", "payload": {"article_ids": ["art_w1"]}})
        sid_art = write_manifest(None, art_pl, format_name="markdown")
        row_art = conn.execute("SELECT article_ids, case_ids, item_kind FROM share_tasks WHERE id = ?", (sid_art,)).fetchone()
        assert json.loads(row_art["article_ids"]) == ["art_w1"]
        assert row_art["case_ids"] is None
        assert row_art["item_kind"] == "article"

        # 2) case 路径写 case_ids, item_kind='case', 去重 article_ids
        _, case_pl = validate_manifest({"format": "case_brief_html", "version": "1.0", "payload": {"case_ids": [cid1, cid2]}})
        sid_case = write_manifest(None, case_pl, format_name="case_brief_html")
        row_case = conn.execute("SELECT article_ids, case_ids, item_kind FROM share_tasks WHERE id = ?", (sid_case,)).fetchone()
        assert json.loads(row_case["case_ids"]) == [cid1, cid2]
        assert json.loads(row_case["article_ids"]) == ["art_w1"]
        assert row_case["item_kind"] == "case"
        assert (queue_dir / f"{sid_case}.json").is_file()

    def test_render_case_brief_html_with_metrics_evidence_and_empty_insights(self, share_db):
        conn, db_file, _ = share_db
        cid = _seed_case(
            conn,
            "art_r1",
            subject="招商银行",
            summary="招行上线智能投顾系统",
            evidence="互动日活3000万，覆盖高净值客群",
            amount="互动日活3000万",
        )
        run_backfill(db_file, apply=True)

        # case_insights 为空时优雅降级
        html_no_ins = render_case_brief_html(
            db_file, [cid], title="P2测试简报", group_by="enterprise"
        )
        assert "<!DOCTYPE html>" in html_no_ins
        assert "P2测试简报" in html_no_ins
        assert "互动日活3000万" in html_no_ins
        assert "dau" in html_no_ins
        assert "深度解读" not in html_no_ins

        # 插入一条 done insight 后应渲染
        conn.execute(
            """
            INSERT INTO case_insights (case_id, prompt_version, input_hash, status, body_json)
            VALUES (?, 'v1', 'h1', 'done', ?)
            """,
            (cid, json.dumps({"one_liner": "招行标杆落地", "why_it_matters": "具备同业可复制性"}, ensure_ascii=False)),
        )
        conn.commit()
        html_with_ins = render_case_brief_html(db_file, [cid], title="含解读简报")
        assert "深度解读" in html_with_ins
        assert "招行标杆落地" in html_with_ins

    def test_post_api_share_cases_enqueue_and_duplicate_guard(self, share_db):
        conn, db_file, _ = share_db
        cid = _seed_case(conn, "art_api1", subject="工商银行", summary="工行上线智能风控", evidence="覆盖全行")
        run_backfill(db_file, apply=True)

        client = TestClient(create_app())
        r1 = client.post("/api/share/cases", json={"case_ids": [cid], "title": "API简报"})
        assert r1.status_code == 201
        body = r1.json()
        assert body["status"] == "pending"
        assert body["format"] == "case_brief_html"
        assert body["case_count"] == 1
        assert body["article_count"] == 1

        # 重复提交同案例集被 409 拦截
        r_dup = client.post("/api/share/cases", json={"case_ids": [cid], "title": "API简报"})
        assert r_dup.status_code == 409

        # force=true 可强制再次入队; 按筛选条件也能入队
        r_force = client.post("/api/share/cases", json={"enterprise_id": "ent_icbc", "force": True})
        assert r_force.status_code == 201
        assert r_force.json()["case_count"] == 1
