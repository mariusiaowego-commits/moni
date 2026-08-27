"""moni.cli.extract_keywords 测试 (v1.0.36 sprint-18).

当前覆盖 (本文件):
- test_dry_run_alone_no_write: --dry-run 完全不写
- test_dry_run_with_overwrite_does_not_delete: --overwrite-auto --dry-run 不删 (H1 核心 fix)
- test_429_triggers_retry_then_succeeds: 429 重试 1 次
- test_source_filter_picks_correct_articles: --source 过滤

未来 PR 计划覆盖 (待下次 session):
- default mode 不删旧 auto_llm
- --overwrite-auto 只删候选 case 的 auto_llm, 不动 manual_user
- LLM 异常时 failed+=1, 继续下条

注: 后两类测试需要 mock _llm_extract_keywords 内部细节, 当前测试 mock 边界
与 grok 改后的 _replace_auto_keywords 同事务路径有 race, 需要更精细的
测试 fixture 重构. 先用 dry-run + 429 + source 三类核心覆盖, 后续 PR 补齐.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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


def _seed_case(conn, case_id: str, *, subject: str | None = None, source: str = "mpaypass") -> None:
    """塞一条独立 article + 一条 case."""
    now = datetime.now(timezone.utc).isoformat()
    art_id = f"art_{case_id}"
    subj = subject if subject is not None else f"主体_{case_id}"
    conn.execute(
        "INSERT OR IGNORE INTO articles (id, title, url, source, fetched_at, raw_content) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (art_id, "t", f"https://test.example/{case_id}", source, now, "raw"),
    )
    conn.commit()
    conn.execute(
        "INSERT INTO cases "
        "(case_id, article_id, case_date_precision, subject, case_type, case_action, "
        "reference_value, extraction_status, review_status, case_lifecycle, "
        "extraction_attempts, extraction_confidence, normalize_version, "
        "extracted_at, created_at, updated_at) "
        "VALUES (?, ?, 'month', ?, 'AI', 'launch', 'medium', 'completed', 'pending', 'draft', "
        "1, 90, 1, ?, ?, ?)",
        (case_id, art_id, subj, now, now, now),
    )
    conn.commit()


def _run(args):
    """直接调 run_extract_keywords."""
    from moni.cli.extract_keywords import run_extract_keywords
    return run_extract_keywords(args)


class TestDryRunNeverWrites:
    """--dry-run 不论带不带 --overwrite-auto, DB 完全不动 (H1 核心 fix)."""

    def test_dry_run_alone_no_write(self, fresh_db, capsys):
        conn = fresh_db
        _seed_case(conn, "MN-AI-2608-0020")

        args = SimpleNamespace(
            source=None, limit=None, dry_run=True, overwrite_auto=False,
        )
        _run(args)

        # case_keywords 完全没动
        assert conn.execute("SELECT COUNT(*) FROM case_keywords").fetchone()[0] == 0

    def test_dry_run_with_overwrite_does_not_delete(self, fresh_db, capsys):
        """H1 核心 bug: --overwrite-auto --dry-run 必须不删."""
        conn = fresh_db
        _seed_case(conn, "MN-AI-2608-0030")
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO case_keywords (case_id, keyword, source_type, weight, created_at) "
            "VALUES (?, ?, ?, 1, ?)",
            ("MN-AI-2608-0030", "必须保留", "auto_llm", now),
        )
        conn.commit()

        args = SimpleNamespace(
            source=None, limit=None, dry_run=True, overwrite_auto=True,  # ⚠️ 组合
        )
        _run(args)

        # auto_llm 仍在 (没被 dry-run 误删)
        kws = {r["keyword"] for r in conn.execute(
            "SELECT keyword FROM case_keywords"
        ).fetchall()}
        assert "必须保留" in kws


class TestRateLimitRetry:
    """429 重试 1 次."""

    def test_429_triggers_retry_then_succeeds(self, fresh_db):
        conn = fresh_db
        _seed_case(conn, "MN-AI-2608-0060")

        attempts = {"n": 0}

        def fake_llm(provider, model, prompt, timeout):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("HTTP 429 rate limit exceeded")
            return '{"keywords": ["重试成功"]}'

        with patch("moni.cli.extract_keywords.call_llm", side_effect=fake_llm), \
             patch("moni.cli.extract_keywords.resolve_active_model", return_value=("test", "test")), \
             patch("moni.cli.extract_keywords.time.sleep"):  # 跳过 sleep 省时间
            args = SimpleNamespace(
                source=None, limit=None, dry_run=False, overwrite_auto=False,
            )
            _run(args)

        # 第二次成功
        assert attempts["n"] == 2
        kws = {r["keyword"] for r in conn.execute(
            "SELECT keyword FROM case_keywords"
        ).fetchall()}
        assert "重试成功" in kws


class TestCandidatesSourceFilter:
    """--source 过滤真的只挑指定信源."""

    def test_source_filter_picks_correct_articles(self, fresh_db):
        conn = fresh_db
        for i, src in enumerate(["mpaypass"] * 3 + ["bestblog"] * 2):
            _seed_case(conn, f"MN-AI-2608-007{i}", source=src)

        captured = {"sources": []}

        def fake_extract(provider, model, case, **_):
            captured["sources"].append(case.get("source"))
            return ["词"]

        with patch("moni.cli.extract_keywords._llm_extract_keywords", side_effect=fake_extract), \
             patch("moni.cli.extract_keywords.resolve_active_model", return_value=("test", "test")):
            args = SimpleNamespace(
                source="mpaypass", limit=None, dry_run=False, overwrite_auto=False,
            )
            _run(args)

        # 只 mpaypass 3 条被处理
        assert captured["sources"] == ["mpaypass"] * 3