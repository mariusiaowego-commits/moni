"""The ``sources`` table switches outrank ``config/sources.yaml`` (v1.0.123).

Failure modes pinned here:

1. A source the user switched off (``is_active = 0``) still ran in the
   scheduled batch — the UI switch was documented but never consulted.
2. A source hidden in the management page (``is_hidden = 1``) still ran.
3. A source with no row in ``sources`` is unmanaged and must keep running
   (the gate must not silently drop it).
4. A broken DB must fail open — never stop the whole sweep.
"""
from __future__ import annotations

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.fetch.source_gate import (
    REASON_HIDDEN,
    REASON_INACTIVE,
    load_source_gates,
    split_fetchable,
)


@pytest.fixture
def fresh_db(tmp_path):
    set_db_path(tmp_path / "gate.db")
    migrate()
    yield
    close_db()


def _add_source(name: str, *, is_active: int = 1, is_hidden: int = 0) -> None:
    conn = get_db()
    conn.execute(
        """
        INSERT INTO sources (
            id, name, source_type, url, is_active, is_hidden, created_at, updated_at
        )
        VALUES (?, ?, 'rss', '', ?, ?, '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00')
        """,
        (f"src-{name}", name, is_active, is_hidden),
    )
    conn.commit()


def _cfgs(*names: str) -> list[dict]:
    return [{"source_name": name} for name in names]


def test_inactive_source_is_skipped(fresh_db):
    _add_source("off_a", is_active=0)
    fetchable, skipped = split_fetchable(_cfgs("off_a"))
    assert fetchable == []
    assert skipped == [("off_a", REASON_INACTIVE)]


def test_hidden_source_is_skipped(fresh_db):
    _add_source("hidden_a", is_hidden=1)
    fetchable, skipped = split_fetchable(_cfgs("hidden_a"))
    assert fetchable == []
    assert skipped == [("hidden_a", REASON_HIDDEN)]


def test_active_visible_source_is_kept(fresh_db):
    _add_source("good_a")
    fetchable, skipped = split_fetchable(_cfgs("good_a"))
    assert [c["source_name"] for c in fetchable] == ["good_a"]
    assert skipped == []


def test_source_without_db_row_stays_fetchable(fresh_db):
    fetchable, skipped = split_fetchable(_cfgs("unmanaged"))
    assert [c["source_name"] for c in fetchable] == ["unmanaged"]
    assert skipped == []


def test_inactive_wins_over_hidden(fresh_db):
    """Both switches off — report the data-plane reason."""
    _add_source("both_off", is_active=0, is_hidden=1)
    gates = load_source_gates(["both_off"])
    assert gates["both_off"].allowed is False
    assert gates["both_off"].reason == REASON_INACTIVE


def test_broken_db_fails_open(fresh_db, monkeypatch):
    import moni.fetch.source_gate as gate_mod

    def _boom():
        raise RuntimeError("db is gone")

    monkeypatch.setattr(gate_mod, "get_db", _boom)
    fetchable, skipped = split_fetchable(_cfgs("whatever"))
    assert [c["source_name"] for c in fetchable] == ["whatever"]
    assert skipped == []


def test_batch_sources_respects_switches(fresh_db, tmp_path, monkeypatch):
    """End-to-end through the YAML scheduler: a hidden source never reaches
    the batch list."""
    from moni.fetch.scheduler import Scheduler

    config = tmp_path / "sources.yaml"
    config.write_text(
        "sources:\n"
        "- source_name: good_a\n"
        "  enabled: true\n"
        "- source_name: hidden_a\n"
        "  enabled: true\n",
        encoding="utf-8",
    )
    _add_source("hidden_a", is_hidden=1)

    fetchable, skipped = split_fetchable(
        list(Scheduler(config_path=config).batch_sources())
    )
    assert [c["source_name"] for c in fetchable] == ["good_a"]
    assert skipped == [("hidden_a", REASON_HIDDEN)]
