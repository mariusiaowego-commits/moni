"""Scheduler — decides which sources are due to be fetched.

Reads ``config/sources.yaml`` and tracks last-run timestamps in
``data/.last_run.json``.

Usage::

    scheduler = Scheduler()
    for cfg in scheduler.sources:
        if scheduler.should_fetch(cfg["source_name"]):
            ...
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Path relative to the moni package root (two levels up from this file)
_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "sources.yaml"
_LAST_RUN_PATH = Path(__file__).parent.parent.parent / "data" / ".last_run.json"


class Scheduler:
    """Reads source configs and tracks last-run timestamps."""

    def __init__(
        self,
        config_path: Path | str | None = None,
        last_run_path: Path | str | None = None,
    ) -> None:
        self.config_path = Path(config_path) if config_path else _CONFIG_PATH
        self.last_run_path = Path(last_run_path) if last_run_path else _LAST_RUN_PATH
        self._sources: list[dict] | None = None
        self._last_run: dict[str, str] | None = None

    # ── sources ──────────────────────────────────────────────────────────────

    @property
    def sources(self) -> list[dict]:
        """All source entries from sources.yaml, in file order."""
        if self._sources is None:
            self._load_config()
        return self._sources  # type: ignore[return-value]

    def _load_config(self) -> None:
        if not self.config_path.exists():
            logger.warning("sources.yaml not found at %s", self.config_path)
            self._sources = []
            return

        with self.config_path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

        self._sources = data.get("sources", []) if isinstance(data, dict) else []
        logger.debug("Loaded %d source configs from %s", len(self._sources), self.config_path)

    # ── last-run timestamps ─────────────────────────────────────────────────

    @property
    def last_run(self) -> dict[str, str]:
        """Map of source_name → ISO8601 last-run timestamp."""
        if self._last_run is None:
            self._load_last_run()
        return self._last_run  # type: ignore[return-value]

    def _load_last_run(self) -> None:
        if self.last_run_path.exists():
            try:
                with self.last_run_path.open(encoding="utf-8") as fh:
                    self._last_run = json.load(fh)
                logger.debug("Loaded last_run from %s", self.last_run_path)
            except Exception as exc:
                logger.warning("Failed to load %s: %s — starting fresh", self.last_run_path, exc)
                self._last_run = {}
        else:
            self._last_run = {}
            logger.debug("No last_run file at %s, starting fresh", self.last_run_path)

    def _save_last_run(self) -> None:
        self.last_run_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.last_run_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self.last_run, fh, ensure_ascii=False, indent=2)
        tmp.replace(self.last_run_path)
        logger.debug("Wrote last_run to %s", self.last_run_path)

    # ── scheduling ───────────────────────────────────────────────────────────

    def should_fetch(self, source_name: str) -> bool:
        """Return True if enough time has elapsed since the last fetch.

        A source is due if it has never been run (``source_name`` not in
        ``last_run``) or if ``fetch_interval_min`` minutes have passed since
        the recorded timestamp.
        """
        cfg = self._source_config(source_name)
        if cfg is None:
            logger.warning("Unknown source %r — skipping", source_name)
            return False

        if not cfg.get("enabled", True):
            logger.debug("Source %r is disabled — skipping", source_name)
            return False

        last = self.last_run.get(source_name)
        if last is None:
            # Never run — always fetch
            return True

        last_dt = datetime.fromisoformat(last).replace(tzinfo=timezone.utc)
        elapsed_min = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60.0
        interval = cfg.get("fetch_interval_min", 30)

        due = elapsed_min >= interval
        logger.debug(
            "[%s] last_run=%s, elapsed=%.1f min, interval=%d min, due=%s",
            source_name, last, elapsed_min, interval, due,
        )
        return due

    def mark_fetched(self, source_name: str) -> None:
        """Record that ``source_name`` was just fetched (now)."""
        self.last_run[source_name] = datetime.now(timezone.utc).isoformat()
        self._save_last_run()

    # ── helpers ─────────────────────────────────────────────────────────────

    def _source_config(self, source_name: str) -> dict | None:
        for cfg in self.sources:
            if cfg.get("source_name") == source_name:
                return cfg
        return None

    def enabled_sources(self) -> list[dict]:
        """Yield all enabled source configs in file order."""
        for cfg in self.sources:
            if cfg.get("enabled", True):
                yield cfg
