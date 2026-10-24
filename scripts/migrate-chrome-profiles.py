#!/usr/bin/env python3
"""scripts/migrate-chrome-profiles.py — migrate moni Chrome profiles out of user Chrome directory.

Part of v1.0.56:
Safely relocates moni Chrome profiles from ~/Library/Application Support/Google/Chrome/
to ~/.moni/chrome/ without touching user profile data in Profile 6 root (hermes).

Relocations:
1. Chrome/Profile 6/Default  -> ~/.moni/chrome/xwatch/Default (X.com login cookies & state)
2. Chrome/Profile 6/Local State -> ~/.moni/chrome/xwatch/Local State
3. Chrome/Profile 7          -> ~/.moni/chrome/daemon (36kr crawler profile)
4. Catalog file              -> ~/.moni/chrome/profile-map.json

Commands:
    --dry-run          (default) Read-only safety check, planned actions, lock detection, and baseline metrics
    --apply            Migrate the daemon profile only (safe default)
    --apply --include-xwatch
                       Explicitly also migrate xwatch Profile 6/Default. Requires a headed,
                       non-sandbox verification of X login; never open the result with
                       chrome-headless-shell (doing so reproduces cookie loss).
    --rollback         Restore moved directories back to their original locations from ~/.moni/chrome/

v1.0.59 incident guard:
    A headless-shell run against a migrated xwatch profile was observed to clear the
    x.com login cookies (274 -> 5, auth_token/ct0 gone). Migration was rolled back and
    the backup restored byte-for-byte. xwatch migration therefore remains opt-in.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from typing import Any


def get_real_user_home() -> Path:
    if platform.system() == "Darwin":
        try:
            import pwd

            return Path(pwd.getpwuid(os.getuid()).pw_dir)
        except Exception:
            pass
    return Path.home()


def get_default_chrome_dir() -> Path:
    if platform.system() == "Darwin":
        return get_real_user_home() / "Library" / "Application Support" / "Google" / "Chrome"
    return Path.home() / ".config" / "google-chrome"


def get_default_moni_chrome_dir() -> Path:
    return get_real_user_home() / ".moni" / "chrome"


def get_default_backup_dir() -> Path:
    today_str = datetime.datetime.now().strftime("%Y%m%d")
    return get_real_user_home() / "Documents" / f"moni-chrome-profile-backup-{today_str}"


def count_cookies(cookies_db_path: Path) -> tuple[int, int]:
    """Return (total_cookies, x_cookies). Returns (-1, -1) on error or (0, 0) if missing."""
    if not cookies_db_path.exists():
        return (0, 0)
    try:
        conn = sqlite3.connect(f"file:{cookies_db_path}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM cookies;")
        row = cursor.fetchone()
        total = row[0] if row else 0
        cursor.execute("SELECT count(*) FROM cookies WHERE host_key LIKE '%x.com%';")
        row_x = cursor.fetchone()
        x_count = row_x[0] if row_x else 0
        conn.close()
        return (total, x_count)
    except Exception:
        return (-1, -1)


def get_processes_holding_dir(path: Path) -> list[dict[str, str]]:
    """Check processes holding files in directory using lsof +D."""
    if not path.exists():
        return []
    try:
        proc = subprocess.run(
            ["lsof", "+D", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        lines = proc.stdout.strip().splitlines()
        if len(lines) <= 1:
            return []
        # Header is usually: COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
        results = []
        for line in lines[1:]:
            parts = line.split(None, 8)
            if len(parts) >= 2 and parts[1].isdigit():
                results.append({
                    "command": parts[0],
                    "pid": parts[1],
                    "user": parts[2] if len(parts) > 2 else "—",
                    "file": parts[-1] if len(parts) > 8 else "—",
                })
        return results
    except Exception:
        return []


def is_cache_entry(name: str) -> bool:
    """Check whether a directory name is transient cache to be excluded from backup."""
    cache_names = {
        "Service Worker",
        "GPUCache",
        "DawnGraphiteCache",
        "DawnWebGPUCache",
        "Cache",
        "Code Cache",
        "GrShaderCache",
        "GraphiteDawnCache",
    }
    return name in cache_names


def create_lean_tarball(source_dir: Path, tar_dest_path: Path, exclude_cache: bool = True) -> int:
    """Create a compressed tarball of source_dir, optionally excluding large cache folders."""
    tar_dest_path.parent.mkdir(parents=True, exist_ok=True)

    def tar_filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if not exclude_cache:
            return tarinfo
        # Check path parts for cache directories
        parts = Path(tarinfo.name).parts
        for p in parts:
            if is_cache_entry(p):
                return None
        return tarinfo

    with tarfile.open(tar_dest_path, "w:gz") as tar:
        tar.add(str(source_dir), arcname=source_dir.name, filter=tar_filter)

    return tar_dest_path.stat().st_size if tar_dest_path.exists() else 0


def format_size(bytes_num: int) -> str:
    if bytes_num <= 0:
        return "0B"
    if bytes_num < 1024:
        return f"{bytes_num}B"
    if bytes_num < 1024 * 1024:
        return f"{bytes_num / 1024:.0f}K"
    if bytes_num < 1024 * 1024 * 1024:
        return f"{bytes_num / (1024 * 1024):.0f}M"
    return f"{bytes_num / (1024 * 1024 * 1024):.1f}G"


def get_dir_size(dir_path: Path, exclude_caches: bool = False) -> int:
    total = 0
    if not dir_path.exists():
        return 0
    try:
        for entry in os.scandir(dir_path):
            try:
                if entry.is_symlink():
                    continue
                if exclude_caches and is_cache_entry(entry.name):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    total += get_dir_size(Path(entry.path), exclude_caches=exclude_caches)
                else:
                    total += entry.stat(follow_symlinks=False).st_size
            except (OSError, PermissionError):
                continue
    except (OSError, PermissionError):
        pass
    return total


class ChromeProfileMigrator:
    def __init__(
        self,
        chrome_dir: Path | None = None,
        moni_chrome_dir: Path | None = None,
        backup_dir: Path | None = None,
        force_ignore_locks: bool = False,
        include_xwatch: bool = False,
    ) -> None:
        self.chrome_dir = (chrome_dir or get_default_chrome_dir()).resolve()
        self.moni_chrome_dir = (moni_chrome_dir or get_default_moni_chrome_dir()).resolve()
        self.backup_dir = (backup_dir or get_default_backup_dir()).resolve()
        self.force_ignore_locks = force_ignore_locks
        self.include_xwatch = include_xwatch

        # Source paths
        self.p6_dir = self.chrome_dir / "Profile 6"
        self.p6_default = self.p6_dir / "Default"
        self.p6_local_state = self.p6_dir / "Local State"
        self.p7_dir = self.chrome_dir / "Profile 7"

        # Target paths
        self.target_xwatch = self.moni_chrome_dir / "xwatch"
        self.target_xwatch_default = self.target_xwatch / "Default"
        self.target_xwatch_local_state = self.target_xwatch / "Local State"
        self.target_daemon = self.moni_chrome_dir / "daemon"
        self.profile_map_file = self.moni_chrome_dir / "profile-map.json"

    def assess_status(self) -> dict[str, Any]:
        """Assess the state of source, target, and backup directories."""
        # Check source states
        p6_exists = self.p6_dir.exists() and self.p6_dir.is_dir()
        p6_default_exists = self.p6_default.exists() and self.p6_default.is_dir()
        p7_exists = self.p7_dir.exists() and self.p7_dir.is_dir()

        # Check target states
        xwatch_exists = self.target_xwatch_default.exists()
        daemon_exists = self.target_daemon.exists()
        map_exists = self.profile_map_file.exists()

        # Cookies check
        p6_root_cookies_total, _ = count_cookies(self.p6_dir / "Cookies") if p6_exists else (-1, -1)
        p6_default_cookies_total, p6_default_x_cookies = (
            count_cookies(self.p6_default / "Cookies") if p6_default_exists else (-1, -1)
        )
        p7_cookies_total, _ = (
            count_cookies(self.p7_dir / "Default" / "Cookies") if p7_exists else (-1, -1)
        )

        target_xwatch_cookies, target_xwatch_x_cookies = (
            count_cookies(self.target_xwatch_default / "Cookies") if xwatch_exists else (-1, -1)
        )
        target_daemon_cookies, _ = (
            count_cookies(self.target_daemon / "Default" / "Cookies") if daemon_exists else (-1, -1)
        )

        # Process locks check
        p6_default_procs = get_processes_holding_dir(self.p6_default) if p6_default_exists else []
        p7_procs = get_processes_holding_dir(self.p7_dir) if p7_exists else []
        target_xwatch_procs = get_processes_holding_dir(self.target_xwatch) if xwatch_exists else []
        target_daemon_procs = get_processes_holding_dir(self.target_daemon) if daemon_exists else []

        # Sizes
        p6_root_size = get_dir_size(self.p6_dir) if p6_exists else 0
        p6_default_size = get_dir_size(self.p6_default) if p6_default_exists else 0
        p6_default_lean_size = get_dir_size(self.p6_default, exclude_caches=True) if p6_default_exists else 0
        p7_size = get_dir_size(self.p7_dir) if p7_exists else 0
        p7_lean_size = get_dir_size(self.p7_dir, exclude_caches=True) if p7_exists else 0

        return {
            "p6_exists": p6_exists,
            "p6_root_size": p6_root_size,
            "p6_root_cookies_total": p6_root_cookies_total,
            "p6_default_exists": p6_default_exists,
            "p6_default_size": p6_default_size,
            "p6_default_lean_size": p6_default_lean_size,
            "p6_default_cookies_total": p6_default_cookies_total,
            "p6_default_x_cookies": p6_default_x_cookies,
            "p6_default_procs": p6_default_procs,
            "p6_local_state_exists": self.p6_local_state.exists(),
            "p7_exists": p7_exists,
            "p7_size": p7_size,
            "p7_lean_size": p7_lean_size,
            "p7_cookies_total": p7_cookies_total,
            "p7_procs": p7_procs,
            "target_xwatch_exists": xwatch_exists,
            "target_xwatch_cookies": target_xwatch_cookies,
            "target_xwatch_x_cookies": target_xwatch_x_cookies,
            "target_xwatch_procs": target_xwatch_procs,
            "target_daemon_exists": daemon_exists,
            "target_daemon_cookies": target_daemon_cookies,
            "target_daemon_procs": target_daemon_procs,
            "profile_map_exists": map_exists,
        }

    def dry_run(self) -> str:
        """Perform dry-run analysis and return a formatted human-readable report."""
        status = self.assess_status()
        lines = []
        lines.append("=" * 80)
        lines.append("moni Chrome Profile 迁移执行计划 (Dry Run)")
        lines.append("=" * 80)
        lines.append("")
        lines.append(f"• Chrome 根目录:    {self.chrome_dir}")
        lines.append(f"• moni 目标目录:   {self.moni_chrome_dir}")
        lines.append(f"• 预定备份目录:    {self.backup_dir}")
        lines.append("")

        lines.append("[1] 用户真实 Profile 6 (hermes) 安全检查:")
        if status["p6_exists"]:
            lines.append(f"    - Profile 6 根层状态: 存在 (大小 {format_size(status['p6_root_size'])})")
            lines.append(f"    - 用户 Cookies 数量:  {status['p6_root_cookies_total']} 个")
            lines.append("    - 边界隔离规则:      严格保护！Profile 6 根层数据（Bookmarks, History, Cookies）一字不动")
        else:
            lines.append("    - Profile 6 根层状态: 不存在 (未检测到)")
        lines.append("")

        lines.append("[2] 待搬迁的 moni 污染与专属目录:")
        if status["p6_default_exists"]:
            lines.append("    A. XWatch 登录态 (Profile 6/Default):")
            lines.append(f"       - 来源路径:       {self.p6_default}")
            lines.append(f"       - 目标路径:       {self.target_xwatch_default}")
            lines.append(f"       - 磁盘占用:       {format_size(status['p6_default_size'])} (排除 Service Worker 缓存后仅 ~{format_size(status['p6_default_lean_size'])})")
            lines.append(f"       - Cookies 总数:   {status['p6_default_cookies_total']} 个 (其中 x.com: {status['p6_default_x_cookies']} 个)")
            if status["p6_default_procs"]:
                pids = ", ".join(f"PID {p['pid']} ({p['command']})" for p in status["p6_default_procs"])
                lines.append(f"       - ⚠️ 进程占用:     {pids} 正在使用")
            else:
                lines.append("       - 进程占用状态:   空闲 (无 Chrome 进程占用)")
        elif status["target_xwatch_exists"]:
            lines.append("    A. XWatch 登录态: 已在目标位置 (~/.moni/chrome/xwatch/Default)")
            lines.append(f"       - Cookies 总数:   {status['target_xwatch_cookies']} 个 (其中 x.com: {status['target_xwatch_x_cookies']} 个)")
        else:
            lines.append("    A. XWatch 登录态: 未发现 Profile 6/Default 或目标目录")

        if status["p7_exists"]:
            lines.append("    B. Chrome Daemon (Profile 7 整个根目录):")
            lines.append(f"       - 来源路径:       {self.p7_dir}")
            lines.append(f"       - 目标路径:       {self.target_daemon}")
            lines.append(f"       - 磁盘占用:       {format_size(status['p7_size'])}")
            lines.append(f"       - 36kr Cookies:   {status['p7_cookies_total']} 个")
            if status["p7_procs"]:
                pids = ", ".join(f"PID {p['pid']} ({p['command']})" for p in status["p7_procs"])
                lines.append(f"       - ⚠️ 进程占用:     {pids} 正在使用")
            else:
                lines.append("       - 进程占用状态:   空闲 (无 Chrome 进程占用)")
        elif status["target_daemon_exists"]:
            lines.append("    B. Chrome Daemon: 已在目标位置 (~/.moni/chrome/daemon)")
            lines.append(f"       - Cookies 总数:   {status['target_daemon_cookies']} 个")
        else:
            lines.append("    B. Chrome Daemon: 未发现 Profile 7 或目标目录")

        lines.append("")
        lines.append("[3] 备份与搬迁计划:")
        if self.include_xwatch:
            lines.append(f"    - 备份文件 1: {self.backup_dir}/xwatch-profile6-default-backup.tar.gz (排除 1.2G Service Worker)")
        else:
            lines.append("    - XWatch 默认不搬迁 (v1.0.59 事故护栏)")
            lines.append("      如需搬迁, 必须显式加 --include-xwatch, 并在非沙箱 headed Chrome 中验证 X 登录。")
        lines.append(f"    - 备份文件 2: {self.backup_dir}/daemon-profile7-backup.tar.gz")
        lines.append(f"    - 元数据索引: {self.backup_dir}/backup-manifest.json")
        lines.append(f"    - Catalog 文件: {self.profile_map_file}")
        lines.append("")

        lines.append("[4] 安全结论与建议:")
        has_locks = bool(status["p6_default_procs"] or status["p7_procs"])
        if has_locks:
            lines.append("    ⚠️ 注意: 检测到有进程正在持有相关目录。")
            lines.append("       在最终执行 `--apply` 时必须先停止占用进程 (例如 moni 抓取/chrome_daemon)。")
        else:
            lines.append("    ✅ 路径与进程检查通过，具备安全搬迁条件。")

        if status["p6_default_exists"] or status["p7_exists"]:
            lines.append("    ℹ️ 当前为 DRY RUN 模式，未修改任何磁盘文件。")
            lines.append("    ℹ️ 待审计通过并确认后，可运行 `--apply` 执行实际搬迁。")
            if status["p6_default_exists"] and not self.include_xwatch:
                lines.append("    ⚠️ `--apply` 默认只搬迁 daemon (Profile 7)，不会搬 XWatch。")
            if self.include_xwatch:
                lines.append("    ⚠️ XWatch 搬迁已显式启用；禁止用 chrome-headless-shell 打开该 profile。")
        elif status["target_xwatch_exists"] and status["target_daemon_exists"]:
            lines.append("    ✅ 目标目录已存在且老目录已清理，系统已处于迁移完成状态。")
        lines.append("=" * 80)

        return "\n".join(lines)

    def apply(self) -> dict[str, Any]:
        """Perform the actual migration with safety checks and backup."""
        status = self.assess_status()

        # v1.0.59: xwatch is opt-in. A headless-shell navigation previously
        # cleared X login cookies, so the default --apply must not touch it.
        migrate_xwatch = self.include_xwatch
        if migrate_xwatch:
            try:
                from moni.fetch.chrome_guard import is_sandboxed

                sandboxed = is_sandboxed()
            except Exception:
                sandboxed = False
            if sandboxed:
                raise RuntimeError(
                    "xwatch profile migration is forbidden inside a sandbox. "
                    "Run outside the sandbox, then verify https://x.com/home in headed Chrome. "
                    "Never open the migrated xwatch profile with chrome-headless-shell."
                )

        # Idempotence check
        if not status["p6_default_exists"] and not status["p7_exists"]:
            if status["target_daemon_exists"] and (
                status["target_xwatch_exists"] or not migrate_xwatch
            ):
                return {
                    "success": True,
                    "message": "Already migrated. Target directories exist and legacy directories are clean.",
                    "already_migrated": True,
                }

        # Active processes check
        if not self.force_ignore_locks:
            blocking = []
            if status["p6_default_procs"]:
                blocking.append(f"Profile 6/Default (PIDs: {[p['pid'] for p in status['p6_default_procs']]})")
            if status["p7_procs"]:
                blocking.append(f"Profile 7 (PIDs: {[p['pid'] for p in status['p7_procs']]})")
            if blocking:
                raise RuntimeError(
                    f"Active processes are holding directories: {'; '.join(blocking)}. "
                    "Please stop moni services or Chrome before running --apply."
                )

        # Baseline metrics
        p6_def_total = status["p6_default_cookies_total"]
        p6_def_x = status["p6_default_x_cookies"]
        p7_total = status["p7_cookies_total"]
        p6_root_user_cookies = status["p6_root_cookies_total"]

        # Step 1: Backup
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        backed_up_files = []

        if status["p6_default_exists"] and migrate_xwatch:
            tar1 = self.backup_dir / "xwatch-profile6-default-backup.tar.gz"
            size1 = create_lean_tarball(self.p6_default, tar1, exclude_cache=True)
            backed_up_files.append({"path": str(tar1), "size": size1})

        if status["p6_local_state_exists"] and migrate_xwatch:
            b_ls = self.backup_dir / "xwatch-profile6-local-state.json"
            shutil.copy2(self.p6_local_state, b_ls)
            backed_up_files.append({"path": str(b_ls), "size": b_ls.stat().st_size})

        if status["p7_exists"]:
            tar2 = self.backup_dir / "daemon-profile7-backup.tar.gz"
            size2 = create_lean_tarball(self.p7_dir, tar2, exclude_cache=True)
            backed_up_files.append({"path": str(tar2), "size": size2})

        # Write backup manifest
        manifest_data = {
            "created_at": datetime.datetime.now().isoformat(),
            "chrome_dir": str(self.chrome_dir),
            "moni_chrome_dir": str(self.moni_chrome_dir),
            "baseline": {
                "p6_default_cookies_total": p6_def_total,
                "p6_default_x_cookies": p6_def_x,
                "p7_cookies_total": p7_total,
                "p6_root_user_cookies": p6_root_user_cookies,
            },
            "backups": backed_up_files,
        }
        with open(self.backup_dir / "backup-manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, ensure_ascii=False, indent=2)

        # Step 2: Relocate directories
        self.moni_chrome_dir.mkdir(parents=True, exist_ok=True)

        # Move Profile 6/Default -> ~/.moni/chrome/xwatch/Default
        if status["p6_default_exists"] and migrate_xwatch:
            self.target_xwatch.mkdir(parents=True, exist_ok=True)
            shutil.move(str(self.p6_default), str(self.target_xwatch_default))

        # Move Profile 6/Local State -> ~/.moni/chrome/xwatch/Local State
        if status["p6_local_state_exists"] and migrate_xwatch:
            shutil.move(str(self.p6_local_state), str(self.target_xwatch_local_state))

        # Clean stale Singleton symlinks in Profile 6 if any
        if status["p6_exists"]:
            for name in ("SingletonCookie", "SingletonLock", "SingletonSocket"):
                sym = self.p6_dir / name
                if sym.is_symlink():
                    try:
                        sym.unlink()
                    except Exception:
                        pass

        # Move Profile 7 -> ~/.moni/chrome/daemon
        if status["p7_exists"]:
            shutil.move(str(self.p7_dir), str(self.target_daemon))

        # Step 3: Write catalog profile-map.json
        profiles = {
            "daemon": {
                "name": "daemon",
                "target_dir": str(self.target_daemon),
                "legacy_user_data_dir": str(self.p7_dir),
                "total_cookies": p7_total,
            },
        }
        if migrate_xwatch:
            profiles["xwatch"] = {
                "name": "xwatch",
                "target_dir": str(self.target_xwatch),
                "legacy_user_data_dir": str(self.p6_dir),
                "legacy_profile_dir": "Default",
                "total_cookies": p6_def_total,
                "x_cookies": p6_def_x,
                "account": "matthew.qi.tao@gmail.com",
            }

        profile_map = {
            "version": 1,
            "migrated_at": datetime.datetime.now().isoformat(),
            "backup_dir": str(self.backup_dir),
            "profiles": profiles,
        }
        with open(self.profile_map_file, "w", encoding="utf-8") as f:
            json.dump(profile_map, f, ensure_ascii=False, indent=2)

        # Step 4: Verification
        new_xwatch_cookies, new_xwatch_x_cookies = count_cookies(self.target_xwatch_default / "Cookies")
        new_daemon_cookies, _ = count_cookies(self.target_daemon / "Default" / "Cookies")
        new_p6_root_cookies, _ = count_cookies(self.p6_dir / "Cookies") if status["p6_exists"] else (-1, -1)

        if migrate_xwatch and p6_def_total >= 0 and new_xwatch_cookies != p6_def_total:
            raise RuntimeError(
                f"Verification failed: xwatch cookies count mismatch! Expected {p6_def_total}, got {new_xwatch_cookies}"
            )
        if migrate_xwatch and p6_def_x >= 0 and new_xwatch_x_cookies != p6_def_x:
            raise RuntimeError(
                f"Verification failed: X cookies count mismatch! Expected {p6_def_x}, got {new_xwatch_x_cookies}"
            )
        if p7_total >= 0 and new_daemon_cookies != p7_total:
            raise RuntimeError(
                f"Verification failed: daemon cookies count mismatch! Expected {p7_total}, got {new_daemon_cookies}"
            )
        if p6_root_user_cookies >= 0 and new_p6_root_cookies != p6_root_user_cookies:
            raise RuntimeError(
                f"Verification failed: user hermes cookies count changed! Expected {p6_root_user_cookies}, got {new_p6_root_cookies}"
            )
        if migrate_xwatch and self.p6_default.exists():
            raise RuntimeError("Verification failed: Profile 6/Default still exists after move!")
        if self.p7_dir.exists():
            raise RuntimeError("Verification failed: Profile 7 still exists after move!")

        return {
            "success": True,
            "backup_dir": str(self.backup_dir),
            "xwatch_migrated": migrate_xwatch,
            "target_xwatch": str(self.target_xwatch) if migrate_xwatch else None,
            "target_daemon": str(self.target_daemon),
            "xwatch_cookies": new_xwatch_cookies,
            "xwatch_x_cookies": new_xwatch_x_cookies,
            "daemon_cookies": new_daemon_cookies,
            "user_p6_cookies": new_p6_root_cookies,
        }

    def rollback(self) -> dict[str, Any]:
        """Roll back migrated profiles from ~/.moni/chrome/ to original locations."""
        status = self.assess_status()

        # Safety check: ensure active processes aren't holding target directories
        if not self.force_ignore_locks:
            blocking = []
            if status["target_xwatch_procs"]:
                blocking.append(f"~/.moni/chrome/xwatch (PIDs: {[p['pid'] for p in status['target_xwatch_procs']]})")
            if status["target_daemon_procs"]:
                blocking.append(f"~/.moni/chrome/daemon (PIDs: {[p['pid'] for p in status['target_daemon_procs']]})")
            if blocking:
                raise RuntimeError(
                    f"Active processes are holding target directories: {'; '.join(blocking)}. "
                    "Please stop moni services or Chrome before running --rollback."
                )

        if not status["target_xwatch_exists"] and not status["target_daemon_exists"]:
            return {
                "success": True,
                "message": "Nothing to rollback: ~/.moni/chrome directories do not exist.",
            }

        # Move ~/.moni/chrome/xwatch/Default back to Profile 6/Default
        if status["target_xwatch_exists"]:
            self.p6_dir.mkdir(parents=True, exist_ok=True)
            if self.p6_default.exists():
                raise RuntimeError(f"Cannot rollback: original destination {self.p6_default} already exists!")
            shutil.move(str(self.target_xwatch_default), str(self.p6_default))

        # Move ~/.moni/chrome/xwatch/Local State back to Profile 6/Local State
        if self.target_xwatch_local_state.exists():
            shutil.move(str(self.target_xwatch_local_state), str(self.p6_local_state))

        # Clean ~/.moni/chrome/xwatch if empty
        if self.target_xwatch.exists():
            try:
                if not any(self.target_xwatch.iterdir()):
                    self.target_xwatch.rmdir()
            except Exception:
                pass

        # Move ~/.moni/chrome/daemon back to Profile 7
        if status["target_daemon_exists"]:
            if self.p7_dir.exists():
                raise RuntimeError(f"Cannot rollback: original destination {self.p7_dir} already exists!")
            shutil.move(str(self.target_daemon), str(self.p7_dir))

        # Remove profile-map.json
        if self.profile_map_file.exists():
            self.profile_map_file.unlink()

        # Verification after rollback
        restored_xwatch, restored_x_cookies = count_cookies(self.p6_default / "Cookies")
        restored_daemon, _ = count_cookies(self.p7_dir / "Default" / "Cookies")

        return {
            "success": True,
            "message": "Rollback completed successfully.",
            "restored_xwatch_cookies": restored_xwatch,
            "restored_xwatch_x_cookies": restored_x_cookies,
            "restored_daemon_cookies": restored_daemon,
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate moni Chrome profiles out of user Chrome directory."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="Print migration plan without changing files (default)")
    group.add_argument("--apply", action="store_true", help="Execute the migration")
    group.add_argument("--rollback", action="store_true", help="Roll back migrated directories to original paths")

    parser.add_argument("--chrome-dir", type=Path, default=None, help="Chrome root directory")
    parser.add_argument("--moni-chrome-dir", type=Path, default=None, help="Moni chrome destination directory")
    parser.add_argument("--backup-dir", type=Path, default=None, help="Backup destination directory")
    parser.add_argument("--force-ignore-locks", action="store_true", help="Bypass lsof locks check (testing only)")
    parser.add_argument(
        "--include-xwatch",
        action="store_true",
        help=(
            "Explicitly migrate xwatch Profile 6/Default (opt-in). Requires non-sandbox "
            "execution and headed Chrome login verification; never use chrome-headless-shell."
        ),
    )

    args = parser.parse_args()

    migrator = ChromeProfileMigrator(
        chrome_dir=args.chrome_dir,
        moni_chrome_dir=args.moni_chrome_dir,
        backup_dir=args.backup_dir,
        force_ignore_locks=args.force_ignore_locks,
        include_xwatch=args.include_xwatch,
    )

    if args.apply:
        try:
            res = migrator.apply()
            print("=" * 80)
            print("✅ Chrome Profile 迁移执行完成！")
            print("=" * 80)
            print(json.dumps(res, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"❌ 迁移失败: {e}", file=sys.stderr)
            return 1
    elif args.rollback:
        try:
            res = migrator.rollback()
            print("=" * 80)
            print("✅ Chrome Profile 回滚完成！")
            print("=" * 80)
            print(json.dumps(res, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"❌ 回滚失败: {e}", file=sys.stderr)
            return 1
    else:
        # Default is dry-run
        print(migrator.dry_run())

    return 0


if __name__ == "__main__":
    sys.exit(main())
