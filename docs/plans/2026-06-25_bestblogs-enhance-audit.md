# Plan: BestBlogs 增强 — 分享模块选项审计与一致性修复

> v1.0.9.3 (planned), 2026-06-25

## 1. 背景

v1.0.5.25 引入 BestBlogs API 增强（分享 modal 的 4 个 checkbox + 1 个摘要来源 radio），代码已合并并在 36kr / mpaypass / qbitai / wechat_rss_* / bestblogs 五个源上稳定运行。

2026-06-25 用户测试发现：分享一份 36kr 文章并勾选 BB 增强复选框，artifact 输出文件**与不勾完全相同**——既无 BestBlogs 评分，也无任何提示。代码审计后定位出 3 个一致性 bug，本 plan 是审计结论 + 修复方案。

## 2. 现状（代码已核对）

### 2.1 数据流概览

```
前端 modal (app.js:1379-1384)
  ↓ 5 个 state 变量
addToQueue() (app.js:1515-1524) 拼 payload
  ↓ {bb_enhance, bb_summary_source, bb_score, bb_main_points, bb_key_quotes, bb_tags}
POST /api/share → validate_manifest (formats.py:407)
  ↓ 7 个 BB 字段写在 7 个 Payload model 里 (formats.py:77-87, 每个 format 重复)
write_manifest → ~/.moni/queue/<id>.json
  ↓
default_worker.sh (worker.sh:386-422)
  ↓ 仅看 manifest.payload.bb_enhance 一个布尔
fetch_and_store(article_id) (bestblogs_api.py:155-260)
  ↓ 1. 过滤 source != 'bestblogs' → return None  (行 183-185)
  ↓ 2. 过滤 bb_fetched_at != NULL → return None  (行 187-197)
  ↓ 3. 一次性把 6 字段全 UPDATE + 写 bb_fetched_at (行 230-255)
worker.sh:316 读 6 字段拼 markdown (if bb_X is not None:)
  ↓
artifact 输出
```

### 2.2 5 个选项的现状

| 前端 state | payload 字段 | 类型 | 默认 | worker 实际用？ |
|---|---|---|---|---|
| `shareBBSummarySource` | `bb_summary_source` | enum: `moni_ai` \| `bestblogs` \| `both` \| `none` | `moni_ai` | ❌ 不读，由 prompt 软约束 |
| `shareBBEnhanceScore` | `bb_score` | bool | false | ✅ worker.sh:333-334 读 `bb_score` 列 |
| `shareBBEnhanceMainPoints` | `bb_main_points` | bool | false | ✅ worker.sh:339-345 读 `bb_main_points` 列 |
| `shareBBEnhanceKeyQuotes` | `bb_key_quotes` | bool | false | ✅ worker.sh:348-354 读 `bb_key_quotes` 列 |
| `shareBBEnhanceTags` | `bb_tags` | bool | false | ✅ worker.sh:357-362 读 `bb_tags` 列 |

`bb_enhance` 字段（行 1519）是 5 个选项的 OR 计算结果，**仅用于触发 API 调用**，渲染时**不读**这个字段。

### 2.3 数据库列（`articles` 表）

迁移已在 v1.0.5.25 落地（migration 011，假设编号）：
- `bb_score INTEGER`
- `bb_summary TEXT`
- `bb_one_sentence TEXT`
- `bb_main_points TEXT` (JSON string)
- `bb_key_quotes TEXT` (JSON string)
- `bb_tags TEXT` (JSON string)
- `bb_resource_id TEXT`
- `bb_fetched_at TEXT` (ISO 8601)

## 3. 发现的 3 个 Bug

### Bug #1 — 非 bestblogs 源静默吞掉（**P1 — 用户体验**）

**复现**：
1. 选 1 篇 36kr 文章（`articles.source = '36kr_rss'`）
2. 分享 modal 勾"BB 评分 / BB 要点"
3. 确认 → 看到 worker 进度条走完，artifact 文件生成
4. 打开 artifact：跟没勾 BB 增强**完全一样**，无任何提示

**根因链**：
1. `worker.sh:398` 调 `fetch_and_store(aid)`，aid 是 36kr 文章
2. `bestblogs_api.py:183-185`：`if row["source"] != "bestblogs": return None`
3. `fetch_and_store` 返回 `None`，worker.sh 不区分"非 bestblogs 源"和"API 失败"
4. 继续走 worker.sh:427 重读 article_content，bb_* 6 字段都是 NULL
5. worker.sh:333 `if bb_score is not None:` 全部 false → artifact 不渲染 BB 块
6. 用户无任何提示

**影响**：
- 用户**实际**浪费 API 额度（worker.sh 循环里仍对每篇 36kr 文章调一次 `fetch_and_store`，`source` 过滤在最内层，单次成本约 5ms sqlite 读，但**前端会发 `bb_enhance=true` 进 manifest**，心理上觉得在"用 BB 服务"）
- 用户**预期**会看到 BB 增强，artifact 看不到，**直接判定功能坏了**（这正是用户报的）

### Bug #2 — 4 个细开关触发同一个 API 调用 + 锁死未来勾选（**P2 — 额度浪费 + 扩展性**）

**复现**：
1. 选 1 篇 bestblogs 文章，分享时只勾"BB 评分"
2. 确认 → API 调用 1 次（`/search` + `/resources/{id}/meta`），6 字段全部落库
3. `bb_fetched_at` 写当前时间
4. 下次分享同一文章，**追加勾选**"BB 要点"
5. 确认 → worker.sh:398 仍调 `fetch_and_store`，但行 187-197 看到 `bb_fetched_at` 非空 → `return None`
6. `bb_main_points` 字段在 DB 里是 NULL（上次只用了 score，其它字段**已经落库但 prompt 没让 LLM 用**——等等，let me re-check）

**修正**：审计后发现 worker.sh:316 的 SELECT 读 `bb_main_points` 字段是**全量读**，**写入时也是全量写**（bestblogs_api.py:232-241），但渲染时只读**有值的字段**。所以：
- 步骤 2：6 字段全落库，score=85, summary="...", main_points=[...], quotes=[...], tags=[...] 全在 DB
- 步骤 3：artifact 里 score=85 显示（因为 bb_score is not None），但 main_points 等其它字段**即使有值也**会渲染——只要 SQL 读出来非空就显示
- 步骤 4-5：第二勾"BB 要点"后，worker.sh 调 `fetch_and_store`，**返回已有数据**（行 188-197：`if existing["bb_score"] is not None: return dict(existing)`），不再调 API ✅
- **但**：行 195 `if existing["bb_score"] is not None: return dict(existing)` —— 仅在 score 有值时返回。如果首次调用 **score 字段恰好为 NULL**（API 偶发），下次再勾任何选项都会被错认为"已拉取" → 永远不重试

**所以 Bug #2 实际上是 2 个子 bug**：
- **#2a**：4 字段独立去重粒度不够——任一字段缺失或新加字段，**没有重新拉取的入口**
- **#2b**：去重判断（`if existing["bb_score"] is not None`）只看 score，**用 score 当作"拉取成功"的 proxy 不严谨**

### Bug #3 — `bb_summary_source` 是 prompt 软约束，非硬约束（**P2 — 语义正确性**）

**复现**：
1. 选 1 篇 bestblogs 文章，摘要来源选"BestBlogs"
2. 确认 → `bb_enhance=true` 触发 API，`bb_summary` 落库（API 返回了）
3. 但 `articles.ai_summary` 仍**有值**（v1.0.5.25 之前已生成过 moni 摘要）
4. worker.sh:316 SELECT 同时读 `ai_summary` 和 `bb_summary` 两个字段
5. worker.sh:330 `if ai_summary and ai_summary.strip():` → 总是显示
6. worker.sh:335 `if bb_summary and bb_summary.strip():` → **也**总是显示（如果有值）
7. 选"BestBlogs"时，**AI 摘要 + BB 摘要两条都渲染**，与用户预期"只用 BB 摘要"不符

**根因**：`format-markdown.md` 的"摘要来源"规则是 **prompt 文档**，LLM 在拼装时**自行判断**用哪个。但 moni worker 是 **SQL 直读 + 模板拼接**，不调 LLM 拼 markdown（行 325-371 是纯 Python 模板），所以 prompt 规则**不生效**。

**影响**：
- 用户选"BB 摘要"：artifact 里**同时出现**"AI 摘要" + "BestBlogs 摘要"两条
- 用户选"两者"：artifact 跟"BB 摘要"**一样**（没区别）
- 用户选"无"：**无效**——`ai_summary` 仍渲染（行 330-331 不看 `bb_summary_source`）

## 4. 修复方案

### 4.1 修复优先级

| # | 修复 | 难度 | 影响 | P |
|---|---|---|---|---|
| 1 | Bug #1 — 非 bestblogs 源过滤 + UI 提示 | 低 | 消除"功能坏了"误判 | **P1** |
| 2 | Bug #2 — 4 字段独立去重 + "再拉一次"按钮 | 中 | 省额度 + 增量勾选可工作 | **P2** |
| 3 | Bug #3 — `bb_summary_source` 硬约束（worker 模板里分支） | 中 | 摘要来源选择有效 | **P2** |

### 4.2 修复 #1 详细方案（P1，本版本必修）

**前端（app.js）**：
- modal 的"摘要来源"radio 下方 + 4 个 checkbox 上方，**永远显示**一行提示文字：
  ```
  ℹ️ BestBlogs 增强仅对 bestblogs 源文章生效，其他源会被静默跳过（不浪费额度）
  ```
- 选文章列表加载完成后，**统计** bestblogs 文章数 vs 总数：
  - `bbCount = selected.filter(a => a.source === 'bestblogs').length`
  - `nonBBCount = selected.length - bbCount`
  - 4 个 BB checkbox 在 `nonBBCount > 0` 时显示橙色 disabled + 提示
  - 但**不强制 disable**——用户可能就是要"测试一下"（这是用户偏好：不喜欢 fallback 兜底，"不能留空就强制必填"反向应用：不限制，让用户自己看清楚）

**后端（worker.sh）**：
- 调 `fetch_and_store` 前过滤 `article_ids`：只对 `source='bestblogs'` 的 id 调
- 这样**实际**不浪费 sqlite 查询时间（成本可忽略）——但**更重要的是**给 worker.log 写一条明确日志：
  ```
  [bb_fetch] skipped N non-bestblogs article(s) for task $task_id
  ```
- 用户开 worker.log 能看到"为什么没生效"

**预估改动量**：
- app.js: +30 行（提示 + 计数）
- worker.sh: +5 行（过滤 + 日志）
- 不改后端 Python

### 4.3 修复 #2 详细方案（P2）

**问题分解**：
- 4 字段独立去重 → 把 `bb_fetched_at` 拆成 4 个：`bb_score_fetched_at / bb_summary_fetched_at / bb_main_points_fetched_at / bb_key_quotes_fetched_at / bb_tags_fetched_at`
- "再拉一次"入口 → 4 个字段拆出后，**用户**每勾一项，worker 都检查对应列。**首次勾选 bb_score** → API 调一次；**再次分享时追加勾选 bb_main_points** → 检查 `bb_main_points_fetched_at` 为 NULL → 调 API 拉该字段
- 不必全量重拉（`/meta` 返回的还是全量）——读 DB 当前值 + 新值合并

**改动量**：
- DB: 新增 4 列 + 1 迁移（migration 012）
- bestblogs_api.py: 重构 `fetch_and_store` 内部逻辑，**按字段去重**
- app.js / worker.sh: 配合 4 字段独立去重

**不修**（明确范围外）：
- 重构 `fetch_and_store` 不动签名
- 不改 API 频率限制

### 4.4 修复 #3 详细方案（P2）

**worker.sh:330-362 改造**：
- 在 SELECT 加 1 列 `bb_summary_source TEXT` 写到 manifest 后由 worker 读 manifest 取
- 实际更简单：worker.sh:325-365 模板里加 1 个分支：
  ```python
  source = p.get('bb_summary_source', 'moni_ai')  # 从 manifest payload 读
  if source in ('bestblogs', 'both') and bb_summary:
      block += f"\n\n**BestBlogs 摘要**: {bb_summary}"
  if source in ('moni_ai', 'both') and ai_summary:
      block += f"\n\n**AI 摘要**: {ai_summary}"
  if source == 'none':
      pass  # 两个都不渲染
  ```

**改动量**：
- worker.sh: ~15 行（分支 + 读 payload）
- 不动 Pydantic models（字段已存在）
- 不动 prompt 文档（`format-markdown.md` 的"摘要来源"规则**改回事实陈述**："worker 已按 bb_summary_source 分支，prompt 不再控制"）

## 5. 实施步骤

### Step 1（v1.0.9.3-a）— Bug #1（仅前端 + worker.sh 日志）
1. feature branch `feature/v1.0.9.3-bb-enhance-audit`
2. app.js: 加提示文字 + 计数 + conditional disabled 样式
3. worker.sh: 在 `bb_enhance_val` 检查后，过滤 `article_ids` 为 bestblogs 子集 + 写日志
4. pytest 全绿
5. **手动验证**：分享 36kr 文章 + 勾 BB 增强 → artifact 不变，**UI 提前显示提示**

### Step 2（v1.0.9.3-b）— Bug #2（4 字段去重）
1. 新建 migration 012：拆 `bb_fetched_at` → 4 列
2. bestblogs_api.py: 重构 `fetch_and_store`，**4 字段独立 fetched_at 判断**
3. app.js: 不变（5 个选项照旧）
4. pytest 新增：bestblogs_api 的 4 字段去重逻辑单测
5. **手动验证**：分享 bestblogs 文章勾 bb_score → 第二次追加勾 bb_main_points → worker.log 应出现"fetched meta for X article(s)"

### Step 3（v1.0.9.3-c）— Bug #3（`bb_summary_source` 硬约束）
1. worker.sh 模板分支改造（按 `bb_summary_source` 走）
2. format-markdown.md 删除"摘要来源"段，加注："worker 已硬约束，prompt 不控制"
3. pytest 新增：worker.sh 模板的 4 个 source 取值行为单测
4. **手动验证**：分享 1 篇有 ai_summary + bb_summary 的 bestblogs 文章，4 个 source 取值分别看 artifact

## 6. 测试矩阵

| 场景 | 选 source | 勾 BB 选项 | 期望 artifact |
|---|---|---|---|
| A. 36kr 文章 | 36kr | 勾 bb_score | 不变 + UI 提示"仅 bestblogs 生效" |
| B. bestblogs 文章（首次） | bestblogs | 勾 bb_score | 出现"BestBlogs 评分: 85/100" |
| C. bestblogs 文章（追加勾选） | bestblogs | 勾 bb_score + bb_main_points | 评分 + 要点都显示，**worker.log 显示"fetched"** |
| D. bestblogs 文章（4 选 1） | bestblogs | 只勾 bb_score | 只显示评分（其它 BB 字段即使有值也不显示——可选） |
| E. bestblogs 文章（4 选 4） | bestblogs | 全勾 | 评分 + 要点 + 金句 + 标签都显示 |
| F. 摘要来源 = BB | bestblogs | 选 bestblogs | 只显示 BB 摘要，**不**显示 AI 摘要 |
| G. 摘要来源 = moni | bestblogs | 选 moni_ai | 只显示 AI 摘要 |
| H. 摘要来源 = both | bestblogs | 选 both | AI 摘要 + BB 摘要**都**显示 |
| I. 摘要来源 = none | bestblogs | 选 none | **都不**显示 |

> 场景 D 是 v1.0.9.3-b 之后的**新行为**（按字段去重后，"只勾一项" = "只显示一项"），需要用户拍板是否要 D 行为（v1.0.5.25 当前是"全显示"，因为首次拉了就全落库）。

## 7. 不在本版本范围

- **#4 字段 lazy 调用**：现在 `fetch_and_store` 一次性拉全 6 字段。**修复 #2 后**，4 字段独立去重时**仍**调一次 `/meta`（API 不支持部分字段），所以**不会**节省 API 次数。只省 DB 行大小。
- **`bb_summary_source='both'` 的 prompt 拼装**：如果 moni 内部 LLM 重新拼 BB + AI 摘要为"融合摘要"，那是 v1.0.10+ PRD 范畴。
- **bestblogs 源识别**：当前是 `articles.source == 'bestblogs'` 字符串硬匹配。如果用户重命名信源，BB 增强全失效。**已知**，v1.0.10+ 再修（加 `sources.bb_enabled BOOLEAN` 列）。
- **`score` 字段 NULL 的兼容性**：v1.0.5.25 部署后**没有 score 字段**的旧数据需要 backfill。**已知**，v1.0.9.3-b 一并处理（迁移 012 加 4 个 fetched_at 列同时 backfill 已有 score 行）。

## 8. 风险与回滚

| 风险 | 概率 | 缓解 |
|---|---|---|
| 4 字段拆列 migration 失败 | 低 | 走 moni 标准 migration 流程（PRAGMA foreign_keys=OFF → ALTER TABLE → 测试 → COMMIT） |
| worker.sh bash 改错 | 中 | 改动小（15-20 行），本地先 dry-run `bash -n worker.sh` 语法检查 + 实际跑 1 个 bestblogs 任务 |
| 旧 share_task 在新 worker 上行为不同 | 低 | manifest 文件格式不变，只改 SQL 读法 |
| 用户接受度：4 字段独立去重后，**首次只勾 1 项**意味着"其它字段不显示"——v1.0.5.25 当时"勾任意一项 = 拉全 6 字段"的行为变了 | 中 | Step 2 PR 描述里**明确**写出来，让用户拍板 |

## 9. PRD 双写清单

- [ ] `docs/plans/2026-06-25_bestblogs-enhance-audit.md`（本文件）
- [ ] `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/tqob/05-Coding/project-moni/plans/2026-06-25_bestblogs-enhance-audit.md`（同步）
- [ ] `docs/STATUS.md` 待 Step 1 完成后追加
- [ ] `docs/USER_GUIDE.md` 待 Step 1 完成后追加（"BB 增强仅对 bestblogs 源生效"段落）
- [ ] `HANDOFF.md` 收尾时更新
- [ ] `vibe-coding-log.md` 收尾时 append

## 10. 验收标准

- [ ] pytest 300+ 全绿
- [ ] 场景 A（36kr + 勾 BB）→ UI 提示 + worker.log 写 "skipped N non-bestblogs"
- [ ] 场景 C（追加勾选）→ worker.log 写 "fetched"
- [ ] 场景 F-I（4 个 source 取值）→ artifact 严格按表显示
- [ ] 缓存清理：旧 `bb_fetched_at` 数据完整保留，迁移 012 不丢数据
- [ ] ?v= 缓存戳更新（如果改了 index.html）
- [ ] 走 PR 流程：feature branch → gh pr create → user review → gh pr merge --squash → tag v1.0.9.3 → 手动启 8900

---

> 文档维护：
> - 2026-06-25 创建（v1.0.9.3 计划阶段，3 个 bug 全部诊断完成）
