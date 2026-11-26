---
type: sprint
sprint: 26112601
status: 进行中
---

# Sprint 81 · 测试计划

## 后端

1. `stats`：total = 432（副本库口径）、high_value = 137、enterprise_count = 305、
   scenario_count = 10、category_count = 8；过滤占位/归档行后数字不虚高。
2. `facets`：单维计数 = 该维在全库的计数；加另一维条件后计数收敛；排除自身维度。
3. 列表筛选：6 维 + q 任意组合的 total 与 items 一致；`limit/offset` 不破。
4. 详情：返回 industry/scenarios/category/related；`evidence_context` 仅在 verified=1 时有值。
5. 空结果：不存在组合返回 `items=[]`、`total=0`，不 500。

## 前端（浏览器实测）

1. 首屏 1440/1024：无横向滚动；分布标签不截断。
2. 筛选：打开面板 → 多选 → token 出现 → 命中数变化 → 清空 → 恢复全量。
3. 搜索「小程序」：列表与命中数一致；清空恢复。
4. 卡片 → 详情 → 关闭；详情内标签增删、编辑、核验按钮不报错。
5. 亮/暗切换：对比度与截图；控制台无 error。

## 回归

- 定向：`tests/test_cases*.py`、`tests/test_share_cases.py`。
- 全量 pytest（CI）。
