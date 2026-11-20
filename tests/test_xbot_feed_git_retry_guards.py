"""tests/test_xbot_feed_git_retry_guards.py — 退避序列 / 不死锁 / 回归护栏。

sprint-51 / id 26111606（v1.0.95）。

覆盖 warden done-criteria 剩下几条：

- **AC-6** 退避被请求的秒数序列 == [2, 4]（**不靠真 sleep 计时** ——
  warden 脆弱测试警示 W-2；墙钟只断言上界，不设下界）。
- **AC-12** 不死锁：新代码内**无锁构造**（AST 断言）+ 并发调用都能返回
  （用 join 超时判定，不断言精确耗时 —— warden W-5）。
- **R-FF-1 / R-FF-2** pull / clone 的 argv **列表完全相等**（不是「包含
  --ff-only」）。
- **R-MD-1 / R-MD-3** ``_filter_by_max_days`` 函数体与 ``max_days`` 默认值
  逐字未变。
- **R-AB-3** ``author_blocked`` 仍是独立标量 int。

纪律：不联网；不依赖真实 sleep；不硬编码本机绝对路径。
"""

from __future__ import annotations

import ast
import subprocess
import threading
import time
from pathlib import Path

import pytest

from moni.db.connection import close_db, set_db_path
from moni.db.migrate import migrate

_SOURCE_NAME = "xbot_feed"
_SOURCE_ID = "src-xbot-feed"

_SRC_FILE = Path(__file__).parent.parent / "moni" / "fetch" / "sources" / "xbot_feed.py"

#: 本 sprint 的**固定** base commit（= 本 PR 的 base，= ``merge-base(tip, base)``）。
#:
#: 为什么不用 ``origin/main``：那个 ref 会**随别人合并而移动** —— 本次
#: 写测试期间 dad 合了 PR #216（``e104fbb``），``git diff origin/main``
#: 就把**他的** main.css / index.html / reader.js 算成本 sprint 的改动，
#: 范围护栏误报。护栏必须钉在**固定的 base commit** 上。
#:
#: 2026-11-16 19:20 同步：dad 又合了 PR #217（``bd53452``）、PR #218
#: （``e5b807f``，migration 044）、PR #219 / #220（``d69f66d``，web 服务胶囊）。
#: 2026-11-17 08:45 提交：改动已 commit 成 ``befd901``（PR #221）。
#: 2026-11-17 09:40 再同步：dad 合了 PR #222（``0aef205``，**仅改 STATUS.md /
#: USER_GUIDE.md 两个文档**）⇒ 本分支已 merge，base 随之更新为 ``0aef205``。
#:
#: **锚点语义（关键，勿改）**：锚点 = **base（merge-base）**，不是分支 tip。
#: ``git diff <base> -- moni/`` 恒等于「本 PR 相对 base 的改动」，
#: 与工作区是否已提交**无关** —— 这是 commit 前后都成立的前提。
#:
#: ⚠️ **绝不可把锚点改成 tip（``HEAD``）** —— commit 之后
#: ``git diff HEAD`` 为空 ⇒ 两条护栏的**后果不同**（实测变异验证过）：
#: - ``test_no_migration_or_web_changes`` 断言「越界集合为空」⇒ diff 恒空
#:   ⇒ **恒绿（假绿）**，越界改动再也咬不住；
#: - ``test_only_one_source_file_changed`` 断言「恰好是 xbot_feed.py」⇒ diff 为空
#:   ⇒ **fail-closed 变红**（还能靠它发现锚点被改）。
#: ⇒ 两条护栏**不能互相替代**；锚点必须是 base。
#:
#: **为什么锚点必须是 base 而不是更早的提交**：本 sprint 的改动在 base 之上
#: 是**已提交**的（squash 合并成 ``60a2a06``）。写测试与提交之间 base 曾多次前移
#: （dad 连续合 PR #216~#222），每次都要跟着更新，否则 dad 在那之后的任何合并
#: 都会被算成「我的越界改动」
#: （实测：base 停在 ``bd53452`` 时，dad 的 PR #218 的 ``moni/db/migrate.py``
#: 与 ``044_orphan_rows_cleanup.sql`` 会让两条范围护栏**误报失败**）。
#:
#: **残留提醒（可接受）**：base 是硬编码，历史被重写时会 **fail-closed**
#: （测试红），不会假绿。
BASE_COMMIT = "0aef2054298e45dea94572422918f8519b1f00da"
#: sprint-51 的**合并提交**（``60a2a06`` = PR #221 squash 合并进 main 的那个
#: commit）。范围护栏钉在 ``BASE_COMMIT..SPRINT_TIP`` 这条**已合并**的历史区间
#: 上：sprint-51 之后的分支（如 52）不再触发误报，而 sprint-51 本身的越界改动
#: 仍能被这两条断言咬住。
#:
#: ⚠️ **必须钉合并提交，不能钉分支提交**（2026-11-20 踩过，CI run 44664882603）：
#: 原来钉的是分支上的 ``befd901``。PR #221 走的是 **squash 合并** —— 进 main 的
#: 是新提交 ``60a2a06``，``befd901`` **从未进入 main 历史**。本地能过纯属侥幸
#: （本机对象库里还残留着那个对象）；CI 每次全新 checkout 只拿 main 可达的对象
#: ⇒ ``git diff`` 退出码 128 ⇒ 两条护栏红。之前一直是绿的，是因为 PR 分支还在
#: 远端时 ``fetch-depth: 0`` 把 ``refs/heads/*`` 一起拉了，分支一删对象就消失，
#: 属于**延迟爆的红**。
#:
#: 同理，任何后续维护：改这里之前先跑
#: ``git merge-base --is-ancestor <新锚点> origin/main``，必须为真（永久可达）。
SPRINT_TIP = "60a2a069746885ff4d6e4ec4fe9fa32c571c07e1"


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


def _install_stub(monkeypatch, results):
    import subprocess as sp

    seq = list(results)
    seen = []

    def _fake_run(argv, **kwargs):
        seen.append((list(argv), kwargs))
        return seq.pop(0) if seq else _Proc(returncode=0)

    monkeypatch.setattr(sp, "run", _fake_run)
    return seen


def _make_clone(data_dir) -> None:
    clone = data_dir / "xbot-feed"
    (clone / ".git").mkdir(parents=True)
    (clone / "feed").mkdir(parents=True)


def _tls_stderr() -> str:
    return (
        "fatal: unable to access 'https://github.com/mariusiaowego-commits/xbot-feed.git/': "
        "LibreSSL SSL_connect: SSL_ERROR_SYSCALL in connection to github.com:443"
    )


# ── AC-6: 退避序列 == [2, 4]（断言被请求的秒数，不真等）──────────────────

def test_backoff_sequence_is_two_then_four(fresh_db, monkeypatch):
    """AC-6: 3 次尝试的退避请求序列恰为 [2, 4]（第 1 次失败后 2s，第 2 次后 4s）。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    _make_clone(fresh_db)
    _install_stub(monkeypatch, [
        _Proc(returncode=128, stderr=_tls_stderr()),
        _Proc(returncode=128, stderr=_tls_stderr()),
        _Proc(returncode=128, stderr=_tls_stderr()),
    ])

    requested: list[float] = []
    monkeypatch.setattr(
        FetcherXbotFeed, "_sleep_backoff",
        lambda self, seconds: requested.append(seconds), raising=False,
    )

    f = FetcherXbotFeed()
    assert f._sync_clone(f._load_config()) is None
    assert requested == [2.0, 4.0], f"退避序列应恰为 [2, 4]，实际 {requested}"


def test_backoff_total_is_bounded(fresh_db, monkeypatch):
    """退避合计 ≤ 6s（取消后最坏多跑这么久 —— warden §4.1）。"""
    from moni.fetch.sources.xbot_feed import (
        GIT_RETRY_BACKOFF_SECONDS,
        GIT_RETRY_MAX_ATTEMPTS,
    )

    assert sum(GIT_RETRY_BACKOFF_SECONDS) <= 6.0
    # 退避次数 = 尝试次数 - 1
    assert len(GIT_RETRY_BACKOFF_SECONDS) >= GIT_RETRY_MAX_ATTEMPTS - 1


def test_wall_clock_upper_bound_only(fresh_db, monkeypatch):
    """AC-6: 墙钟只断言**上界**（不断言下界 —— warden W-10）。

    warden round-1 P2-1：退避被 no-op 化 ⇒ elapsed 恒≈0 ⇒ 上界永不触及。
    warden round-2 P2-1：**修得仍不对** —— 上界公式里的 ``backoff_total`` 与
    ``per_call`` **都是测试自己注入的常量** ⇒ 被测行为变慢时上界同步放大 ⇒
    依然恒真（实测：假 git 睡 0.6s / 退避改 5s / 退避改 no-op，三种变异
    都不红）。

    这次用**独立于被测输入的绝对常量**做上下界：
    - 注入的退避合计 = 0.06s，3 次假 git 合计 = 0.15s ⇒ 正常耗时 ≈ 0.21s
    - 上界 **1.0s**（绝对常量）：假 git 睡 0.6s ⇒ 1.8s > 1.0 ⇒ 红
    - 下界 **0.15s**（绝对常量）：3 次假 git 的最小耗时 ⇒ 退避被 no-op 化
      时 elapsed ≈ 0.15 仍 ≥ 0.15 … 这条对 no-op 不敏感，所以下界改成
      **0.20s**（0.15 + 退避 0.06 - 余量 0.01）⇒ 退避被 no-op 化 ⇒
      elapsed ≈ 0.15 < 0.20 ⇒ 红
    """
    from moni.fetch.sources import xbot_feed as xf
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    clone = fresh_db / "xbot-feed"
    (clone / ".git").mkdir(parents=True)
    (clone / "feed").mkdir(parents=True)

    per_call = 0.05

    def _fake_run(argv, **kwargs):
        time.sleep(per_call)
        return _Proc(returncode=128, stderr=_tls_stderr())

    import subprocess as sp
    monkeypatch.setattr(sp, "run", _fake_run)
    # 退避改小但**真等**（不 no-op）
    monkeypatch.setattr(xf, "GIT_RETRY_BACKOFF_SECONDS", (0.02, 0.04), raising=False)

    # 下界不靠墙钟（sleep 抖动会让 0.06s 的差值不可靠）—— 改成直接断言
    # **退避确实被请求过**（实现若跳过退避，这里就是 0 ⇒ 红）。
    requested: list[float] = []

    def _spy_backoff(self, seconds):
        requested.append(seconds)
        time.sleep(seconds)

    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", _spy_backoff,
                        raising=False)

    f = xf.FetcherXbotFeed()
    t0 = time.monotonic()
    assert f._sync_clone(f._load_config()) is None
    elapsed = time.monotonic() - t0

    # 上界: 绝对常量，与被测输入无关（假 git 睡 0.6s ⇒ 1.8s ⇒ 必红）
    UPPER = 1.0
    assert elapsed <= UPPER, f"墙钟上界被突破: {elapsed:.3f}s > {UPPER}s"
    # 下界: 退避**确实被请求过**（且被真等）
    assert requested == [0.02, 0.04], f"退避未被请求: {requested}"
    assert elapsed >= 0.15, f"3 次假 git 的最小耗时都没到: {elapsed:.3f}s"


# ── AC-12: 不死锁 ───────────────────────────────────────────────────────

def _lock_constructs_in(path: Path, func_names: set[str]) -> list[str]:
    """AST 扫描: 这些函数体内是否出现锁构造。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []

    class V(ast.NodeVisitor):
        def visit_FunctionDef(self, node):  # noqa: N802
            if node.name in func_names:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name) and sub.id in {
                        "Lock", "RLock", "Semaphore", "BoundedSemaphore"
                    }:
                        hits.append(f"{node.name}: {sub.id}")
                    if isinstance(sub, ast.Attribute) and sub.attr in {
                        "Lock", "RLock", "acquire", "release"
                    }:
                        hits.append(f"{node.name}: .{sub.attr}")
            self.generic_visit(node)

    V().visit(tree)
    return hits


def test_no_lock_constructs_in_new_code():
    """AC-12: 新增的重试代码里**没有**任何锁构造。"""
    hits = _lock_constructs_in(
        _SRC_FILE,
        {"_run_git_with_retry", "_backoff_for", "_sleep_backoff", "_sync_clone"},
    )
    assert hits == [], f"重试代码里出现锁构造: {hits}"


def test_concurrent_sync_clone_calls_all_return(fresh_db, monkeypatch):
    """AC-12 并发: 两个线程并发调 _sync_clone，都必须返回（无饿死/死锁）。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    _make_clone(fresh_db)
    _install_stub(monkeypatch, [
        _Proc(returncode=128, stderr=_tls_stderr()),
        _Proc(returncode=128, stderr=_tls_stderr()),
        _Proc(returncode=128, stderr=_tls_stderr()),
        _Proc(returncode=128, stderr=_tls_stderr()),
        _Proc(returncode=128, stderr=_tls_stderr()),
        _Proc(returncode=128, stderr=_tls_stderr()),
    ])
    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)

    results: list = []

    def _worker():
        f = FetcherXbotFeed()
        try:
            results.append(f._sync_clone(f._load_config()))
        except Exception as exc:  # pragma: no cover - 只为记录异常
            results.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    # join 超时 = 单次 git 超时(120) + 退避(6) + 余量 —— 断言「都返回」，
    # 不断言精确耗时（warden W-5）
    for t in threads:
        t.join(timeout=60)

    assert all(not t.is_alive() for t in threads), "有线程没返回（疑似死锁）"
    assert len(results) == 2
    assert all(r is None for r in results), f"两个调用都该失败返回 None: {results}"


# ── R-FF-1 / R-FF-2: argv 列表完全相等 ──────────────────────────────────

def test_pull_argv_is_exactly_ff_only(fresh_db, monkeypatch):
    """R-FF-1: pull 的 argv **完全相等** ["git", "pull", "--ff-only"]。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    _make_clone(fresh_db)
    seen = _install_stub(monkeypatch, [_Proc(returncode=0, stderr="")])
    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)

    f = FetcherXbotFeed()
    f._sync_clone(f._load_config())
    pulls = [argv for argv, _kw in seen if argv[:2] == ["git", "pull"]]
    assert pulls, "应发起过 pull"
    for argv in pulls:
        assert argv == ["git", "pull", "--ff-only"], f"pull argv 被改了: {argv}"


def test_clone_argv_keeps_dashdash_and_quiet(fresh_db, monkeypatch):
    """R-FF-2: clone 的 argv 保留 `--quiet` 与 `--` 分隔符。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    seen = _install_stub(monkeypatch, [_Proc(returncode=0, stderr="")])
    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)

    f = FetcherXbotFeed()
    f._sync_clone(f._load_config())
    clones = [argv for argv, _kw in seen if argv[:2] == ["git", "clone"]]
    assert clones, "应发起过 clone"
    for argv in clones:
        assert argv[:4] == ["git", "clone", "--quiet", "--"], \
            f"clone argv 的前 4 段被改了: {argv}"
        assert len(argv) == 6, f"clone argv 应是 [git, clone, --quiet, --, url, dir]: {argv}"


# ── R-MD-1 / R-MD-3: max_days 去重逐字未变 ─────────────────────────────

def _function_source(name: str) -> str:
    """从源文件里切出某个函数的源码文本。"""
    src = _SRC_FILE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            start = node.lineno - 1
            return "\n".join(lines[start:node.end_lineno])
    raise AssertionError(f"函数 {name} 没找到")


def test_filter_by_max_days_body_unchanged():
    """R-MD-1: _filter_by_max_days 的函数体**逐字**未变（用 AST 行号切片比对）。"""
    body = _function_source("_filter_by_max_days")
    assert "cutoff = today - timedelta(days=max_days - 1)" in body
    assert "file_date >= cutoff" in body
    # 关键：重试没碰这个函数的任何一行 —— 与基线 commit 逐字比对
    baseline = subprocess.run(
        ["git", "show", f"{BASE_COMMIT}:moni/fetch/sources/xbot_feed.py"],
        check=True, capture_output=True, text=True,
    ).stdout
    base_tree = ast.parse(baseline)
    base_lines = baseline.splitlines()
    for node in ast.walk(base_tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_filter_by_max_days":
            base_body = "\n".join(base_lines[node.lineno - 1:node.end_lineno])
            break
    else:  # pragma: no cover
        raise AssertionError("基线里没有 _filter_by_max_days")
    assert body == base_body, "_filter_by_max_days 被改动了"


def test_max_days_default_unchanged():
    """R-MD-3: DEFAULT_FETCH_CONFIG 里 max_days 仍逐字是 30。"""
    from moni.fetch.sources.xbot_feed import DEFAULT_FETCH_CONFIG

    assert DEFAULT_FETCH_CONFIG["max_days"] == 30


# ── R-AB-3: author_blocked 仍是独立标量 int ────────────────────────────

def test_author_blocked_counter_stays_independent_int():
    """R-AB-3: author_blocked 仍是独立 int 计数（不并入 threshold_dropped）。

    判据要精确：**代码里**不能把 author_blocked 与 threshold_dropped 混在
    一起赋值/累加（注释里提到 threshold_dropped 是正常的 —— 那正是
    「不并入」的说明文字）。
    """
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    f = FetcherXbotFeed()
    assert isinstance(f._author_blocked, int)
    assert f._author_blocked == 0

    src = _SRC_FILE.read_text(encoding="utf-8")
    # 独立赋值：result["author_blocked"] = self._author_blocked
    assert 'result["author_blocked"] = self._author_blocked' in src
    # 没有任何一行把 author_blocked 与 threshold_dropped 写在同一条赋值里
    for line in src.splitlines():
        code = line.split("#", 1)[0]  # 去掉行注释
        if "author_blocked" in code and "threshold_dropped" in code:
            raise AssertionError(f"author_blocked 与 threshold_dropped 混在一起: {line}")
    # threshold_dropped 的累加不涉及 author_blocked
    for line in src.splitlines():
        code = line.split("#", 1)[0]
        if "threshold_dropped" in code and "+=" in code and "author" in code:
            raise AssertionError(f"threshold_dropped 累加里混进了 author: {line}")


# ── 改动范围护栏（warden EV-15）─────────────────────────────────────────

def _require_commit(rev: str) -> None:
    """锚点必须在本 clone 里存在，否则 fail-closed 并给可操作的报错。

    范围护栏断言的是**已合并进 main 的历史区间**，天然要求完整历史。浅克隆
    （``fetch-depth: 1``）或历史被改写时对象会缺失；此时 ``git diff`` 只会抛
    ``CalledProcessError: exit status 128``，看不出该改什么 —— 这里先显式
    报出来。**不 skip**：跳过会让护栏静默变成假绿。
    """
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{rev}^{{commit}}"],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        raise AssertionError(
            f"锚点 commit {rev} 在本 clone 里不存在。范围护栏钉的是已合并进 main "
            f"的历史区间，需要完整历史（CI 的 actions/checkout 必须 fetch-depth: 0；"
            f"本地请先 git fetch --all）。若确认历史被改写，请把 BASE_COMMIT / "
            f"SPRINT_TIP 换成仍可从 main 到达的等价区间（"
            f"git merge-base --is-ancestor <rev> origin/main 必须为真）。"
        )


def _diff_names(*pathspec: str) -> list[str]:
    """``BASE_COMMIT..SPRINT_TIP`` 区间内的改动文件名，锚点缺失时 fail-closed。"""
    for rev in (BASE_COMMIT, SPRINT_TIP):
        _require_commit(rev)
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{BASE_COMMIT}..{SPRINT_TIP}", "--", *pathspec],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"git diff {BASE_COMMIT}..{SPRINT_TIP} 失败（exit {proc.returncode}）："
            f"{proc.stderr.strip()}"
        )
    return proc.stdout.split()


def test_only_one_source_file_changed():
    """EV-15: sprint-51 生产代码只改了 1 个文件（xbot_feed.py）。"""
    out = _diff_names("moni/")
    assert out == ["moni/fetch/sources/xbot_feed.py"], \
        f"生产代码改动应只有 xbot_feed.py，实际: {out}"


def test_no_migration_or_web_changes():
    """sprint-51 区间内不许动 db / web / 前端（范围收窄）。"""
    out = _diff_names("moni/db/", "moni/web/", "static/", "config/")
    assert out == [], f"越界改动: {out}"
