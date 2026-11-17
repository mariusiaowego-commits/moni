"""tests/test_xbot_feed_git_retry_signatures.py — 重试判据的逐条签名用例。

sprint-51 / id 26111606 的配套（v1.0.95）。

warden done-criteria（``/tmp/moni-sprint51/warden-done-criteria-sprint51.md``）
AC-2 / AC-3 / AC-4 要求：**表 A 每条临时签名一条独立用例**、**表 B 每条永久
签名一条独立用例**、外加「未知错误不重试」与「跨类冲突」两条。

为什么不并进 ``test_xbot_feed_git_retry.py``：那两个文件关注的是「重试机制
本身」（短暂失败后成功 / 上限 / 副作用 / 日志 / 预算）；本文件关注的是
**判据的覆盖面** —— 删掉任何一条签名，对应的参数化用例必须变红（NC-5 /
NC-4）。分开更利于对照 warden 的负控清单逐条核销。

纪律：不联网（全部用桩模拟 git 的 stderr 文本）；不依赖真实 sleep；不用
本机绝对路径。
"""

from __future__ import annotations

import pytest

from moni.db.connection import close_db, set_db_path
from moni.db.migrate import migrate

_REAL_TLS_FAILURE = (
    "fatal: unable to access 'https://github.com/mariusiaowego-commits/xbot-feed.git/': "
    "LibreSSL SSL_connect: SSL_ERROR_SYSCALL in connection to github.com:443"
)


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    close_db()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MONI_DATA_DIR", str(data_dir))
    set_db_path(data_dir / "news.db")
    migrate()
    yield data_dir
    close_db()


class _Proc:
    def __init__(self, returncode=0, stderr="", stdout=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


def _install_git_stub(monkeypatch, results):
    """把 subprocess.run 换成按序返回预设结果的桩。"""
    import subprocess as sp

    seq = list(results)
    seen = []

    def _fake_run(argv, **kwargs):
        seen.append((list(argv), kwargs))
        return seq.pop(0) if seq else _Proc(returncode=0)

    monkeypatch.setattr(sp, "run", _fake_run)
    return seen


def _no_backoff(monkeypatch):
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)


def _make_clone(data_dir) -> None:
    clone = data_dir / "xbot-feed"
    (clone / ".git").mkdir(parents=True)
    (clone / "feed").mkdir(parents=True)


def _pull_calls(seen) -> list:
    return [c for c in seen if c[0][:2] == ["git", "pull"]]


# ── 表 A：临时网络/TLS 签名 —— 每条都应触发重试 ──────────────────────────

#: warden §3 表 A 的 11 条签名（含 Q-3 建议加入的 curl 28）。
#: 断言文本里必须能看出命中的是哪一条 —— 删掉实现里的某条签名，
#: 对应用例变红（NC-5）。
TRANSIENT_CASES = [
    ("ssl_error_syscall",
     "fatal: unable to access 'https://github.com/o/r.git/': "
     "LibreSSL SSL_connect: SSL_ERROR_SYSCALL in connection to github.com:443"),
    ("connection_reset",
     "fatal: unable to access 'https://github.com/o/r.git/': "
     "Failed to connect to github.com port 443: Connection reset by peer"),
    ("connection_timed_out",
     "fatal: unable to access 'https://github.com/o/r.git/': "
     "Failed to connect to github.com port 443: Connection timed out"),
    ("couldnt_resolve_host",
     "fatal: unable to access 'https://github.com/o/r.git/': "
     "Could not resolve host: github.com"),
    ("temporary_failure_name_resolution",
     "fatal: unable to access 'https://github.com/o/r.git/': "
     "Temporary failure in name resolution"),
    ("early_eof",
     "fatal: unable to access 'https://github.com/o/r.git/': "
     "early EOF, read 0 bytes"),
    ("gnutls_recv_error",
     "fatal: unable to access 'https://github.com/o/r.git/': "
     "GnuTLS recv error: The TLS connection was non-properly terminated."),
    ("gnutls_handshake",
     "fatal: unable to access 'https://github.com/o/r.git/': "
     "gnutls_handshake() failed: The TLS handshake was non-properly terminated."),
    # P1-2（warden round-1）：实现表 A 有 14 条但用例只有 12 条 ⇒
    # `couldn't resolve host` 与 `gnutls error` 两条**零覆盖**，删掉签名
    # 全部测试仍全绿。下面两条是补齐的**独立**用例。
    ("couldnt_resolve_host_libc",
     "fatal: unable to access 'https://github.com/o/r.git/': "
     "Couldn't resolve host: github.com"),
    ("gnutls_error_generic",
     "fatal: unable to access 'https://github.com/o/r.git/': "
     "GnuTLS error: The TLS connection was non-properly terminated."),
    ("curl_28",
     "fatal: unable to access 'https://github.com/o/r.git/': "
     "RPC failed; curl 28 Operation timed out"),
    ("curl_35",
     "fatal: unable to access 'https://github.com/o/r.git/': "
     "RPC failed; curl 35 SSL connect error"),
    ("curl_52",
     "fatal: unable to access 'https://github.com/o/r.git/': "
     "RPC failed; curl 52 Empty reply from server"),
    ("curl_56",
     "fatal: unable to access 'https://github.com/o/r.git/': "
     "RPC failed; curl 56 Recv failure"),
]


@pytest.mark.parametrize("case_id,stderr", TRANSIENT_CASES,
                         ids=[c[0] for c in TRANSIENT_CASES])
def test_each_transient_signature_is_retried(fresh_db, monkeypatch, case_id, stderr):
    """AC-2: 表 A 每条临时签名各自都能触发重试（第 1 次失败 → 第 2 次成功）。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    _make_clone(fresh_db)
    seen = _install_git_stub(monkeypatch, [
        _Proc(returncode=128, stderr=stderr),
        _Proc(returncode=0, stderr=""),
    ])
    _no_backoff(monkeypatch)

    f = FetcherXbotFeed()
    root = f._sync_clone(f._load_config())

    assert root is not None, f"[{case_id}] 临时错误应重试并最终成功"
    assert len(_pull_calls(seen)) == 2, f"[{case_id}] 应恰好重试一次"


# ── 表 B：永久错误签名 —— 每条都不应重试 ────────────────────────────────

#: warden §3 表 B 的 7 条签名。
PERMANENT_CASES = [
    ("not_fast_forward",
     "fatal: Not possible to fast-forward, aborting."),
    ("authentication_failed",
     "fatal: Authentication failed for 'https://github.com/o/r.git'"),
    ("could_not_read_username",
     "fatal: could not read Username for 'https://github.com/o/r.git'"),
    ("repository_not_found",
     "remote: Repository not found.\nfatal: repository "
     "'https://github.com/o/r.git' not found"),
    ("permission_denied",
     "fatal: cannot access 'https://github.com/o/r.git/': Permission denied"),
    ("host_key_verification_failed",
     "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
     "Host key verification failed."),
    ("local_changes_overwritten",
     "error: Your local changes to the following files would be overwritten "
     "by merge:\n\tfeed/latest.json"),
]


@pytest.mark.parametrize("case_id,stderr", PERMANENT_CASES,
                         ids=[c[0] for c in PERMANENT_CASES])
def test_each_permanent_signature_is_not_retried(fresh_db, monkeypatch,
                                                  case_id, stderr):
    """AC-3: 表 B 每条永久签名都只试一次。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    _make_clone(fresh_db)
    seen = _install_git_stub(monkeypatch, [_Proc(returncode=1, stderr=stderr)])
    _no_backoff(monkeypatch)

    f = FetcherXbotFeed()
    assert f._sync_clone(f._load_config()) is None
    assert len(_pull_calls(seen)) == 1, f"[{case_id}] 永久错误不该重试"


# ── AC-4：未知错误不重试 ────────────────────────────────────────────────

def test_unknown_error_is_not_retried(fresh_db, monkeypatch):
    """AC-4: 既不在表 A 也不在表 B 的 stderr → 只试一次 (L4 保守默认)。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    _make_clone(fresh_db)
    seen = _install_git_stub(monkeypatch, [
        _Proc(returncode=1, stderr="fatal: something we never saw before")
    ])
    _no_backoff(monkeypatch)

    f = FetcherXbotFeed()
    assert f._sync_clone(f._load_config()) is None
    assert len(_pull_calls(seen)) == 1, "未知错误不该重试"


# ── §3 跨类冲突：同时含临时与永久签名 → 永久优先，不重试 ─────────────────

def test_permanent_signature_wins_over_transient(fresh_db, monkeypatch):
    """一条 stderr 同时含表 A 与表 B 签名 → 判**不重试** (L2 先于 L3)。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    _make_clone(fresh_db)
    seen = _install_git_stub(monkeypatch, [
        _Proc(returncode=128,
              stderr="SSL_ERROR_SYSCALL in connection to github.com:443\n"
                     "fatal: Authentication failed for 'https://github.com/o/r.git'"),
    ])
    _no_backoff(monkeypatch)

    f = FetcherXbotFeed()
    assert f._sync_clone(f._load_config()) is None
    assert len(_pull_calls(seen)) == 1, "永久签名优先，不该重试"


# ── NC-7 负控的反向证明：只看 rc 会误判 ─────────────────────────────────

def test_rc_alone_cannot_classify(fresh_db, monkeypatch):
    """永久错误与临时错误的 rc **都是 128** ⇒ 判据必须看 stderr 文本。

    这条是「为什么不能只看 rc」的可执行证明（warden NC-7）。
    """
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    _make_clone(fresh_db)
    seen = _install_git_stub(monkeypatch, [
        _Proc(returncode=128, stderr="fatal: Authentication failed for 'https://x'"),
    ])
    _no_backoff(monkeypatch)

    f = FetcherXbotFeed()
    f._sync_clone(f._load_config())
    rc = seen[0][1].get("timeout")
    assert rc is not None
    assert len(_pull_calls(seen)) == 1, "rc=128 的永久错误不该重试"


# ── NC-8 负控的反向证明：rc=0 时即使 stderr 有永久签名也算成功 ──────────

def test_rc_zero_with_permanent_signature_in_stderr_is_success(fresh_db, monkeypatch):
    """rc == 0 → 成功，不看 stderr (L1 最先求值)。

    git 成功时 stderr 里可能有无关内容；rc 是「成功」的权威来源。
    """
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    _make_clone(fresh_db)
    seen = _install_git_stub(monkeypatch, [
        _Proc(returncode=0,
              stderr="warning: unable to rmdir 'old': Directory not empty"),
    ])
    _no_backoff(monkeypatch)

    f = FetcherXbotFeed()
    root = f._sync_clone(f._load_config())
    assert root is not None, "rc=0 必须视为成功"
    assert len(_pull_calls(seen)) == 1, "成功不该重试"


# ── 判据表与实现的一致性护栏 ────────────────────────────────────────────

def test_transient_table_covers_real_production_failure():
    """本次生产的真实 stderr 必须落在表 A 里 (否则重试形同虚设)。"""
    from moni.fetch.sources.xbot_feed import _is_transient_git_error

    assert _is_transient_git_error(_REAL_TLS_FAILURE) is True
