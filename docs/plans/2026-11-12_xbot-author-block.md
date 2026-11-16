---
id: 26111203
type: plan
version: v1.0.88
status: 进行中
sprint: 38
date: 2026-11-12
---

# sprint-38 — X-bot 精选详情弹窗「屏蔽作者」(v1.0.88)

> 与 vault `tqob/05-Coding/project-moni/sprints/sprint-38-xbot-author-block-2026-11-12/`
> 同一 id (26111203)。本文件是**仓库内**的交付追溯件；vault 那份是
> 架构稿 / DoD / 决策的权威来源。

## 需求

x-bot 精选（`xbot_feed`）的文章详情弹窗里，可以一键屏蔽作者（按 `@handle`）。
被屏蔽的作者以后不会再出现在精选里，之后也能在管理入口解除屏蔽。

## dad 的四条决定（2026-11-12 15:04 GO IMPL）

1. **存储用方案 B**：新表 `source_author_blocks` + migration 042，`_CURRENT_VERSION` 同步到 42。
2. **旧文章物理删除**（不做软隐藏）：屏蔽时把该作者在 `xbot_feed` 下的已有文章删掉。
   删除前确认框显示「将删除 N 篇已有文章，不可恢复」。关联数据（摘要、核查、pin 等）
   一并处理干净，不留孤儿行，不报错。解除屏蔽只影响以后的入库，旧文章不恢复。
3. **只对 `xbot_feed` 生效**；被屏蔽丢弃单独计入 `author_blocked`，不混进 `threshold_dropped`。
4. 其余按 agy 架构稿：入库时拦截 / 三个独立 REST / 作者 chip 右侧幽灵按钮 /
   先确认再 toast 不做撤销 / 名单管理放在 sprint-35 的 X-bot 配置区。

## 为什么是方案 B（而不是 A）

`sources.fetch_config` 会被 `moni db seed-config` **整行覆盖**
（`moni/db/seed_config.py:507-508` 的 `COALESCE(excluded.fetch_config, sources.fetch_config)`，
且 `excluded` 恒非 null）。**已实测**：往 `fetch_config.blacklisted_handles` 加一个自定义
handle，再跑一遍 seed 的 upsert 语义，名单**被抹回内置 5 个号**。

屏蔽名单是 dad 的个人运营资产，丢失 = 被屏蔽的作者悄悄复活（**有害回退**），
与 sprint-35 门槛/阈值丢失（回落无害默认）后果不同，不能只靠「升级只用 migrate」
的人工纪律兜底 → 独立表绝对免疫。

## 落地范围

| 层 | 改动 |
|---|---|
| DB | `moni/db/migrations/042_source_author_blocks.sql`（新表，幂等）；`_CURRENT_VERSION` 41 → 42 |
| fetcher | `moni/fetch/sources/xbot_feed.py`：`_prepare()` 合并名单（fetch_config ∪ 新表）；`parse()` 命中即 `self._author_blocked += 1`；`run()` 结果带 `author_blocked` |
| API | `moni/web/routes/author_blocks.py`（新）：POST / DELETE / GET `blocked-authors` |
| 前端 | `index.html`（作者 chip 内按钮 + 配置区名单管理）、`app.js`（名单加载/增删 + 确认流程）、`main.css`（幽灵按钮 + 名单样式） |
| 测试 | `tests/test_xbot_author_block.py`（解析/计数）、`tests/test_xbot_author_block_api.py`（端点/删除）、`tests/test_migration_042_source_author_blocks.py`（迁移）、`tests/test_xbot_author_block_ui.py`（UI 契约） |

## 删除的完整影响面（AC-6）

7 张引用表全是 `ON DELETE CASCADE`（`PRAGMA foreign_keys` 是 per-connection，
`moni/db/connection.py:121` → **已实测 cascade 真的生效**）：
`article_tags` / `article_pins` / `article_keyword_hits` / `cases` / `events` /
`fact_check_evidence` / `llm_jobs`。

两个**没有 FK 保护**的孤儿源，必须显式处理：

- `share_tasks.article_ids` 是 **JSON 数组**（`moni/db/schema.sql:65`）→
  CASCADE 管不到 → 显式剔除已删 id（同一数组里其它 id 保留）。
  **dad 2026-11-12 16:02 拍板**：剔空也**保留整行**（生产库 111 行全是终态
  done 93 / failed 10 / cancelled 8 —— 是**历史记录**，删整行等于抹掉操作历史）。
- `articles_fts` 是 **external-content FTS5**（`content='articles'`），
  而 `schema.sql:34` 的注释说「FTS5 triggers keep articles_fts in sync」——
  **实测生产库 `articles` 上没有任何 trigger**（只有 `cases_fts` 的 3 个）
  → 删行不会同步索引 → 显式发一次 FTS5 命令。
  **dad 2026-11-12 16:02 拍板**：用**逐条 `'delete'`** 而非全表 `'rebuild'`
  （rebuild 在 5080 行上实测 ~520ms，每次屏蔽都付不划算）。
  ⚠️ `'delete'` 需要该行的**旧值**（external-content 表不存原文）→ 实现上
  **必须在删 `articles` 行之前**取 rowid+title+publisher+raw_content+summary。
  **实测**：特征词 MATCH 命中 1 → 0，反证索引确有内容（`'the'` 命中 252）。

## 计数口径

`author_blocked` 是**标量 int**（不是 dict）：作者屏蔽是跨 topic 的作者级事件，
用 dict 会把同一条 raw 拆成两项。它与 `threshold_dropped` / `threshold_missing`
**彼此独立、互不包含**，且贯通到抓取结果的 `result_json`（前端结果面板单列一行）。

## 测试先行

先写 4 个测试文件（1148 行）→ 确认 RED（`59 failed, 10 passed`，通过的那 10 个
全是「既有行为不许被破坏」的回归守卫）→ 再写实现 → 转绿（`69 passed`）。

已跑 4 组**负控**（把实现改坏，确认对应测试会红）：关掉 share_tasks 清理 /
删除不限定 source / fetcher 不读 DB 名单 / 计数并入 threshold —— 4 组全部变红，
还原后全绿。

## UI 复审（agy，w52:p8）

**r1 结论 FAIL**（1 个 P0 + 1 个 P1 + 1 个 P2），3 个问题**全部已修**：

| 级别 | 问题 | 修复 |
|---|---|---|
| P0 | 首页首次点「屏蔽作者」报「找不到 X-bot 精选信源」 | `_xbotSourceId()` 加 `src-xbot-feed` 确定性兜底 + 多来源查找（`configSources \|\| sources`） |
| P1 | 确认框 Esc/取消后焦点丢到 `<body>` | 暂存触发元素（**优先 `evt.currentTarget`** —— Safari 点 button 不给焦点，`activeElement` 不可靠），取消时还原 |
| P2 | 深色模式「已屏蔽作者」标题对比度 1.2:1 | `.fetch-config-zh` 改用主题 token（**最终采用上游的 `var(--fg-strong)`**：浅 #0f172a / 深 #f8fafc —— 见「同步上游」一节） |

**r2 结论 PASS**（P0/P1/P2 各 0），agy 端到端实测：P0 首页首点确认框即时弹出
（删除数 12 篇正确）、P1 `document.activeElement` 回到 `.author-block-btn`、
P2 深色 10.1:1 / 浅色 12.2:1，6 项回归无回退。

### 静态资源缓存标记

`app.js` / `main.css` 的 `?v=` 必须 bump —— moni 用它做缓存失效，不 bump 浏览器会拿旧资源，修复对真实用户不生效。
**同步上游后的最终值**：`20261116001`（上游先用过 `20261113014`，本 sprint 取更晚的号，
保证既比上游新、也确实是本次改动触发的失效标记）。

### 行为级测试（不是字符串匹配）

新增 `tests/js/sprint38_ui_harness.js`：用 node 的 `vm` context 按 index.html 的
script 顺序加载**真实的** app.js，直接调用组件方法做断言。
新增 `tests/test_xbot_author_block_ui_r1.py`：pytest 包一层（node 缺失则 skip）。

RED 阶段 harness 真实复现了 P0（返回 `""`）与 P1（`focus()` 0 次）；
GREEN 阶段 harness 6/6、pytest 5 passed。负控累计 6 组全咬住。

## 顺路修掉一个既有 CI flake

`test_sigterm_kills_blocking_child_that_ignores_term` 的 `assert elapsed < 1.0`
是**写死**的，而 worker 看门狗 `sleep 0.8` 决定了 TERM→退出的**耗时下界**
→ 只剩 0.2s 余量，机器一忙就假红（实测 1.032s）。
改成从 worker 脚本里**真实 grace 值推导**预算（`grace + 1.0`），以后调 grace
预算自动跟随。负控：grace 改 1.5 → 新用例红；grace 改 5.0 → 原用例仍红
（真实变慢仍抓得到，不是放水）。

## 同步上游 main（2026-11-16）

11-13 的基线已过期。**重新 fetch 实时 origin/main** 后确认：

| 项 | 11-13 旧状态 | 11-16 实时状态 |
|---|---|---|
| HEAD | `7375e84` | `7375e84`（未变，本 sprint 仍未 commit） |
| origin/main | `adc53b7` | **`d7e6680`**（21 个 commit 未合入） |
| 落后 | 17 | **21** |
| upstream 占用迁移号 | 041（bank_feed） | 041（bank_feed）→ 本 sprint 改 **042** |
| `_CURRENT_VERSION` | 41 | 41 → 本 sprint 改 **42** |
| 最新已发布 tag | v1.0.84 | **v1.0.87**（v1.0.75 从未发布过） |
| 发布版本 | v1.0.75 | **v1.0.88**（v1.0.87 之后的下一个可用号） |

同步方式：`git stash push -u` → `git merge --ff-only origin/main` → `git stash pop`。
7 处冲突逐个按「两边意图都保留」解：migrate.py 版本号 / 3 个版本断言测试 /
STATUS.md（上游 v1.0.76–87 发布记录 + 本 sprint 条目并存）/ main.css（见下）/
index.html（两个 `?v=` 标记取更晚的号）。

**main.css 的 P2 冲突是「上游已用另一种方式修好同一个问题」**：
上游 v1.0.77 UI 规范化把 `.fetch-config-zh` 的硬编码近黑 `#0f172a` 换成了
主题 token `var(--fg-strong)`（`:root` = #0f172a / `.dark` = #f8fafc）。
解算后深色 16.63:1、浅色 17.68:1 —— P2 根因已被上游修掉。
因此**丢弃本 sprint 自带的 `.dark` 覆盖**（同一问题两套并行变量系统 = 主题漂移隐患），
采用上游 token 体系；配套把 P2 断言从「断言实现」改写为「断言解算后的对比度」
（CSS 变量链解算 sRGB → 跟同作用域 `--card` 算 WCAG 对比度），
三遍负控：绿 → 硬编码近黑（红）→ 深色 token 改近黑（红）→ 还原（绿）。

## 最终验证

- sprint-38 专项：**77 passed**
- 全量（同步上游 main 之后）：**1821 passed, 2 skipped, 0 failed**
- 端到端（生产库副本，inode 不同，prod 未动）：2 篇删除、7 张 FK 表引用全 0、
  share_tasks 无残留、FTS posting 已移除、其它作者与其它源零影响、
  `integrity_check ok`
- **未 commit / 未 push**；prod 8900 与预览 8902 均未重启，prod DB 零改动
