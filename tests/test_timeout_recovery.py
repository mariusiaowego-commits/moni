"""超时后回收产物：只认本次运行之后落盘、且内容校验通过的文件。"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from moni.share.timeout_recovery import (
    PNG_MAGIC,
    find_recent_artifact,
    is_valid_html,
    is_valid_png,
    png_paths_from_text,
)


def _write_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        PNG_MAGIC + b"\x00\x00\x00\rIHDR" + b"\x00" * 2000 + b"IEND\xaeB`\x82"
    )
    _age(path, 5)
    return path


def _write_html(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<!DOCTYPE html><html><body>" + b"x" * 3000 + b"</body></html>")
    _age(path, 5)
    return path


def _age(path: Path, seconds: float) -> None:
    """mtime 退回几秒，避开「刚写完 1 秒内不回收」的稳定性保护。"""
    ts = time.time() - seconds
    os.utime(path, (ts, ts))


def _search(fmt: str, not_before: float, tmp_path: Path) -> Path | None:
    return find_recent_artifact(
        fmt,
        not_before,
        repo_root=tmp_path,
        artifacts_dir=tmp_path / "data" / "artifacts",
        profile_dir=tmp_path / "profile",
    )


def test_find_png_written_during_run(tmp_path):
    png = _write_png(tmp_path / "infographic" / "daily" / "infographic.png")
    started = time.time() - 60
    assert _search("info_graphic_png", started, tmp_path) == png.resolve()


@pytest.mark.parametrize("subdir", ["cache/artifacts", "cache/documents"])
def test_find_png_in_hermes_profile_cache(subdir, tmp_path):
    """kami 技能把成品写进 profile cache，不在 repo 内也要能回收。"""
    png = _write_png(tmp_path / "profile" / subdir / "report.png")
    started = time.time() - 60
    assert _search("report_png", started, tmp_path) == png.resolve()


def test_scratch_preview_never_beats_real_artifact(tmp_path):
    """cache/scratch 里是手机预览，mtime 更新也不能顶替主搜索根的成品。"""
    final = _write_png(tmp_path / "profile" / "reports" / "final.png")
    preview = _write_png(tmp_path / "profile" / "cache" / "scratch" / "phone.png")
    os.utime(preview, (time.time() - 2, time.time() - 2))
    assert _search("report_png", time.time() - 60, tmp_path) == final.resolve()


def test_scratch_used_only_when_nothing_else(tmp_path):
    preview = _write_png(tmp_path / "profile" / "cache" / "scratch" / "phone.png")
    assert _search("report_png", time.time() - 60, tmp_path) == preview.resolve()


def test_extra_dir_is_searched(tmp_path):
    png = _write_png(tmp_path / "moni-output" / "moni-report.png")
    assert find_recent_artifact(
        "report_png",
        time.time() - 60,
        repo_root=tmp_path,
        artifacts_dir=tmp_path / "data" / "artifacts",
        profile_dir=tmp_path / "profile",
        extra_dirs=(tmp_path / "moni-output",),
    ) == png.resolve()


def test_png_paths_from_text_prefers_reported_path(tmp_path):
    png = _write_png(tmp_path / "reports" / "行业跟踪.png")
    text = f"输出文件：\n`{png}`\n参考 https://fal.media/files/abc/x.png\n"
    assert png_paths_from_text(text, not_before=time.time() - 60) == [png.resolve()]


def test_png_paths_from_text_skips_stale_and_excluded(tmp_path):
    stale = _write_png(tmp_path / "reports" / "old.png")
    old = time.time() - 3600
    os.utime(stale, (old, old))
    own = _write_png(tmp_path / "artifacts" / "own.png")
    text = f"{stale}\n{own}\n"
    assert (
        png_paths_from_text(
            text,
            not_before=time.time() - 120,
            exclude_dir=tmp_path / "artifacts",
        )
        == []
    )


def test_png_paths_from_text_accepts_freshly_written_file(tmp_path):
    """成品可能就在回复前 <1s 落盘，显式路径分支不能套稳定窗。"""
    png = tmp_path / "reports" / "fresh.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(PNG_MAGIC + b"\x00\x00\x00\rIHDR" + b"\x00" * 2000 + b"IEND\xaeB`\x82")
    assert png_paths_from_text(str(png), not_before=time.time() - 60) == [png.resolve()]


def test_ignore_png_from_before_run(tmp_path):
    png = _write_png(tmp_path / "infographic" / "old.png")
    old = time.time() - 3600
    os.utime(png, (old, old))
    assert _search("info_graphic_png", time.time() - 120, tmp_path) is None


def test_reject_truncated_png(tmp_path):
    png = tmp_path / "infographic" / "partial.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(PNG_MAGIC + b"\x00\x00\x00\rIHDR" + b"\x00" * 2000)
    assert is_valid_png(png) is False
    assert _search("info_graphic_png", time.time() - 60, tmp_path) is None


def test_reject_png_without_ihdr(tmp_path):
    png = tmp_path / "infographic" / "headerless.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(PNG_MAGIC + b"\x00" * 2000 + b"IEND\xaeB`\x82")
    assert is_valid_png(png) is False


def test_png_format_does_not_pick_html(tmp_path):
    _write_html(tmp_path / "infographic" / "x.html")
    assert _search("info_graphic_png", time.time() - 60, tmp_path) is None


def test_find_html_for_html_landing(tmp_path):
    html = _write_html(tmp_path / "profile" / "reports" / "report.html")
    found = _search("html_landing", time.time() - 60, tmp_path)
    assert found == html.resolve()


def test_reject_tiny_html(tmp_path):
    small = tmp_path / "profile" / "reports" / "tiny.html"
    small.parent.mkdir(parents=True, exist_ok=True)
    small.write_text("<html>hi</html>", encoding="utf-8")
    assert is_valid_html(small) is False
    assert _search("html_landing", time.time() - 60, tmp_path) is None


def test_pick_newest_candidate(tmp_path):
    older = _write_png(tmp_path / "infographic" / "older.png")
    newer = _write_png(tmp_path / "infographic" / "newer.png")
    base = time.time() - 120
    os.utime(older, (base, base))
    os.utime(newer, (base + 60, base + 60))
    assert _search("report_png", base - 1, tmp_path) == newer.resolve()


def test_markdown_has_no_recovery(tmp_path):
    _write_html(tmp_path / "data" / "artifacts" / "x.html")
    assert _search("markdown", time.time() - 60, tmp_path) is None


def test_skip_other_tasks_canonical_artifact(tmp_path):
    """artifacts/ 里别的任务的成品不能当成本次回收对象。"""
    other = _write_png(tmp_path / "data" / "artifacts" / "ji_other-report_png.png")
    fresh = _write_png(tmp_path / "infographic" / "mine.png")
    assert _search("report_png", time.time() - 60, tmp_path) == fresh.resolve()
    assert other.exists()


@pytest.mark.parametrize("fmt", ["info_graphic_png", "report_png", "html_landing", "markdown"])
def test_empty_dirs_are_safe(fmt, tmp_path):
    assert _search(fmt, time.time() - 60, tmp_path) is None
