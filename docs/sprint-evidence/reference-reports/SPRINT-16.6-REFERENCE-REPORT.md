# Sprint-16.6 参考报告: 【无需核查】空 evidence 误报 DB writeback failed

**日期**: 2026-08-24  
**基准**: main HEAD = `15a5256` (v1.0.35.9 → v1.0.35.10 ship 后)  
**风险评级**: **Low** (4 行生产代码 + 3 测试, 无 migration / API 变更)

---

## §1 代码定位

### 1.1 触发路径

```
LLM 返回 → _run_job (L411-420) → _persist_fact_check_plus (L591)
                                    → 解析 raw_output
                                    → Gate 1 (L619-641): 过滤无效 evidence → valid=[]
                                    → L640-641: if not valid: raise ValueError("no evidence with snippet")
                                    → 被 L770-776 捕获 → B5 _log_failure → 外层 L421-427 _mark_failed
                                    → status=failed, error="DB writeback failed: RAW OUTPUT: ... | PARSE: no evidence with snippet"
```

### 1.2 精确故障行

[jobs.py:640-641](../../../moni/llm/jobs.py#L640-L641):

```python
        if not valid:
            raise ValueError("no evidence with snippet")
```

这段逻辑**不区分 conclusion 类型** — 无论 LLM 返回 【已核实】/【待核实】/【存疑】/【无需核查】, 只要 Gate 1 过滤后 `valid` 列表为空, 统一报错.

### 1.3 模板设计 vs 闸逻辑矛盾

[fact_check_plus.md:41-42](../../../moni/llm/prompts/fact_check_plus.md#L41-L42):

```
- 没有可引用证据时 evidence 给空数组，conclusion 只能是【待核实】/【存疑】/【无需核查】
- 【无需核查】时 evidence 必须为空数组
```

**模板明确规定 【无需核查】的 evidence 必须是 `[]`**, 但闸逻辑将 `evidence=[]` 视为失败. 设计与实现矛盾.

### 1.4 受影响 callers (3 条入口, 全部走同一 `_run_job`)

| 入口 | 路径 | 受影响? |
|------|------|--------|
| 单篇 POST `/api/articles/{id}/fact-check-plus` | [fact_check_plus.py:319](../../../moni/web/routes/fact_check_plus.py) → enqueue → worker → `_run_job` | ✅ 受影响 |
| 批量 POST `/api/fact-check-plus/batch` | [fact_check_plus.py:369](../../../moni/web/routes/fact_check_plus.py) → enqueue (≤20) → worker → `_run_job` | ✅ 受影响 |
| `share/enrichment.py` 简报 enrich | [enrichment.py:287-292](../../../moni/share/enrichment.py#L287-L292) → INSERT `llm_jobs` → worker → `_run_job` | ✅ 受影响 |

**修复 `_persist_fact_check_plus` 一处, 3 条入口全部修好.**

### 1.5 conclusion 提取时机

`conclusion` 在 [L644](../../../moni/llm/jobs.py#L644) 提取:

```python
conclusion = str(parsed.get("conclusion") or "【待核实】").strip()
```

但此行在 Gate 1 之后 (L640 raise 之后), **所以当 evidence 为空时, 代码永远到不了 L644**. 这是核心问题: conclusion 判断晚于 evidence 闸.

---

## §2 修复方案 (3 选 1)

### 方案 A: 在 Gate 1 前提取 conclusion, 【无需核查】跳过 evidence 闸 ✅ 推荐

**改动**: [jobs.py](../../../moni/llm/jobs.py) `_persist_fact_check_plus` 内, ~4 行

```python
# 在 L615 (raw_evidence 赋值) 之后, L619 (Gate 1) 之前, 提前提取 conclusion:
conclusion = str(parsed.get("conclusion") or "【待核实】").strip()

# 在 Gate 1 过滤后 (L640), 替换原来的无条件 raise:
if not valid:
    if conclusion.startswith("【无需核查】"):
        # 模板规定【无需核查】evidence 必须为空. 跳过 evidence 闸, 直接写库.
        # timeliness 不适用 (无 evidence 无 days_old), 设 None.
        note = "[]"
        timeliness = None
        # 直接跳到 DB UPDATE (下面的 conn.execute UPDATE articles ...)
    else:
        raise ValueError("no evidence with snippet")
```

然后将 L678-722 的 DB UPDATE 段提出到 evidence 闸之后 (或用 flag 控制), 跳过 L724-769 的 evidence INSERT (因为 valid=[], 没东西可插).

**优势**:
- 改动面最小 (~4 行逻辑 + 提前 conclusion 赋值)
- 其他 3 档 (【已核实】/【待核实】/【存疑】) 行为完全不变
- evidence INSERT 段不执行 (valid=[], 循环体不进)
- Gate 2 (白名单) 和 Gate 3 (时效) 不触发 (因为 valid=[], 循环不进)

**劣势**:
- 需要把 conclusion 赋值提前 (~L615 后), 改变一点读顺序

**回归风险**: **极低**. 仅在 `conclusion.startswith("【无需核查】") AND valid==[]` 时走新路径, 其他分支原封不动.

### 方案 B: 改闸逻辑, evidence 空不算失败, 但只对【无需核查】豁免

跟方案 A 本质相同, 只是把判断写成 `if not valid and not conclusion.startswith("【无需核查】"):`. 差异仅在代码组织方式.

**等价方案 A**, 无额外优劣. 选哪个看代码可读性偏好.

### 方案 C: 在 LLM 返回解析层判断, 【无需核查】直接返成功

**改动**: 在 `_parse_fact_check_plus_evidence` 返回后、`_persist_fact_check_plus` 调用前, 在 `_run_job` L411-420 区域判断.

**劣势**:
- 需要在 `_run_job` 里重复解析 conclusion (跟 `_persist_fact_check_plus` 内部逻辑重复)
- `_run_job` 是通用调度, 不该知道 fact_check_plus 的 conclusion 语义
- 改动面更大, 职责边界模糊

**不推荐.**

### 推荐: **方案 A**

---

## §3 方案 A 实现细节

### 3.1 改动位置

[_persist_fact_check_plus](../../../moni/llm/jobs.py#L591-L777), 改动 ~15 行 (含注释):

```python
def _persist_fact_check_plus(...) -> None:
    try:
        parsed = _parse_fact_check_plus_evidence(raw_output)
        if not parsed:
            raise ValueError("evidence parse failed")

        raw_evidence = parsed.get("evidence") or []
        if not isinstance(raw_evidence, list):
            raise ValueError("evidence parse failed: evidence is not a list")

        # ★ 提前提取 conclusion (原在 L644, 现提前到 Gate 1 之前)
        conclusion = str(parsed.get("conclusion") or "【待核实】").strip()

        # Gate 1: drop rows without snippet / invalid URL
        valid: list[dict[str, Any]] = []
        for e in raw_evidence:
            # ... (现有逻辑不变) ...
            valid.append({...})

        if not valid:
            # ★ 新增: 【无需核查】档允许空 evidence (模板 L42 明确规定)
            if conclusion.startswith("【无需核查】"):
                _persist_no_check_needed(
                    conn, article_id=article_id, conclusion=conclusion,
                    finished_at=finished_at, job_id=job_id,
                )
                return
            raise ValueError("no evidence with snippet")

        # ★ 删掉原 L644 的 conclusion 赋值 (已提前)

        # Gate 2: whitelist (不变)
        # Gate 3: timeliness (不变)
        # DB UPDATE articles (不变)
        # INSERT fact_check_evidence (不变)
    except ValueError as exc:
        # ... (不变)
```

新增辅助函数 `_persist_no_check_needed` (~15 行):

```python
def _persist_no_check_needed(
    conn, *, article_id, conclusion, finished_at, job_id
):
    """【无需核查】档: 写 articles 结论, 不插 evidence, 跳过 Gate 2/3."""
    try:
        conn.execute("""
            UPDATE articles
            SET fact_check_conclusion = ?,
                fact_check_method     = 'web_search',
                fact_checked_at       = ?,
                fact_check_note       = '[]',
                fact_check_timeliness = NULL,
                has_fact_check        = 1,
                has_web_fact_check    = 1
            WHERE id = ?
        """, (conclusion, finished_at, article_id))
    except Exception:
        # Pre-migration 026/027 fallback (同现有模式)
        conn.execute("""
            UPDATE articles
            SET fact_check_conclusion = ?,
                fact_check_method     = 'web_search',
                fact_checked_at       = ?,
                fact_check_note       = '[]',
                has_fact_check        = 1
            WHERE id = ?
        """, (conclusion, finished_at, article_id))
    # 清空旧 evidence (re-run 覆盖场景)
    conn.execute(
        "DELETE FROM fact_check_evidence WHERE article_id = ?",
        (article_id,),
    )
```

### 3.2 articles 表写入值

| 列 | 值 | 理由 |
|----|-----|------|
| `fact_check_conclusion` | `"【无需核查】"` (可能带 ≤20 字说明后缀) | 保持 LLM 原始输出 |
| `fact_check_method` | `'web_search'` | 确实走了联网核查流程 (hermes `-t web`), 只是结论是无需核查 |
| `fact_checked_at` | `finished_at` | 标准时间戳 |
| `fact_check_note` | `'[]'` | 空 JSON 数组, 无 evidence |
| `fact_check_timeliness` | `NULL` | 无 evidence → 无 days_old → 时效不适用 |
| `has_fact_check` | `1` | 已做过核查 |
| `has_web_fact_check` | `1` | 已做过联网核查 |

### 3.3 `llm_jobs` 状态

`_persist_fact_check_plus` return 后, 外层 `_run_job` L430-458 正常走 `status='done'`, 无需额外处理.

---

## §4 测试设计

### 4.1 现有测试影响评估

| 测试 | 影响 | 说明 |
|------|------|------|
| 现有 11 个 `_run_job` 测试 | **不受影响** | 全部用带 evidence 的 payload, 走正常 Gate 1→2→3 路径 |
| `test_run_job_prompt_reaches_call_llm_...` (v1.0.35.10) | **不受影响** | spy 返 带 evidence 的 payload |
| `test_run_job_uses_raw_content_...` (v1.0.35.10) | **不受影响** | spy 返 带 evidence 的 payload |
| `test_render_fact_check_plus_fills_placeholders` (v1.0.35.9) | **不受影响** | render-only, 不走 persist |
| `test_render_fact_check_plus_empty_article_falls_back` (v1.0.35.9) | **不受影响** | render-only |

**结论: 0 个现有测试需要修改.**

### 4.2 新增测试 (3 个)

```python
# tests/test_fact_check_plus.py 新增, 在 sprint-16.5 spy 段之后

def test_no_check_needed_empty_evidence_succeeds(fresh_db, monkeypatch):
    """【无需核查】+ evidence=[] 应 status=done, articles 入库."""
    _insert_article("a-nc")
    _insert_job("job-nc", "a-nc")

    payload = json.dumps({
        "conclusion": "【无需核查】",
        "evidence": [],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({
        "id": "job-nc", "task_type": "fact_check_plus",
        "article_id": "a-nc", "provider": "minimax-cn",
        "model": "MiniMax-M3", "attempts": 0,
    })

    conn = get_db()
    # job 应 done
    job = conn.execute("SELECT status, error FROM llm_jobs WHERE id = 'job-nc'").fetchone()
    assert job["status"] == "done"
    assert job["error"] is None

    # articles 应入库
    art = conn.execute(
        "SELECT fact_check_conclusion, has_web_fact_check, fact_check_note "
        "FROM articles WHERE id = 'a-nc'"
    ).fetchone()
    assert art["fact_check_conclusion"] == "【无需核查】"
    assert art["has_web_fact_check"] == 1
    assert art["fact_check_note"] == "[]"

    # evidence 表应为空 (清空旧 + 不插新)
    ev_count = conn.execute(
        "SELECT COUNT(*) FROM fact_check_evidence WHERE article_id = 'a-nc'"
    ).fetchone()[0]
    assert ev_count == 0


def test_verified_empty_evidence_still_fails(fresh_db, monkeypatch):
    """防御: 【已核实】+ evidence=[] 仍应 status=failed (现有行为不变)."""
    _insert_article("a-vf")
    _insert_job("job-vf", "a-vf")

    payload = json.dumps({
        "conclusion": "【已核实】",
        "evidence": [],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({
        "id": "job-vf", "task_type": "fact_check_plus",
        "article_id": "a-vf", "provider": "minimax-cn",
        "model": "MiniMax-M3", "attempts": 0,
    })

    conn = get_db()
    job = conn.execute("SELECT status FROM llm_jobs WHERE id = 'job-vf'").fetchone()
    assert job["status"] == "failed"


def test_pending_empty_evidence_still_fails(fresh_db, monkeypatch):
    """防御: 【待核实】+ evidence=[] 仍应 status=failed (现有行为不变)."""
    _insert_article("a-pf")
    _insert_job("job-pf", "a-pf")

    payload = json.dumps({
        "conclusion": "【待核实】",
        "evidence": [],
    }, ensure_ascii=False)
    monkeypatch.setattr("moni.llm.jobs.call_llm", lambda *a, **k: payload)

    _run_job({
        "id": "job-pf", "task_type": "fact_check_plus",
        "article_id": "a-pf", "provider": "minimax-cn",
        "model": "MiniMax-M3", "attempts": 0,
    })

    conn = get_db()
    job = conn.execute("SELECT status FROM llm_jobs WHERE id = 'job-pf'").fetchone()
    assert job["status"] == "failed"
```

### 4.3 关于 enrichment.py 路径

enrichment.py 只做 INSERT `llm_jobs` (task_type='fact_check_plus'), 不调 render / persist. Worker pickup 后走同一 `_run_job`. **修 `_persist_fact_check_plus` 即覆盖此路径, 无需单独测.**

---

## §5 工期 + 风险 + 一句话

### 工期

| 活动 | 估时 |
|------|------|
| `_persist_fact_check_plus` 改 4 行 + 新增 `_persist_no_check_needed` 15 行 | 10 min |
| 新增 3 个测试 | 10 min |
| `pytest tests/ -p no:cacheprovider` 全量验证 | 5 min |
| PR + review + squash + tag + restart 8900 | 10 min |
| **合计** | **~35 min** |

### 风险评级: **Low**

- 改动面: ~20 行生产代码 + ~50 行测试
- 仅在 `conclusion.startswith("【无需核查】") AND valid==[]` 时走新路径
- 其他 3 档行为零影响 (防御测试钉死)
- 无 migration / API / 前端变更
- rollback = revert 1 commit

### 一句话

> **推荐方案 A**: 在 `_persist_fact_check_plus` Gate 1 之后、raise 之前, 提前提取 conclusion, 如果是 【无需核查】且 evidence 为空就跳过闸直接 done 入库. 改动 ~20 行, 30 分钟可 ship, 风险 Low. 第一刀: 先提前 `conclusion = str(parsed.get("conclusion")...)` 赋值到 L618 (Gate 1 循环之前), 这一步不改任何行为, 只改读顺序, 先验证 0 regression.
