"""Store-level tests for the research guidance (sprint-73).

The store owns the invalidation rule: changing the guidance expires the
passed research, exactly like changing the label or the brief does.
Toggling ``enabled`` must not -- a disabled direction keeps its research
and becomes usable again when re-enabled.
"""
from __future__ import annotations

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.scoring import direction_store
from moni.scoring.direction import STATUS_EXPIRED, STATUS_PASSED
from tests.test_scoring_direction import _payload


@pytest.fixture
def db(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    yield conn
    close_db()


def _direction(db, label="微信生态", brief="微信里的银行动作", guidance=""):
    direction_id, error = direction_store.create_direction(
        db, label, brief, guidance
    )
    assert error is None and direction_id is not None
    return direction_id


def _pass_research(db, direction_id):
    research, error = direction_store.start_research(db, direction_id)
    assert error is None and research is not None
    direction_store.finish_research_success(
        db,
        direction_id=direction_id,
        research_version=research["research_version"],
        raw_research=_payload(),
        model="test",
    )
    return research


def test_guidance_is_stored_and_read_back(db):
    direction_id = _direction(db, guidance="重点看投放数据")
    row = direction_store.get_direction(db, direction_id)
    assert row["research_guidance"] == "重点看投放数据"
    listed = direction_store.list_directions(db)
    assert listed[0]["research_guidance"] == "重点看投放数据"


def test_over_long_guidance_is_rejected_on_create_and_update(db):
    _, error = direction_store.create_direction(db, "新方向", "说明", "好" * 801)
    assert error and "800" in error

    direction_id = _direction(db)
    _, error = direction_store.update_direction(
        db, direction_id, research_guidance="好" * 801
    )
    assert error and "800" in error


def test_changing_guidance_expires_the_passed_research(db):
    direction_id = _direction(db, guidance="旧要求")
    _pass_research(db, direction_id)

    updated, error = direction_store.update_direction(
        db, direction_id, research_guidance="新要求"
    )
    assert error is None and updated is not None
    assert updated["research_guidance"] == "新要求"

    rows = direction_store.latest_research_rows(db, direction_id)
    assert rows[0]["status"] == STATUS_EXPIRED

    usable, usable_error = direction_store.direction_usable(db, updated)
    assert usable is None
    # update_direction marks the passed row expired before the usable gate
    # runs, so the gate stops on status. The snapshot mismatch wording is
    # covered by test_scoring_direction_guidance.py.
    assert usable_error and "expired" in usable_error


def test_toggling_enabled_keeps_the_research(db):
    direction_id = _direction(db, guidance="重点看投放数据")
    _pass_research(db, direction_id)

    updated, error = direction_store.update_direction(db, direction_id, enabled=False)
    assert error is None and updated is not None
    rows = direction_store.latest_research_rows(db, direction_id)
    assert rows[0]["status"] == STATUS_PASSED

    usable, _ = direction_store.direction_usable(db, updated)
    assert usable is not None


def test_saving_the_same_guidance_keeps_the_research(db):
    direction_id = _direction(db, guidance="重点看投放数据")
    _pass_research(db, direction_id)

    updated, error = direction_store.update_direction(
        db, direction_id, research_guidance="重点看投放数据"
    )
    assert error is None and updated is not None
    rows = direction_store.latest_research_rows(db, direction_id)
    assert rows[0]["status"] == STATUS_PASSED


def test_research_snapshots_the_guidance_it_ran_with(db):
    direction_id = _direction(db, guidance="重点看投放数据")
    research, error = direction_store.start_research(db, direction_id)
    assert error is None and research is not None
    assert research["guidance_snapshot"] == "重点看投放数据"
    rows = direction_store.latest_research_rows(db, direction_id)
    assert rows[0]["guidance_snapshot"] == "重点看投放数据"


def test_an_empty_guidance_keeps_the_research_usable(db):
    """The live direction has no guidance yet and must stay usable."""
    direction_id = _direction(db)
    _pass_research(db, direction_id)
    row = direction_store.get_direction(db, direction_id)
    usable, usable_error = direction_store.direction_usable(db, row)
    assert usable_error is None
    assert usable is not None
    assert usable.version == 1
