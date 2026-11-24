"""Masking engine: 4-detector pipeline, span resolution, and outbound leak check."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Mapping, NamedTuple

from .policy import PolicyMode, TaskPolicy, resolve_policy
from .rules import (
    BuiltinRule,
    CandidateSpan,
    UserRules,
    filter_id_and_number_false_positives,
    is_ascii_word_char,
    non_candidate_rule_groups,
    scan_candidate_spans,
)
from .vault import (
    PLACEHOLDER_RE,
    STANDIN_USER_RE,
    InMemoryVault,
    RequestScopeVault,
    make_path_user_standin,
)

logger = logging.getLogger(__name__)

_HIGH_ENTROPY_LEAK_KINDS: frozenset[str] = frozenset(
    {"API_KEY", "JWT", "BEARER", "PRIVATE_KEY", "SECRET", "ID_CARD", "BANK_CARD"}
)

_MINIMAL_SCRUB_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("API_KEY", re.compile(r"\bsk-(?:ant-|proj-|or-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}\b")),
    ("BEARER", re.compile(r"(?i)\bBearer[ \t]+([A-Za-z0-9._~+/=-]{8,})")),
    ("URL_TOKEN", re.compile(r"(?i)([?&](?:token|access_token|api_key|secret)=)([^&\s#]+)")),
)


class MaskItem(NamedTuple):
    """Zero-raw-value audit item describing one masked occurrence."""

    kind: str
    placeholder: str
    by: str
    head_hint: str
    length: int
    action: str = "mask"


@dataclass(slots=True)
class MaskResult:
    """Output of ``mask_text`` / ``mask_prompt`` (contains no raw secrets in items)."""

    text: str
    masked_total: int
    items: tuple[MaskItem, ...]
    vault: RequestScopeVault | InMemoryVault
    blocked: bool = False
    blocked_reason: str = ""
    masked_distinct: int = 0
    kinds: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    by: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    latency_us: int = 0
    leak_scan_hits: int = 0
    fallback_used: str | None = None
    prompt_chars_before: int = 0
    prompt_chars_after: int = 0


def _head_hint(kind: str, val: str) -> str:
    if len(val) < 2:
        return ""
    if kind in {"API_KEY", "JWT", "URL_TOKEN", "SECRET"} and len(val) >= 16:
        for i, ch in enumerate(val[:5]):
            if ch in "-_":
                return val[: i + 1]
    return val[:1]


def _has_any_marker(text: str, markers: tuple[str, ...]) -> bool:
    for m in markers:
        if m in text:
            return True
    return False


def _collect_windows(
    haystack: str,
    markers: tuple[str, ...],
    back: int,
    ahead: int,
    text_len: int,
    *,
    skip_query_prefix: bool = False,
) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    for mk in markers:
        pos = 0
        mk_len = len(mk)
        while True:
            idx = haystack.find(mk, pos)
            if idx < 0:
                break
            pos = idx + mk_len
            if skip_query_prefix and idx > 0 and haystack[idx - 1] in "?&":
                continue
            w0 = idx - back if idx > back else 0
            w1 = pos + ahead
            if w1 > text_len:
                w1 = text_len
            windows.append((w0, w1))
    if len(windows) <= 1:
        return windows
    windows.sort()
    merged: list[tuple[int, int]] = [windows[0]]
    for w0, w1 in windows[1:]:
        prev0, prev1 = merged[-1]
        if w0 <= prev1:
            if w1 > prev1:
                merged[-1] = (prev0, w1)
        else:
            merged.append((w0, w1))
    return merged


def _run_rule_matches(
    text: str,
    rule: BuiltinRule,
    matches: Iterable[re.Match[str]],
    spans: list[CandidateSpan],
    text_len: int,
) -> None:
    bound = rule.bound
    rule_ok = rule.ok
    rule_at = rule.at
    rule_group = rule.group
    rule_kind = rule.kind
    for m in matches:
        a, b = m.span(rule_group)
        if b <= a:
            continue
        if bound:
            if a > 0 and text[a - 1] in bound:
                continue
            if b < text_len and text[b] in bound:
                continue
        val = text[a:b]
        if rule_ok is not None and not rule_ok(val):
            continue
        if rule_at is not None and not rule_at(text, a, b):
            continue
        spans.append(
            CandidateSpan(
                start=a,
                end=b,
                kind=rule_kind,
                canonical=val,
                raw=val,
                by="rule",
                priority=3,
            )
        )


def _find_builtin_spans(text: str, policy: TaskPolicy) -> list[CandidateSpan]:
    allows_kind = policy.allows_kind
    spans = scan_candidate_spans(
        text,
        allow_url_token=allows_kind("URL_TOKEN"),
        allow_password=allows_kind("PASSWORD"),
        allow_email=allows_kind("EMAIL"),
        allow_path_user=allows_kind("PATH_USER"),
        allow_ip=allows_kind("PUBLIC_IP"),
        allow_id_card=allows_kind("ID_CARD"),
        allow_phone=allows_kind("PHONE"),
        allow_bank_card=allows_kind("BANK_CARD"),
    )

    direct_rules, direct_prescreen_re, window_or_fold_rules = non_candidate_rule_groups()
    text_len = len(text)

    if direct_prescreen_re.search(text) is not None:
        for rule in direct_rules:
            if rule.requires_any and not _has_any_marker(text, rule.requires_any):
                continue
            if not allows_kind(rule.kind):
                continue
            _run_rule_matches(text, rule, rule.pattern.finditer(text), spans, text_len)

    lower_text: str | None = None
    for rule in window_or_fold_rules:
        if rule.requires_any and not _has_any_marker(text, rule.requires_any):
            continue
        if rule.markers and not _has_any_marker(text, rule.markers):
            continue
        if not allows_kind(rule.kind):
            continue
        if rule.fold_markers:
            if lower_text is None:
                lower_text = text.lower()
            if not _has_any_marker(lower_text, rule.fold_markers):
                continue

        if rule.window_ahead > 0 and (rule.markers or rule.fold_markers):
            haystack = lower_text if rule.fold_markers else text
            markers = rule.fold_markers if rule.fold_markers else rule.markers
            assert haystack is not None
            windows = _collect_windows(
                haystack,
                markers,
                rule.window_back,
                rule.window_ahead,
                text_len,
                skip_query_prefix=rule.skip_query_prefix,
            )
            if not windows:
                continue
            matches: list[re.Match[str]] = []
            for w0, w1 in windows:
                matches.extend(rule.pattern.finditer(text, w0, w1))
            _run_rule_matches(text, rule, matches, spans, text_len)
        else:
            _run_rule_matches(text, rule, rule.pattern.finditer(text), spans, text_len)

    return spans


def _find_known_spans(
    text: str,
    vault: RequestScopeVault | InMemoryVault,
    policy: TaskPolicy,
    covered: set[tuple[int, int]] | None = None,
) -> list[CandidateSpan]:
    if not vault.has_known():
        return []
    known = vault.known_items()
    if not known:
        return []
    spans: list[CandidateSpan] = []
    for raw_val, (kind, _placeholder) in known.items():
        if not policy.allows_kind(kind):
            continue
        if raw_val not in text:
            continue
        is_ascii = raw_val.isascii()
        start = 0
        while True:
            idx = text.find(raw_val, start)
            if idx == -1:
                break
            end = idx + len(raw_val)
            start = end
            if covered is not None and (idx, end) in covered:
                continue
            if is_ascii:
                if idx > 0 and is_ascii_word_char(raw_val[0]) and is_ascii_word_char(text[idx - 1]):
                    continue
                if end < len(text) and is_ascii_word_char(raw_val[-1]) and is_ascii_word_char(text[end]):
                    continue
            spans.append(
                CandidateSpan(
                    start=idx,
                    end=end,
                    kind=kind,
                    canonical=raw_val,
                    raw=raw_val,
                    by="known",
                    priority=4,
                )
            )
    return spans


def _filter_protected_spans(text: str, spans: list[CandidateSpan]) -> list[CandidateSpan]:
    if not spans:
        return spans
    protected: list[tuple[int, int]] = []
    if "{{" in text:
        protected.extend(m.span() for m in PLACEHOLDER_RE.finditer(text))
    if "u_" in text:
        protected.extend(m.span() for m in STANDIN_USER_RE.finditer(text))
    if not protected:
        return spans
    return [
        sp
        for sp in spans
        if not any(sp.start < p_end and sp.end > p_start for p_start, p_end in protected)
    ]


def _minimal_scrub(text: str, vault: RequestScopeVault | InMemoryVault) -> tuple[str, int]:
    total = 0
    out = text
    for kind, pat in _MINIMAL_SCRUB_PATTERNS:
        def _rep(m: re.Match[str], _kind: str = kind) -> str:
            nonlocal total
            total += 1
            if m.lastindex and m.lastindex >= 2:
                val = m.group(2)
                return m.group(1) + vault.put(_kind, val, raw_value=val)
            val = m.group(1) if m.lastindex else m.group(0)
            ph = vault.put(_kind, val, raw_value=val)
            return f"Bearer {ph}" if _kind == "BEARER" else ph

        out = pat.sub(_rep, out)
    return out, total


def _apply_spans(
    text: str,
    spans: list[CandidateSpan],
    vault: RequestScopeVault | InMemoryVault,
    policy: TaskPolicy,
    *,
    kind_hint: str | None = None,
) -> tuple[str, list[MaskItem], list[tuple[str, str]], bool, int, int, dict[str, int], dict[str, int], int]:
    spans.sort(key=lambda sp: (sp.start, -sp.end, sp.priority))

    out_parts: list[str] = []
    items: list[MaskItem] = []
    masked_pairs: list[tuple[str, str]] = []
    blocked = False
    cursor = 0
    last = 0
    ph_cache: dict[tuple[str, str], tuple[str, str]] = {}
    pseudo_mode = policy.use_chinese_pseudonym
    pseudo_kinds = {"ORG", "SELF_ORG", "PROJECT", "WATCHLIST", "SENSITIVE_VIEW"}

    policy_actions = policy.actions
    kinds_count: dict[str, int] = {}
    by_count: dict[str, int] = {}
    distinct_placeholders: set[str] = set()
    masked_total = 0
    construct_violations = 0

    for sp in spans:
        if sp.start < cursor:
            continue
        cursor = sp.end
        eff_kind = sp.kind if sp.kind != "CUSTOM" or not kind_hint else kind_hint
        eff_action = policy_actions.get(eff_kind, sp.action) if policy_actions else sp.action
        if eff_action not in {"mask", "warn", "block"}:
            eff_action = "mask"

        if eff_action == "warn":
            ph = vault.put(eff_kind, sp.canonical, raw_value=sp.raw)
            items.append(
                MaskItem(
                    kind=eff_kind,
                    placeholder=ph,
                    by=sp.by,
                    head_hint=_head_hint(eff_kind, sp.raw),
                    length=len(sp.raw),
                    action="warn",
                )
            )
            kinds_count[eff_kind] = kinds_count.get(eff_kind, 0) + 1
            by_count[sp.by] = by_count.get(sp.by, 0) + 1
            continue

        if eff_action == "block":
            blocked = True

        if pseudo_mode and eff_kind in pseudo_kinds:
            replacement = policy.pseudonym_for(eff_kind, sp.pseudonym)
            hint = _head_hint(eff_kind, sp.raw)
            distinct_placeholders.add(replacement)
        else:
            cache_key = (eff_kind, sp.canonical)
            cached_pair = ph_cache.get(cache_key)
            if cached_pair is None:
                if eff_kind == "PATH_USER":
                    standin = make_path_user_standin(sp.canonical, vault.salt)
                    replacement = vault.put(
                        "PATH_USER", sp.canonical, raw_value=sp.raw, standin=standin
                    )
                    if not replacement.startswith("u_"):
                        construct_violations += 1
                else:
                    replacement = vault.put(eff_kind, sp.canonical, raw_value=sp.raw)
                    if not (replacement.startswith("{{") and replacement.endswith("}}")):
                        construct_violations += 1
                hint = _head_hint(eff_kind, sp.raw)
                ph_cache[cache_key] = (replacement, hint)
                distinct_placeholders.add(replacement)
            else:
                replacement, hint = cached_pair

        out_parts.append(text[last : sp.start])
        out_parts.append(replacement)
        last = sp.end
        masked_pairs.append((eff_kind, sp.raw))
        masked_total += 1
        kinds_count[eff_kind] = kinds_count.get(eff_kind, 0) + 1
        by_count[sp.by] = by_count.get(sp.by, 0) + 1
        items.append(
            MaskItem(
                kind=eff_kind,
                placeholder=replacement,
                by=sp.by,
                head_hint=hint,
                length=len(sp.raw),
                action=eff_action,
            )
        )

    out_parts.append(text[last:])
    return (
        "".join(out_parts),
        items,
        masked_pairs,
        blocked,
        masked_total,
        len(distinct_placeholders),
        kinds_count,
        by_count,
        construct_violations,
    )


def _mask_text_impl(
    text: str,
    *,
    kind_hint: str | None = None,
    vault: RequestScopeVault | InMemoryVault | None = None,
    policy: TaskPolicy | str | None = None,
    user_rules: UserRules | None = None,
) -> tuple[MaskResult, list[tuple[str, str]], int]:
    start_ns = time.perf_counter_ns()
    active_vault: RequestScopeVault | InMemoryVault = (
        vault if vault is not None else RequestScopeVault()
    )
    eff_policy = resolve_policy(policy) if isinstance(policy, str) or policy is None else policy

    if not text or eff_policy.mode == PolicyMode.BYPASS:
        return (
            MaskResult(
                text=text or "",
                masked_total=0,
                items=(),
                vault=active_vault,
                latency_us=(time.perf_counter_ns() - start_ns) // 1000,
            ),
            [],
            0,
        )

    try:
        spans: list[CandidateSpan] = []
        if user_rules is not None:
            for sp in user_rules.find_dict_spans(text):
                if eff_policy.allows_kind(sp.kind):
                    spans.append(sp)
            for sp in user_rules.find_custom_spans(text):
                if eff_policy.allows_kind(sp.kind):
                    spans.append(sp)

        spans.extend(_find_builtin_spans(text, eff_policy))
        if active_vault.has_known():
            covered = {(sp.start, sp.end) for sp in spans} if spans else None
            spans.extend(_find_known_spans(text, active_vault, eff_policy, covered=covered))
        spans = _filter_protected_spans(text, spans)
        spans = filter_id_and_number_false_positives(text, spans)

        if user_rules is not None and user_rules.allowlist:
            allow = user_rules.allowlist
            spans = [sp for sp in spans if sp.by == "dict" or sp.raw.lower() not in allow]

        if not spans:
            return (
                MaskResult(
                    text=text,
                    masked_total=0,
                    items=(),
                    vault=active_vault,
                    latency_us=(time.perf_counter_ns() - start_ns) // 1000,
                ),
                [],
                0,
            )

        (
            masked_out,
            items,
            masked_pairs,
            blocked,
            masked_total,
            masked_distinct,
            kinds_count,
            by_count,
            construct_violations,
        ) = _apply_spans(text, spans, active_vault, eff_policy, kind_hint=kind_hint)
    except Exception:
        if eff_policy.strict_fail_closed:
            raise
        logger.exception("desensitize: engine failure, falling back to minimal scrub")
        scrubbed, scrub_count = _minimal_scrub(text, active_vault)
        return (
            MaskResult(
                text=scrubbed,
                masked_total=scrub_count,
                items=(),
                vault=active_vault,
                fallback_used="mask_error_scrub_pass",
                latency_us=(time.perf_counter_ns() - start_ns) // 1000,
            ),
            [],
            0,
        )

    return (
        MaskResult(
            text=masked_out,
            masked_total=masked_total,
            items=tuple(items),
            vault=active_vault,
            blocked=blocked,
            blocked_reason="policy_block" if blocked else "",
            masked_distinct=masked_distinct,
            kinds=MappingProxyType(kinds_count),
            by=MappingProxyType(by_count),
            latency_us=(time.perf_counter_ns() - start_ns) // 1000,
        ),
        masked_pairs,
        construct_violations,
    )


def _scan_outbound_leaks(
    outbound_text: str,
    *,
    masked_pairs: Iterable[tuple[str, str]],
    construct_violations: int = 0,
    leak_terms: Iterable[str] = (),
) -> int:
    """Enforce construct assertion + high-entropy substring & leak_terms scan."""
    leak_hits = construct_violations
    for term in leak_terms:
        if term and term in outbound_text:
            leak_hits += 1

    # High-entropy/checksum fallback for credential/ID kinds that cannot safely embed in words.
    for kind, raw_val in masked_pairs:
        if kind in _HIGH_ENTROPY_LEAK_KINDS and len(raw_val) >= 8 and raw_val in outbound_text:
            leak_hits += 1

    return leak_hits


def mask_text(
    text: str,
    *,
    kind_hint: str | None = None,
    vault: RequestScopeVault | InMemoryVault | None = None,
    policy: TaskPolicy | str | None = None,
    user_rules: UserRules | None = None,
) -> MaskResult:
    """Detect and replace sensitive values in ``text`` using deterministic placeholders."""
    result, _pairs, _violations = _mask_text_impl(
        text,
        kind_hint=kind_hint,
        vault=vault,
        policy=policy,
        user_rules=user_rules,
    )
    return result


def mask_prompt(
    prompt: str,
    *,
    task_type: str = "standard",
    vault: RequestScopeVault | InMemoryVault | None = None,
    policy: TaskPolicy | None = None,
    user_rules: UserRules | None = None,
    leak_terms: Iterable[str] = (),
    inject_note: bool = False,
) -> MaskResult:
    """Mask an outbound LLM prompt and enforce the outbound leak-scan assertion."""
    eff_policy = policy if policy is not None else resolve_policy(task_type)
    result, masked_pairs, construct_violations = _mask_text_impl(
        prompt, vault=vault, policy=eff_policy, user_rules=user_rules
    )
    result.prompt_chars_before = len(prompt)

    leak_hits = 0
    if not result.blocked and eff_policy.mode != PolicyMode.BYPASS:
        leak_hits = _scan_outbound_leaks(
            result.text,
            masked_pairs=masked_pairs,
            construct_violations=construct_violations,
            leak_terms=leak_terms,
        )

    if leak_hits > 0:
        result.leak_scan_hits = leak_hits
        result.blocked = True
        result.blocked_reason = "leak_scan_hit"
        result.fallback_used = "leak_scan_hit"
        result.prompt_chars_after = len(result.text)
        return result

    if inject_note and result.masked_total > 0:
        # Example code deliberately uses '1' (invalid RFC 4648 base32) so it is never restored.
        note = "[系统脱敏说明：文本中的 {{ORG_11111111}} 等编码为脱敏占位符，请原样保留并在回答中直接引用。]\n\n"
        result.text = note + result.text

    result.prompt_chars_after = len(result.text)
    return result
