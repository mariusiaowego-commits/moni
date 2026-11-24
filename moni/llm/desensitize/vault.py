"""In-memory and request-scoped placeholder vaults (zero disk persistence)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from .salt import load_salt

# Aligned with magpie write-side kind limit (internal/redact/rules.go:28, maxKind = 24);
# stricter than magpie's read-side regex (internal/redact/redact.go:331, unbounded '*').
# Safe because moni only restores placeholders it writes (all normalized kinds <= 24 chars).
PLACEHOLDER_RE = re.compile(r"\{\{[A-Z][A-Z0-9_]{0,23}_[a-z2-7]{8}\}\}")
STANDIN_USER_RE = re.compile(r"(?<![A-Za-z0-9_])u_[a-z2-7]{8}(?![A-Za-z0-9_])")

DEFAULT_MAX_ENTRIES = 50_000
DEFAULT_TTL_SECONDS = 24.0 * 3600.0
MIN_KNOWN_LEN = 8


def make_placeholder(kind: str, canonical_value: str, salt: bytes) -> str:
    """Build deterministic ``{{KIND_xxxxxxxx}}`` placeholder using HMAC-SHA256."""
    digest = hmac.new(
        salt,
        f"{kind}\x00{canonical_value}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    # First 8 RFC 4648 base32 chars consume exactly 40 bits (5 bytes).
    code = base64.b32encode(digest[:5]).decode("ascii").lower()
    return "{{" + kind + "_" + code + "}}"


def make_path_user_standin(username: str, salt: bytes) -> str:
    """Build shape-preserving ``u_<8-char base32>`` stand-in for /Users/<name>."""
    digest = hmac.new(
        salt,
        f"PATH_USER\x00{username}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    code = base64.b32encode(digest[:5]).decode("ascii").lower()
    return f"u_{code}"


@dataclass(slots=True)
class RestoreStats:
    """Cumulative restoration counters for a vault."""

    restored_total: int = 0
    fuzzy_restored_total: int = 0
    unrestored_count: int = 0
    hallucinated_count: int = 0
    invalid_code_count: int = 0
    residual_placeholders: int = 0
    restore_latency_us: int = 0
    back_counts: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class _VaultEntry:
    kind: str
    value: str
    expires_at: float


class InMemoryVault:
    """Thread-safe bounded LRU+TTL mapping from placeholders to original values."""

    def __init__(
        self,
        *,
        salt: bytes | None = None,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._lock = threading.RLock()
        self._salt = bytes(salt) if salt is not None else load_salt()
        self._max_entries = max_entries
        self._ttl_seconds = float(ttl_seconds)
        self._entries: OrderedDict[str, _VaultEntry] = OrderedDict()
        self._forward: dict[tuple[str, str], str] = {}
        self._suffix_index: dict[str, set[str]] = {}
        self._kinds: set[str] = set()
        self._standins: dict[str, _VaultEntry] = {}
        self._known: dict[str, tuple[str, str]] = {}
        self.stats = RestoreStats()

    @property
    def salt(self) -> bytes:
        with self._lock:
            return self._salt

    def put(
        self,
        kind: str,
        canonical_value: str,
        *,
        raw_value: str | None = None,
        standin: str | None = None,
    ) -> str:
        """Register a sensitive value and return its placeholder or stand-in."""
        now = time.monotonic()
        expires_at = now + self._ttl_seconds
        with self._lock:
            if standin is not None:
                self._standins[standin] = _VaultEntry(kind, canonical_value, expires_at)
                self._kinds.add(kind)
                self._record_known(kind, standin, raw_value or canonical_value)
                return standin

            fwd_key = (kind, canonical_value)
            placeholder = self._forward.get(fwd_key)
            if placeholder is None:
                placeholder = make_placeholder(kind, canonical_value, self._salt)
                self._forward[fwd_key] = placeholder

            entry = self._entries.get(placeholder)
            if entry is not None:
                entry.expires_at = expires_at
                self._entries.move_to_end(placeholder)
            else:
                self._evict_if_needed(now)
                self._entries[placeholder] = _VaultEntry(kind, canonical_value, expires_at)
                suffix = placeholder[-10:-2]
                self._suffix_index.setdefault(suffix, set()).add(placeholder)
                self._kinds.add(kind)

            self._record_known(kind, placeholder, raw_value or canonical_value)
            return placeholder

    def _record_known(self, kind: str, placeholder: str, value: str) -> None:
        if kind == "PATH_USER" or len(value) < MIN_KNOWN_LEN:
            return
        existing = self._known.get(value)
        if existing is None or placeholder < existing[1]:
            self._known[value] = (kind, placeholder)

    def _evict_if_needed(self, now: float) -> None:
        expired = [k for k, v in self._entries.items() if v.expires_at <= now]
        for k in expired:
            self._remove_entry(k)
        while len(self._entries) >= self._max_entries:
            oldest, _ = self._entries.popitem(last=False)
            self._remove_suffix_ref(oldest)

    def _remove_entry(self, placeholder: str) -> None:
        entry = self._entries.pop(placeholder, None)
        if entry is not None:
            self._forward.pop((entry.kind, entry.value), None)
        self._remove_suffix_ref(placeholder)

    def _remove_suffix_ref(self, placeholder: str) -> None:
        suffix = placeholder[-10:-2]
        bucket = self._suffix_index.get(suffix)
        if bucket is not None:
            bucket.discard(placeholder)
            if not bucket:
                self._suffix_index.pop(suffix, None)

    def get(self, placeholder: str) -> str | None:
        """Look up a canonical placeholder; returns None if missing or expired."""
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(placeholder)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._remove_entry(placeholder)
                return None
            self._entries.move_to_end(placeholder)
            return entry.value

    def get_standin(self, standin: str) -> str | None:
        """Look up a shape-preserving stand-in token (e.g. ``u_xxxxxxxx``)."""
        now = time.monotonic()
        with self._lock:
            entry = self._standins.get(standin)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._standins.pop(standin, None)
                return None
            return entry.value

    def lookup_by_suffix(self, suffix: str) -> tuple[str, str] | None:
        """Return ``(placeholder, value)`` only if ``suffix`` uniquely matches one active entry."""
        norm = suffix.lower()
        now = time.monotonic()
        with self._lock:
            candidates = list(self._suffix_index.get(norm, ()))
            if not candidates:
                return None
            live: list[tuple[str, str]] = []
            for p in candidates:
                entry = self._entries.get(p)
                if entry is None or entry.expires_at <= now:
                    self._remove_entry(p)
                    continue
                live.append((p, entry.value))
            if len(live) == 1:
                return live[0]
            return None

    def has_standins(self) -> bool:
        with self._lock:
            return bool(self._standins)

    def kinds(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._kinds)

    def has_known(self) -> bool:
        with self._lock:
            return bool(self._known)

    def known_items(self) -> dict[str, tuple[str, str]]:
        with self._lock:
            return dict(self._known)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._forward.clear()
            self._suffix_index.clear()
            self._kinds.clear()
            self._standins.clear()
            self._known.clear()
            self.stats = RestoreStats()

    def rotate_salt(self, new_salt: bytes | None = None) -> bytes:
        with self._lock:
            self.clear()
            self._salt = bytes(new_salt) if new_salt is not None else load_salt(rotate=True)
            return self._salt

    def __len__(self) -> int:
        now = time.monotonic()
        with self._lock:
            expired = [k for k, v in self._entries.items() if v.expires_at <= now]
            for k in expired:
                self._remove_entry(k)
            return len(self._entries)

    def __contains__(self, placeholder: object) -> bool:
        return isinstance(placeholder, str) and self.get(placeholder) is not None


class RequestScopeVault:
    """Single-request overlay vault backed by an optional shared InMemoryVault."""

    def __init__(
        self,
        parent: InMemoryVault | None = None,
        *,
        salt: bytes | None = None,
        write_through: bool = False,
    ) -> None:
        self._parent = parent
        self._write_through = write_through and parent is not None
        effective_salt = salt if salt is not None else (parent.salt if parent is not None else None)
        self._local = InMemoryVault(salt=effective_salt)

    @property
    def salt(self) -> bytes:
        return self._local.salt

    @property
    def parent(self) -> InMemoryVault | None:
        return self._parent

    @property
    def stats(self) -> RestoreStats:
        return self._local.stats

    def put(
        self,
        kind: str,
        canonical_value: str,
        *,
        raw_value: str | None = None,
        standin: str | None = None,
    ) -> str:
        if standin is None:
            cached = self._local._forward.get((kind, canonical_value))
            if cached is not None and cached in self._local._entries:
                return cached
        token = self._local.put(kind, canonical_value, raw_value=raw_value, standin=standin)
        if self._write_through and self._parent is not None:
            self._parent.put(kind, canonical_value, raw_value=raw_value, standin=standin)
        return token

    def get(self, placeholder: str) -> str | None:
        val = self._local.get(placeholder)
        if val is not None:
            return val
        if self._parent is not None:
            return self._parent.get(placeholder)
        return None

    def get_standin(self, standin: str) -> str | None:
        val = self._local.get_standin(standin)
        if val is not None:
            return val
        if self._parent is not None:
            return self._parent.get_standin(standin)
        return None

    def lookup_by_suffix(self, suffix: str) -> tuple[str, str] | None:
        # Prefer unique match inside the active request scope before falling back to parent.
        hit = self._local.lookup_by_suffix(suffix)
        if hit is not None:
            return hit
        if self._parent is not None:
            return self._parent.lookup_by_suffix(suffix)
        return None

    def has_standins(self) -> bool:
        if self._local.has_standins():
            return True
        return self._parent.has_standins() if self._parent is not None else False

    def kinds(self) -> frozenset[str]:
        ks = self._local.kinds()
        if self._parent is not None:
            return ks | self._parent.kinds()
        return ks

    def has_known(self) -> bool:
        if self._local.has_known():
            return True
        return self._parent.has_known() if self._parent is not None else False

    def known_items(self) -> dict[str, tuple[str, str]]:
        if self._parent is None:
            return self._local.known_items()
        if not self._local.has_known():
            return self._parent.known_items()
        merged = self._parent.known_items()
        merged.update(self._local.known_items())
        return merged

    def clear(self) -> None:
        self._local.clear()

    def finalize_restore(self, text: str, *, mode: str = "plain") -> str:
        """Scan residual placeholders, retry once, and normalize remaining unknowns."""
        if mode not in {"plain", "html", "json"}:
            raise ValueError(f"unsupported restore mode: {mode!r}")
        if not text:
            return text

        t0 = time.perf_counter_ns()
        from .restorer import (
            _RESIDUAL_PLACEHOLDER_RE,
            _is_hallucinated_code,
            _unknown_fallback,
            restore_html_safe,
            restore_json_aware,
            restore_text,
        )

        initial_hits = sum(1 for _ in _RESIDUAL_PLACEHOLDER_RE.finditer(text))
        t_before_restore = time.perf_counter_ns()
        if initial_hits == 0:
            self._local.stats.restore_latency_us += (t_before_restore - t0) // 1000
            return text

        self._local.stats.residual_placeholders += initial_hits
        self._local.stats.restore_latency_us += (t_before_restore - t0) // 1000

        if mode == "json":
            out = restore_json_aware(text, self)
        elif mode == "html":
            out = restore_html_safe(text, self)
        else:
            out = restore_text(text, self)

        t_after_restore = time.perf_counter_ns()
        if mode == "json":
            still_unrestored = 0
            for m in _RESIDUAL_PLACEHOLDER_RE.finditer(out):
                kind = m.group(1) or m.group(3) or m.group(5) or ""
                code = m.group(2) or m.group(4) or m.group(6) or ""
                if _is_hallucinated_code(kind, code):
                    still_unrestored += 1
            if still_unrestored:
                self._local.stats.unrestored_count += still_unrestored
        else:
            remaining_unrestored = 0
            remaining_invalid = 0

            def _replace_residual(m: re.Match[str]) -> str:
                nonlocal remaining_unrestored, remaining_invalid
                kind = m.group(1) or m.group(3) or m.group(5) or ""
                code = m.group(2) or m.group(4) or m.group(6) or ""
                if _is_hallucinated_code(kind, code):
                    remaining_unrestored += 1
                else:
                    remaining_invalid += 1
                fallback = _unknown_fallback(kind)
                if mode == "html":
                    return html.escape(fallback, quote=True)
                return fallback

            if _RESIDUAL_PLACEHOLDER_RE.search(out):
                out = _RESIDUAL_PLACEHOLDER_RE.sub(_replace_residual, out)
            if remaining_unrestored:
                self._local.stats.unrestored_count += remaining_unrestored
            if remaining_invalid:
                self._local.stats.invalid_code_count += remaining_invalid
        self._local.stats.restore_latency_us += (time.perf_counter_ns() - t_after_restore) // 1000
        return out

    def __len__(self) -> int:
        return len(self._local)

    def __contains__(self, placeholder: object) -> bool:
        return isinstance(placeholder, str) and self.get(placeholder) is not None
