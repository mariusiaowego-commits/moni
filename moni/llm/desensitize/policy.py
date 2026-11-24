"""Task-aware desensitization policy resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping

L1_KINDS: frozenset[str] = frozenset(
    {
        "PRIVATE_KEY",
        "API_KEY",
        "URL_TOKEN",
        "JWT",
        "BEARER",
        "PASSWORD",
        "SECRET",
    }
)

L2_BUILTIN_KINDS: frozenset[str] = frozenset(
    {
        "PHONE",
        "ID_CARD",
        "BANK_CARD",
        "EMAIL",
    }
)

L2_DOMAIN_KINDS: frozenset[str] = frozenset(
    {
        "ORG",
        "SELF_ORG",
        "PROJECT",
        "WATCHLIST",
        "SENSITIVE_VIEW",
    }
)

L3_DEFAULT_ON_KINDS: frozenset[str] = frozenset({"PATH_USER"})
L3_OPTIONAL_KINDS: frozenset[str] = frozenset({"INTERNAL_HOST", "PUBLIC_IP", "PUBLISHER"})

# Sensitive research/internal kinds still masked in web_search_safe; public ORG is kept.
WEB_SEARCH_KEEP_KINDS: frozenset[str] = frozenset({"ORG", "PUBLISHER"})

RASTER_PSEUDONYM_BY_KIND: Mapping[str, str] = MappingProxyType(
    {
        "ORG": "某金融机构",
        "SELF_ORG": "某股份制银行",
        "PROJECT": "某重点项目",
        "WATCHLIST": "某关注主体",
        "SENSITIVE_VIEW": "内部评估口径",
    }
)


class PolicyMode(str, Enum):
    """Execution mode derived from ``task_type``."""

    STANDARD = "standard"
    WEB_SEARCH_SAFE = "web_search_safe"
    RASTER_GENERALIZE = "raster_generalize"
    BYPASS = "bypass"


@dataclass(frozen=True, slots=True)
class TaskPolicy:
    """Resolved per-call masking policy."""

    task_type: str = "standard"
    mode: PolicyMode = PolicyMode.STANDARD
    level_l1: bool = True
    level_l2: bool = True
    level_l3: bool = False
    strict_fail_closed: bool = False
    disabled_kinds: frozenset[str] = frozenset()
    actions: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def use_chinese_pseudonym(self) -> bool:
        return self.mode == PolicyMode.RASTER_GENERALIZE

    def allows_kind(self, kind: str) -> bool:
        """Return True if ``kind`` should be masked/checked under this policy."""
        if self.mode == PolicyMode.BYPASS or kind in self.disabled_kinds:
            return False
        if self.mode == PolicyMode.WEB_SEARCH_SAFE and kind in WEB_SEARCH_KEEP_KINDS:
            return False
        if kind in L1_KINDS:
            return self.level_l1
        if kind in L2_BUILTIN_KINDS or kind in L2_DOMAIN_KINDS:
            return self.level_l2
        if kind in L3_DEFAULT_ON_KINDS:
            return self.level_l2 or self.level_l3
        if kind in L3_OPTIONAL_KINDS:
            return self.level_l3
        # Custom user kinds follow L1/L2 unless explicitly disabled.
        return self.level_l1 or self.level_l2

    def pseudonym_for(self, kind: str, custom_pseudonym: str | None = None) -> str:
        if custom_pseudonym:
            return custom_pseudonym
        return RASTER_PSEUDONYM_BY_KIND.get(kind, "某相关主体")


def resolve_policy(
    task_type: str = "standard",
    *,
    enabled: bool = True,
    level_l1: bool = True,
    level_l2: bool = True,
    level_l3: bool = False,
    strict_fail_closed: bool = False,
    disabled_kinds: Iterable[str] = (),
    actions: Mapping[str, str] | None = None,
) -> TaskPolicy:
    """Map a moni ``task_type`` to its TaskPolicy."""
    normalized = (task_type or "standard").strip().lower()
    if not enabled:
        mode = PolicyMode.BYPASS
    elif normalized == "fact_check_plus":
        mode = PolicyMode.WEB_SEARCH_SAFE
    elif normalized in {"info_graphic_png", "report_png"}:
        mode = PolicyMode.RASTER_GENERALIZE
    else:
        mode = PolicyMode.STANDARD

    return TaskPolicy(
        task_type=normalized,
        mode=mode,
        level_l1=level_l1,
        level_l2=level_l2,
        level_l3=level_l3,
        strict_fail_closed=strict_fail_closed,
        disabled_kinds=frozenset(k.strip().upper() for k in disabled_kinds if k),
        actions=MappingProxyType(dict(actions or {})),
    )
