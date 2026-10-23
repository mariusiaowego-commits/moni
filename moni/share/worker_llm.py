"""moni.share.worker_llm — LLM invocation helper for default_worker.sh.

v1.0.31 (sprint-12): PNG 生图流程重新设计的三块核心逻辑:

1. **A1 显式 provider/model** — 每次调 hermes 都从 DB 读
   ``llm_settings.active_summary_model_id`` → ``llm_models`` →
   ``llm_providers``,拼成显式 ``--provider X -m Y``,不再依赖
   ``~/.hermes/config.yaml`` 的默认 provider。用户在 manifest payload
   里的 provider/model override 优先。

2. **A2 LLM 失败检测** — hermes 可能返回退出码 0 但 stdout 实际是错误
   消息(API call failed / HTTP 4xx / HTTP 5xx / rate limit /
   insufficient credits / requires available credits)。检测到任一关键字
   就返回退出码 1,让 worker 走 failed 分支而不是"假 done"。

3. **A3 远程 PNG 下载 + 校验** — 提取 hermes_output 里的 fal.media 等
   远程 PNG URL,curl 下载到 artifacts 目录,用 PNG magic bytes 校验;
   校验不过的产物不会冒充 PNG 落盘。

CLI 由 ``scripts/default_worker.sh`` 以 subprocess 调用,避免 bash
拼 ``hermes`` argv 的转义问题:

    python3 moni/share/worker_llm.py --db data/news.db \\
        --timeout 600 --skill baoyu-infographic \\
        --provider <override> --model <override> \\
        --output /tmp/out.txt -- '<prompt>'

退出码: 0 = 成功; 124 = 超时; 1 = LLM 失败(非零退出或 stdout 命中
失败关键字)或运行期错误。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from moni.db.connection import get_db_path

#: 默认 profile —— 与 summary_gen / llm.client 保持一致。
DEFAULT_PROFILE = os.environ.get("MONI_HERMES_PROFILE", "default")

#: DB 配置缺失时的兜底 (与 moni/share/enrichment.py 一致)。
DEFAULT_PROVIDER = "minimax-cn"
DEFAULT_MODEL = "MiniMax-M3"

#: A2: hermes 返回 0 但 stdout 实际失败的信号关键字。
#: 采样样本: "API call failed", "HTTP 4xx", rate limit, 余额不足。
FAILURE_PATTERNS = [
    r"API\s+call\s+failed",
    r"HTTP\s+4\d\d\b",
    r"HTTP\s+4xx\b",
    r"HTTP\s+5\d\d\b",
    r"HTTP\s+5xx\b",
    r"rate\s+limit",
    r"insufficient\s+credits",
    r"requires\s+available\s+credits",
]
_FAILURE_RE = re.compile(
    "|".join(f"(?:{p})" for p in FAILURE_PATTERNS),
    re.IGNORECASE,
)

#: PNG magic bytes: 89 50 4E 47 0D 0A 1A 0A
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: 远程 PNG URL 识别: 协议 http(s) + 路径以 .png 结尾。
#: 覆盖 fal.media (v3b.fal.media / fal.media) 及任何 CDN。
REMOTE_PNG_RE = re.compile(r"https?://[^\s<>\"']+?\.png(?:\?[^\s<>\"']*)?", re.IGNORECASE)


def resolve_provider_model(
    db_path: str | os.PathLike[str] | None = None,
    *,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> tuple[str, str]:
    """Resolve (hermes_provider, model_id) 显式传给 hermes。

    Priority: CLI/env override → llm_settings.active_summary_model_id
    → llm_models JOIN llm_providers → 兜底 minimax-cn / MiniMax-M3。
    任何一步查不到都不 raise(生图流程不许因 DB 缺行直接挂)。
    """
    provider = (provider_override or "").strip() or None
    model = (model_override or "").strip() or None
    if provider and model:
        return provider, model

    conn = None
    try:
        conn = sqlite3.connect(str(db_path or get_db_path()))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT value FROM llm_settings WHERE key = 'active_summary_model_id'"
        ).fetchone()
        if row and row["value"]:
            mrow = conn.execute(
                """
                SELECT m.model_id, p.hermes_provider
                FROM llm_models m
                JOIN llm_providers p ON p.id = m.provider_id
                WHERE m.id = ?
                """,
                (row["value"],),
            ).fetchone()
            if mrow:
                provider = provider or mrow["hermes_provider"]
                model = model or mrow["model_id"]
    except (sqlite3.Error, OSError, TypeError):
        # DB 缺表/文件不存在 → 走兜底, 不炸 worker。
        pass
    finally:
        if conn is not None:
            conn.close()

    return provider or DEFAULT_PROVIDER, model or DEFAULT_MODEL


def detect_llm_failure(stdout: str) -> str | None:
    """A2: 检查 stdout 是否命中失败关键字。

    Returns:
        命中的关键字文本; 未命中返回 None。
    """
    if not stdout:
        return None
    for pattern in FAILURE_PATTERNS:
        m = re.search(pattern, stdout, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def _build_argv(
    prompt: str,
    provider: str,
    model: str,
    *,
    profile: str = DEFAULT_PROFILE,
    skill: str | None = None,
    usage_file: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Build hermes argv — 显式 --provider / -m,不依赖用户默认配置。"""
    argv = ["hermes", "-z", prompt, "--profile", profile]
    if skill and skill.strip():
        argv.extend(["-s", skill.strip()])
    argv.extend(["--provider", provider, "-m", model])
    # v1.0.32: hermes -z 支持 --usage-file (one-shot 模式), 失败也写文件。
    if usage_file:
        argv.extend(["--usage-file", str(usage_file)])
    return argv


def parse_usage_file(
    usage_file: str | os.PathLike[str] | None,
) -> dict[str, int | float | None]:
    """Parse hermes --usage-file JSON into usage counters.

    Returns keys: input_tokens / output_tokens / cost_usd (missing or
    null → 0).  Also keeps the raw dict under 'raw' for debugging.
    """
    empty: dict[str, int | float | None] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "raw": {},
    }
    if not usage_file:
        return empty
    path = Path(usage_file)
    if not path.is_file():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    def _num(key: str) -> int | float | None:
        val = data.get(key)
        try:
            return int(val) if val is not None else 0
        except (TypeError, ValueError):
            try:
                return float(val)
            except (TypeError, ValueError):
                return 0
    cost = data.get("estimated_cost_usd")
    try:
        cost_f = float(cost) if cost is not None else 0.0
    except (TypeError, ValueError):
        cost_f = 0.0
    return {
        "input_tokens": _num("input_tokens"),
        "output_tokens": _num("output_tokens"),
        "cost_usd": cost_f,
        "raw": data,
    }


def run_hermes(
    prompt: str,
    *,
    db_path: str | os.PathLike[str] | None = None,
    timeout_sec: int = 600,
    skill: str | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
    usage_file: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Invoke hermes and return a dict with output + usage counters.

    Returns::
        {
            "stdout": str,
            "exit_code": 0 | 1 | 124 | rc,
            "input_tokens": int,
            "output_tokens": int,
            "cost_usd": float,
            "usage": dict,   # parse_usage_file() result incl. raw
        }

    Exit codes mirror the old worker semantics:
      0   — hermes 成功且 stdout 无失败关键字
      124 — subprocess 超时
      1   — hermes 非零退出 / stdout 命中失败关键字 / 运行期错误
    """
    provider, model = resolve_provider_model(
        db_path,
        provider_override=provider_override,
        model_override=model_override,
    )
    argv = _build_argv(prompt, provider, model, skill=skill, usage_file=usage_file)

    def _result(stdout: str, code: int) -> dict[str, Any]:
        usage = parse_usage_file(usage_file)
        return {
            "stdout": stdout,
            "exit_code": code,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "cost_usd": usage["cost_usd"],
            "usage": usage,
        }

    proc: subprocess.Popen[str] | None = None
    try:
        # v1.0.42: route through moni.llm.runner so subprocess killpg +
        # argv pre-check + stdin transport handling come for free.
        from moni.llm.runner import HardenedSubprocess
        argv = _build_argv(prompt, provider, model, profile=DEFAULT_PROFILE,
                           skill=skill, usage_file=usage_file)
        # Strip the prompt arg that _build_argv inlined; HardenedSubprocess
        # appends the prompt itself as the last argv.
        argv = [a for a in argv if a != prompt]
        runner = HardenedSubprocess(cmd=argv, prompt_text=prompt, timeout=timeout_sec)
        sub = runner.run()
        stdout = sub.stdout
        code = sub.returncode
    except subprocess.TimeoutExpired:
        # HardenedSubprocess already killed the process group; just report.
        return _result("", 124)
    except FileNotFoundError:
        return _result(
            "hermes CLI not found on PATH — install Hermes Agent or fix $PATH.\n", 1
        )
    except Exception as exc:  # noqa: BLE001 — worker 不能因运行期异常挂掉
        return _result(f"worker_llm run_hermes error: {exc!r}\n", 1)

    if code != 0:
        return _result(stdout, code)

    hit = detect_llm_failure(stdout or "")
    if hit:
        return _result(stdout, 1)
    return _result(stdout, 0)


def extract_remote_png_urls(output: str) -> list[str]:
    """Extract remote PNG URLs (fal.media 等) from hermes stdout."""
    seen: list[str] = []
    for m in REMOTE_PNG_RE.finditer(output or ""):
        url = m.group(0)
        # 去掉尾部闭合标点 (逗号/括号/引号可能被 regex 吞进来)。
        url = url.rstrip(".,;:!?)]}>\"'")
        if url not in seen:
            seen.append(url)
    return seen


def is_png(path: str | os.PathLike[str]) -> bool:
    """A3: PNG magic bytes 校验 (89 50 4E 47 0D 0A 1A 0A)。"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return False
    return head == PNG_MAGIC


def download_png(url: str, dest: str | os.PathLike[str], timeout_sec: int = 60) -> bool:
    """curl 下载远程 PNG,落盘后校验 magic bytes。

    Returns:
        True 仅当文件下载成功且通过 PNG magic 校验。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["curl", "-fsSL", "--max-time", str(timeout_sec), "-o", str(dest), url],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    if result.returncode != 0:
        # 半截文件不许留着冒充 PNG。
        dest.unlink(missing_ok=True)
        return False
    if not is_png(dest):
        dest.unlink(missing_ok=True)
        return False
    return True


def _write_output(stdout: str, output_path: str | os.PathLike[str] | None) -> None:
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stdout or "", encoding="utf-8")
    else:
        sys.stdout.write(stdout or "")


def cli(argv: Iterable[str] | None = None) -> int:
    """CLI entry used by default_worker.sh."""
    parser = argparse.ArgumentParser(
        prog="worker_llm",
        description="hermes invocation helper for default_worker.sh",
    )
    parser.add_argument("--db", default=None, help="SQLite DB path (default: data/news.db)")
    parser.add_argument("--timeout", type=int, default=600, help="subprocess timeout seconds")
    parser.add_argument("--skill", default=None, help="hermes skill name (-s)")
    parser.add_argument("--provider", default=None, help="provider override (manifest payload)")
    parser.add_argument("--model", default=None, help="model override (manifest payload)")
    parser.add_argument("--output", default=None, help="write stdout here instead of stdout")
    parser.add_argument(
        "--usage-file",
        default=None,
        help="hermes --usage-file path (JSON usage report)",
    )
    parser.add_argument("prompt", help="the prompt text")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        result = run_hermes(
            args.prompt,
            db_path=args.db,
            timeout_sec=args.timeout,
            skill=args.skill,
            provider_override=args.provider,
            model_override=args.model,
            usage_file=args.usage_file,
        )
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 — CLI 永不 traceback 到 bash
        _write_output(f"worker_llm error: {exc!r}\n", args.output)
        return 1

    _write_output(result["stdout"], args.output)
    return int(result["exit_code"])


if __name__ == "__main__":
    try:
        raise SystemExit(cli())
    except KeyboardInterrupt:
        raise SystemExit(130)
