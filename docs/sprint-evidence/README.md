# Sprint Evidence Archive

Local evidence retained from completed sprints. These are verification
artifacts, not source files; nothing here is imported or served by the app.

## Contents

| Directory | Source | What it proves |
|---|---|---|
| `sprint-24/` | `moni-worktrees/26101001-render-hygiene/docs/sprint-24-evidence/` | v1.0.44 render hygiene: online/offline/reduced-motion screenshots plus flash-timing, font-axis, font-platform and offline probe JSON |
| `sprint-25/` | `moni-worktrees/26101201-motion/docs/sprint-25-evidence/` | v1.0.45 motion work: before/after FPS runs, B-group verification, mutation probes |
| `sprint-27/` | sprint-27（中文独立博客源） | v1.0.60 / v1.0.62 中文独立博客源：独立审计报告 |
| `sprint-27-hotfix/` | sprint-27 hotfix（失败率熔断 + 配置链修复） | v1.0.62 hotfix：审计 + 实施记录 |
| `sprint-30/` | sprint-30（xbot_feed 回填与迁移 039） | v1.0.66 xbot_feed 真实运行：203 导出 / 71 入库 / rerun 0（三天数据）+ 真实库副本上的 migration 039 实测 |
| `reference-reports/` | `moni-ref-fact-check/*REPORT.md` | Read-only research for sprint-16 ($name placeholder fix) and sprint-16.5/16.6 fact-check hardening |

> **文件命名约定（2026-11-05 起）**：`sprint-27/` 只有 `AUDIT.md`；
> **sprint-30 起新增 `RUN.md`** —— 真实运行记录（人读），配套 `*.json`（机器可读统计）。
> 详见 `sprint-30/RUN.md`。

## Reference reports (逐份索引)

| 文件 | 内容 | 状态 |
|---|---|---|
| `reference-reports/REFERENCE-REPORT.md` | sprint-16 P1 —— `$name` 占位符修复的只读调研 (131 行) | 已归档, 链接已改为仓库相对路径 |
| `reference-reports/SPRINT-16.5-REFERENCE-REPORT.md` | sprint-16.5 —— 事实核查占位符渲染链调研 | 同上 |
| `reference-reports/SPRINT-16.6-REFERENCE-REPORT.md` | sprint-16.6 —— `【无需核查】` 空 evidence 误报调研 (353 行) | 同上 |
| `reference-reports/SPRINT-16.6-FOLLOWUP-REPORT.md` | sprint-16.6 跟进 —— Gate 1 判定逻辑改造方案 (179 行) | 同上 |

> 原报告里的 `file:///Users/mt16/...` 本机链接在 GitHub 上全部失效, 2026-10-24 已统一改成仓库相对路径 (报告内链到 `../../../moni/...`, 报告间互链到同目录文件名)。

## Provenance

- Archived 2026-10-23 from temporary worktrees before removing them.
- The sprint-24 and sprint-25 files were verified byte-for-byte against
  their source directories (`shasum -a 256`); the four reference reports
  were verified with `cmp`.
- Probe scripts themselves live in `scripts/evidence/` on `main`.
- **2026-10-24 复核**: 三个源工作目录 (`moni-worktrees/26101001-render-hygiene`,
  `moni-worktrees/26101201-motion`, `moni-ref-fact-check`) 已在当次清理中删除,
  **上面的 hash 复核无法再现场重跑** —— 本文档记录的结论以 2026-10-23 的验证结果为准。

## Not archived

- Code, tests and plan documents belong in their normal tracked locations.
- Build artifacts, caches and unrelated working-tree files were not copied.
