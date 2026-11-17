"""tests/test_xbot_feed_git_retry.py — xbot_feed 上游 git 临时网络失败的退避重试。

sprint-51 / id 26111606（v1.0.95）。

背景：2026-11-16 16:32 生产 xbot_feed 抓取失败
（``RuntimeError: xbot-feed: git clone/pull failed — cannot read upstream feed``），
底层 stderr 是 ``LibreSSL SSL_connect: SSL_ERROR_SYSCALL`` —— 上游私有仓库
经本机代理访问时隧道建立后 TLS 握手被重置。诊断量化（引用，不重跑）：
同代理路径 ``git ls-remote`` 15 次 OK=12/FAIL=3，google.com 与 api.github.com
经同一代理也各有 1 次失败 ⇒ 隧道整体间歇抖动。

``_sync_clone`` 原本对 ``git pull --ff-only`` **单次尝试无重试** ⇒ 一次抖动
即整轮失败。本文件给「有限次数退避重试」定可验证标准。

纪律（照 AGENTS.md）：
- **全程不联网**：git 操作全在 tmp_path 里的本地 bare repo 上跑；网络失败
  用 mock 的 ``subprocess.run`` 桩模拟，不打真实上游。
- **不硬编码本机绝对路径**，不依赖真实 sleep 时长（退避可注入/可跳过）。
- **负控**：每条断言都要能通过注水/取反证明会红（见 warden done-criteria）。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from moni.db.connection import close_db, set_db_path
from moni.db.migrate import migrate

_SOURCE_NAME = "xbot_feed"
_SOURCE_ID = "src-xbot-feed"

#: 真实的 subprocess.run —— 测试里把 subprocess.run 换成桩之后, 用它做
#: **独立于桩**的校验调用 (否则连自己的 git rev-parse 都被桩吃掉,
#: 校验会读到空值 —— 这正是本次踩到的一个假绿陷阱)。
_REAL_RUN = subprocess.run

# 真实生产故障的 stderr 逐字签名（来自 /private/tmp/moni-8900-v1.0.52.log）
REAL_TLS_FAILURE = (
    "fatal: unable to access 'https://github.com/mariusiaowego-commits/xbot-feed.git/': "
    "LibreSSL SSL_connect: SSL_ERROR_SYSCALL in connection to github.com:443"
)


# ── fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Isolated tmp sqlite + tmp MONI_DATA_DIR."""
    close_db()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MONI_DATA_DIR", str(data_dir))
    set_db_path(data_dir / "news.db")
    migrate()
    yield data_dir
    close_db()


def _seed_source(conn, fetch_config=None, is_active=1):
    conn.execute("DELETE FROM source_tags WHERE source_id=?", (_SOURCE_ID,))
    conn.execute("DELETE FROM sources WHERE id=? OR name=?", (_SOURCE_ID, _SOURCE_NAME))
    conn.commit()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, notes, fetch_config, created_at, updated_at) "
        "VALUES (?, ?, 'X-bot 精选', 'json', ?, 60, ?, 'test', ?, "
        "'2026-01-01', '2026-01-01')",
        (_SOURCE_ID, _SOURCE_NAME,
         "https://github.com/mariusiaowego-commits/xbot-feed.git",
         is_active, fetch_config),
    )
    conn.commit()


def _fetcher(data_dir, fetch_config=None):
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    return FetcherXbotFeed()


def _make_bare_repo(tmp_path: Path) -> Path:
    """建一个本地 bare repo 当上游（**纯离线**，不联网）。

    与 ``tests/test_xbot_feed_fetch.py`` 的同名 helper 同款：真实 git
    操作全在这个临时目录里跑，不碰真实上游、不碰生产。
    """
    work = tmp_path / "retry-upstream-work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "feed").mkdir(parents=True, exist_ok=True)
    (work / "feed" / "2026-11-03.json").write_text(
        '{"date": "2026-11-03", "items": []}', encoding="utf-8"
    )
    (work / "feed" / "latest.json").write_text(
        '{"date": "2026-11-03", "items": []}', encoding="utf-8"
    )

    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@example.com"],
                   check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "feed"], check=True)

    bare = tmp_path / "retry-upstream.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return bare


class _Proc:
    """subprocess.run 的最小桩。"""

    def __init__(self, returncode=0, stderr="", stdout=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


def _patch_run(monkeypatch, results, calls=None):
    """把 subprocess.run 换成按序返回 results 的桩。"""
    import subprocess as sp

    seq = list(results)
    seen = calls if calls is not None else []

    def _fake_run(argv, **kwargs):
        seen.append((list(argv), kwargs))
        item = seq.pop(0) if seq else _Proc(returncode=0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(sp, "run", _fake_run)
    return seen


# ── 1. 短暂失败后能成功 ──────────────────────────────────────────────────

def test_transient_failure_then_success(fresh_db, monkeypatch):
    """第一次 pull 撞 TLS 抖动，第二次成功 → 整体成功，不抛错。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    clone = fresh_db / "xbot-feed"
    (clone / ".git").mkdir(parents=True)
    (clone / "feed").mkdir(parents=True)

    calls = []
    _patch_run(monkeypatch, [
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
        _Proc(returncode=0, stderr=""),
    ], calls)

    f = _fetcher(fresh_db)
    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)

    root = f._sync_clone(f._load_config())
    assert root is not None, "短暂失败后重试成功，不该返回 None"
    assert root == clone.resolve()
    # 确实跑了两次 git
    pulls = [c for c in calls if c[0][:2] == ["git", "pull"]]
    assert len(pulls) == 2, f"应重试一次（总共 2 次 pull），实际 {len(pulls)}"


# ── 2. 打满上限及时失败 ──────────────────────────────────────────────────

def test_exhausted_attempts_returns_none(fresh_db, monkeypatch):
    """一直失败 → 返回 None（fetch() 再抛 RuntimeError），不无限重试。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    clone = fresh_db / "xbot-feed"
    (clone / ".git").mkdir(parents=True)
    (clone / "feed").mkdir(parents=True)

    calls = []
    _patch_run(monkeypatch, [
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
    ], calls)
    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)

    f = _fetcher(fresh_db)
    assert f._sync_clone(f._load_config()) is None
    pulls = [c for c in calls if c[0][:2] == ["git", "pull"]]
    assert len(pulls) == 3, f"上限 3 次，实际 {len(pulls)}"


def test_fetch_raises_runtime_error_after_exhaustion(fresh_db, monkeypatch):
    """打满上限后 fetch() 仍抛原来的 RuntimeError（对外契约不变）。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    clone = fresh_db / "xbot-feed"
    (clone / ".git").mkdir(parents=True)
    (clone / "feed").mkdir(parents=True)

    _patch_run(monkeypatch, [
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
    ])
    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)

    f = _fetcher(fresh_db)
    with pytest.raises(RuntimeError, match="git clone/pull failed"):
        f.fetch()


# ── 3. 永久错误不乱重试 ──────────────────────────────────────────────────

@pytest.mark.parametrize("permanent_stderr", [
    "fatal: Not possible to fast-forward, aborting.",
    "fatal: Authentication failed for 'https://user@github.com/o/r.git'",
    "remote: Repository not found.\nfatal: repository 'https://github.com/o/r.git' not found",
    "fatal: cannot access 'https://github.com/o/r.git/': Permission denied",
    "Host key verification failed.",
    "error: Your local changes to the following files would be overwritten by merge:",
])
def test_permanent_error_is_not_retried(fresh_db, monkeypatch, permanent_stderr):
    """非快进 / 认证 / 仓库不存在 / 权限 / host key / 本地冲突 → 只试一次。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    clone = fresh_db / "xbot-feed"
    (clone / ".git").mkdir(parents=True)
    (clone / "feed").mkdir(parents=True)

    calls = []
    _patch_run(monkeypatch, [_Proc(returncode=1, stderr=permanent_stderr)], calls)
    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)

    f = _fetcher(fresh_db)
    assert f._sync_clone(f._load_config()) is None
    pulls = [c for c in calls if c[0][:2] == ["git", "pull"]]
    assert len(pulls) == 1, f"永久错误不应重试，实际跑了 {len(pulls)} 次"


def test_timeout_error_is_not_retried_forever(fresh_db, monkeypatch):
    """git 超时（TimeoutExpired）算临时网络问题 → 可重试，但有上限。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed
    import subprocess as sp

    clone = fresh_db / "xbot-feed"
    (clone / ".git").mkdir(parents=True)
    (clone / "feed").mkdir(parents=True)

    calls = []
    _patch_run(monkeypatch, [
        sp.TimeoutExpired(cmd="git pull", timeout=120),
        sp.TimeoutExpired(cmd="git pull", timeout=120),
        sp.TimeoutExpired(cmd="git pull", timeout=120),
    ], calls)
    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)

    f = _fetcher(fresh_db)
    assert f._sync_clone(f._load_config()) is None
    pulls = [c for c in calls if c[0][:2] == ["git", "pull"]]
    assert len(pulls) == 3


# ── 4. 副作用安全 ────────────────────────────────────────────────────────

def test_failed_clone_leaves_no_partial_dir(fresh_db, monkeypatch):
    """AC-7: clone 失败 → **每次重试前**都清掉半成品目录，最终也不留残留。

    warden AC-7 的原话：「重试前的那次失败也必须已清理」。所以这条断言
    **两次**：①每次 git clone 调用**之前**目录都不存在；②最终目录不存在。

    只断言「最终不存在」是不够的（那是假绿 —— 删掉中途清理也能通过），
    所以这里记录每次调用前的目录状态快照。
    """
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    clone_root = fresh_db / "xbot-feed"
    seen_dir_state: list[bool] = []

    def _fake_run(argv, **kwargs):
        if argv[0] == "git" and argv[1] == "clone":
            # 记录调用前 clone_root 是否存在（重试前必须已被清理）
            seen_dir_state.append(clone_root.exists())
            # 模拟真实 git：失败时留下半拉目录
            clone_root.mkdir(parents=True, exist_ok=True)
            (clone_root / ".git").mkdir(exist_ok=True)
            return _Proc(returncode=128, stderr=REAL_TLS_FAILURE)
        return _Proc(returncode=0)

    import subprocess as sp
    monkeypatch.setattr(sp, "run", _fake_run)
    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)

    f = _fetcher(fresh_db)
    assert f._sync_clone(f._load_config()) is None

    assert len(seen_dir_state) == 3, f"应尝试 clone 3 次，实际 {len(seen_dir_state)}"
    # 第 1 次之前目录本来就不存在；第 2、3 次之前**必须**已被清理
    assert seen_dir_state[0] is False
    assert seen_dir_state[1] is False, "重试前必须清理半成品 clone 目录"
    assert seen_dir_state[2] is False, "重试前必须清理半成品 clone 目录"
    # 最终也不留残留
    assert not clone_root.exists(), f"clone 失败后不该留下 {clone_root}"


def test_failed_pull_leaves_clone_untouched(fresh_db, monkeypatch):
    """pull 失败 → 原 clone 的 HEAD / 工作区 / 文件列表三者逐字不变。

    用**真本地 bare repo**（纯离线，可控）—— warden AC-8 / W-7：比对
    ``git rev-parse HEAD`` + 工作区文件 sha256 清单 + 路径列表，不比对
    mtime（mtime 受文件系统影响，不可靠）。
    """
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    bare = _make_bare_repo(fresh_db.parent)
    f = _fetcher(fresh_db)
    clone_root = f._clone_root(f._load_config())
    # 真 clone 一次，拿到一个完整 clone
    _REAL_RUN(
        ["git", "clone", "-q", str(bare), str(clone_root)],
        check=True, capture_output=True,
    )

    head_before = _REAL_RUN(
        ["git", "-C", str(clone_root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    tree_before = _worktree_fingerprint(clone_root)

    # 现在让 pull 失败（模拟 TLS 抖动，打满 3 次）
    _patch_run(monkeypatch, [
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
    ])
    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)

    f2 = _fetcher(fresh_db)
    assert f2._sync_clone(f2._load_config()) is None

    head_after = _REAL_RUN(
        ["git", "-C", str(clone_root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    tree_after = _worktree_fingerprint(clone_root)

    assert head_after == head_before, "pull 失败不该动原 clone 的 HEAD"
    assert tree_after == tree_before, "pull 失败不该动原 clone 的工作区"


def _worktree_fingerprint(clone_root: Path) -> dict[str, str]:
    """工作区文件清单 + 每个文件的 sha256（不含 .git 内部）。"""
    import hashlib

    out: dict[str, str] = {}
    for path in sorted(clone_root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(clone_root))
        if rel.startswith(".git/"):
            continue
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def test_clone_retries_after_cleanup(fresh_db, monkeypatch):
    """第一次 clone 失败留了半成品目录 → 重试前必须清理，否则第二次必撞 already exists。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    clone_root = fresh_db / "xbot-feed"

    def _fake_run(argv, **kwargs):
        if argv[0] == "git" and argv[1] == "clone":
            # 模拟 git clone 失败后留下的半成品目录
            clone_root.mkdir(parents=True, exist_ok=True)
            (clone_root / ".git").mkdir(exist_ok=True)
            return _Proc(returncode=128, stderr=REAL_TLS_FAILURE)
        return _Proc(returncode=0)

    import subprocess as sp
    monkeypatch.setattr(sp, "run", _fake_run)
    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)

    f = _fetcher(fresh_db)
    f._sync_clone(f._load_config())
    # 重试前清掉了半成品：最终不该残留一个「只有 .git 的空壳」
    if clone_root.exists():
        assert not (clone_root / ".git" / "HEAD").exists(), \
            "重试前应清理失败的 clone 半成品"


# ── 5. 超时守总时限 / 不吃 job 预算 ──────────────────────────────────────

def test_total_budget_caps_attempts(fresh_db, monkeypatch):
    """总时限用尽 → 不再发起新的 git 尝试（不能无限重试到超时）。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    clone = fresh_db / "xbot-feed"
    (clone / ".git").mkdir(parents=True)
    (clone / "feed").mkdir(parents=True)

    calls = []
    _patch_run(monkeypatch, [
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
    ], calls)
    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)

    f = _fetcher(fresh_db)
    cfg = f._load_config()
    cfg["git_retry_total_timeout"] = 0.0   # 预算耗尽
    assert f._sync_clone(cfg) is None
    pulls = [c for c in calls if c[0][:2] == ["git", "pull"]]
    assert len(pulls) == 0, "预算为 0 时不该发起 git 尝试"


def test_single_attempt_timeout_capped_by_remaining_budget(fresh_db, monkeypatch):
    """单次 git 超时 = min(git_timeout, 剩余总预算) —— 不吃 job 级 600s 预算。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    clone = fresh_db / "xbot-feed"
    (clone / ".git").mkdir(parents=True)
    (clone / "feed").mkdir(parents=True)

    calls = []
    _patch_run(monkeypatch, [
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
    ], calls)
    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)

    f = _fetcher(fresh_db)
    cfg = f._load_config()
    cfg["git_timeout"] = 120
    cfg["git_retry_total_timeout"] = 30
    f._sync_clone(cfg)
    pulls = [c for c in calls if c[0][:2] == ["git", "pull"]]
    assert pulls, "应至少尝试一次"
    for argv, kwargs in pulls:
        assert kwargs.get("timeout") is not None
        assert kwargs["timeout"] <= 30, \
            f"单次超时不该超过剩余总预算，实际 {kwargs['timeout']}"


# ── 6. 日志保真 + 不泄密 ────────────────────────────────────────────────

def test_log_contains_attempts_and_real_stderr(fresh_db, monkeypatch, caplog):
    """日志含尝试次数 + 真实 stderr（脱敏后），且不含 token。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    clone = fresh_db / "xbot-feed"
    (clone / ".git").mkdir(parents=True)
    (clone / "feed").mkdir(parents=True)

    _patch_run(monkeypatch, [
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
        _Proc(returncode=128, stderr=REAL_TLS_FAILURE),
    ])
    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)

    f = _fetcher(fresh_db)
    with caplog.at_level("ERROR"):
        f._sync_clone(f._load_config())

    text = caplog.text
    assert "SSL_ERROR_SYSCALL" in text, "日志要保留真实 stderr"
    assert "attempt" in text.lower(), "日志要标明尝试次数"


def test_log_redacts_credentials(fresh_db, monkeypatch, caplog):
    """stderr 里的 user:token@ 必须脱敏，不落盘。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    clone = fresh_db / "xbot-feed"
    (clone / ".git").mkdir(parents=True)
    (clone / "feed").mkdir(parents=True)

    _patch_run(monkeypatch, [
        _Proc(returncode=128,
              stderr="fatal: Authentication failed for 'https://tok3n@github.com/o/r.git'"),
        _Proc(returncode=128,
              stderr="fatal: Authentication failed for 'https://tok3n@github.com/o/r.git'"),
        _Proc(returncode=128,
              stderr="fatal: Authentication failed for 'https://tok3n@github.com/o/r.git'"),
    ])
    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)

    f = _fetcher(fresh_db)
    with caplog.at_level("ERROR"):
        f._sync_clone(f._load_config())

    assert "tok3n" not in caplog.text, "凭据不得进日志"
    assert "***" in caplog.text


# ── 7. 回归护栏：ff-only / 作者屏蔽 / 去重 逐字未变 ──────────────────────

def test_pull_command_still_uses_ff_only(fresh_db, monkeypatch):
    """重试不得改 git pull --ff-only 语义。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    clone = fresh_db / "xbot-feed"
    (clone / ".git").mkdir(parents=True)
    (clone / "feed").mkdir(parents=True)

    calls = []
    _patch_run(monkeypatch, [_Proc(returncode=0, stderr="")], calls)
    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)

    f = _fetcher(fresh_db)
    f._sync_clone(f._load_config())
    pulls = [c for c in calls if c[0][:2] == ["git", "pull"]]
    assert pulls
    for argv, _kwargs in pulls:
        assert "--ff-only" in argv, f"pull 必须仍是 --ff-only: {argv}"


def test_author_block_logic_unchanged():
    """作者屏蔽的归一化单一来源不被重试改动破坏。

    归一规则（现行实现）：strip → lstrip("@") → lower（下划线保留）。
    """
    from moni.fetch.sources.xbot_feed import normalize_handle

    assert normalize_handle("@Foo_Bar") == "foo_bar"
    assert normalize_handle("FooBar") == "foobar"
    assert normalize_handle("  @FooBar  ") == "foobar"
    assert normalize_handle(None) == ""
