"""
Ensure all done share_tasks have their artifact in data/artifacts/.

v1.0.5.15: pre-v1.0.5.15 share_tasks rows have output_path pointing to
arbitrary locations (repo root, sandbox home, $HOME/.moni/output).  The
new /artifacts/<file> static mount needs files under data/artifacts/.
This module runs on web server startup, scans the DB, and copies any
done task's output_path to data/artifacts/<task_id>-<format>.<ext>.

Failure modes:
- Source file missing (BSD-grep-truncated, sandbox-fenced): skip + log.
- Destination already exists (idempotent re-runs): skip.
- Path traversal: enforced by the deduped filename, not user input.

This is fire-and-forget.  Failures are logged, never raised.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "data" / "artifacts"


def _ext_for_format(fmt: str) -> str:
    """Map share format → file extension.  Unknown formats → ``.out``."""
    return {
        "html_landing": "html",
        "markdown": "md",
        "plain_text": "txt",
        "pdf_bound": "pdf",
        "ppt_slides": "pptx",
        "ascii_art": "txt",
        "ascii_video": "txt",
        "architecture_svg": "svg",
        "info_graphic_png": "png",
        "knowledge_comic": "png",
    }.get(fmt, "out")


def ensure_artifact(task_id: str, format_name: str, output_path: str) -> str | None:
    """Copy ``output_path`` into artifacts dir if not already there.

    Returns the artifact filename (e.g. ``abc123-info_graphic_png.png``)
    if a file is present at the destination, else None.
    """
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    ext = _ext_for_format(format_name)
    dest_name = f"{task_id}-{format_name}.{ext}"
    dest = ARTIFACTS_DIR / dest_name

    if dest.exists():
        return dest_name  # idempotent re-run

    src = Path(output_path)
    if not src.is_file():
        log.warning(
            "ensure_artifact: source missing for task %s (%s): %s",
            task_id, format_name, output_path,
        )
        return None

    try:
        shutil.copy2(src, dest)
        log.info("ensure_artifact: copied %s → %s", src, dest)
        return dest_name
    except OSError as exc:
        log.error("ensure_artifact: copy failed for %s: %s", task_id, exc)
        return None

