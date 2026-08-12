# Sprint 08 — 看板 method 分布 tech spec (v1.0.25)

> **状态**: draft, 父 agent 全部拍板 (Q1-Q3 = A/B/B)
> **作者**: blue-owl (arch, hermes coder profile)
> **日期**: 2026-08-12
> **关联**: sprint-07 v1.0.22 + sprint-08 v1.0.23/v1.0.24 实施后量化看板增量

---

## 0. 背景与决策回顾

### 0.1 现状 (sprint-07/08 ship 后实查)

- 量化看板 (v1.0.19 sprint 06 W1) 5 模块: fetch / llm / extract / usage / efficiency, 各模块统计 article / llm_jobs / share_tasks
- 事实核查 method 字段在 `articles.fact_check_method`, 取值 `llm_inference` (基础档) / `web_search` (联网档) / 未来 `human`
- 当前 33 篇 llm_inference, 0 篇 web_search (B5+ 简报 web 模式 ship 后会有 web_search 增长)
- **量化盲区**: 看板看不到"团队实际走了多少联网核查" → 申报书估分 + 内部 token 成本估算无据

### 0.2 拍板 (Q1-Q3 = A/B/B, 父 agent 全权)

- **Q1=A** 位置 = 现有 5 模块区加 1 块 "事实核查 method 分布" (跟现有风格一致, 申报书 W1 路线)
- **Q2=B** 粒度 = method × 文章数 + 百分比 (申报书量化数据直接用)
- **Q3=B** 时间窗 = 最近 7/30 天切换 (跟 W1 看板风格一致)

---

## 1. 范围 (In/Out)

### 1.1 IN (v1.0.25 ship)

1. **新增 `fact_check_method_layer_stats(period)` repository 函数** — 聚合 `articles.fact_check_method` × 时间窗
2. **新增 1 个端点** `GET /api/stats/fact-check-method?period=7d|30d|all`
3. **前端 stats.js 加 1 块饼图** (method 分布, 3 色: llm_inference=蓝 / web_search=绿 / human=灰)
4. **前端 index.html 在量化看板 section 加第 6 块"事实核查 method 分布"** (跟 5 模块并排)
5. **测试** (4-5 个): 见 §5

### 1.2 OUT (P1+ 留)

- method × 来源交叉分析 (llm_inference 多 vs web_search 多 哪个信源, P1)
- 按 method 分的 token 成本 (P1, 申报书可补)
- 按 method 分的事实核查准确率 (B5 + 抽样核验, P2)
- method 趋势 (按天 method 切换, P1)

### 1.3 Non-goals

- 不改现有 5 模块 (sprint 06 W1 已 ship, 不动)
- 不动 stats.js 其他 4 块 (fetch/llm/extract/usage/efficiency)
- 不动 method 字段本身 (sprint-07 migration 025 已落)

---

## 2. 文件清单 (~100 行 net add)

| 文件 | 改法 | 内容 |
|------|------|------|
| `moni/stats/repository.py` | +40 | `fact_check_method_layer_stats(period)` 函数 + SQL 聚合 |
| `moni/stats/savings.py` | +10 | 跟 efficiency 模块联动 (method 切换的 token 成本估算, P1 暂空) |
| `moni/web/routes/stats.py` | +15 | 新端点 `GET /api/stats/fact-check-method` |
| `moni/web/static/js/stats.js` | +30 | 新 1 块饼图 (3 色) + 跟现有 5 块并排渲染 |
| `moni/web/static/index.html` | +10 | 加第 6 块容器 `<div id="stats-fact-check-method">` |
| `moni/web/static/css/main.css` | +5 | `.stats-method-*-color` 3 色 class |
| `tests/test_stats_method.py` | +60 | 4-5 个新测试 |

---

## 3. 数据契约

### 3.1 `articles.fact_check_method` 字段 (sprint-07 migration 025 已有)

| 取值 | 含义 | sprint-07 颜色映射 |
|------|------|-------------------|
| `llm_inference` | 基础档 (LLM 常识推断) | 蓝色 (现有) |
| `web_search` | 联网档 (hermes -t web 实时检索) | 绿色 (新增) |
| `human` | 人工覆写 (P1) | 灰色 (预留) |
| `NULL` | 未核查 | 不在饼图统计 (单独显示总数) |

### 3.2 SQL 聚合

```sql
-- 7d / 30d / all 三种时间窗
SELECT
    COALESCE(fact_check_method, '<NULL>') AS method,
    COUNT(*) AS article_count,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS percentage
FROM articles
WHERE has_fact_check = 1
  AND fact_checked_at >= :period_start  -- NULL 时不限制
GROUP BY fact_check_method
ORDER BY article_count DESC;
```

### 3.3 API 端点

`GET /api/stats/fact-check-method?period=7d|30d|all`

**响应**:
```json
{
  "period": "7d",
  "period_start": "2026-08-05T00:00:00Z",
  "total_articles_with_fact_check": 15,
  "by_method": [
    {"method": "llm_inference", "article_count": 10, "percentage": 66.7},
    {"method": "web_search", "article_count": 4, "percentage": 26.7},
    {"method": "human", "article_count": 1, "percentage": 6.7}
  ],
  "unfact_checked_total": 2655  -- has_fact_check=0 的文章数, 不在饼图内
}
```

### 3.4 前端饼图 (3 色)

```javascript
const COLORS = {
  llm_inference: '#3B82F6',  // blue-500
  web_search: '#10B981',     // emerald-500
  human: '#6B7280',          // gray-500
};
```

饼图标题: "事实核查 method 分布 (最近 7 天)"

---

## 4. 代码契约 (repository.py 伪代码)

```python
def fact_check_method_layer_stats(period: str = "30d") -> dict[str, Any]:
    period_start = _period_to_start(period)  # 复用 sprint 06 helper
    conn = get_db()
    rows = conn.execute(
        """
        SELECT
            COALESCE(fact_check_method, '<NULL>') AS method,
            COUNT(*) AS article_count
        FROM articles
        WHERE has_fact_check = 1
          AND (?1 = '' OR fact_checked_at >= ?1)
        GROUP BY fact_check_method
        ORDER BY article_count DESC
        """,
        (period_start or "",),
    ).fetchall()

    total = sum(r["article_count"] for r in rows)
    by_method = [
        {
            "method": r["method"],
            "article_count": r["article_count"],
            "percentage": round(100.0 * r["article_count"] / total, 1) if total else 0.0,
        }
        for r in rows
    ]

    unfact = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE has_fact_check = 0"
    ).fetchone()[0]

    return {
        "period": period,
        "period_start": period_start,
        "total_articles_with_fact_check": total,
        "by_method": by_method,
        "unfact_checked_total": unfact,
        "module": "fact_check_method",  # 跟现有 5 模块命名对齐
    }
```

---

## 5. 测试 (test_stats_method.py ~60 行)

| 测试名 | 验证 |
|--------|------|
| `test_fact_check_method_layer_stats_returns_3_methods` | seed 3 种 method 各 5 篇 → 验 by_method 长度 3 + 比例正确 |
| `test_fact_check_method_layer_stats_7d_filters_correctly` | seed 8d 前 5 篇 + 7d 内 5 篇 → 验 7d 只返 5 篇 |
| `test_fact_check_method_layer_stats_all_no_filter` | period=all → 返全部 method 分布 |
| `test_fact_check_method_layer_stats_unfact_total_correct` | seed 10 篇有 fact_check + 5 篇没有 → 验 unfact_checked_total=5 |
| `test_fact_check_method_endpoint_200_with_data` | 端点返 200 + JSON 含 by_method + total |

---

## 6. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| period_start 解析错误 (前端传 '7d' 后端不识别) | 低 | 端点返 400 | 复用 sprint 06 `_period_to_start` helper, 已知支持 '7d'/'30d'/'all' |
| 饼图渲染需 CDN (D3/Recharts) | 低 | 增加前端依赖 | 用原生 SVG, 跟现有 stats.js 一致 (sprint 06 W1 没引 D3) |
| 100% 比例舍入误差 (10+10+10 = 99.9% 显示) | 低 | 视觉不准 | ROUND(..., 1) 单数舍入, 留 0.1% 误差, 总量补 "其他" 兜底 |
| 跟现有 5 块并排布局乱 | 低 | 看板整体不协调 | 复用 `.stats-card` 容器, 第 6 块网格 2×3 布局 |

---

## 7. 实施顺序 (Day 8)

1. `git checkout -b feature/v1.0.25-stats-method` (从 main)
2. 改 `moni/stats/repository.py` + 1 个新端点 `moni/web/routes/stats.py`
3. 改 `moni/web/static/js/stats.js` + `index.html` + `main.css`
4. 写 5 个新测试
5. pytest 全量 0 回归
6. git commit + push + gh pr + merge --squash
7. tag v1.0.25 + push
8. kill 8900 + restart + 5 端点 curl + 浏览器手测

---

## 8. 关联

- 触发 sprint-07 v1.0.22 fact_check_plus 落地 (method=web_search 数据源)
- 跟 sprint-08 B5 (v1.0.23) 协同: B5 让 web_search 失败更少, 看板数据更准
- 跟 sprint-08 简报 web 模式 (v1.0.24) 协同: 简报带 evidence → web_search 增长 → 看板有数据可看
- 申报书 B v4 估分: 看板 method 分布 +1-2 分 (新增 W6 路线图)
