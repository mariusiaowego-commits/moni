"""Tests for the in-process direction research runner."""
from __future__ import annotations

import json
import threading
import time

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
        model_name="test-model",
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
        model_name="test-model",
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
        model_name="test-model",
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

    runner = DirectionResearchRunner(slow, "test")
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
