"""SQLite connections for moni — thread-local, thread-safe.

The DB file lives at data/news.db (gitignored).  Call get_db() to get
an open sqlite3.Connection bound to the *calling thread*.

Why thread-local (fix for v1.0.37 prod crash):

The previous design shared ONE singleton connection across all threads
(check_same_thread=False) with the claim that "concurrent execute()
calls are serialized by the GIL".  That claim is wrong: the GIL only
serializes individual bytecodes, not a full statement lifecycle.  When
two FastAPI worker threads execute() on the same sqlite3.Connection
interleaved (one thread's statement binding params while the other
thread runs its own execute), sqlite3 raises:

    sqlite3.InterfaceError: bad parameter or other API misuse

observed on /api/cases/<id>/keywords under concurrent load in the
8900 prod process (v1.0.37).

New design:

* get_db() returns a connection owned by the calling thread
  (threading.local).  Each thread builds its own connection lazily
  under a lock, so first-call races are impossible.
* close_db() closes only the current thread's connection and retires
  it; other threads' connections are left open (bounded by the
  threadpool size, ~40 in FastAPI — well under macOS fd limits).
* check_same_thread stays default True per-thread ownership; the
  dangerous cross-thread execute interleave is structurally gone.
* WAL mode + busy_timeout keep cross-connection (and cross-process,
  e.g. the 8900/8901 dev-prod pair) writes from deadlocking.

Historical note: the pre-thread-local close_db() used to destroy the
connection while a background thread was mid-execute, crashing the
interpreter with EXC_BAD_ACCESS (SIGSEGV).  Thread-local ownership
eliminates that race class entirely.
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
_CREATE_LOCK = threading.Lock()

#: Thread-local connections: each thread gets its own sqlite3.Connection.
_LOCAL = threading.local()

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
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    # WAL is persistent per-db file; only switch when not already WAL.
    try:
        mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        if mode != "wal":
            con.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass  # another connection is mid-switch; WAL persists once set
    # busy_timeout + foreign_keys are per-connection, set every time.
    # 5 s is enough for production where write traffic is single-actor;
    # tests that race the FastAPI TestClient anyio worker threadpool
    # must ``commit()`` their DML before any ``client.*`` call, or the
    # worker waits the full 5 s for the dangling write lock.  Adding
    # busy_timeout in tests is not the answer — see v1.0.37.5 commit
    # message for the recipe.
    con.execute("PRAGMA busy_timeout = 5000")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def get_db() -> sqlite3.Connection:
    """Return a sqlite3.Connection owned by the calling thread.

    Each thread lazily builds its own connection (under a lock, so
    concurrent first calls never race).  Thread-local ownership makes
    cross-thread execute() interleaving — the v1.0.37 prod
    sqlite3.InterfaceError — structurally impossible.

    Path-aware self-healing: a thread that reused a worker slot from a
    prior pytest test (anyio threadpool, FastAPI TestClient) may still
    hold a connection pointing at the old temporary db file.  We track
    the path the cached connection was built against and rebuild if
    get_db_path() has moved (e.g. fixture switched dbs).  Closes the
    "no such table" / cross-test contamination window surfaced after
    v1.0.37.1.

    WAL mode + busy_timeout make multi-connection writes safe.
    """
    curr_path = get_db_path()
    conn = getattr(_LOCAL, "conn", None)
    cached_path = getattr(_LOCAL, "db_path", None)
    if conn is not None and cached_path != curr_path:
        # Stale connection from a prior test/fixture — drop it, build fresh.
        _RETIRED.append(conn)
        _LOCAL.conn = None
        conn = None
    if conn is None:
        with _CREATE_LOCK:
            conn = getattr(_LOCAL, "conn", None)
            cached_path = getattr(_LOCAL, "db_path", None)
            if conn is not None and cached_path != curr_path:
                _RETIRED.append(conn)
                _LOCAL.conn = None
                conn = None
            if conn is None:
                conn = _new_connection()
                _LOCAL.conn = conn
                _LOCAL.db_path = curr_path
    return conn


def close_db() -> None:
    """Close this thread's connection; the next get_db() rebuilds fresh.

    Best-effort rollback any uncommitted DML (releases the write lock
    synchronously, no busy_timeout wait), then ``.close()`` to free
    the file descriptor.  The closed Connection goes to ``_RETIRED``
    for the bounded burst case where another thread might still hold
    a reference; ``_close_retired`` is called at process exit to drain
    any straggler connections (other threads' that this thread never
    touched).
    """
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None:
        try:
            try:
                conn.rollback()
            except sqlite3.OperationalError:
                pass
            conn.close()
        except Exception:  # noqa: BLE001 — best-effort close
            pass
        _RETIRED.append(conn)
        _LOCAL.conn = None
