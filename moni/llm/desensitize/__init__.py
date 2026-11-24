"""Public facade for moni's outbound LLM desensitization shield (M0 core)."""

from __future__ import annotations

from .engine import MaskResult, mask_prompt, mask_text
from .policy import PolicyMode, TaskPolicy, resolve_policy
from .restorer import (
    StreamRestorer,
    restore_html_safe,
    restore_json_aware,
    restore_text,
)
from .rules import Rule, UserRules, builtin_rules
from .salt import load_salt, salt_path
from .vault import InMemoryVault, RequestScopeVault, RestoreStats

__all__ = [
    "InMemoryVault",
    "MaskResult",
    "PolicyMode",
    "RequestScopeVault",
    "RestoreStats",
    "Rule",
    "StreamRestorer",
    "TaskPolicy",
    "UserRules",
    "builtin_rules",
    "load_salt",
    "mask_prompt",
    "mask_text",
    "resolve_policy",
    "restore_html_safe",
    "restore_json_aware",
    "restore_text",
    "salt_path",
]
