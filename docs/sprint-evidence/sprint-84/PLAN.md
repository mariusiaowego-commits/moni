---
type: sprint
sprint: 26112604
status: 进行中
---

# Sprint 84 · 案例深度解读（case_insights）后端与试跑

## 背景

Issue #346：`case_insights` 表从 067 迁移起就建好，但从未跑过 LLM，详情页也没有消费方。

## 方案

- `moni/cases/insights.py`：prompt v1（做法 / 风险 / 可复制点 / 适用条件），
  只吃结构化字段，可用 `--with-article` 加 1200 字原文节选；证据不足时返回
  `{"insufficient": true}` 而不是编造。
- 解析容忍 Hermes CLI 在 JSON 后追加的进度行（`tq-s-select · 2%`），取第一个完整对象。
- `moni/cases/backfill_insights.py`：`--dry-run` 默认 / `--apply` / `--limit` / `--ids` /
  `--refresh` / `--with-article` / `--show`；写 `case_insights` 并回填 `cases.insight_id`。
- 幂等键 `(case_id, prompt_version, input_hash)`，重跑覆盖同一行。

## 非目标（下一 PR）

- 详情页 UI 折叠区与 `/api/cases/{id}` 返回 insight。
- 全量 432 条回填（单条约 40s，全量约 5 小时，需排期）。

## 验收

- 单测覆盖：prompt 组装 / 解析（含尾部噪声、insufficient）/ 幂等写入 / CLI dry-run 排除归档与已有。
- 副本库真实试跑 3 条：2 条完整、1 条 insufficient。
