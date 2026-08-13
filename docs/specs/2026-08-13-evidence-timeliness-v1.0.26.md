# Sprint 09 — evidence 时效校验 tech spec (v1.0.26)

> **状态**: draft, 父 agent 全权拍板
> **作者**: blue-owl (arch, hermes coder profile)
> **日期**: 2026-08-13
> **关联**: sprint-08 v1.0.25 看板 method 分布 P1 候选第 6 项

---

## 0. 背景与决策回顾

### 0.1 现状 (sprint-08 ship 后实查)

- v1.0.22 fact_check_plus 实施后, 双闸防幻觉 (闸 1 证据片段非空 + 闸 2 权威域白名单)
- **缺闸 3**: evidence 抓取时间 (fact_check_evidence.fetched_at) 可能过期 (例如 6 月抓的证据核查 8 月文章, 数据可能变)
- 25 篇 llm_inference 里有 0 篇有 evidence 行 (sprint-08 v1.0.24 简报 web 模式刚开始 enqueue, 时间窗短还没积累)
- **缺查询**: `/api/fact-check-plus/evidence/{id}` 端点只返 evidence 列表, 不返 timeliness 标志, 前端无法标"过期"

### 0.2 拍板 (父 agent 全权)

- 闸 3: evidence.fetched_at 距今 > 30 天 → 标 `【过期】` (不降级只标)
- evidence.fetched_at 距今 > 90 天 → 标 `【存疑】` (强降级)
- 端点 GET evidence 返新字段 `timeliness: "fresh" | "30d_warn" | "90d_demote" | "unknown"`
- 新字段 `articles.fact_check_timeliness` (TEXT, 最新一次 evidence 抓取时效)
- 新字段 `fact_check_evidence.days_old` (INTEGER, 抓取时距今天数, 加速 query)

---

## 1. 范围 (In/Out)

### 1.1 IN (v1.0.26 ship)

1. **`moni/db/migrations/027_evidence_timeliness.sql`** (新):
   - `ALTER TABLE articles ADD COLUMN fact_check_timeliness TEXT` (最新一次 evidence 抓取时效)
   - `ALTER TABLE fact_check_evidence ADD COLUMN days_old INTEGER` (抓取时距今天数, 写入时计算)
   - 存量回填: `UPDATE fact_check_evidence SET days_old = CAST((julianday('now') - julianday(fetched_at)) AS INTEGER) WHERE fetched_at IS NOT NULL`
   - 索引: `idx_fce_days_old ON fact_check_evidence(days_old)`
   - `INSERT OR IGNORE INTO schema_version (version=27)` (sprint 05 教训)
2. **`moni/db/migrate.py` _CURRENT_VERSION 26 → 27**
3. **`moni/llm/jobs.py` fact_check_plus 双闸 #3 (timeliness)**:
   - `_persist_fact_check_plus` 写 evidence 时计算 `days_old` 字段
   - 30 天 < days_old ≤ 90 天 → conclusion 加 `(已过期 30-90 天)` 后缀
   - 90 天 < days_old → conclusion 强制改 `【存疑】` (即使原本【已核实】)
4. **`moni/web/routes/fact_check_plus.py` evidence 端点新返 timeliness 字段**:
   - 计算 `timeliness = "fresh" if days_old <= 30 else ("30d_warn" if days_old <= 90 else "90d_demote")`
   - 返 JSON 多 1 字段 `timeliness`
5. **5 测试 (`tests/test_fact_check_plus.py` 追加)**:
   - `evidence_timeliness_fresh_pass` — days_old=5 → timeliness=fresh, 结论不变
   - `evidence_timeliness_30d_warn` — days_old=60 → timeliness=30d_warn, 结论加 `(已过期 30-90 天)` 后缀
   - `evidence_timeliness_90d_demote` — days_old=120 → timeliness=90d_demote, 结论强制【存疑】
   - `evidence_timeliness_null_handled` — days_old=NULL → timeliness=unknown, 不影响结论
   - `evidence_timeliness_endpoint_returns_timeliness` — 端点返 timeliness 字段

### 1.2 OUT (P1+ 留)

- evidence 落库脱敏 (P0 数据安全, 留 sprint-10)
- 简报 worker 改用 LLM 检索 (v1.0.24 同步性升级, 留 sprint-10)
- 状态机 (人工覆写 draft→verified, 留 sprint-11)
- method × 来源交叉分析 (看板 method 分布升级, 留 sprint-10)
- 按 method token 成本 (留 sprint-10)
- evidence URL 去重 (留 sprint-12)
- 自动重新跑过期 evidence (留 sprint-12, 需 enqueue job retry 机制)

### 1.3 Non-goals

- 不改 evidence 双闸 #1 (片段非空) / #2 (权威域白名单), 闸 3 是补充
- 不改 method 字段 (sprint-08 v1.0.22 已落)
- 不改抓取时间字段语义 (fact_check_evidence.fetched_at 保持 ISO 8601, days_old 是计算字段)

---

## 2. 文件清单 (~80 行 net add)

| 文件 | 改法 | 内容 |
|------|------|------|
| `moni/db/migrations/027_evidence_timeliness.sql` | +20 | ALTER 2 字段 + 索引 + 存量回填 + INSERT OR IGNORE |
| `moni/db/migrate.py` | +1 | `_CURRENT_VERSION` 26 → 27 |
| `moni/llm/jobs.py` | +25 | `_compute_timeliness(days_old)` helper + `_persist_fact_check_plus` 写 days_old + 30d/90d 标 |
| `moni/web/routes/fact_check_plus.py` | +10 | evidence 端点返 timeliness 字段 |
| `tests/test_fact_check_plus.py` | +80 | 5 新测试 |

---

## 3. 数据契约

### 3.1 新字段 (migration 027)

```sql
ALTER TABLE articles ADD COLUMN fact_check_timeliness TEXT;
ALTER TABLE fact_check_evidence ADD COLUMN days_old INTEGER;
CREATE INDEX idx_fce_days_old ON fact_check_evidence(days_old);
```

`articles.fact_check_timeliness`: 最新一次 evidence 抓取时效, 取值 `fresh` / `30d_warn` / `90d_demote` / `unknown`.
`fact_check_evidence.days_old`: 抓取时距今天数, 写入时计算 (后续 query 加速).

### 3.2 端点 GET /api/fact-check-plus/evidence/{id}

新增字段 `timeliness` per evidence row:
```json
{
  "article_id": "a1",
  "items": [
    {
      "query": "...",
      "url": "https://www.pbc.gov.cn/...",
      "domain": "pbc.gov.cn",
      "snippet": "...",
      "fetched_at": "2026-08-13T...",
      "model": "MiniMax-M3",
      "provider": "minimax-cn",
      "conclusion": "【已核实】",
      "rationale": "...",
      "evidence_seq": 1,
      "days_old": 5,
      "timeliness": "fresh"  // 新增
    }
  ]
}
```

### 3.3 timeliness 映射

| days_old | timeliness | 结论影响 |
|----------|------------|----------|
| 0-30 | `fresh` | 不变 |
| 31-90 | `30d_warn` | 加 `(已过期 30-90 天)` 后缀 |
| 91+ | `90d_demote` | 强制【存疑】 (即使原本【已核实】) |
| NULL | `unknown` | 不影响结论 |

---

## 4. 代码契约 (jobs.py 伪代码)

```python
def _compute_timeliness(days_old: int | None) -> str:
    if days_old is None:
        return "unknown"
    if days_old <= 30:
        return "fresh"
    if days_old <= 90:
        return "30d_warn"
    return "90d_demote"


def _apply_timeliness(conclusion: str, days_old: int | None) -> str:
    """闸 3: 30-90 天加后缀, 90+ 强降级."""
    if days_old is None or days_old <= 30:
        return conclusion
    if days_old <= 90:
        return f"{conclusion}（已过期 30-90 天）"
    return "【存疑】"  # 强降级, 不管原本


def _persist_fact_check_plus(conn, *, job_id, article_id, provider, model,
                              raw_output, finished_at) -> None:
    # ... 现有解析 + 双闸 #1 #2 逻辑
    ...
    now = datetime.now(timezone.utc)
    for i, e in enumerate(valid):
        days_old = (now - datetime.fromisoformat(e["fetched_at"])).days
        e["days_old"] = days_old
        conn.execute(
            """
            INSERT INTO fact_check_evidence
                (id, article_id, job_id, query, url, domain, snippet,
                 fetched_at, model, provider, conclusion, rationale,
                 evidence_seq, created_at, days_old)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (..., days_old),
        )
    # 闸 3 应用到 conclusion
    timeliness = _compute_timeliness(days_old)
    final_conclusion = _apply_timeliness(conclusion, days_old)
    conn.execute(
        """
        UPDATE articles SET
            fact_check_conclusion = ?,
            fact_check_method     = 'web_search',
            fact_checked_at       = ?,
            fact_check_note       = ?,
            fact_check_timeliness = ?,
            has_fact_check        = 1
        WHERE id = ?
        """,
        (final_conclusion, finished_at, note, timeliness, article_id),
    )
```

---

## 5. 测试 (test_fact_check_plus.py +80 行)

| 测试名 | 验证 |
|--------|------|
| `evidence_timeliness_fresh_pass` | seed evidence fetched_at = today-5d → days_old=5 → timeliness=fresh, 结论【已核实】不变 |
| `evidence_timeliness_30d_warn` | seed evidence fetched_at = today-60d → days_old=60 → timeliness=30d_warn, 结论加 `(已过期 30-90 天)` 后缀 |
| `evidence_timeliness_90d_demote` | seed evidence fetched_at = today-120d → days_old=120 → timeliness=90d_demote, 结论强制【存疑】即使原本【已核实】 |
| `evidence_timeliness_null_handled` | days_old=NULL → timeliness=unknown, 结论【已核实】不变 (NULL 兼容) |
| `evidence_timeliness_endpoint_returns_timeliness` | 端点 GET evidence/{id} 返 timeliness 字段, 3 种值各验证一次 |

---

## 6. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 存量 0 evidence 行的回填不影响 (UPDATE 0 行) | 低 | 无 | 幂等 |
| 30 天阈值偏短 (实时新闻 1 周就过期) | 中 | 标过期太频繁 | 90 天强降级是软保护, 30 天只加后缀不降级 |
| days_old 计算依赖 fetched_at 不为 NULL | 中 | 闸 3 失效 | NULL 兼容, 4 测试覆盖 |
| 闸 3 跟双闸 #2 冲突 (权威域白名单命中但 90+ 天) | 低 | 闸 3 优先 | 闸 3 在双闸 #1 #2 后跑, 最后决定结论 |
| 端点性能 (每条 evidence 算 days_old) | 低 | 慢 | days_old 已写入 DB, 端点直接读不重算 |

---

## 7. 实施顺序 (Day 1-2, sprint-09 唯一一个 sub-ship)

1. `git checkout -b feature/v1.0.26-evidence-timeliness` (从 main, 父 agent 已做)
2. 写 spec (父 agent 已做)
3. gold-tiger 改 migration 027 + jobs.py + routes
4. red-fox 写 5 测试
5. green-frog 跑 pytest verify
6. arch 5 层 verify
7. gh pr create + merge + tag v1.0.26 + push

---

## 8. 关联

- 触发: sprint-08 v1.0.25 看板 method 分布 P1 候选第 6 项
- 跟 sprint-08 v1.0.22 双闸 #1 #2 协同 (闸 3 是补充)
- 申报书 B v4 估分: 跟时效校验相关, 但 v1.0.26 是 sub-feature, 不单独立 W 路线
- Obsidian 双写: sprint-09 收尾时补
