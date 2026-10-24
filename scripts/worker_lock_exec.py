#!/usr/bin/env python3
"""Acquire the worker flock and exec the worker with the same open file.

The lock descriptor is deliberately passed as fd 9 across exec.  flock()
is associated with the open file description, not with a Python process,
so the bash process that replaces this helper keeps the lock until it
exits (or all descriptors referring to that description are closed).
"""

from __future__ import annotations

import fcntl
import os
import sys


LOCK_BUSY = 75


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: worker_lock_exec.py LOCK_FILE SCRIPT [args...]", file=sys.stderr)
        return 64

    lock_path, script = sys.argv[1], sys.argv[2]
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[worker] another instance is running — exiting", file=sys.stderr)
        os.close(fd)
        return LOCK_BUSY
    except OSError as exc:
        print(f"[worker] cannot lock {lock_path}: {exc}", file=sys.stderr)
        os.close(fd)
        return 74

    # fd 9 is inherited by bash and all of its children.  Do not close it
    # here: that would release the only reference to the flock.
    if fd != 9:
        os.dup2(fd, 9)
        os.close(fd)
    os.set_inheritable(9, True)
    os.lseek(9, 0, os.SEEK_SET)
    os.ftruncate(9, 0)
    os.write(9, f"{os.getpid()}\n".encode())

    env = os.environ.copy()
    env["MONI_WORKER_LOCK_ACQUIRED"] = "1"
    env["MONI_WORKER_LOCK_FD"] = "9"
    os.execve("/bin/bash", ["/bin/bash", script, *sys.argv[3:]], env)
    return 127  # pragma: no cover - execve only returns on failure


if __name__ == "__main__":
    raise SystemExit(main())
