"""PNG 分享任务的 hermes 超时必须比通用任务长。

产物实际 4-11 分钟才出来；600s 会在 agent 收尾自检时砍掉已经生成的 PNG
（2026-11-12 任务 PdnKZBj_ylX6XIjqy6Zc9A：600s 超时，13:31 已产出
1080x1920 PNG，13:33 agent 才收尾）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "default_worker.sh"


def _resolve(fmt: str, **env: str) -> str:
    """Source the real resolver from default_worker.sh and call it."""
    env_exports = " ".join(f"{k}={v!r}" for k, v in env.items())
    script = f"""
set -eu
export {env_exports}
TIMEOUT_SECONDS="${{MONI_WORKER_TIMEOUT:-600}}"
eval "$(sed -n '/^task_timeout_for_format()/,/^}}/p' {SCRIPT})"
task_timeout_for_format {fmt}
"""
    out = subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


@pytest.mark.parametrize("fmt", ["info_graphic_png", "report_png"])
def test_png_formats_get_longer_timeout(fmt: str) -> None:
    assert _resolve(fmt, MONI_WORKER_TIMEOUT="600") == "1200"


@pytest.mark.parametrize("fmt", ["html_landing", "markdown", "plain_text"])
def test_other_formats_keep_default_timeout(fmt: str) -> None:
    assert _resolve(fmt, MONI_WORKER_TIMEOUT="600") == "600"


def test_png_timeout_env_override() -> None:
    assert _resolve("info_graphic_png", MONI_WORKER_TIMEOUT_PNG="1500") == "1500"
