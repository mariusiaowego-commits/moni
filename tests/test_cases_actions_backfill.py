"""case_action 规则回填：关键词优先级 / CLI dry-run 与 apply / 归档排除."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from moni.cases.normalize import normalize_case_action, normalize_case_action_with_source
from moni.db.connection import close_db, set_db_path
from moni.db.migrate import migrate


@pytest.fixture()
def tmp_db(tmp_path: Path):
    db_file = tmp_path / "test_actions.db"
    set_db_path(db_file)
    migrate()
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    yield conn, db_file
    conn.close()
    close_db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed(
    conn: sqlite3.Connection,
    case_id: str,
    summary: str,
    *,
    lifecycle: str = "verified",
    action: str = "launch",
    source: str = "default",
    article_id: str | None = None,
) -> None:
    article_id = article_id or f"art_{case_id}"
    conn.execute(
        "INSERT OR IGNORE INTO articles (id, title, url, source, fetched_at) VALUES (?,?,?,?,?)",
        (article_id, "t", "u", "s", _now()),
    )
    conn.execute(
        """INSERT INTO cases (case_id, article_id, case_date_precision, subject, case_type,
               case_action, case_action_source, reference_value, extraction_status,
               review_status, case_lifecycle, extraction_attempts, normalize_version,
               summary, evidence_snippet, extracted_at, created_at, updated_at)
           VALUES (?, ?, 'month', '招商银行', 'AI', ?, ?, 'high',
                   'completed', 'confirmed', ?, 1, 1, ?, '', ?, ?, ?)""",
        (case_id, article_id, action, source, lifecycle, summary, _now(), _now(), _now()),
    )
    conn.commit()


class TestNormalizeAction:
    def test_mna_maps_to_transform(self):
        assert normalize_case_action("Visa 以 24 亿美元收购反欺诈企业") == "transform"
        assert normalize_case_action("汇丰私有化恒生银行") == "transform"

    def test_exit_beats_generic_launch(self):
        assert normalize_case_action("信用卡 App 将于 11 月 30 日关停") == "exit"

    def test_more_specific_context_beats_llm_launch_enum(self):
        action, source = normalize_case_action_with_source(
            "launch", context_text="收购 AI 反欺诈公司并整合团队",
        )
        assert (action, source) == ("transform", "rule_text")

    def test_explicit_non_launch_enum_wins(self):
        action, source = normalize_case_action_with_source(
            "pilot", context_text="收购 AI 公司",
        )
        assert (action, source) == ("pilot", "llm_enum")

    def test_unmatched_falls_back_to_launch_default(self):
        assert normalize_case_action_with_source(None, context_text="白皮书披露行业现状") == (
            "launch", "default",
        )


class TestCli:
    def test_dry_run_does_not_write(self, tmp_db, capsys):
        conn, db_file = tmp_db
        _seed(conn, "MN-AI-2608-0004", "Visa 以 24 亿美元收购反欺诈企业")
        from moni.cases.backfill_actions import main
        assert main(["--db", str(db_file)]) == 0
        assert "需改动 1 条" in capsys.readouterr().out
        row = conn.execute("SELECT case_action, case_action_source FROM cases").fetchone()
        assert tuple(row) == ("launch", "default")

    def test_apply_writes_action_and_source(self, tmp_db):
        conn, db_file = tmp_db
        _seed(conn, "MN-AI-2608-0004", "Visa 以 24 亿美元收购反欺诈企业")
        _seed(conn, "MN-AI-2608-0001", "北京银行启动上线试运行")
        from moni.cases.backfill_actions import main
        assert main(["--db", str(db_file), "--apply"]) == 0
        got = dict(conn.execute("SELECT case_id, case_action FROM cases").fetchall())
        assert got == {"MN-AI-2608-0004": "transform", "MN-AI-2608-0001": "pilot"}
        sources = {r[0] for r in conn.execute("SELECT case_action_source FROM cases")}
        assert sources == {"rule_text"}

    def test_archived_excluded(self, tmp_db, capsys):
        conn, db_file = tmp_db
        _seed(conn, "MN-AI-2608-0004", "Visa 收购反欺诈企业", lifecycle="archived")
        from moni.cases.backfill_actions import main
        assert main(["--db", str(db_file), "--apply"]) == 0
        assert "无候选" in capsys.readouterr().out
        row = conn.execute("SELECT case_action FROM cases").fetchone()
        assert row[0] == "launch"

    def test_llm_enum_skipped_by_default(self, tmp_db, capsys):
        conn, db_file = tmp_db
        _seed(conn, "MN-AI-2608-0004", "Visa 收购反欺诈企业", action="pilot", source="llm_enum")
        from moni.cases.backfill_actions import main
        assert main(["--db", str(db_file), "--apply"]) == 0
        assert "跳过 llm_enum/manual 1 条" in capsys.readouterr().out
        row = conn.execute("SELECT case_action, case_action_source FROM cases").fetchone()
        assert tuple(row) == ("pilot", "llm_enum")

    def test_include_llm_overrides(self, tmp_db):
        conn, db_file = tmp_db
        _seed(conn, "MN-AI-2608-0004", "Visa 收购反欺诈企业", action="pilot", source="llm_enum")
        from moni.cases.backfill_actions import main
        assert main(["--db", str(db_file), "--apply", "--include-llm"]) == 0
        row = conn.execute("SELECT case_action FROM cases").fetchone()
        assert row[0] == "transform"

    def test_unique_conflict_skipped_not_fatal(self, tmp_db, capsys):
        conn, db_file = tmp_db
        # 同文章 + 同主体 + 同类型：一条已是 transform，另一条待改 transform
        _seed(conn, "MN-AI-2608-0004", "Visa 收购反欺诈企业", action="transform", source="rule_text")
        _seed(conn, "MN-AI-2608-0005", "Visa 收购另一家公司", article_id="art_MN-AI-2608-0004")
        from moni.cases.backfill_actions import main
        assert main(["--db", str(db_file), "--apply"]) == 0
        out = capsys.readouterr().out
        assert "冲突跳过 1 条" in out
        row = conn.execute(
            "SELECT case_action FROM cases WHERE case_id = 'MN-AI-2608-0005'"
        ).fetchone()
        assert row[0] == "launch"
