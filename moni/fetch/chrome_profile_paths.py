"""moni.fetch.chrome_profile_paths — centralized Chrome profile path resolution.

Part of v1.0.56 Chrome profile isolation:
Moves moni Chrome user-data-dir profiles out of macOS ~/Library/Application Support/Google/Chrome/
to ~/.moni/chrome/ to avoid polluting user profiles (specifically Profile 6 / hermes and Profile 7).

Target paths:
- Daemon (36kr / general): ~/.moni/chrome/daemon
- XWatch (X.com following): ~/.moni/chrome/xwatch
- Catalog: ~/.moni/chrome/profile-map.json

Backward compatibility:
- Respects MONI_CHROME_USER_DATA_DIR / MONI_XWATCH_CHROME_USER_DATA if explicitly set.
- Automatically claims legacy directories if legacy directory has data and new directory
  does not yet exist, emitting a logger warning instructing the user to run the migration script.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import platform
from typing import Any

logger = logging.getLogger(__name__)


def get_real_user_home() -> Path:
    """Return the real home directory for the running user, handling macOS pw_dir."""
    if platform.system() == "Darwin":
        try:
            import pwd

            return Path(pwd.getpwuid(os.getuid()).pw_dir)
        except Exception:
            pass
    return Path.home()


def get_moni_chrome_root() -> Path:
    """Root directory for moni-owned Chrome profiles (~/.moni/chrome)."""
    return get_real_user_home() / ".moni" / "chrome"


def get_new_daemon_dir() -> Path:
    return get_moni_chrome_root() / "daemon"


def get_new_xwatch_dir() -> Path:
    return get_moni_chrome_root() / "xwatch"


def get_profile_map_path() -> Path:
    return get_moni_chrome_root() / "profile-map.json"


def get_legacy_daemon_dir() -> Path:
    if platform.system() == "Darwin":
        return (
            get_real_user_home()
            / "Library"
            / "Application Support"
            / "Google"
            / "Chrome"
            / "Profile 7"
        )
    return Path.home() / ".moni" / "chrome-profile"


def get_legacy_xwatch_dir() -> Path:
    if platform.system() == "Darwin":
        return (
            get_real_user_home()
            / "Library"
            / "Application Support"
            / "Google"
            / "Chrome"
            / "Profile 6"
        )
    return Path.home() / ".moni" / "xwatch-chrome-profile"


def _directory_has_data(path: Path, *, target_kind: str = "general") -> bool:
    """Check if a directory exists and contains meaningful profile data."""
    if not path.exists() or not path.is_dir():
        return False

    # For xwatch legacy (which lived inside Profile 6/Default):
    if target_kind == "xwatch":
        nested_default = path / "Default"
        if nested_default.is_dir():
            if (nested_default / "Cookies").exists():
                return True
            try:
                if any(nested_default.iterdir()):
                    return True
            except Exception:
                pass
        if (path / "Cookies").exists():
            return True
        try:
            return any(path.iterdir())
        except Exception:
            return False

    # For daemon or general directory
    try:
        if (path / "Default").exists() or (path / "Cookies").exists():
            return True
        return any(path.iterdir())
    except Exception:
        return False


def resolve_user_data_dir(
    env_var_name: str,
    new_path: Path,
    legacy_path: Path,
    target_name: str,
) -> Path:
    """Resolve Chrome user-data-dir respecting env var, migration state, and legacy fallback.

    1. If env_var_name is in os.environ -> return Path(os.environ[env_var_name]).
    2. If new_path exists and has data -> return new_path.
    3. If legacy_path exists and has data (and new_path does NOT exist or is empty) ->
       log warning, auto-claim legacy_path, and return legacy_path.
    4. Otherwise -> return new_path.
    """
    custom_env = os.environ.get(env_var_name)
    if custom_env:
        return Path(custom_env).expanduser()

    # Check if new path already exists and has data
    if _directory_has_data(new_path, target_kind=target_name):
        return new_path

    # Check if legacy path has data
    if _directory_has_data(legacy_path, target_kind=target_name):
        logger.warning(
            "[chrome_profile] Using legacy Chrome user-data-dir %s for %s "
            "(new path %s not found or empty). "
            "Please run `scripts/migrate-chrome-profiles.py --dry-run` to inspect and migrate.",
            legacy_path,
            target_name,
            new_path,
        )
        return legacy_path

    return new_path


def resolve_daemon_user_data_dir() -> Path:
    """Resolve user-data-dir for moni chrome_daemon (36kr)."""
    return resolve_user_data_dir(
        env_var_name="MONI_CHROME_USER_DATA_DIR",
        new_path=get_new_daemon_dir(),
        legacy_path=get_legacy_daemon_dir(),
        target_name="daemon",
    )


def resolve_xwatch_user_data_dir() -> Path:
    """Resolve user-data-dir for moni xwatch (X.com following)."""
    return resolve_user_data_dir(
        env_var_name="MONI_XWATCH_CHROME_USER_DATA",
        new_path=get_new_xwatch_dir(),
        legacy_path=get_legacy_xwatch_dir(),
        target_name="xwatch",
    )


def load_profile_map(map_path: Path | None = None) -> dict[str, Any]:
    """Load ~/.moni/chrome/profile-map.json catalog if present."""
    target = map_path or get_profile_map_path()
    if not target.exists():
        return {}
    try:
        with open(target, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("[chrome_profile] Failed reading %s: %s", target, e)
        return {}
