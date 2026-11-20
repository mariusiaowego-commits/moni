#!/usr/bin/env python3
"""scripts/chrome-profiles.py — inspect Chrome UI profiles, disk directories, and moni bindings.

Outputs an alignment table matching Chrome Local State info_cache (UI names, accounts)
with disk directories and moni's profile allocations.

Usage:
    /usr/local/bin/python3 scripts/chrome-profiles.py [--json] [--chrome-dir <path>] [--moni-chrome-dir <path>]
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import sys
import unicodedata
from typing import Any


def get_display_width(text: str) -> int:
    """Calculate terminal display width considering CJK full-width characters."""
    width = 0
    for char in text:
        if unicodedata.east_asian_width(char) in ("F", "W"):
            width += 2
        else:
            width += 1
    return width


def pad_to_width(text: str, target_width: int, align_left: bool = True) -> str:
    """Pad string with spaces based on visible terminal display width."""
    current_width = get_display_width(text)
    pad_len = max(0, target_width - current_width)
    spaces = " " * pad_len
    return (text + spaces) if align_left else (spaces + text)


def format_size(bytes_num: int) -> str:
    """Format size in human readable string (e.g. 1.6G, 153M, 24K)."""
    if bytes_num <= 0:
        return "0B"
    if bytes_num < 1024:
        return f"{bytes_num}B"
    if bytes_num < 1024 * 1024:
        return f"{bytes_num / 1024:.0f}K"
    if bytes_num < 1024 * 1024 * 1024:
        return f"{bytes_num / (1024 * 1024):.0f}M"
    return f"{bytes_num / (1024 * 1024 * 1024):.1f}G"


def get_directory_size(dir_path: Path) -> int:
    """Recursively calculate directory size in bytes, ignoring broken symlinks."""
    total = 0
    try:
        for entry in os.scandir(dir_path):
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    total += get_directory_size(Path(entry.path))
                else:
                    total += entry.stat(follow_symlinks=False).st_size
            except (OSError, PermissionError):
                continue
    except (OSError, PermissionError):
        pass
    return total


def get_default_chrome_dir() -> Path:
    if platform.system() == "Darwin":
        try:
            import pwd

            home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        except Exception:
            home = Path.home()
        return home / "Library" / "Application Support" / "Google" / "Chrome"
    return Path.home() / ".config" / "google-chrome"


def get_default_moni_chrome_dir() -> Path:
    try:
        import pwd

        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except Exception:
        home = Path.home()
    return home / ".moni" / "chrome"


def inspect_chrome_profiles(
    chrome_dir: Path | None = None,
    moni_chrome_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Inspect Chrome profiles from Local State, Chrome directory, and moni profiles."""
    chrome_root = chrome_dir or get_default_chrome_dir()
    moni_root = moni_chrome_dir or get_default_moni_chrome_dir()

    info_cache: dict[str, dict[str, Any]] = {}
    local_state_file = chrome_root / "Local State"
    if local_state_file.exists():
        try:
            with open(local_state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                info_cache = data.get("profile", {}).get("info_cache", {})
        except Exception:
            pass

    profile_map: dict[str, Any] = {}
    map_file = moni_root / "profile-map.json"
    if map_file.exists():
        try:
            with open(map_file, "r", encoding="utf-8") as f:
                profile_map = json.load(f)
        except Exception:
            pass

    results: list[dict[str, Any]] = []

    # 1. Inspect user Chrome profiles defined in info_cache or disk
    # We prioritize known info_cache order or alphabetical
    profile_dirs_seen = set()

    # Pre-read directories under Chrome root
    existing_dirs: dict[str, Path] = {}
    if chrome_root.exists() and chrome_root.is_dir():
        for entry in chrome_root.iterdir():
            if entry.is_dir() and (entry.name == "Default" or entry.name.startswith("Profile")):
                existing_dirs[entry.name] = entry

    # First add entries from info_cache (canonical Chrome user profiles)
    for folder_name, info in info_cache.items():
        profile_dirs_seen.add(folder_name)
        disk_path = existing_dirs.get(folder_name, chrome_root / folder_name)
        dir_size = get_directory_size(disk_path) if disk_path.exists() else 0
        ui_name = info.get("name") or folder_name
        user_name = info.get("user_name") or info.get("gaia_name") or "—"

        # Check if moni is using this profile
        # For Profile 6, if Profile 6/Default exists and moni xwatch is not migrated,
        # note that the root belongs to the user
        moni_using = "否"

        results.append({
            "ui_name": ui_name,
            "disk_dir": folder_name,
            "full_path": str(disk_path),
            "owner": "用户",
            "size_bytes": dir_size,
            "size_str": format_size(dir_size) if disk_path.exists() else "不存在",
            "account": user_name,
            "moni_in_use": moni_using,
            "type": "user_profile",
        })

    # Check remaining directories in Chrome root not in info_cache (e.g. Profile 7)
    for folder_name, disk_path in sorted(existing_dirs.items()):
        if folder_name in profile_dirs_seen:
            continue
        dir_size = get_directory_size(disk_path)
        # Profile 7 was created by moni daemon
        if folder_name == "Profile 7":
            results.append({
                "ui_name": "(moni daemon legacy)",
                "disk_dir": folder_name,
                "full_path": str(disk_path),
                "owner": "moni",
                "size_bytes": dir_size,
                "size_str": format_size(dir_size),
                "account": "—",
                "moni_in_use": "是(遗留目录)",
                "type": "legacy_daemon",
            })
        else:
            results.append({
                "ui_name": f"({folder_name})",
                "disk_dir": folder_name,
                "full_path": str(disk_path),
                "owner": "用户",
                "size_bytes": dir_size,
                "size_str": format_size(dir_size),
                "account": "—",
                "moni_in_use": "否",
                "type": "user_profile",
            })

    # 2. Check moni profiles in ~/.moni/chrome/
    daemon_dir = moni_root / "daemon"
    if daemon_dir.exists():
        d_size = get_directory_size(daemon_dir)
        results.append({
            "ui_name": "(moni daemon)",
            "disk_dir": "~/.moni/chrome/daemon",
            "full_path": str(daemon_dir),
            "owner": "moni",
            "size_bytes": d_size,
            "size_str": format_size(d_size),
            "account": "—",
            "moni_in_use": "是",
            "type": "moni_daemon",
        })

    xwatch_dir = moni_root / "xwatch"
    if xwatch_dir.exists():
        x_size = get_directory_size(xwatch_dir)
        # Try to resolve xwatch account from profile-map.json or xwatch Local State
        account = "(unknown)"
        x_map = profile_map.get("profiles", {}).get("xwatch", {})
        if x_map.get("account"):
            account = x_map["account"]
        elif (xwatch_dir / "Local State").exists():
            try:
                with open(xwatch_dir / "Local State", "r", encoding="utf-8") as f:
                    xd = json.load(f)
                    cached = xd.get("profile", {}).get("info_cache", {}).get("Default", {})
                    account = cached.get("user_name") or cached.get("gaia_name") or account
            except Exception:
                pass

        results.append({
            "ui_name": "(moni xwatch)",
            "disk_dir": "~/.moni/chrome/xwatch",
            "full_path": str(xwatch_dir),
            "owner": "moni",
            "size_bytes": x_size,
            "size_str": format_size(x_size),
            "account": account,
            "moni_in_use": "是(登录态)",
            "type": "moni_xwatch",
        })
    elif (chrome_root / "Profile 6" / "Default").exists():
        # Pre-migration: xwatch is currently inside Profile 6/Default
        x_size = get_directory_size(chrome_root / "Profile 6" / "Default")
        account = "(unknown)"
        results.append({
            "ui_name": "(moni xwatch legacy)",
            "disk_dir": "Profile 6/Default",
            "full_path": str(chrome_root / "Profile 6" / "Default"),
            "owner": "moni",
            "size_bytes": x_size,
            "size_str": format_size(x_size),
            "account": account,
            "moni_in_use": "是(登录态/待迁移)",
            "type": "legacy_xwatch",
        })

    return results


def format_table(profiles: list[dict[str, Any]]) -> str:
    """Format profiles as aligned text table."""
    headers = [
        ("Chrome UI 名字", 16),
        ("磁盘目录", 26),
        ("所有者", 8),
        ("大小", 8),
        ("账号", 28),
        ("moni 在用?", 14),
    ]

    lines = []
    # Build header line
    header_cols = [pad_to_width(title, width) for title, width in headers]
    lines.append(" ".join(header_cols))
    # Separator
    lines.append("-" * sum(w + 1 for _, w in headers))

    for p in profiles:
        cols = [
            pad_to_width(str(p.get("ui_name", "—")), 16),
            pad_to_width(str(p.get("disk_dir", "—")), 26),
            pad_to_width(str(p.get("owner", "—")), 8),
            pad_to_width(str(p.get("size_str", "—")), 8),
            pad_to_width(str(p.get("account", "—")), 28),
            pad_to_width(str(p.get("moni_in_use", "—")), 14),
        ]
        lines.append(" ".join(cols))

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect Chrome UI profiles and moni allocations.")
    parser.add_argument("--json", action="store_true", help="Output JSON format")
    parser.add_argument("--chrome-dir", type=Path, default=None, help="Path to Chrome user data dir")
    parser.add_argument("--moni-chrome-dir", type=Path, default=None, help="Path to moni chrome dir")
    args = parser.parse_args()

    profiles = inspect_chrome_profiles(
        chrome_dir=args.chrome_dir,
        moni_chrome_dir=args.moni_chrome_dir,
    )

    if args.json:
        print(json.dumps(profiles, ensure_ascii=False, indent=2))
    else:
        print(format_table(profiles))

    return 0


if __name__ == "__main__":
    sys.exit(main())
