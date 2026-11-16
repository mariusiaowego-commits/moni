"""tests/test_worker_lock.py — worker 单实例 flock.

背景
----
v1.0.49.4 起，分享队列 worker 的单实例保证靠 ``$QUEUE_DIR/.lock``
上的内核 ``flock``。旧版用 PID 文件、目录 guard 和“先查后写”模拟
互斥，审计实测可产生多个赢家；内核锁随 open file description 存活，
进程退出即释放，因此不存在 stale 回收窗口。

测试必须走真实入口 ``scripts/default_worker.sh``：它自己 re-exec
``worker_lock_exec.py``，helper 用 LOCK_EX|LOCK_NB 拿锁，把 fd 9 设为
inheritable 后 exec bash。测试不得复制锁协议，否则失败实现仍可能全绿。

这些测试不碰真实的 ``~/.moni/queue`` —— 全部在 tmp_path 里跑。
"""

from __future__ import annotations

import fcntl
import os
import re
import signal
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moni.share import worker as sw

WORKER_SCRIPT = REPO_ROOT / "scripts" / "default_worker.sh"


def _watchdog_grace_seconds() -> float:
    """从 worker 脚本里读出看门狗的 grace 秒数 (默认 0.8)。

    `_stop_child_group()` 用 `sleep <grace>` + SIGKILL 去收拾**忽略 TERM**
    的子进程 → TERM 到退出的耗时**下界**就是这个 grace。

    测试的 TERM 预算必须由它推导, 不能写死 —— 两处数字一旦漂移
    (比如有人把 grace 调大), 写死的预算就会变成假红 (见
    test_sigterm_kills_blocking_child_that_ignores_term 的注释)。

    解析不到就退回 0.8 (当前脚本里的值), 不让测试因为脚本格式变动而崩。
    """
    try:
        src = WORKER_SCRIPT.read_text(encoding="utf-8")
    except OSError:
        return 0.8
    # 只认看门狗那一处 —— 脚本里现在**只有**这一处 `sleep <数字>`, 但
    # 以后有人在别处加个 sleep 的话, 宽泛的正则会取错值 → 预算算错。
    # 所以锚在 `kill -KILL` 之前的那段 watch dog 块上。
    m = re.search(r"sleep\s+([0-9]+(?:\.[0-9]+)?)\s*\n\s*kill\s+-KILL", src)
    if m:
        return float(m.group(1))
    m = re.search(r"sleep\s+([0-9]+(?:\.[0-9]+)?)", src)
    return float(m.group(1)) if m else 0.8

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


def _worker_env(queue: Path, tmp_path: Path) -> dict[str, str]:
    """Minimal env for invoking the real worker in an isolated tmp tree."""
    env = dict(
        os.environ,
        HOME=str(tmp_path),
        MONI_QUEUE_DIR=str(queue),
        MONI_DB=str(tmp_path / "news.db"),
        MONI_WORKER_LOG=str(tmp_path / "worker.log"),
        ARTIFACTS_DIR=str(tmp_path / "artifacts"),
    )
    # The test process's stdin/stdout may be pipes.  The worker's child
    # process must not inherit a pipe that the test closes while waiting.
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _lock_is_free(path: Path) -> bool:
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return True
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return True
    finally:
        os.close(fd)


def _hold_flock(path: Path) -> subprocess.Popen:
    code = "import fcntl,os,time;fd=os.open(%r,os.O_RDWR|os.O_CREAT,0o600);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);print('ready',flush=True);time.sleep(30)" % str(path)
    p = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert p.stdout is not None
    assert p.stdout.readline().strip() == "ready"
    return p


# ---------------------------------------------------------------------------
# AC-1 — the shipped worker must use a kernel lock
# ---------------------------------------------------------------------------


def test_worker_uses_flock_not_directory_guard():
    """The authoritative primitive must be flock; guard/claim must be gone."""
    src = WORKER_SCRIPT.read_text(encoding="utf-8")
    helper = (REPO_ROOT / "scripts" / "worker_lock_exec.py").read_text(encoding="utf-8")
    assert "fcntl.flock" in helper
    assert "os.set_inheritable(9, True)" in helper
    assert 'os.execve("/bin/bash"' in helper
    assert "worker_lock_exec.py" in src
    assert "MONI_WORKER_LOCK_ACQUIRED" in src
    assert ".lock.reclaim.d" not in src
    assert "claim.$$" not in src


# ---------------------------------------------------------------------------
# AC-2 — the protocol actually admits exactly one winner
# ---------------------------------------------------------------------------


def test_lock_protocol_admits_exactly_one_winner(tmp_path):
    """Direct helper race: exactly one executable holder for 24 contenders."""
    lock = tmp_path / ".lock"
    harness = tmp_path / "hold.sh"
    harness.write_text(
        "#!/bin/bash\n"
        "echo START $$ $(cat \"$1\")\n"
        "sleep 0.25\n"
        "echo END $$\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    procs = [
        subprocess.Popen(
            [sys.executable, str(REPO_ROOT / "scripts" / "worker_lock_exec.py"),
             str(lock), str(harness)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(24)
    ]
    outputs = [p.communicate(timeout=15)[0].strip() for p in procs]
    starts = [line for out in outputs for line in out.splitlines() if line.startswith("START")]
    ends = [line for out in outputs for line in out.splitlines() if line.startswith("END")]
    assert len(starts) == 1, outputs
    assert len(ends) == 1, outputs
    assert [p.returncode for p in procs].count(75) == 23


def test_lock_protocol_barrier_admits_exactly_one_winner(tmp_path):
    """After acquiring the lock, contenders hold a barrier before work.

    All 16 processes reach the helper at once.  The winner creates ENTER
    and waits for a release file; losers get rc=75.  This proves that no
    second contender can pass the lock while the critical section is open,
    rather than merely observing a short winner.
    """
    lock = tmp_path / ".lock"
    entered = tmp_path / "entered"
    release = tmp_path / "release"
    harness = tmp_path / "barrier.sh"
    harness.write_text(
        "#!/bin/bash\n"
        "printf 'ENTER %s\\n' \"$$\" >>\"$1\"\n"
        "while [[ ! -e \"$2\" ]]; do sleep 0.02; done\n"
        "echo LEAVE $$ >>\"$1\"\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)

    procs = [
        subprocess.Popen(
            [sys.executable, str(REPO_ROOT / "scripts" / "worker_lock_exec.py"),
             str(lock), str(harness), str(entered), str(release)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(16)
    ]
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if entered.exists() and "ENTER" in entered.read_text(encoding="utf-8"):
                break
            time.sleep(0.02)
        else:
            pytest.fail("no contender entered the critical section")

        # Give every loser time to attempt the lock while the winner holds it.
        time.sleep(0.4)
        lines = entered.read_text(encoding="utf-8").splitlines()
        assert len([line for line in lines if line.startswith("ENTER")]) == 1, lines
    finally:
        release.write_text("go", encoding="utf-8")
        for proc in procs:
            proc.communicate(timeout=15)

    lines = entered.read_text(encoding="utf-8").splitlines()
    assert len([line for line in lines if line.startswith("ENTER")]) == 1, lines
    assert len([line for line in lines if line.startswith("LEAVE")]) == 1, lines
    assert [proc.returncode for proc in procs].count(75) == 15


def test_stale_pid_text_does_not_block_a_new_worker(tmp_path):
    """PID text is diagnostic only; no lock owner means no stale reclaim."""
    lock = tmp_path / ".lock"
    lock.write_text("999999999")
    noop = tmp_path / "noop.sh"
    noop.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    noop.chmod(0o755)

    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "worker_lock_exec.py"),
         str(lock), str(noop)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)


def test_lock_is_released_when_holder_exits(tmp_path):
    """Kernel releases flock on process exit; no stale PID cleanup needed."""
    lock = tmp_path / ".lock"
    holder = _hold_flock(lock)
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and _lock_is_free(lock):
            time.sleep(0.01)
        assert not _lock_is_free(lock)
    finally:
        holder.kill()
        holder.wait(timeout=5)
    assert _lock_is_free(lock)


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
        (queue_dir / ".lock.result").write_text("started")
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
        with patch.object(sw, "_check_lock", return_value=holder):
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
    """PID text alone is not a lock; a real flock holder is."""
    lock = queue_dir / ".lock"
    lock.write_text(str(os.getpid()))
    assert sw._check_lock() is None
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert sw._check_lock() == os.getpid()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


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
    assert sm._queue_dir() == sw._queue_dir()


def test_python_and_bash_agree_on_tilde_expansion_for_queue_dir(monkeypatch, tmp_path):
    """MONI_QUEUE_DIR='~/...' 时 Python 与 bash 必须解析到同一物理路径 (audit F3)。"""
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("MONI_QUEUE_DIR", "~/myqueue")

    # Python 侧解析
    py_dir = sw._queue_dir()
    assert py_dir == fake_home / "myqueue"

    # 从真实脚本提取 QUEUE_DIR 定义与展开逻辑 (audit F3 严谨性要求: 不写死副本)
    script_text = WORKER_SCRIPT.read_text(encoding="utf-8")
    assign_lines = [ln for ln in script_text.splitlines() if ln.startswith("QUEUE_DIR=")]
    assert len(assign_lines) >= 2, "scripts/default_worker.sh must contain QUEUE_DIR assignment and tilde replacement"
    script_snippet = chr(10).join(assign_lines) + chr(10) + 'echo "$QUEUE_DIR"'
    bash_dir = subprocess.run(
        ["/bin/bash", "-c", script_snippet],
        env=dict(os.environ, HOME=str(fake_home), MONI_QUEUE_DIR="~/myqueue"),
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert Path(bash_dir) == py_dir


def test_launch_reports_error_when_queue_dir_missing(monkeypatch, tmp_path):
    """队列目录不存在时不应误报 completed_nothing_to_do (audit F4)。"""
    non_existent = tmp_path / "missing_queue"
    monkeypatch.setenv("MONI_QUEUE_DIR", str(non_existent))
    result = sw.launch_worker()
    assert result.started is False
    assert result.completed_nothing_to_do is False
    assert result.error and "queue dir not found" in result.error

    monkeypatch.setenv("MONI_QUEUE_DIR", str(tmp_path / "custom-queue"))
    assert sw._queue_dir() == sw.resolve_queue_dir()
    assert sw._queue_dir() == tmp_path / "custom-queue"


# ---------------------------------------------------------------------------
# R2 / v1.0.49.3 Regression tests: P1-1, P1-2, P2-1, P3
# ---------------------------------------------------------------------------


def test_live_flock_holder_makes_worker_exit_75(tmp_path):
    """A live flock holder must block the helper with the explicit busy code."""
    lock = tmp_path / ".lock"
    lock.write_text("999999999\n")
    noop = tmp_path / "noop.sh"
    noop.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    noop.chmod(0o755)
    holder = _hold_flock(lock)
    try:
        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "worker_lock_exec.py"),
             str(lock), str(noop)],
            capture_output=True,
            text=True,
            timeout=15,
        )
    finally:
        holder.kill()
        holder.wait(timeout=5)
    assert res.returncode == 75, (res.returncode, res.stdout, res.stderr)
    assert "another instance is running" in res.stderr
    assert lock.read_text().strip() == "999999999"


def test_guard_held_by_living_process_fails_launch_without_nothing_to_do(monkeypatch, tmp_path):
    """A live lock must make launch_worker report already_running, not done."""
    qdir = tmp_path / "queue"
    qdir.mkdir()
    lock = qdir / ".lock"
    lock.write_text(str(os.getpid()))
    holder = _hold_flock(qdir / ".held")
    try:
        # _check_lock probes .lock, so hold that exact inode in this process.
        fd = os.open(lock, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:  # pragma: no cover
            pass
        monkeypatch.setenv("MONI_QUEUE_DIR", str(qdir))
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(sw, "DEFAULT_DB", tmp_path / "news.db")
        res = sw.launch_worker()
    finally:
        holder.kill()
        holder.wait(timeout=5)
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        except OSError:
            pass
    assert res.started is False
    assert res.already_running is True
    assert res.completed_nothing_to_do is False


def test_sigterm_terminates_worker_immediately_with_exit_143_and_cleans_lock(tmp_path):
    """TERM exits 143 quickly; the flock is released but the inode survives."""
    import sqlite3

    qdir = tmp_path / "queue"
    qdir.mkdir()
    lock_file = qdir / ".lock"
    db_file = tmp_path / "news.db"

    conn = sqlite3.connect(db_file)
    conn.executescript("""
        CREATE TABLE share_tasks (
            id TEXT PRIMARY KEY, format TEXT, article_ids TEXT,
            output_path TEXT, status TEXT, error TEXT, created_at TEXT,
            started_at TEXT, finished_at TEXT, extra TEXT,
            progress_stage TEXT, progress_pct INTEGER, progress_message TEXT
        );
        INSERT INTO share_tasks (id, format, article_ids, status, created_at)
        VALUES ('t1', 'plain_text', '[]', 'pending', '2026-10-24 00:00:00');
    """)
    conn.commit()
    conn.close()

    (qdir / "t1.json").write_text('{"id":"t1","format":"plain_text","payload":{"article_ids":[]}}')

    env = _worker_env(qdir, tmp_path)

    proc = subprocess.Popen(
        ["/bin/bash", str(WORKER_SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )

    # Wait until the re-exec'd bash has reached the main loop (result
    # marker written), not merely until the helper created the lock file.
    t0 = time.time()
    result_file = qdir / ".lock.result"
    while time.time() - t0 < 3.0:
        if result_file.exists():
            break
        time.sleep(0.05)

    assert lock_file.exists(), "worker failed to create lock file"
    assert result_file.exists(), "worker never reached the main loop"
    assert lock_file.read_text().strip() == str(proc.pid)

    # 发送 SIGTERM
    proc.send_signal(signal.SIGTERM)
    t1 = time.time()
    rc = proc.wait(timeout=3.0)
    elapsed = time.time() - t1

    assert elapsed < 1.0, f"worker took too long to terminate: {elapsed:.2f}s"
    assert rc == 143, f"expected exit code 143 on SIGTERM, got {rc}"
    # The inode stays (unlink would split the flock generation), but the
    # kernel lock must be free immediately.
    assert _lock_is_free(lock_file)


def test_sigterm_kills_blocking_child_that_ignores_term(tmp_path):
    """TERM must stop the worker even when its LLM child ignores TERM."""
    qdir = tmp_path / "queue"
    qdir.mkdir()
    lock_file = qdir / ".lock"
    child_pid_file = tmp_path / "child.pid"
    db_file = tmp_path / "news.db"
    conn = sqlite3.connect(db_file)
    conn.executescript("""
        CREATE TABLE share_tasks (
            id TEXT PRIMARY KEY, format TEXT, article_ids TEXT,
            output_path TEXT, status TEXT, error TEXT, created_at TEXT,
            started_at TEXT, finished_at TEXT, extra TEXT,
            progress_stage TEXT, progress_pct INTEGER, progress_message TEXT
        );
        CREATE TABLE articles (
            id TEXT PRIMARY KEY, title TEXT, raw_content TEXT, ai_summary TEXT,
            source TEXT, bb_score INTEGER, bb_summary TEXT,
            bb_one_sentence TEXT, bb_main_points TEXT,
            bb_key_quotes TEXT, bb_tags TEXT
        );
        INSERT INTO share_tasks (id, format, article_ids, status, created_at)
        VALUES ('t1', 'markdown', '["a1"]', 'pending', '2026-10-24 00:00:00');
        INSERT INTO articles (id, title, raw_content, ai_summary, source)
        VALUES ('a1', 'Title', 'Body', NULL, 'test');
    """)
    conn.commit()
    conn.close()
    (qdir / "t1.json").write_text(
        '{"id":"t1","format":"markdown","payload":'
        '{"article_ids":["a1"],"include_abstract":true}}',
        encoding="utf-8",
    )

    # The real worker calls summary_gen through _run_worker_child.  This
    # wrapper delegates every ordinary Python invocation to the real
    # interpreter but turns summary_gen into a TERM-ignoring blocker, so
    # the shipped worker's trap and group cleanup are what get exercised.
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        f"#!/bin/bash\n"
        f"if [[ \"$1\" == *summary_gen.py ]]; then\n"
        f"  echo $$ >\"{child_pid_file}\"\n"
        f"  trap '' TERM\n"
        f"  while true; do sleep 1; done\n"
        f"fi\n"
        f"exec {sys.executable} \"$@\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    env = _worker_env(qdir, tmp_path)
    env["TMPDIR"] = str(tmp_path)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["MONI_PYTHON"] = str(fake_python)
    proc = subprocess.Popen(
        ["/bin/bash", str(WORKER_SCRIPT)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.time() + 3
        while time.time() < deadline and not child_pid_file.exists():
            time.sleep(0.02)
        assert child_pid_file.exists(), "fake worker never started its child"
        child_pid = int(child_pid_file.read_text().strip())
        assert os.kill(child_pid, 0) is None

        started = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        rc = proc.wait(timeout=3)
        elapsed = time.monotonic() - started
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    assert rc == 143, f"expected 143, got {rc}"
    # TERM 预算: 不能写死一个数字。
    #
    # worker 的 `_stop_child_group()` 里的看门狗是 `sleep <grace>` 之后才
    # SIGKILL 那个**忽略 TERM** 的子进程 —— 所以 TERM→退出的**下界**就是
    # 这个 grace 值。原先这里写死 `elapsed < 1.0`, 而 grace 是 0.8 → 只剩
    # 0.2s 余量: 机器一忙 (CI runner / 全量并发跑), 调度抖动就能把实测推到
    # 1.03s → 假红。
    #
    # 这个 flake 与业务逻辑无关 (2026-11-12 sprint-38 全量回归里实测:
    # 单跑 10 次 0 失败, 全量并发跑时偶发), 但它会**卡住 CI**。
    # 修法: 预算从 worker 脚本里的真实 grace 值推导, 留 1.0s 余量 ——
    # 这样以后有人调 grace, 预算自动跟着走, 不会再漂移。
    grace = _watchdog_grace_seconds()
    budget = grace + 1.0
    assert elapsed < budget, (
        f"worker exceeded {budget:.1f}s TERM budget "
        f"(watchdog grace={grace:.1f}s): {elapsed:.2f}s"
    )
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
    assert _lock_is_free(lock_file)


def test_watchdog_grace_is_parsed_from_worker_script():
    """TERM 预算必须跟着 worker 脚本的真实 grace 走, 不能写死。

    背景: 看门狗 `sleep <grace>` 决定了 TERM→退出的耗时下界。测试里若写死
    一个预算 (原先是 1.0s), 而 grace 是 0.8 → 只剩 0.2s 余量, 机器一忙就
    假红 (2026-11-12 sprint-38 全量回归实测 1.03s)。现在预算由 grace 推导,
    这条用例守住「推导本身正确」。
    """
    grace = _watchdog_grace_seconds()
    assert grace > 0, f"看门狗 grace 解析失败或非法: {grace}"
    # 脚本里的实际值 —— 变了这条用例会提醒同步 (而不是让预算悄悄漂移)
    assert grace == 0.8, f"worker 看门狗 grace 变成了 {grace}, 请同步这条用例"
    # 预算 = grace + 余量, 必须明显大于 grace 本身 (余量用于吸收调度抖动)
    assert grace + 1.0 > grace + 0.5


def test_empty_queue_reports_nothing_to_do_20_times(queue_dir, monkeypatch):
    """P1-C: the result marker, not a transient PID, decides emptiness."""
    import sqlite3

    db = queue_dir.parent.parent / "news.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE share_tasks (
            id TEXT PRIMARY KEY, format TEXT, article_ids TEXT,
            output_path TEXT, status TEXT, error TEXT, created_at TEXT,
            started_at TEXT, finished_at TEXT, extra TEXT, progress_stage TEXT,
            progress_pct INTEGER, progress_message TEXT
        );
    """)
    conn.close()
    monkeypatch.setenv("MONI_QUEUE_DIR", str(queue_dir))
    monkeypatch.setenv("HOME", str(queue_dir.parent.parent))
    monkeypatch.setattr(sw, "DEFAULT_DB", db)
    results = [sw.launch_worker() for _ in range(20)]
    assert all(r.completed_nothing_to_do for r in results), repr([
        (i, r.started, r.already_running, r.completed_nothing_to_do, r.error)
        for i, r in enumerate(results)
    ])
    assert all(not r.started for r in results)


def test_nonempty_queue_reports_started(queue_dir, monkeypatch):
    """A worker that finds real work writes the started marker."""
    import sqlite3

    db = queue_dir.parent.parent / "news.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE share_tasks (
            id TEXT PRIMARY KEY, format TEXT, article_ids TEXT,
            output_path TEXT, status TEXT, error TEXT, created_at TEXT,
            started_at TEXT, finished_at TEXT, extra TEXT,
            progress_stage TEXT, progress_pct INTEGER, progress_message TEXT
        );
        INSERT INTO share_tasks (id,format,article_ids,status,created_at)
        VALUES ('t1','plain_text','[]','pending','x');
    """)
    conn.commit()
    conn.close()
    (queue_dir / "t1.json").write_text('{"id":"t1","format":"plain_text","payload":{"article_ids":[]}}')
    monkeypatch.setenv("MONI_QUEUE_DIR", str(queue_dir))
    monkeypatch.setenv("HOME", str(queue_dir.parent.parent))
    monkeypatch.setattr(sw, "DEFAULT_DB", db)
    result = sw.launch_worker()
    assert result.started is True
    assert result.completed_nothing_to_do is False


def test_stale_guard_directory_is_ignored(tmp_path):
    """A leftover R2 guard directory must not affect the flock worker."""
    lock = tmp_path / ".lock"
    lock.write_text("999999999\n")
    guard = tmp_path / ".lock.reclaim.d"
    guard.mkdir()
    (guard / "pid").write_text("888888888\n")
    (guard / "claim").write_text("777777777\n")
    noop = tmp_path / "noop.sh"
    noop.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    noop.chmod(0o755)

    res = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "worker_lock_exec.py"),
         str(lock), str(noop)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert res.returncode == 0, f"expected rc=0, got {res.returncode}: {res.stderr}"
    assert guard.exists(), "flock must not depend on or delete legacy state"


def test_launch_reports_error_when_queue_path_is_file(monkeypatch, tmp_path):
    """P3-1: 当 MONI_QUEUE_DIR 是普通文件而非目录时必须返回明确 error。"""
    q_file = tmp_path / "not_a_dir"
    q_file.write_text("hello")
    monkeypatch.setenv("MONI_QUEUE_DIR", str(q_file))
    monkeypatch.setenv("HOME", str(tmp_path))

    res = sw.launch_worker()
    assert res.started is False
    assert res.completed_nothing_to_do is False
    assert res.error and "queue path is not a directory" in res.error


def test_signal_traps_and_flock_structural_invariants():
    """Pin the signal exit codes and the inherited-fd design."""
    src = WORKER_SCRIPT.read_text(encoding="utf-8")
    assert "trap '_stop_child_group; exit 143' TERM" in src
    assert "trap '_stop_child_group; exit 130' INT" in src
    assert "kill -TERM -- \"-$_child_to_stop\"" in src
    assert "kill -KILL -- \"-$_child_to_stop\"" in src
    assert "sleep 0.8" in src
    assert "run_in_new_group.py" not in src


def test_launch_worker_uses_current_interpreter(queue_dir, monkeypatch):
    """launch_worker() must spawn worker_lock_exec.py with sys.executable, not a hardcoded host path."""
    spawned_args = []

    def fake_popen(args, *p_args, **kwargs):
        spawned_args.append(args)

        class FakeProc:
            pid = 99999

            def poll(self):
                return 0

        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(sw, "_read_worker_result", lambda: "nothing_to_do")
    db = queue_dir.parent.parent / "news.db"
    monkeypatch.setenv("MONI_QUEUE_DIR", str(queue_dir))
    monkeypatch.setenv("HOME", str(queue_dir.parent.parent))
    monkeypatch.setattr(sw, "DEFAULT_DB", db)

    sw.launch_worker()
    assert spawned_args, "Popen was not called"
    assert spawned_args[0][0] == sys.executable


