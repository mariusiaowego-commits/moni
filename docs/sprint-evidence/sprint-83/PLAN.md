---
type: sprint
sprint: 26112603
status: 进行中
---

# Sprint 83 · 案例归档口径收尾（Issue #347 / #348）

## 背景

Sprint 82 统一了在线读路径的 archived 口径，留下两个小项：

- #348：`CaseRepository.restore_manual_keywords` 按 (article, subject, type, action) 查案例时
  没排 archived，归档案例仍会接收恢复的人工关键词（无 LLM 成本、无数字影响）。
  issue 原文说的「两个离线 CLI 未排」是误读 grep，两处早已有 archived 过滤，已在 issue 更正。
- #347：`_geo_distribution` 改成 `JOIN cases` 后没有独立单测。

## 改动

1. `moni/cases/repository.py:520-526` 查询加 `AND case_lifecycle != 'archived'`。
2. `tests/test_cases_pipeline_tagging.py::test_restore_manual_keywords_skips_archived`。
3. `tests/test_stats.py::test_geo_distribution_excludes_archived`（归档后地理计数归零）。

## 验收

- 定向 pytest 全绿；CI `pytest · py3.12` 绿。
- 不改表、不改 API、不改前端。
