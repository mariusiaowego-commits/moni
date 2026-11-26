# 案例库三层区域 UI 区分方案审核（agy）

**结论：有条件接受（APPROVE WITH CHANGES）**

三档表面明度（`--background` 89.4 / `--muted` 79.9 / `--card` 98.9）+ 圆角（0 / 6px / 12px）+ 有线/无线在 `zone-hierarchy-1440.png` 与 `zone-hierarchy-gray.png` 中**无需区域标题即可一眼分层**；但 mock 仍沿用了旧版 `.cc-metric` 与 `1024 横向盲滑`，且未对齐 `2026-11-26-cases-ui-round2.md` §3 的筛选条规格，需按以下 findings 修正后才可进前端。

---

## Findings（按 P0 / P1 / P2 分级）

| # | 级别 | 问题 | 具体失败场景 | 建议改法（CSS 值） |
|---|---|---|---|---|
| 1 | **P0** | **mock 沿用旧版 `.cc-metric` 单行 `margin-left:auto` 排版，与 worktree 基线不一致** | 实测 `zone-hierarchy-1024.png` 左卡「覆盖场景」与「30+ 个触点」被折成两行错位，右卡「指纹」孤立掉行。worktree `prototype-v2.html:91-99` 已改为双列网格。 | 直接同步 worktree `.cc-metric`：`display:grid; grid-template-columns:1fr 1fr; overflow:hidden;`，`.cc-m + .cc-m { border-left:1px solid var(--border) }`，`.mn { font-size:12px }`，`.mv, .mu { font-size:14px }`。 |
| 2 | **P0** | **1024 档仍用 `overflow-x:auto; scrollbar-width:none` 隐藏横滚，与已拍板规格冲突** | `zone-hierarchy-pixel.md:96` 与 mock `:65-66` 写单行横滚；按 `round2.md:126` 加上右侧「432 条 + 清空」后，1024 下右侧胶囊会被静默裁切，用户不知可滑。 | `@media (max-width:1100px)` 去掉 `overflow-x:auto`，改为 `.filterbar__row { flex-wrap: wrap; gap: 6px 8px; }`（最多两行，+38px）。 |
| 3 | **P1** | **B 区缺 `round2.md` 已拍板的搜索接管、命中数、清空、已选 token 行** | mock 把 `.search` 留在 `.topnav`，导致 `1024.png` 顶栏折行占去 `104px`；且 `.filterbar` 内无 `.fhit`/`清空`/`.ftokens`，研发照 mock 切图会漏掉交互状态。 | 按 `round2.md:129,136`：`/cases` 视图隐藏顶栏 `.search`，把 `.search`、`432 条`、`清空` 放进 `.filterbar__row`，`.ftokens` 放其下且**仅 `.filterbar__row` 吸顶**，`.ftokens` 与 `.filterbar__panel` 留在文档流不吸顶。 |
| 4 | **P1** | **B 区展开态三层嵌套过重，且内层 `.filterbar__panel` 用 `--card` 削弱了 B/C 分层** | 展开时形成「`--muted` 容器(6px) → `--card` 面板(8px) → `--background` 选项(6px)」三层套娃；面板用 `--card`（98.9）与下方 C 区案例卡同明度，且 `max-height:240px` 暖棕大底在首屏视觉权重偏高。 | 面板去掉内层卡片底或改半透明/背景色：`.filterbar__panel { background: var(--background); border-radius: var(--radius-sm); }`，选项 `.opt { background: var(--card); }`，或把 `.filterbar` 展开态外层底改为 `oklch(0.942 0.022 86)`（介于 background 与 muted 之间），保持 B 整体明度低于 C。 |
| 5 | **P1** | **案例卡仍保留 `MN-AI-2611-0004` 编号胶囊，违背 `round2.md:128`** | `1440.png` 与 `1024.png` 每张卡左上角仍占一块 `--muted` 编号胶囊，与「30 秒可扫读、编号移至详情页」冲突。 | 从 `.case-card .cc-top` 删除 `.cc-id`，只留企业名 + 右侧 `.cc-type`。 |
| 6 | **P2** | **A 区 1024 下无细微分隔，宽屏下 4 列数字视觉略散** | 在低亮度屏或 `1440px` 下 `.kpi` 仅靠上下 `1px` 细线界定，4 个数字横向跨度 `1280px`，第 3、4 项（`94`、`10`）与右侧留白边界略弱。 | 保持无框与 `padding: 16px 0`，可在 `.kpi__item + .kpi__item` 加极轻竖线或保持左对齐网格，无需加卡片背景（`border-left: 1px solid oklch(0.848 0.030 75.6 / 0.45); padding-left: 16px;` 可选）。 |
| 7 | **P2** | **方案引用基线与实际仓库路径有误判** | `zone-hierarchy-pixel.md:4` 写 `docs/plans/2026-11-26-cases-ui-round2.md` 不存在（实际在 worktree `/Users/mt16/.codex/worktrees/1323/moni`）。 | 在方案基线注脚注明以 worktree 分支 `feature/v1.0.131-cases-library` 为准，避免后续开发去主仓拿旧版 `prototype-v2.html`。 |

---

## 三行总结

- **我会保留**：A（无框 `--background` + 上下 `1px --border` + `0px` 圆角）/ B（`--muted` 内凹控制面 + `6px` 圆角）/ C（`--card` 抬起 + `12px` 圆角 + hover `--shadow`）的三档明度与圆角编码（灰度下 98.9 / 79.9 / 89.4 清晰可辨，纯 CSS 即可落地）。
- **我会改**：① 同步 worktree `.cc-metric` 双列等字号排版；② 1024 下 `.filterbar__row` 改 `flex-wrap: wrap`（禁横向盲滑）；③ 把 `.search`、`432 条`、`清空`、`.ftokens` 补进 `.filterbar` 并仅让首行吸顶，展开面板改 `--background` 底消除三层套娃。
- **我会砍**：删掉案例卡 `.cc-id`（`MN-` 编号），并砍掉 `.filterbar__panel` 的 `8px` 独立白底卡片壳（避免与 C 区案例卡同明度打架）。
