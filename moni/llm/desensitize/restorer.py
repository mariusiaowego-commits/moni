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
    r"(?:\{\{|｛｛|【)\s*([A-Za-z][A-Za-z0-9_]{0,23})[_＿]([A-Za-z0-9]{8})\s*(?:\}\}|｝｝|】)?"
    r"|(?:\{|｛)\s*([A-Za-z][A-Za-z0-9_]{0,23})[_＿]([A-Za-z0-9]{8})\s*(?:\}|｝)"
    r"|([A-Za-z][A-Za-z0-9_]{0,23})[_＿]([A-Za-z0-9]{8})\s*(?:\}\}|｝｝|】)"
)
_MD_LINE_FENCE_RE = re.compile(
    r"(?:^|\n)[ \t]*(```|~~~)[^\n]*\n(.*?)\n[ \t]*\1[ \t]*(?=\n|$)",
    re.DOTALL,
)
_MD_INLINE_FENCE_RE = re.compile(
    r"(```|~~~)[^\n]*\n?(.*?)\1",
    re.DOTALL,
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


def _is_hallucinated_code(kind: str, code: str) -> bool:
    """Return True for plausible valid-base32 model hallucinations; False for example/non-base32 codes."""
    if kind.upper() == "EXAMPLE":
        return False
    return all(ch in _BASE32_CHARS for ch in code.lower())


def _count_unknown_codes(text: str) -> tuple[int, int]:
    if "{{" not in text and "｛｛" not in text:
        return 0, 0
    hallucinated = 0
    invalid = 0
    for m in _EXPLICIT_UNKNOWN_RE.finditer(text):
        if _is_hallucinated_code(m.group(1), m.group(2)):
            hallucinated += 1
        else:
            invalid += 1
    return hallucinated, invalid


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
            hallucinated, invalid = _count_unknown_codes(result)
            if hallucinated:
                stats.hallucinated_count += hallucinated
            if invalid:
                stats.invalid_code_count += invalid
        else:
            hallucinated = 0
            invalid = 0

            def _sub_unknown(m: re.Match[str]) -> str:
                nonlocal hallucinated, invalid
                if _is_hallucinated_code(m.group(1), m.group(2)):
                    hallucinated += 1
                else:
                    invalid += 1
                return _unknown_fallback(m.group(1))

            result = _EXPLICIT_UNKNOWN_RE.sub(_sub_unknown, result)
            if hallucinated:
                stats.hallucinated_count += hallucinated
            if invalid:
                stats.invalid_code_count += invalid

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


def _balance_json_span(text: str, start: int) -> int | None:
    """Return exclusive end index of a balanced JSON object/array starting at ``start``, or None."""
    stack: list[str] = []
    in_string = False
    backslash_count = 0
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if ch == "\\":
                backslash_count += 1
            else:
                if ch == '"' and (backslash_count & 1) == 0:
                    in_string = False
                backslash_count = 0
            continue
        if ch == '"':
            in_string = True
            backslash_count = 0
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch == "}" or ch == "]":
            if not stack or stack[-1] != ch:
                return None
            stack.pop()
            if not stack:
                return i + 1
    return None


def _extract_json_spans(text: str) -> list[tuple[int, int]]:
    """Extract balanced JSON object/array intervals, prioritizing Markdown code fences."""
    spans: list[tuple[int, int]] = []
    fence_matches = list(_MD_LINE_FENCE_RE.finditer(text))
    if not fence_matches and ("```" in text or "~~~" in text):
        fence_matches = list(_MD_INLINE_FENCE_RE.finditer(text))

    if fence_matches:
        saw_fence_json_opener = False
        for fm in fence_matches:
            body_start, body_end = fm.span(2)
            body = text[body_start:body_end]
            brace = body.find("{")
            bracket = body.find("[")
            if brace < 0 and bracket < 0:
                continue
            saw_fence_json_opener = True
            idx = brace if bracket < 0 else (bracket if brace < 0 else min(brace, bracket))
            end = _balance_json_span(body, idx)
            if end is not None:
                spans.append((body_start + idx, body_start + end))
        if saw_fence_json_opener:
            return spans

    if "}" not in text and "]" not in text:
        return spans

    pos = 0
    n = len(text)
    while pos < n:
        brace = text.find("{", pos)
        bracket = text.find("[", pos)
        if brace < 0 and bracket < 0:
            break
        start = brace if bracket < 0 else (bracket if brace < 0 else min(brace, bracket))
        end = _balance_json_span(text, start)
        if end is not None:
            spans.append((start, end))
            pos = end
        else:
            pos = start + 1
    return spans


def _json_lexical_state(text: str) -> tuple[list[bool], list[bool]]:
    """Compute per-character ``in_string`` and ``quote_boundary`` masks for one JSON fragment."""
    n = len(text)
    in_str = [False] * n
    quote_char = [False] * n
    inside = False
    backslash_count = 0
    for i, ch in enumerate(text):
        if not inside:
            if ch == '"':
                quote_char[i] = True
                inside = True
                backslash_count = 0
        else:
            if ch == "\\":
                in_str[i] = True
                backslash_count += 1
            elif ch == '"' and (backslash_count & 1) == 0:
                quote_char[i] = True
                inside = False
                backslash_count = 0
            else:
                in_str[i] = True
                backslash_count = 0
    return in_str, quote_char


def _is_legal_bare_json_slot(frag: str, start: int, end: int) -> bool:
    """Check whether ``frag[start:end]`` sits in a valid bare JSON value position."""
    i = start - 1
    while i >= 0 and frag[i] in " \t\r\n":
        i -= 1
    if i < 0 or frag[i] not in ":[,":
        return False
    j = end
    n = len(frag)
    while j < n and frag[j] in " \t\r\n":
        j += 1
    return j < n and frag[j] in ",}]"


def _restore_json_fragment(
    frag: str,
    vault: RequestScopeVault | InMemoryVault,
) -> str:
    """Restore one balanced JSON fragment lexically, falling back safely on ambiguity or breakage."""
    t0 = time.perf_counter_ns()
    matches = _iter_matches(frag, vault)
    if not matches:
        hallucinated, invalid = _count_unknown_codes(frag)
        if hallucinated:
            vault.stats.hallucinated_count += hallucinated
        if invalid:
            vault.stats.invalid_code_count += invalid
        vault.stats.restore_latency_us += (time.perf_counter_ns() - t0) // 1000
        return frag

    in_str, quote_char = _json_lexical_state(frag)
    out: list[str] = []
    last = 0
    for a, z, val, _kind, _is_fuzzy in matches:
        if any(quote_char[k] for k in range(a, z)):
            return restore_text(frag, vault)
        if a < len(in_str) and in_str[a]:
            rep = json.dumps(val, ensure_ascii=False)[1:-1]
        elif _is_legal_bare_json_slot(frag, a, z):
            rep = json.dumps(val, ensure_ascii=False)
        else:
            return restore_text(frag, vault)
        out.append(frag[last:a])
        out.append(rep)
        last = z
    out.append(frag[last:])
    candidate = "".join(out)

    try:
        json.loads(frag)
        pre_valid = True
    except Exception:
        pre_valid = False

    if pre_valid:
        try:
            json.loads(candidate)
        except Exception:
            vault.stats.restore_latency_us += (time.perf_counter_ns() - t0) // 1000
            return frag

    stats = vault.stats
    back_counts = stats.back_counts
    for _a, _z, _val, kind, _is_fuzzy in matches:
        back_counts[kind] = back_counts.get(kind, 0) + 1
    stats.restored_total += len(matches)
    stats.fuzzy_restored_total += len(matches)

    hallucinated, invalid = _count_unknown_codes(candidate)
    if hallucinated:
        stats.hallucinated_count += hallucinated
    if invalid:
        stats.invalid_code_count += invalid
    stats.restore_latency_us += (time.perf_counter_ns() - t0) // 1000
    return candidate


def restore_json_aware(
    text: str,
    vault: RequestScopeVault | InMemoryVault,
) -> str:
    """Restore Markdown-fenced or embedded JSON lexically while restoring surrounding prose as text."""
    if not text:
        return text
    try:
        spans = _extract_json_spans(text)
        if not spans:
            return restore_text(text, vault)
        if len(spans) == 1 and spans[0] == (0, len(text)):
            return _restore_json_fragment(text, vault)

        out: list[str] = []
        last = 0
        for a, z in spans:
            if a > last:
                out.append(restore_text(text[last:a], vault))
            out.append(_restore_json_fragment(text[a:z], vault))
            last = z
        if last < len(text):
            out.append(restore_text(text[last:], vault))
        return "".join(out)
    except Exception:
        try:
            return restore_text(text, vault)
        except Exception:
            return text


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
