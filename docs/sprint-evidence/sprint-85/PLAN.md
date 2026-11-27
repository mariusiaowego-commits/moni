---
type: sprint
sprint: 26112701
status: 进行中
---

# Sprint 85 · case_action 口径修复与规则回填（Issue #351）

## 根因

1. `CASE_ACTION_KEYWORDS` 只有 5 类且缺「收购 / 并购 / 私有化 / 合并 / 重组」
   等词，任何未命中的文本都兜底成 `launch`；
2. 存量 432 条是迁移 062 之前写入的，`case_action_source` 被迁移补成 `default`；
3. `normalize_case_action_with_source` 在 `raw_action='launch'` 时直接返回，
   不再看上下文，模型把一切标 launch 时无法纠正。

## 方案（不调 LLM、不改 schema）

- 扩充 5 类关键词：`exit` 用「关停/停止服务/下线…」，`transform` 吸收
  「收购/并购/私有化/合并/重组/整合/转型/升级」，`pilot`「试点/内测/灰度」，
  `scale`「规模化/全面推广/扩容」，`launch`「上线/推出/成立/签…」。
- 上下文优先级固定为 `exit > transform > pilot > scale > launch`；
  当模型给 `launch` 而上下文更具体时，以上下文为准。
- 新增 `moni/cases/backfill_actions.py`：按 summary + evidence + 标题重算，
  默认 dry-run，`--apply` 才写库，`--ids/--limit` 可控。
- 前端标签 `transform` 由「转型」改为「转型/整合」，覆盖 M&A 语义。

## 非目标

- 不新增动作枚举（`cases.case_action` 有 CHECK 约束，加类需重建表）。
- 不重跑案例抽取、不调 LLM。

## 验收

- 生产 dry-run：需改动 312 / 432，5 类分布非零。
- CLI 单测覆盖：M&A→transform、关停→exit、上下文压过 launch、显式枚举优先、
  未命中兜底、dry-run 不写库、归档排除。
- 8901 副本库截图：卡片上出现「试点 / 规模化 / 转型/整合 / 退出」。
