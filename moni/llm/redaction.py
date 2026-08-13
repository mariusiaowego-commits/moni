"""LLM 输出脱敏 — v1.0.28.

6 模式正则覆盖 LLM 常见敏感凭据, 替换为 [REDACTED:type] 占位符.
在 B5 _log_failure 写库前调用, 避免凭据落库.

Spec: docs/specs/2026-08-13-evidence-redaction-v1.0.28.md
"""

from __future__ import annotations

import re

# 6 模式: api_key / token / bearer / secret / password / uuid_token
# Python re 不支持可变长 lookbehind, 用 capture group + re.sub lambda 替换 value

# 模式 1. sk-xxx (OpenAI / DeepSeek / Anthropic 风格 API key, 20-80 字符)
SK_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{20,80}\b")

# 模式 2/4/5. key=value / "key":"value" / Key: value
#   capture group 拆 prefix 和 value, prefix 含闭合引号
KEY_VALUE_PATTERN = re.compile(
    r"""(?ix)
    (?P<prefix>["']?              # 可选左引号
       (?:token|secret|password)  # key 名
       ["']?\s*[:=]\s*             # 可选右引号 + 冒号/等号 + 空白
    )                             # prefix 不含 value 前引号 (修复 27 vs 26 chars 错位)
    (?P<q>["']?)                  # value 前引号 (独立 group)
    (?P<value>[A-Za-z0-9_.-]{8,100}|[^\s,;"']{8,100})  # value (token/secret/password 一致 8+ 字符, 避免 4-7 chars 短 token 误判)
    \b
    """
)

# 模式 3. Bearer xxx: capture "Bearer " + value
BEARER_PATTERN = re.compile(r"(?i)(Bearer\s+)([A-Za-z0-9_.-]{8,200})")

# 模式 6. UUID 风格 token (8-4-4-4-12)
UUID_PATTERN = re.compile(
    r"\b[A-Za-z0-9]{8}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{12}\b"
)


def redact_text(text: str) -> str:
    """Replace sensitive patterns with [REDACTED:type] markers.

    v1.0.28: 在 B5 _log_failure 写库前调用, 避免 LLM 凭据 (api_key / token / bearer / secret / password / uuid_token) 落库.
    保留诊断价值 (脱敏后文本仍可读, 只是 [REDACTED:type] 占位) 同时避免凭据落库.

    Args:
        text: 原始 LLM 输出文本 (可能是 RAW OUTPUT 截断前 / 落库后 / 任何 LLM 响应).

    Returns:
        脱敏后文本, 敏感模式替换为 [REDACTED:type].
        如果 text 为空 (None / ""), 返 text (不抛异常).
    """
    if not text:
        return text

    # 模式 1: sk-xxx 整段替换 → [REDACTED:api_key]
    redacted = SK_PATTERN.sub("[REDACTED:api_key]", text)
    # 模式 6: UUID 风格 token 整段替换 (先做, 避免值里的 [REDACTED:uuid] 二次匹配)
    redacted = UUID_PATTERN.sub("[REDACTED:uuid_token]", redacted)
    # 模式 3: Bearer 保留 "Bearer ", 替换 value → "Bearer [REDACTED:bearer]"
    redacted = BEARER_PATTERN.sub(lambda m: m.group(1) + "[REDACTED:bearer]", redacted)
    # 模式 2/4/5: key=value 保留 prefix, 替换 value
    # 关键: 先把整个 prefix+value 整段匹配, 然后用 lambda 返 prefix + [REDACTED:type]
    # 避免被模式 2 二次匹配: value 已经被替换, 不会含原 value
    def _key_value_replace(m: re.Match) -> str:
        prefix = m.group(1)
        q = m.group("q")
        key_name = prefix.strip("\"'").rstrip(":=").strip().lower()
        return f"{prefix}{q}[REDACTED:{key_name}]{m.group(0)[-1] if m.group(0)[-1] == chr(34) else ''}"
    redacted = KEY_VALUE_PATTERN.sub(_key_value_replace, redacted)
    return redacted
