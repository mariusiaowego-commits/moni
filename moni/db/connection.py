"""SQLite singleton connection for moni.

The DB file lives at data/news.db (gitignored).  Call get_db() to get
the open sqlite3.Connection.  It is kept open for the lifetime of the
process (single-threaded CLI usage); close it with close_db().
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

_DB_PATH: Optional[Path] = None
_CON: Optional[sqlite3.Connection] = None


def set_db_path(path: Path) -> None:
    """Set the path to the SQLite file before any connection is opened."""
    global _DB_PATH
    _DB_PATH = path


def get_db_path() -> Path:
    """Return the configured DB path.

    Resolution order:
    1. Explicitly set via set_db_path() (unit tests)
    2. MONI_DB_PATH environment variable (integration tests, container deployments)
    3. <MONI_DATA_DIR>/news.db if MONI_DATA_DIR is set
    4. data/news.db relative to cwd (default for local dev)
    """
    if _DB_PATH is not None:
        return _DB_PATH
    env_path = os.environ.get("MONI_DB_PATH")
    if env_path:
        return Path(env_path)
    data_dir = os.environ.get("MONI_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "news.db"
    return Path("data") / "news.db"


def get_db() -> sqlite3.Connection:
    """Return the singleton sqlite3.Connection, creating it if necessary.

    Enables:
    - WAL mode for better concurrent read performance
    - busy_timeout so connections wait up to 5 s for locks before raising
    - foreign keys enforcement
    """
    global _CON
    if _CON is None:
        path = get_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _CON = sqlite3.connect(str(path), check_same_thread=False)
        _CON.row_factory = sqlite3.Row
        # WAL mode + busy_timeout for v1.0 single-instance locking
        _CON.execute("PRAGMA journal_mode = WAL")
        _CON.execute("PRAGMA busy_timeout = 5000")
        # Enable foreign keys (SQLite default is OFF)
        _CON.execute("PRAGMA foreign_keys = ON")
    return _CON


def close_db() -> None:
    """Close the singleton connection."""
    global _CON
    if _CON is not None:
        _CON.close()
        _CON = None
