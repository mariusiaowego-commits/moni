"""tests/conftest.py — 全局测试隔离护栏。

v1.0.51: 之前 `tests/test_api.py::test_put_update_200` 会调
`PUT /api/fetch/sources`，而那个端点把请求体里的字段**应用到每一个源**，
然后把整个 `config/sources.yaml` 重写回去 —— 也就是说，**跑一次测试
套件就会改生产配置文件**。当时所有源的 `crawler_type` 恰好都是 `rss`，
所以重写出来的内容碰巧一模一样，`git status` 一直是干净的 —— 属于
"靠运气不炸"。

端点本身已经删掉了（PR #151），但这里再加一道**兜底**：整个测试会话
期间，任何指向真实 `config/sources.yaml` 的写操作都不可能落地。

做法：把 `moni.web.routes.fetch._CONFIG_PATH` 指向一份 tmp 副本，
并在会话结束时断言真实文件字节没变。这样"测试改了生产配置"这件事
会**当场炸**，而不是靠人肉 `git diff` 发现。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_SOURCES_YAML = REPO_ROOT / "config" / "sources.yaml"
REAL_DATA_DIR = REPO_ROOT / "data"


def _snapshot_data_dir(dir_path: Path) -> dict[str, tuple[int, bytes]]:
    """Snapshot files under dir_path: relative path -> (size, sha256)."""
    if not dir_path.exists():
        return {}
    import hashlib
    res = {}
    for p in dir_path.rglob("*"):
        if p.is_file():
            try:
                h = hashlib.sha256()
                with open(p, "rb") as f:
                    while chunk := f.read(65536):
                        h.update(chunk)
                res[str(p.relative_to(dir_path))] = (p.stat().st_size, h.digest())
            except OSError:
                pass
    return res


def assert_data_dir_unchanged(
    before: dict[str, tuple[int, bytes]],
    after: dict[str, tuple[int, bytes]],
) -> None:
    """Fail when a test session added, removed, or rewrote a data file."""
    diff_added = set(after.keys()) - set(before.keys())
    diff_removed = set(before.keys()) - set(after.keys())
    diff_changed = {
        k for k in set(before.keys()) & set(after.keys())
        if before[k] != after[k]
    }
    assert not diff_added and not diff_removed and not diff_changed, (
        f"测试套件改动了生产 data/ 目录！\n"
        f"新增文件: {diff_added}\n"
        f"删除文件: {diff_removed}\n"
        f"修改文件: {diff_changed}\n"
        "请确保测试上下文使用 MONI_DATA_DIR 指向临时目录。"
    )


@pytest.fixture(scope="session", autouse=True)
def _isolate_data_dir():
    """Never let the test suite write into the repo's real data/ directory."""
    import os
    real = REAL_DATA_DIR
    before = _snapshot_data_dir(real)

    tmp_base = Path(tempfile.mkdtemp(prefix="moni-test-"))
    tmp_data_dir = tmp_base / "data"
    tmp_data_dir.mkdir(parents=True, exist_ok=True)
    (tmp_data_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (tmp_data_dir / ".last_run.json").write_text("{}", encoding="utf-8")

    orig_env = os.environ.get("MONI_DATA_DIR")
    os.environ["MONI_DATA_DIR"] = str(tmp_data_dir)

    from moni.db import connection as db_conn
    try:
        yield
    finally:
        if orig_env is None:
            os.environ.pop("MONI_DATA_DIR", None)
        else:
            os.environ["MONI_DATA_DIR"] = orig_env

        db_conn.close_db()
        shutil.rmtree(tmp_base, ignore_errors=True)

    after = _snapshot_data_dir(real)
    assert_data_dir_unchanged(before, after)


@pytest.fixture(scope="session", autouse=True)
def _isolate_fetch_sources_config():
    """Never let the test suite write the repo's real config/sources.yaml."""
    from moni.web.routes import fetch as fetch_route
    from moni.fetch import scheduler as fetch_scheduler
    from moni.fetch import health as fetch_health

    real = REAL_SOURCES_YAML
    if not real.exists():
        import warnings
        warnings.warn(
            f"Real config file {real} not found; cannot isolate sources config.",
            RuntimeWarning,
        )
        yield
        return

    before = real.read_bytes()
    tmp_dir = Path(tempfile.mkdtemp(prefix="moni-test-config-"))
    try:
        tmp_copy = tmp_dir / "sources.yaml"
        shutil.copy2(real, tmp_copy)

        orig_fetch = fetch_route._CONFIG_PATH
        orig_sched = fetch_scheduler._CONFIG_PATH
        orig_health = fetch_health._CONFIG_PATH

        fetch_route._CONFIG_PATH = tmp_copy
        fetch_scheduler._CONFIG_PATH = tmp_copy
        fetch_health._CONFIG_PATH = tmp_copy
        try:
            yield
        finally:
            fetch_route._CONFIG_PATH = orig_fetch
            fetch_scheduler._CONFIG_PATH = orig_sched
            fetch_health._CONFIG_PATH = orig_health
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    after = real.read_bytes()
    assert before == after, (
        "测试套件改动了真实的 config/sources.yaml —— 那是生产配置，"
        "不是测试夹具。查一下是不是哪个端点在写它。"
    )

