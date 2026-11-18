"""moni.share.timeout_recovery — 超时后回收已产出文件。

PNG/HTML 分享任务由 hermes agent 直接写文件。实测 agent 会在收尾自检
阶段（vision_analyze / 像素检查）耗时超过 worker 上限，文件其实已经
落盘（2026-11-12 任务 PdnKZBj_ylX6XIjqy6Zc9A：13:31 写出 1080x1920
PNG，13:32:40 判 timeout）。这里按「本次运行开始之后新出现 + 内容
校验通过」的规则找回产物，避免超时直接把成品丢掉。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MIN_HTML_BYTES = 2048
_SKIP_DIRS = {".git", "node_modules", "__pycache__"}
# artifacts 目录里 moni 自己的标准产物名（其他任务的成品），不能当成本次
# agent 新写的文件回收。
_CANONICAL_ARTIFACT_RE = re.compile(
    r"-(?:html_landing|info_graphic_png|report_png)(?:-\d+)?\.(?:png|html)$"
)


def is_valid_png(path: Path) -> bool:
    """签名 + IHDR 头 + IEND 尾。只校验 magic 会把半截文件当成成品。"""
    try:
        with path.open("rb") as fh:
            head = fh.read(16)
            if head[:8] != PNG_MAGIC or head[12:16] != b"IHDR":
                return False
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size < 1024:
                return False
            fh.seek(max(0, size - 64))
            return b"IEND" in fh.read()
    except OSError:
        return False


def is_valid_html(path: Path) -> bool:
    try:
        if path.stat().st_size < MIN_HTML_BYTES:
            return False
        head = path.read_bytes()[:4096].lower()
    except OSError:
        return False
    return b"<html" in head or b"<!doctype html" in head


def _validators(format_name: str):
    if format_name in ("info_graphic_png", "report_png"):
        return (".png",), is_valid_png
    if format_name == "html_landing":
        return (".html", ".htm"), is_valid_html
    return (), None


def _search_dirs(
    format_name: str,
    repo_root: Path,
    artifacts_dir: Path,
    profile_dir: Path,
) -> list[Path]:
    if format_name in ("info_graphic_png", "report_png"):
        return [
            repo_root / "infographic",
            repo_root / "data" / "briefings",
            artifacts_dir,
            profile_dir / "reports",
            profile_dir / "cache" / "scratch",
            # 2026-11-17: agent 用 kami 技能时把成品写进自己的 cache，
            # 不在 repo 内。不扫这两个目录会出现「图已出、任务判 failed」。
            profile_dir / "cache" / "artifacts",
            profile_dir / "cache" / "documents",
        ]
    return [
        repo_root / "data" / "briefings",
        artifacts_dir,
        profile_dir / "reports",
        profile_dir / "cache" / "scratch",
        profile_dir / "cache" / "artifacts",
        profile_dir / "cache" / "documents",
    ]


def find_recent_artifact(
    format_name: str,
    not_before: float,
    *,
    repo_root: Path,
    artifacts_dir: Path,
    profile_dir: Path,
    now: float | None = None,
) -> Path | None:
    """返回 ``not_before`` 之后修改、且校验通过的最新文件。"""
    suffixes, validate = _validators(format_name)
    if not validate:
        return None
    deadline = (now if now is not None else time.time()) - 1.0
    best: tuple[float, Path] | None = None
    for root in _search_dirs(format_name, repo_root, artifacts_dir, profile_dir):
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                if not name.lower().endswith(suffixes):
                    continue
                if root == artifacts_dir and _CANONICAL_ARTIFACT_RE.search(name):
                    continue
                path = Path(dirpath) / name
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if mtime < not_before or mtime > deadline:
                    continue
                if not validate(path):
                    continue
                if best is None or mtime > best[0]:
                    best = (mtime, path)
    return best[1].resolve() if best else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="timeout_recovery")
    parser.add_argument("--format", required=True)
    parser.add_argument("--not-before", type=float, required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--artifacts-dir", required=True)
    parser.add_argument("--profile-dir", required=True)
    args = parser.parse_args(argv)

    found = find_recent_artifact(
        args.format,
        args.not_before,
        repo_root=Path(args.repo_root),
        artifacts_dir=Path(args.artifacts_dir),
        profile_dir=Path(args.profile_dir),
    )
    if found:
        sys.stdout.write(str(found))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
