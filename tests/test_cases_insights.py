"""案例深度解读 (case_insights)：prompt 组装 / JSON 解析 / 幂等写入 / CLI 边界."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from moni.cases.insights import (
    build_prompt,
    input_hash,
    parse_insight,
    save_insight,
)
from moni.db.connection import set_db_path, get_db, close_db
import moni.db.connection as conn_mod
import moni.db.migrate as mig_mod
from moni.db.migrate import migrate


@pytest.fixture()
def tmp_db(tmp_path: Path):
    db_file = tmp_path / "test.db"
    raw = sqlite3.connect(str(db_file))
    raw.row_factory = sqlite3.Row
    set_db_path(db_file)
    migrate()
    yield raw, db_file
    raw.close()
    close_db()
    conn_mod._CON = None
    conn_mod._DB_PATH = None
    mig_mod._CON = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_case(conn: sqlite3.Connection, case_id: str = "MN-AI-2601-0001",
               *, lifecycle: str = "verified", subject: str = "招商银行") -> None:
    article_id = f"art_{case_id}"
    conn.execute(
        "INSERT OR IGNORE INTO articles (id, title, url, source, fetched_at) VALUES (?,?,?,?,?)",
        (article_id, "t", "u", "s", _now()),
    )
    conn.execute(
        "INSERT INTO cases (case_id, article_id, case_date_precision, subject, case_type, "
        "case_action, reference_value, extraction_status, review_status, case_lifecycle, "
        "extraction_attempts, normalize_version, summary, evidence_snippet, extracted_at, "
        "created_at, updated_at) VALUES (?,?, 'month', '招商银行', 'AI', 'launch', 'high', "
        "'completed', 'confirmed', ?, 1, 1, ?, ?, ?, ?, ?)",
        (case_id, article_id, lifecycle, "招行上线智能投顾", "招行公告上线智能投顾", _now(), _now(), _now()),
    )
    conn.execute("UPDATE cases SET subject = ? WHERE case_id = ?", (subject, case_id))
    conn.commit()


class TestPromptAndParse:
    def test_prompt_contains_fields_and_truncates(self):
        prompt = build_prompt({
            "subject": "招商银行",
            "counterparty": "华为",
            "industry_l1": "银行",
            "category_name": "AI与大模型应用",
            "scenario_name": "大模型与智能体",
            "amount": "投资 600 万美元",
            "summary": "x" * 2000,
            "evidence_snippet": "y" * 1000,
            "metrics": [{"metric_type": "investment", "value_num": 6000000, "unit": "USD"}],
        })
        assert "招商银行" in prompt and "华为" in prompt
        assert "investment 6000000USD" in prompt
        assert len(prompt) < 1600

    def test_parse_strips_fence_and_keeps_lists(self):
        body = parse_insight(
            '```json\n{"what":"上线智能投顾","how":["自研模型","分层服务"],'
            '"risks":["合规"],"reusable":["分层"],"fit":"零售客群"}\n```'
        )
        assert body["what"] == "上线智能投顾"
        assert body["how"] == ["自研模型", "分层服务"]
        assert body["fit"] == "零售客群"

    def test_parse_insufficient_and_invalid(self):
        assert parse_insight({"insufficient": True, "reason": "只有标题"}) == {
            "insufficient": True, "reason": "只有标题",
        }
        assert parse_insight("not json") == {}
        assert parse_insight({"how": ["x"]}) == {}

    def test_parse_ignores_trailing_cli_noise(self):
        body = parse_insight('{"what":"上线智能投顾"}\n\ntq-s-select · 2% · ~')
        assert body["what"] == "上线智能投顾"

    def test_parse_rejects_wrong_types(self):
        assert parse_insight({"what": {"a": 1}}) == {}
        assert parse_insight({"insufficient": "false", "what": "A"})["what"] == "A"
        assert parse_insight({"insufficient": "TRUE", "reason": "r"}) == {
            "insufficient": True, "reason": "r",
        }
        assert parse_insight('["x"]') == {}
        assert parse_insight("null") == {}


class TestSaveInsight:
    def test_idempotent_and_links_case(self, tmp_db):
        conn, _ = tmp_db
        _seed_case(conn)
        h = input_hash("p")
        first = save_insight(conn, "MN-AI-2601-0001", {"what": "A"}, provider="p", model="m", prompt_hash=h)
        second = save_insight(conn, "MN-AI-2601-0001", {"what": "B"}, provider="p", model="m", prompt_hash=h)
        assert first == second and first > 0
        row = conn.execute(
            "SELECT body_json FROM case_insights WHERE case_id = ?", ("MN-AI-2601-0001",)
        ).fetchone()
        assert json.loads(row[0])["what"] == "B"
        linked = conn.execute(
            "SELECT insight_id FROM cases WHERE case_id = ?", ("MN-AI-2601-0001",)
        ).fetchone()[0]
        assert linked == first

    def test_new_hash_marks_old_row_stale_and_moves_pointer(self, tmp_db):
        conn, _ = tmp_db
        _seed_case(conn)
        first = save_insight(conn, "MN-AI-2601-0001", {"what": "A"}, provider="p", model="m", prompt_hash="h1")
        second = save_insight(conn, "MN-AI-2601-0001", {"what": "B"}, provider="p", model="m", prompt_hash="h2")
        rows = conn.execute(
            "SELECT id, status FROM case_insights WHERE case_id = ? ORDER BY id", ("MN-AI-2601-0001",)
        ).fetchall()
        assert [r["status"] for r in rows] == ["stale", "done"]
        linked = conn.execute(
            "SELECT insight_id FROM cases WHERE case_id = ?", ("MN-AI-2601-0001",)
        ).fetchone()[0]
        assert first != second and linked == second

    def test_unknown_case_raises_and_writes_nothing(self, tmp_db):
        conn, _ = tmp_db
        with pytest.raises(ValueError):
            save_insight(conn, "NOPE-1", {"what": "A"}, provider="p", model="m", prompt_hash="h")
        assert conn.execute("SELECT COUNT(*) FROM case_insights").fetchone()[0] == 0

    def test_empty_body_rejected(self, tmp_db):
        conn, _ = tmp_db
        _seed_case(conn)
        with pytest.raises(ValueError):
            save_insight(conn, "MN-AI-2601-0001", {}, provider="p", model="m", prompt_hash="h")


class TestCli:
    def test_dry_run_excludes_archived_and_existing(self, tmp_db, capsys):
        conn, db_file = tmp_db
        _seed_case(conn, "MN-AI-2601-0001")
        _seed_case(conn, "MN-AI-2601-0002", lifecycle="archived")
        from moni.cases.backfill_insights import main
        assert main(["--db", str(db_file)]) == 0
        out = capsys.readouterr().out
        assert "候选 1 条" in out

    def test_apply_writes_rows(self, tmp_db, monkeypatch, capsys):
        conn, db_file = tmp_db
        _seed_case(conn)
        import moni.cases.backfill_insights as cli
        monkeypatch.setattr(cli, "generate_insight", lambda *a, **k: {"what": "W", "how": [], "risks": [], "reusable": [], "fit": ""})
        monkeypatch.setattr("moni.llm.client.resolve_active_model", lambda: ("p", "m"))
        assert cli.main(["--db", str(db_file), "--apply"]) == 0
        assert conn.execute("SELECT COUNT(*) FROM case_insights").fetchone()[0] == 1
        assert "apply" in capsys.readouterr().out

    def test_dry_run_excludes_processing_placeholder(self, tmp_db, capsys):
        conn, db_file = tmp_db
        _seed_case(conn, "MN-AI-2601-0001")
        _seed_case(conn, "MN-AI-2601-0003", subject="__processing__")
        from moni.cases.backfill_insights import main
        assert main(["--db", str(db_file)]) == 0
        assert "候选 1 条" in capsys.readouterr().out

    def test_with_article_reruns_only_new_hash(self, tmp_db, monkeypatch):
        conn, db_file = tmp_db
        _seed_case(conn)
        import moni.cases.backfill_insights as cli
        monkeypatch.setattr("moni.llm.client.resolve_active_model", lambda: ("p", "m"))
        body = {"what": "W", "how": [], "risks": [], "reusable": [], "fit": ""}
        monkeypatch.setattr(cli, "generate_insight", lambda *a, **k: body)
        assert cli.main(["--db", str(db_file), "--apply"]) == 0
        assert cli.main(["--db", str(db_file), "--apply"]) == 0
        assert conn.execute("SELECT COUNT(*) FROM case_insights").fetchone()[0] == 1
        assert cli.main(["--db", str(db_file), "--apply", "--with-article"]) == 0
        rows = conn.execute("SELECT id, status FROM case_insights ORDER BY id").fetchall()
        assert [r["status"] for r in rows] == ["stale", "done"]

    def test_failure_records_row_and_retries(self, tmp_db, monkeypatch, capsys):
        conn, db_file = tmp_db
        _seed_case(conn)
        import moni.cases.backfill_insights as cli
        monkeypatch.setattr("moni.llm.client.resolve_active_model", lambda: ("p", "m"))

        def boom(*_a, **_k):
            raise RuntimeError("timeout boom")

        monkeypatch.setattr(cli, "generate_insight", boom)
        assert cli.main(["--db", str(db_file), "--apply"]) == 1
        row = conn.execute("SELECT status, error FROM case_insights").fetchone()
        assert row["status"] == "failed" and "timeout boom" in row["error"]

        monkeypatch.setattr(
            cli, "generate_insight",
            lambda *a, **k: {"what": "W", "how": [], "risks": [], "reusable": [], "fit": ""},
        )
        assert cli.main(["--db", str(db_file), "--apply"]) == 0
        row = conn.execute("SELECT status, error FROM case_insights").fetchone()
        assert row["status"] == "done" and row["error"] is None

    def test_limit_must_be_positive(self, tmp_db):
        _, db_file = tmp_db
        from moni.cases.backfill_insights import main
        with pytest.raises(SystemExit):
            main(["--db", str(db_file), "--limit", "0"])

    def test_unknown_ids_message(self, tmp_db, capsys):
        _, db_file = tmp_db
        from moni.cases.backfill_insights import main
        assert main(["--db", str(db_file), "--ids", "NOPE-1"]) == 0
        assert "未匹配到候选" in capsys.readouterr().out
