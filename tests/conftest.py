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


@pytest.fixture(scope="session", autouse=True)
def _isolate_fetch_sources_config():
    """Never let the test suite write the repo's real config/sources.yaml."""
    from moni.web.routes import fetch as fetch_route

    real = fetch_route._CONFIG_PATH
    if not real.exists():
        yield
        return

    before = real.read_bytes()
    tmp_dir = Path(tempfile.mkdtemp(prefix="moni-test-config-"))
    try:
        tmp_copy = tmp_dir / "sources.yaml"
        shutil.copy2(real, tmp_copy)
        fetch_route._CONFIG_PATH = tmp_copy
        try:
            yield
        finally:
            fetch_route._CONFIG_PATH = real
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    after = real.read_bytes()
    assert before == after, (
        "测试套件改动了真实的 config/sources.yaml —— 那是生产配置，"
        "不是测试夹具。查一下是不是哪个端点在写它。"
    )

