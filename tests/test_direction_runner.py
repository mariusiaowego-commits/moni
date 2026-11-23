"""Tests for the in-process direction research runner."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.scoring import direction_store
from moni.scoring.direction_runner import (
    DirectionResearchRunner,
    ResearchRequest,
    run_research,
)
from tests.test_scoring_direction import _payload


@pytest.fixture
def db(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    yield conn
    close_db()


def _direction(db):
    direction_id, error = direction_store.create_direction(db, "微信生态", "微信里的银行动作")
    assert error is None
    return direction_id


def test_research_prompt_is_label_and_brief_only():
    request = ResearchRequest(
        direction_id=1,
        research_version=1,
        label="微信生态",
        brief="微信里的银行动作",
    )
    prompt = run_research.__globals__["research_prompt"](request)
    assert "微信生态" in prompt
    assert "微信里的银行动作" in prompt
    assert "标题：" not in prompt


def test_run_research_persists_success(db):
    direction_id = _direction(db)
    research, _ = direction_store.start_research(db, direction_id)

    error = run_research(
        ResearchRequest(
            direction_id=direction_id,
            research_version=research["research_version"],
            label="微信生态",
            brief="微信里的银行动作",
        ),
        call_model=lambda _prompt: json.dumps(_payload(), ensure_ascii=False),
        model_name=lambda: "test-model",
    )

    assert error is None
    row = db.execute(
        "SELECT status, model FROM bv_direction_research WHERE direction_id=?",
        (direction_id,),
    ).fetchone()
    assert row[0] == "passed"
    assert row[1] == "test-model"


def test_run_research_persists_invalid_output_as_failed(db):
    direction_id = _direction(db)
    research, _ = direction_store.start_research(db, direction_id)

    error = run_research(
        ResearchRequest(
            direction_id=direction_id,
            research_version=research["research_version"],
            label="微信生态",
            brief="微信里的银行动作",
        ),
        call_model=lambda _prompt: "这不是 JSON",
        model_name=lambda: "test-model",
    )

    assert error is not None
    row = db.execute(
        "SELECT status, error FROM bv_direction_research WHERE direction_id=?",
        (direction_id,),
    ).fetchone()
    assert row[0] == "failed"
    assert "JSON" in row[1]


def test_run_research_persists_model_exception_as_failed(db):
    direction_id = _direction(db)
    research, _ = direction_store.start_research(db, direction_id)

    def broken(_prompt: str) -> str:
        raise RuntimeError("模型超时")

    error = run_research(
        ResearchRequest(
            direction_id=direction_id,
            research_version=research["research_version"],
            label="微信生态",
            brief="微信里的银行动作",
        ),
        call_model=broken,
        model_name=lambda: "test-model",
    )
    assert error and "模型超时" in error
    row = db.execute(
        "SELECT status, error FROM bv_direction_research WHERE direction_id=?",
        (direction_id,),
    ).fetchone()
    assert row[0] == "failed"


def test_runner_runs_jobs_serially(db):
    direction_id = _direction(db)
    research, _ = direction_store.start_research(db, direction_id)
    order: list[int] = []
    lock = threading.Lock()

    def slow(_prompt: str) -> str:
        time.sleep(0.05)
        with lock:
            order.append(1)
        return json.dumps(_payload(), ensure_ascii=False)

    runner = DirectionResearchRunner(slow, lambda: "test")
    request = ResearchRequest(
        direction_id=direction_id,
        research_version=research["research_version"],
        label="微信生态",
        brief="微信里的银行动作",
    )
    runner.submit(request)
    runner.submit(request)
    for _ in range(100):
        if runner.active is None and runner.pending_count == 0:
            break
        time.sleep(0.02)
    assert order == [1, 1]
    assert runner.active is None
    assert runner.pending_count == 0


def test_run_research_persists_model_name_exception_as_failed(db):
    """A model that cannot even report its name must not leave a running row."""
    direction_id = _direction(db)
    research, _ = direction_store.start_research(db, direction_id)

    def broken_name() -> str:
        raise RuntimeError("模型名解析失败")

    error = run_research(
        ResearchRequest(
            direction_id=direction_id,
            research_version=research["research_version"],
            label="微信生态",
            brief="微信里的银行动作",
        ),
        call_model=lambda _prompt: json.dumps(_payload(), ensure_ascii=False),
        model_name=broken_name,
    )

    assert error is not None
    row = db.execute(
        "SELECT status, error FROM bv_direction_research WHERE direction_id=?",
        (direction_id,),
    ).fetchone()
    assert row[0] == "failed"
    assert "模型名解析失败" in row[1]


def test_create_direction_rejects_duplicate_label(db):
    direction_id, error = direction_store.create_direction(db, "微信生态", "说明")
    assert error is None

    duplicate_id, duplicate_error = direction_store.create_direction(db, "微信生态", "另一份说明")
    assert duplicate_id is None
    assert duplicate_error == direction_store.DUPLICATE_LABEL_ERROR

    padded_id, padded_error = direction_store.create_direction(db, "  微信生态  ", "说明")
    assert padded_id is None
    assert padded_error == direction_store.DUPLICATE_LABEL_ERROR
    assert direction_id != duplicate_id


def test_update_direction_rejects_duplicate_label(db):
    first_id, _ = direction_store.create_direction(db, "微信生态", "说明")
    second_id, _ = direction_store.create_direction(db, "短视频", "说明")

    updated, error = direction_store.update_direction(db, second_id, label="微信生态")
    assert updated is None
    assert error == direction_store.DUPLICATE_LABEL_ERROR

    # Renaming a direction to its own name is not a conflict.
    same, error = direction_store.update_direction(db, first_id, label=" 微信生态 ")
    assert error is None
    assert same is not None


def test_stale_running_with_naive_utc_timestamp_is_not_reaped(db):
    """SQLite writes naive UTC; a local-timezone box must not read it as old."""
    direction_id = _direction(db)
    research, _ = direction_store.start_research(db, direction_id)
    naive = "2020-01-01 00:00:00"  # deliberately old and naive
    db.execute(
        "UPDATE bv_direction_research SET started_at=? WHERE id=?",
        (naive, research["research_version"]),
    )
    db.commit()

    reaped = direction_store.recover_stale_running(db)
    assert reaped == 1
    row = db.execute(
        "SELECT status FROM bv_direction_research WHERE direction_id=?",
        (direction_id,),
    ).fetchone()
    assert row[0] == "failed"


def test_naive_utc_started_at_is_treated_as_utc_not_local(db):
    """A fresh naive-UTC row must survive the reaper."""
    direction_id = _direction(db)
    research, _ = direction_store.start_research(db, direction_id)
    naive_now = (
        datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    )
    db.execute(
        "UPDATE bv_direction_research SET started_at=? WHERE id=?",
        (naive_now, research["research_version"]),
    )
    db.commit()

    reaped = direction_store.recover_stale_running(db)
    assert reaped == 0
    row = db.execute(
        "SELECT status FROM bv_direction_research WHERE direction_id=?",
        (direction_id,),
    ).fetchone()
    assert row[0] == "running"
