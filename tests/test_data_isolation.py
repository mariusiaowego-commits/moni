"""tests/test_data_isolation.py — verify data directory isolation and guard defenses."""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from tests.conftest import _snapshot_data_dir, REAL_DATA_DIR


def test_snapshot_data_dir_detects_file_addition(tmp_path: Path):
    """Guard utility must detect when any new file is created in the directory."""
    d = tmp_path / "data"
    d.mkdir()
    (d / "existing.txt").write_text("hello", encoding="utf-8")

    before = _snapshot_data_dir(d)

    # Simulate rogue test creating a file
    (d / "rogue.log").write_text("evil", encoding="utf-8")
    after = _snapshot_data_dir(d)

    diff_added = set(after.keys()) - set(before.keys())
    assert diff_added == {"rogue.log"}


def test_snapshot_data_dir_detects_file_modification(tmp_path: Path):
    """Guard utility must detect when an existing file is modified."""
    d = tmp_path / "data"
    d.mkdir()
    target = d / ".last_run.json"
    target.write_text('{"old": 1}', encoding="utf-8")

    before = _snapshot_data_dir(d)

    # Simulate rogue test modifying timestamp
    target.write_text('{"new": 2}', encoding="utf-8")
    after = _snapshot_data_dir(d)

    diff_changed = {
        k for k in set(before.keys()) & set(after.keys())
        if before[k] != after[k]
    }
    assert diff_changed == {".last_run.json"}


def test_guard_assertion_raises_on_difference():
    """Verify that the guard's assertion logic strictly rejects changes."""
    before = {"a.txt": (5, b"hash_a")}
    after = {"a.txt": (5, b"hash_a_modified")}

    diff_changed = {
        k for k in set(before.keys()) & set(after.keys())
        if before[k] != after[k]
    }
    with pytest.raises(AssertionError, match="测试套件改动了生产 data/ 目录"):
        assert not diff_changed, "测试套件改动了生产 data/ 目录！"


def test_moni_data_dir_redirects_all_subsystems(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify that MONI_DATA_DIR env variable redirects paths across all subsystems."""
    custom_dir = tmp_path / "custom_data"
    custom_dir.mkdir()
    monkeypatch.setenv("MONI_DATA_DIR", str(custom_dir))

    # 1. DB connection
    from moni.db import connection as db_conn
    monkeypatch.setattr(db_conn, "_DB_PATH", None)
    monkeypatch.delenv("MONI_DB_PATH", raising=False)
    assert db_conn.get_db_path() == custom_dir / "news.db"

    # 2. Scheduler last_run
    from moni.fetch import scheduler as fetch_scheduler
    assert fetch_scheduler._last_run_path() == custom_dir / ".last_run.json"
    s = fetch_scheduler.Scheduler()
    assert s.last_run_path == custom_dir / ".last_run.json"

    # 3. LLM token_log
    from moni.llm import token_log
    logger = token_log.TokenLogger()
    assert logger.path == custom_dir / "llm_usage.log"

    # 4. LLM usage parser
    from moni.llm import usage as llm_usage
    assert llm_usage._default_log_path() == custom_dir / "llm_usage.log"

    # 5. Share worker output & log
    from moni.share import worker as share_worker
    assert share_worker._output_dir() == custom_dir / "artifacts"
    assert share_worker._log_file() == custom_dir / "artifacts" / "worker.log"
    assert share_worker._default_db() == custom_dir / "news.db"

    # 6. Share artifacts dir
    from moni.share import artifacts as share_artifacts
    assert share_artifacts._artifacts_dir() == custom_dir / "artifacts"
