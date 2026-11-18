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
import struct
import sys
import time
from pathlib import Path

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MIN_HTML_BYTES = 2048
#: PNG 成品下限（宽、高都要过）。低于这个的是裁切条、放大检查图、手机预览，
#: agent 在同一次运行里会写一堆，mtime 往往比成品新。
MIN_PNG_SIDE = 800
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


def png_dimensions(path: Path) -> tuple[int, int] | None:
    """读 IHDR 里的宽高；不是 PNG 或读不到返回 None。"""
    try:
        with path.open("rb") as fh:
            head = fh.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != PNG_MAGIC or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


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
    extra_dirs: tuple[Path, ...] = (),
) -> tuple[list[Path], list[Path]]:
    """返回 (主搜索根, 兜底搜索根)。

    ``cache/scratch`` 放兜底：agent 在里面写手机预览/中间截图，mtime 往往
    比成品新，混在主搜索里会抢走成品（2026-11-17 实测 phone.png 390x1317
    被当成报告图）。只有主搜索根一个候选都没有时才看它。
    """
    scratch = [profile_dir / "cache" / "scratch"]
    if format_name in ("info_graphic_png", "report_png"):
        main = [
            repo_root / "infographic",
            repo_root / "data" / "briefings",
            artifacts_dir,
            profile_dir / "reports",
            # 2026-11-17: agent 用 kami 技能时把成品写进自己的 cache，
            # 不在 repo 内。不扫这两个目录会出现「图已出、任务判 failed」。
            profile_dir / "cache" / "artifacts",
            profile_dir / "cache" / "documents",
        ]
    else:
        main = [
            repo_root / "data" / "briefings",
            artifacts_dir,
            profile_dir / "reports",
            profile_dir / "cache" / "artifacts",
            profile_dir / "cache" / "documents",
        ]
    return [*main, *extra_dirs], scratch


def _scan_roots(
    roots: list[Path],
    suffixes: tuple[str, ...],
    validate,
    not_before: float,
    deadline: float,
    min_side: int | None = None,
) -> tuple[float, Path] | None:
    best: tuple[float, Path] | None = None
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                if not name.lower().endswith(suffixes):
                    continue
                if _CANONICAL_ARTIFACT_RE.search(name):
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
                if min_side is not None:
                    dims = png_dimensions(path)
                    if dims is None or min(dims) < min_side:
                        continue
                if best is None or mtime > best[0]:
                    best = (mtime, path)
    return best


#: agent 回复里常见 ``/abs/path/x.png``：不含空白与引号括号，或行首独占一行。
_PNG_PATH_RE = re.compile(r"(/(?:[^\s\"'()<>`，。；：])+\.png)")


def png_paths_from_text(
    text: str,
    *,
    not_before: float,
    now: float | None = None,
) -> list[Path]:
    """从 agent 回复里取出本次运行产出的 PNG 绝对路径，按 mtime 升序。

    比 mtime 猜测可靠：agent 通常会把成品路径直接写在回复里。

    这里不设「刚写完 1 秒内不认」的稳定窗：路径是 agent 自己报的，且
    ``is_valid_png`` 已校验完整，文件可能就在回复前 <1s 落盘。
    """
    deadline = now if now is not None else time.time()
    found: dict[str, tuple[float, Path]] = {}

    def consider(raw: str) -> None:
        candidate = raw.strip().strip("`'\"<>()[]{} ")
        if not candidate or candidate.startswith(("http://", "https://", "//")):
            return
        path = Path(candidate)
        if not path.is_absolute() or path.suffix.lower() != ".png":
            return
        resolved = path.resolve()
        if str(resolved) in found:
            return
        try:
            mtime = resolved.stat().st_mtime
        except OSError:
            return
        if mtime < not_before or mtime > deadline or not is_valid_png(resolved):
            return
        found[str(resolved)] = (mtime, resolved)

    for line in text.splitlines():
        consider(line)
    for match in _PNG_PATH_RE.finditer(text):
        consider(match.group(1))
    return [path for _, path in sorted(found.values())]


def find_recent_artifact(
    format_name: str,
    not_before: float,
    *,
    repo_root: Path,
    artifacts_dir: Path,
    profile_dir: Path,
    extra_dirs: tuple[Path, ...] = (),
    now: float | None = None,
) -> Path | None:
    """返回 ``not_before`` 之后修改、且校验通过的最新文件。"""
    suffixes, validate = _validators(format_name)
    if not validate:
        return None
    deadline = (now if now is not None else time.time()) - 1.0
    main_roots, fallback_roots = _search_dirs(
        format_name, repo_root, artifacts_dir, profile_dir, extra_dirs
    )
    min_side = MIN_PNG_SIDE if suffixes == (".png",) else None
    best = _scan_roots(main_roots, suffixes, validate, not_before, deadline, min_side)
    if best is None:
        best = _scan_roots(fallback_roots, suffixes, validate, not_before, deadline, min_side)
    return best[1].resolve() if best else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="timeout_recovery")
    parser.add_argument("--format")
    parser.add_argument("--from-output")
    parser.add_argument("--not-before", type=float, required=True)
    parser.add_argument("--repo-root")
    parser.add_argument("--artifacts-dir")
    parser.add_argument("--profile-dir")
    parser.add_argument("--extra-dir", action="append", default=[])
    args = parser.parse_args(argv)

    if args.from_output:
        text = Path(args.from_output).read_text(encoding="utf-8", errors="replace")
        found_paths = png_paths_from_text(
            text,
            not_before=args.not_before,
        )
        sys.stdout.write("\n".join(str(p) for p in found_paths))
        return 0

    missing = [
        name
        for name in ("format", "repo_root", "artifacts_dir", "profile_dir")
        if not getattr(args, name)
    ]
    if missing:
        parser.error(f"missing required args: {', '.join('--' + m.replace('_', '-') for m in missing)}")

    found = find_recent_artifact(
        args.format,
        args.not_before,
        repo_root=Path(args.repo_root),
        artifacts_dir=Path(args.artifacts_dir),
        profile_dir=Path(args.profile_dir),
        extra_dirs=tuple(Path(p) for p in args.extra_dir),
    )
    if found:
        sys.stdout.write(str(found))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
