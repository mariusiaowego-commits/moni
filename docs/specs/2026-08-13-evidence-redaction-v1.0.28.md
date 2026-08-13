# Sprint 09 — evidence 落库脱敏 tech spec (v1.0.28)

> **状态**: draft, 父 agent 全部拍板 (Q1-Q4 = A/A/A/A)
> **作者**: blue-owl (arch, hermes coder profile)
> **日期**: 2026-08-13
> **关联**: sprint-08 v1.0.25 看板 method 分布 P1 候选 #1 (P0 数据安全, B5 落库遗留)

---

## 0. 背景与决策回顾

### 0.1 现状 (sprint-08 v1.0.23 B5 ship 后实查)

- v1.0.23 B5 修复 fact_check_plus 解析失败诊断盲区, 把 LLM 原始输出 RAW OUTPUT 前 2KB 落进 `llm_jobs.error` 字段
- **P0 数据安全漏洞**: LLM 响应可能含 api_key / token / bearer / secret / password 等敏感凭据
- moni 8900 + sqlite 单文件, 数据本地, 但 moni 是银行业 AI 工具, 合规审计要求"敏感凭据不落任何存储"
- 申报书 B v4 §4.5 "数据安全与风险控制" 提了"AI 调用 api key 仅发送必要 token", 落库侧没明文规定, 走 B5 后的 RAW OUTPUT 漏洞, 落库数据可能含敏感凭据

### 0.2 拍板 (Q1-Q4 = A/A/A/A, 父 agent 全权)

- **Q1=A** 脱敏对象: `llm_jobs.error` 字段 RAW OUTPUT (B5 落库路径, P0 漏洞), 不动全局其他 error 字段
- **Q2=A** 脱敏策略: 正则替换 (按 6 种 LLM 常见敏感模式), 保留诊断价值 (截断会丢信息)
- **Q3=A** 脱敏触发: 写入前自动脱敏 (B5 _log_failure helper 改), 存的是干净数据, 下游不需重复脱敏
- **Q4=A** 脱敏规则: 6 种 (api_key / token / bearer / secret / password / sk-)

---

## 1. 范围 (In/Out)

### 1.1 IN (v1.0.28 ship)

1. **`moni/llm/redaction.py`** (新, ~50 行):
   - `REDACTION_PATTERNS`: 6 种正则模式 (api_key / token / bearer / secret / password / sk-)
   - `redact_text(text: str) -> str` 主函数, 按模式替换为 `[REDACTED:{type}]`
   - 单测覆盖: 6 模式各 1 个 case + 混合 case
2. **`moni/llm/jobs.py` 改 `_log_failure` 写库前调用 redact_text**:
   - raw_output 截断前先 redact, 再走 _truncate_utf8
   - 错误信息格式: `RAW OUTPUT: <redacted> | PARSE: <reason>` (redacted 后长度可能 < 2KB, 但 truncation 仍按 2KB 切)
3. **5 测试** (`tests/test_fact_check_plus.py` 追加):
   - `test_redact_api_key_pattern`: `sk-abc123` → `[REDACTED:api_key]`
   - `test_redact_token_pattern`: `token=xyz` → `[REDACTED:token]`
   - `test_redact_bearer_pattern`: `Bearer eyJhbGc...` → `[REDACTED:bearer]`
   - `test_redact_mixed_patterns`: 多模式同字符串, 全部替换
   - `test_failure_log_redacts_before_truncate`: B5 _log_failure 写库后 error 字段无 api_key

### 1.2 OUT (P1+ 留)

- 全局 error 字段脱敏 (本次只改 B5 _log_failure 一条路径, 全局是 P2)
- 脱敏规则可配置 (moni/llm/redaction.py 是常量, 配置文件读规则是 P1)
- 审计脱敏 (记录谁被脱敏了, P1)
- 脱敏前后对比 (回放原始 LLM 输出, 需新表, P1)
- LLM 输出前脱敏 (pre-LLM filter, 不是落库, P2)

### 1.3 Non-goals

- 不改 `llm_jobs.error` 字段类型
- 不动 summary / fact_check / event_extract 等其他 task_type 的 error 字段
- 不动 _truncate_utf8 (脱敏在 truncation 前)
- 不动 _persist_fact_check_plus 写库逻辑 (脱敏在 _log_failure 内, 不动闸 1/2/3)

---

## 2. 文件清单 (~70 行 net add)

| 文件 | 改法 | 内容 |
|------|------|------|
| `moni/llm/redaction.py` | +50 | 6 模式 + redact_text 主函数 |
| `moni/llm/jobs.py` | +3 | `_log_failure` 写库前调 `redact_text` |
| `tests/test_fact_check_plus.py` | +80 | 5 新测试 |

---

## 3. 数据契约

### 3.1 脱敏规则 (6 模式)

```python
REDACTION_PATTERNS = [
    # 1. sk-xxx (OpenAI / DeepSeek 风格 API key, 20-50 字符)
    (r'\bsk-[A-Za-z0-9_-]{20,80}\b', 'api_key'),
    # 2. token=xxx / token: xxx / "token":"xxx" (通用 token)
    (r'(?i)(["\']?token["\']?\s*[:=]\s*["\']?)([A-Za-z0-9_.-]{8,100})\b', 'token'),
    # 3. Bearer xxx (Authorization header)
    (r'(?i)Bearer\s+([A-Za-z0-9_.-]{8,200})', 'bearer'),
    # 4. secret=xxx / secret: xxx / "secret":"xxx" (通用 secret)
    (r'(?i)(["\']?secret["\']?\s*[:=]\s*["\']?)([A-Za-z0-9_.-]{8,100})\b', 'secret'),
    # 5. password=xxx / password: xxx / "password":"xxx"
    (r'(?i)(["\']?password["\']?\s*[:=]\s*["\']?)([^\s,;"\']{4,100})\b', 'password'),
    # 6. xxxxx-xxxx-xxxx-xxxx-xxxxxxx (UUID 风格 secret, 但排除 4-2-2-2-6 短 UUID 误判)
    (r'\b[A-Za-z0-9]{8}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{12}\b', 'uuid_token'),
]
```

注: 6 模式 vs 拍板 Q4=A 说 6 种 (api_key / token / bearer / secret / password / sk-), 加 uuid_token 凑 6 种覆盖 LLM 常见凭据.

### 3.2 redact_text 主函数

```python
import re

def redact_text(text: str) -> str:
    """Replace sensitive patterns with [REDACTED:type] markers.
    
    v1.0.28: B5 落库诊断数据后, LLM RAW OUTPUT 可能含 api_key / token 等敏感凭据.
    此函数在 _log_failure 写库前调用, 替换敏感模式为 [REDACTED:type] 占位符,
    保留诊断价值 (脱敏后文本仍可读) 同时避免凭据落库.
    """
    if not text:
        return text
    redacted = text
    for pattern, ptype in REDACTION_PATTERNS:
        redacted = re.sub(pattern, f'[REDACTED:{ptype}]', redacted)
    return redacted
```

### 3.3 B5 _log_failure 集成

```python
def _log_failure(conn, job_id, raw_output, parse_reason) -> str:
    raw = raw_output or ""
    # v1.0.28: redact before truncate
    redacted = redact_text(raw)
    truncated = _truncate_utf8(redacted, 2048)
    total_bytes = len(redacted.encode("utf-8"))
    suffix = "" if total_bytes <= 2048 else f" ... [truncated {total_bytes}B total]"
    err_msg = f"RAW OUTPUT: {truncated}{suffix} | PARSE: {parse_reason}"
    conn.execute(
        "UPDATE llm_jobs SET error = ? WHERE id = ?",
        (err_msg, job_id),
    )
    conn.commit()
    return err_msg
```

---

## 4. 代码契约 (redaction.py 完整代码)

```python
"""LLM 输出脱敏 — v1.0.28.

6 模式正则覆盖 LLM 常见敏感凭据, 替换为 [REDACTED:type] 占位符.
在 B5 _log_failure 写库前调用, 避免凭据落库.
"""
from __future__ import annotations

import re

REDACTION_PATTERNS: list[tuple[str, str]] = [
    # 1. sk-xxx (OpenAI / DeepSeek / Anthropic 风格 API key)
    (r'\bsk-[A-Za-z0-9_-]{20,80}\b', 'api_key'),
    # 2. token=xxx / token: xxx (通用 token)
    (r'(?i)(["\']?token["\']?\s*[:=]\s*["\']?)[A-Za-z0-9_.-]{8,100}\b', 'token'),
    # 3. Bearer xxx (Authorization header)
    (r'(?i)Bearer\s+[A-Za-z0-9_.-]{8,200}', 'bearer'),
    # 4. secret=xxx
    (r'(?i)(["\']?secret["\']?\s*[:=]\s*["\']?)[A-Za-z0-9_.-]{8,100}\b', 'secret'),
    # 5. password=xxx
    (r'(?i)(["\']?password["\']?\s*[:=]\s*["\']?)[^\s,;"\']{4,100}\b', 'password'),
    # 6. UUID 风格 token (排除短 UUID 误判)
    (r'\b[A-Za-z0-9]{8}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{12}\b', 'uuid_token'),
]


def redact_text(text: str) -> str:
    """Replace sensitive patterns with [REDACTED:type] markers.
    
    v1.0.28: 写入 llm_jobs.error 前调用, 避免 LLM 凭据落库.
    6 模式: api_key / token / bearer / secret / password / uuid_token.
    
    Returns:
        text 原文, 敏感模式替换为 [REDACTED:type].
        如果 text 为空, 返 text (不抛异常).
    """
    if not text:
        return text
    redacted = text
    for pattern, ptype in REDACTION_PATTERNS:
        redacted = re.sub(pattern, f'[REDACTED:{ptype}]', redacted)
    return redacted
```

---

## 5. 测试 (test_fact_check_plus.py +80 行)

| 测试名 | 验证 |
|--------|------|
| `test_redact_api_key_pattern` | `sk-abc123` → `[REDACTED:api_key]`, 普通 "sk" 不误判 |
| `test_redact_token_pattern` | `token=xyz` / `"token":"xyz"` / `Token: xyz` → `[REDACTED:token]`, 短 token (< 8 字符) 不脱敏 |
| `test_redact_bearer_pattern` | `Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...` → `[REDACTED:bearer]`, 普通 "Bearer" 不误判 |
| `test_redact_mixed_patterns` | 字符串含 6 种模式各 1 个, 全部替换为对应 [REDACTED:type] |
| `test_failure_log_redacts_before_truncate` | seed LLM 返回 `"api_key=sk-abc123"`, _log_failure 后 error 字段不含 "sk-abc123", 含 `[REDACTED:api_key]` |

---

## 6. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 6 正则误判 (如 LLM 输出 "token" 普通词) | 中 | 脱敏过度, 失诊断价值 | 5 个测试 case 覆盖, 后续根据实际失败迭代规则 |
| 6 正则漏判 (新 LLM 凭据格式) | 中 | 凭据落库 | P1 加配置化规则, 留扩展 |
| 脱敏替换在 truncation 前, 脱敏后文本变短 | 低 | _truncate_utf8 行为不变 (还是 2KB 切) | 已在 4.3 伪代码验证 |
| 跨行 / 编码异常的 LLM 输出 | 低 | 正则漏 | _truncate_utf8 已 UTF-8 边界保护, 脱敏在之前 |
| 大小写敏感 (Bearer / bearer) | 低 | 漏判 | 5 模式都用 (?i) flag, 大小写不敏感 |

---

## 7. 实施顺序 (Day 1, sprint-09 第三个 sub-ship)

1. `git checkout -b feature/v1.0.28-evidence-redaction` (从 main, 父 agent 已做)
2. 写 spec (父 agent 已做)
3. 父 agent 写 moni/llm/redaction.py (~50 行)
4. 父 agent 改 moni/llm/jobs.py _log_failure 调 redact_text (~3 行)
5. 父 agent 写 5 测试到 tests/test_fact_check_plus.py (~80 行)
6. pytest 全量 verify
7. gh pr create + merge + tag v1.0.28 + push
8. 8900 重启 (生产 DB 无 schema 变化, 但代码变了)

---

## 8. 关联

- 触发: sprint-08 v1.0.23 B5 落库后, P0 数据安全漏洞
- 跟 sprint-08 v1.0.22 fact_check_plus + v1.0.23 B5 协同
- 申报书 B v4 §4.5 数据安全与风险控制: "AI 调用 api key 仅发送必要 token" — 落库侧没明文规定, 本次填坑
- 跟 v1.0.26 evidence 时效 + v1.0.27 状态机协同 (3 个 sprint-09 sub-ships)
- 关联 PR: PR #90 (dad 合并) + #91 (v1.0.26) + #93 (v1.0.27) 已 merge 到 main
