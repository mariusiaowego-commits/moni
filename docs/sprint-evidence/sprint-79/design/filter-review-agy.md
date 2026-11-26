# 案例库筛选 UI 方案独立审核（agy）

**结论：REQUEST CHANGES**

整体方向（模式 A：顶部胶囊行 + `in-flow` 单开面板 + 条件 token 行）正确，视觉 token 与 `prototype-v2.html` 一致；但在 **1024×768 布局/sticky 高度、企业大维度可扩展性、URL/后端契约对齐、键盘焦点管理** 存在 4 个阻断级（P0/P1）缺陷，需修改后再进开发。

---

## 1. Findings 表

| 编号 | 严重度 | 位置 | 问题 | 建议改法 |
|---|---|---|---|---|
| F1 | **P0** | §2.4 / mock `:46, 142-159` | **方案与 mock 矛盾导致 1024×768 半屏被占满**：§2.4 写「胶囊行 sticky，面板和 token 行不 sticky」，但 mock 把 `.ftokens` 和 `.fpanel` 放在 `position: sticky; top:56px` 的 `.filters` 容器内。1024×768 下顶栏(56)+筛选行(46)+token(38)+面板(200)=**340px（占视口 44%）**随滚动锁死，列表只剩 ~420px。 | 将 `.ftokens` 与 `.fpanel` 移出 sticky 容器，仅 `.filters__row` 吸顶；或展开面板时临时取消 `.filters` sticky。 |
| F2 | **P0** | §5.4 / mock `:278, 399-418` | **键盘多选与 Esc 焦点全部失效**：① Space/Enter 勾选 `.fopt` 后 `toggleVal` 调 `render()` 重建 DOM 并把焦点踢回 `.fchip`，无法连续键盘多选；② `ArrowDown/Up` 移动焦点但未更新 `tabindex="0/-1"`，roving tabindex 形同虚设；③ `Esc` 用 `querySelector('.fchip[aria-expanded="false"]')` 永远聚焦到第一个胶囊（企业），而非触发关闭的胶囊；④ §5.4 写的行业 `←/→` 二级列在 DOM 和 mock 中根本不存在。 | `toggleVal` 在面板内勾选时保留焦点在当前 `data-val` 选项上；方向键同步切换 `tabindex`；`Esc` 按 `lastOpenDim` 精准回位焦点；砍掉行业二级列后同步删 `←/→`。 |
| F3 | **P1** | §2.3 / §5.3 | **企业维度（116+ 家）放进 200px 限高滚动面板是把复杂度转嫁给用户**：真实库 `064_enterprises.sql` 已种子化 116 家企业、`cases.subject` 有 356 个值。1024 下 200px 高最多露 5 行（~15 个），无搜索也无 Top 截断，用户要在 40 行小窗里盲找。 | `企业` 面板默认只展示有案例的 Top 15–20（按命中数降序），面板头加一个极简过滤框或「其余请用顶部搜索」提示，禁止百条平铺硬滚。 |
| F4 | **P1** | §4 / §5.5 | **URL 与后端契约多处冲突**：① 方案用中文值（`ent=腾讯&ind=互联网/社交&type=智能体&ref=高`），而后端 `routes/cases.py` 与 `064/065` 用稳定 code/enum（`ent_tencent`、`ai_adoption`、`high`），中文名改名即破且 `互联网/社交` 的 `/` 与路径语义混淆；② §4 写 `?query`，mock `:374` 写 `#hash`；③ §4 写 `time=30d`，mock `:196` 写 `month=2026-11`。 | URL 统一用稳定 ID/枚举（`ent=ent_tencent&ind=银行&scene=llm_agent&type=ai_adoption&ref=high&time=30d`）+ `?query`；后端抽公共 `_build_cases_where()` 同时服务 `GET /api/cases` 与 `GET /api/cases/facets` 保证同口径。 |
| F5 | **P1** | §2.3 / mock `:52-53` | **1024×768 隐藏滚动条横滚不可发现**：`.fchips` 设 `overflow-x:auto; scrollbar-width:none`，窄屏下右侧胶囊（价值/时间）被静默裁切，用户看不出还能右滑。 | 1024 下允许 `.fchips` 换行（最多两行仅多 34px），或右侧加渐变遮罩 + 滑动提示；推荐直接允许换行，消灭横向盲区。 |
| F6 | **P1** | §0 / §2.2 / §5.5 | **「7 个维度 vs 6 个胶囊」文档自相矛盾**：§0 声称 7 个维度（含关键词），§2.2/§3/§5.1/§5.5 和 mock 只有 6 个（无关键词）。真实库 816 个关键词中 90% 只出现 1 次，本就不适合做 facet。 | §0 删掉「关键词」维度，明确「6 个结构化筛选 + 1 个全文搜索框（覆盖关键词/机构/正文）」。 |
| F7 | **P2** | §4 / §5.1 / mock `:350` | **违反 `copy-review.md` 已定规则**：① mock 列表卡首位仍放了 `cc-id`（`MN-AI-2611-0004`），`copy-review.md` 已裁定移出列表卡；② token 行多了一个「复制链接」按钮（`.fcopy`），但 `replaceState` 已同步浏览器地址栏，且详情页也有「复制链接」，两处语义冲突。 | 列表卡移除 `cc-id`；删掉 token 行的 `.fcopy` 按钮（§4 明确清空入口不设第三个，同理分享/复制也不该泛滥）。 |
| F8 | **P2** | §2.2 / mock `:48, 134-147` | **搜索框位置与 `prototype-v2.html` 冲突**：基线原型 `.search` 在 `.topnav` 右侧，本方案移进 `.filters__row`，合入 `index.html` 时若不明确替换关系会出现两个搜索框。 | 在方案中显式写明「案例库视图下隐藏顶栏全局搜索，由 `.filters__row` 内的 `.search` 接管」。 |
| F9 | **P2** | §5.5 | **`GET /api/cases/facets` 缺少「非零过滤 + 排除自身维度」口径说明**：SQLite 在 432 案例下全量 GROUP BY 耗时 < 5ms 完全可行，但若不按标准 facet 语义「算某维度计数时排除该维度自身筛选条件」，用户单选一个企业后该维度其他企业计数全变 0 就没法多选了（mock `:259` `filtered(openDim)` 做对了，但 §5.5 后端契约漏写）。 | 在 §5.5 明确写入：后端 `facets` 接口对每个维度 `d` 应用除 `d` 以外的其余条件（与 mock `filtered(openDim)` 一致），且只返回 `n > 0` 或当前已选的选项。 |

---

## 2. 必须改的 3 条（按优先级）

1. **修复 sticky 范围与 1024×768 布局（F1 + F5）**：`.ftokens` 和 `.fpanel` 移出 sticky 容器，避免展开面板占死 340px/768px；1024 下 `.fchips` 改自然换行而非 `scrollbar-width:none` 盲滑。
2. **修复键盘焦点管理（F2）**：面板内 `Space/Enter` 多选保持焦点不回跳、方向键同步更新 `tabindex`、`Esc` 精准回到关闭前的那个 `.fchip`。
3. **对齐 URL 与后端数据契约（F4 + F9）**：URL 参数改用稳定 code/ID（`ent_id` / `category_code` / `scenario_code` / `high|medium|low`）并统一 `?query`；后端用同一 `_build_cases_where()` 驱动 `GET /api/cases` 与 `GET /api/cases/facets`（排除自身维度计算计数）。

---

## 3. 可以砍的 3 条

1. **砍掉行业二级级联（`← / →` 切列 & `ind=一级/二级`）**：P1-1 一级行业仅 5 类（银行/保险/证券/互联网/其他），432 条数据下 `保险 2`、`证券 2`，做二级列与 `/` 编码纯属过度设计，只保留一级行业即可。
2. **砍掉「关键词」作为独立筛选维度（修正 §0）**：816 个词 90% 为单次长尾词，做胶囊/面板毫无聚合价值，交给顶部搜索框足够。
3. **砍掉 token 行的「复制链接」按钮（`.fcopy`）**：地址栏已通过 `history.replaceState` 实时同步，用户直接复制浏览器 URL 即可；且避免与详情页的「复制链接（原文链接）」混淆。

---

## 4. 三行摘要

1. 审核结论：**REQUEST CHANGES**；模式 A（顶部胶囊 + `in-flow` 面板 + token 行）方向正确且 token 一致，但需先修 4 个 P0/P1 阻断项。
2. 必须改：将 `.ftokens`/`.fpanel` 移出 sticky 避免 1024 半屏锁死、补齐面板多选保持焦点与 `Esc` 精准回位、URL/API 改用稳定 code 并以单 `_build_cases_where()` 保证列表与计数同口径。
3. 可砍项：行业二级级联（只留 5 个一级行业）、`关键词` 独立筛选维度、token 行的重复「复制链接」按钮。
