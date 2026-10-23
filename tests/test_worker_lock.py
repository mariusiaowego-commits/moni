"""tests/test_worker_lock.py — v1.0.48 worker 单实例锁.

背景
----
分享队列 worker 的单实例保证靠 ``$QUEUE_DIR/.lock`` 这把 PID 锁。
v1.0.48 审计发现两处真实竞态:

1. **bash 侧"先查后写"** —— ``[[ ! -e "$LOCK_FILE" ]]`` 和
   ``echo > "$LOCK_FILE"`` 之间有窗口期, 两个 worker 同时启动会同时
   通过检查、同时写进自己的 PID, 于是**两把锁都算拿到**, 队列被处理
   两遍 (同一批 share_task 被跑两次, LLM 成本翻倍)。
2. **python 侧 spawn 之后不验证** —— 两个并发请求会各自 spawn 一个
   子进程, 后到的那个在 bash 里立刻自杀 ("another instance is running
   — exiting"), 但接口照样返回 ``started=True`` 和一个**假 PID**,
   UI 显示"worker 已启动 (pid=xxx)"而那个进程早就没了。

修法:
- bash 侧改用 ``set -o noclobber`` 的原子创建 (bash 在 noclobber 下用
  O_EXCL 打开目标文件), 窗口期消失。
- python 侧在 spawn 之后**等子进程真的拿到锁**再下结论: 拿到 → started;
  别人拿着 → already_running + 对方的真实 PID; 子进程退出 → 按退出码
  判断成功/失败。

这些测试不碰真实的 ``~/.moni/queue`` —— 全部在 tmp_path 里跑。
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moni.share import worker as sw

WORKER_SCRIPT = REPO_ROOT / "scripts" / "default_worker.sh"

#: v1.0.49.1 (audit F2): the concurrency tests used to run a HAND-COPIED
#: copy of the lock block (``_LOCK_PROTOCOL``).  Reverting the shipped
#: script to the buggy version left those tests green — they were testing
#: the copy, not the product.  We now extract the live block from
#: ``scripts/default_worker.sh`` so the tests exercise what actually ships.
_LOCK_BLOCK_SENTINEL_START = "_acquired_lock=false"
_LOCK_BLOCK_SENTINEL_END = 'if ! "$_acquired_lock"; then'


def _extract_lock_block() -> str:
    """Return the real lock-acquisition block from the shipped script.

    The block runs from ``_acquired_lock=false`` through the closing ``fi``
    of the ``if ! "$_acquired_lock"; then … exit 1; fi`` guard — i.e. it
    includes the failure exit.  Extracting only up to the ``if`` line (as
    an earlier version did) leaves the harness with no failure branch, so
    every contender reaches the trailing ``echo WON`` and the test reports
    N winners no matter what the lock did.
    """
    lines = WORKER_SCRIPT.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index(_LOCK_BLOCK_SENTINEL_START)
    except ValueError:  # pragma: no cover - only if the script is refactored
        raise AssertionError(
            f"{WORKER_SCRIPT} no longer contains {_LOCK_BLOCK_SENTINEL_START!r} "
            "— update this extractor together with the script"
        )
    try:
        guard = next(
            i for i in range(start, len(lines))
            if lines[i].startswith(_LOCK_BLOCK_SENTINEL_END)
        )
    except StopIteration:  # pragma: no cover
        raise AssertionError(
            f"{WORKER_SCRIPT}: lock block has no terminating "
            f"{_LOCK_BLOCK_SENTINEL_END!r}"
        )
    try:
        end = lines.index("fi", guard)
    except ValueError:  # pragma: no cover
        raise AssertionError(
            f"{WORKER_SCRIPT}: the {_LOCK_BLOCK_SENTINEL_END!r} guard is unclosed"
        )
    return "\n".join(lines[start : end + 1])


@pytest.fixture(scope="module")
def lock_harness(tmp_path_factory) -> Path:
    """A runnable script wrapping the REAL lock block from the worker.

    The wrapper keeps the block verbatim, then reports the outcome and
    holds the lock briefly so a second contender has a window to (wrongly)
    win.  Regenerated per module from the live source, so any edit to the
    shipped block is picked up automatically.
    """
    harness = tmp_path_factory.mktemp("lockharness") / "try_lock.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        'LOCK_FILE="$1"\n'
        f"{_extract_lock_block()}\n"
        'echo WON\n'
        "sleep 0.5\n"
        'rm -f "$LOCK_FILE"\n',
        encoding="utf-8",
    )
    harness.chmod(0o755)
    return harness


@pytest.fixture
def queue_dir(monkeypatch, tmp_path):
    """Point the worker's queue/lock at a tmp dir — never the real ~/.moni."""
    monkeypatch.delenv("MONI_QUEUE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    # launch_worker() pre-flights DEFAULT_DB.parent.  In a clean checkout
    # the gitignored data/ directory does not exist, so point the default
    # at a real temp parent instead of relying on local state.
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(sw, "DEFAULT_DB", tmp_path / "data" / "news.db")
    qdir = tmp_path / ".moni" / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    return qdir


# ---------------------------------------------------------------------------
# AC-1 — the bash side must create the lock atomically
# ---------------------------------------------------------------------------


def test_bash_lock_uses_atomic_creation():
    """noclobber → O_EXCL: only one process can create the lock file."""
    code = _extract_lock_block()
    assert "set -o noclobber" in code, (
        "the worker lock is not created atomically — two workers can still "
        "both pass the existence check"
    )
    assert '[[ ! -e "$LOCK_FILE" ]]' not in code, (
        "the old check-then-write lock acquisition is still present"
    )
    # v1.0.49.1 (audit F1): the stale-reclaim path must not be an
    # unguarded `rm -f`, otherwise concurrent reclaimers delete each
    # other's fresh lock and all of them "win".
    assert ".reclaim" in code, (
        "the stale-lock reclaim path is not serialised — concurrent "
        "reclaimers can delete each other's lock and every one of them "
        "will believe it owns the worker"
    )


# ---------------------------------------------------------------------------
# AC-2 — the protocol actually admits exactly one winner
# ---------------------------------------------------------------------------


def test_lock_protocol_admits_exactly_one_winner(tmp_path, lock_harness):
    """8 processes race for the same lock — exactly one may win."""
    lock = tmp_path / ".lock"

    procs = [
        subprocess.Popen(
            ["/bin/bash", str(lock_harness), str(lock)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        for _ in range(8)
    ]
    results = []
    for p in procs:
        out, _ = p.communicate(timeout=30)
        results.append(out.strip())

    winners = [r for r in results if r == "WON"]
    # Losers exit from inside the lock block (exit 0 / exit 1) before the
    # harness's trailing `echo WON`, so their stdout is empty.  Assert on
    # the winner count plus "everyone else produced nothing", which is what
    # the shipped script actually does.
    assert len(winners) == 1, f"expected exactly 1 winner, got {results!r}"
    assert len(results) == 8


def test_lock_protocol_reclaims_a_stale_lock(tmp_path, lock_harness):
    """A dead PID in the lock file must not block a new worker forever."""
    lock = tmp_path / ".lock"
    lock.write_text("999999999")  # PID that cannot exist

    proc = subprocess.run(
        ["/bin/bash", str(lock_harness), str(lock)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.stdout.strip() == "WON", (
        f"a stale lock was not reclaimed: {proc.stdout!r} {proc.stderr!r}"
    )


def test_stale_reclaim_race_admits_exactly_one_winner(tmp_path, lock_harness):
    """v1.0.49.1 (audit F1) — the regression test the PR was missing.

    With a *stale* lock present, the unguarded ``rm -f`` reclaim let every
    contender delete the lock another contender had just created, so
    several of them reported success and each ran the queue in full
    (duplicate share_tasks → N× hermes calls).  Measured on the pre-fix
    code: 16 contenders produced up to 9 winners.

    The earlier version of this file only tested reclaim with a single
    worker, which cannot observe the race at all.
    """
    n = 12
    winners_by_round = []
    for _ in range(3):  # repeat: this is a race, one clean round proves little
        lock = tmp_path / ".lock"
        lock.write_text("999999999")  # PID that cannot exist
        procs = [
            subprocess.Popen(
                ["/bin/bash", str(lock_harness), str(lock)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for _ in range(n)
        ]
        outs = [p.communicate(timeout=60)[0].strip() for p in procs]
        winners_by_round.append(sum(1 for o in outs if o == "WON"))
        lock.unlink(missing_ok=True)
        (tmp_path / ".lock.reclaim").unlink(missing_ok=True)

    assert winners_by_round == [1, 1, 1], (
        "stale-lock reclaim admitted multiple winners "
        f"(winners per round: {winners_by_round}) — concurrent reclaimers "
        "are deleting each other's lock"
    )


# ---------------------------------------------------------------------------
# AC-3 — the python side must report what actually happened
# ---------------------------------------------------------------------------


def _fake_script(tmp_path) -> Path:
    script = tmp_path / "default_worker.sh"
    script.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def test_launch_reports_started_when_the_child_takes_the_lock(queue_dir, tmp_path):
    fake = _fake_script(tmp_path)
    with patch.object(sw, "_check_lock", return_value=None), \
         patch.object(sw, "WORKER_SCRIPT", fake), \
         patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value.pid = 4242
        mock_popen.return_value.poll.return_value = None
        (queue_dir / ".lock").write_text("4242")
        result = sw.launch_worker()

    assert result.started is True
    assert result.already_running is False
    assert result.pid == 4242
    assert result.error is None


def test_launch_reports_already_running_when_another_worker_holds_the_lock(
    queue_dir, tmp_path
):
    """The loser of the race must not claim "started" with a bogus PID."""
    fake = _fake_script(tmp_path)
    holder = os.getpid()  # a PID that is definitely alive
    with patch.object(sw, "_check_lock", return_value=None), \
         patch.object(sw, "WORKER_SCRIPT", fake), \
         patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value.pid = 4242
        mock_popen.return_value.poll.return_value = None
        (queue_dir / ".lock").write_text(str(holder))
        result = sw.launch_worker()

    assert result.started is False
    assert result.already_running is True
    assert result.pid == holder


def test_launch_reports_error_when_the_child_exits_non_zero(queue_dir, tmp_path):
    fake = _fake_script(tmp_path)
    with patch.object(sw, "_check_lock", return_value=None), \
         patch.object(sw, "WORKER_SCRIPT", fake), \
         patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value.pid = 4242
        mock_popen.return_value.poll.return_value = 3
        result = sw.launch_worker()

    assert result.started is False
    assert result.already_running is False
    assert result.error and "rc=3" in result.error


def test_launch_reports_started_when_the_child_is_alive_but_slow_to_lock(
    queue_dir, tmp_path, monkeypatch
):
    """A live child that misses the settle window is not a failed launch."""
    fake = _fake_script(tmp_path)
    monkeypatch.setattr(sw, "_LOCK_SETTLE_SECONDS", 0.01)
    with patch.object(sw, "_check_lock", return_value=None), \
         patch.object(sw, "WORKER_SCRIPT", fake), \
         patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value.pid = 4242
        mock_popen.return_value.poll.return_value = None
        result = sw.launch_worker()

    assert result.started is True
    assert result.already_running is False
    assert result.pid == 4242
    assert result.error is None


def test_check_lock_ignores_a_stale_pid(queue_dir):
    (queue_dir / ".lock").write_text("999999999")
    assert sw._check_lock() is None


def test_check_lock_reports_a_live_pid(queue_dir):
    (queue_dir / ".lock").write_text(str(os.getpid()))
    assert sw._check_lock() == os.getpid()


# ---------------------------------------------------------------------------
# AC-4 — python and bash must agree on where the queue lives
# ---------------------------------------------------------------------------


def test_queue_dir_honours_the_env_var(monkeypatch, tmp_path):
    """default_worker.sh resolves ${MONI_QUEUE_DIR:-...}; so must Python."""
    monkeypatch.setenv("MONI_QUEUE_DIR", str(tmp_path / "custom-queue"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert sw._queue_dir() == tmp_path / "custom-queue"
    assert sw._lock_file() == tmp_path / "custom-queue" / ".lock"


def test_queue_dir_falls_back_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("MONI_QUEUE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert sw._queue_dir() == tmp_path / ".moni" / "queue"


def test_worker_script_and_python_agree_on_the_lock_name():
    src = WORKER_SCRIPT.read_text(encoding="utf-8")
    assert 'LOCK_FILE="${QUEUE_DIR}/.lock"' in src
    assert sw._lock_file().name == ".lock"


def test_manifest_and_worker_resolve_the_same_custom_queue(monkeypatch, tmp_path):
    """A custom MONI_QUEUE_DIR must not split writers and the worker."""
    from moni.share import manifest as sm

    monkeypatch.setenv("MONI_QUEUE_DIR", str(tmp_path / "custom-queue"))
    assert sw._queue_dir() == sm.resolve_queue_dir()
    assert sw._queue_dir() == tmp_path / "custom-queue"
