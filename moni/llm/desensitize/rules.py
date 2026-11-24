"""Detection rules, alias normalization, user rules, and anti-false-positive guards."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Iterable, NamedTuple, Sequence
from urllib.parse import unquote

logger = logging.getLogger(__name__)

_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_TOKEN_CH = _ALNUM + "_-"
_SECRET_CH = r"[A-Za-z0-9_\-./+=~!@#%^&*]"

MAX_USER_RULES = 32
MAX_CUSTOM_REGEX_LEN = 300
MIN_CUSTOM_PREFIX_LEN = 3
MAX_CUSTOM_PREFIX_LEN = 64
MAX_KIND_LEN = 24

# Mirrors moni/web/redact.py:_TOKEN_PARAM_NAMES (with hyphen->underscore folding).
_TOKEN_PARAM_NAMES: frozenset[str] = frozenset(
    {
        "token",
        "poc_token",
        "access_token",
        "refresh_token",
        "id_token",
        "auth_token",
        "secret",
        "client_secret",
        "api_key",
        "apikey",
        "key",
        "auth",
        "authorization",
        "password",
        "passwd",
        "jwt",
        "signature",
        "sig",
    }
)
_JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$")
_URL_RE = re.compile(r"""[A-Za-z][A-Za-z0-9+.-]*://[^\s<>"'`\)\]}]+""")

_DOC_FILES: frozenset[str] = frozenset(
    {
        "agents.md",
        "claude.md",
        "gemini.md",
        "readme.md",
        "skill.md",
        "changelog.md",
        "contributing.md",
        "license.md",
        "lessons.md",
        "todo.md",
    }
)
_FILE_EXTS: frozenset[str] = frozenset(
    {
        "jpg",
        "jpeg",
        "png",
        "gif",
        "webp",
        "svg",
        "heic",
        "bmp",
        "ico",
        "pdf",
        "mp4",
        "mp3",
        "wav",
        "json",
        "yaml",
        "yml",
        "toml",
        "xml",
        "txt",
        "csv",
        "log",
        "html",
        "css",
        "js",
        "ts",
        "tsx",
        "jsx",
        "gz",
        "tar",
        "zip",
        "mov",
        "md",
    }
)
_FAKE_EMAIL_DOMAINS = (
    "example.com",
    "example.org",
    "example.net",
    "localhost",
    ".test",
    ".invalid",
    ".example",
)

_FINANCIAL_UNITS = (
    "亿元",
    "万元",
    "元",
    "美元",
    "港元",
    "港币",
    "欧元",
    "日元",
    "人民币",
    "rmb",
    "usd",
    "cny",
    "hkd",
    "bp",
    "基点",
    "%",
    "％",
    "股",
    "户",
    "笔",
    "万网点",
    "个网点",
    "网点",
    "家",
    "倍",
    "位",
    "字节",
    "毫秒",
    "ms",
    "秒",
    "分钟",
    "小时",
)
_PHONE_FIELD_WORDS: frozenset[str] = frozenset(
    {
        "phone",
        "phones",
        "mobile",
        "mobiles",
        "tel",
        "telephone",
        "cell",
        "cellphone",
        "contact",
        "contacts",
        "whatsapp",
        "sms",
        "msisdn",
        "fax",
        "number",
    }
)
_PHONE_CJK_MARKS = ("手机", "电话", "電話", "联系", "聯繫", "联络")
_ID_PROSE_WORDS = (
    "order",
    "id",
    "uid",
    "no.",
    "no",
    "#",
    "number",
    "ticket",
    "invoice",
    "timestamp",
    "ts",
    "订单号",
    "订单编号",
    "订单",
    "单号",
    "编号",
    "流水号",
    "工单号",
    "工单",
    "运单号",
    "快递单号",
    "交易号",
    "序号",
    "账单号",
    "id号",
    "时间戳",
    "金额",
    "成交额",
    "成交量",
    "资产",
    "余额",
    "市值",
    "营收",
    "利润",
    "总计",
    "合计",
)
_CREDENTIAL_FIELD_REJECT = frozenset(
    {
        "id",
        "ids",
        "uuid",
        "guid",
        "page",
        "next",
        "cursor",
        "continuation",
        "csrf",
        "xsrf",
        "trace",
        "request",
        "idempotency",
        "sync",
        "nonce",
        "hash",
        "digest",
        "checksum",
        "sha",
        "fingerprint",
        "etag",
        "cache",
        "commit",
        "object",
        "blob",
        "file",
        "content",
        "revision",
        "rev",
        "partition",
        "shard",
        "bucket",
        "dedup",
        "dedupe",
        "lookup",
        "row",
        "sort",
    }
)

_VERSION_RE = re.compile(
    r"^(?:[~^]|[<>=!~]=?|==)?[vV]?\d+(?:\.\d+)+"
    r"(?:[-+._]?(?:(?:alpha|beta|rc|pre|preview|post|dev|canary|next|nightly|snapshot|release|final|ga|stable|build|incubating)\d*|[abcmABCM]\d+|\d+))*$",
    re.IGNORECASE,
)
_STAMP_RE = re.compile(
    r"^\d{4}-\d{1,2}-\d{1,2}[Tt]\d{1,2}(?::\d{1,2}){0,2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:?\d{2})?$"
)
_IDENT_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SCALE_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?x$")


def normalize_kind(kind: str) -> str:
    """Normalize a rule kind to ``[A-Z][A-Z0-9_]{0,23}`` (default ``CUSTOM``)."""
    out: list[str] = []
    for ch in (kind or "").strip().upper():
        if "A" <= ch <= "Z" or "0" <= ch <= "9" or ch == "_":
            out.append(ch)
        elif ch in {" ", "-", "."}:
            out.append("_")
    cleaned = "".join(out).strip("_")
    if not cleaned:
        return "CUSTOM"
    if not ("A" <= cleaned[0] <= "Z"):
        cleaned = f"CUSTOM_{cleaned}"
    if len(cleaned) > MAX_KIND_LEN:
        cleaned = cleaned[:MAX_KIND_LEN].rstrip("_")
    return cleaned or "CUSTOM"


def is_ascii_word_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch == "_")


def is_pure_ascii(text: str) -> bool:
    return bool(text) and text.isascii()


def _not_a_variable(val: str) -> bool:
    if not val or val[0] in "$%{<[*":
        return False
    return val.strip("*xX.") != ""


def _not_a_slug(val: str) -> bool:
    """Reject lowercase hyphenated article slugs like ``sk-hynix-third-quarter``."""
    if any("A" <= c <= "Z" for c in val):
        return True
    tail = val[3:] if val.startswith("sk-") else val
    if all(c in "0123456789abcdef-_" for c in tail):
        return True
    return any(len(part) >= 16 for part in re.split(r"[-_]", val))


def _secret_value(val: str) -> bool:
    if not _not_a_variable(val) or val.startswith(("process.env", "os.")):
        return False
    has_alpha = any(("A" <= c <= "Z" or "a" <= c <= "z") for c in val)
    has_digit = any("0" <= c <= "9" for c in val)
    return has_alpha and has_digit


@lru_cache(maxsize=4096)
def chinese_id_checksum(val: str) -> bool:
    """Validate 18-digit Chinese resident ID card using GB 11643-1999 mod-11."""
    if len(val) != 18 or not val[:17].isdigit():
        return False
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    total = sum((ord(val[i]) - 48) * weights[i] for i in range(17))
    expected = "10X98765432"[total % 11]
    return val[17].upper() == expected


@lru_cache(maxsize=4096)
def luhn_checksum(val: str) -> bool:
    """Validate 15-19 digit bank card number using Luhn mod-10."""
    digits = [ord(c) - 48 for c in val if "0" <= c <= "9"]
    if not (15 <= len(digits) <= 19):
        return False
    total = 0
    for idx, d in enumerate(reversed(digits)):
        if idx % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _name_words(name: str) -> list[str]:
    words: list[str] = []
    cur: list[str] = []
    for i, ch in enumerate(name):
        if ch in "_-.":
            if cur:
                words.append("".join(cur))
                cur.clear()
        elif "A" <= ch <= "Z":
            if i > 0 and "a" <= name[i - 1] <= "z" and cur:
                words.append("".join(cur))
                cur.clear()
            cur.append(ch.lower())
        else:
            cur.append(ch.lower())
    if cur:
        words.append("".join(cur))
    return words


def _field_name_before(s: str, a: int) -> tuple[str, str, bool]:
    i = a
    while i > 0 and s[i - 1] in " \t\"'\\":
        i -= 1
    if i == 0:
        return "", "", False
    sep_ch = s[i - 1]
    if sep_ch not in ":：=>":
        return "", "", False
    if sep_ch == ">":
        left = max(0, i - 65)
        k = s.rfind("<", left, i - 1)
        if k < 0:
            return "", "", False
        tag = s[k + 1 : i - 1].split()[0] if s[k + 1 : i - 1].strip() else ""
        return (tag, "", True) if tag else ("", "", False)
    i -= 1
    while i > 0 and s[i - 1] in " \t\"'\\":
        i -= 1
    end = i
    while i > 0 and (s[i - 1].isalnum() or s[i - 1] in "_.-" or ord(s[i - 1]) > 127):
        i -= 1
    name = s[i:end]
    if not name:
        return "", "", False
    prev = ""
    if i > 0 and s[i - 1] == " ":
        j = i - 1
        while j > 0 and s[j - 1] == " ":
            j -= 1
        p_end = j
        while j > 0 and (s[j - 1].isalnum() or s[j - 1] in "_.-"):
            j -= 1
        prev = s[j:p_end]
    return name, prev, True


def _is_phone_field(name: str) -> bool:
    if any(w in _PHONE_FIELD_WORDS for w in _name_words(name)):
        return True
    return any(m in name for m in _PHONE_CJK_MARKS)


_FINANCIAL_UNIT_PREFIX_RE = re.compile(
    r"[ \t]*(?:" + "|".join(re.escape(u) for u in _FINANCIAL_UNITS) + ")",
    re.IGNORECASE,
)
_ID_PROSE_WORDS_TUPLE = tuple(_ID_PROSE_WORDS)


def _followed_by_unit(s: str, b: int) -> bool:
    return _FINANCIAL_UNIT_PREFIX_RE.match(s, b) is not None


_ID_PROSE_EXCEPT_NO = tuple(w for w in _ID_PROSE_WORDS if w not in {"no", "no.", "number"})
_ID_PROSE_NO_WORDS = ("no.", "no", "number")


_NON_UNIT_FOLLOW = frozenset("，。；、！？\n\r)}]\"'”」")


@lru_cache(maxsize=2048)
def _prose_before_allows_phone(snippet: str) -> bool:
    before = snippet.rstrip(" \t(\"'“「")
    for copula in ("是", "为", "為"):
        if before.endswith(copula):
            before = before[: -len(copula)].rstrip(" \t")
    low = before.lower()
    if low.endswith(_ID_PROSE_EXCEPT_NO):
        for w in _ID_PROSE_EXCEPT_NO:
            if low.endswith(w):
                rest = low[: len(low) - len(w)]
                if w[0].isascii() and rest and (rest[-1].isalnum() or rest[-1] == "_"):
                    continue
                return False
    if low.endswith(_ID_PROSE_NO_WORDS):
        for w in _ID_PROSE_NO_WORDS:
            if low.endswith(w):
                rest = low[: len(low) - len(w)]
                if rest and (rest[-1].isalnum() or rest[-1] == "_"):
                    continue
                tail_word = rest.rstrip().split()[-1] if rest.rstrip() else ""
                return _is_phone_field(tail_word)
    return True


def _phone_at(s: str, a: int, b: int) -> bool:
    if b < len(s) and s[b] not in _NON_UNIT_FOLLOW:
        if _FINANCIAL_UNIT_PREFIX_RE.match(s, b) is not None:
            return False
    # Explicit +86 / 86 prefix is always phone.
    if b - a > 11:
        return True
    name, prev, is_field = _field_name_before(s, a)
    if is_field:
        return _is_phone_field(name) or (bool(prev) and _is_phone_field(prev))
    if a > 0 and s[a - 1] == "#":
        return False
    return _prose_before_allows_phone(s[max(0, a - 32) : a])


def _bank_card_at(s: str, _a: int, b: int) -> bool:
    return not _followed_by_unit(s, b)


@lru_cache(maxsize=4096)
def _real_email(val: str) -> bool:
    at = val.rfind("@")
    if at <= 0:
        return False
    local = val[:at]
    if local.lower() == "git" or local.endswith("@"):
        return False
    host = val[at + 1 :].lower()
    if "." not in host or host.startswith(".") or host.endswith("."):
        return False
    if host in _DOC_FILES:
        return False
    dot = host.rfind(".")
    ext = host[dot + 1 :]
    if ext in _FILE_EXTS:
        if ext in {"zip", "mov", "md"} and _SCALE_RE.match(host[:dot]):
            return False
        if ext not in {"zip", "mov", "md"}:
            return False
    for fake in _FAKE_EMAIL_DOMAINS:
        if host == fake.lstrip(".") or host.endswith(fake):
            return False
    if host.startswith("noreply") or "noreply@" in val.lower():
        return False
    return True


def _email_at(s: str, a: int, b: int) -> bool:
    if a > 0 and s[a - 1] == ".":
        return False
    if b + 1 < len(s) and s[b] == "." and s[b + 1] in _TOKEN_CH:
        return False
    # Reject URL userinfo (scheme://user:pass@host) while keeping ?to=user@domain.
    if "://" in s[max(0, a - 64) : a]:
        i = a
        while i > 0 and s[i - 1] not in " \t\r\n/?#\"'<>@`":
            i -= 1
        if i >= 3 and s[i - 3 : i] == "://":
            return False
    return True


def _in_url_query(s: str, pos: int) -> bool:
    """True when ``pos`` lies inside the query string of an ``scheme://...`` URL."""
    i = pos
    while i > 0 and s[i - 1] not in " \t\r\n\"'<>()[]{}":
        i -= 1
    token_prefix = s[i:pos]
    return "://" in token_prefix and "?" in token_prefix


def _field_at(s: str, a: int, b: int) -> bool:
    val = s[a:b]
    if not val or val[0] == "=":
        return False
    if _in_url_query(s, a):
        return False
    if _IDENT_PATH_RE.match(val):
        rest = s[b:].lstrip(" \t")
        if rest.startswith("("):
            return False
        quoted_before = a > 0 and s[a - 1] in "\"'"
        if not quoted_before and s[:a].rstrip(" \t").endswith(":"):
            if rest and rest[0] in ";,)}]|?&<>":
                return False
    if _VERSION_RE.match(val) or _STAMP_RE.match(val):
        return False
    if val.startswith(("~/", "/", "./", "../")):
        return False
    name, _, is_field = _field_name_before(s, a)
    if is_field:
        low_name = name.lower()
        if any(
            low_name.endswith(sfx)
            for sfx in ("sort_key", "cache_key", "prompt_cache_key", "public_key")
        ):
            return False
        if "." in name and name.rsplit(".", 1)[1].lower() in _FILE_EXTS:
            return False
    return True


def _cjk_key_at(s: str, a: int, b: int) -> bool:
    val = s[a:b]
    if _VERSION_RE.match(val) or _STAMP_RE.match(val) or val.startswith(("~/", "/")):
        return False
    if _secret_value(val) or (
        any("A" <= c <= "Z" or "a" <= c <= "z" for c in val) and len(val) >= 16
    ):
        return True
    if len(val) >= 6 and val.isdigit():
        before = s[:a].rstrip(" \t\"'“「\\")
        if before.endswith(("为", "為", "是", "：", ":", "=")) and not _followed_by_unit(s, b):
            return True
    return False


def _public_ip_at(s: str, a: int, b: int) -> bool:
    val = s[a:b]
    before = s[max(0, a - 16) : a].lower()
    if "version" in before or "ver." in before or "chrome/" in before or before.endswith("v"):
        if before.rstrip().endswith(("version", "version:", "version=", "ver.", "v")):
            return False
    parts = val.split(".")
    if len(parts) != 4:
        return False
    try:
        o0, o1, o2, o3 = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return False
    if not (0 <= o0 <= 255 and 0 <= o1 <= 255 and 0 <= o2 <= 255 and 0 <= o3 <= 255):
        return False
    # Reject non-global IPv4 ranges matching ipaddress.IPv4Address.is_global.
    if o0 in (0, 10, 127) or o0 >= 224:
        return False
    if o0 == 100 and 64 <= o1 <= 127:
        return False
    if o0 == 169 and o1 == 254:
        return False
    if o0 == 172 and 16 <= o1 <= 31:
        return False
    if o0 == 192:
        if o1 == 168 or (o1 == 0 and o2 in (0, 2)):
            return False
    if o0 == 198 and (o1 in (18, 19) or (o1 == 51 and o2 == 100)):
        return False
    if o0 == 203 and o1 == 0 and o2 == 113:
        return False
    return True


_PATH_PRECEDE_BLOCK = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.~/\\-"
)


def _path_user_unix_at(s: str, a: int, _b: int) -> bool:
    # Check outer match start (before /Users/ or /home/, or before file:// or /mnt/x).
    prefix_start = s.rfind("/", 0, a - 1)
    if prefix_start < 0:
        return False
    # Check for /mnt/<letter> or file:// immediately before /(Users|home)/
    check_pos = prefix_start
    if check_pos >= 6 and s[check_pos - 6 : check_pos - 1] == "/mnt/" and "a" <= s[check_pos - 1] <= "z":
        check_pos -= 6
    if check_pos >= 7 and s[check_pos - 7 : check_pos] == "file://":
        check_pos -= 7
    return check_pos == 0 or s[check_pos - 1] not in _PATH_PRECEDE_BLOCK


def _path_user_win_at(s: str, a: int, _b: int) -> bool:
    # Walk back past 'Users\\' + 'X:\\'
    drive_sep = s.rfind(":", 0, a)
    if drive_sep <= 0:
        return False
    check_pos = drive_sep - 1
    return check_pos == 0 or s[check_pos - 1] not in _ALNUM


def _path_user_ok(val: str) -> bool:
    if not val or val in {"Shared", "Public", "Default", "All Users"}:
        return False
    if val.startswith("u_") and len(val) == 10:
        return False
    return not val.startswith(("$", "%", "<", "{"))


def _digest_shaped(val: str) -> bool:
    n = len(val)
    if not (16 <= n < 32):
        return False
    lower = upper = digits = 0
    for ch in val:
        if "0" <= ch <= "9":
            digits += 1
        elif "a" <= ch <= "f":
            lower += 1
        elif "A" <= ch <= "F":
            upper += 1
        else:
            return False
    letters = lower + upper
    threshold = max(3, n // 8)
    return (lower == 0 or upper == 0) and letters >= threshold and digits >= threshold


def _id_shaped(val: str) -> bool:
    if _UUID_RE.match(val):
        return True
    if len(val) >= 32 and "-" not in val:
        return True
    return _digest_shaped(val)


def _credential_field(s: str, at: int) -> bool:
    name, _, ok = _field_name_before(s, at)
    if not ok:
        return False
    words = _name_words(name)
    if len(words) == 1 and words[0] == "key":
        before = s[max(0, at - 24) : at].lower()
        return any(f"{w} key" in before for w in ("api", "access", "secret", "private"))
    if len(words) < 2:
        return False
    if words[-1] in {"id", "ids", "uuid", "guid"}:
        return False
    return not any(w in _CREDENTIAL_FIELD_REJECT for w in words)


def _id_around(s: str, a: int, b: int) -> tuple[int, int, bool]:
    l, r = a, b
    while l > 0 and (s[l - 1] in "0123456789abcdefABCDEF-"):
        l -= 1
    while r < len(s) and (s[r] in "0123456789abcdefABCDEF-"):
        r += 1
    while l < r and s[l] == "-":
        l += 1
    while r > l and s[r - 1] == "-":
        r -= 1
    if a < l or b > r:
        return 0, 0, False
    sub = s[l:r]
    if not _id_shaped(sub):
        return 0, 0, False
    if _digest_shaped(sub) and (
        (l > 0 and is_ascii_word_char(s[l - 1]))
        or (r < len(s) and is_ascii_word_char(s[r]))
    ):
        return 0, 0, False
    return l, r, True


def in_decimal(s: str, a: int, b: int) -> bool:
    """True if s[a:b] is adjacent to a decimal point and digit (e.g. 5.13800138000)."""
    if a >= 2 and s[a - 1] == "." and s[a].isdigit() and s[a - 2].isdigit():
        return True
    if b + 1 < len(s) and s[b] == "." and s[b - 1].isdigit() and s[b + 1].isdigit():
        return True
    return False


def is_json_bare_number(s: str, a: int, b: int) -> bool:
    """True if s[a:b] is a bare numeric JSON value (e.g. ``"ts": 13800138000``)."""
    sub = s[a:b]
    if not sub or not sub.isdigit():
        return False
    k = b
    while k < len(s) and s[k] in " \t\n\r":
        k += 1
    if k < len(s) and s[k] not in ",}]":
        return False
    i = a - 1
    while i >= 0 and s[i] in " \t\n\r":
        i -= 1
    if i < 0:
        return False
    if s[i] == ":":
        i -= 1
        while i >= 0 and s[i] in " \t\n\r":
            i -= 1
        return i >= 0 and s[i] == '"'
    if s[i] in "[,":
        while i >= 0:
            if s[i] == "[":
                return True
            if s[i] not in "0123456789,.-+eE \t\n\r":
                return False
            i -= 1
    return False


class CandidateSpan(NamedTuple):
    """Internal detected span before overlap resolution."""

    start: int
    end: int
    kind: str
    canonical: str
    raw: str
    by: str
    priority: int
    action: str = "mask"
    pseudonym: str | None = None
    is_id_exempt: bool = False


@dataclass(frozen=True, slots=True)
class BuiltinRule:
    """Precompiled built-in deterministic detector."""

    kind: str
    pattern: re.Pattern[str]
    requires_any: tuple[str, ...] = ()
    markers: tuple[str, ...] = ()
    fold_markers: tuple[str, ...] = ()
    bound: str = ""
    group: int = 0
    window_back: int = 0
    window_ahead: int = 0
    skip_query_prefix: bool = False
    candidate_managed: bool = False
    ok: Callable[[str], bool] | None = None
    at: Callable[[str, int, int], bool] | None = None


@dataclass(frozen=True, slots=True)
class Rule:
    """User-configurable dictionary, custom regex/prefix, or allowlist rule."""

    kind: str = "ORG"
    word: str = ""
    aliases: tuple[str, ...] = ()
    canonical: str = ""
    prefix: str = ""
    pattern: str = ""
    regex: str = ""
    rule_type: str = ""
    requires_any: tuple[str, ...] = ()
    pseudonym: str | None = None
    action: str = "mask"
    match_case: bool = False
    is_active: bool = True

    def __post_init__(self) -> None:
        eff_pat = (self.pattern or self.regex).strip()
        object.__setattr__(self, "kind", normalize_kind(self.kind))
        object.__setattr__(self, "pattern", eff_pat)
        object.__setattr__(self, "regex", eff_pat)
        object.__setattr__(self, "prefix", (self.prefix or "").strip())
        object.__setattr__(self, "word", (self.word or "").strip())
        object.__setattr__(
            self,
            "aliases",
            tuple(a.strip() for a in self.aliases if a and a.strip()),
        )
        if not self.canonical and self.word:
            object.__setattr__(self, "canonical", self.word)
        if not self.rule_type:
            if self.prefix or eff_pat:
                object.__setattr__(self, "rule_type", "regex")
            else:
                object.__setattr__(self, "rule_type", "word")
        if self.action not in {"mask", "warn", "block"}:
            object.__setattr__(self, "action", "mask")


@dataclass(frozen=True, slots=True)
class _DictEntry:
    kind: str
    canonical: str
    action: str
    pseudonym: str | None


@dataclass(frozen=True, slots=True)
class _CompiledCustomRule:
    kind: str
    pattern: re.Pattern[str]
    requires_any: tuple[str, ...]
    markers: tuple[str, ...]
    action: str
    pseudonym: str | None


class UserRules:
    """Compiled bundle of user dictionary terms, custom regex/prefix rules, and allowlist."""

    def __init__(
        self,
        *,
        dict_pattern: re.Pattern[str] | None = None,
        dict_lookup: dict[str, _DictEntry] | None = None,
        dict_icase_lookup: dict[str, _DictEntry] | None = None,
        custom_rules: Sequence[_CompiledCustomRule] = (),
        allowlist: frozenset[str] = frozenset(),
    ) -> None:
        self._dict_pattern = dict_pattern
        self._dict_lookup = dict_lookup or {}
        self._dict_icase_lookup = dict_icase_lookup or {}
        self._custom_rules = tuple(custom_rules)
        self._allowlist = allowlist

    @classmethod
    def from_rules(cls, rules: Iterable[Rule]) -> UserRules:
        """Compile user rules; skip broken custom regexes without failing."""
        term_entries: dict[str, tuple[bool, _DictEntry]] = {}
        custom_compiled: list[_CompiledCustomRule] = []
        allowlist: set[str] = set()

        for r in rules:
            if not r.is_active:
                continue
            if r.rule_type == "allowlist":
                for val in (r.word, r.pattern, *r.aliases):
                    if val:
                        allowlist.add(val.strip().lower())
                continue

            if r.prefix or r.pattern:
                if len(custom_compiled) >= MAX_USER_RULES:
                    logger.warning("desensitize: custom rule limit (%d) reached; skipping %s", MAX_USER_RULES, r.kind)
                    continue
                compiled = _compile_custom_rule(r)
                if compiled is not None:
                    custom_compiled.append(compiled)
                continue

            canon = r.canonical or r.word
            if not canon:
                continue
            entry = _DictEntry(
                kind=r.kind,
                canonical=canon,
                action=r.action,
                pseudonym=r.pseudonym,
            )
            for term in (r.word, *r.aliases):
                if not term:
                    continue
                if term not in term_entries or len(canon) > len(term_entries[term][1].canonical):
                    term_entries[term] = (r.match_case, entry)

        dict_pattern, exact_map, icase_map = _compile_dict_matcher(term_entries)
        return cls(
            dict_pattern=dict_pattern,
            dict_lookup=exact_map,
            dict_icase_lookup=icase_map,
            custom_rules=custom_compiled,
            allowlist=frozenset(allowlist),
        )

    @property
    def allowlist(self) -> frozenset[str]:
        return self._allowlist

    def find_dict_spans(self, text: str) -> list[CandidateSpan]:
        if self._dict_pattern is None or not text:
            return []
        spans: list[CandidateSpan] = []
        exact_get = self._dict_lookup.get
        icase_get = self._dict_icase_lookup.get
        has_icase = bool(self._dict_icase_lookup)
        for m in self._dict_pattern.finditer(text):
            raw = m.group(0)
            entry = exact_get(raw)
            if entry is None and has_icase:
                entry = icase_get(raw.lower())
            if entry is None:
                continue
            a, b = m.span()
            spans.append(
                CandidateSpan(
                    start=a,
                    end=b,
                    kind=entry.kind,
                    canonical=entry.canonical,
                    raw=raw,
                    by="dict",
                    priority=1,
                    action=entry.action,
                    pseudonym=entry.pseudonym,
                    is_id_exempt=True,
                )
            )
        return spans

    def find_custom_spans(self, text: str) -> list[CandidateSpan]:
        if not self._custom_rules or not text:
            return []
        spans: list[CandidateSpan] = []
        for rule in self._custom_rules:
            if rule.requires_any and not any(m in text for m in rule.requires_any):
                continue
            if rule.markers and not any(m in text for m in rule.markers):
                continue
            for m in rule.pattern.finditer(text):
                grp = 1 if m.lastindex and m.group(1) is not None else 0
                a, b = m.span(grp)
                if b <= a or (b - a) < 4:
                    continue
                val = text[a:b]
                spans.append(
                    CandidateSpan(
                        start=a,
                        end=b,
                        kind=rule.kind,
                        canonical=val,
                        raw=val,
                        by="custom",
                        priority=2,
                        action=rule.action,
                        pseudonym=rule.pseudonym,
                        is_id_exempt=True,
                    )
                )
        return spans


def _compile_custom_rule(r: Rule) -> _CompiledCustomRule | None:
    if r.prefix and r.pattern:
        logger.warning("desensitize: rule %s cannot specify both prefix and pattern", r.kind)
        return None
    markers: tuple[str, ...] = ()
    if r.prefix:
        if not (MIN_CUSTOM_PREFIX_LEN <= len(r.prefix) <= MAX_CUSTOM_PREFIX_LEN):
            logger.warning("desensitize: invalid prefix length for %s", r.kind)
            return None
        pat_str = re.escape(r.prefix) + r"[A-Za-z0-9_\-]{8,}"
        markers = (r.prefix,)
    else:
        if len(r.pattern) > MAX_CUSTOM_REGEX_LEN:
            logger.warning("desensitize: regex too long for %s", r.kind)
            return None
        pat_str = r.pattern
    try:
        flags = 0 if r.match_case else 0
        compiled = re.compile(pat_str, flags)
    except re.error as exc:
        logger.warning("desensitize: failed to compile custom rule %s: %s", r.kind, exc)
        return None
    if compiled.match("") is not None:
        logger.warning("desensitize: rejecting custom rule %s that matches empty string", r.kind)
        return None
    return _CompiledCustomRule(
        kind=r.kind,
        pattern=compiled,
        requires_any=r.requires_any,
        markers=markers,
        action=r.action,
        pseudonym=r.pseudonym,
    )


def _compile_dict_matcher(
    term_entries: dict[str, tuple[bool, _DictEntry]],
) -> tuple[re.Pattern[str] | None, dict[str, _DictEntry], dict[str, _DictEntry]]:
    if not term_entries:
        return None, {}, {}
    ordered = sorted(term_entries.keys(), key=lambda t: (-len(t), t))
    parts: list[str] = []
    exact_map: dict[str, _DictEntry] = {}
    icase_map: dict[str, _DictEntry] = {}

    for term in ordered:
        match_case, entry = term_entries[term]
        exact_map[term] = entry
        if not match_case:
            icase_map[term.lower()] = entry
        escaped = re.escape(term)
        if not match_case and any("A" <= c <= "Z" or "a" <= c <= "z" for c in term):
            core = f"(?i:{escaped})"
        else:
            core = escaped
        if is_pure_ascii(term):
            left = r"(?<![A-Za-z0-9_])" if is_ascii_word_char(term[0]) else ""
            right = r"(?![A-Za-z0-9_])" if is_ascii_word_char(term[-1]) else ""
            parts.append(f"{left}{core}{right}")
        else:
            parts.append(core)

    return re.compile("|".join(parts)), exact_map, icase_map


def _build_builtin_rules() -> tuple[BuiltinRule, ...]:
    return (
        BuiltinRule(
            kind="PRIVATE_KEY",
            pattern=re.compile(
                r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----[\s\S]+?-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
            ),
            requires_any=("PRIVATE KEY",),
            markers=("PRIVATE KEY",),
        ),
        BuiltinRule(
            kind="API_KEY",
            pattern=re.compile(r"sk-(?:ant-|proj-|or-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}"),
            requires_any=("sk-",),
            markers=("sk-",),
            bound=_TOKEN_CH,
            ok=_not_a_slug,
        ),
        BuiltinRule(
            kind="API_KEY",
            pattern=re.compile(r"[a-z]{2,4}_sk_[A-Za-z0-9_-]{20,}"),
            requires_any=("_sk_",),
            markers=("_sk_",),
            bound=_TOKEN_CH,
            ok=_secret_value,
        ),
        BuiltinRule(
            kind="API_KEY",
            pattern=re.compile(r"ark-[A-Za-z0-9][A-Za-z0-9_-]{15,}"),
            requires_any=("ark-",),
            markers=("ark-",),
            bound=_TOKEN_CH,
            ok=_secret_value,
        ),
        BuiltinRule(
            kind="API_KEY",
            pattern=re.compile(
                r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|glpat-[A-Za-z0-9_-]{20,})"
            ),
            requires_any=("gh", "github_pat_", "glpat-"),
            markers=("gh", "github_pat_", "glpat-"),
            bound=_TOKEN_CH,
        ),
        BuiltinRule(
            kind="API_KEY",
            pattern=re.compile(r"AIza[0-9A-Za-z_-]{35}"),
            requires_any=("AIza",),
            markers=("AIza",),
            bound=_TOKEN_CH,
        ),
        BuiltinRule(
            kind="API_KEY",
            pattern=re.compile(r"xox[abposr]-[0-9A-Za-z-]{10,}"),
            requires_any=("xox",),
            markers=("xox",),
            bound=_TOKEN_CH,
        ),
        BuiltinRule(
            kind="API_KEY",
            pattern=re.compile(r"(?:sk|rk)_live_[0-9A-Za-z]{20,}"),
            requires_any=("_live_",),
            markers=("_live_",),
            bound=_TOKEN_CH,
        ),
        BuiltinRule(
            kind="API_KEY",
            pattern=re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}"),
            requires_any=("AKIA", "ASIA"),
            markers=("AKIA", "ASIA"),
            bound=_ALNUM,
        ),
        BuiltinRule(
            kind="API_KEY",
            pattern=re.compile(
                r"(?:hf_[A-Za-z0-9]{30,}|gsk_[A-Za-z0-9]{40,}|xai-[A-Za-z0-9]{40,}|npm_[A-Za-z0-9]{36}|pypi-[A-Za-z0-9_-]{50,}|dop_v1_[a-f0-9]{64}|SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43})"
            ),
            requires_any=("hf_", "gsk_", "xai-", "npm_", "pypi-", "dop_v1_", "SG."),
            markers=("hf_", "gsk_", "xai-", "npm_", "pypi-", "dop_v1_", "SG."),
            bound=_TOKEN_CH,
        ),
        BuiltinRule(
            kind="JWT",
            pattern=re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
            requires_any=("eyJ",),
            markers=("eyJ",),
            bound=_TOKEN_CH,
        ),
        BuiltinRule(
            kind="BEARER",
            pattern=re.compile(r"(?i)\bBearer[ \t]+([A-Za-z0-9._~+/=-]{8,})"),
            requires_any=("bearer", "Bearer", "BEARER"),
            fold_markers=("bearer",),
            group=1,
            bound=_TOKEN_CH,
            ok=_not_a_variable,
        ),
        BuiltinRule(
            kind="PASSWORD",
            pattern=re.compile(
                r"""(?i)(?:password|passwd|passphrase)[A-Za-z0-9_.-]*["']?[ \t]*[:=][ \t]*["']?("""
                + _SECRET_CH
                + r"{8,})"
            ),
            requires_any=(":", "="),
            fold_markers=("pass",),
            group=1,
            window_back=0,
            window_ahead=160,
            skip_query_prefix=True,
            ok=_secret_value,
            at=_field_at,
        ),
        BuiltinRule(
            kind="SECRET",
            pattern=re.compile(
                r"""(?i)(?:secret|token|api[_-]?key|access[_-]?key|private[_-]?key|priv[_-]?key|credential)[A-Za-z0-9_.-]*["']?[ \t]*[:=][ \t]*["']?("""
                + _SECRET_CH
                + r"{8,})"
            ),
            requires_any=(":", "="),
            fold_markers=("secret", "token", "key", "credential"),
            group=1,
            window_back=0,
            window_ahead=160,
            skip_query_prefix=True,
            ok=_secret_value,
            at=_field_at,
        ),
        BuiltinRule(
            kind="PASSWORD",
            pattern=re.compile(
                r"""(?:密码|密碼|口令|PIN码|PIN碼|安全码)[ \t]*(?:[:：=][ \t]*|[为為是][ \t]*[:：]?[ \t]*)?["'“「]?("""
                + _SECRET_CH
                + r"{4,64})"
            ),
            requires_any=("密码", "密碼", "口令", "PIN码", "PIN碼", "安全码"),
            markers=("密码", "密碼", "口令", "PIN码", "PIN碼", "安全码"),
            group=1,
            window_back=0,
            window_ahead=96,
            ok=_not_a_variable,
        ),
        BuiltinRule(
            kind="SECRET",
            pattern=re.compile(
                r"""(?:密钥|密鑰|秘钥|秘鑰|私钥|私鑰|金钥|令牌|授权码|授權碼)[ \t]*(?:[:：=][ \t]*|[为為是][ \t]*[:：]?[ \t]*)?["'“「]?("""
                + _SECRET_CH
                + r"{4,64})"
            ),
            requires_any=("密钥", "密鑰", "秘钥", "秘鑰", "私钥", "私鑰", "金钥", "令牌", "授权码", "授權碼"),
            markers=("密钥", "密鑰", "秘钥", "秘鑰", "私钥", "私鑰", "金钥", "令牌", "授权码", "授權碼"),
            group=1,
            window_back=0,
            window_ahead=96,
            at=_cjk_key_at,
        ),
        BuiltinRule(
            kind="EMAIL",
            pattern=re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
            requires_any=("@",),
            markers=("@",),
            bound=_ALNUM + "_%+-",
            candidate_managed=True,
            ok=_real_email,
            at=_email_at,
        ),
        BuiltinRule(
            kind="ID_CARD",
            pattern=re.compile(
                r"[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]"
            ),
            bound=_ALNUM,
            candidate_managed=True,
            ok=chinese_id_checksum,
        ),
        BuiltinRule(
            kind="PHONE",
            pattern=re.compile(r"1[3-9]\d{9}"),
            bound=_ALNUM,
            candidate_managed=True,
            at=_phone_at,
        ),
        BuiltinRule(
            kind="BANK_CARD",
            pattern=re.compile(r"[3-6]\d{3}(?:[ -]?\d{4}){2,3}(?:[ -]?\d{1,3})?"),
            bound=_ALNUM,
            candidate_managed=True,
            ok=luhn_checksum,
            at=_bank_card_at,
        ),
        BuiltinRule(
            kind="PATH_USER",
            pattern=re.compile(
                r"""/(?:Users|home)/([^\s/\\:*?"'<>|,;()\[\]{}`$%]{1,64})"""
            ),
            requires_any=("/Users/", "/home/"),
            markers=("/Users/", "/home/"),
            group=1,
            candidate_managed=True,
            ok=_path_user_ok,
            at=_path_user_unix_at,
        ),
        BuiltinRule(
            kind="PATH_USER",
            pattern=re.compile(
                r"""[A-Za-z]:(?:\\{1,4}|/)(?i:users)(?:\\{1,4}|/)([^\s/\\:*?"'<>|,;()\[\]{}`$%]{1,64})"""
            ),
            requires_any=(":\\Users", ":\\users", ":/Users", ":/users", ":\\USERS", ":/USERS"),
            markers=(":\\Users", ":\\users", ":/Users", ":/users", ":\\USERS", ":/USERS"),
            group=1,
            ok=_path_user_ok,
            at=_path_user_win_at,
        ),
        BuiltinRule(
            kind="INTERNAL_HOST",
            pattern=re.compile(
                r"\b[A-Za-z0-9][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)*\.(?:internal|local|corp|lan|intranet|localdomain)\b"
            ),
            requires_any=(".",),
            fold_markers=(".internal", ".local", ".corp", ".lan", ".intranet"),
            bound=_ALNUM + "-",
            window_back=80,
            window_ahead=24,
        ),
        BuiltinRule(
            kind="PUBLIC_IP",
            pattern=re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
            requires_any=(".",),
            markers=(".",),
            bound=_ALNUM + ".",
            candidate_managed=True,
            at=_public_ip_at,
        ),
    )


_BUILTIN_RULES: tuple[BuiltinRule, ...] = _build_builtin_rules()
_NON_CANDIDATE_RULES: tuple[BuiltinRule, ...] = tuple(
    r for r in _BUILTIN_RULES if not r.candidate_managed
)
_DIRECT_TOKEN_RULES: tuple[BuiltinRule, ...] = tuple(
    r for r in _NON_CANDIDATE_RULES if r.markers and not r.fold_markers and r.window_ahead == 0
)
_DIRECT_TOKEN_PRESCREEN_RE = re.compile(
    "|".join(re.escape(m) for r in _DIRECT_TOKEN_RULES for m in r.markers)
)
_WINDOW_OR_FOLD_RULES: tuple[BuiltinRule, ...] = tuple(
    r for r in _NON_CANDIDATE_RULES if r.fold_markers or r.window_ahead > 0
)

_CANDIDATE_RE = re.compile(
    r"(?P<IP>\b\d{1,3}(?:\.\d{1,3}){3}\b)"
    r"|(?P<DIGITS>[1-9]\d{10,18}[\dXx]?|[3-6]\d{3}(?:[ -]\d{4}){2,3}(?:[ -]\d{1,3})?)"
    r"|(?P<PATH_U>/(?:Users|home)/[^\s/\\:*?\"'<>|,;()\[\]{}`$%]{1,64})"
    r"|(?P<EMAIL>[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})"
    r"|(?P<URL>[A-Za-z][A-Za-z0-9+.-]{1,15}://[^\s<>\"'`\)\]}]+)"
)
_ID_CARD_FULL_RE = re.compile(
    r"[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]"
)
_BANK_CARD_FULL_RE = re.compile(
    r"[3-6]\d{3}(?:[ -]?\d{4}){2,3}(?:[ -]?\d{1,3})?"
)
_EMAIL_INNER_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
)
_EMAIL_BOUND = _ALNUM + "_%+-"
_IP_BOUND = _ALNUM + "."


def builtin_rules() -> tuple[BuiltinRule, ...]:
    """Return immutable tuple of built-in deterministic rules."""
    return _BUILTIN_RULES


def non_candidate_rule_groups() -> (
    tuple[tuple[BuiltinRule, ...], re.Pattern[str], tuple[BuiltinRule, ...]]
):
    """Return declarative non-candidate rule groups and combined marker prescreen."""
    return _DIRECT_TOKEN_RULES, _DIRECT_TOKEN_PRESCREEN_RE, _WINDOW_OR_FOLD_RULES


def _extract_url_slice_spans(
    text: str,
    url_start: int,
    url_end: int,
    *,
    allow_password: bool,
    allow_url_token: bool,
    allow_email: bool,
) -> list[CandidateSpan]:
    url = text[url_start:url_end]
    spans: list[CandidateSpan] = []

    scheme_idx = url.find("://")
    auth_start = scheme_idx + 3
    path_cut = len(url)
    for sep in ("/", "?", "#"):
        pos = url.find(sep, auth_start)
        if pos != -1 and pos < path_cut:
            path_cut = pos
    if allow_password:
        at_idx = url.find("@", auth_start, path_cut)
        if at_idx != -1:
            colon_idx = url.find(":", auth_start, at_idx)
            if colon_idx != -1 and at_idx - (colon_idx + 1) >= 3:
                user = url[auth_start:colon_idx]
                pw = url[colon_idx + 1 : at_idx]
                rest_url = url[at_idx + 1 :]
                rest_parts = re.split(r"[:/?#&=;,@.]", rest_url)
                if _not_a_variable(pw) and pw != user and pw not in rest_parts:
                    spans.append(
                        CandidateSpan(
                            start=url_start + colon_idx + 1,
                            end=url_start + at_idx,
                            kind="PASSWORD",
                            canonical=pw,
                            raw=pw,
                            by="rule",
                            priority=3,
                        )
                    )

    q_idx = url.find("?")
    if q_idx != -1 and allow_url_token:
        frag_idx = url.find("#", q_idx + 1)
        q_end = frag_idx if frag_idx != -1 else len(url)
        pos = q_idx + 1
        while pos < q_end:
            amp = url.find("&", pos, q_end)
            seg_end = amp if amp != -1 else q_end
            eq = url.find("=", pos, seg_end)
            if eq != -1 and eq + 1 < seg_end:
                raw_name = url[pos:eq]
                if "%" in raw_name:
                    raw_name = unquote(raw_name)
                name = raw_name.strip().lower().replace("-", "_")
                val_start = url_start + eq + 1
                val_end = url_start + seg_end
                val = text[val_start:val_end]
                if (
                    val
                    and val != "…"
                    and not val.startswith("{{")
                    and (name in _TOKEN_PARAM_NAMES or _JWT_RE.match(val) is not None)
                ):
                    spans.append(
                        CandidateSpan(
                            start=val_start,
                            end=val_end,
                            kind="URL_TOKEN",
                            canonical=val,
                            raw=val,
                            by="rule",
                            priority=3,
                        )
                    )
            pos = seg_end + 1

    # Also detect non-userinfo emails inside URL path/query (e.g. ?to=bob@acme.cn).
    if allow_email and path_cut < len(url) and "@" in url[path_cut:]:
        text_len = len(text)
        for em in _EMAIL_INNER_RE.finditer(text, url_start + path_cut, url_end):
            ea, eb = em.span()
            if (ea > 0 and text[ea - 1] in _EMAIL_BOUND) or (
                eb < text_len and text[eb] in _EMAIL_BOUND
            ):
                continue
            ev = text[ea:eb]
            if _real_email(ev) and _email_at(text, ea, eb):
                spans.append(
                    CandidateSpan(
                        start=ea,
                        end=eb,
                        kind="EMAIL",
                        canonical=ev,
                        raw=ev,
                        by="rule",
                        priority=3,
                    )
                )
    return spans


def scan_candidate_spans(
    text: str,
    *,
    allow_url_token: bool,
    allow_password: bool,
    allow_email: bool,
    allow_path_user: bool,
    allow_ip: bool,
    allow_id_card: bool,
    allow_phone: bool,
    allow_bank_card: bool,
) -> list[CandidateSpan]:
    """Single-pass candidate scan for URLs, emails, Unix home paths, IPs, and digit PII."""
    spans: list[CandidateSpan] = []
    text_len = len(text)
    for m in _CANDIDATE_RE.finditer(text):
        grp = m.lastgroup
        a, b = m.span()
        if grp == "DIGITS":
            val = text[a:b]
            n = b - a
            if allow_id_card and n == 18 and _ID_CARD_FULL_RE.fullmatch(val):
                if (
                    (a == 0 or text[a - 1] not in _ALNUM)
                    and (b == text_len or text[b] not in _ALNUM)
                    and chinese_id_checksum(val)
                ):
                    spans.append(CandidateSpan(a, b, "ID_CARD", val, val, "rule", 3))
                    continue
            if allow_bank_card and 15 <= n <= 23 and _BANK_CARD_FULL_RE.fullmatch(val):
                if (
                    (a == 0 or text[a - 1] not in _ALNUM)
                    and (b == text_len or text[b] not in _ALNUM)
                    and luhn_checksum(val)
                    and _bank_card_at(text, a, b)
                ):
                    spans.append(CandidateSpan(a, b, "BANK_CARD", val, val, "rule", 3))
                    continue
            if allow_phone:
                # Handle plain 11-digit phone, +86/86 separated prefix, or 8613... 13-digit run.
                pa, pb = a, b
                if n == 11 and val[0] == "1" and "3" <= val[1] <= "9":
                    if pa >= 4 and text[pa - 4 : pa] in ("+86 ", "+86-"):
                        pa -= 4
                    elif pa >= 3 and text[pa - 3 : pa] in ("+86", "86 ", "86-"):
                        pa -= 3
                    elif pa >= 2 and text[pa - 2 : pa] == "86":
                        pa -= 2
                elif n == 13 and val[:2] == "86" and val[2] == "1" and "3" <= val[3] <= "9":
                    pass
                else:
                    continue
                if (
                    (pa == 0 or text[pa - 1] not in _ALNUM)
                    and (pb == text_len or text[pb] not in _ALNUM)
                    and _phone_at(text, pa, pb)
                ):
                    pval = text[pa:pb]
                    spans.append(CandidateSpan(pa, pb, "PHONE", pval, pval, "rule", 3))
            continue

        if grp == "EMAIL":
            if not allow_email:
                continue
            if (a > 0 and text[a - 1] in _EMAIL_BOUND) or (
                b < text_len and text[b] in _EMAIL_BOUND
            ):
                continue
            val = text[a:b]
            if _real_email(val) and _email_at(text, a, b):
                spans.append(CandidateSpan(a, b, "EMAIL", val, val, "rule", 3))
            continue

        if grp == "IP":
            if not allow_ip:
                continue
            if (a > 0 and text[a - 1] in _IP_BOUND) or (
                b < text_len and text[b] in _IP_BOUND
            ):
                continue
            if _public_ip_at(text, a, b):
                val = text[a:b]
                spans.append(CandidateSpan(a, b, "PUBLIC_IP", val, val, "rule", 3))
            continue

        if grp == "PATH_U":
            if not allow_path_user:
                continue
            # Trim '/Users/' (7) or '/home/' (6).
            ua = a + (7 if text[a + 1] == "U" else 6)
            uval = text[ua:b]
            if _path_user_ok(uval) and _path_user_unix_at(text, ua, b):
                spans.append(CandidateSpan(ua, b, "PATH_USER", uval, uval, "rule", 3))
            continue

        if grp == "URL":
            spans.extend(
                _extract_url_slice_spans(
                    text,
                    a,
                    b,
                    allow_password=allow_password,
                    allow_url_token=allow_url_token,
                    allow_email=allow_email,
                )
            )
    return spans


def find_url_spans(text: str) -> list[CandidateSpan]:
    """Extract only credential query-param values and userinfo passwords inside URLs."""
    if "://" not in text:
        return []
    spans: list[CandidateSpan] = []
    for m in _URL_RE.finditer(text):
        spans.extend(
            _extract_url_slice_spans(
                text,
                m.start(),
                m.end(),
                allow_password=True,
                allow_url_token=True,
                allow_email=False,
            )
        )
    return spans


_ID_SKIP_KINDS = frozenset({"EMAIL", "PATH_USER", "PUBLIC_IP", "INTERNAL_HOST", "URL_TOKEN"})
_HEX_OR_HYPHEN = frozenset("0123456789abcdefABCDEF-")


def filter_id_and_number_false_positives(
    text: str,
    spans: list[CandidateSpan],
) -> list[CandidateSpan]:
    """Drop partial ID slices, non-credential hex IDs, and decimal/JSON numbers."""
    out: list[CandidateSpan] = []
    text_len = len(text)
    for sp in spans:
        if sp.is_id_exempt:
            out.append(sp)
            continue

        # Anti-false-positive on decimal fractions & bare JSON numbers for PII digits.
        if sp.kind in {"PHONE", "BANK_CARD", "ID_CARD"}:
            if in_decimal(text, sp.start, sp.end) or is_json_bare_number(text, sp.start, sp.end):
                continue
        elif sp.kind in _ID_SKIP_KINDS:
            out.append(sp)
            continue

        # Skip _id_around unless adjacent to hex/hyphen or candidate itself is >=16 hex-like.
        has_edge_hex = (sp.start > 0 and text[sp.start - 1] in _HEX_OR_HYPHEN) or (
            sp.end < text_len and text[sp.end] in _HEX_OR_HYPHEN
        )
        if not has_edge_hex and (sp.end - sp.start) < 16:
            out.append(sp)
            continue

        # Anti-ID truncation (UUIDs, 16-31 hex digests like articles.id, 32+ hex).
        l, r, is_id = _id_around(text, sp.start, sp.end)
        if is_id:
            if sp.start > l or sp.end < r:
                continue
            if sp.kind in {"API_KEY", "PRIVATE_KEY", "URL_TOKEN", "JWT", "BEARER", "PASSWORD"}:
                out.append(sp)
                continue
            if sp.kind == "SECRET" and _credential_field(text, l):
                out.append(sp)
                continue
            continue

        out.append(sp)
    return out
