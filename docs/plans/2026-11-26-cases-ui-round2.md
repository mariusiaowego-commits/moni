# 案例库第二轮：指标排版 / 原文证据 / 筛选 / 分享简报

- 日期：2026-11-26
- 状态：§1、§1.1、§2、§4 已落地；§3 方案已拍板，待按 mock 落地
- 相关：`docs/sprint-evidence/sprint-79/`（第一轮验收）、`design/prototype-v2.html`

## 1. 指标排版（已改，可直接看）

**问题**：列表卡 `.cc-metric` 把「标签 12px / 数值 22px / 单位 13px / 第二个标签 12px …」
塞在同一行，字号来回跳，读起来像四个重点。

**改法**：一个指标 = 一列。标签独立一行（12px 灰）；数值与单位同一行、同为 14px，
只靠字重区分（数值 600，单位 400 灰）。两列之间用 1px 分隔线，不再用 `margin-left:auto` 硬推。

```
互动日活            覆盖场景
3000 万 / 日        30+ 个触点
```

截图：`docs/sprint-evidence/sprint-79/render/prototype-metric-1440.png`。
详细页的 `.hero-metrics` 本来就是三行结构（标签 / 数值 / 说明），本轮不动。

## 1.1 顶部 stats-strip（本轮改）

**问题**：4 张 KPI 卡各自 3 行（28px 数字 / 12px 标签 / 12px 副文案），而且一卡混两种口径：
`137 高价值` 同卡写「中 199 · 低 96」、`429 / 3` 在一个数字位塞两个值、`30+` 与副行重复。

agy 出 3 案、pixel 给规格，融合后选「一卡一指标、只留两级字号」：

| 卡 | 数字 | 标签 |
|---|---|---|
| 1 | 432 | 案例 |
| 2 | 137 | 高价值 |
| 3 | 94 | 覆盖企业 |
| 4 | 10 | 业务场景 |

- 删除第三行 DOM（`.stat .d`），从结构上杜绝再塞第二口径；高/中/低分布下沉到「价值」筛选面板。
- 数字 28px / 600 / tabular-nums / line-height 1.1；标签 13px / 400 / `--muted-fg`，间距 4px。
- 卡 padding 14/20（1024 下 12/16），无阴影；1440 与 1024 都保持 4 列不换行，<800px 才 2×2。
- 口径：`94` 是生产库副本迁到 069 并跑完 `backfill_taxonomy` 后的
  `COUNT(DISTINCT enterprise_id)=94`，不是 `enterprises` 表的 116 家。
  `10` 是 `case_scenario_links` 的 10 个场景（9 个业务场景 + `other`）。

原始建议归档：`design/stats-strip-agy.md`、`design/stats-strip-pixel.md`。
截图：`render/prototype-zones-1440.png`、`render/prototype-zones-1024.png`（三档表面融合后）。

复算（生产库副本，迁移到 069 并跑完 `backfill_taxonomy`）：

```bash
sqlite3 /private/tmp/ev/news.db "
  select count(*) from cases;
  select count(*) from cases where reference_value='high';
  select count(distinct enterprise_id) from case_enterprises;
  select count(distinct scenario_code) from case_scenario_links;"
# 432 | 137 | 94 | 10
```

## 2. 「原文证据」为什么只有一行

**实测（生产库 432 条案例）**

```sql
SELECT COUNT(*), ROUND(AVG(LENGTH(evidence_snippet)),1), MAX(LENGTH(evidence_snippet)),
       SUM(CASE WHEN LENGTH(evidence_snippet)<40 THEN 1 ELSE 0 END)
FROM cases;
-- 432 | 62.2 | 142 | 56
```

**原因有三层，都不是"模型能力不行"**

1. 提示词就要求短：`moni/llm/prompts/case_extraction.txt:22` 第 10 条写的是
   「直接引用文章中支撑该案例的 **1-2 句**原文」。平均 62 字就是 1-2 句。
2. 入库截断：`moni/cases/normalize.py:1114` 把 `evidence_snippet` 截到 400 字（实际远没到）。
3. 它没有"被校验"：系统只要求模型挑一句，**没有校验这句是否真的在原文里**。
   逐字比对生产库结果：

   | 情况 | 条数 | 占比 |
   |---|---|---|
   | 整句逐字命中 `articles.raw_content` | 272 | 63.0% |
   | 只有前 12 字命中（拼接/省略/改写） | 135 | 31.3% |
   | 完全找不到 | 25 | 5.8% |

   指标层反而更严：`backfill_metrics.py` 要求 `evidence_quote` 在证据句或正文窗口里
   逐字命中，否则丢弃。所以"金额/日活"比"证据句"更可信。

**建议改法（0 次 LLM，纯程序）**

| 层 | 内容 | 来源 |
|---|---|---|
| 证据句 | 现有 1-2 句，加 `evidence_verified`（逐字命中 1 / 否 0） | 现有 + 子串校验 |
| 上下文 | 命中位置前后各 200 字，程序截取，不生成 | `raw_content` + `str.find` |
| 原文定位 | 详情页「查看原文」跳到高亮位置（`#:~:text=` 或站内高亮） | 现有 `article_url` |

界面上：未命中时标「模型转述」而不是假装是原文；证据区默认 3 行，超出折叠。
这样既不增加 LLM 成本，也把"为什么算证据"讲清楚。

**已落地（migration 069）**

- `moni/db/migrations/069_case_evidence.sql`：`cases.evidence_verified` / `cases.evidence_offset`。
- `moni/cases/evidence.py`：空白归一的逐字匹配 + 命中位置前后各 200 字上下文。
- 抽取时写标记（`moni/cases/pipeline.py`）；详情读取附带 `evidence_context`
  （`moni/cases/repository.py` → `moni/web/routes/cases.py`）。
- 存量回填：`python3 -m moni.cases.backfill_evidence --db <db> [--apply]`。
- 生产库副本实测（001→069 + taxonomy 回填）：432 条中 **265 条命中（61.3%）**，
  167 条标「模型转述」；`MN-AI-2611-0004` 命中偏移 2696。
- 测试：`tests/test_cases_evidence.py` 15 passed；受影响集 144 passed。
- 原型：详情页「✓ 原文引用」徽章 + 上下文高亮 + 判定方式说明，
  截图 `render/prototype-detail-evidence-1440.png`。

复算：`PYTHONPATH=. python3 -m moni.cases.backfill_evidence --db /private/tmp/ev/news.db`
→ `total=432 verified=265 not_found=167 missing_article=0`。

## 3. 筛选交互（pixel 出方案 → agy 审核 → 我融合）

**采纳方向**：顶部 facet 胶囊行 + 就地展开（单开手风琴）+ 第二行已选 token。
否决左侧栏（1024 吃列表宽度）与搜索框内联 token（可发现性差、计数无处放）。

**agy 审核提了 9 条，我采纳 8 条、部分采纳 1 条**

| 审核项 | 我的处理 |
|---|---|
| P0 面板/token 跟着 sticky，1024 会锁死 340px | 采纳：只让筛选行吸顶，面板与 token 留在文档流 |
| P0 键盘多选后焦点回跳、Esc 回错胶囊 | 采纳：勾选保持焦点、方向键同步 tabindex、Esc 回到触发胶囊 |
| P1 116 家企业平铺在 200px 面板里盲滚 | 采纳：按命中数取 Top 18，面板底注「共 N 家 · 用搜索框继续找」 |
| P1 URL 用中文名、`/` 编码、与后端 code 不一致 | 采纳：URL 用稳定 code（`ent_tencent` / `ai_adoption` / `high`），后端一个 `_build_cases_where()` 同时服务列表与 facets |
| P1 1024 隐藏滚动条横滚不可发现 | 采纳：窄屏允许换行（最多两行），不做横向盲滑 |
| P1 文档写 7 维、图里 6 维 | 采纳：砍掉「关键词」独立维度，交给搜索框 |
| P2 列表卡仍露 `MN-` 编号、token 行多一个「复制链接」 | 采纳：都删（地址栏已同步） |
| P2 顶栏搜索与筛选行搜索重复 | 采纳：`/cases` 视图下由筛选行搜索接管，顶栏搜索隐藏 |
| P2 facets 未写「排除自身维度」口径 | 采纳：每个维度计数时排除该维度自身条件，只返回 `n>0` |
| 砍行业二级级联 | 采纳：一级 5 类足够（保险 2 / 证券 2），不做二级 |

**最终交互规格**

- 维度：企业 / 行业（一级）/ 场景（10）/ 类型（8）/ 参考价值（3）/ 时间（近 7 天 / 近 30 天 / 今年）。
- 默认：一行胶囊（30px 高），右端「432 条」+「清空」（0 条件置灰）。
- 展开：点胶囊 → 面板在文档流内展开，限高 240（1440）/ 200（1024），内部滚动；同时只开一个。
- 已选：胶囊显示计数（「行业 2」），第二行 token 可单个删除；URL 用 `history.replaceState` 同步。
- 空态：列表区「无匹配」+「清除筛选」，保留筛选行。
- 键盘：Tab 进出、Enter/Space 展开与勾选、Esc 收起并回到触发胶囊、方向键 roving tabindex。

截图与可点 mock：

- `docs/sprint-evidence/sprint-79/render/filter-1440.png`（默认态）
- `docs/sprint-evidence/sprint-79/render/filter-open-1440.png`（企业面板展开）
- `docs/sprint-evidence/sprint-79/design/filter-mock.html`（可点：展开/多选/token/清空/URL）

**工作量**：前端 1 人日（组件 + 键盘 + URL 同步），后端 0.5 人日（`facets` 接口 + 统一 where）。

## 4. 案例分享简报 vs 常规简报

**是不是同一种模式？一半是。**

| | 常规简报 | 案例简报 |
|---|---|---|
| 入口 | `POST /api/share` | `POST /api/share/cases` |
| 队列/状态/产物目录 | `share_tasks` + worker + `data/artifacts` | 同一套 |
| 渲染器 | `moni/share/brief_html.py`（0 LLM，阅读型） | `moni/share/case_brief.py`（0 LLM，数据清单） |
| 排版 | 米白底 + 衬线正文 17px + 960px，优先关注 → 逐篇 → 事实核查证据 → 未纳入 | 冷白底 + 无衬线 + 960px，统计条 → 分组 → 案例卡 |

所以：**管道同一条，排版两套**。案例简报现在像"数据导出"，不像"简报"。

**建议**：不合并渲染器（一个读文章、一个读案例），但统一「简报骨架」与排版规则：

1. 封面：标题 + 生成时间 + 一句总览（案例数/机构数/最高频场景）。
2. 关键数字：现有 stats 条，数字用 sans tabular，标签 12px。
3. 分组导航：超过 3 组时给一行锚点。
4. 正文：案例摘要/解读用衬线 16.5px（与常规简报一致），标签/数字用 sans。
5. 证据区：默认 3 行 + 「展开原文上下文」（接 §2 的上下文层）。
6. 页脚：口径声明（自动抽取 / 未经人工核验 / 生成时间）。
7. 打印：A4 分页，卡片不跨页。

这样分享出去的两类简报看起来是同一个产品，而不是两个工具。

**已落地（排版统一）**

- `moni/share/case_brief.py` 的 `_CSS` 换成与常规简报同一套 token：
  米白 `#f5f2eb` / 卡片 `#fffcf7` / 暖灰边框，正文 `--serif` 16.5px / 1.75，
  标签·数字·页脚用 `--sans`；标题 2rem，统计数字 24px tabular。
- 分组标题改 sans 15px + 3px 暖色左边线；案例卡 22/24 内边距；证据块用衬线 14.5px。
- 加 `@media print`：A4 下 hero 与卡片 `break-inside: avoid`，不跨页。
- 未合并渲染器：常规简报读文章，案例简报读案例，仍各用各的渲染器。
- 样例：`docs/sprint-evidence/sprint-79/render/share-brief-sample.html`，
  截图 `share-brief-sample-1440.png`；分享相关测试 62 passed。
- 待办：证据区接 §2 的「原文引用 / 模型转述」徽章与上下文展开（等 069 上生产后再接）。

## 5. 拍板结果

1. §2 证据三层：做（校验 + 上下文已落地；原文定位随详情页 UI 一起做）。
2. §3 筛选方案：按上表落地（含砍行业二级、砍关键词维度），等 mock 样式确认后进前端。
3. §4 案例简报：按「同一骨架、两种渲染器」统一，排版层已落地。

## 6. 三层区域 UI 区分（pixel 设计 → agy 审核 → 融合）

**问题**：概览、筛选、案例卡三层都用 `--card` + 1px 边框 + 12px 圆角 + 相近内距，
用户读起来是「同一种卡片」。

**融合结论：三档表面 + 圆角 + 边界三重编码**（不靠区域标题）

| 区域 | 表面 | 圆角 | 边框 | 内距 | 交互 |
|---|---|---|---|---|---|
| A 概览 KPI | 透明（页面 `--background`） | 0 | 上下各 1px `--border` | 16px 0 | 无（只读） |
| B 筛选控制 | `--muted`（内凹） | 6px | 1px `--border` | 12px 16px | 胶囊 hover / 选中黑底 |
| C 案例卡 | `--card`（抬起） | 12px | 1px `--border` | 20px | hover `--shadow` + 上移 1px |

- 灰度明度：A 89.4 / B 79.9 / C 98.9；圆角 0 / 6 / 12；边界「有 / 有 / 全框」，
  打印灰度与色盲下都不丢层级。
- 展开面板：`--background` 底 + `--card` 选项，避免与 C 区案例卡同明度。
- 1024：A 仍 4 列（gap 8 / padding 14 0），B 仍 3 列单行（不横滚），C 2 列；
  <800px 才把 A 折 2×2、B 折单列。
- agy 的 P0 两条已处理：mock 的旧 `.cc-metric` 不进入融合版；1024 不做隐藏横滚。
  P1「搜索 / 命中数 / 清空 / token 行」以 `filter-mock.html` 为准，原型里的三组 facet
  只是同一表面的简化态。

**单一事实来源（后续开发照这个还原）**

- 页面级视觉与三档表面：`design/prototype-v2.html`
- 筛选条交互（搜索 / 计数 / 清空 / token / 面板）：`design/filter-mock.html`
- 最终 `/cases` = 原型的 A/C + filter-mock 的 B 条，表面语言两处一致（`--muted` + 6px）。

**证据**

- pixel 原始方案：`design/zone-hierarchy-pixel.md`、`design/zone-hierarchy-pixel.html`
- agy 审核：`design/zone-hierarchy-agy.md`（有条件接受，2×P0 / 3×P1 / 2×P2）
- 融合截图：`render/prototype-zones-1440.png`、`prototype-zones-1024.png`
- 筛选 mock：`render/filter-zones-1440.png`、`filter-zones-open-1440.png`、`filter-zones-1024.png`

### 开发还原验收（逐条对截图）

- A 区不得有底色、圆角、阴影；上下 1px `--border` 细线必须在。
- B 区必须是 `--muted` 面 + 6px 圆角；展开面板 `--background`、选项 `--card`；
  不得把 B 做成 12px 圆角的卡片。
- C 区必须是 `--card` + 12px 圆角 + hover `--shadow`；列表卡不得出现 `MN-` 编号。
- 1024：A 4 列、B 单行 3 组、C 2 列，页面不得横向滚动。
- 只允许使用现有 token；不新增色板、渐变、发光、玻璃拟态。
- 对照基线：`render/prototype-zones-1440.png` / `-1024.png`、
  `render/filter-zones-1440.png` / `filter-zones-open-1440.png`。
