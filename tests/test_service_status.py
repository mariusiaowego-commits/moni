"""Tests for /api/service status pidfile + port fallback."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from moni.web.routes import service as svc


@pytest.fixture(autouse=True)
def _isolate_pidfile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point PIDFILE at a temp path so tests never touch data/.web.pid."""
    pidfile = tmp_path / ".web.pid"
    monkeypatch.setattr(svc, "PIDFILE", pidfile)
    yield pidfile


def test_read_pidfile_missing(tmp_path: Path):
    assert svc._read_pidfile() is None


def test_read_pidfile_alive(tmp_path: Path):
    pid = Path(svc.PIDFILE)
    # Current pytest process is always alive
    import os

    pid.write_text(f"{os.getpid()}\n")
    assert svc._read_pidfile() == os.getpid()


def test_read_pidfile_stale(tmp_path: Path):
    # PID 1 may exist on Unix; use a high unlikely dead pid
    dead = 999_999_999
    svc.PIDFILE.write_text(f"{dead}\n")
    # If somehow alive, skip
    if svc._pid_alive(dead):
        pytest.skip("unlikely pid is alive on this host")
    assert svc._read_pidfile() is None


def test_resolve_prefers_pidfile_over_port(tmp_path: Path):
    import os

    svc.PIDFILE.write_text(f"{os.getpid()}\n")
    with patch.object(svc, "_find_listener_pid", return_value=12345) as find:
        pid, source = svc.resolve_service_pid(heal=False)
    assert pid == os.getpid()
    assert source == "pidfile"
    find.assert_not_called()


def test_resolve_port_fallback_heals_pidfile(tmp_path: Path):
    assert not svc.PIDFILE.exists()
    with patch.object(svc, "_find_listener_pid", return_value=424242):
        with patch.object(svc, "_pid_alive", return_value=True):
            # _find_listener_pid returns pid; heal writes it
            pid, source = svc.resolve_service_pid(heal=True)
    assert pid == 424242
    assert source == "port"
    assert svc.PIDFILE.read_text().strip() == "424242"


def test_resolve_none_when_no_pidfile_no_listener(tmp_path: Path):
    with patch.object(svc, "_find_listener_pid", return_value=None):
        pid, source = svc.resolve_service_pid(heal=True)
    assert pid is None
    assert source == "none"


def test_find_listener_psutil_access_denied_falls_to_lsof(tmp_path: Path):
    """macOS: psutil.net_connections raises AccessDenied — must not bubble."""
    with patch.object(svc, "_find_listener_via_lsof", return_value=None):
        with patch.object(svc, "_find_listener_via_psutil", wraps=svc._find_listener_via_psutil):
            # Force lsof miss then psutil path that raises
            import psutil

            with patch("psutil.net_connections", side_effect=psutil.AccessDenied(pid=1)):
                assert svc._find_listener_via_psutil(8900) is None
                # full path: lsof None → psutil None
                assert svc._find_listener_pid(8900) is None


def test_find_listener_prefers_lsof(tmp_path: Path):
    with patch.object(svc, "_find_listener_via_lsof", return_value=111):
        with patch.object(svc, "_find_listener_via_psutil") as ps:
            assert svc._find_listener_pid(8900) == 111
            ps.assert_not_called()


def test_status_endpoint_includes_source(tmp_path: Path):
    from fastapi.testclient import TestClient

    from moni.web.server import create_app

    with patch.object(svc, "resolve_service_pid", return_value=(None, "none")):
        # create_app uses real router; patch is on module used by handler
        client = TestClient(create_app())
        # Re-patch after app import path — handler calls resolve_service_pid on svc module
        with patch.object(svc, "resolve_service_pid", return_value=(None, "none")):
            r = client.get("/api/service/status")
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is False
    assert body["pid"] is None
    assert body["port"] == 8900
    assert body["source"] == "none"


def test_status_endpoint_running_via_port(tmp_path: Path):
    from fastapi.testclient import TestClient

    from moni.web.server import create_app

    with patch.object(svc, "resolve_service_pid", return_value=(54231, "port")):
        with patch.object(svc, "_uptime", return_value=12.3):
            client = TestClient(create_app())
            with patch.object(svc, "resolve_service_pid", return_value=(54231, "port")):
                with patch.object(svc, "_uptime", return_value=12.3):
                    r = client.get("/api/service/status")
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is True
    assert body["pid"] == 54231
    assert body["source"] == "port"
    assert body["uptime_sec"] == 12.3
