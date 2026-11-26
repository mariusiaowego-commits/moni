---
type: sprint
sprint: 26112602
status: 进行中
---

# Sprint 82 · 案例「在用」口径统一

## 问题

同一个「案例数」在 5 处 SQL 各写一份 where，archived 软删行只在 `/cases` 与分享简报里被排除：

1. `/cases` 列表 / facets / `/api/cases/stats`：排 `__processing__` + 排 `archived`（`moni/cases/repository.py:87`）。
2. `/api/stats/cases-dashboard`：只排 `__processing__`（`moni/stats/repository.py:734`）。
3. 抽取层 `extract_layer_stats`：两者都不排（`moni/stats/repository.py:309`）。
4. 数据漏斗 `data_funnel_stats`：只排 `__processing__`（`moni/stats/repository.py:999`）。
5. FTS 搜索 `search_fts`：只排 `__processing__`（`moni/cases/repository.py:1090`）——归档案例仍可被搜到。

生产库当前 `archived = 0`，所以今天数字一致，属潜在分叉；只要第一次软删就会同页分裂。

## 方案

- `moni/cases/repository.py` 新增纯函数 `active_case_clause(alias, include_archived=False)`，输出
  `subject != '__processing__' [AND case_lifecycle != 'archived']`，别名可选。
- 上述 5 处 + `high_value_ranking` + `_geo_distribution` 全部复用该函数；`/cases` 显式传
  `case_lifecycle=archived` 时仍可查看归档（`include_archived=True`）。
- `/api/stats/cases-dashboard` 新增 `archived_total`（同 period 口径）；`/stats` 卡片只在
  `archived_total > 0` 时追加「· 归档 N」，零值不占位。
- 分享简报（`moni/share/case_brief.py:170`）已排 archived，保持不动。

## 非目标

- 不改 `case_lifecycle` 状态机与迁移。
- 不重算历史数字：当前 archived=0，改动前后 total 都是 432。
- 不把「归档」做成 `/cases` 的 KPI。

## 验收

- 单测：归档 1 条后 dashboard `total -1` / `archived_total 1` / 榜单剔除 / 与 `CaseRepository.stats()` 相等；
  抽取层、数据漏斗同口径；FTS 搜不到归档案例。
- CI `pytest · py3.12` 绿；8900 上 `/api/stats/cases-dashboard` 与 `/api/cases/stats` 的 total 相等。
