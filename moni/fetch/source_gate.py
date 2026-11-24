"""Fetch gate driven by the ``sources`` table switches.

``is_active = 0`` turns the data plane off, ``is_hidden = 1`` hides the
source from the management page.  Migration 052 documents both as
"not scheduled / not batched"; until now only the YAML file was consulted,
so a source the user had switched off in the UI still ran every morning.

``config/sources.yaml`` decides *when* a source runs, this module decides
*whether* it may run at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..db.connection import get_db

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
    except Exception:
        # A broken DB must never silently stop the whole sweep — fail open.
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
