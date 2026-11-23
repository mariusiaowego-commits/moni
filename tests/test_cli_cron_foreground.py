"""`moni cron start --foreground` keeps launchd supervising the real process."""

from __future__ import annotations

import os

import pytest

import moni.cli.main as cli
from moni.cron import scheduler as scheduler_mod


def test_foreground_writes_pid_then_cleans_up(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    seen: dict[str, str] = {}

    class FakeScheduler:
        def start(self) -> None:
            seen["pid"] = (tmp_path / "data" / ".cron.pid").read_text()
            raise SystemExit(0)

    monkeypatch.setattr(cli, "migrate", lambda: None)
    monkeypatch.setattr(scheduler_mod, "CronScheduler", FakeScheduler)

    with pytest.raises(SystemExit):
        cli.cmd_cron_start(foreground=True)

    assert seen["pid"] == str(os.getpid())
    assert not (tmp_path / "data" / ".cron.pid").exists()


def test_foreground_refuses_when_pid_is_alive(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / ".cron.pid").write_text(str(os.getpid()))
    monkeypatch.setattr(cli, "migrate", lambda: None)

    assert cli.cmd_cron_start(foreground=True) == 1
