"""回填脚本共用的连接与备份 helper。

三条回填链路（taxonomy / evidence / metrics）共用同一套纪律：
dry-run 只读、apply 前自动热备、连接带 busy_timeout 与外键开关。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

BUSY_TIMEOUT_MS = 30_000
BACKUP_DIR_NAME = "backups"


def connect(db_path: Path | str, *, apply: bool) -> sqlite3.Connection:
    """按模式打开回填用的连接。

    dry-run 用只读 URI（不会产生 -wal/-journal 副作用）；apply 用读写连接，
    两者都设 busy_timeout，避免与 8900 / cron 并发写库时立刻 database is locked。
    """
    if apply:
        conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_MS / 1000)
    else:
        conn = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def backup_database(db_path: Path | str, *, tag: str = "backfill") -> Path:
    """WAL 安全在线热备（sqlite3 backup API），返回备份文件路径。"""
    db_path = Path(db_path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest_dir = db_path.parent / BACKUP_DIR_NAME
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{db_path.name}.pre-{tag}-{stamp}"
    src = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_MS / 1000)
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest
