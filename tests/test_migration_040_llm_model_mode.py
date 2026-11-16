"""Migration 040: ``llm_settings.llm_model_mode`` 默认 ``profile``.

``profile`` 让 call_llm / worker 不传 --provider/-m，跟随 moni profile
（即 magpie 路由组）。迁移只补默认值，不覆盖用户已设的 ``explicit``。
"""
from __future__ import annotations

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, migrate


def _mode(conn) -> str | None:
    row = conn.execute(
        "SELECT value FROM llm_settings WHERE key = 'llm_model_mode'"
    ).fetchone()
    return row["value"] if row else None


@pytest.fixture
def v39_db(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    conn.execute("DELETE FROM llm_settings WHERE key = 'llm_model_mode'")
    conn.execute("DELETE FROM schema_version WHERE version >= 40")
    conn.commit()
    yield conn
    close_db()


def test_040_sets_profile_by_default(v39_db):
    assert _mode(v39_db) is None
    migrate()
    assert _CURRENT_VERSION == 42
    assert _mode(get_db()) == "profile"


def test_040_keeps_existing_explicit(v39_db):
    v39_db.execute(
        "INSERT INTO llm_settings (key, value) VALUES ('llm_model_mode', 'explicit')"
    )
    v39_db.commit()
    migrate()
    assert _mode(get_db()) == "explicit"
