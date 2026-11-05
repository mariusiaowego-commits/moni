"""Regression tests for migration 038 (ai-fintech += CVPR / ECCV).

v1.0.64 added ASCII word boundaries. That killed real false positives
(``MA`` inside MASAJIN, ``ark`` inside Markdown) but also stopped ``CV``
from matching CVPR/ECCV paper titles — ``CV`` is a deliberate ai-fintech
keyword meaning "computer vision", and the conference names are exactly
what it is there for.

038 appends the two conference acronyms. Measured on the 2026-10-25
snapshot: 26 articles restored, 0 previously-rejected articles added.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, _run_sql_file, migrate
from moni.db.seed_config import seed

MIGRATION_038 = (
    Path(__file__).resolve().parents[1]
    / "moni" / "db" / "migrations" / "038_ai_fintech_cv_acronyms.sql"
)

EXISTING = [
    "人工智能", "生成式AI", "AIGC", "大语言模型", "计算机视觉", "CV",
    "自然语言处理", "NLP",
]


@pytest.fixture
def db_with_ai_fintech(tmp_path):
    """Fresh DB whose ai-fintech keywords are a small known list."""
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    seed()  # tag_definitions rows come from seed, not from the migrations
    conn = get_db()
    conn.execute(
        "UPDATE tag_definitions SET keywords = ? WHERE name = 'ai-fintech'",
        (json.dumps(EXISTING, ensure_ascii=False),),
    )
    conn.commit()
    yield conn
    close_db()


def _keywords(conn) -> list[str]:
    raw = conn.execute(
        "SELECT keywords FROM tag_definitions WHERE name = 'ai-fintech'"
    ).fetchone()[0]
    return json.loads(raw)


def test_038_appends_the_two_conference_acronyms(db_with_ai_fintech):
    assert _CURRENT_VERSION == 39
    _run_sql_file(db_with_ai_fintech, MIGRATION_038)
    kws = _keywords(db_with_ai_fintech)
    assert kws[-2:] == ["CVPR", "ECCV"]
    assert len(kws) == len(EXISTING) + 2


def test_038_preserves_the_existing_order(db_with_ai_fintech):
    """Appending must not reorder user-managed data.

    The first draft used UNION, which sorts — the list came back as
    ``AI Agent, AI Copilot, AIGC, ...`` instead of the curated order the
    user built in /config.  That is a silent data rewrite even though the
    set of keywords is unchanged.
    """
    _run_sql_file(db_with_ai_fintech, MIGRATION_038)
    kws = _keywords(db_with_ai_fintech)
    assert kws[: len(EXISTING)] == EXISTING


def test_038_is_idempotent(db_with_ai_fintech):
    _run_sql_file(db_with_ai_fintech, MIGRATION_038)
    once = _keywords(db_with_ai_fintech)
    _run_sql_file(db_with_ai_fintech, MIGRATION_038)
    assert _keywords(db_with_ai_fintech) == once
    assert once.count("CVPR") == 1
    assert once.count("ECCV") == 1


def test_038_keeps_the_bare_cv_keyword(db_with_ai_fintech):
    """CVPR/ECCV are additions, not a replacement for CV."""
    _run_sql_file(db_with_ai_fintech, MIGRATION_038)
    assert "CV" in _keywords(db_with_ai_fintech)


def test_038_skips_a_tag_with_null_keywords(tmp_path):
    """A tag with no keyword list must stay untouched.

    Observable contract: ``keywords`` is still NULL afterwards — not ``[]``
    and not ``[] + 2``.  (The NULL survives because ``json_insert(NULL, ...)``
    propagates NULL; the ``WHERE keywords IS NOT NULL`` clause is belt and
    braces, not the thing being pinned here.)
    """
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    seed()
    conn = get_db()
    conn.execute(
        "UPDATE tag_definitions SET keywords = NULL WHERE name = 'ai-fintech'"
    )
    conn.commit()
    _run_sql_file(conn, MIGRATION_038)
    raw = conn.execute(
        "SELECT keywords FROM tag_definitions WHERE name = 'ai-fintech'"
    ).fetchone()[0]
    assert raw is None
    close_db()


def test_038_skips_a_tag_with_malformed_json_keywords(tmp_path):
    """A row whose ``keywords`` is not valid JSON must not abort the migration.

    ``json_valid(keywords)`` in the WHERE clause is the load-bearing guard
    here: without it SQLite raises ``malformed JSON`` and the whole migration
    dies, so no other tag ever gets its keywords.
    """
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    seed()
    conn = get_db()
    conn.execute(
        "UPDATE tag_definitions SET keywords = 'not-json' WHERE name = 'ai-fintech'"
    )
    conn.commit()

    _run_sql_file(conn, MIGRATION_038)  # must not raise

    raw = conn.execute(
        "SELECT keywords FROM tag_definitions WHERE name = 'ai-fintech'"
    ).fetchone()[0]
    assert raw == "not-json"
    close_db()


def test_038_logs_the_cleanup(db_with_ai_fintech):
    _run_sql_file(db_with_ai_fintech, MIGRATION_038)
    row = db_with_ai_fintech.execute(
        "SELECT migration FROM schema_cleanup_log WHERE migration = ?",
        ("038_ai_fintech_cv_acronyms",),
    ).fetchone()
    assert row is not None


def test_cvpr_titles_match_after_038():
    """The whole point: a CVPR title passes the gate once CVPR exists.

    Under v1.0.64's boundaries the bare keyword ``CV`` no longer matches
    "CVPR 2026｜..." — the acronym runs straight into the year.
    """
    from moni.fetch.keyword_match import keyword_matches

    title = "CVPR 2026｜告别「一属性一训练」，美图&北交大提出统一属性编辑框架"
    assert keyword_matches("CV", title=title, body="") is False
    assert keyword_matches("CVPR", title=title, body="") is True

    eccv = "入选ECCV 2026！清华开源空间模型打败Gemini"
    assert keyword_matches("CV", title=eccv, body="") is False
    assert keyword_matches("ECCV", title=eccv, body="") is True
