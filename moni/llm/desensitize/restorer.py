"""Five-level fault-tolerant, JSON-aware, HTML-safe, and streaming restoration."""

from __future__ import annotations

import html
import json
import re
import time
from typing import Callable

from .vault import InMemoryVault, RequestScopeVault

_WORD_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"
)
_BASE32_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz234567")
_MAX_PLACEHOLDER_TAIL = 40

_HASH_TAIL_RE = re.compile(
    r"(\{\{[A-Z][A-Z0-9_]{0,23}_[a-z2-7]{8}\}\})|([_＿])([A-Za-z2-7]{8})(?![A-Za-z0-9_])"
)
_HASH_OR_STANDIN_RE = re.compile(
    r"(\{\{[A-Z][A-Z0-9_]{0,23}_[a-z2-7]{8}\}\})|(?<![A-Za-z0-9_])[Uu]_([A-Za-z2-7]{8})(?![A-Za-z0-9_])|([_＿])([A-Za-z2-7]{8})(?![A-Za-z0-9_])"
)
_STANDIN_USER_RESTORE_RE = re.compile(r"(?<![A-Za-z0-9_])[Uu]_([A-Za-z2-7]{8})(?![A-Za-z0-9_])")
_EXPLICIT_UNKNOWN_RE = re.compile(
    r"(?:\{\{|｛｛)\s*([A-Za-z][A-Za-z0-9_]{0,23})[_＿]([A-Za-z0-9]{8})\s*(?:\}\}|｝｝)"
)
_RESIDUAL_PLACEHOLDER_RE = re.compile(
    r"(?:\{\{|｛｛|【)\s*([A-Za-z][A-Za-z0-9_]{0,23})[_＿]([A-Za-z2-7]{8})\s*(?:\}\}|｝｝|】)?"
    r"|(?:\{|｛)\s*([A-Za-z][A-Za-z0-9_]{0,23})[_＿]([A-Za-z2-7]{8})\s*(?:\}|｝)"
    r"|([A-Za-z][A-Za-z0-9_]{0,23})[_＿]([A-Za-z2-7]{8})\s*(?:\}\}|｝｝|】)"
)
_LOCALIZED_KIND_SUFFIXES = (
    "相关机构",
    "机构",
    "项目",
    "人员",
    "主体",
    "密钥",
    "凭据",
    "密码",
    "口令",
    "手机",
    "电话",
    "邮箱",
    "身份证",
    "银行卡",
)

_FALLBACK_LABELS: dict[str, str] = {
    "ORG": "【某相关机构】",
    "SELF_ORG": "【某相关机构】",
    "PROJECT": "【某业务项目】",
    "PERSON": "【某相关人员】",
}


def _is_word_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch == "_")


def _expand_braces(text: str, a: int, z: int) -> tuple[int, int]:
    """Consume balanced, unbalanced-double, or fullwidth brackets around a placeholder body."""
    la = a
    while la > 0 and text[la - 1] in " \t":
        la -= 1
    rz = z
    while rz < len(text) and text[rz] in " \t":
        rz += 1

    # Level 4: 【KIND_x】
    if la > 0 and text[la - 1] == "【" and rz < len(text) and text[rz] == "】":
        return la - 1, rz + 1

    # Level 1-4: { / {{ / ｛ / ｛｛ and } / }} / ｝ / ｝｝
    l_count = 0
    l_pos = la
    while l_count < 2 and l_pos > 0 and text[l_pos - 1] in "{｛":
        l_pos -= 1
        l_count += 1

    r_count = 0
    r_pos = rz
    while r_count < 2 and r_pos < len(text) and text[r_pos] in "}｝":
        r_pos += 1
        r_count += 1

    out_a = l_pos if (l_count == 2 or (l_count == 1 and r_count >= 1)) else a
    out_z = r_pos if (r_count == 2 or (r_count == 1 and l_count >= 1)) else z
    return out_a, out_z


def _unknown_fallback(kind: str) -> str:
    return _FALLBACK_LABELS.get(kind.upper(), "【脱敏项】")


def _iter_matches(
    text: str,
    vault: RequestScopeVault | InMemoryVault,
) -> list[tuple[int, int, str, str, bool]]:
    """Find all vault-backed placeholder and stand-in matches in ``text``."""
    matches: list[tuple[int, int, str, str, bool]] = []
    if "_" not in text and "＿" not in text:
        return matches

    has_standins = vault.has_standins() and ("u_" in text or "U_" in text)
    scan_re = _HASH_OR_STANDIN_RE if has_standins else _HASH_TAIL_RE
    last_end = 0
    word_chars = _WORD_CHARS
    vault_get = vault.get
    resolved_cache: dict[str, str | None] = {}

    for m in scan_re.finditer(text):
        canonical = m.group(1)
        if canonical is not None:
            ea, ez = m.span()
            if ea < last_end:
                continue
            val = resolved_cache.get(canonical)
            if val is None and canonical not in resolved_cache:
                val = vault_get(canonical)
                resolved_cache[canonical] = val
            if val is not None:
                matches.append((ea, ez, val, canonical[2:-11], False))
                last_end = ez
            continue

        if has_standins:
            standin_hash = m.group(2)
            if standin_hash is not None:
                ea, ez = m.span()
                if ea < last_end:
                    continue
                val = vault.get_standin(f"u_{standin_hash.lower()}")
                if val is not None:
                    matches.append((ea, ez, val, "PATH_USER", text[ea] == "U"))
                    last_end = ez
                continue
            sep_idx, hash_idx = 3, 4
        else:
            sep_idx, hash_idx = 2, 3

        sep_pos, z = m.span(sep_idx)[0], m.end()
        if sep_pos < last_end or sep_pos == 0:
            continue
        hash_part = m.group(hash_idx).lower()
        prev_ch = text[sep_pos - 1]

        a = sep_pos
        val = None
        kind = ""
        if prev_ch in word_chars:
            limit = last_end if last_end > sep_pos - 24 else sep_pos - 24
            if limit < 0:
                limit = 0
            while a > limit and text[a - 1] in word_chars:
                a -= 1
            if a > 0 and text[a - 1] in word_chars:
                continue
            kind_part = text[a:sep_pos]
            if not kind_part or not kind_part[0].isalpha():
                continue
            kind = kind_part.upper()
            canonical = f"{{{{{kind}_{hash_part}}}}}"
            val = resolved_cache.get(canonical)
            if val is None and canonical not in resolved_cache:
                val = vault_get(canonical)
                resolved_cache[canonical] = val
        elif "\u4e00" <= prev_ch <= "\u9fff":
            limit = max(last_end, sep_pos - 12)
            while a > limit and "\u4e00" <= text[a - 1] <= "\u9fff":
                a -= 1
            han_run = text[a:sep_pos]
            # Prefer bracketed localized kind; otherwise trim to known localized noun suffix.
            if not (a > 0 and text[a - 1] in "{｛【 \t\n"):
                matched_sfx = ""
                for sfx in _LOCALIZED_KIND_SUFFIXES:
                    if han_run.endswith(sfx):
                        matched_sfx = sfx
                        break
                if not matched_sfx:
                    continue
                a = sep_pos - len(matched_sfx)
            hit = vault.lookup_by_suffix(hash_part)
            if hit is not None:
                kind = hit[0][2:-11]
                val = hit[1]

        if val is None:
            continue

        if a >= 2 and z + 2 <= len(text) and text[a - 2 : a] == "{{" and text[z : z + 2] == "}}":
            ea, ez = a - 2, z + 2
        else:
            ea, ez = _expand_braces(text, a, z)
        if ea < last_end:
            continue
        matches.append((ea, ez, val, kind, True))
        last_end = ez

    return matches


def _count_explicit_unknown(text: str) -> int:
    if "{{" not in text and "｛｛" not in text:
        return 0
    return sum(1 for _ in _EXPLICIT_UNKNOWN_RE.finditer(text))


def _apply_replacements(
    text: str,
    matches: list[tuple[int, int, str, str, bool]],
    transform: Callable[[int, str], str],
    *,
    preserve_unknown: bool,
    vault: RequestScopeVault | InMemoryVault,
    t0_ns: int,
    force_fuzzy: bool = False,
) -> str:
    stats = vault.stats
    if matches:
        out: list[str] = []
        last = 0
        back_counts = stats.back_counts
        fuzzy_count = 0
        for a, z, val, kind, is_fuzzy in matches:
            out.append(text[last:a])
            out.append(transform(a, val))
            last = z
            if is_fuzzy or force_fuzzy:
                fuzzy_count += 1
            back_counts[kind] = back_counts.get(kind, 0) + 1
        out.append(text[last:])
        result = "".join(out)
        stats.restored_total += len(matches)
        if fuzzy_count:
            stats.fuzzy_restored_total += fuzzy_count
    else:
        result = text

    if "{{" in result or "｛｛" in result:
        if preserve_unknown:
            hallucinated = _count_explicit_unknown(result)
            if hallucinated:
                stats.hallucinated_count += hallucinated
        else:
            hallucinated = 0

            def _sub_unknown(m: re.Match[str]) -> str:
                nonlocal hallucinated
                hallucinated += 1
                code = m.group(2).lower()
                if all(ch in _BASE32_CHARS for ch in code):
                    return _unknown_fallback(m.group(1))
                # Preserve non-base32 example codes (e.g. {{ORG_11111111}}) verbatim.
                return m.group(0)

            result = _EXPLICIT_UNKNOWN_RE.sub(_sub_unknown, result)
            if hallucinated:
                stats.hallucinated_count += hallucinated

    stats.restore_latency_us += (time.perf_counter_ns() - t0_ns) // 1000
    return result


def restore_text(
    text: str,
    vault: RequestScopeVault | InMemoryVault,
    *,
    escaped: bool = False,
) -> str:
    """Restore placeholders in natural language (or escaped JSON string when ``escaped=True``)."""
    if not text:
        return text
    t0 = time.perf_counter_ns()
    matches = _iter_matches(text, vault)
    if not matches and ("{{" not in text and "｛｛" not in text):
        vault.stats.restore_latency_us += (time.perf_counter_ns() - t0) // 1000
        return text

    if escaped:
        return _apply_replacements(
            text,
            matches,
            lambda _pos, val: json.dumps(val, ensure_ascii=False)[1:-1],
            preserve_unknown=True,
            vault=vault,
            t0_ns=t0,
            force_fuzzy=True,
        )
    return _apply_replacements(
        text,
        matches,
        lambda _pos, val: val,
        preserve_unknown=False,
        vault=vault,
        t0_ns=t0,
    )


def restore_html_safe(
    text: str,
    vault: RequestScopeVault | InMemoryVault,
) -> str:
    """Restore placeholders with HTML entity escaping to prevent DOM/XSS breakage."""
    if not text:
        return text
    t0 = time.perf_counter_ns()
    matches = _iter_matches(text, vault)
    if not matches and ("{{" not in text and "｛｛" not in text):
        vault.stats.restore_latency_us += (time.perf_counter_ns() - t0) // 1000
        return text
    return _apply_replacements(
        text,
        matches,
        lambda _pos, val: html.escape(val, quote=True),
        preserve_unknown=False,
        vault=vault,
        t0_ns=t0,
    )


def _json_string_positions(text: str) -> list[bool]:
    """Compute a per-character mask indicating whether index ``i`` is inside a JSON string."""
    in_str = [False] * len(text)
    inside = False
    escaped = False
    for i, ch in enumerate(text):
        in_str[i] = inside
        if not inside:
            if ch == '"':
                inside = True
        else:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str[i] = False
                inside = False
    return in_str


def restore_json_aware(
    text: str,
    vault: RequestScopeVault | InMemoryVault,
) -> str:
    """Lexically restore placeholders in JSON text, escaping inside strings and quoting bare values."""
    if not text:
        return text
    t0 = time.perf_counter_ns()
    matches = _iter_matches(text, vault)
    if not matches:
        hallucinated = _count_explicit_unknown(text)
        if hallucinated:
            vault.stats.hallucinated_count += hallucinated
        vault.stats.restore_latency_us += (time.perf_counter_ns() - t0) // 1000
        return text

    in_str = _json_string_positions(text)

    def _transform(pos: int, val: str) -> str:
        if pos < len(in_str) and in_str[pos]:
            return json.dumps(val, ensure_ascii=False)[1:-1]
        # Outside quotes: auto-quote if sitting as a JSON value after ':', '[', or ','.
        i = pos - 1
        while i >= 0 and text[i] in " \t\r\n":
            i -= 1
        if i >= 0 and text[i] in ":[,":
            return json.dumps(val, ensure_ascii=False)
        return json.dumps(val, ensure_ascii=False)[1:-1]

    # Strict JSON preserves unknown placeholders literally to avoid breaking schema enums.
    return _apply_replacements(
        text,
        matches,
        _transform,
        preserve_unknown=True,
        vault=vault,
        t0_ns=t0,
        force_fuzzy=True,
    )


class StreamRestorer:
    """Incremental streaming restorer with partial-placeholder tail buffering."""

    def __init__(
        self,
        vault: RequestScopeVault | InMemoryVault,
        *,
        escaped: bool = False,
    ) -> None:
        self._vault = vault
        self._escaped = escaped
        self._buf = ""

    def feed(self, delta: str) -> str:
        """Accept one streaming chunk and return the safely-restored prefix."""
        if not delta:
            return ""
        data = self._buf + delta
        hold = self._partial_tail(data)
        split_at = len(data) - hold
        emit_raw = data[:split_at]
        self._buf = data[split_at:]
        if not emit_raw:
            return ""
        return restore_text(emit_raw, self._vault, escaped=self._escaped)

    def flush(self, *, aborted: bool = False) -> str:
        """Flush and restore any remaining buffered characters at stream end."""
        if not self._buf:
            return ""
        rem = self._buf
        self._buf = ""
        if aborted:
            hold = self._brace_tail(rem)
            if hold == 0:
                word_hold = self._word_tail(rem)
                if word_hold > 0 and ("_" in rem[-word_hold:] or "＿" in rem[-word_hold:]):
                    hold = word_hold
            if hold > 0:
                rem = rem[: len(rem) - hold]
        if not rem:
            return ""
        return restore_text(rem, self._vault, escaped=self._escaped)

    def _partial_tail(self, s: str) -> int:
        if not s:
            return 0
        brace_hold = self._brace_tail(s)
        if brace_hold > 0:
            return brace_hold
        return self._word_tail(s)

    def _brace_tail(self, s: str) -> int:
        window_start = max(0, len(s) - _MAX_PLACEHOLDER_TAIL)
        idx = -1
        for opener in ("{", "｛", "【"):
            pos = s.rfind(opener, window_start)
            if pos > idx:
                idx = pos
        if idx < 0:
            return 0
        if idx > 0 and s[idx - 1] == s[idx] and s[idx] in "{｛":
            idx -= 1

        tail = s[idx:]
        if len(tail) >= _MAX_PLACEHOLDER_TAIL:
            return 0
        if tail.endswith(("}}", "｝｝", "】")):
            return 0

        # Hold a trailing single-brace {KIND_xxxxxxxx} in case a second '}' arrives next.
        inner = tail.lstrip("{｛【").lstrip(" \t")
        if not inner:
            return len(tail)

        # Allow alphanumeric/CJK kind prefix, '_'/'＿', base32 hash, optional whitespace and at most one closer.
        pos = 0
        while pos < len(inner) and inner[pos] not in " \t}｝】":
            if inner[pos] in "\r\n":
                return 0
            pos += 1
        rest = inner[pos:].lstrip(" \t")
        if rest not in {"", "}", "｝", "】"}:
            return 0
        return len(tail)

    def _word_tail(self, s: str) -> int:
        # Walk ASCII word chars first so "裸OR" doesn't swallow preceding Chinese text.
        i = len(s)
        while i > 0 and (s[i - 1] in _WORD_CHARS or s[i - 1] == "＿"):
            i -= 1
            if len(s) - i > _MAX_PLACEHOLDER_TAIL:
                return 0
        word = s[i:]
        if word:
            if self._vault.has_standins():
                low = word.lower()
                if low == "u" or (
                    low.startswith("u_")
                    and len(low) <= 10
                    and all(c in _BASE32_CHARS for c in low[2:])
                ):
                    return len(word)

            upper = word.upper().replace("＿", "_")
            for kind in self._vault.kinds():
                prefix = f"{kind}_"
                if prefix.startswith(upper):
                    return len(word)
                if upper.startswith(prefix):
                    hash_part = upper[len(prefix) :].lower()
                    if len(hash_part) <= 8 and all(c in _BASE32_CHARS for c in hash_part):
                        return len(word)

        # Level-5 localized Han prefix + '_'/'＿' + partial base32 suffix.
        if word and word[0] in "_＿" and all(c in _BASE32_CHARS for c in word[1:].lower()):
            j = i
            while j > 0 and "\u4e00" <= s[j - 1] <= "\u9fff" and (len(s) - j) < _MAX_PLACEHOLDER_TAIL:
                j -= 1
            if j < i:
                return len(s) - j

        if not word and s and "\u4e00" <= s[-1] <= "\u9fff":
            # Hold trailing localized kind noun (e.g. "机构") in case '_' arrives next.
            for sfx in _LOCALIZED_KIND_SUFFIXES:
                if s.endswith(sfx):
                    return len(sfx)
        return 0
