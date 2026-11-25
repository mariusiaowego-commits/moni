"""Runtime settings cache, outbound masking, inbound restoration, and audit logging."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Sequence

from moni.db.connection import get_db, get_db_path

from . import engine as _engine
from .engine import MaskResult
from .policy import PolicyMode, resolve_policy
from .restorer import restore_json_aware, restore_text
from .rules import Rule, UserRules
from .vault import RequestScopeVault

logger = logging.getLogger(__name__)

_SETTINGS_TTL_SEC = 30.0
_MAX_AUDIT_ITEMS = 200
_HEAD_HINT_ALLOWED_KINDS = frozenset(
    {"API_KEY", "JWT", "URL_TOKEN", "BEARER", "SECRET", "PRIVATE_KEY"}
)
_SETTING_KEYS = (
    "desensitize.enabled",
    "desensitize.level_l1",
    "desensitize.level_l2",
    "desensitize.level_l3",
    "desensitize.strict_fail_closed",
)

_CACHE_LOCK = threading.Lock()
_SETTINGS_CACHE: dict[str, tuple[float, DesensSettings]] = {}


@dataclass(frozen=True, slots=True)
class DesensSettings:
    """Cached desensitization feature flags and active custom rules."""

    enabled: bool
    level_l1: bool
    level_l2: bool
    level_l3: bool
    strict_fail_closed: bool
    rules: UserRules | None
    #: Uncompiled rule rows — kept so callers can merge extra terms in and
    #: recompile (the desensitize lab's ad-hoc terms).
    rules_raw: tuple[Rule, ...] = ()


_DISABLED_SETTINGS = DesensSettings(
    enabled=False,
    level_l1=True,
    level_l2=True,
    level_l3=False,
    strict_fail_closed=False,
    rules=None,
)


@dataclass(frozen=True, slots=True)
class DesensScope:
    """Ambient per-call desensitization metadata passed through closures via ContextVar."""

    task_type: str | None = None
    article_id: str | None = None
    job_id: str | None = None
    share_task_id: str | None = None
    json_output: bool | None = None
    db_path: str | None = None


_ACTIVE_SCOPE: ContextVar[DesensScope] = ContextVar(
    "moni_desens_scope",
    default=DesensScope(),
)


@contextmanager
def desensitize_scope(
    *,
    task_type: str | None = None,
    article_id: str | None = None,
    job_id: str | None = None,
    share_task_id: str | None = None,
    json_output: bool | None = None,
    db_path: str | os.PathLike[str] | None = None,
) -> Iterator[DesensScope]:
    """Bind ambient desensitization metadata for nested LLM channel calls."""
    prev = _ACTIVE_SCOPE.get()
    nxt = DesensScope(
        task_type=task_type if task_type is not None else prev.task_type,
        article_id=article_id if article_id is not None else prev.article_id,
        job_id=job_id if job_id is not None else prev.job_id,
        share_task_id=share_task_id if share_task_id is not None else prev.share_task_id,
        json_output=json_output if json_output is not None else prev.json_output,
        db_path=str(db_path) if db_path is not None else prev.db_path,
    )
    tok = _ACTIVE_SCOPE.set(nxt)
    try:
        yield nxt
    finally:
        _ACTIVE_SCOPE.reset(tok)


def current_desensitize_scope() -> DesensScope:
    """Return the active ambient DesensScope."""
    return _ACTIVE_SCOPE.get()


@dataclass(slots=True)
class OutboundContext:
    """Per-call state spanning outbound masking, inbound restoration, and DB audit."""

    masked_text: str
    mask_result: MaskResult
    vault: RequestScopeVault
    call_site: str
    task_type: str
    article_id: str | None
    job_id: str | None
    share_task_id: str | None
    json_output: bool
    started_ns: int
    db_path: str | None = None
    strict_fail_closed: bool = False
    policy_mode: str = "standard"
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    event_recorded: bool = False
    fallback_override: str | None = None
    tokens_before: int = 0


def _reset_cache() -> None:
    """Clear the in-process settings cache (for unit tests)."""
    with _CACHE_LOCK:
        _SETTINGS_CACHE.clear()


def _cache_key(db_path: str | os.PathLike[str] | None) -> str:
    if db_path is not None:
        return str(db_path)
    try:
        return str(get_db_path())
    except Exception:
        return "__default__"


def _open_conn(db_path: str | os.PathLike[str] | None) -> tuple[sqlite3.Connection, bool]:
    if db_path is not None:
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        conn.row_factory = sqlite3.Row
        return conn, True
    return get_db(), False


def _rules_from_rows(rows: list[sqlite3.Row]) -> list[Rule]:
    compiled: list[Rule] = []
    for row in rows:
        rule_type = str(row["rule_type"] or "").strip()
        kind = str(row["kind"] or "ORG")
        pattern = str(row["pattern"] or "")
        pseudonym = row["pseudonym"]
        action = str(row["action"] or "mask")
        match_case = bool(row["match_case"])
        try:
            aliases_raw = json.loads(row["aliases_json"] or "[]")
            aliases = tuple(str(a) for a in aliases_raw if a) if isinstance(aliases_raw, list) else ()
        except (ValueError, TypeError):
            aliases = ()

        if rule_type == "allowlist":
            compiled.append(
                Rule(kind=kind, word=pattern, aliases=aliases, rule_type="allowlist")
            )
        elif rule_type == "regex":
            compiled.append(
                Rule(
                    kind=kind,
                    pattern=pattern,
                    rule_type="regex",
                    pseudonym=pseudonym,
                    action=action,
                    match_case=match_case,
                )
            )
        else:
            compiled.append(
                Rule(
                    kind=kind,
                    word=pattern,
                    aliases=aliases,
                    pseudonym=pseudonym,
                    action=action,
                    match_case=match_case,
                )
            )
    return compiled


def load_settings(db_path: str | os.PathLike[str] | None = None) -> DesensSettings:
    """Read ``app_settings`` and ``desensitize_rules`` with a 30s in-process cache."""
    key = _cache_key(db_path)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _SETTINGS_CACHE.get(key)
        if cached is not None and (now - cached[0]) < _SETTINGS_TTL_SEC:
            return cached[1]

    conn: sqlite3.Connection | None = None
    owns_conn = False
    try:
        conn, owns_conn = _open_conn(db_path)
        placeholders = ",".join("?" for _ in _SETTING_KEYS)
        rows = conn.execute(
            f"SELECT key, value FROM app_settings WHERE key IN ({placeholders})",
            _SETTING_KEYS,
        ).fetchall()
        cfg = {str(r["key"]): str(r["value"]).strip() for r in rows}
        enabled = cfg.get("desensitize.enabled", "0") == "1"
        level_l1 = cfg.get("desensitize.level_l1", "1") == "1"
        level_l2 = cfg.get("desensitize.level_l2", "1") == "1"
        level_l3 = cfg.get("desensitize.level_l3", "0") == "1"
        strict_fail_closed = cfg.get("desensitize.strict_fail_closed", "0") == "1"

        rule_rows = conn.execute(
            """
            SELECT rule_type, kind, pattern, aliases_json, pseudonym, action, match_case
            FROM desensitize_rules
            WHERE is_active = 1
            ORDER BY id ASC
            """
        ).fetchall()
        rules_raw = _rules_from_rows(rule_rows)
        user_rules = UserRules.from_rules(rules_raw) if rules_raw else None

        settings = DesensSettings(
            enabled=enabled,
            level_l1=level_l1,
            level_l2=level_l2,
            level_l3=level_l3,
            strict_fail_closed=strict_fail_closed,
            rules=user_rules,
            rules_raw=tuple(rules_raw),
        )
    except Exception as exc:  # noqa: BLE001 — fail-open when DB/tables unavailable
        logger.debug("desensitize load_settings failed (returning disabled): %s", exc)
        return _DISABLED_SETTINGS
    finally:
        if owns_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    with _CACHE_LOCK:
        _SETTINGS_CACHE[key] = (now, settings)
    return settings


def _estimate_tokens_local(text: str) -> int:
    if not text:
        return 0
    cn = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cn
    return cn * 2 // 3 + other // 4


def prepare_outbound(
    prompt: str,
    *,
    call_site: str,
    task_type: str = "standard",
    article_id: str | None = None,
    job_id: str | None = None,
    share_task_id: str | None = None,
    json_output: bool = False,
    inject_note: bool = True,
    db_path: str | os.PathLike[str] | None = None,
    force_level_l2: bool | None = None,
    force_level_l3: bool | None = None,
    force: bool = False,
    extra_terms: Sequence[str] = (),
) -> OutboundContext | None:
    """Mask an outbound prompt when ``desensitize.enabled`` is active; else return None.

    ``force=True`` masks regardless of the global switch — the desensitize lab
    uses it so the demo always shows the shielded side even while the
    production switch stays off.  Forced calls are also **fail-closed**: the lab
    must never put unmasked plaintext on the wire, so a masking failure raises
    instead of falling back to the original text.  ``extra_terms`` adds
    ad-hoc dictionary terms (kind ``ORG``) on top of the stored user rules
    without touching the DB.
    """
    if not prompt:
        return None
    settings = load_settings(db_path)
    if not settings.enabled and not force:
        return None

    started_ns = time.perf_counter_ns()
    vault = RequestScopeVault()
    eff_l2 = settings.level_l2 if force_level_l2 is None else force_level_l2
    eff_l3 = settings.level_l3 if force_level_l3 is None else force_level_l3
    eff_strict = settings.strict_fail_closed or force
    eff_db = str(db_path) if db_path is not None else None
    tokens_before = _estimate_tokens_local(prompt)
    user_rules = settings.rules
    ad_hoc = [Rule(kind="ORG", word=term.strip()) for term in extra_terms if term and term.strip()]
    if ad_hoc:
        user_rules = UserRules.from_rules([*settings.rules_raw, *ad_hoc])

    try:
        policy = resolve_policy(
            task_type,
            enabled=True,
            level_l1=settings.level_l1,
            level_l2=eff_l2,
            level_l3=eff_l3,
            strict_fail_closed=eff_strict,
        )
        eff_inject = inject_note and (policy.mode != PolicyMode.RASTER_GENERALIZE)
        mask_result = _engine.mask_prompt(
            prompt,
            task_type=task_type,
            vault=vault,
            policy=policy,
            user_rules=user_rules,
            inject_note=eff_inject,
        )
        return OutboundContext(
            masked_text=mask_result.text,
            mask_result=mask_result,
            vault=vault,
            call_site=call_site,
            task_type=task_type,
            article_id=article_id,
            job_id=job_id,
            share_task_id=share_task_id,
            json_output=json_output,
            started_ns=started_ns,
            db_path=eff_db,
            strict_fail_closed=eff_strict,
            policy_mode=policy.mode.value,
            tokens_before=tokens_before,
        )
    except Exception as exc:
        if eff_strict:
            raise
        logger.warning("desensitize prepare_outbound failed (fail-open): %s", exc)
        fallback_result = MaskResult(
            text=prompt,
            masked_total=0,
            items=(),
            vault=vault,
            blocked=False,
            fallback_used="mask_error",
            prompt_chars_before=len(prompt),
            prompt_chars_after=len(prompt),
        )
        return OutboundContext(
            masked_text=prompt,
            mask_result=fallback_result,
            vault=vault,
            call_site=call_site,
            task_type=task_type,
            article_id=article_id,
            job_id=job_id,
            share_task_id=share_task_id,
            json_output=json_output,
            started_ns=started_ns,
            db_path=eff_db,
            strict_fail_closed=False,
            fallback_override="mask_error",
            tokens_before=tokens_before,
        )


def finalize_inbound(ctx: OutboundContext, raw_reply: str) -> str:
    """Restore placeholders in inbound LLM output; fall back to raw_reply on error."""
    if not raw_reply:
        return raw_reply
    mode = "json" if ctx.json_output else "plain"
    try:
        restored = (
            restore_json_aware(raw_reply, ctx.vault)
            if ctx.json_output
            else restore_text(raw_reply, ctx.vault)
        )
        return ctx.vault.finalize_restore(restored, mode=mode)
    except Exception as exc:
        if ctx.strict_fail_closed:
            raise
        logger.warning("desensitize finalize_inbound failed (returning raw reply): %s", exc)
        ctx.fallback_override = ctx.fallback_override or "restore_error"
        return raw_reply


def _serialize_items(mask_result: MaskResult) -> tuple[str, int]:
    truncated = 1 if len(mask_result.items) > _MAX_AUDIT_ITEMS else 0
    payload = [
        {
            "kind": item.kind,
            "placeholder": item.placeholder,
            "by": item.by,
            "head_hint": item.head_hint if item.kind in _HEAD_HINT_ALLOWED_KINDS else "",
            "length": item.length,
            "action": item.action,
        }
        for item in mask_result.items[:_MAX_AUDIT_ITEMS]
    ]
    return json.dumps(payload, ensure_ascii=False), truncated


def record_event(
    ctx: OutboundContext,
    *,
    provider: str = "",
    model: str = "",
    blocked: bool = False,
) -> None:
    """Best-effort insert/update into ``desensitize_events``; never raises."""
    conn: sqlite3.Connection | None = None
    owns_conn = False
    try:
        mr = ctx.mask_result
        stats = ctx.vault.stats
        fallback_used = ctx.fallback_override if ctx.fallback_override is not None else mr.fallback_used

        conn, owns_conn = _open_conn(ctx.db_path)
        if not ctx.event_recorded:
            items_json, items_truncated = _serialize_items(mr)
            kinds_json = json.dumps(dict(mr.kinds), ensure_ascii=False)
            by_json = json.dumps(dict(mr.by), ensure_ascii=False)
            tokens_after = _estimate_tokens_local(ctx.masked_text)
            tokens_delta_est = tokens_after - ctx.tokens_before
            created_at = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            conn.execute(
                """
                INSERT INTO desensitize_events (
                    call_id, job_id, share_task_id, article_id, call_site, task_type,
                    provider, model, policy_mode, checked, blocked,
                    masked_total, masked_distinct, restored_total, fuzzy_restored_total,
                    unrestored_count, hallucinated_count, leak_scan_hits, residual_placeholders,
                    fallback_used, prompt_chars_before, prompt_chars_after, tokens_delta_est,
                    mask_latency_us, restore_latency_us, kinds_json, by_json, items_json,
                    items_truncated, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, 1, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?
                )
                """,
                (
                    ctx.call_id,
                    ctx.job_id,
                    ctx.share_task_id,
                    ctx.article_id,
                    ctx.call_site,
                    ctx.task_type or "standard",
                    provider or None,
                    model or None,
                    ctx.policy_mode or "standard",
                    1 if (blocked or mr.blocked) else 0,
                    int(mr.masked_total),
                    int(mr.masked_distinct),
                    int(stats.restored_total),
                    int(stats.fuzzy_restored_total),
                    int(stats.unrestored_count),
                    int(stats.hallucinated_count),
                    int(mr.leak_scan_hits),
                    int(stats.residual_placeholders),
                    fallback_used,
                    int(mr.prompt_chars_before),
                    int(mr.prompt_chars_after),
                    int(tokens_delta_est),
                    int(mr.latency_us),
                    int(stats.restore_latency_us),
                    kinds_json,
                    by_json,
                    items_json,
                    int(items_truncated),
                    created_at,
                ),
            )
            conn.commit()
            ctx.event_recorded = True
        else:
            conn.execute(
                """
                UPDATE desensitize_events
                SET restored_total = ?,
                    fuzzy_restored_total = ?,
                    unrestored_count = ?,
                    hallucinated_count = ?,
                    residual_placeholders = ?,
                    restore_latency_us = ?,
                    fallback_used = COALESCE(?, fallback_used),
                    provider = COALESCE(NULLIF(?, ''), provider),
                    model = COALESCE(NULLIF(?, ''), model)
                WHERE call_id = ?
                """,
                (
                    int(stats.restored_total),
                    int(stats.fuzzy_restored_total),
                    int(stats.unrestored_count),
                    int(stats.hallucinated_count),
                    int(stats.residual_placeholders),
                    int(stats.restore_latency_us),
                    fallback_used,
                    provider,
                    model,
                    ctx.call_id,
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 — audit never interrupts LLM calls
        logger.debug("desensitize record_event failed: %s", exc)
    finally:
        if owns_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass
