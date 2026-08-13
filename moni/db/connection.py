"""SQLite singleton connection for moni — thread-safe lifecycle.

The DB file lives at data/news.db (gitignored).  Call get_db() to get
the open sqlite3.Connection.  It is kept open for the lifetime of the
process; close it with close_db().

Why this design (fix for intermittent SIGSEGV, Python 3.12):

The previous implementation closed the connection immediately on
close_db() and reset the module singleton, which let the GC destroy the
sqlite3.Connection (pysqlite_connection_close -> stmt/lru_cache dealloc)
while a background thread was still executing SQL on it.  That
use-after-free crashed the interpreter with EXC_BAD_ACCESS (SIGSEGV,
KERN_INVALID_ADDRESS at 0x8) in _pysqlite_query_execute.

Mitigations in this version:

* The singleton connection is created under a lock so concurrent first
  calls never race.
* close_db() never destroys a connection that another thread may still
  be executing on.  It only *retires* the current connection (future
  get_db() calls build a fresh one) and defers the actual close to
  process exit (atexit), when no user threads are running.  Old
  connections are kept referenced (deque) so the GC can't touch them
  mid-execute either.
* check_same_thread=False stays: the connection is shared across
  threads by design.  Concurrent execute() calls are serialized by the
  GIL; the dangerous race (close vs execute) is eliminated above.

WAL mode + busy_timeout keep cross-connection (and cross-process,
e.g. the 8900/8901 dev-prod pair) writes from deadlocking.
"""

from __future__ import annotations

import atexit
import os
import sqlite3
import threading
from collections import deque
from pathlib import Path
from typing import Optional

_DB_PATH: Optional[Path] = None
_CON: Optional[sqlite3.Connection] = None
_CREATE_LOCK = threading.Lock()

#: Retired connections awaiting process-exit close.  Bounded small so a
#: long test run (one close_db per test) can't exhaust file descriptors
#: (each open SQLite conn holds ~3 fds; macOS default ulimit is 256).
#: Anything popped is many close_db() calls old — far past any in-flight
#: execute, so closing it is safe.
_RETIRED: deque[sqlite3.Connection] = deque(maxlen=8)


def _close_retired() -> None:
    while _RETIRED:
        try:
            _RETIRED.popleft().close()
        except Exception:  # noqa: BLE001 — best effort at shutdown
            pass


atexit.register(_close_retired)


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


def _new_connection() -> sqlite3.Connection:
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    # WAL is persistent per-db file; only switch when not already WAL.
    try:
        mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        if mode != "wal":
            con.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass  # another connection is mid-switch; WAL persists once set
    # busy_timeout + foreign_keys are per-connection, set every time
    con.execute("PRAGMA busy_timeout = 5000")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def get_db() -> sqlite3.Connection:
    """Return the singleton sqlite3.Connection, creating it if necessary.

    Enables:
    - WAL mode for better concurrent read performance
    - busy_timeout so connections wait up to 5 s for locks before raising
    - foreign keys enforcement
    """
    global _CON
    if _CON is None:
        with _CREATE_LOCK:
            if _CON is None:
                _CON = _new_connection()
    return _CON


def close_db() -> None:
    """Retire the singleton connection; the next get_db() reopens fresh.

    The old connection is NOT closed here — another thread may still be
    executing on it, and closing mid-execute is exactly the race that
    caused the SIGSEGV.  It is kept referenced and closed at process
    exit (see _close_retired).
    """
    global _CON
    if _CON is not None:
        _RETIRED.append(_CON)
        _CON = None
