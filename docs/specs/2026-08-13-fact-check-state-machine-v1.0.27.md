# Sprint 09 — 事实核查+ 状态机 tech spec (v1.0.27)

> **状态**: draft, 父 agent 全部拍板 (Q1-Q4 = A/A/A/A)
> **作者**: blue-owl (arch, hermes coder profile)
> **日期**: 2026-08-13
> **关联**: sprint-09 v1.0.26 evidence 时效后 P1 候选第 3 项 (合规留痕刚需)

---

## 0. 背景与决策回顾

### 0.1 现状 (sprint-08/09 ship 后实查)

- v1.0.22 fact_check_plus 实施, 联网核查结论 LLM 直接写 articles.fact_check_conclusion
- v1.0.25 看板 method 分布, llm_inference 25 篇 + web_search 0 篇
- v1.0.26 evidence 时效, 端点返 timeliness
- **缺人工覆写流程**: LLM 结论没机会"被业务人员确认/驳回", 跟 F6 cases 库 + F5 events 库的 draft→verified 状态机不匹配, 合规审计查不到"谁最终确认这条核查"

### 0.2 拍板 (Q1-Q4 = A/A/A/A, 父 agent 全权)

- **Q1=A** 状态机字段位置: 复用 `articles.fact_check_timeliness` 字段, 值改为 `state` 枚举 (semver 字段语义变化, 单字段 5 档)
- **Q2=A** 状态枚举: 5 档 `draft` / `verified` / `published` / `deprecated` / `archived` (跟 F6 cases 同构)
- **Q3=A** 覆写端点: `PATCH /api/fact-check-plus/{id}/review` body `{state, reason, reviewer}` (RESTful PATCH 局部更新)
- **Q4=A** 审计字段: 加 `articles.fact_check_reviewed_by` (TEXT) + `articles.fact_check_reviewed_at` (TEXT ISO8601) + `articles.fact_check_review_note` (TEXT 备注) 3 字段

---

## 1. 范围 (In/Out)

### 1.1 IN (v1.0.27 ship)

1. **`moni/db/migrations/028_fact_check_state.sql`** (新):
   - `articles.fact_check_timeliness` 字段改名 → `articles.fact_check_state` (TEXT, 默认 'draft')
   - ALTER TABLE 不支持重命名, 用 workflow: ADD COLUMN new + UPDATE FROM old + DROP old (3 步), 需保留历史 timeliness 数据
   - 实际方案: 不改名, 加新字段 `articles.fact_check_state` (默认 'draft'), 保留 timeliness
   - 加 3 字段: `fact_check_reviewed_by` (TEXT) / `fact_check_reviewed_at` (TEXT) / `fact_check_review_note` (TEXT)
   - 存量回填: `UPDATE articles SET fact_check_state = 'verified' WHERE has_fact_check = 1` (已有核查的 = verified)
   - 索引: `idx_articles_state` on fact_check_state
   - `INSERT OR IGNORE INTO schema_version (version=28)` (sprint 05 教训)
2. **`moni/db/migrate.py` _CURRENT_VERSION 27 → 28**
3. **`moni/web/routes/fact_check_plus.py` 新端点** `PATCH /api/fact-check-plus/{id}/review`:
   - body: `{"state": "draft|verified|published|deprecated|archived", "reason": "...", "reviewer": "user_id"}`
   - 校验 state 5 档枚举
   - 校验转换合法性: `draft → verified` (允许), `verified → published` (允许), `published → deprecated` (允许), `deprecated → archived` (允许), 跨档跳 (如 `draft → published`) 返 400
   - UPDATE articles SET fact_check_state, fact_check_reviewed_by, fact_check_reviewed_at, fact_check_review_note
   - 返 200 + 新 state 详情
4. **看板 method 端点扩展** `GET /api/stats/fact-check-method?period=...`: by_method 多加 `state` 维度 (verified/published/archived 计数)
5. **6 测试**:
   - `test_state_machine_draft_default`: seed 1 article → state='draft'
   - `test_state_machine_review_draft_to_verified`: PATCH {state=verified, ...} → 200 + state='verified'
   - `test_state_machine_review_validates_transition`: PATCH {state=archived, from=draft} → 400 (跨档跳非法)
   - `test_state_machine_review_records_audit_fields`: PATCH 后 reviewed_by/at/note 写入
   - `test_state_machine_endpoint_404_unknown_article`: 未知 article → 404
   - `test_state_machine_stats_method_includes_state_dimension`: 看板端点返 by_state 数组

### 1.2 OUT (P1+ 留)

- 状态机转换审计日志表 (`fact_check_audit_log`, P1, 留 sprint-10)
- 多 reviewer 协同 (一个 article 多人审, P1)
- 自动按时间推进 (e.g. 30 天 verified 自动 deprecated, P1, 留 sprint-10)
- 状态机与闸 3 timeliness 联动 (e.g. 90+ 天 verified → 自动降级, P1)
- reviewer 权限 (现在 reviewer 是 free-form 字符串, 后期接 RBAC, P1)

### 1.3 Non-goals

- 不改 fact_check_conclusion 字段 (LLM 结论保留, 状态是另一维度)
- 不改 fact_check_method 字段 (sprint-08 已落)
- 不动 evidence 表
- 不改 events 库状态机 (F6 已有, 不重复)

---

## 2. 文件清单 (~80 行 net add)

| 文件 | 改法 | 内容 |
|------|------|------|
| `moni/db/migrations/028_fact_check_state.sql` | +25 | 3 字段 + 索引 + 存量回填 + INSERT OR IGNORE |
| `moni/db/migrate.py` | +1 | `_CURRENT_VERSION` 27 → 28 |
| `moni/web/routes/fact_check_plus.py` | +60 | 新端点 PATCH review + 转换合法性校验 + audit 字段写入 |
| `moni/stats/repository.py` | +15 | `fact_check_method_layer_stats` 加 by_state 维度 |
| `moni/web/routes/stats.py` | +10 | 看板端点返 by_state 数组 |
| `tests/test_fact_check_state.py` | +100 | 6 新测试 |

---

## 3. 数据契约

### 3.1 新字段 (migration 028)

```sql
ALTER TABLE articles ADD COLUMN fact_check_state TEXT NOT NULL DEFAULT 'draft';
ALTER TABLE articles ADD COLUMN fact_check_reviewed_by TEXT;
ALTER TABLE articles ADD COLUMN fact_check_reviewed_at TEXT;
ALTER TABLE articles ADD COLUMN fact_check_review_note TEXT;
CREATE INDEX idx_articles_state ON articles(fact_check_state);
```

`fact_check_state` 5 档:
- `draft` — LLM 刚生成, 未审
- `verified` — 业务人员确认可用
- `published` — 已对外发布 (简报/对外报告)
- `deprecated` — 后续被新数据推翻, 但保留审计
- `archived` — 不再使用, 仅留痕

### 3.2 状态机转换规则

| from \ to | draft | verified | published | deprecated | archived |
|-----------|-------|----------|-----------|-------------|----------|
| (无)      | ✓ (LLM 自动) | | | | |
| draft     | - | ✓ | 400 (跨档) | 400 | 400 |
| verified  | 400 (回退需 archived→) | - | ✓ | ✓ | ✓ |
| published | 400 | 400 | - | ✓ | ✓ |
| deprecated | 400 | 400 | 400 | - | ✓ |
| archived  | 400 | 400 | 400 | 400 | - |

单向流转: draft → verified → published → deprecated → archived. 不允许回退 (需重新创建核查).

### 3.3 端点 PATCH /api/fact-check-plus/{id}/review

请求:
```json
{
  "state": "verified",
  "reason": "央行公告已二次核实, 数字一致",
  "reviewer": "zhang.wei"
}
```

响应 (200):
```json
{
  "article_id": "a1",
  "state": "verified",
  "previous_state": "draft",
  "reviewed_by": "zhang.wei",
  "reviewed_at": "2026-08-13T10:30:00Z",
  "review_note": "央行公告已二次核实, 数字一致"
}
```

错误:
- 404: 未知 article_id
- 400: state 枚举不合法 / 跨档跳非法 / reason 为空
- 422: reviewer 为空

### 3.4 看板 method 端点扩展

响应:
```json
{
  "period": "30d",
  "by_method": [
    {"method": "llm_inference", "article_count": 28, "percentage": 87.5}
  ],
  "by_state": [
    {"state": "draft", "article_count": 20, "percentage": 71.4},
    {"state": "verified", "article_count": 5, "percentage": 17.9},
    {"state": "published", "article_count": 2, "percentage": 7.1},
    {"state": "deprecated", "article_count": 1, "percentage": 3.6},
    {"state": "archived", "article_count": 0, "percentage": 0.0}
  ]
}
```

---

## 4. 代码契约 (routes 伪代码)

```python
VALID_STATES = ("draft", "verified", "published", "deprecated", "archived")
ALLOWED_TRANSITIONS = {
    None: {"draft"},                              # 初始
    "draft": {"verified", "archived"},            # 草稿可验证/直接归档
    "verified": {"published", "deprecated", "archived"},
    "published": {"deprecated", "archived"},
    "deprecated": {"archived"},
    "archived": set(),                            # 终态
}

@router.patch("/fact-check-plus/{article_id}/review")
def review_article(article_id: str, body: ReviewRequest) -> dict:
    if not body.reviewer or not body.reviewer.strip():
        raise BadRequestError(message="reviewer is required")
    if body.state not in VALID_STATES:
        raise BadRequestError(message=f"state must be one of {VALID_STATES}")
    if not body.reason or not body.reason.strip():
        raise BadRequestError(message="reason is required")
    
    conn = get_db()
    row = conn.execute("SELECT fact_check_state FROM articles WHERE id = ?", (article_id,)).fetchone()
    if row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")
    
    current_state = row["fact_check_state"]
    if body.state not in ALLOWED_TRANSITIONS.get(current_state, set()):
        raise BadRequestError(
            message=f"Invalid transition {current_state!r} -> {body.state!r}",
        )
    
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE articles SET
            fact_check_state = ?,
            fact_check_reviewed_by = ?,
            fact_check_reviewed_at = ?,
            fact_check_review_note = ?
        WHERE id = ?
        """,
        (body.state, body.reviewer, now, body.reason, article_id),
    )
    conn.commit()
    return {
        "article_id": article_id,
        "state": body.state,
        "previous_state": current_state,
        "reviewed_by": body.reviewer,
        "reviewed_at": now,
        "review_note": body.reason,
    }
```

---

## 5. 测试 (test_fact_check_state.py +100 行)

| 测试名 | 验证 |
|--------|------|
| `test_state_machine_draft_default` | seed article → state='draft' 默认 |
| `test_state_machine_review_draft_to_verified` | PATCH state=verified → 200 + state='verified' + previous_state='draft' |
| `test_state_machine_review_validates_transition` | PATCH state=archived (from draft) → 400 (跨档跳) |
| `test_state_machine_review_records_audit_fields` | PATCH 后 reviewed_by='zhang.wei' + reviewed_at + review_note 写入 |
| `test_state_machine_endpoint_404_unknown_article` | 未知 article → 404 |
| `test_state_machine_stats_method_includes_state_dimension` | 看板端点返 by_state 数组 5 档全有 |

---

## 6. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 状态机跨档跳非法 → 用户卡住 | 中 | 体验差 | 端点返 400 + 明确 allowed_transitions 提示 |
| reviewer 是 free-form 字符串, 后期接 RBAC 改复杂 | 中 | 数据迁移 | P1 留, sprint-10+ 处理 |
| 审计字段跟 events/cases 库状态机不互通 | 低 | 跨库分析难 | 3 库各自独立审计, P1 留统一审计 |
| 看板 method × state 交叉太复杂 | 低 | 性能 | 5 档 × 3 method = 15 组合, SQL 简单 GROUP BY |
| archived 终态不可恢复 | 中 | 合规 | 终态设计, 需重新核查, 这是正确行为 |

---

## 7. 实施顺序 (Day 1-2, sprint-09 第二个 sub-ship)

1. `git checkout -b feature/v1.0.27-fact-check-state-machine` (从 main, 父 agent 已做)
2. 写 spec (父 agent 已做)
3. gold-tiger 改 migration 028 + routes fact_check_plus + stats
4. red-fox 写 6 测试
5. green-frog 跑 pytest verify
6. arch 5 层 verify
7. gh pr create + merge + tag v1.0.27 + push

---

## 8. 关联

- 触发: sprint-08 v1.0.25 看板 method 分布 P1 候选第 3 项 (合规留痕)
- 跟 F6 cases 库状态机同构 (`draft → verified → published → deprecated → archived`)
- 跟 F5 events 库 review_status 类似 (`draft/confirmed/rejected/archived`)
- 申报书 B v4 估分: 合规留痕刚需, W7 路线图 +2-3 分
- 关联 PR: PR #90 (dad 合并) + #91 (v1.0.26 evidence 时效) 已 merge 到 main
