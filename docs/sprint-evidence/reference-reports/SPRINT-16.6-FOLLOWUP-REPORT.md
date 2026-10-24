# Sprint-16.6 Follow-up: 【待核实】+[] 是否 bug?

**日期**: 2026-08-24  
**基准**: main HEAD = `dd3ef00` (v1.0.35.11 ship, PR #118 squash)  
**风险评级**: **Low** (~15 行改动, 无 migration / API 变更)

---

## §1 代码定位: 确认【待核实】+[] 仍 fail

### 1.1 当前 Gate 1 逻辑 ([jobs.py:646-655](../../../moni/llm/jobs.py#L646-L655))

```python
if not valid:
    if conclusion.startswith("【无需核查】"):         # ← v1.0.35.11 新增
        _persist_no_check_needed(...)
        return
    raise ValueError("no evidence with snippet")     # ← 【待核实】/【存疑】仍走这里
```

**确认**: 【待核实】+`evidence=[]` 仍 raise → status=failed. **【存疑】+[] 也同样 fail**.

### 1.2 模板规定 ([fact_check_plus.md:41](../../../moni/llm/prompts/fact_check_plus.md#L41))

```
没有可引用证据时 evidence 给空数组，conclusion 只能是【待核实】/【存疑】/【无需核查】
```

**模板明确允许 3 个档 evidence=[]**: 【待核实】/【存疑】/【无需核查】.

只有 **【已核实】不允许** evidence=[] (模板 L20: "不联网则不得给【已核实】。没有证据支撑只能给【待核实】或【存疑】").

### 1.3 USER_GUIDE §5.4 四档规则 ([USER_GUIDE.md:602-605](../../../USER_GUIDE.md#L602-L605))

```
- 【待核实】— 检索结果不足, 或仅自媒体命中 (无 snippet 证据丢弃)
- 【存疑】  — 数字与文内声明矛盾, 或检索结果冲突
```

**关键**: "检索结果不足" 和 "仅自媒体命中 (无 snippet 证据丢弃)" 都是合法的【待核实】场景. Gate 1 丢弃无 snippet 的 evidence 后 valid=[] 是正常结果, 不是 LLM 错误.

### 1.4 所有返 【待核实】+[] 的 LLM 合法场景

| 场景 | 说明 | 模板支持? |
|------|------|-----------|
| 联网搜了但 0 结果 | web_search 返回空 | ✅ L41 |
| 搜到结果但全是自媒体 (无 snippet) | Gate 1 丢弃后 valid=[] | ✅ L41 + USER_GUIDE |
| 搜到结果但 snippet 不足以判断 | LLM 诚实返空 evidence | ✅ L41 |
| 文章含可验证主张但检索超时 | hermes web agent 超时后判【待核实】 | ✅ 合理降级 |

**结论: 全部是合法场景, 不是 LLM 返回错误.**

### 1.5 sprint-16.6 防御测试的矛盾

[test_fact_check_plus.py:1103-1128](../../../tests/test_fact_check_plus.py#L1103-L1128):

```python
def test_pending_empty_evidence_still_fails(fresh_db, monkeypatch):
    """防御: 【待核实】+ evidence=[] 仍应 status=failed (现有行为不变)."""
    ...
    assert job["status"] == "failed"    # ← 这个断言跟模板 L41 矛盾
```

sprint-16.6 报告 (§4.2) 设计此测试时, 目的是"钉死其他 3 档行为不变". 但现在发现模板 L41 明确允许【待核实】+[], 这个"不变"本身就是 bug.

---

## §2 结论: **是 bug, 跟【无需核查】同性质**

| 判据 | 结论 |
|------|------|
| 模板 L41 规定 | ✅ 明确允许【待核实】/【存疑】evidence=[] |
| USER_GUIDE §5.4 | ✅ "检索结果不足" = 合法【待核实】 |
| LLM 行为 | ✅ LLM 按模板返回, 不是错误 |
| 历史数据 | 6 个 fail 全是合法的【待核实】+[] |
| 唯一不允许 evidence=[] 的档 | **只有【已核实】** (模板 L7 + L20) |

**只有【已核实】+[] 是真正的闸拒绝场景** — LLM 声称"已核实"但拿不出证据, 这才是 Gate 1 应该拦的.

---

## §3 修法建议

### 方案 A: 扩展豁免 — 只拦【已核实】+[] ✅ 推荐

**改动**: [jobs.py:646-655](../../../moni/llm/jobs.py#L646-L655), ~5 行

```python
        if not valid:
            # 模板 L41: "没有可引用证据时 evidence 给空数组, conclusion 只能是
            # 【待核实】/【存疑】/【无需核查】". 只有【已核实】+[] 是闸拒绝场景
            # (L7+L20: "不联网则不得给【已核实】, 没有证据支撑只能给【待核实】").
            if not conclusion.startswith("【已核实】"):
                _persist_empty_evidence(
                    conn, article_id=article_id, conclusion=conclusion,
                    finished_at=finished_at,
                )
                return
            raise ValueError("no evidence with snippet")
```

**附带**:
- `_persist_no_check_needed` → 重命名为 `_persist_empty_evidence` (语义更准, 不再只服务【无需核查】)
- 函数体不变 (写 articles 结论 + 清空旧 evidence + 不插新)
- **防御测试修改**: `test_pending_empty_evidence_still_fails` → `test_pending_empty_evidence_succeeds` (断言 `status=="done"`)
- **新增防御**: `test_doubtful_empty_evidence_succeeds` (【存疑】+[] → done)
- **保留**: `test_verified_empty_evidence_still_fails` (【已核实】+[] → failed, 唯一应拦的)

**优势**:
- 完全对齐模板 L41 设计
- 改动极小, 逻辑判断从白名单 (`startswith("【无需核查】")`) 变黑名单 (`not startswith("【已核实】")`)
- 6 个历史 fail 自然修好

### 方案 B: 改 prompt 让 LLM 不返【待核实】+[]

要求 LLM "【待核实】必须返 ≥1 evidence"

**不推荐**:
- 违反模板 L41 自身规定 (L41 允许空)
- LLM 被迫编造 evidence → 降低数据质量
- prompt 改动跟 USER_GUIDE 冲突
- 不修实现的话, 历史 6 个 fail 仍不修复

---

## §4 改动清单 (方案 A)

| 文件 | 改动 | 行数 |
|------|------|------|
| [jobs.py](../../../moni/llm/jobs.py) L646-654 | 判断条件从 `conclusion.startswith("【无需核查】")` → `not conclusion.startswith("【已核实】")` | ~3 行 |
| [jobs.py](../../../moni/llm/jobs.py) L793 | 函数名 `_persist_no_check_needed` → `_persist_empty_evidence`; docstring 更新 | ~5 行 |
| [test_fact_check_plus.py](../../../tests/test_fact_check_plus.py) L1103-1128 | `test_pending_empty_evidence_still_fails` → 改断言为 `status=="done"` + 验 articles 入库 | ~8 行 |
| test_fact_check_plus.py | 新增 `test_doubtful_empty_evidence_succeeds` (【存疑】+[] → done) | ~20 行 |
| **合计** | | **~36 行** |

### 4.1 受影响测试

| 测试 | 影响 |
|------|------|
| `test_no_check_needed_empty_evidence_succeeds` (L1023) | ✅ 不变 (仍 pass, 走新函数名) |
| `test_verified_empty_evidence_still_fails` (L1070) | ✅ 不变 (仍 fail, 唯一被拦的) |
| `test_pending_empty_evidence_still_fails` (L1103) | ⚠️ **必须改**: 断言从 `failed` → `done` |
| 其他 30+ 测试 | ✅ 不变 |

---

## §5 风险 + 工期 + 判断

### 风险: **Low**

- ~36 行改动 (含测试), 无 migration / API / 前端
- 判断从白名单 → 黑名单, 只新增 2 个 done 路径 (【待核实】+[] / 【存疑】+[])
- 【已核实】+[] 防御测试保持 failed (唯一拦截场景)
- rollback = revert 1 commit

### 工期: **~20 min**

| 活动 | 估时 |
|------|------|
| 改 jobs.py (条件 + 重命名) | 5 min |
| 改/新增测试 | 8 min |
| pytest 全量验证 | 5 min |
| PR + merge + tag | 5 min |

### 是否值得修?

> **值得, 且应该跟 v1.0.35.11 一起作为 sprint-16.6 hotfix ship.** 性质完全相同 (模板 L41 vs Gate 1 矛盾), sprint-16.6 漏了【待核实】/【存疑】两档. 历史 6 个 fail 在 DB 里, dad 已看到. 改动面极小 (3 行条件 + 重命名), 不值得等下一个 sprint.

### 第一刀

把 [L649](../../../moni/llm/jobs.py#L649) 从:
```python
if conclusion.startswith("【无需核查】"):
```
改为:
```python
if not conclusion.startswith("【已核实】"):
```
这一行就修好了. 其余是重命名和测试同步.
