# X-bot scope 展示 + per-topic 门槛可配（v1.0.71 · sprint-35）

> 文档创建日期：2026-11-12 · 分支 `feat/xbot-scope-thresholds-v1.0.71` · 基线 main @ `04c9a42`
> Sprint: 35（`26111103`）· 目标版本 v1.0.71

> ## ⚠️ 状态：§2 与 AC-12 已被取代（2026-11-25）
>
> 本文档 §2「scope 是 moni 侧副本，**不参与入库闸门**」与 AC-12
> 「改 scope 前后入库 URL 集合相同」是 **v1.0.71 / sprint-35 当时的决策**，
> 已被 **sprint-55 / v1.0.131**（2026-11-25 12:33 批准）**取代**：
>
> - `fetch_config.scope` 的 `wechat_regex` / `xcircle_accounts` 现在是 moni 的
>   **入库准入门**：与领域关键词表 **OR**（命中即准入）；作者屏蔽 / 粉丝门槛 /
>   跨源 URL 去重仍前置，scope 命中不能绕过；
> - 只有 **moni 显式存储**的副本参与准入（展示用的兜底 / 上游值不参与）；
> - `scope_gates_ingest` 由 `false` 翻为 **`true`**；AC-12 的行为测试由
>   `test_scope_edit_does_not_change_ingested_url_set` 改为
>   `test_scope_admission_behavior_off_vs_on`（未存储不变 / 上游导出仍不变 /
>   显式存储后**严格多收**）；
> - scope 单独救回的条目挂专用归属 tag `xbot-scope`
>   （`moni/db/migrations/061_xbot_scope_admission_tag.sql`）。
>
> **原文完整保留作历史**。读本文请以本状态块为准，不要照 §2 / AC-12 的旧结论
> 改动当前实现；现行实现见 `moni/fetch/sources/xbot_feed.py::_match_scope`。

## 背景

X-bot 精选（`src-xbot-feed` / `xbot_feed`）这条信源"它到底抓什么、按什么标准筛"，
在 moni 工作台里**完全不可见**：

- **范围**：xcircle 半 = 固定 28 个推特账号；wechat 半 = 话题搜索（微信 / WeChat /
  小程序 / 公众号 / 视频号）+ 本地正则。这些名单与正则都在**另一台机器**的私有仓库
  `mariusiaowego-commits/xbot-feed` 里（`export.py` / `parse_daily.py` 归 X-bot，
  **moni 不得改**）。
- **门槛**：上游从 2026-11-12 起的每日 feed 自己叠了一层 wechat 门槛
  （`followers >= 5000 AND likes >= 5`，`WECHAT_MIN_FOLLOWERS` / `WECHAT_MIN_LIKES`）。
  moni 侧完全看不到这个事实。

实测统计（近 7 天 wechat 237 条）：粉丝中位数 908、点赞中位数 1。
→ 照抄上游 5000/5 会挡掉约 3/4（同时满足两者的只有 55 条 = 23%），门槛默认必须"关"。

## 方案

### 1. 范围展示（`GET /api/config/sources/{id}/scope`）

新增端点返回 scope（`xcircle_accounts` / `wechat_regex` / `wechat_queries`）、
thresholds、来源标注。

**`scope_source` 三态**（优先级从上到下）：

| 值 | 含义 |
|---|---|
| `upstream` | 本地 clone 的 feed JSON 顶层有 `scope` 键 → 直接用上游值（权威、只读） |
| `moni_stored` | 上游没导出，但 moni 侧存着**用户编辑过的副本** |
| `moni_fallback` | 上游没导出、moni 侧也没副本 → 用 `DEFAULT_SCOPE` 兜底快照（2026-11-11） |

另加独立字段 `scope_edited: bool`（向后兼容旧客户端）。端点**只读本地 clone 的
feed JSON**（与 fetcher 同一份数据），**不在请求路径里做 git 操作**。

### 2. scope 是 moni 侧副本，**不参与入库闸门**（关键决策）

> **⛔ 本节结论已被取代（2026-11-25，sprint-55 / v1.0.131）**：scope 现在是
> moni 的**入库准入门**（与领域关键词表 OR；只有显式存储的副本生效）。
> 下面的三条理由与「不作为入库闸门」只作历史记录，**勿据此实现**。原文保持不动。

moni 侧 `fetch_config.scope` 是**可编辑、可保存的副本**，用于"记录 + 展示上游到底在搜什么"。

**它不作为入库闸门** —— 编辑它不会改变 X-bot 的搜索，也不会让 moni 多收或少收任何条目。

理由：

1. 上游已经把范围筛过一遍，feed 里只有上游选中的条目；moni 再按一份手抄名单
   （28 个 handle 无上游可验证真值）过滤 = **静默丢条目**，而收益为零。
2. moni 改不了上游搜索 —— 副本一旦参与闸门，dad 会以为"在 moni 里改了就在上游生效了"，
   与事实不符，属于**危险的假控件**。
3. 默认行为零变化是硬要求；副本参与闸门会让"编辑 scope"变成破坏性操作。

UI 上必须写清三行：①moni 侧副本仅记录与展示，不改变 X-bot 搜索范围；
②上游自 2026-11-12 起自带 wechat 门槛；③moni 门槛是第二道串联闸门，只能更严。

### 3. per-topic 门槛（第二道闸门）

`moni/fetch/sources/xbot_feed.py` 新增纯函数 `evaluate_thresholds(item, thresholds, topic)`：

- `min_followers` / `min_likes` 按 topic 独立判定；**边界含等号**（等于最小值放行）。
- 默认全 0 = 不设限 → **行为零变化**。
- 缺 `author_followers` / `metrics.like`（含**类型不对**）→ **放行 + 计数**，不丢弃。
  门槛是"收紧"闸门，不是"内容匹配"，不能误杀。
- 未知 topic → 放行 + warning。
- 与 `bypass_keyword_filters` **独立**（bypass 只短路黑白名单）。

计数贯通到抓取结果：`threshold_dropped` / `threshold_dropped_by`（形如
`{"wechat:below_min_followers": 157}`）/ `threshold_missing_by`，写进 job 的
`result_json`，UI 抓取结果面板显示「门槛过滤」段落。

**被丢弃的条目不写进 `.xbot_feed_state.json`** → 门槛调松后能重新导入。

### 4. API 契约

- `PUT /api/config/sources/{id}/fetch_config` 对 xbot 源做校验（非法 → 400，**不写库**）：
  topic 名必须在 `{wechat, xcircle}`；`min_followers` / `min_likes` 必须是 `int`
  （**显式排除 bool**）且 `>= 0`；`scope` 字段类型严格。
- **合并语义只对 xbot 源**：只传 `thresholds` 时其余 8 个键仍在。
- **校验只在 xbot 分支内** → 其它信源的 `thresholds` / `scope` 键照常透传，
  维持"整体替换 body"的既有契约。
- 400 时 `sources.fetch_config` **逐字未变**。

### 5. UI

现有「配置管理 → 抓取源 → 抓取配置」弹窗内，新增一个
`x-show="fetchConfigData.source_name === 'xbot_feed'"` 的专属区块：

1. **信源范围 (scope)**：xcircle 账号 textarea + wechat 正则 input +
   wechat 每日查询词 textarea + 来源徽标（`上游导出` / `moni 侧副本` /
   `moni 侧兜底（快照 2026-11-11）`）。
2. **入库门槛 (thresholds)**：wechat / xcircle 两行 × `min_followers` / `min_likes`，
   `type="number" min="0" step="1"`，**不走 raw JSON textarea**。
3. **说明文字三行**（见 §2）。
4. 通用弹窗不受影响：xbot 专属键从通用键循环里排除，其它信源渲染**逐字不变**。
5. 深色模式可读性：只用**已定义**的主题变量（`--card` / `--foreground` / `--border` /
   `--muted` / `--muted-fg`），选择器带 `.fetch-config-xbot` 前缀压过通用
   `input[type=text]`（0,1,1）；textarea `resize: vertical`。
6. <480px 小屏：topic 占满整行，两个数字框并排在下一行（不阶梯错位）。

## 约束（不变量）

- **方案 A 铁律**：门槛与 scope 的默认值放**独立常量**；
  `DEFAULT_FETCH_CONFIG` / `moni/db/migrations/039_xbot_feed_source.sql` /
  `tests/test_migration_039_xbot_feed.py` 的护栏**零改动**。
- **schema 不变（40）、无新表、无新 migration**；状态全落在已有的
  `sources.fetch_config` JSON 列。（schema 40 是 **main 的 migration 040** ——
  `llm_model_mode` 默认 profile —— **本版没有引入 040，也没有新迁移**。）
- **`moni/db/seed_config.py` 零改动**（G-7）—— 见 §边界。
- 不改 `moni/fetch/keyword_match.py` / `filters.py` / `tag_filter.py`，
  不改 X-bot 仓库，不反向同步门槛到 X-bot。
- 不动生产 8900 与生产数据；验证全程走 8901 + 隔离数据副本。

## ⚠️ 已知边界：`moni db init` / `moni db seed-config` 会覆盖整行 `fetch_config`

`moni/db/seed_config.py` 的 sources upsert 用
`fetch_config = COALESCE(excluded.fetch_config, sources.fetch_config)`，而
`excluded.fetch_config` **恒非 null** → seed **一定覆盖**整行 `fetch_config`，
**包含本版新增的 `thresholds` 与 `scope`**。

本版**不加** seed 保护（`seed_config.py` **零改动**，全局 seed 语义不动；
是否加保护由 dad 另拍）。

- **升级用 `python3 -m moni.db.migrate`**（只跑迁移）—— migrate-only 路径**不碰**
  `sources` 行，dad 配的门槛与 scope 在升级后原样保留。
- ⚠️ **不要用 `moni db init` 升级** —— 它 = migrate + seed，会抹掉门槛与 scope。

## 验收

| # | AC | 验证方式 |
|---|---|---|
| AC-1 | scope 端点返回 scope / thresholds / `scope_source`（三态）；404 / 400 分明 | API 测试 |
| AC-2 | 非法门槛 → 400 且**库内容逐字未变** | API 测试（每个用例都断言库未变） |
| AC-3 | 合法门槛 → 200 + 回读一致；只传 thresholds 时其余 8 键仍在；其它信源整体替换语义不变 | API 测试 + 既有测试全绿 |
| AC-4 | 门槛默认全关 → 入库 URL 集合与**改动前**基线逐条相同 | 单元（基线由 pre-change fetcher 采集） |
| AC-5 | 边界含等号；阈值 0 → 永远放行；判定是纯函数 | 单元 |
| AC-6 | 缺字段（含类型不对）→ 放行 + 计数 | 单元 |
| AC-7 | 只配 wechat 不影响 xcircle；未知 topic → 放行 + warning | 单元 |
| AC-8 | 被丢弃条目不进 state 文件；门槛调松后能重新导入 | 单元 |
| AC-9 | 结果 dict 含三个计数键；UI 出现「门槛过滤」段落 | 单元 + 截图 |
| AC-10 | 弹窗内专属区块；其它信源弹窗渲染不变 | 截图（改前/改后/其它源对照）+ 断言 |
| AC-11 | 全量 pytest 绿；schema 仍 40；无新表 | 命令原文 + 输出原文 |
| AC-12 | scope 可编辑可保存；**改 scope 前后入库 URL 集合相同** | API 测试 + 行为测试（跑两遍比集合） |
| AC-13 | `seed_config.py` 零改动；migrate-only 升级保留 thresholds / scope；守卫测试钉死"seed 无 xbot 特判" | 单元 |

> **⛔ AC-12 已被取代（2026-11-25，sprint-55 / v1.0.131）**：
> scope 现在是入库准入门，「改 scope 前后入库 URL 集合相同」不再成立
> —— 显式存储后必须**严格多收**（不变的情况只有「未存储」与「上游导出值」）。
> 现行验收：`tests/test_xbot_scope_api.py::test_scope_admission_behavior_off_vs_on`
> 与 `tests/test_xbot_scope_admission_ingest.py::test_scope_stored_admits_scope_only_items`。
> 上表 AC-12 原文保留作历史。

## 测试

- 新增 `tests/test_xbot_thresholds.py`（25）、`tests/test_xbot_scope_api.py`（…）、
  `tests/test_xbot_scope_ui.py`（…）+ 真实语料 fixture
  `tests/fixtures/xbot_feed/2026-11-11.json`（118 条）→ sprint-35 合计 73 条。
- 全量：`1598 passed / 2 skipped`（含 upstream v1.0.70 的 7 条新测试）。
- AC-4 基线拓宽到 3 份 fixture（125 raw → 47 条入库 URL），且基线由
  **pre-change（main）fetcher** 采集 —— 是真的"改动前"，不是改动后的自我对照。
