"""Token usage logger — writes JSON lines to data/llm_usage.log."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TokenLogger:
    """Appends a JSON entry to data/llm_usage.log on each LLM call."""

    def __init__(self, log_path: str | Path | None = None) -> None:
        if log_path is None:
            root = Path(__file__).parent.parent.parent  # moni/ → project root
            data_dir = root / "data"
            data_dir.mkdir(exist_ok=True)
            log_path = data_dir / "llm_usage.log"
        self._path = Path(log_path)

    @property
    def path(self) -> Path:
        return self._path

    def log(self, *, model: str, prompt_tokens: int, completion_tokens: int, **extra: Any) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            **extra,
        }
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
