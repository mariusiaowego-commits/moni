"""tests/test_chrome_profile_migration.py — comprehensive tests for Chrome profile migration script."""

import json
from pathlib import Path
import pytest
import sqlite3
import sys
import tarfile

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "scripts"))

from importlib import import_module
migrate_mod = import_module("migrate-chrome-profiles")
ChromeProfileMigrator = migrate_mod.ChromeProfileMigrator
count_cookies = migrate_mod.count_cookies
create_lean_tarball = migrate_mod.create_lean_tarball


def create_fake_cookies_db(db_path: Path, cookies: list[tuple[str, str, str]]) -> None:
    """Create a mock SQLite cookies DB with host_key, name, and value."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, path TEXT);")
    for host_key, name, val in cookies:
        cur.execute(
            "INSERT INTO cookies (host_key, name, value, path) VALUES (?, ?, ?, '/');",
            (host_key, name, val),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def mock_migration_env(tmp_path):
    """Setup a realistic Chrome structure with Profile 6 (user + xwatch) and Profile 7 (daemon)."""
    chrome_dir = tmp_path / "Google" / "Chrome"
    chrome_dir.mkdir(parents=True)
    moni_chrome_dir = tmp_path / ".moni" / "chrome"
    backup_dir = tmp_path / "backups"

    # User's Profile 6 root
    p6_dir = chrome_dir / "Profile 6"
    p6_dir.mkdir()
    (p6_dir / "Bookmarks").write_text('{"roots": {}}', encoding="utf-8")
    (p6_dir / "History").write_bytes(b"history data")
    (p6_dir / "Preferences").write_text('{"account": "hermes"}', encoding="utf-8")

    # User's hermes cookies (338 cookies)
    p6_user_cookies = [(".google.com", f"cookie_{i}", f"val_{i}") for i in range(338)]
    create_fake_cookies_db(p6_dir / "Cookies", p6_user_cookies)

    # Moni's xwatch pollution in Profile 6/Default (274 cookies, 17 x.com)
    p6_default = p6_dir / "Default"
    p6_default.mkdir()
    (p6_default / "Preferences").write_text('{"profile": "xwatch"}', encoding="utf-8")
    (p6_default / "IndexedDB").mkdir()
    (p6_default / "IndexedDB" / "test.db").write_bytes(b"indexeddb")
    (p6_default / "Service Worker").mkdir()
    (p6_default / "Service Worker" / "cache.dat").write_bytes(b"huge cache" * 500)

    # 17 x.com cookies + 257 other cookies = 274 cookies
    xwatch_cookies = [(".x.com", f"auth_token_{i}", f"tok_{i}") for i in range(17)]
    xwatch_cookies += [(".twitter.com", f"tw_{i}", f"val_{i}") for i in range(257)]
    create_fake_cookies_db(p6_default / "Cookies", xwatch_cookies)

    # Profile 6 Local State (moni created)
    p6_local_state = p6_dir / "Local State"
    p6_local_state.write_text('{"profile": {"info_cache": {"Default": {}}}}', encoding="utf-8")

    # Moni's Profile 7 (daemon, 2 cookies)
    p7_dir = chrome_dir / "Profile 7"
    p7_dir.mkdir()
    (p7_dir / "Local State").write_text('{"profile": {}}', encoding="utf-8")
    p7_default = p7_dir / "Default"
    p7_default.mkdir()
    daemon_cookies = [(".36kr.com", "session_id", "s1"), (".36kr.com", "token", "t1")]
    create_fake_cookies_db(p7_default / "Cookies", daemon_cookies)

    return {
        "chrome_dir": chrome_dir,
        "moni_chrome_dir": moni_chrome_dir,
        "backup_dir": backup_dir,
        "p6_dir": p6_dir,
        "p6_default": p6_default,
        "p7_dir": p7_dir,
    }


# ── Acceptance 1: Dry-run across 4 states ────────────────────────────────────


def test_dry_run_case_1_both_exist(mock_migration_env):
    """Case 1: Both old directories and new directories exist."""
    env = mock_migration_env
    # Pre-create target directories as well
    (env["moni_chrome_dir"] / "xwatch" / "Default").mkdir(parents=True)
    (env["moni_chrome_dir"] / "daemon").mkdir(parents=True)

    migrator = ChromeProfileMigrator(
        chrome_dir=env["chrome_dir"],
        moni_chrome_dir=env["moni_chrome_dir"],
        backup_dir=env["backup_dir"],
        force_ignore_locks=True,
    )
    report = migrator.dry_run()
    assert "Dry Run" in report
    assert "Profile 6 根层数据" in report
    assert "来源路径" in report
    assert "目标路径" in report


def test_dry_run_case_2_only_old_exists(mock_migration_env):
    """Case 2: Only old directories exist (pre-migration state)."""
    env = mock_migration_env

    migrator = ChromeProfileMigrator(
        chrome_dir=env["chrome_dir"],
        moni_chrome_dir=env["moni_chrome_dir"],
        backup_dir=env["backup_dir"],
        force_ignore_locks=True,
    )
    report = migrator.dry_run()
    assert "Profile 6 根层状态: 存在" in report
    assert "用户 Cookies 数量:  338 个" in report
    assert "Cookies 总数:   274 个 (其中 x.com: 17 个)" in report
    assert "36kr Cookies:   2 个" in report
    assert "DRY RUN 模式，未修改任何磁盘文件" in report


def test_dry_run_case_3_only_new_exists(mock_migration_env):
    """Case 3: Only new directories exist (post-migration state)."""
    env = mock_migration_env
    # Remove old moni directories, populate new
    import shutil
    shutil.rmtree(env["p6_default"])
    shutil.rmtree(env["p7_dir"])

    new_xwatch_def = env["moni_chrome_dir"] / "xwatch" / "Default"
    new_xwatch_def.mkdir(parents=True)
    create_fake_cookies_db(new_xwatch_def / "Cookies", [(".x.com", "auth", "1")])

    new_daemon = env["moni_chrome_dir"] / "daemon" / "Default"
    new_daemon.mkdir(parents=True)
    create_fake_cookies_db(new_daemon / "Cookies", [(".36kr.com", "auth", "1")])

    migrator = ChromeProfileMigrator(
        chrome_dir=env["chrome_dir"],
        moni_chrome_dir=env["moni_chrome_dir"],
        backup_dir=env["backup_dir"],
        force_ignore_locks=True,
    )
    report = migrator.dry_run()
    assert "已在目标位置" in report
    assert "系统已处于迁移完成状态" in report


def test_dry_run_case_4_neither_exists(tmp_path):
    """Case 4: Neither old nor new directories exist."""
    chrome_dir = tmp_path / "empty_chrome"
    moni_chrome_dir = tmp_path / "empty_moni"
    backup_dir = tmp_path / "empty_backups"

    migrator = ChromeProfileMigrator(
        chrome_dir=chrome_dir,
        moni_chrome_dir=moni_chrome_dir,
        backup_dir=backup_dir,
        force_ignore_locks=True,
    )
    report = migrator.dry_run()
    assert "Profile 6 根层状态: 不存在" in report
    assert "未发现 Profile 6/Default" in report
    assert "未发现 Profile 7" in report


# ── Acceptance 2: Apply migration with tmp_path ──────────────────────────────


def test_apply_migrates_moni_profiles_and_preserves_user_profile(mock_migration_env):
    env = mock_migration_env
    migrator = ChromeProfileMigrator(
        chrome_dir=env["chrome_dir"],
        moni_chrome_dir=env["moni_chrome_dir"],
        backup_dir=env["backup_dir"],
        force_ignore_locks=True,
    )

    result = migrator.apply()
    assert result["success"] is True
    assert result["xwatch_cookies"] == 274
    assert result["xwatch_x_cookies"] == 17
    assert result["daemon_cookies"] == 2
    assert result["user_p6_cookies"] == 338

    # 1. Verify user's Profile 6 root is untouched
    assert env["p6_dir"].exists()
    assert (env["p6_dir"] / "Bookmarks").exists()
    assert (env["p6_dir"] / "History").exists()
    assert (env["p6_dir"] / "Preferences").exists()
    user_cookies_cnt, _ = count_cookies(env["p6_dir"] / "Cookies")
    assert user_cookies_cnt == 338

    # 2. Verify Profile 6/Default is GONE
    assert not env["p6_default"].exists()

    # 3. Verify Profile 7 is GONE from Chrome root
    assert not env["p7_dir"].exists()

    # 4. Verify target directory ~/.moni/chrome/xwatch/Default exists with cookies
    target_xwatch_cookies = env["moni_chrome_dir"] / "xwatch" / "Default" / "Cookies"
    assert target_xwatch_cookies.exists()
    tot, x_cnt = count_cookies(target_xwatch_cookies)
    assert tot == 274
    assert x_cnt == 17

    # 5. Verify target directory ~/.moni/chrome/daemon exists with cookies
    target_daemon_cookies = env["moni_chrome_dir"] / "daemon" / "Default" / "Cookies"
    assert target_daemon_cookies.exists()
    tot_d, _ = count_cookies(target_daemon_cookies)
    assert tot_d == 2

    # 6. Verify profile-map.json exists and is valid
    map_file = env["moni_chrome_dir"] / "profile-map.json"
    assert map_file.exists()
    map_data = json.loads(map_file.read_text(encoding="utf-8"))
    assert map_data["profiles"]["xwatch"]["total_cookies"] == 274
    assert map_data["profiles"]["xwatch"]["x_cookies"] == 17
    assert map_data["profiles"]["daemon"]["total_cookies"] == 2

    # 7. Verify backup tarball was created and contains files
    assert env["backup_dir"].exists()
    assert (env["backup_dir"] / "backup-manifest.json").exists()
    tar_xwatch = env["backup_dir"] / "xwatch-profile6-default-backup.tar.gz"
    assert tar_xwatch.exists()
    with tarfile.open(tar_xwatch, "r:gz") as tar:
        names = tar.getnames()
        # Cookies and Preferences must be in backup
        assert any("Cookies" in n for n in names)
        assert any("Preferences" in n for n in names)
        # Service Worker must NOT be in backup
        assert not any("Service Worker" in n for n in names)

    # 8. Idempotence: applying again should report already_migrated
    res2 = migrator.apply()
    assert res2["already_migrated"] is True


# ── Acceptance 3: Rollback restores to original state ────────────────────────


def test_rollback_restores_pre_migration_state(mock_migration_env):
    env = mock_migration_env
    migrator = ChromeProfileMigrator(
        chrome_dir=env["chrome_dir"],
        moni_chrome_dir=env["moni_chrome_dir"],
        backup_dir=env["backup_dir"],
        force_ignore_locks=True,
    )

    # 1. Apply first
    migrator.apply()
    assert not env["p6_default"].exists()
    assert not env["p7_dir"].exists()

    # 2. Rollback
    rb_res = migrator.rollback()
    assert rb_res["success"] is True
    assert rb_res["restored_xwatch_cookies"] == 274
    assert rb_res["restored_xwatch_x_cookies"] == 17
    assert rb_res["restored_daemon_cookies"] == 2

    # 3. Verify original locations restored
    assert env["p6_default"].exists()
    assert (env["p6_default"] / "Cookies").exists()
    assert env["p7_dir"].exists()
    assert (env["p7_dir"] / "Default" / "Cookies").exists()

    # 4. Verify user cookies remain intact
    user_cookies_cnt, _ = count_cookies(env["p6_dir"] / "Cookies")
    assert user_cookies_cnt == 338

    # 5. Verify catalog file removed
    assert not (env["moni_chrome_dir"] / "profile-map.json").exists()


# ── Acceptance: Lean tarball excludes cache ─────────────────────────────────


def test_create_lean_tarball_excludes_cache(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "keep.txt").write_text("keep me")
    (source / "Service Worker").mkdir()
    (source / "Service Worker" / "cache.bin").write_text("cache data")

    dest_tar = tmp_path / "out.tar.gz"
    create_lean_tarball(source, dest_tar, exclude_cache=True)

    with tarfile.open(dest_tar, "r:gz") as tar:
        names = tar.getnames()
        assert any("keep.txt" in n for n in names)
        assert not any("Service Worker" in n for n in names)
