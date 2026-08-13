# Sprint 08 — B5 修复 tech spec (v1.0.23)

> **状态**: draft, 父 agent 全部拍板 (Q1-Q3 = A/B/A)
> **作者**: blue-owl (arch, hermes coder profile)
> **日期**: 2026-08-12
> **关联**: sprint-07 v1.0.22 事实核查+ 实施后 P1 候选 #1

---

## 0. 背景与决策回顾

### 0.1 现状 (sprint-07 ship 后实查)

- v1.0.22 fact_check_plus 实施后, 4 个 fact_check_plus jobs failed, error = `DB writeback failed: evidence parse failed`
- 根因: `moni/llm/jobs.py:511` `_parse_fact_check_plus_evidence` 失败 (3 步解析全落空) → `raise ValueError("evidence parse failed")` → 上层 `_run_job:325` 标 failed
- **盲区**: 失败的 LLM `raw_output` (中文乱码 / 缺引号 / 含 thinking tags) **没落库** → 修 LLM 回复格式时无诊断数据
- AGENTS.md 教训: 跟 v1.0.10 摘要失败不回滚 jobs 表同模式, 缺诊断信息

### 0.2 拍板 (Q1-Q3 = A/B/A, 父 agent 全权)

- **Q1=A** 落库位置 = 复用 `llm_jobs.error` 字段 (TEXT, 已存在, 不需新表) — 简单, schema_version 不变
- **Q2=B** 截断前 2KB — 失败回复一般前 2KB 就够诊断, 省空间
- **Q3=A** 仅落"解析失败" (B5 范围聚焦), 写库失败是另 P0

---

## 1. 范围 (In/Out)

### 1.1 IN (v1.0.23 ship)

1. **`jobs.py:_persist_fact_check_plus` 失败时, 把 `raw_output[:2048]` 落进 `llm_jobs.error` 字段** (前缀 `RAW OUTPUT: `)
2. **解析失败子分支** (3 个) 落不同前缀:
   - `RAW OUTPUT: <truncated> | PARSE: evidence parse failed`
   - `RAW OUTPUT: <truncated> | PARSE: evidence is not a list`
   - `RAW OUTPUT: <truncated> | PARSE: no evidence with snippet`
3. **测试** (3-5 个):
   - test_fact_check_plus_failure_log_writes_raw_output: 模拟 LLM 返回无效 JSON → 验 llm_jobs.error 含 raw_output[:2048]
   - test_fact_check_plus_failure_log_truncates_at_2kb: LLM 返回 5KB → 验 truncated
   - test_fact_check_plus_failure_log_preserves_unicode: 含中文 → 验 utf-8 不破
   - test_fact_check_plus_no_failure_log_when_parse_succeeds: 解析成功 → 验 llm_jobs.error 不含 RAW OUTPUT 前缀

### 1.2 OUT (P1+ 留)

- 写库失败时落 raw_output (Q3=B 否决, 是另 P0)
- 重试机制 (jobs.py MAX_ATTEMPTS 已有)
- LLM prompt 优化 (B5 只是诊断, 修 prompt 是另 sprint)

### 1.3 Non-goals

- 不改 `llm_jobs` schema (复用 error 字段)
- 不动方法 1 解析逻辑 (3 步解析仍保持, 失败才落 log)
- 不动 `llm_jobs.error` 字段类型 (TEXT 容纳 2KB raw)

---

## 2. 文件清单 (~30 行 net add)

| 文件 | 改法 | 内容 |
|------|------|------|
| `moni/llm/jobs.py` | +20 | `_persist_fact_check_plus` 失败 try/except 改写 → 把 raw_output[:2048] 拼 error |
| `tests/test_fact_check_plus.py` | +50 | 4 个新测试 (failure log 系列) |

---

## 3. 数据契约

### 3.1 `llm_jobs.error` 字段格式 (B5 后)

| 触发条件 | 字段值格式 |
|----------|------------|
| 解析失败 (B5 范围) | `RAW OUTPUT: <utf-8 truncated 2KB> \| PARSE: <reason>` |
| 之前其他失败 | 保持原 `TypeError: ...` / `TimeoutError: ...` 格式 (不变) |

例:
```
RAW OUTPUT: 好的, 让我查一下。{"conclusion": "已核实" (缺引号) | PARSE: evidence parse failed
```

### 3.2 截断策略

- 2KB = 2048 字节
- UTF-8 边界: 若第 2048 字节在多字节字符中间, **向前**到字符边界 (用 `[:s].encode('utf-8')[:2048].decode('utf-8', 'ignore')` 模式, 或 `len(s.encode('utf-8')[:2048].decode('utf-8', 'ignore'))` 简化版)
- 截断后加 `... [truncated 5KB total]` 提示 (用 `len(raw.encode('utf-8'))` 算原始大小)

### 3.3 失败子分支枚举

| ValueError msg | 解析原因 |
|----------------|----------|
| `evidence parse failed` | `_parse_fact_check_plus_evidence` 3 步解析全落空 |
| `evidence parse failed: evidence is not a list` | JSON 解析 OK 但 `evidence` 字段不是 list |
| `no evidence with snippet` | evidence 列表有但全无 snippet (闸 1 全部丢弃) |

---

## 4. 代码契约 (jobs.py 伪代码)

```python
def _persist_fact_check_plus(conn, *, job_id, article_id, provider, model,
                              raw_output, finished_at) -> None:
    try:
        parsed = _parse_fact_check_plus_evidence(raw_output)
        if not parsed:
            _log_failure(conn, job_id, raw_output, "evidence parse failed")
            raise ValueError("evidence parse failed")
        # ... 现有逻辑
    except ValueError as exc:
        # B5: 解析失败时把 raw_output[:2048] 落进 llm_jobs.error
        if str(exc) in ("evidence parse failed",
                        "evidence parse failed: evidence is not a list",
                        "no evidence with snippet"):
            _log_failure(conn, job_id, raw_output, str(exc))
        raise


def _log_failure(conn, job_id, raw_output, parse_reason) -> None:
    """B5: 失败时把 LLM raw_output 落进 llm_jobs.error 供诊断."""
    truncated = _truncate_utf8(raw_output, 2048)
    total_bytes = len(raw_output.encode("utf-8"))
    suffix = "" if total_bytes <= 2048 else f" ... [truncated {total_bytes}B total]"
    err_msg = f"RAW OUTPUT: {truncated}{suffix} | PARSE: {parse_reason}"
    conn.execute(
        "UPDATE llm_jobs SET error = ? WHERE id = ?",
        (err_msg, job_id),
    )
    conn.commit()


def _truncate_utf8(s: str, max_bytes: int) -> str:
    """截断字符串到 max_bytes, 不破 UTF-8 多字节字符边界."""
    encoded = s.encode("utf-8")[:max_bytes]
    return encoded.decode("utf-8", errors="ignore")
```

注意: 现有 `raise ValueError` 仍上抛 (不改上层 `_run_job:325` 调用方, 行为一致: `llm_jobs.status='failed'`)

---

## 5. 测试 (test_fact_check_plus.py +50 行)

| 测试名 | 验证 |
|--------|------|
| `test_fact_check_plus_failure_log_writes_raw_output` | LLM 返回 `"not json"` → 验 `llm_jobs.error` 含 `RAW OUTPUT: not json` + `\| PARSE: evidence parse failed` |
| `test_fact_check_plus_failure_log_truncates_at_2kb` | LLM 返回 5KB 字符串 → 验 `llm_jobs.error` 长度 ≤ 2048 + 含 `[truncated 5120B total]` |
| `test_fact_check_plus_failure_log_preserves_unicode` | LLM 返回中文 (含 emoji) → 验 utf-8 解码 OK, error 字段中文可读 |
| `test_fact_check_plus_no_failure_log_when_parse_succeeds` | 解析成功 → 验 `llm_jobs.error` 不含 `RAW OUTPUT:` 前缀 (确保不污染成功路径) |

---

## 6. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| LLM 返回含敏感 token (api key, 用户名) | 低 | 数据落库外泄 | B5 暂不脱敏 (走 P1 留 sprint-09), 但落库字段 `llm_jobs.error` 是本地 SQLite 不外发 |
| 截断后诊断信息不足 | 低 | 修 prompt 无据 | 截断后附 `[truncated X KB total]` 提示原始大小, 调试时手动调阈值 |
| 测试工时爆炸 | 低 | 拖延 ship | B5 范围聚焦, 4 个测试, 1-2 天 ship |

---

## 7. 实施顺序 (Day 2-3)

1. `git checkout -b feature/v1.0.23-b5-failure-log` (从 main)
2. 改 `moni/llm/jobs.py` (~20 行) + `tests/test_fact_check_plus.py` (~50 行)
3. pytest 4 个新测试 + 全量 0 回归
4. git commit + push
5. gh pr create + merge --squash
6. tag v1.0.23 + push (新 tag 无 --force, 跟 v1.0.19 教训一致)
7. 不需 restart 8900 (无代码重启必要, 但保险起见 kill + restart + 5 端点 curl 验)

---

## 8. 关联

- 触发 sprint-07 v1.0.22 ship 后 4 个失败 jobs
- 关联 sprint-08 拍板题 (Q1=A / Q2=B / Q3=A)
- 落库字段复用 `llm_jobs.error` (跟现有失败原因共存)
