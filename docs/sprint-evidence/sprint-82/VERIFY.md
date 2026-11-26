---
type: sprint
sprint: 26112602
status: 进行中
---

# Sprint 82 · 验收记录

## 1. 命令与输出

```text
$ /usr/local/bin/python3 -m pytest tests/test_stats.py tests/test_cases_p0_v2.py \
    tests/test_cases_v3_api.py tests/test_cases.py -p no:cacheprovider -q
146 passed in 19.33s
```

```text
$ /usr/local/bin/python3 -m pytest tests/test_cases_frontend_contract.py \
    tests/test_frontend_microinteraction.py tests/test_frontend_safe_href_and_vendor.py \
    tests/test_frontend_motion.py tests/test_web_static_headers.py -q
61 passed in 2.41s
```

```text
$ /usr/local/bin/python3 -m pytest tests/test_share_cases.py tests/test_share_manifest.py \
    tests/test_stats_geography_api.py -q
73 passed in 4.38s
```

审计修复后复跑：

```text
$ /usr/local/bin/python3 -m pytest tests/test_stats.py tests/test_cases_p0_v2.py \
    tests/test_cli_verify_all.py tests/test_cases_v3_api.py tests/test_cases.py -q
151 passed in 25.65s
```

新增用例：

- `tests/test_stats.py::TestCasesDashboard::test_archived_excluded_and_counted`（total −1、
  `archived_total=1`、榜单剔除、与 `CaseRepository.stats()` 相等）。
- `tests/test_stats.py::TestCasesDashboard::test_archived_excluded_from_extract_and_funnel`。
- `tests/test_cases_p0_v2.py::TestSearchFtsV2::test_archived_hidden_from_search`。

## 2. UI 实测

生产 `archived=0`，`/stats` 不渲染「归档」后缀（x-if 不成立）；`/api/stats/cases-dashboard`
返回 `archived_total: 0`。归档数 >0 时的展示走同一条 `stats-card-sub`，不新增布局块。

（最终 8900 端点实测在合并部署后补本条。）

## 3. 数字可复算

```text
$ curl -s :8900/api/stats/cases-dashboard | jq '.total_cases, .archived_total'
$ curl -s :8900/api/cases/stats | jq '.total'
# 两处 total 相等；archived_total = SELECT COUNT(*) FROM cases
#   WHERE subject != '__processing__' AND case_lifecycle='archived'
```

## 4. failed / skipped

- `skipped`：`ruff check` 未执行（本机 Python 环境无 ruff 模块），由 CI 兜底。
- `skipped`：`_geo_distribution` 的归档过滤无独立单测（同口径靠 `active_case_clause` 覆盖）。
- `skipped`：CI 结果在 PR 开出后回填 §5。

## 5. CI

（PR 开出后回填 run id 与通过数。）

## 6. 独立审计（agy，只读）

结论 APPROVE WITH NITS，2×P1 已修：

- P1 `get_by_article` 未排归档/占位（`moni/cases/repository.py:936`）→ 改为复用 `active_case_clause()`，
  补断言 `get_by_article("art_a1") == []`。
- P1 `moni cases verify-all` 会把 pending 的归档案例写回 `verified`（`moni/cli/main.py:851-871`）→
  `base_where` 与 `pending_after` 都排 archived，新增 `test_verify_all_skips_archived`。
- P2 6 处手写口径（相关案例 + 4 个 aggregate）→ 全部改用 `active_case_clause()`。
- P2 测试缺口 → 补 2 字短词 LIKE 分支、`total_institutions` 断言。
- P2 回填脚本未排 archived → taxonomy / metrics / evidence 三条 SELECT 加 `case_lifecycle != 'archived'`；
  `tests/test_cases_taxonomy.py` 期望同步改为「archived 不参与回填」（matched 3 → 2）。
