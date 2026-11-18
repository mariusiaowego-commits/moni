"""tests/test_worker_env.py — verify the share worker is launched with
the right env (PATH, MONI_DB, BESTBLOGS_API_KEY).

v1.0.9.3: caught 3 related bugs in one debug session:
1. setdefault("MONI_DB", ...) only sets if key absent; if env has
   MONI_DB='' (empty), it stays empty and worker reads wrong DB.
2. PATH didn't include /usr/local/bin (where python3.12 lives);
   subprocess invocations used system python3.14 which is missing
   yaml, breaking `moni.fetch.bestblogs_api` import.
3. The "lsof -ti :PORT" check matched transient Chrome CLOSE_WAIT
   connections, causing dev_8900.sh stop/restart to misidentify
   port state.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# 只能插当前 checkout：硬编码 ~/dev/moni 会让同一次 pytest 进程里
# `moni.*` 从另一份代码解析，测试结果随收集顺序变化。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moni.share import worker as sw


@pytest.fixture
def fresh_env(monkeypatch, tmp_path):
    """Reset env to a known state — no MONI_DB, no BESTBLOGS."""
    monkeypatch.delenv("MONI_DB", raising=False)
    monkeypatch.delenv("MONI_QUEUE_DIR", raising=False)
    monkeypatch.delenv("MONI_OUTPUT_DIR", raising=False)
    monkeypatch.delenv("BESTBLOGS_API_KEY", raising=False)
    # Default home for HOME lookups
    monkeypatch.setenv("HOME", str(tmp_path))
    # Keep launch_worker()'s DB pre-flight independent of the checkout's
    # gitignored data/ directory (absent in clean clones and CI).
    (tmp_path / "data").mkdir()
    (tmp_path / ".moni" / "queue").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sw, "DEFAULT_DB", tmp_path / "data" / "news.db")
    return monkeypatch


def test_launch_worker_sets_correct_monit_db(fresh_env, tmp_path):
    """Even when env has MONI_DB='', launch_worker must set the
    project-relative default (data/news.db), not fall through to
    ~/.moni/data/moni.db."""
    fresh_env.setenv("MONI_DB", "")  # empty string
    # Create a real file so the pre-flight is_file() check passes
    fake_script = tmp_path / "default_worker.sh"
    fake_script.write_text("#!/bin/bash\necho ok\n")
    fake_script.chmod(0o755)
    with patch.object(sw, "_check_lock", return_value=None), \
         patch.object(sw, "WORKER_SCRIPT", fake_script), \
         patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value.pid = 99999
        mock_popen.return_value.poll.return_value = 0
        sw.launch_worker()
        assert mock_popen.called
        args, kwargs = mock_popen.call_args
        env = kwargs.get("env", {})
        assert env.get("MONI_DB") == str(sw.DEFAULT_DB)
        assert env["MONI_DB"].endswith("/data/news.db"), \
            f"MONI_DB must point to data/news.db, got {env.get('MONI_DB')}"


def test_launch_worker_prepends_usr_local_bin_to_path(fresh_env, tmp_path):
    """v1.0.9.3: PATH must include /usr/local/bin so worker's
    `python3` resolves to 3.12 (which has yaml)."""
    fake_script = tmp_path / "default_worker.sh"
    fake_script.write_text("#!/bin/bash\necho ok\n")
    fake_script.chmod(0o755)
    with patch.object(sw, "_check_lock", return_value=None), \
         patch.object(sw, "WORKER_SCRIPT", fake_script), \
         patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value.pid = 99999
        mock_popen.return_value.poll.return_value = 0
        sw.launch_worker()
        args, kwargs = mock_popen.call_args
        env = kwargs.get("env", {})
        path = env.get("PATH", "")
        assert "/usr/local/bin" in path.split(":"), \
            f"PATH must include /usr/local/bin, got: {path[:200]}"
        parts = path.split(":")
        local_bin_idx = parts.index("/usr/local/bin")
        assert local_bin_idx < 5, \
            f"/usr/local/bin must come early in PATH, found at index {local_bin_idx}"


def test_launch_worker_propagates_bestblogs_key(fresh_env, tmp_path):
    """If the web process has BESTBLOGS_API_KEY, the worker must
    inherit it so BB enhancement can call the API."""
    fresh_env.setenv("BESTBLOGS_API_KEY", "test_key_xyz")
    fake_script = tmp_path / "default_worker.sh"
    fake_script.write_text("#!/bin/bash\necho ok\n")
    fake_script.chmod(0o755)
    with patch.object(sw, "_check_lock", return_value=None), \
         patch.object(sw, "WORKER_SCRIPT", fake_script), \
         patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value.pid = 99999
        mock_popen.return_value.poll.return_value = 0
        sw.launch_worker()
        args, kwargs = mock_popen.call_args
        env = kwargs.get("env", {})
        assert env.get("BESTBLOGS_API_KEY") == "test_key_xyz"


def test_launch_worker_db_path_resolves_empty_string(monkeypatch, tmp_path):
    """os.environ.get('MONI_DB') or default — empty string is falsy,
    so the or-chain should fall through to DEFAULT_DB."""
    monkeypatch.setenv("MONI_DB", "")
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / ".moni" / "queue").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sw, "DEFAULT_DB", tmp_path / "data" / "news.db")
    fake_script = tmp_path / "default_worker.sh"
    fake_script.write_text("#!/bin/bash\necho ok\n")
    fake_script.chmod(0o755)
    with patch.object(sw, "_check_lock", return_value=None), \
         patch.object(sw, "WORKER_SCRIPT", fake_script), \
         patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value.pid = 99999
        mock_popen.return_value.poll.return_value = 0
        sw.launch_worker()
        args, kwargs = mock_popen.call_args
        env = kwargs.get("env", {})
        assert env.get("MONI_DB"), f"MONI_DB must be non-empty, got: {env.get('MONI_DB')!r}"
        assert env["MONI_DB"].endswith("/data/news.db")
