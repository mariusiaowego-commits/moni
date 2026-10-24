"""tests/test_data_isolation.py — verify data directory isolation and guard defenses."""

from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest

from tests.conftest import _snapshot_data_dir, assert_data_dir_unchanged


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
    """The real conftest guard must reject an actual on-disk modification."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="moni-guard-") as tmp:
        d = Path(tmp) / "data"
        d.mkdir()
        target = d / "a.txt"
        target.write_text("hello", encoding="utf-8")
        before = _snapshot_data_dir(d)
        target.write_text("changed", encoding="utf-8")
        after = _snapshot_data_dir(d)

    # This is the same helper the session-level fixture uses at teardown.
    with pytest.raises(AssertionError, match="测试套件改动了生产 data/ 目录"):
        assert_data_dir_unchanged(before, after)


def test_moni_data_dir_redirects_all_subsystems(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify that MONI_DATA_DIR env variable redirects paths across all subsystems."""
    custom_dir = tmp_path / "custom_data"
    custom_dir.mkdir()
    (custom_dir / "artifacts").mkdir()
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

    # 7. worker_llm direct sqlite connection
    from moni.share import worker_llm
    opened: list[Path] = []

    def _connect(path, *args, **kwargs):
        opened.append(Path(path))
        raise sqlite3.OperationalError("stop after path capture")

    monkeypatch.setattr(worker_llm.sqlite3, "connect", _connect)
    worker_llm.resolve_provider_model()
    assert opened == [custom_dir / "news.db"]

    # 8. Web app resolves static artifact mount and worker log at runtime
    from moni.web.server import create_app
    from moni.web.routes.share import read_worker_log

    app = create_app()
    artifact_mount = next(
        route for route in app.routes
        if getattr(route, "name", None) == "artifacts"
    )
    assert Path(artifact_mount.app.directory) == custom_dir / "artifacts"
    assert Path(read_worker_log(tail=1)["log_path"]) == (
        custom_dir / "artifacts" / "worker.log"
    )
