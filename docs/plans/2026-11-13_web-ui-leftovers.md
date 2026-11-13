---
id: 26111302
type: plan
version: v1.0.78
start_date: 2026-11-13
status: 进行中
sprint: "[[sprint-44-web-ui-leftovers-2026-11-13]]"
---

# Sprint 44 PLAN — 遗留两处 P0

- 分支：在 `feat/web-ui-recheck` 上实施（从 origin/main 拉出）
- 实现：agy（w52:p9）· 验收：orchestrator
- 依据：`/private/tmp/ui-audit/recheck-report.md` §3.1 / §3.2

## A. 顶栏图标（`main.css:1540-1547`、`index.html:73-75`）

- `.theme-toggle` → `.btn.btn-ghost.btn-sm`（28px 透明 ghost），删除 34px 白卡与 `background: var(--card)`、`box-shadow`
- 图标 16 → 18px，`stroke-width 1.6 → 1.8`
- palette 圆点 `r=".5"` → `r="1.2"`
- moon path 加 `fill: oklch(from var(--foreground) l c h / 0.12)`（仅该 path，不动 grid 视角 svg）

## B. 配置页 L1/L2（`index.html:2404-2418`、`2593-2601`）

- `.panel-header` 内移除 `.config-tabs`；L1 移到 `.panel-header` 之后、单独一行（左对齐，宽度 100%，`border-bottom` 保持）
- L2（`configTab === 'llm'` 内的网关/用量统计）类名 `.config-tabs` → `.subtab-bar`，按钮 `.tab-btn` → `.subtab`（28px 次级胶囊）
- 左边缘与 L1 对齐（同 x=120）

## 验收（orchestrator 执行）

1. `pytest tests/ -p no:cacheprovider -q` → ≥1739 passed / 2 skipped
2. 顶栏三按钮 computed 高度一致 28px（`--control-h-sm`）；moon 在 light 下可见（截图）
3. palette 四个圆点 screen 半径 ≥0.9px（截图 2x 放大验证）
4. `/config` L1/L2 左边缘 `Math.abs(x2-x1) ≤ 2px`
5. 全站 10 视图 1440 亮/暗截图存 `/private/tmp/ui-audit/after-s44/`，无布局回归
6. 改动文件 ⊆ `css/main.css` + `index.html`
