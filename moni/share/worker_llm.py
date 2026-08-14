"""moni.share.worker_llm — hermes chat 子进程调用 + LLM 失败检测 (sprint-12 B).

default_worker.sh 调 hermes 的调用从内嵌 heredoc python 抽到这里, 方便
pytest mock ``subprocess.Popen`` 验证 fail/done 两条路径 (v1.0.31)。

背景: hermes 有时退出码为 0 但 stdout 里其实是错误 (如 "API call failed" /
"HTTP 404"), 旧 worker 会把这类任务标成 done (假成功)。这里在子进程退出码
之上叠加 stdout 关键字判定: 命中失败关键字 → 返回退出码 1, default_worker.sh
沿用现有 "非零退出码 → status=failed" 分支, 错误消息 (stdout 前几行) 落
``share_tasks.error``。
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys

#: 失败关键字启发式 (PLAN sprint-12 B, assumption #2)。子串匹配、不区分大小写。
#: "HTTP 4"/"HTTP 5" 覆盖 "HTTP 404" 及任意 4xx/5xx。
#: "requires available credits" — 部分 provider (例如 google) 余额不足时的措辞。
LLM_FAILURE_MARKERS = (
    "API call failed",
    "HTTP 4",
    "HTTP 5",
    "rate limit",
    "insufficient credits",
    "requires available credits",
)


def is_llm_failure(stdout: str | None) -> bool:
    """True if hermes stdout 命中失败关键字 (即使子进程退出码为 0)。"""
    if not stdout:
        return False
    lowered = stdout.lower()
    return any(marker.lower() in lowered for marker in LLM_FAILURE_MARKERS)


def run_hermes(
    prompt: str,
    timeout_sec: int = 300,
    extra_args: str = "",
    skill: str = "",
) -> tuple[str, int]:
    """Invoke hermes chat CLI and return ``(stdout, exit_code)``.

    语义与原 default_worker.sh 内嵌 python 一致:

    - argv: ``hermes -z <prompt> --profile <MONI_HERMES_PROFILE|default>``
      + ``-s <skill>`` (可选) + shlex 拆分后的 ``extra_args`` (--provider/--model)
    - stdout/stderr 合并 (``stderr=STDOUT``), ``text=True``
    - 超时: kill + wait, 返回 ``("", 124)`` (worker 的 124 分支对齐)
    - stdout 命中 ``LLM_FAILURE_MARKERS`` 且退出码为 0 → 返回 ``(stdout, 1)``,
      让 worker 走非零退出码 → failed 分支 (PLAN B)
    """
    profile = os.environ.get("MONI_HERMES_PROFILE", "default")
    argv = ["hermes", "-z", prompt, "--profile", profile]
    if skill.strip():
        argv.extend(["-s", skill.strip()])
    if extra_args.strip():
        argv.extend(shlex.split(extra_args))

    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        stdout, _ = proc.communicate(timeout=timeout_sec)
        if proc.returncode == 0 and is_llm_failure(stdout):
            return stdout or "", 1
        return stdout or "", proc.returncode
    except subprocess.TimeoutExpired:
        if proc is not None:
            proc.kill()
            proc.wait()
        return "", 124
    except Exception as exc:
        # Popen 启动失败 (如 hermes 不在 PATH) 等同调用失败
        return str(exc), 1


def _main(argv: list[str]) -> int:
    """CLI entry, 参数顺序与旧内嵌 python 一致:
    ``<timeout_sec> <prompt> [extra_args] [skill]``。
    """
    if len(argv) < 2:
        print(
            "usage: python3 -m moni.share.worker_llm "
            "<timeout_sec> <prompt> [extra_args] [skill]",
            file=sys.stderr,
        )
        return 2
    timeout_sec = int(argv[0])
    prompt = argv[1]
    extra_args = argv[2] if len(argv) > 2 else ""
    skill = argv[3] if len(argv) > 3 else ""
    stdout, rc = run_hermes(
        prompt,
        timeout_sec=timeout_sec,
        extra_args=extra_args,
        skill=skill,
    )
    sys.stdout.write(stdout)
    return rc


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
