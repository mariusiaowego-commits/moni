# 案例库 2.0：调研结论与实施方案

> 状态：调研完成，待确认范围。分支 `feature/v1.0.131-cases-library`。
> 实测库：`data/news.db`（只读查询），代码基线 `main@5ba7a54`。

## 1. 先回答六个问题

### 1.1 案例只能看不能分享：判断正确

- 案例详情只有核验 / 驳回 / 删除 / 查看原文 / 关闭，没有分享入口（`moni/web/static/index.html:2375-2389`）。
- 分享模型全部只接受 `article_ids`（`moni/share/formats.py:109` 等 7 处），`share_tasks.article_ids` 还是 `NOT NULL`。
- 分享页是文章选择器（`index.html:1176+`），没有案例选择器。

结论：不是“入口藏起来了”，是链路不存在。

### 1.2 案例类型：结构上只有 2 类，且实际统计被 bug 压扁

- `case_type` 的 CHECK 只有 `AI` / `PI`（`moni/db/migrations/024_cases.sql:26-27`），其他类型不是“没统计到”，是写不进去。
- 实测 `AI=429 / PI=3`，`case_action` 全部是 `launch`。根因是 `normalize_case_action()` / `normalize_case_type()` 只认中文关键词，LLM 返回 `pilot` / `PI` 时全部 fallback（`moni/cases/normalize.py:309-337`）。
- 真实内容里有融资、并购、招采、处罚、组织调整，但都被塞进 AI。行业事件表已经证明这些类型存在：`regulation 121 / product 153 / financing 67 / tech 227 / other 345`。

### 1.3 搜“小程序”很少：既没抽全，也没索引全

| 口径 | 数量 |
|---|---|
| 正文提到“小程序”的文章 | 252 |
| 这些文章里已产出案例的 | 5 篇 / 10 个案例 |
| `case_keywords` 含“小程序” | 2 行 |
| `cases_fts` 命中“小程序” | 1 条 |

两个原因叠加：

1. 覆盖率低：432 个案例只来自 273 / 6596 篇文章（4.1%）；2026-09 以后新增 926 篇正文 ≥100 字的文章没有抽取，2026-11 单月缺口 639 篇。`cron_jobs` 只有抓取和事件抽取，没有案例抽取（`056_cron_jobs.sql:48-60`）。
2. 索引窄：`cases_fts` 只索引 subject / counterparty / summary / evidence / amount，不含关键词、文章标题、正文（`024_cases.sql:84-92`）。

另外 trigram 对少于 3 字的词直接 0 命中：`MATCH '支付' = 0`，而 `LIKE '%支付%' = 123`；`C++` 这类输入还会抛 FTS5 语法错误。

### 1.4 详情文字少：不是单纯模型弱，是我们只要了 80 字

- 提示词明确要求摘要 ≤80 字、参考依据 1 句话（`moni/llm/prompts/case_extraction.txt:20-21`）。
- 正文只取前 4000 字（`moni/cases/pipeline.py:48`）。
- 实测平均长度：摘要 35.1 字、依据 39.6 字、证据 62.2 字。
- 抽取走 `resolve_active_model()`，当前落到 `ark-code-latest`（编码模型），而库里已有 `gemini-2.5-pro`、`MiniMax-M3`、`MiMo v2.5 Pro` 等可选模型。
- `amount` 是裸字符串，语义丢失：“互动日活3000万”被写成“3000 万元”；外币还会被改成人民币（`2.5亿美元 → 2.5 亿元`，`9.9万亿韩元 → 9.9 万元`）。

结论：模型只是其中一环。真正的瓶颈是“抽取 schema 太浅 + 正文截断 + 指标无语义 + 模型选型不匹配”。

### 1.5 缺企业 / 行业 / 业务场景聚合：确认

- `stats()` 只返回 total / by_type / by_action / by_reference_value / pending_review（`moni/cases/repository.py:732-765`）。
- `subject` 是自由文本：356 个不同主体支撑 432 个案例；分行被当成独立主体，另有 10 条“银行银行”叠词。
- 没有企业实体表、行业字段、场景字段。`case_keywords` 有 995 行 / 816 个不同词，其中 733 个只出现一次，直接聚合会碎片化。

### 1.6 详情页太小：确认

`.case-detail` 固定 560px、max-height 80vh（`moni/web/static/css/main.css:3396-3412`）。在 1440 屏上只占 39% 宽，撑不起“结论 + 做法 + 数字 + 证据 + 企业画像”。

## 2. 方案结论

### 2.1 三个轴，不要继续往 case_type 里塞

- **企业**：规范实体 + 别名 + 父子机构（总行 / 分行 / 子品牌）。
- **行业**：两级受控词表，挂在企业上，案例继承。
- **业务场景**：35 个左右的受控场景，`case_keywords` 保留为长尾召回词。
- **案例类型**：8 类，新增 `case_category`，不复用旧 `case_type`：
  `ai_adoption` / `product_launch` / `digital_infra` / `partnership` / `capital` / `procurement` / `compliance_risk` / `org_strategy`。

旧值兼容：`ai_adoption`、`digital_infra` 继续写 `AI`；其余写 `PI`。旧唯一键和旧筛选不破。

### 2.2 指标必须结构化

新增 `case_metrics`：`metric_type + value_num + unit + period + value_raw + evidence_quote`。

- “互动日活3000万” → `dau / 30000000 / user / 2026Q3`。
- “2.5亿美元” → `investment / 250000000 / USD`。
- “9.9万亿韩元” → `investment / 9.9e12 / KRW`。
- `cases.amount` 降级为展示字符串，不再承担语义。

### 2.3 抽取从“一次性摘要”升级成“两段式分析”

- Stage A 事实抽取：主体、企业、类型、动作、场景、指标、证据原文，必须能回指正文。
- Stage B 解读：一句话结论、为什么值得看、可迁移做法、前提与风险、同类案例。只吃 Stage A 的结构化事实，降低幻觉。
- 复用现有能力：`llm_jobs` 队列、fact-check 的证据校验、context pack、业务价值评分；不要另起一套同步调用。
- 模型：新增 `active_case_model_id`，默认走 `gemini-2.5-pro` 或 `MiniMax-M3`，不要用 coding model 跑业务分析。
- 自动覆盖：新 cron `extract_cases_pending`，按 `case_extract_runs` 账本幂等消费；回填按正文长度 ≥300 优先，分批 30，带日 token 预算闸。

### 2.4 搜索：案例级 FTS v2 + 文章级兜底

- `cases_fts` 增加 `keywords`、`article_title`、企业别名；`case_keywords` 增删触发同步。
- 查询清洗 + 双引号转义；少于 3 字的词走 `search_text LIKE` 兜底。
- 新增文章级 trigram 索引，搜索结果分两栏：“案例命中”与“文章提到但未抽案例”，后者可一键补抽。

### 2.5 分享：融合基础设施，独立案例渲染

结论：**不要单独做一套队列 / worker / 产物系统，也不要把案例硬塞进 article brief。**

- 复用：`share_tasks` 队列、manifest、worker 锁与超时恢复、artifacts、进度、暂停 / 取消、权限门禁。
- 新增：`share_tasks.case_ids`、`item_kind='case'`、`CaseBriefPayload`、`case_brief_html` / `case_card_png` 两个格式、`case_brief.py` 渲染器。
- 案例简报默认不调 LLM，直接渲染 Stage B 的结构化结果；只有“生成综述”才调一次模型。
- UI：详情页“分享此案例”，列表页多选“生成案例简报”，队列仍走现有“分享队列”。

### 2.6 详情页 UI

- 桌面端 90vw / 最大 1280px，双栏：左侧结论、关键数字、可迁移做法、风险前提、证据；右侧企业画像、同类案例、分享。
- 首屏必须回答：谁、做了什么、数字是什么、为什么值得看、我能抄什么。
- 列表页增加企业 / 行业 / 场景三个聚合区与 facet，不再只有类型和参考价值。

## 3. 分阶段

### P0 止血（建议 1 个 PR）

1. 标签 405：前端 `/tags` → `/keywords`，删除按 keyword；已完成并补测试。
2. 归一 bug：枚举直通、主体最长别名与叠词、币种与万亿。已完成并补测试。
3. `061_case_metrics` + `raw_json` + `case_action_source`。
4. `062_cases_fts_v2` + 关键词 / 标题同步。
5. `063_cron_extract_cases` + 抽取账本 + 预算闸。
6. 回填 dry-run：action / category / 主体 / 指标先出报告，不直接改生产。

### P1 结构化升级（2–3 个 PR）

1. `064_enterprises`：企业、别名、父子机构、案例企业角色。
2. `065_case_taxonomy`：行业、场景、`case_category`、受控词表。
3. Stage A/B 提示词与 `case_insights`；聚合 API 与企业画像。
4. 搜索 L2：文章级 trigram + 缺口补抽。

### P2 分享与精修

1. `067_share_case_ids` + 案例简报 / 案例卡片。
2. 汇率表与跨币种可比金额。
3. 旧表重建（可选）：去掉 `case_type` CHECK、`case_action` 增加 `unknown`。

## 4. 待确认

- 案例类型 8 类是否按此定稿，还是先保留 AI/PI 只加 `case_category` 辅助标签。
- 行业分类是否接受“挂在企业、案例继承”。
- 回填范围：只回填 2026-09 以后，还是全量 6596 篇；日 token 预算上限。
- 分享首发格式：案例长图、案例简报 HTML、还是两者都要。

## 5. 实施进展（2026-11-25 实测）

### 5.1 已落地（工作区未提交）

| 层 | 内容 |
|---|---|
| P0-1 | 标签 405：前端改走 `/keywords`，POST `{keyword, weight}`，DELETE 按 keyword；补契约测试 |
| P0-2 | 归一：`launch/pilot/scale/transform/exit` 与 `AI/PI` 直通；主体最长别名；币种与万亿；叠词修复 |
| P0-3 | `061_case_metrics`：`case_metrics` + `cases.raw_json` + `case_action_source` |
| P0-4 | `062_cases_fts_v2`：索引 keywords / article_title，`search_text` 兜底，<3 字走 LIKE |
| P0-5 | `063_cron_extract_cases`：`case_extract_runs` 账本 + 13:30 / 20:30 两条 cron + 日 token 预算闸 |
| P1-1 | `064_enterprises`（116 企业 / 154 别名）、`065_case_taxonomy`（8 类 + 10 场景） |
| P1-2 | `backfill_taxonomy.py` 规则回填 + 四维聚合 + `GET /api/cases/stats` |
| P2-1 | `066_case_insights`（建表）、`067_share_case_ids`、`CaseBriefPayload`、`case_brief.py` + 模板、`POST /api/share/cases`、worker 短路 |
| P3-1 | `backfill_metrics.py`：存量 432 条规则回填 `case_metrics` 并语义化 `cases.amount`（0 次 LLM） |

### 5.2 复检数字（从生产库重新拷贝，001→065 全链迁移）

```
schema_version=65  cases=432  enterprises=116  aliases=154
categories=8  scenarios=10  case_extract_runs=273
enterprise_matched=217  enterprise_unmatched=215
by_industry: 其他 224 / 银行 121 / 互联网 83 / 保险 2 / 证券 2
by_category: product_launch 144 / ai_adoption 72 / capital 65 / partnership 61
             / procurement 42 / org_strategy 26 / digital_infra 16 / compliance_risk 6
by_scenario: other 90 / ecny_stablecoin 76 / llm_agent 73 / cross_border_pay 50
             / core_infra_cloud 37 / risk_compliance 36 / credit_supply_chain 29
             / channel_service 15 / wealth_advisory 14 / mobile_digital_pay 12
```

### 5.3 测试基线

- 受影响集合（cases / case_* / share / db / cron / cron_registry / migration_039）：**307 passed**。
- 全量（rebase 到 `5ba7a54` 后）：**2880 passed, 2 skipped**（0 failed）。详见 `docs/sprint-evidence/sprint-79/VERIFY.md`。
- 迁移重跑类此前 27 红已清零：其中 7 个由本次修复（cron 断言 5→7；039 手写 v38 fixture 补上 v38 本就存在的 `cases` / `case_keywords`），其余随 P0 修复合入转绿。

### 5.4 仍未做

- Stage B `case_insights` 的 LLM 回填、汇率表 068、案例卡片 PNG。
- 前端入口：详情页「分享此案例」、列表多选「生成案例简报」尚未接 `POST /api/share/cases`；沉浸式详情目前只有 pixel 的原型，未进真实页面。

## 6. 契约与回滚

### 6.1 数据库

- 迁移 `061`–`067` 全部为**增量**：新表 + 新列 + 索引 + 触发器重建，无列删除、无 CHECK 收紧。
  唯一的重建是 `062` 对 FTS 虚表 `cases_fts` 的 `DROP` + `CREATE`，属于索引结构升级，不涉及业务数据。
- main 侧最高迁移是 `060`，`061`–`067` 编号未占用。
- 回滚：代码回退即可；新列与新表对旧代码无害，不需要 down migration。若必须回到干净 schema，用迁移前备份恢复 `data/news.db`。
- 唯一有副作用的迁移是 `062`：重建 `cases_fts` 并回填；回滚代码后旧 `cases_fts` 结构需要由备份恢复或重跑旧迁移。

### 6.2 公开 API

- 新增 `GET /api/cases/stats`：四维聚合，纯读。
- 新增 `POST /api/share/cases`：只入队 `share_tasks`，不直接发。
- 新增分享格式 `case_brief_html`；现有 7 种格式的 payload、校验、worker 分支保持不变。
- `POST /api/cases/{id}/keywords` 与 `DELETE .../keywords/{keyword}` 为既有路由，本轮只修前端调用路径（`/tags` → `/keywords`）。

### 6.3 运行时

- `config/schedule.yaml` 新增 `extract_cases_noon_post`（13:30）与 `extract_cases_night`（20:30）；都走 `case_extract_runs` 账本，可随时停用。
- 日 token 预算闸：`app_settings.case_extract_daily_input_token_budget=300000`。
- `scripts/default_worker.sh` 只为 `case_*` 格式加短路，article 分支未改。

### 6.4 回滚步骤

1. `git revert <merge commit>`（或回退到上一 tag）。
2. 若已上生产：`launchctl kickstart -k gui/$(id -u)/com.moni.web`。
3. `case_metrics` / `case_enterprises` / `case_scenario_links` / `case_insights` 可保留为空表；不需要删列。

## 7. 文案约定（已确认）

- 复杂度留给系统：界面不出现 `case_type` / `PI` / `facet` / “真实库” / “分层可追溯” / “形态 1 / 1:1” 这类内部或设计稿术语。
- 参考价值统一写 `高价值` / `中价值` / `低价值`，保留三档与颜色区分。
- 详情固定 6 个小节：主栏 `结论` / `做法` / `风险与前提` / `原文证据`；侧栏 `基本信息` / `相关案例`。`分享` 只留按钮组，不占标题。
- 列表卡不显示 `MN-` 编号，行业只显示一级；按钮用 `生成分享` / `复制链接`。
- 空状态只写结果与下一步：`暂无匹配案例，试试清除筛选条件`。
- 生产库迁移与 8900 重启、commit / PR / tag，均未执行，等授权。
