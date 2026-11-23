"""Tests for the S2 direction store and research state machine."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.scoring import direction_store
from moni.scoring.direction import (
    DIRECTION_PROMPT_ID,
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_RUNNING,
)
from tests.test_scoring_direction import _payload

NOW = datetime(2026, 11, 23, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    yield conn
    close_db()


def _direction(db, label="微信生态", brief="微信里的银行动作"):
    direction_id, error = direction_store.create_direction(db, label, brief)
    assert error is None and direction_id is not None
    return direction_id


def test_create_rejects_bad_label_and_over_long_brief(db):
    _, error = direction_store.create_direction(db, "", "说明")
    assert error and "方向名" in error

    _, error = direction_store.create_direction(db, "微信生态", "长" * 201)
    assert error and "200" in error


def test_update_expires_passed_research_when_label_or_brief_changes(db):
    direction_id = _direction(db)
    research, error = direction_store.start_research(db, direction_id)
    assert error is None and research is not None
    direction_store.finish_research_success(
        db,
        direction_id=direction_id,
        research_version=research["research_version"],
        raw_research=_payload(),
        model="test",
    )

    updated, error = direction_store.update_direction(
        db, direction_id, brief="公众号和小程序里的银行动作"
    )
    assert error is None and updated is not None
    row = db.execute(
        "SELECT status FROM bv_direction_research WHERE direction_id=?",
        (direction_id,),
    ).fetchone()
    assert row[0] == STATUS_EXPIRED


def test_update_enabled_keeps_passed_research(db):
    direction_id = _direction(db)
    research, _ = direction_store.start_research(db, direction_id)
    direction_store.finish_research_success(
        db,
        direction_id=direction_id,
        research_version=research["research_version"],
        raw_research=_payload(),
        model="test",
    )

    updated, error = direction_store.update_direction(db, direction_id, enabled=False)
    assert error is None and updated is not None
    status = db.execute(
        "SELECT status FROM bv_direction_research WHERE direction_id=?",
        (direction_id,),
    ).fetchone()[0]
    assert status == STATUS_PASSED


def test_start_research_is_versioned_and_expires_old_passed(db):
    direction_id = _direction(db)
    first, _ = direction_store.start_research(db, direction_id)
    direction_store.finish_research_success(
        db,
        direction_id=direction_id,
        research_version=first["research_version"],
        raw_research=_payload(),
        model="test",
    )

    second, error = direction_store.start_research(db, direction_id)
    assert error is None
    assert second["research_version"] == first["research_version"] + 1

    rows = db.execute(
        "SELECT research_version, status FROM bv_direction_research "
        "WHERE direction_id=? ORDER BY research_version",
        (direction_id,),
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [
        (first["research_version"], STATUS_EXPIRED),
        (second["research_version"], STATUS_RUNNING),
    ]


def test_start_research_rejects_concurrent_running(db):
    direction_id = _direction(db)
    first, _ = direction_store.start_research(db, direction_id)
    second, error = direction_store.start_research(db, direction_id)
    assert second is None
    assert error and "运行" in error


def test_recover_stale_running_marks_old_row_failed(db):
    direction_id = _direction(db)
    research, _ = direction_store.start_research(db, direction_id)

    old = NOW - timedelta(seconds=direction_store.RESEARCH_STALE_AFTER_SEC + 1)
    db.execute(
        "UPDATE bv_direction_research SET started_at=? WHERE direction_id=?",
        (old.isoformat(), direction_id),
    )
    db.commit()

    recovered = direction_store.recover_stale_running(db, now=NOW)
    assert recovered == 1
    row = db.execute(
        "SELECT status, error FROM bv_direction_research WHERE direction_id=?",
        (direction_id,),
    ).fetchone()
    assert row[0] == STATUS_FAILED
    assert "中断" in row[1]


def test_recover_stale_running_keeps_recent_row(db):
    direction_id = _direction(db)
    direction_store.start_research(db, direction_id)
    recent = NOW - timedelta(seconds=30)
    db.execute(
        "UPDATE bv_direction_research SET started_at=? WHERE direction_id=?",
        (recent.isoformat(), direction_id),
    )
    db.commit()

    assert direction_store.recover_stale_running(db, now=NOW) == 0
    status = db.execute(
        "SELECT status FROM bv_direction_research WHERE direction_id=?",
        (direction_id,),
    ).fetchone()[0]
    assert status == STATUS_RUNNING


def test_finish_success_stores_validated_payload(db):
    direction_id = _direction(db)
    research, _ = direction_store.start_research(db, direction_id)
    ok, error = direction_store.finish_research_success(
        db,
        direction_id=direction_id,
        research_version=research["research_version"],
        raw_research=_payload(),
        model="test-model",
    )
    assert ok and error is None
    row = db.execute(
        "SELECT status, scope, out_of_scope, rubric_json, model "
        "FROM bv_direction_research WHERE direction_id=?",
        (direction_id,),
    ).fetchone()
    assert row[0] == STATUS_PASSED
    assert row[1] and row[2]
    assert row[4] == "test-model"
    payload = json.loads(row[3])
    assert payload["prompt_id"] == DIRECTION_PROMPT_ID


def test_finish_failure_marks_research_failed(db):
    direction_id = _direction(db)
    research, _ = direction_store.start_research(db, direction_id)
    direction_store.finish_research_failure(
        db,
        direction_id=direction_id,
        research_version=research["research_version"],
        error="模型超时",
    )
    row = db.execute(
        "SELECT status, error FROM bv_direction_research WHERE direction_id=?",
        (direction_id,),
    ).fetchone()
    assert row[0] == STATUS_FAILED
    assert "模型超时" in row[1]


def test_invalid_research_output_is_rejected_and_failed(db):
    direction_id = _direction(db)
    research, _ = direction_store.start_research(db, direction_id)
    ok, error = direction_store.finish_research_success(
        db,
        direction_id=direction_id,
        research_version=research["research_version"],
        raw_research="这不是 JSON",
        model="test-model",
    )
    assert ok is False
    assert error and "JSON" in error
    row = db.execute(
        "SELECT status, error FROM bv_direction_research WHERE direction_id=?",
        (direction_id,),
    ).fetchone()
    assert row[0] == STATUS_FAILED
    assert "JSON" in row[1]


def test_direction_usable_requires_latest_passed(db):
    direction_id = _direction(db)
    direction = direction_store.get_direction(db, direction_id)
    research, error = direction_store.direction_usable(db, direction)
    assert research is None and error

    run, _ = direction_store.start_research(db, direction_id)
    direction_store.finish_research_failure(
        db,
        direction_id=direction_id,
        research_version=run["research_version"],
        error="模型超时",
    )
    research, error = direction_store.direction_usable(db, direction)
    assert research is None and "failed" in error

    direction_store.finish_research_success(
        db,
        direction_id=direction_id,
        research_version=run["research_version"],
        raw_research=_payload(),
        model="test",
    )
    # Failed rows are immutable; only the next version can become usable.
    second, _ = direction_store.start_research(db, direction_id)
    direction_store.finish_research_success(
        db,
        direction_id=direction_id,
        research_version=second["research_version"],
        raw_research=_payload(),
        model="test",
    )
    research, error = direction_store.direction_usable(db, direction)
    assert error is None and research is not None
    assert research.version == second["research_version"]


# ── S2 audit regression cases ────────────────────────────────────────


def test_update_same_label_and_brief_keeps_passed_research(db):
    """Toggling enabled through the edit dialog must not expire research."""
    direction_id = _direction(db)
    research, _ = direction_store.start_research(db, direction_id)
    direction_store.finish_research_success(
        db,
        direction_id=direction_id,
        research_version=research["research_version"],
        raw_research=_payload(),
        model="test",
    )

    direction = direction_store.get_direction(db, direction_id)
    updated, error = direction_store.update_direction(
        db,
        direction_id,
        label=direction["label"],
        brief=direction["brief"],
        enabled=False,
    )
    assert error is None and updated is not None
    status = db.execute(
        "SELECT status FROM bv_direction_research WHERE direction_id=?",
        (direction_id,),
    ).fetchone()[0]
    assert status == STATUS_PASSED


def test_concurrent_start_research_yields_conflict_not_integrity_error(db):
    """Two racing starts must produce one run, not a UNIQUE-crash 500."""
    import threading

    direction_id = _direction(db)
    barrier = threading.Barrier(2)
    results: list[tuple[dict | None, str | None]] = []
    errors: list[BaseException] = []

    def start() -> None:
        from moni.db.connection import get_db

        try:
            thread_conn = get_db()
            barrier.wait()
            results.append(direction_store.start_research(thread_conn, direction_id))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=start) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    started = [result for result, error in results if error is None]
    conflicts = [error for result, error in results if result is None]
    assert len(started) == 1
    assert len(conflicts) == 1
    assert "运行" in conflicts[0]


def test_late_finish_after_stale_recovery_is_rejected(db):
    direction_id = _direction(db)
    research, _ = direction_store.start_research(db, direction_id)

    old = NOW - timedelta(seconds=direction_store.RESEARCH_STALE_AFTER_SEC + 1)
    db.execute(
        "UPDATE bv_direction_research SET started_at=? WHERE direction_id=?",
        (old.isoformat(), direction_id),
    )
    db.commit()
    assert direction_store.recover_stale_running(db, now=NOW) == 1

    ok, error = direction_store.finish_research_success(
        db,
        direction_id=direction_id,
        research_version=research["research_version"],
        raw_research=_payload(),
        model="late-model",
    )
    assert ok is False
    assert error and "结束" in error
