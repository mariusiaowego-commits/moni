"""tests/test_chrome_profile_paths.py — test Chrome profile path resolution and legacy fallback."""

import logging
from pathlib import Path
import pytest

from moni.fetch.chrome_profile_paths import (
    resolve_user_data_dir,
    resolve_daemon_user_data_dir,
    resolve_xwatch_user_data_dir,
    load_profile_map,
)


def test_env_var_override_takes_precedence(tmp_path, monkeypatch):
    custom_dir = tmp_path / "custom_chrome_profile"
    monkeypatch.setenv("MONI_CHROME_USER_DATA_DIR", str(custom_dir))

    resolved = resolve_daemon_user_data_dir()
    assert resolved == custom_dir


def test_env_var_override_xwatch_takes_precedence(tmp_path, monkeypatch):
    custom_dir = tmp_path / "custom_xwatch_profile"
    monkeypatch.setenv("MONI_XWATCH_CHROME_USER_DATA", str(custom_dir))

    resolved = resolve_xwatch_user_data_dir()
    assert resolved == custom_dir


def test_new_dir_used_when_it_has_data(tmp_path, monkeypatch):
    monkeypatch.delenv("MONI_CHROME_USER_DATA_DIR", raising=False)
    new_dir = tmp_path / "new_daemon"
    new_dir.mkdir(parents=True)
    (new_dir / "Default").mkdir()

    legacy_dir = tmp_path / "legacy_daemon"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "Default").mkdir()

    resolved = resolve_user_data_dir(
        env_var_name="MONI_CHROME_USER_DATA_DIR",
        new_path=new_dir,
        legacy_path=legacy_dir,
        target_name="daemon",
    )
    assert resolved == new_dir


def test_legacy_dir_auto_claimed_with_warning_when_new_missing(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("MONI_CHROME_USER_DATA_DIR", raising=False)
    new_dir = tmp_path / "new_daemon"  # does not exist

    legacy_dir = tmp_path / "legacy_daemon"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "Default").mkdir()

    with caplog.at_level(logging.WARNING):
        resolved = resolve_user_data_dir(
            env_var_name="MONI_CHROME_USER_DATA_DIR",
            new_path=new_dir,
            legacy_path=legacy_dir,
            target_name="daemon",
        )

    assert resolved == legacy_dir
    assert "Using legacy Chrome user-data-dir" in caplog.text
    assert "scripts/migrate-chrome-profiles.py --dry-run" in caplog.text


def test_legacy_xwatch_claimed_when_nested_default_has_data(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("MONI_XWATCH_CHROME_USER_DATA", raising=False)
    new_dir = tmp_path / "new_xwatch"  # does not exist

    legacy_dir = tmp_path / "Profile 6"
    legacy_dir.mkdir(parents=True)
    nested_default = legacy_dir / "Default"
    nested_default.mkdir()
    (nested_default / "Cookies").write_text("dummy")

    with caplog.at_level(logging.WARNING):
        resolved = resolve_user_data_dir(
            env_var_name="MONI_XWATCH_CHROME_USER_DATA",
            new_path=new_dir,
            legacy_path=legacy_dir,
            target_name="xwatch",
        )

    assert resolved == legacy_dir
    assert "Using legacy Chrome user-data-dir" in caplog.text


def test_new_dir_used_by_default_when_neither_has_data(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("MONI_CHROME_USER_DATA_DIR", raising=False)
    new_dir = tmp_path / "new_daemon"
    legacy_dir = tmp_path / "legacy_daemon"  # neither exists

    with caplog.at_level(logging.WARNING):
        resolved = resolve_user_data_dir(
            env_var_name="MONI_CHROME_USER_DATA_DIR",
            new_path=new_dir,
            legacy_path=legacy_dir,
            target_name="daemon",
        )

    assert resolved == new_dir
    assert "Using legacy Chrome user-data-dir" not in caplog.text


def test_load_profile_map_empty_when_missing(tmp_path):
    assert load_profile_map(tmp_path / "nonexistent.json") == {}


def test_load_profile_map_parses_json(tmp_path):
    map_file = tmp_path / "profile-map.json"
    map_file.write_text('{"profiles": {"xwatch": {"total_cookies": 274}}}', encoding="utf-8")
    data = load_profile_map(map_file)
    assert data["profiles"]["xwatch"]["total_cookies"] == 274
