from __future__ import annotations

import pytest

from moni.llm import progress
from moni.db.connection import close_db, set_db_path
from moni.db.migrate import migrate
from moni.web.routes.llm_jobs import _annotate


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    set_db_path(tmp_path / "test.db")
    migrate()
    yield
    close_db()


def test_running_job_exposes_live_progress():
    progress.start_job("running-job")
    progress.record_event(
        "running-job",
        {"type": "tool_use", "name": "web_search", "input": {"query": "央行 辽宁"}},
    )
    try:
        job = _annotate([{"id": "running-job", "status": "running"}])[0]
        assert job["progress"]["stage"] == "searching"
        assert job["progress"]["searches"] == 1
        assert job["progress"]["last_query"] == "央行 辽宁"
    finally:
        progress.finish_job("running-job")


def test_terminal_and_pending_jobs_do_not_invent_progress():
    jobs = _annotate(
        [
            {"id": "pending-job", "status": "pending"},
            {"id": "done-job", "status": "done"},
        ]
    )
    assert jobs[0].get("progress") is None
    assert jobs[1].get("progress") is None
