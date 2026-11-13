---
id: 26111303
type: plan
version: v1.0.80
start_date: 2026-11-13
status: 已完成
sprint: "[[sprint-45-web-ui-chrome-cleanup-2026-11-13]]"
---

# Sprint 45 PLAN — 标签叠加与按钮散落（P0 灾区）

- 分支：`feat/web-ui-sprint45`（worktree `/Users/mt16/dev/moni-worktrees/ui-s45`，base = `main@0d61aca`）
- 实现：agy（Herdr w52:p9）· 设计 reference：`/private/tmp/ui-audit/s45/ref/index.html`
- 依据：`/private/tmp/ui-audit/s45/report.md` + orchestrator 独立实测

## 背景（dad 点名的 AI slop）

`/config` 一个页面在内容出现前堆了 4 层横向 chrome，且表格里标签竖排把行高撑到 337px：

| 位置 | 改前实测 |
|---|---|
| `/config` 抓取源 `chinese_independent_blogs` 行 | **337px**，10 个标签竖排 |
| `/config` 抓取源 `bestblogs` 行 | 212px，6 个标签 |
| `/config` 关键词过滤 | 单行最多 **12** 个 chip，平均 8.57 |
| `/config` 全站「刷新」按钮 | 10 个，`/config` 独占 8 个（`switchConfigTab()` 已自动加载） |
| `/config` 内容起始 y | **298px**（标题@88 / L1@141 / L2@202 / 孤立刷新条@252） |
| `/articles` 行高 | 70px，真因是**时间列** 126px 装不下 `2025-03-21 00:00:00`（不是标签） |

## 落地内容

1. **`.tags-cell` 排版修复**：`font-size:0` + `max-width:calc(100% - 4px)` 让每个 chip 独占一行 → 改 `inline-flex` 横排，单行 ≤2（抓取源）/ ≤3（关键词）+ `+N` 悬停浮层
2. **表格列宽体系**：`table-layout: fixed` + `<colgroup>`，抓取源 8 列 / 过滤 7 列逐列定宽，消除截断
3. **关键词过滤列语义**：`所属源→作用范围`、`模式→类型`、`匹配字段→匹配范围`、`关键词→命中关键词`，新增 `规则说明` 列（展示数据库已有但从未显示的 `notes` 字段，不动 schema）；弹窗表单标签同步
4. **chrome 压层**：删 `/config` 8 条孤立「刷新」工具条；「新建 xxx」并入标题行右侧；`网关/用量统计` 降级为内容区左对齐分段控件 → 内容起始 y 186px
5. **`/articles`**：`formatTime` 去掉秒（`YYYY-MM-DD HH:mm`），时间列 160px，行高 44px
6. **`/fetch`、`/llmJobs`**：各保留 1 个图标刷新；新增抓取配置字段（`feed_url` / `entry_xpath` / `title_xpath` / …）的表单标签与提示

## 验收（orchestrator 独立实测，8902 预览）

| 项 | 结果 |
|---|---|
| pytest | **1743 passed / 2 skipped** |
| 抓取源行高 | 18 行全部 **44px** |
| 状态徽标 | `td 93px`，`启用/停用` 完整，0 截断 |
| 显示名列 | 174px，18 行 0 截断 |
| 关键词过滤规则说明 | 376px，7 行 0 截断；`已停用` 双徽标完整 |
| 文章时间列 | 160px ×100 单元格，0 截断，`2025-03-21 00:00` |
| 标签→操作列间距 | 12px |
| `/config` chrome | 2 层（`panel-header@88` + `config-tabs@125`），内容 186px |
| 分段控件 | `x=120, y=186`，与下方卡片左对齐 |

截图：`/private/tmp/ui-audit/s45/final/`（light/dark × 4 场景）
