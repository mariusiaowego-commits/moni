"""tests/test_chrome_profiles_inspect.py — test chrome-profiles.py inspector script."""

import json
from pathlib import Path
import pytest
import sys

# Add scripts directory to path to import inspect_chrome_profiles
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "scripts"))

from importlib import import_module
chrome_profiles_mod = import_module("chrome-profiles")
inspect_chrome_profiles = chrome_profiles_mod.inspect_chrome_profiles
format_table = chrome_profiles_mod.format_table


@pytest.fixture
def fake_chrome_setup(tmp_path):
    """Fixture creating realistic fake Chrome Local State and directories."""
    chrome_dir = tmp_path / "Google" / "Chrome"
    chrome_dir.mkdir(parents=True)

    local_state_data = {
        "profile": {
            "info_cache": {
                "Default": {
                    "name": "用户1",
                    "user_name": "fledgeel@gmail.com",
                },
                "Profile": {
                    "name": "您的 Chrome",
                    "user_name": "linqin7533@gmail.com",
                },
                "Profile 1": {
                    "name": "Marius",
                    "user_name": "mariusiaowego@gmail.com",
                },
                "Profile 6": {
                    "name": "hermes",
                    "user_name": "matthew.qi.tao@gmail.com",
                },
            }
        }
    }
    (chrome_dir / "Local State").write_text(json.dumps(local_state_data), encoding="utf-8")

    # Create directories for each profile
    for d in ["Default", "Profile", "Profile 1", "Profile 6", "Profile 7"]:
        p = chrome_dir / d
        p.mkdir()
        (p / "dummy.txt").write_bytes(b"hello" * 100)

    # Moni directory
    moni_chrome_dir = tmp_path / ".moni" / "chrome"
    moni_chrome_dir.mkdir(parents=True)

    daemon_dir = moni_chrome_dir / "daemon"
    daemon_dir.mkdir()
    (daemon_dir / "data.bin").write_bytes(b"x" * 1024)

    xwatch_dir = moni_chrome_dir / "xwatch"
    xwatch_dir.mkdir()
    (xwatch_dir / "data.bin").write_bytes(b"x" * 2048)

    map_data = {
        "version": 1,
        "profiles": {
            "xwatch": {
                "account": "matthew.qi.tao@gmail.com",
            }
        }
    }
    (moni_chrome_dir / "profile-map.json").write_text(json.dumps(map_data), encoding="utf-8")

    return chrome_dir, moni_chrome_dir


def test_inspect_profiles_matches_ui_names_and_accounts(fake_chrome_setup):
    chrome_dir, moni_chrome_dir = fake_chrome_setup

    profiles = inspect_chrome_profiles(chrome_dir=chrome_dir, moni_chrome_dir=moni_chrome_dir)

    prof_by_dir = {p["disk_dir"]: p for p in profiles}

    # Verify Profile 6 -> hermes / matthew.qi.tao@gmail.com
    assert "Profile 6" in prof_by_dir
    p6 = prof_by_dir["Profile 6"]
    assert p6["ui_name"] == "hermes"
    assert p6["account"] == "matthew.qi.tao@gmail.com"
    assert p6["owner"] == "用户"
    assert p6["moni_in_use"] == "否"

    # Verify Profile 1 -> Marius / mariusiaowego@gmail.com
    assert "Profile 1" in prof_by_dir
    p1 = prof_by_dir["Profile 1"]
    assert p1["ui_name"] == "Marius"
    assert p1["account"] == "mariusiaowego@gmail.com"
    assert p1["owner"] == "用户"
    assert p1["moni_in_use"] == "否"

    # Verify Profile -> 您的 Chrome
    assert "Profile" in prof_by_dir
    assert prof_by_dir["Profile"]["ui_name"] == "您的 Chrome"
    assert prof_by_dir["Profile"]["account"] == "linqin7533@gmail.com"

    # Verify Default -> 用户1
    assert "Default" in prof_by_dir
    assert prof_by_dir["Default"]["ui_name"] == "用户1"
    assert prof_by_dir["Default"]["account"] == "fledgeel@gmail.com"

    # Verify moni daemon and xwatch
    assert "~/.moni/chrome/daemon" in prof_by_dir
    p_daemon = prof_by_dir["~/.moni/chrome/daemon"]
    assert p_daemon["ui_name"] == "(moni daemon)"
    assert p_daemon["owner"] == "moni"
    assert p_daemon["moni_in_use"] == "是"

    assert "~/.moni/chrome/xwatch" in prof_by_dir
    p_xwatch = prof_by_dir["~/.moni/chrome/xwatch"]
    assert p_xwatch["ui_name"] == "(moni xwatch)"
    assert p_xwatch["owner"] == "moni"
    assert p_xwatch["account"] == "matthew.qi.tao@gmail.com"
    assert p_xwatch["moni_in_use"] == "是(登录态)"


def test_format_table_renders_all_columns(fake_chrome_setup):
    chrome_dir, moni_chrome_dir = fake_chrome_setup
    profiles = inspect_chrome_profiles(chrome_dir=chrome_dir, moni_chrome_dir=moni_chrome_dir)
    table_str = format_table(profiles)

    assert "Chrome UI 名字" in table_str
    assert "Marius" in table_str
    assert "mariusiaowego@gmail.com" in table_str
    assert "hermes" in table_str
    assert "matthew.qi.tao@gmail.com" in table_str
    assert "(moni daemon)" in table_str
    assert "(moni xwatch)" in table_str
