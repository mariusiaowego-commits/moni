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


@pytest.fixture
def queue_dir(monkeypatch, tmp_path):
    """Point the worker's queue/lock at a tmp dir — never the real ~/.moni."""
    monkeypatch.delenv("MONI_QUEUE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    qdir = tmp_path / ".moni" / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    return qdir


# ---------------------------------------------------------------------------
# AC-1 — the bash side must create the lock atomically
# ---------------------------------------------------------------------------


def test_bash_lock_uses_atomic_creation():
    """noclobber → O_EXCL: only one process can create the lock file."""
    src = WORKER_SCRIPT.read_text(encoding="utf-8")
    # ignore comments — they document the old pattern
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    assert "set -o noclobber" in code, (
        "the worker lock is not created atomically — two workers can still "
        "both pass the existence check"
    )
    assert '[[ ! -e "$LOCK_FILE" ]]' not in code, (
        "the old check-then-write lock acquisition is still present"
    )


# ---------------------------------------------------------------------------
# AC-2 — the protocol actually admits exactly one winner
# ---------------------------------------------------------------------------


_LOCK_PROTOCOL = textwrap.dedent(
    """
    set -uo pipefail
    LOCK_FILE="$1"
    _acquired_lock=false
    if ( set -o noclobber; echo $$ > "$LOCK_FILE" ) 2>/dev/null; then
        _acquired_lock=true
    elif pid=$(cat "$LOCK_FILE" 2>/dev/null) && kill -0 "$pid" 2>/dev/null; then
        echo LOST
        exit 0
    else
        rm -f "$LOCK_FILE"
        if ( set -o noclobber; echo $$ > "$LOCK_FILE" ) 2>/dev/null; then
            _acquired_lock=true
        fi
    fi
    if ! "$_acquired_lock"; then
        echo LOST
        exit 0
    fi
    echo WON
    sleep 0.4
    """
)


def test_lock_protocol_admits_exactly_one_winner(tmp_path):
    """8 processes race for the same lock — exactly one may win."""
    lock = tmp_path / ".lock"
    script = tmp_path / "try_lock.sh"
    script.write_text(_LOCK_PROTOCOL, encoding="utf-8")

    procs = [
        subprocess.Popen(
            ["/bin/bash", str(script), str(lock)],
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
    losers = [r for r in results if r == "LOST"]
    assert len(winners) == 1, f"expected exactly 1 winner, got {results!r}"
    assert len(losers) == 7, f"expected 7 losers, got {results!r}"


def test_lock_protocol_reclaims_a_stale_lock(tmp_path):
    """A dead PID in the lock file must not block a new worker forever."""
    lock = tmp_path / ".lock"
    lock.write_text("999999999")  # PID that cannot exist
    script = tmp_path / "try_lock.sh"
    script.write_text(_LOCK_PROTOCOL, encoding="utf-8")

    proc = subprocess.run(
        ["/bin/bash", str(script), str(lock)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.stdout.strip() == "WON", (
        f"a stale lock was not reclaimed: {proc.stdout!r} {proc.stderr!r}"
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
