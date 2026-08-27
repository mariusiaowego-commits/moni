"""tests/test_cli_extract_geo.py — 地理回填 CLI 安全边界 + 规则 0 token."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from moni.db.connection import set_db_path, get_db, close_db
import moni.db.connection as conn_mod
import moni.db.migrate as mig_mod
from moni.db.migrate import migrate


@pytest.fixture(autouse=True)
def fresh_db(tmp_path: Path):
    set_db_path(tmp_path / "test.db")
    migrate()
    yield get_db()
    close_db()
    conn_mod._CON = None
    conn_mod._DB_PATH = None
    mig_mod._CON = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_article(conn, article_id: str, source: str = "src_a") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO articles (id, title, url, source, fetched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (article_id, f"t-{article_id}", f"u-{article_id}", source, _now()),
    )


def _seed_case(conn, *, case_id: str, article_id: str, subject: str) -> None:
    now = _now()
    conn.execute(
        "INSERT INTO cases "
        "(case_id, article_id, case_date_precision, subject, case_type, case_action, "
        "reference_value, extraction_status, review_status, case_lifecycle, "
        "extraction_attempts, extraction_confidence, normalize_version, "
        "extracted_at, created_at, updated_at) "
        "VALUES (?, ?, 'month', ?, 'AI', 'launch', 'medium', 'completed', 'pending', "
        "'draft', 1, 90, 1, ?, ?, ?)",
        (case_id, article_id, subject, now, now, now),
    )


def _seed_geo(conn, case_id: str, code: str = "US", name: str = "美国", region: str = "NAM") -> None:
    conn.execute(
        """INSERT INTO case_geography
               (case_id, country_code, country_name, region, is_primary, source_type, created_at)
           VALUES (?, ?, ?, ?, 1, 'auto_rule', ?)""",
        (case_id, code, name, region, _now()),
    )


def _geos(conn, case_id: str):
    return [
        (r["country_code"], r["source_type"])
        for r in conn.execute(
            "SELECT country_code, source_type FROM case_geography WHERE case_id = ?",
            (case_id,),
        ).fetchall()
    ]


def _args(**kw):
    base = dict(source=None, limit=None, dry_run=False, overwrite=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_default_dry_run_no_write(fresh_db, monkeypatch):
    conn = fresh_db
    _seed_article(conn, "art_d")
    _seed_case(conn, case_id="C-D", article_id="art_d", subject="某未知科技公司XYZ")
    conn.commit()

    def boom(*a, **k):
        raise AssertionError("dry-run 不应调 LLM")

    import moni.cli.extract_geo as mod
    monkeypatch.setattr(mod, "call_llm", boom)
    from moni.cli.extract_geo import run_extract_geo

    rc = run_extract_geo(_args(dry_run=True))
    assert rc == 0
    assert conn.execute("SELECT COUNT(*) FROM case_geography").fetchone()[0] == 0


def test_overwrite_only_affects_candidates(fresh_db, monkeypatch):
    conn = fresh_db
    _seed_article(conn, "art_a", "src_a")
    _seed_article(conn, "art_b", "src_b")
    _seed_case(conn, case_id="C-A", article_id="art_a", subject="某未知科技公司AAA")
    _seed_case(conn, case_id="C-B", article_id="art_b", subject="某未知科技公司BBB")
    _seed_geo(conn, "C-A", "JP", "日本", "APAC")
    _seed_geo(conn, "C-B", "DE", "德国", "EMEA")
    conn.commit()

    import moni.cli.extract_geo as mod
    monkeypatch.setattr(mod, "resolve_active_model", lambda: ("mock", "mock"))
    monkeypatch.setattr(mod, "call_llm", lambda *a, **k: '{"country": "美国"}')
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    from moni.cli.extract_geo import run_extract_geo

    rc = run_extract_geo(_args(overwrite=True, source="src_a"))
    assert rc == 0
    assert _geos(conn, "C-A") == [("US", "auto_llm")]
    assert _geos(conn, "C-B") == [("DE", "auto_rule")]


def test_dry_run_with_overwrite_does_not_delete(fresh_db, monkeypatch):
    conn = fresh_db
    _seed_article(conn, "art_od")
    _seed_case(conn, case_id="C-OD", article_id="art_od", subject="某未知科技公司OD")
    _seed_geo(conn, "C-OD", "FR", "法国", "EMEA")
    conn.commit()

    import moni.cli.extract_geo as mod
    monkeypatch.setattr(mod, "call_llm", lambda *a, **k: '{"country": "美国"}')
    from moni.cli.extract_geo import run_extract_geo

    rc = run_extract_geo(_args(overwrite=True, dry_run=True))
    assert rc == 0
    assert _geos(conn, "C-OD") == [("FR", "auto_rule")]


def test_subject_rule_zero_token(fresh_db, monkeypatch):
    """招商银行走规则库, 不调 LLM."""
    conn = fresh_db
    _seed_article(conn, "art_r")
    _seed_case(conn, case_id="C-R", article_id="art_r", subject="招商银行")
    conn.commit()

    def boom(*a, **k):
        raise AssertionError("规则命中不应调 LLM")

    import moni.cli.extract_geo as mod
    monkeypatch.setattr(mod, "call_llm", boom)
    monkeypatch.setattr(mod, "resolve_active_model", lambda: (_ for _ in ()).throw(AssertionError("no llm")))
    from moni.cli.extract_geo import run_extract_geo

    rc = run_extract_geo(_args())
    assert rc == 0
    assert _geos(conn, "C-R") == [("CN", "auto_rule")]
