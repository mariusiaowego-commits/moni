"""Fetch gate driven by the ``sources`` table switches.

``is_active = 0`` turns the data plane off; ``is_hidden = 1`` hides the
source from the management page.  Since v1.0.123 **both** keep the source
out of scheduled and batch runs — before this gate only ``config/sources.yaml``
was consulted, so a source the user had switched off in the UI still ran
every morning.

Migration 052 introduced ``is_hidden`` as a display-only switch ("照跑但不
显示").  The owner later decided hidden means "don't fetch either", so the
gate implements that newer contract here rather than by editing the applied
migration.  Manual single-source runs are deliberately *not* gated: 052
defines the switches as controlling 定时/批量同步.

``config/sources.yaml`` decides *when* a source runs, this module decides
*whether* it may run at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

from ..db.connection import get_db

logger = logging.getLogger(__name__)

REASON_INACTIVE = "inactive"
REASON_HIDDEN = "hidden"


@dataclass(frozen=True)
class SourceGate:
    """Whether one source may be fetched, and why not."""

    allowed: bool
    reason: str = ""


def load_source_gates(names: Iterable[str]) -> dict[str, SourceGate]:
    """Gate decisions for ``names``, keyed by source name.

    A source with no row in ``sources`` is unmanaged and stays fetchable.
    """
    wanted = list(dict.fromkeys(names))
    if not wanted:
        return {}
    gates = {name: SourceGate(True) for name in wanted}
    try:
        conn = get_db()
        placeholders = ",".join("?" * len(wanted))
        rows = conn.execute(
            f"SELECT name, is_active, is_hidden FROM sources WHERE name IN ({placeholders})",
            wanted,
        ).fetchall()
    except Exception as exc:
        # A broken DB must never silently stop the whole sweep — fail open.
        # But it must be loud: a silent fail-open lets disabled sources back
        # into the batch with no trace in the run record.
        logger.warning(
            "[source_gate] could not read the sources table (%s: %s) — "
            "allowing every source to run",
            type(exc).__name__, exc,
        )
        return gates
    for row in rows:
        if not row["is_active"]:
            gates[row["name"]] = SourceGate(False, REASON_INACTIVE)
        elif row["is_hidden"]:
            gates[row["name"]] = SourceGate(False, REASON_HIDDEN)
    return gates


def split_fetchable(
    cfgs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """Split source configs into ``(fetchable, [(name, reason)])``."""
    gates = load_source_gates(cfg["source_name"] for cfg in cfgs)
    fetchable: list[dict[str, Any]] = []
    skipped: list[tuple[str, str]] = []
    for cfg in cfgs:
        gate = gates.get(cfg["source_name"], SourceGate(True))
        if gate.allowed:
            fetchable.append(cfg)
        else:
            skipped.append((cfg["source_name"], gate.reason))
    return fetchable, skipped
