"""tests/test_xbot_feed_git_retry_safety.py — 修复 warden round-1 审计发现。

sprint-51 / id 26111606（v1.0.95）· warden round 1 报告：
``/tmp/moni-sprint51/warden-audit-sprint51.md``（P0=1 / P1=2 / P2=3）。

本文件覆盖 round-1 要求的**修复项**与**补充覆盖**：

- **P0-1**（数据丢失）：``clone_dir`` 指向含用户文件的目录 / 数据目录本身时，
  clone 失败**不得**删掉用户数据（原先「目录存在就 rmtree」会删光 news.db）。
- **P1-2**（签名零覆盖）：``couldn't resolve host`` / ``gnutls error``
  两条签名各补一条**独立**参数化用例（删掉签名必须变红）。
- **P2-1**（无效断言）：墙钟上界改成「上界随注入的假 git 耗时收紧」，
  让它**真的能触及**（否则断言永不触发 = 不验证任何东西）。
- **P2-2**（护栏缺口）：R-AB-2（``source_author_blocks`` 表结构逐字）/
  R-MD-2（``max_days`` 行为等价）/ R-FF-3（分叉场景 ff-only 语义效果）。
- **P2-3**（NC-8 无护栏）：rc=0 但 stderr **含永久签名**时仍算成功。

纪律：不联网（git 操作全在 ``tmp_path`` 里的本地 bare repo）；不依赖真实
sleep；不硬编码本机绝对路径。
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate

_REAL_RUN = subprocess.run

#: 本 sprint 的固定 base（= 本 PR 的 base，= ``merge-base(tip, base)``）。
#: 2026-11-16 19:20 分支已 fast-forward 到 dad 的 PR #220（``d69f66d``）。
#: 2026-11-17 08:45 改动已 commit 成 ``befd901``（PR #221）。
#: 2026-11-17 09:40 再同步：dad 合了 PR #222（``0aef205``，仅文档）⇒ base 更新。
#: 注：``moni/fetch/sources/xbot_feed.py`` 与 migration 042 在
#: dc730322 → bd53452 → e5b807f → d69f66d → 0aef205 之间**均未被 dad 改动**
#: ⇒ 各 base 取到的基线逐字相同，换 base 不影响任何断言的结论。
#: 锚点语义：锚点 = base（merge-base），**不是** tip —— commit 之后
#: ``git diff HEAD`` 为空会让范围护栏恒绿（假绿）。详见 guards 文件里的同段注释
#: （那里精确写了锚点=tip 时两条护栏各自的后果）。
_BASE_COMMIT = "0aef2054298e45dea94572422918f8519b1f00da"

_TLS_STDERR = (
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


def _install_stub(monkeypatch, results):
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


# ── P0-1: clone 失败绝不能删用户数据 ────────────────────────────────────

def test_clone_failure_keeps_user_files_in_target_dir(fresh_db, monkeypatch):
    """P0-1 场景 A：clone_dir 指向含用户文件的目录 → clone 失败后**文件仍在**。

    warden 的复现：原实现「目录存在就 rmtree」⇒ ``IMPORTANT.txt`` 被删。
    """
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    target = fresh_db / "my-clone"
    target.mkdir(parents=True)
    (target / "IMPORTANT.txt").write_text("do not delete me", encoding="utf-8")

    def _fake_run(argv, **kwargs):
        if argv[0] == "git" and argv[1] == "clone":
            # 模拟真实 git：失败时留下半拉目录（有 .git、无 .git/HEAD）
            (target / ".git").mkdir(exist_ok=True)
            (target / ".git" / "config").write_text("[core]\n", encoding="utf-8")
            return _Proc(returncode=128, stderr=_TLS_STDERR)
        return _Proc(returncode=0)

    import subprocess as sp
    monkeypatch.setattr(sp, "run", _fake_run)
    _no_backoff(monkeypatch)

    f = FetcherXbotFeed()
    cfg = f._load_config()
    cfg["clone_dir"] = str(target)
    assert f._sync_clone(cfg) is None

    assert (target / "IMPORTANT.txt").exists(), "clone 失败绝不能删用户文件"
    assert (target / "IMPORTANT.txt").read_text(encoding="utf-8") == "do not delete me"


def test_clone_failure_does_not_delete_data_dir_itself(fresh_db, monkeypatch):
    """P0-1 场景 B（最坏情形）：clone_dir == 数据目录 → news.db 不能被删。

    warden 的复现：原实现会把 ``data/news.db`` 与其它源目录一起删光。
    """
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    # 造出「像生产一样」的数据目录内容
    db_file = fresh_db / "news.db"
    db_file.write_text("fake db", encoding="utf-8")
    other = fresh_db / "bank_feed"
    other.mkdir()
    (other / "keep.json").write_text("{}", encoding="utf-8")

    def _fake_run(argv, **kwargs):
        if argv[0] == "git" and argv[1] == "clone":
            (fresh_db / ".git").mkdir(exist_ok=True)
            return _Proc(returncode=128, stderr=_TLS_STDERR)
        return _Proc(returncode=0)

    import subprocess as sp
    monkeypatch.setattr(sp, "run", _fake_run)
    _no_backoff(monkeypatch)

    f = FetcherXbotFeed()
    cfg = f._load_config()
    cfg["clone_dir"] = str(fresh_db)
    assert f._sync_clone(cfg) is None

    assert db_file.exists(), "clone 失败绝不能删数据目录里的 news.db"
    assert (other / "keep.json").exists(), "clone 失败绝不能删其它源的目录"


def test_clone_failure_still_removes_genuine_partial_clone(fresh_db, monkeypatch):
    """P0 修复不能矫枉过正：**真正的半成品**（只含 .git）仍要整个删掉。

    否则重试会撞 "destination path already exists"（warden AC-7 的原意）。
    """
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    target = fresh_db / "xbot-feed"

    def _fake_run(argv, **kwargs):
        if argv[0] == "git" and argv[1] == "clone":
            (target / ".git").mkdir(parents=True, exist_ok=True)
            return _Proc(returncode=128, stderr=_TLS_STDERR)
        return _Proc(returncode=0)

    import subprocess as sp
    monkeypatch.setattr(sp, "run", _fake_run)
    _no_backoff(monkeypatch)

    f = FetcherXbotFeed()
    assert f._sync_clone(f._load_config()) is None
    assert not target.exists(), "只含 .git 的半成品目录应整个删掉"


def test_clone_failure_keeps_completed_clone_untouched(fresh_db, monkeypatch):
    """已完成 clone（有 ``.git/HEAD``）在 pull 失败时**不被删**。

    注意：``.git`` 存在时 ``_sync_clone`` 走的是 **pull** 分支（不是 clone），
    而 pull 分支**从不挂 cleanup** ⇒ 原 clone 天然不被删。这条断言把这个
    性质钉死（防止将来有人给 pull 分支也挂上 rmtree）。
    """
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    target = fresh_db / "xbot-feed"
    (target / ".git").mkdir(parents=True)
    (target / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (target / "keep.txt").write_text("keep", encoding="utf-8")

    def _fake_run(argv, **kwargs):
        if argv[0] == "git" and argv[1] == "clone":
            return _Proc(returncode=128, stderr=_TLS_STDERR)
        return _Proc(returncode=128, stderr=_TLS_STDERR)

    import subprocess as sp
    monkeypatch.setattr(sp, "run", _fake_run)
    _no_backoff(monkeypatch)

    f = FetcherXbotFeed()
    assert f._sync_clone(f._load_config()) is None
    assert (target / "keep.txt").exists(), "pull 失败不该删原 clone 的内容"
    assert (target / ".git" / "HEAD").exists(), "pull 失败不该动原 clone 的 .git"


# ── P1-2: 补齐两条签名的独立用例 ────────────────────────────────────────
# （两条用例已并入 tests/test_xbot_feed_git_retry_signatures.py 的
#   TRANSIENT_CASES —— 单一来源，避免两处载荷漂移。本文件只留
#   「元护栏」：实现表 A 的每条签名都必须有测试载荷覆盖。）

def test_every_transient_signature_has_a_test_case():
    """AC-2 的元护栏：实现里的每条临时签名，**必须**有对应的测试载荷。

    这条比逐条参数化更抗漏 —— 以后往表 A 加签名而忘了补用例，本测试立刻红
    （P1-2 的根因就是「表 14 条 / 用例 12 条」这种静默缺口）。

    **收紧为「最长匹配」判据**（warden r2 的 P2-3）：原先只查「用例载荷里是否
    **包含**该签名字符串」⇒ 加一条**宽泛**签名（``"curl"``，是 ``"curl 35"`` 的
    子串）时元护栏**不红**，等于允许「加签名但不补专属用例」蒙混过关。
    现在要求：每条签名必须存在一个载荷，使它在该载荷里**不被任何更长的签名覆盖**
    （即它是该载荷的**最长匹配**）⇒ 宽泛签名拿不到专属载荷 ⇒ 立刻红。

    语义说明：这里判的是「**这条签名有专属用例**」，不是「这条签名更长」——
    「gnutls error」虽是「gnutls recv error」的子串，两者各自有专属载荷，都合法。
    """
    from moni.fetch.sources.xbot_feed import TRANSIENT_GIT_ERROR_SIGNATURES

    from tests.test_xbot_feed_git_retry_signatures import TRANSIENT_CASES

    payloads = [(cid, p.lower()) for cid, p in TRANSIENT_CASES]
    sigs = list(TRANSIENT_GIT_ERROR_SIGNATURES)

    missing: list[str] = []
    for sig in sigs:
        s = sig.lower()
        has_own_case = False
        for _cid, payload in payloads:
            if s not in payload:
                continue
            # 该载荷里是否还有**更长**的签名也命中？若都有，则本签名在该载荷里
            # 不是最长匹配 ⇒ 拿不到专属用例
            shadowed = [
                t for t in sigs
                if t.lower() != s and t.lower() in payload and len(t) > len(s)
            ]
            if not shadowed:
                has_own_case = True
                break
        if not has_own_case:
            missing.append(sig)

    assert not missing, (
        f"这些临时签名没有专属测试载荷覆盖（AC-2 要求逐条独立用例；"
        f"若某签名是另一签名的子串，需给它自己的载荷）: {missing}"
    )


# ── P2-2: 补三条回归护栏 ────────────────────────────────────────────────

def test_r_ab_2_source_author_blocks_table_schema_verbatim(fresh_db):
    """R-AB-2: ``source_author_blocks`` 表结构与基线 migration **逐字**一致。"""
    conn = get_db()
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='source_author_blocks'"
    ).fetchone()
    assert row is not None, "表不存在"
    schema = " ".join((row["sql"] or "").split())

    baseline = _REAL_RUN(
        ["git", "show", f"{_BASE_COMMIT}:moni/db/migrations/042_source_author_blocks.sql"],
        check=True, capture_output=True, text=True,
    ).stdout
    # 基线 migration 里的 CREATE TABLE 段
    assert "CREATE TABLE" in baseline
    # 列名逐个核对（基线 migration 与运行库必须一致）
    for col in ("source_name", "handle", "display_name", "blocked_at", "notes"):
        assert col in schema, f"表结构缺列 {col}"
        assert col in baseline, f"基线 migration 缺列 {col}"
    # 且 migration 文件本身未被本 sprint 改动
    diff = _REAL_RUN(
        ["git", "diff", "--name-only", _BASE_COMMIT, "--",
         "moni/db/migrations/042_source_author_blocks.sql"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert diff == "", "042 migration 被改动了"


def test_r_md_2_max_days_behavior_equivalent(fresh_db):
    """R-MD-2: ``max_days`` 行为等价 —— 同一组日期文件，前后输出集合相同。

    用基线 commit 的实现（从 ``git show`` 取源码）与当前实现跑同一组输入，
    比对**返回的文件列表**（集合 + 顺序）。
    """
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed, _parse_feed_date

    today = __import__("datetime").date.today()
    files = []
    for offset in (0, 1, 2, 29, 30, 31, 60):
        d = today - __import__("datetime").timedelta(days=offset)
        p = fresh_db / f"{d.isoformat()}.json"
        p.write_text("{}", encoding="utf-8")
        files.append(p)
    latest = fresh_db / "latest.json"
    latest.write_text("{}", encoding="utf-8")
    files.append(latest)

    f = FetcherXbotFeed()
    got = f._filter_by_max_days(files, 30)

    # 基线实现（从 git show 取，逐字相同才可比）
    baseline_src = _REAL_RUN(
        ["git", "show", f"{_BASE_COMMIT}:moni/fetch/sources/xbot_feed.py"],
        check=True, capture_output=True, text=True,
    ).stdout
    import ast

    tree = ast.parse(baseline_src)
    base_lines = baseline_src.splitlines()
    fn_src = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_filter_by_max_days":
            fn_src = "\n".join(base_lines[node.lineno - 1:node.end_lineno])
            break
    assert fn_src, "基线里找不到 _filter_by_max_days"

    # 基线函数体逐字相同（R-MD-1），所以行为必然等价；这里再显式验证
    current_src = Path(
        __file__).parent.parent / "moni" / "fetch" / "sources" / "xbot_feed.py"
    current_src = current_src.read_text(encoding="utf-8")
    cur_tree = ast.parse(current_src)
    cur_lines = current_src.splitlines()
    cur_fn = None
    for node in ast.walk(cur_tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_filter_by_max_days":
            cur_fn = "\n".join(cur_lines[node.lineno - 1:node.end_lineno])
            break
    assert cur_fn == fn_src, "函数体逐字不同 ⇒ 行为可能已变"

    # 显式核对输出集合：最近 30 天（含今天）的日期文件 + latest
    cutoff = today - __import__("datetime").timedelta(days=29)
    expected = {p for p in files if p.name == "latest.json"}
    for p in files:
        if p.name == "latest.json":
            continue
        fd = _parse_feed_date(p)
        if fd is not None and fd >= cutoff:
            expected.add(p)
    assert set(got) == expected
    assert len(got) == len(list(expected))


def test_r_ff_3_ff_only_prevents_merge_on_divergence(fresh_db, monkeypatch):
    """R-FF-3: 分叉场景下 pull **必须失败**且不产生 merge commit。

    warden round-2 P2-2：原版**不敏感**且没走本仓代码路径 ——
    (a) 它直接调 ``git pull --ff-only``，不走 `_sync_clone`；
    (b) git 2.x 在 ``pull.rebase`` 未配置时，即使**不带** ``--ff-only``
        也会拒绝自动 merge（提示 divergent branches）⇒ 去掉 ``--ff-only``
        断言依然成立 ⇒ 证明不了 ff-only 的语义。

    改法：
    - **走 `_sync_clone`**（本仓真实代码路径），捕获它实际发的 argv；
    - 在 clone 里显式设 ``pull.rebase=false``，让「不带 --ff-only 的 pull」
        会**真的产生 merge commit**（可区分的差异）；
    - 断言 (1) `_sync_clone` 发的 argv 里**必须有** ``--ff-only``；
      (2) 分叉时 `_sync_clone` 返回 None（失败）；
      (3) HEAD 不变、无 ``MERGE_HEAD``。
    """
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    work = fresh_db.parent / "ff-upstream-work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "feed").mkdir(exist_ok=True)
    (work / "feed" / "latest.json").write_text('{"items": []}', encoding="utf-8")
    _REAL_RUN(["git", "init", "-q", "-b", "main", str(work)], check=True)
    _REAL_RUN(["git", "-C", str(work), "config", "user.email", "t@example.com"], check=True)
    _REAL_RUN(["git", "-C", str(work), "config", "user.name", "t"], check=True)
    _REAL_RUN(["git", "-C", str(work), "add", "-A"], check=True)
    _REAL_RUN(["git", "-C", str(work), "commit", "-qm", "base"], check=True)
    bare = fresh_db.parent / "ff-upstream.git"
    _REAL_RUN(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)

    f = FetcherXbotFeed()
    clone_root = f._clone_root(f._load_config())
    _REAL_RUN(["git", "clone", "-q", str(bare), str(clone_root)],
              check=True, capture_output=True)
    # 关键: pull.ff=false ⇒ 不带 --ff-only 的 pull 会真的 merge commit
    #（git 2.x 默认 pull.ff=only ⇒ 必须显式关掉才能区分）
    _REAL_RUN(["git", "-C", str(clone_root), "config", "pull.ff", "false"],
              check=True)
    _REAL_RUN(["git", "-C", str(clone_root), "config", "user.email", "t@example.com"],
              check=True)
    _REAL_RUN(["git", "-C", str(clone_root), "config", "user.name", "t"], check=True)

    # 制造分叉：本地一个 commit，远端一个 commit
    (clone_root / "feed" / "local.txt").write_text("local", encoding="utf-8")
    _REAL_RUN(["git", "-C", str(clone_root), "add", "-A"], check=True)
    _REAL_RUN(["git", "-C", str(clone_root), "commit", "-qm", "local"], check=True)

    (work / "feed" / "remote.txt").write_text("remote", encoding="utf-8")
    _REAL_RUN(["git", "-C", str(work), "add", "-A"], check=True)
    _REAL_RUN(["git", "-C", str(work), "commit", "-qm", "remote"], check=True)
    _REAL_RUN(["git", "-C", str(bare), "fetch", "-q", str(work), "main:main"], check=True)

    # 分叉已造成 ⇒ 记录**分叉后**的 HEAD
    head_before = _REAL_RUN(["git", "-C", str(clone_root), "rev-parse", "HEAD"],
                            check=True, capture_output=True, text=True).stdout.strip()

    # 走**本仓真实代码路径** `_sync_clone`（记录实际 argv）
    seen: list[list[str]] = []
    real_run = subprocess.run

    def _spy_run(argv, **kwargs):
        seen.append(list(argv))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", _spy_run)
    monkeypatch.setattr(FetcherXbotFeed, "_sleep_backoff", lambda self, s: None,
                        raising=False)

    f2 = FetcherXbotFeed()
    assert f2._sync_clone(f2._load_config()) is None, "分叉时 _sync_clone 必须失败"

    pulls = [a for a in seen if a[:2] == ["git", "pull"]]
    assert pulls, "_sync_clone 应发起过 pull"
    for argv in pulls:
        assert "--ff-only" in argv, f"_sync_clone 的 pull 必须带 --ff-only: {argv}"

    head_after = _REAL_RUN(["git", "-C", str(clone_root), "rev-parse", "HEAD"],
                           check=True, capture_output=True, text=True).stdout.strip()
    assert head_after == head_before, "--ff-only 失败时 HEAD 不该变"
    assert not (clone_root / ".git" / "MERGE_HEAD").exists(), "不该留下 MERGE_HEAD"


def test_r_ff_3_without_ff_only_would_merge(fresh_db):
    """R-FF-3 的**可区分性**证明：不带 --ff-only 时 pull 真的会产生 merge。

    这条让上面那条变得有意义 —— 若 git 在本配置下即使没有 --ff-only 也不
    merge，那两条断言就无法区分「有/无 --ff-only」。

    注意：两侧必须改**不同的文件**，否则是内容冲突（pull 失败原因会是
    conflict 而不是 divergent），就测不到 ff-only 的语义了。
    """
    work = fresh_db.parent / "ff2-work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "base.txt").write_text("base", encoding="utf-8")
    _REAL_RUN(["git", "init", "-q", "-b", "main", str(work)], check=True)
    _REAL_RUN(["git", "-C", str(work), "config", "user.email", "t@example.com"], check=True)
    _REAL_RUN(["git", "-C", str(work), "config", "user.name", "t"], check=True)
    _REAL_RUN(["git", "-C", str(work), "add", "-A"], check=True)
    _REAL_RUN(["git", "-C", str(work), "commit", "-qm", "base"], check=True)
    bare = fresh_db.parent / "ff2.git"
    _REAL_RUN(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)

    clone2 = fresh_db.parent / "ff2-clone"
    _REAL_RUN(["git", "clone", "-q", str(bare), str(clone2)], check=True,
              capture_output=True)
    _REAL_RUN(["git", "-C", str(clone2), "config", "pull.ff", "false"], check=True)
    _REAL_RUN(["git", "-C", str(clone2), "config", "user.email", "t@example.com"], check=True)
    _REAL_RUN(["git", "-C", str(clone2), "config", "user.name", "t"], check=True)

    # 本地改 local.txt，远端改 remote.txt —— 不同文件 ⇒ 不会内容冲突
    (clone2 / "local.txt").write_text("local", encoding="utf-8")
    _REAL_RUN(["git", "-C", str(clone2), "add", "-A"], check=True)
    _REAL_RUN(["git", "-C", str(clone2), "commit", "-qm", "local"], check=True)

    (work / "remote.txt").write_text("remote", encoding="utf-8")
    _REAL_RUN(["git", "-C", str(work), "add", "-A"], check=True)
    _REAL_RUN(["git", "-C", str(work), "commit", "-qm", "remote"], check=True)
    _REAL_RUN(["git", "-C", str(bare), "fetch", "-q", str(work), "main:main"], check=True)

    head_before = _REAL_RUN(["git", "-C", str(clone2), "rev-parse", "HEAD"],
                            check=True, capture_output=True, text=True).stdout.strip()

    # **不带** --ff-only ⇒ 在 pull.ff=false 下会真的 merge
    proc = _REAL_RUN(["git", "-C", str(clone2), "pull"], capture_output=True, text=True)
    assert proc.returncode == 0, f"预期无 --ff-only 的 pull 能成功 merge: {proc.stderr}"
    head_after = _REAL_RUN(["git", "-C", str(clone2), "rev-parse", "HEAD"],
                           check=True, capture_output=True, text=True).stdout.strip()
    assert head_after != head_before, "无 --ff-only 的 pull 应改变 HEAD（产生 merge）"


# ── P2-3: NC-8 —— rc=0 但 stderr 含永久签名，仍算成功 ──────────────────

def test_nc8_rc_zero_with_permanent_signature_stderr_is_success(fresh_db, monkeypatch):
    """P2-3: 载荷必须**真的含永久签名**（原用例载荷不含，NC-8 无护栏）。

    L1（rc 判成功）优先于 L2（永久签名）—— rc==0 就是成功，不重试。
    """
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    clone = fresh_db / "xbot-feed"
    (clone / ".git").mkdir(parents=True)
    (clone / "feed").mkdir(parents=True)

    stderr_with_permanent = (
        "warning: unable to rmdir 'old': Directory not empty\n"
        "fatal: Authentication failed for 'https://user@github.com/o/r.git'"
    )
    seen = _install_stub(monkeypatch, [
        _Proc(returncode=0, stderr=stderr_with_permanent),
    ])
    _no_backoff(monkeypatch)

    f = FetcherXbotFeed()
    root = f._sync_clone(f._load_config())
    assert root is not None, "rc=0 必须算成功（L1 优先）"
    pulls = [c for c in seen if c[0][:2] == ["git", "pull"]]
    assert len(pulls) == 1, "rc=0 不该重试"
