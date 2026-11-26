---
type: sprint
sprint: 26112601
status: 进行中
---

# Sprint 81 · 技术规格

## 1. API

### GET /api/cases

新增可选参数（与现有参数并存）：

| 参数 | 语义 |
|---|---|
| `q` | LIKE：subject / subject_raw / summary / counterparty / 企业名 |
| `enterprise` | `COALESCE(subject_enterprise_id, 'other') IN (...)`；未归一主体统一归 `other` |
| `industry` | `enterprises.industry_l1 = ?` |
| `scenario` | EXISTS `case_scenario_links.scenario_code = ?` |
| `category` | `cases.case_category = ?` |
| `month` | `substr(case_date,1,7) = ?` |

统一过滤：`subject != '__processing__' AND case_lifecycle != 'archived'`。

### GET /api/cases/facets

入参同上（不含 limit/offset）。返回每个维度的选项与计数，**计算某维度时排除该维度自身条件**，
其余条件（含 q）照常生效：

```json
{
  "enterprise": [{"value": "e_tencent", "label": "腾讯", "count": 10}],
  "industry":   [{"value": "银行", "label": "银行", "count": 121}],
  "scenario":   [{"value": "llm_agent", "label": "大模型与智能体", "count": 73}],
  "category":   [{"value": "ai_adoption", "label": "AI与大模型应用", "count": 72}],
  "value":      [{"value": "high", "label": "高", "count": 137}],
  "action":     [{"value": "launch", "label": "上线", "count": 432}],
  "month":      [{"value": "2026-11", "label": "2026-11", "count": 33}]
}
```

### GET /api/cases/stats

在现有字段上补：

- `total` 改为过滤占位行/归档后的计数；
- `high_value`：`reference_value='high'`；
- `enterprise_count`：`COUNT(DISTINCT COALESCE(subject_enterprise_id, subject))`；
- `scenario_count`：`COUNT(DISTINCT scenario_code)`；
- `category_count`：`COUNT(DISTINCT case_category)`。

`month` 过滤 `case_date GLOB 'YYYY-MM*'`，格式脏值（如 `relativ`）不产出选项。
`by_*` 数组保留。

### GET /api/cases/{id}

补 `industry_l1`、`enterprise_name`、`category_name`、`scenarios: [{code,name}]`、
`related: [{case_id, subject, summary, reference_value}]`（同企业优先，最多 4 条）。

## 2. 前端

- 状态：`cvQ / cvSel{dim} / cvStats / cvFacets / cvItems / cvTotal / cvPanel / cvDetail`。
- URL：`/cases?q=…&enterprise=…&industry=…&scenario=…&category=…&value=…&month=…`；
  `replaceState` 同步，详情用 `?case=` 深链。
- 概览数字与分布条恒为全库截面；点击分布行 = 应用对应筛选。
- 卡片：企业 / 摘要（结论）/ 指标两格 / 参考依据（2 行截断）/ 著录行（行业 · 动作 · 时间 + 高价值）。
- 列表首屏 100 条，`加载更多` 以 `offset` 追加，避免 432 条一次渲染。
- 详情：90vw / max 1280 / 62-38 双栏；左栏结论、指标、摘要、依据、证据（`evidence_verified` 徽章 +
  `evidence_context`），右栏基本信息、标签、相关案例、分享、原文。

## 3. Theme

- 只用 `--background/--foreground/--card/--muted/--muted-fg/--border/--accent/--shadow`。
- 列表不再输出类型/价值彩色药丸；详情价值用文本 + `.cv-flag`。
- 暗色由 `main.css .dark` 覆盖 token，页面无新增暗色规则。
