---
id: 26111204
type: plan
version: v1.0.76
start_date: 2026-11-12
status: 进行中
sprint: "[[sprint-39-web-ui-token-base-2026-11-12]]"
---

# Sprint 39 PLAN — 栅格与 Token 收敛

- 基线：`main @ 7375e84`，分支 `feat/web-ui-polish`
- 实现方：agy（Herdr w52:p9）；编排与验收：orchestrator（本 session）
- 依据：unslop `SKILL.md` Step 5 第 1/2 遍（Spacing → Color）+ 体检报告 UI-01/UI-07

## 1. Token 层（新增，不删旧变量）

在 `moni/web/static/css/main.css` 顶部 `:root` 增补（暗色同名变量在 `.dark` 覆盖）：

```
--space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px;
--space-5: 20px; --space-6: 24px; --space-8: 32px;
--radius-xs: 4px; --radius-sm: 6px; --radius-md: 8px; --radius-lg: 12px; --radius-xl: 16px;
--control-h-sm: 28px; --control-h-md: 36px;
--z-dropdown: 100; --z-sticky: 200; --z-modal: 500; --z-toast: 1000;
--link: …; --tag-chip-fg: …; --chart-1..5: …;
```

## 2. 具体改动点（逐条可核对）

| # | 位置 | 现状 | 目标 |
|---|---|---|---|
| 1 | `main.css:492` | `.btn-sm { padding: 5px 11px }` | `padding: 4px 10px; height: var(--control-h-sm)` |
| 2 | `main.css:224` | `.topnav-links a { padding: 9px 14px }` | `padding: 6px 14px; height: var(--control-h-md); display:inline-flex; align-items:center` |
| 3 | `main.css:230` | 激活态 `box-shadow: 0 2px 8px oklch(...)` | 1px hairline（`var(--border)`）+ 表面色阶，无 glow |
| 4 | `main.css:801-806` | `.link` 系列裸 hex（#4a3a8c / #D1B3FF / #F2E6CE） | 换 `var(--link)` / `var(--link-hover)`，在 `:root`/`.dark` 定义 |
| 5 | `main.css:626-629` | `.tag-chip` 裸 hex | 换 `var(--tag-chip-fg)` |
| 6 | `main.css:950-971` | `#0f172a / #64748b / #f1f5f9` | 换 `var(--fg-strong) / var(--fg-muted) / var(--surface-2)` |
| 7 | `main.css:3059-3067` | 图表 4 色裸 hex | 换 `var(--chart-user) / --chart-fact / --chart-human / --chart-web` |
| 8 | `main.css:3014/3159/3188/3218` | `var(--accent, #4a90e2)` 兜底 | 统一兜底变量，避免裸 hex |
| 9 | `.data-table` 数字列 | 无 | `font-variant-numeric: tabular-nums` |
| 10 | 全文 | `padding/margin: …奇数px` | 收敛到 4px 网格；1px 边框、2px hairline、字体行高例外需在 PR 说明 |

## 3. Acceptance（orchestrator 亲自执行）

1. `/usr/local/bin/python3 -m pytest tests/ -p no:cacheprovider -q` → 基线 1598+ passed / 2 skip，不得新增失败。
2. 组件规则裸 hex 归零：`rg -n ':\s*[^;]*#[0-9a-fA-F]{6}' moni/web/static/css/main.css | rg -v '^\s*[0-9]+:\s*--'` 结果为空（hex 仅允许出现在变量定义行）。
3. 核心组件无奇数内边距：对 `.btn-sm`、`.topnav-links a`、`.tag-chip`、`.panel-header`、`.data-table th/td` 逐一 computed-style 取值，全部落在 `{0,2,4,6,8,10,12,14,16,20,24,32}` ∪ {1px 边框}。
4. 导航激活态 `getComputedStyle(...).boxShadow` 不含非 `none` 的彩色投影。
5. 1440×900 亮/暗截图：10 个视图逐页留存于 `/private/tmp/ui-audit/after-s39/`，与 `shots/*_desktop_*.png` 对照，无布局回归（表格列宽、卡片高度、按钮换行）。
6. 改动文件集合 ⊆ `moni/web/static/css/main.css`、`moni/web/static/js/theme.js`。

## 4. 风险

| 风险 | 缓解 |
|---|---|
| 全局变量替换引发大面积色差 | 只替换已有裸 hex 的等价语义变量，不重设色相；逐视图截图比对 |
| 字体/行高变化导致表格换行 | 数字列只加 tabular-nums，不改字号；表格截图核对行高 |
| 与 sprint-38（`feat/xbot-author-block-v1.0.75`）冲突 | 该分支改动在 `moni/fetch/` 与 `moni/db/`，文件集合无交集；合并前 rebase `main` 复查 |

## 5. 后续阶段（已排期，不在本 sprint）

- Phase 2：按钮组件合并（`.link-btn` → `.btn-link`）、操作列 ≤2、危险操作降权、emoji → Lucide SVG。
- Phase 3：Pin 列/Pin Tab 去重、fresh+new 合一、双「刷新」、文章详情弹窗单 Primary + 固定 footer、z-index 标尺落地。
- Phase 4：PC 视口一致性（1280/1440/1920）、对比度 ≥4.5:1、空状态与焦点态。**移动端不做。**
