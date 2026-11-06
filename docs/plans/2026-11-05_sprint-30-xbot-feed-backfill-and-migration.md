LHMONI-XBOT-PLAN-1

# sprint-30 TECH-SPEC: xbot_feed 回填与迁移 039 (v1.0.68)

- **Sprint ID**: 26110501
- **目标版本**: v1.0.68
- **阶段**: sprint-30
- **日期**: 2026-11-05
- **目标仓库**: `mariusiaowego-commits/moni`
- **基线**: `main` @ `e53956c`（PR #172 squash merge，v1.0.66）
- **状态**: **已实现**（2026-11-05）—— 分支 `feat/sprint-30-xbot-feed-migration`，
  待独立审计（§11 验收标准逐条已核对）
- **前序**: v1.0.66（PR #172）合并了 `xbot_feed` 源，但**没有 migration** —— 新源只进了
  `moni/db/seed_config.py` + `config/sources.yaml`，没进任何存量数据库。

---

## 0. 本文档要解决什么

v1.0.66 落地后有两件事没做完：

1. **文档回填**：PR #172 的审计报告、真实运行数字、sprint evidence 全在
   `/workspace/moni-plan/` 的工作区里，没进仓库 `docs/`。仓库里只有
   `docs/plans/2026-11-03_xbot-feed-source.md` 一份方案文档。
2. **升级路径缺失**：新源靠 `moni db seed-config` 落地。对**已经跑过 seed 的存量库**，
   `seed()` 会 `ON CONFLICT DO UPDATE` 覆盖 `display_name` / `url` /
   `fetch_interval_min` / `notes` / `fetch_config`（见 §4.2 实测）——
   用户在 `/config` 改过的东西会被**静默重置**。而"跑不跑 seed-config"这个决定
   现在没有文档说明，升级的人只能猜。

本 sprint 做两件事：把 v1.0.66 的证据补进仓库，并加一个 **migration 039**，
让升级**不再依赖 seed-config**。

> **升级命令 = migrate-only**：`python3 -m moni.db.migrate`（只跑迁移，不跑 seed）。
> **不要用 `moni db init` 做升级** —— 它是 `migrate() + seed_llm + seed_config`，
> 会把用户在 `/config` 改过的源字段重置（§4.2 实测）。详见 §7.1。

---

## 1. 触发与实测数据

### 1.0 「升级后没有这个源」到底会坏什么（实测）

这是本 sprint 存在的理由，先把症状量化 —— 下面是 PLAN 阶段在一次性库里
**实测**出来的（v38 库、跑过 seed、然后把 `xbot_feed` 源行删掉，模拟
"升级后没跑 seed-config 的存量库"）：

| 检查点 | 源行**存在** | 源行**不存在** |
|---|---|---|
| `/config` 抓取源列表 | 14 个源 | **13 个源**（`xbot_feed` 不显示） |
| `moni/fetch/filters.py::_is_source_bypassed("xbot_feed")` | `True` | **`False`** |
| `tag_filter._load_bound_tag_names_for_source` | 4 个 tag | **0 个** |
| 全局黑名单命中「微信」的一篇文章 | **kept=1**（bypass 短路黑白名单） | **kept=0**（`dropped_global_blacklist=1`） |

**关键点**：fetcher 自己的默认值 `DEFAULT_FETCH_CONFIG["bypass_keyword_filters"]` 是
`True`（`moni/fetch/sources/xbot_feed.py:178`），但 **`_is_source_bypassed` 只读
DB**（`moni/fetch/filters.py:36-58`）。源行不存在 → DB 查不到 → 返回 `False`
→ **全局/源级黑白名单闸门重新生效** → 精选内容被黑名单误杀。

也就是说：**没有 migration 的话，存量库升级后要么看不到这个源，要么它被
全局黑名单误杀** —— 而且是**静默**的（抓取不报错，只是少文章）。
这正是 migration 039 要解决的。

### 1.1 为什么现在做

- PR #172 已于 2026-11-03 squash merge（`e53956c`），远端分支已删。
- `STATUS.md:7` 现在还写着 `v1.0.66 — xbot_feed 源（X-bot 精选桥接）(2026-11-03 · **IN REVIEW**)`
  —— 状态与仓库事实（已合并）**不一致**，需收尾。
- `STATUS.md:9` 写着 `**schema**: 38（无新迁移）` —— 这句话在 v1.0.66 当时是对的
  （没新增 articles 列），但**新源本身需要落库**，38 不够。

### 1.2 真实运行数字（独立复算，非转抄）

审计报告 `/workspace/moni-plan/pr172-audit.md` 第 8 节给了真实运行表。本 PLAN 阶段用
`/workspace/xbot-feed/feed/2026-11-03.json` **独立复算了一遍**，结论完全一致：

| 项目 | 数值 | 复算方式 |
|---|---:|---|
| 上游导出 items | **203** | `len(payload["items"])`，id 唯一（203 个不同 id） |
| topic 分布 | wechat 46 / xcircle 157 | `payload["counts_by_topic"]` 与逐条计数一致 |
| 作者黑名单过滤 | **6**（全是 wechat topic） | 5 个内置 handle 精确匹配命中 6 条 |
| 关键词未命中（不入库） | **126**（wechat 9 / xcircle 117） | 用审计里的测试关键词跑 `keyword_match.keyword_matches` |
| **第 1 次 inserted** | **71**（wechat 31 / xcircle 40） | 203 − 6 − 126 = 71 |
| 零 tag 文章 | **0** | 命中的 71 条全部带 tag |
| tag 分布 | wechat 28 / miniprogram 21 / general-tech 37 / ai-fintech 7 | 合计 93（多 tag 文章各计一次） |
| 跨 topic 误打标签 | **0** | xcircle 无 wechat/miniprogram，wechat 无 general-tech/ai-fintech |
| **第 2 次（rerun）inserted** | **0** | 已入库的 71 条记进状态文件（`moni/fetch/sources/xbot_feed.py:110` 的 `STATE_FILENAME`），不再拉 |

> **复算方法**：用 `moni/fetch/keyword_match.py::keyword_matches`（唯一的匹配引擎）
> + 审计记录的测试关键词（wechat: 微信/WeChat/公众号/视频号/微信支付；miniprogram:
> 小程序/mini program/miniprogram；general-tech: AI/大模型/LLM/开源/Agent/模型；
> ai-fintech: 量化/投资/A股/基金/金融），在 `2026-11-03.json` 上重放。
> 数字与审计报告 §8 **逐项吻合**。

### 1.3 这个数字怎么读（三条要注意的事）

1. **71/203 不是"命中率低"，是闸门在工作**：126 条没命中的是**候选 tag 当前关键词**
   没命中（xcircle 117 条占大头，因为 `general-tech` / `ai-fintech` 的关键词是
   AI×金融向的）。用户改完关键词它们下一轮还能进 —— 被挡掉的条目不写 state。
2. **Mac 上的实际条数取决于 Mttt 在 `/config` 里配的关键词**。上面用的是**测试关键词**，
   真实库的关键词不同，条数就不同。**机制**一样，**数字**不是承诺。
3. **6 条黑名单里 `jkghzone` 出现 2 次**（同一账号两条不同推文），其余 4 个 handle 各 1 条。

---

## 2. 文件清单（IMPL 要创建/修改的东西）

### 2.1 新建

| 路径 | 内容 | 依据 |
|---|---|---|
| `docs/plans/2026-11-05_sprint-30-xbot-feed-backfill-and-migration.md` | 本文档（已创建） | sprint-28/29 惯例 |
| `docs/plans/2026-11-05_sprint-30-xbot-feed-backfill-and-migration-AUDIT.md` | v1.0.66 的审计回填（§3） | `docs/plans/2026-09-01-config-interaction-upgrade-AUDIT.md` 惯例 |
| `docs/sprint-evidence/sprint-30/` | 真实运行证据（§3.4） | `docs/sprint-evidence/sprint-27/` 惯例 |
| `docs/sprint-evidence/sprint-30/RUN.md` | 真实运行记录：命令、输出、数字表 | 参照 `docs/sprint-evidence/sprint-27/` 的目录粒度；`RUN.md` 是本 sprint 新起的命名（仓库里此前没有） |
| `docs/sprint-evidence/sprint-30/realdb-migrate.md` | 真实库**副本**上的 039 迁移实测（before/after diff，不含个人数据） | sprint-27-hotfix「生产库副本重放」惯例 |
| `docs/sprint-evidence/sprint-30/xbot-feed-import-stats.json` | 机器可读的运行统计 | sprint-25 `*.json` 惯例 |
| `moni/db/migrations/039_xbot_feed_source.sql` | 新源 + 4 个 tag 绑定的幂等插入（§5） | migration 037/038 惯例 |
| `tests/test_migration_039_xbot_feed.py` | 039 的回归测试（§6） | `tests/test_migration_038_cv_acronyms.py` 惯例 |

### 2.2 修改

| 路径 | 改动 | 为什么 |
|---|---|---|
| `moni/db/migrate.py` | `_CURRENT_VERSION` 38 → **39** | 新增了 migration 文件；`moni/db/migrate.py:21` |
| `moni/db/seed_config.py` | **只改 `notes` 文案一个字符串**（去掉字面量里的 `;`，见 §5.4.1） | 039 的 notes 必须与 seed 逐字一致（用例 11），且两边都不能含 `;` |
| `tests/test_migration_030_ghost_cleanup.py` | `== 38` → `== 39`（2 处断言：L114、L248）+ L115 的报错文案 | 写死版本号的测试必须同步（AGENTS.md 铁律 2） |
| `tests/test_migration_036_36kr_source_id.py` | `== 38` → `== 39`（L194） | 同上 |
| `tests/test_migration_037_cn_indie_tags.py` | `== 38` → `== 39`（L73） | 同上 |
| `tests/test_migration_038_cv_acronyms.py` | `== 38` → `== 39`（L60） | 同上 |
| `tests/test_llm_usage_counters.py` | `== 38` → `== 39`（L123） | 同上 |
| `README.md` | 升级说明：不再需要 seed-config（§7.1） | — |
| `USER_GUIDE.md` | 升级说明 + seed-config 重置字段的警告（§7.2） | — |
| `STATUS.md` | v1.0.68 状态节；v1.0.66 的 `IN REVIEW` 改成 `SHIPPED`；schema 38 → 39 | — |
| `docs/sprint-evidence/README.md` | 内容表加 `sprint-30/` 一行 | 归档索引 |

> **版本号写死的地方：5 个文件、6 处断言**（`grep -rn "== 38" tests/` 实测）：
>
> | 文件 | 行 | 备注 |
> |---|---|---|
> | `tests/test_migration_030_ghost_cleanup.py` | 114、248 | 另有一处**报错文案** L115（`f"_CURRENT_VERSION must be 38 for v1.0.65..."`），也要一起改 |
> | `tests/test_migration_036_36kr_source_id.py` | 194 | |
> | `tests/test_migration_037_cn_indie_tags.py` | 73 | 行尾注释 `# v1.0.65 bumped it to 038` 也要更新 |
> | `tests/test_migration_038_cv_acronyms.py` | 60 | |
> | `tests/test_llm_usage_counters.py` | 123 | 行尾注释提到 v1.0.65 / 038，也要更新 |
>
> `tests/test_db.py:98` 用的是 `_CURRENT_VERSION` 常量，**不用改**。
> `tests/test_events.py` / `tests/test_cases.py` 用的是 `>=` 比较，**不用改**。
> **实施时的 checklist：`grep -rn "== 38" tests/` 应该只剩 `tests/test_migration_039_xbot_feed.py`
> 里那一处 `assert old_version == 38`** —— 那是**故意**断言"旧代码建出来的库
> 是 schema 38"，不是写死的版本号常量。其余（5 个文件、6 处断言 + 1 处报错文案）
> 必须全部改成 39。

### 2.3 不动

- `moni/fetch/sources/xbot_feed.py` —— 抓取逻辑一行不改（本次是文档回填 + 迁移）。
- `config/sources.yaml` —— 已有 `xbot_feed` 条目，不改。
- `moni/web/routes/config_sources.py` —— 已有 `xbot_feed` 默认值，不改。

> **`moni/db/seed_config.py` 只动一处**：`notes` 文案里的 `;`（§5.4.1）。
> seed 的**逻辑**一个字不改（它仍是新库的来源）。
> ⚠️ `seed_config.py:311` 的 notes 与 039 的 notes 必须**逐字相同**（用例 11 会断言），
> 所以这两处要**一起改**，不能只改 migration。

---

## 3. 文档回填方案（scope 1）

### 3.1 三份产物的分工

| 产物 | 讲什么 | 不讲什么 |
|---|---|---|
| `docs/plans/2026-11-03_xbot-feed-source.md`（**已存在**） | v1.0.66 的**设计方案** | 审计结论、真实运行数字 |
| `docs/plans/2026-11-05_...-AUDIT.md`（**新建**） | PR #172 的**独立审计回填**：9 blocking / 6 should-fix / 8 nits 的处置 | 设计推导 |
| `docs/sprint-evidence/sprint-30/`（**新建**） | **真实运行证据**：命令、数字、产物 | 设计、审计 |

### 3.2 现有 `2026-11-03_xbot-feed-source.md` 要不要改名/加链接？

**决定：不改名，加链接。** 理由：

1. **改名会断外部引用**。该文件带 Obsidian frontmatter（`id: 26110301`），
   说明它被双写进用户的 Obsidian vault。改文件名会让 vault 里的链接指向空。
2. **它的自我定位是"方案"，没被审计覆盖**。PR #172 的审计（S-6）改写了它的
   第 4/5/6/7/8/9 节并新增第 11 节（"审计修复后的测试结果"），但文件本身
   仍是**当时的设计方案**，不是审计报告。把审计内容塞进去会让一个文件承担两种角色。
3. **`-AUDIT.md` 是本仓库已有的惯例**：`docs/plans/2026-09-01-config-interaction-upgrade-AUDIT.md`
   与 `docs/plans/2026-09-01-config-interaction-upgrade.md` 配对存在，方案与审计分开。

**具体动作**：

- 在 `2026-11-03_xbot-feed-source.md` 顶部（frontmatter 之后、标题之前）加一行指针：

  ```markdown
  > **审计与真实运行**：见 `docs/plans/2026-11-05_sprint-30-xbot-feed-backfill-and-migration-AUDIT.md`
  > （PR #172 独立审计回填）与 `docs/sprint-evidence/sprint-30/RUN.md`（真实运行数字）。
  ```

- 同时把它的 frontmatter `status` 从 `已实现（PR #172，未合并）` 改成
  `已实现（PR #172 已合并 e53956c，v1.0.66）` —— 现在这句是**假的**。
- 在新的 sprint-30 plan 里反向链回它（本文档 §2.1 已隐含）。

### 3.3 `-AUDIT.md` 的内容大纲

从 `/workspace/moni-plan/pr172-audit.md`（509 行）**压缩**而来，不要整篇搬。
目标 150–220 行，保留可核查的结论，砍掉审计过程的絮述。

```
# sprint-30 AUDIT: PR #172 xbot_feed 源独立审计回填

- 审计对象: PR #172 (feat/xbot-feed-source → squash merge e53956c)
- 原始审计报告: /workspace/moni-plan/pr172-audit.md (2026-11-03)
- 基线 / 修复后测试: 1414 passed / 2 skipped → 1456 passed / 2 skipped
- 结论: 9 blocking + 6 should-fix 全修完 → 可合并（已合并）

## 1. 结论摘要
   严重度表: blocking 9 / should-fix 6 / nit 8

## 2. Blocking（9 条，逐条一句 + commit）
   B1  tag 闸门被 bypass 架空 → 改成"候选 tag + 当前关键词真实命中"  59553f2
   B2  url LIKE '%status/{id}%' 前缀误命中 → 精确尾部匹配           75983af
   B3  可变默认参数 / 类属性共享可变状态 → None 默认 + 实例属性       75983af
   B4  obsidian_push 把整个 xbot_feed 当微信生态 → 改按文章 tag 判    c171dfe
   B5  fetch_config 默认值抄三处 → DEFAULT_FETCH_CONFIG 单一来源     9b520f9
   B-6 git repo_url / clone_dir 注入防护                            329814d
   B-7 git 输出凭据脱敏                                             329814d
   B-8 多余的 bypass-hit 过滤 → 直接透传真实命中                     59553f2
   B-9 scripts/make_xbot_feed_icon.py 不符合惯例 → 删脚本留 png       a857ea7

## 3. Should-fix（6 条，逐条一句 + commit）
   S-1 删掉死的 state.files 字段 + 修正 docstring                   a857ea7
   S-2 max_days 改成按文件名真实日期判断                             a857ea7
   S-3 _source_label 语义修正                                       c171dfe
   S-4 app.js sortOrder 归到"导入型"分组                             a857ea7
   S-5 config/sources.yaml crawler_type: rss → json                  a857ea7
   S-6 STATUS.md / 方案文档 / USER_GUIDE 同步新机制                   a857ea7

## 4. Nit（8 条：处置 + 状态）
   N-1 保留重复（follow_builders 的 URL 归一逻辑未抽公共）      保留，文档注明
   N-2 topic_tags() 语义改为"候选"                             已改 docstring
   N-3 类属性 → 实例属性                                       部分修
   N-4 fixture latest.json 与日期文件逐字节相同（4.4 KB）     保留
   N-5 图标 19 KB 偏小                                        保留
   N-6 WECHAT_SOURCES 硬编码 → WECHAT_TAGS                    随 B4
   N-7 moni/fetch/sources/follow_builders.py:152 同款 LIKE 前缀 bug —— 不在本 PR 范围，见下文 §5
   N-8 loop.sh 的 $? 被 echo 吞掉                              已由编排者修（rc=$?）

## 5. 未修项与后续
   N-7: moni/fetch/sources/follow_builders.py:152 的 url LIKE '%status/{id}%' 前缀误命中
        —— 与 B2 同源，但改动会牵动另一个源，本 PR 不做
        —— 建议单独 PR，不要塞进本 sprint
   N-8: /home/box/.local/share/xbot-feed/loop.sh 退出码记录
        —— 已由编排者于 2026-11-03 修（rc=$?），本仓库不涉及

## 6. 真实运行
   指向 docs/sprint-evidence/sprint-30/RUN.md

## 7. 结论
   可合并（已合并 e53956c）。 nit 不阻塞。
```

> **注意 `/workspace/moni-plan/pr172-audit.md` 第 23–39 行有一段「本报告的修复状态说明」**，明说初版
> 声称"已全部修掉"但当时**一行代码都没改**。回填到仓库时**不要照抄那句"已修"**，
> 要照抄**实际 commit hash**（审计报告 §7 的表格有）。

### 3.4 `docs/sprint-evidence/sprint-30/` 的内容

目录布局**参照** `docs/sprint-evidence/sprint-27/` 的目录粒度，但文件命名按本 sprint 的
`RUN.md` + `*.json` 约定 —— 仓库里目前**没有任何 `RUN.md`**（`find docs/sprint-evidence -name "RUN.md"` → 0 命中），
`sprint-27/` 里只有 `AUDIT.md`。**`RUN.md` 是本 sprint 新起的命名**，在 evidence README 里注明：

```
docs/sprint-evidence/sprint-30/
├── RUN.md                     # 真实运行记录（人读）—— 本 sprint 新起的命名
├── xbot-feed-import-stats.json # 机器可读统计（脚本产物）
└── realdb-migrate.md          # 真实库副本上的迁移实测（不含个人数据）
```

**`RUN.md` 内容要点**：

1. **环境**：一次性 `MONI_DATA_DIR`（没碰 Mttt 的真实库）。这里是**建全新空库**，
   所以 `moni db init`（= migrate + seed）是**正确**的用法 ——
   跟"存量库升级"是两回事，**升级一律用 `python3 -m moni.db.migrate`**（§4.3.1）。
   然后 `uv run moni fetch --source xbot_feed`。
2. **命令原文**（可复现）。
3. **数字表**（§1.2 的表）。
4. **tag 分布 + 跨 topic 误打标签 = 0**。
5. **rerun = 0**，并解释为什么（132 条被过滤的每轮重新评估，便于改关键词后补入；
   已入库的 71 条记在 state 的 `imported_ids`，不再拉）。
6. **关键词说明**：用的是**测试关键词**（列出 4 组），**Mac 生产库的数字会不同**，
   机制一样。这一条必须写进证据文件，否则下一个人会拿 71 当基线。
7. **复算方法**：本 PLAN 阶段用 `keyword_match.keyword_matches` 在
   `feed/2026-11-03.json` 上重放，数字与审计逐项吻合。

**`xbot-feed-import-stats.json`**（字段名自己定，但要能重跑生成）：

```json
{
  "sprint": "sprint-30",
  "source": "xbot_feed",
  "feed_file": "2026-11-03.json",
  "exported": 203,
  "by_topic": {"wechat": 46, "xcircle": 157},
  "blacklisted": 6,
  "no_keyword_hit": {"total": 126, "wechat": 9, "xcircle": 117},
  "inserted": {"total": 71, "wechat": 31, "xcircle": 40},
  "tag_distribution": {"wechat": 28, "miniprogram": 21,
                       "general-tech": 37, "ai-fintech": 7},
  "zero_tag_articles": 0,
  "cross_topic_mislabel": 0,
  "rerun_inserted": 0,
  "note": "测试关键词下的数字；生产库关键词不同，条数会不同"
}
```

> `docs/sprint-evidence/README.md` 的 "Contents" 表要加一行：
> `| sprint-30/ | sprint-30 evidence | v1.0.66 xbot_feed 真实运行：203 导出 / 71 入库 / rerun 0 |`

---

## 4. 为什么升级不能靠 seed-config（scope 3 的事实基础）

### 4.1 seed-config 的双轨制

- `config/sources.yaml` 是**调度器真正读的文件**（`moni/fetch/scheduler.py:76-86`）。
- DB `sources` 表是**配置面板**（`moni/web/routes/config_sources.py`）读的数据源。
- `moni db seed-config` 写的是 DB 那一半。

### 4.2 实测：seed 会重置用户改过的字段

PLAN 阶段在一次性库里实测（seed 前改、seed 后读）：

| 字段 | seed 前（用户改过） | seed 后 | 结论 |
|---|---|---|---|
| `sources.is_active` | 0 | **0** | ✅ 保留（`seed_config.py:489` 只在 INSERT 时用 `is_active`，`ON CONFLICT` 子句**不含** `is_active`） |
| `sources.display_name` | `MY-XBOT` | `X-bot 精选` | ❌ **被重置** |
| `sources.fetch_interval_min` | 15 | 60 | ❌ **被重置** |
| `sources.notes` | 我的备注 | seed 默认文案 | ❌ **被重置** |
| `sources.url` | `https://example.com/x` | 上游仓库地址 | ❌ **被重置** |
| `sources.fetch_config` | `{"max_days": 3, ...}` | seed 默认 | ❌ **被重置** |
| `source_tags` 少一个绑定 | 3 个 | **4 个**（缺的被补回） | ❌ **被加回** |
| `tag_definitions.keywords` | `["我的词"]` | `["我的词"]` | ✅ **不动**（`seed_config.py:471-479` 的 `ON CONFLICT` 子句不含 `keywords`） |
| **`tag_definitions.display_name`** | **我的名字** | **微信生态** | ❌ **被重置**（S-4） |
| **`tag_definitions.description`** | **我的描述** | **微信公众号/小程序/企业微信** | ❌ **被重置**（S-4） |
| **`tag_definitions.color`** | **#000000** | **#7C3AED** | ❌ **被重置**（S-4） |
| **`tag_definitions.is_active`** | **0（用户停用的 tag）** | **1（被重新启用）** | ❌ **被重置**（S-4）—— **闸门重新打开** |

> **S-4 的实测记录**（PLAN 阶段实跑，seed 前改、seed 后读）：
>
> ```
> BEFORE tag wechat: {'display_name': '我的名字', 'description': '我的描述',
>                      'color': '#000000', 'is_active': 0, 'keywords': '["我的词"]'}
> AFTER  tag wechat: {'display_name': '微信生态', 'description': '微信公众号/小程序/企业微信',
>                      'color': '#7C3AED', 'is_active': 1, 'keywords': '["我的词"]'}
> ```
>
> `is_active=0`（用户停用的 tag）被重新启用 = **闸门重新打开** —— 这跟"用户停用的
> 源被重新启用"是同一类问题。§7.2 的 USER_GUIDE 文案必须覆盖这一条。

根因在 `moni/db/seed_config.py:497-508` 的 `ON CONFLICT(id) DO UPDATE SET` 子句：
它显式写了 `name` / `display_name` / `source_type` / `url` / `fetch_interval_min` /
`notes` / `fetch_config`，**没有** `is_active`（所以停用状态能保住），
也没有 `keywords`（所以用户配的关键词能保住）。

**但 `tag_definitions` 是另一条 `ON CONFLICT`**（`seed_config.py:466-478`）：

```python
ON CONFLICT(name) DO UPDATE SET
    display_name = excluded.display_name,
    description  = excluded.description,
    color        = excluded.color,
    is_active    = 1          # ← 用户停用的 tag 会被重新启用
```

它**没有** `keywords`（所以用户的关键词能保住），但**有** `is_active = 1`
（所以用户停用的 tag 会被重新启用）。

`source_tags` 的语义是 `INSERT ... ON CONFLICT(source_id, tag_name) DO NOTHING`
（`seed_config.py:518-522`）—— **只增不删**，所以用户在 `/config` 里取消的
tag 勾选会被补回来。这跟 `USER_GUIDE.md:1348-1351` 已经写的一致。

> **S-4 补一句**：上表"3 个 → 4 个"里的 4 个，指的是 **seed 的
> `_DEFAULT_SOURCE_TAGS` 全集**（14 个源、34 条绑定），**不是** xbot_feed
> 自己那 4 条（`wechat` / `miniprogram` / `general-tech` / `ai-fintech`）。
> `source_tags` 的 ON CONFLICT 是**按 (source_id, tag_name)** 匹配的，
> 所以补回的是"这个源缺的那几条绑定"，别的源不受影响。

### 4.3 结论

> **v1.0.68 起，升级不再需要跑 `moni db seed-config`** —— 新源由 migration 039 落库，
> migration 只做 `INSERT OR IGNORE`，**不覆盖任何已有行**。
> 反过来，**如果用户已经跑过 seed-config 且改过 xbot_feed 的字段，
> 跑一次 seed-config 就把这些改动抹掉了** —— 这一点要写进 README / USER_GUIDE。

#### 4.3.1 ⚠️ 升级命令必须是 migrate-only，不能是 `moni db init`

**`moni db init` 不只是迁移，它会跑 seed —— 而 seed 恰好会抹掉用户的改动。**

`moni/cli/main.py:45-50`：

```python
def cmd_init() -> None:
    migrate()
    from ..db.seed_llm import seed as seed_llm_seed
    seed_llm_seed()
    from ..db.seed_config import seed as seed_config_seed
    seed_config_seed()      # ← 一定会跑 seed_config
```

而且 `moni/cli/main.py:778-779` 是 `if args.subcmd == "db": migrate()` ——
**所有 `moni db *` 子命令（`stats` / `init` / `seed-config` / `export`）都会先跑 `migrate()`**，
但只有 `init` 会额外跑 seed。

**实跑复现**（一次性库里把 `src-xbot-feed` 改过，然后跑 `moni db init`）：

```
BEFORE: display_name='MY-XBOT', fetch_interval_min=15,
        notes='我的备注', fetch_config='{"max_days": 3}'
        links: ['general-tech', 'miniprogram', 'wechat']   ← ai-fintech 被用户取消
$ moni db init
AFTER : display_name='X-bot 精选', fetch_interval_min=60,
        notes='X-bot 精选 feed (...)…', fetch_config='{"repo_url": …, …}'
        links: ['ai-fintech', 'general-tech', 'miniprogram', 'wechat']
```

**用户的 `display_name` / `fetch_interval_min` / `notes` / `fetch_config` 被静默重置，
被取消的 tag 绑定被补回**（`seed_config.py:497-508` 的 `ON CONFLICT DO UPDATE` +
`:516-523` 的 `ON CONFLICT DO NOTHING`，见 §4.2）。这与 §4.2 的实测表完全一致 ——
区别只是 `db init` 把这个行为**默认触发**了。

> 对照组：**migrate-only 路径对同一个库，用户改动一个字都不动**
> （`display_name` / `fetch_interval_min` / `notes` / `fetch_config` 全部原样，
> 取消掉的绑定也不会被补回）。这条对照是 §6.1 用例 20/21 的设计依据。

**因此：升级命令 = migrate-only。** 仓库里已有现成的入口
（`moni/db/migrate.py:124-133` 的 `__main__` 块）：

```bash
/usr/local/bin/python3 -m moni.db.migrate   # 只跑迁移，不碰 sources/tag_definitions
```

**实跑确认**（空库 + `MONI_DATA_DIR` 指到临时目录）：

```
$ python3 -m moni.db.migrate
Applying migration 038_ai_fintech_cv_acronyms.sql (v37 → v38) ...
Migration complete. schema_version = 38
sources=1  tag_definitions=0  source_tags=0
```

→ **全量重跑**基表 `schema.sql`（幂等）+ 跑迁移，**不 seed 任何 sources / tag_definitions**，
用户数据一个字都不动。（`sources=1` 来自 migration 012 的 `src-bestblogs`，
不是 seed。）

**三条硬约束（实施时必须照做）：**

1. **§7.1 / §7.2 / §11 里的升级指令一律改成 `python3 -m moni.db.migrate`**，
   不许再出现 `moni db init` 作为升级步骤。
2. **README 必须写明 `moni db init` = migrate + seed**，并明确"升级不要跑它"。
   （保留 `db init` 本身的行为 —— 那是本 sprint 范围外的行为变更，
   见 Q-O-10。）
3. **加一条回归测试（用例 20/21）**：走真实 CLI 路径，断言用户的
   `display_name` / `fetch_interval_min` / `notes` / `fetch_config` 逐字段不变。
   这条测试是 B-1 的护栏。

---

## 5. Migration 039 设计（scope 2）

### 5.1 目标与非目标

**目标**：让**没跑过 seed-config 的存量库**在升级后直接就有 `xbot_feed` 这个源，
且**不覆盖任何用户已有的数据**。

**非目标**：
- 不改 `xbot_feed.py` 的抓取逻辑。
- 不动 `tag_definitions.keywords`（用户的关键词）。
- 不 `UPDATE` 已存在的 `sources` 行（连 `is_active` 都不碰）。
- 不新建 tag（4 个绑定 tag 都是**已有**业务 tag）。

### 5.2 表与列（实测确认）

| 表 | 关键列 | 出处 |
|---|---|---|
| `sources` | `id` (PK) / `name` (UNIQUE) / `display_name` / `source_type` / `url` / `fetch_interval_min` / `is_active` / `notes` / `fetch_config` / `created_at` / `updated_at` | `moni/db/migrations/004_sources_and_tags.sql:14-25`（`fetch_config` 由 `012_bestblogs_fetch_config.sql:5` 加） |
| `source_tags` | `source_id` / `tag_name` / `created_at`，PK `(source_id, tag_name)`，两个 FK 都 `ON DELETE CASCADE` | `moni/db/migrations/004_sources_and_tags.sql:29-36` |
| `tag_definitions` | `name` (PK) / `display_name` / `description` / `color` / `created_at` / `is_active` / `keywords` | 基表 `moni/db/schema.sql:41-47`；`keywords` 由 `moni/db/migrations/006_tag_keywords.sql:20` 加；`display_name` 由 `moni/db/migrations/007_tag_display_name.sql:24` 加 |

**关键实测**：`PRAGMA foreign_keys = ON` 是连接级默认（`moni/db/connection.py:121`），
而 **`INSERT OR IGNORE` 不会吞掉外键违例** —— 实测（SQLite 3.53.1）：

```
INSERT OR IGNORE INTO source_tags (...) VALUES ('src-x','nope',...)  →  FOREIGN KEY constraint failed
INSERT OR IGNORE INTO source_tags (...) VALUES ('src-missing','wechat',...)  →  FOREIGN KEY constraint failed
```

**结论**：`source_tags` 的插入**不能**裸写 `INSERT OR IGNORE`，必须先保证
`tag_definitions` 里有那 4 行（用 `INSERT ... SELECT ... FROM tag_definitions`
天然满足 —— 目标不存在就不会产生行）。

### 5.3 值（与 `seed_config.py` / `sources.yaml` 逐字一致）

| 字段 | 值 | 出处 |
|---|---|---|
| `id` | `src-xbot-feed` | `seed_config.py:305` |
| `name` | `xbot_feed` | `seed_config.py:306` |
| `display_name` | `X-bot 精选` | `seed_config.py:307` |
| `source_type` | `json` | `seed_config.py:308` |
| `url` | `https://github.com/mariusiaowego-commits/xbot-feed.git` | `seed_config.py:309` |
| `fetch_interval_min` | `60` | `seed_config.py:310` |
| `is_active` | `1`（**只在 INSERT 时**，见 §5.5） | `seed_config.py:489` 缺省 1 |
| `notes` | 见 §5.4.1 的文案（与 `seed_config.py:311` 逐字一致） | `seed_config.py:311` |
| `fetch_config` | `DEFAULT_FETCH_CONFIG` 的 JSON（`ensure_ascii=False`） | `moni/fetch/sources/xbot_feed.py:170-191` |
| `source_tags` | `wechat` / `miniprogram` / `general-tech` / `ai-fintech` | `seed_config.py:385-388` |

**`fetch_config` 的 JSON 原文**（实测 `json.dumps(DEFAULT_FETCH_CONFIG, ensure_ascii=False)` 的输出，
378 字符）：

```json
{"repo_url": "https://github.com/mariusiaowego-commits/xbot-feed.git", "clone_dir": "", "bypass_keyword_filters": true, "blacklisted_handles": ["jkghzone", "sensenxnyz", "Eason3110188520", "duizhanggege", "HunchoJ01"], "topic_tags": {"wechat": ["wechat", "miniprogram"], "xcircle": ["general-tech", "ai-fintech"]}, "max_days": 30, "git_timeout": 120, "skip_existing_urls": true}
```

> **单值来源**：`DEFAULT_FETCH_CONFIG` 是审计 B5 定的唯一来源（`xbot_feed.py:170`），
> `seed_config.py:312-315` 和 `config_sources.py:109` 都引用它。
> **migration 的 SQL 是静态文本**，没法 import Python 常量 —— 这是**第四份拷贝**
> （fetcher / seed / web / migration SQL），所以必须有一条测试把它钉住
> （§6.1 的 `test_039_fetch_config_matches_default`），
> 否则改了 `DEFAULT_FETCH_CONFIG` 而忘了改 migration，DB 里的默认值会漂。
> **这一条要在 `-AUDIT.md` 里记成已知的"三处 → 四处"风险，并写明测试是唯一的护栏。**

### 5.4 SQL 草案

```sql
-- 039_xbot_feed_source.sql - v1.0.68 (sprint-30)
--
-- v1.0.66 (PR #172) 加了 xbot_feed 源，但只进了 seed_config.py 和
-- config/sources.yaml —— 没有 migration。后果：
--   * 从没跑过 `moni db seed-config` 的存量库，升级后 sources 表里没有
--     这个源 → /config 面板看不到，moni fetch --source xbot_feed 找不到
--     它的 fetch_config；
--   * 跑 seed-config 能补上，但 seed 的 ON CONFLICT DO UPDATE 会重置
--     用户在 /config 改过的 display_name / url / fetch_interval_min /
--     notes / fetch_config，并把用户取消掉的 tag 绑定补回来
--     （实测见 docs/plans/2026-11-05_sprint-30-...-AUDIT.md §4）。
--
-- 这个迁移让升级**不再依赖 seed-config**：只 INSERT，绝不 UPDATE。
--
-- 语义（与 seed_config 的差别是刻意的）：
--   * INSERT ... SELECT ... WHERE NOT EXISTS —— 已存在的行**一个字段都不碰**。
--     用户把源停用 (is_active=0) 就还是停用；改了 display_name 就还是改过的名字。
--     （不用 INSERT OR IGNORE：UNIQUE 违例会被它静默吞掉，源行没建成
--     迁移却"成功"了 —— 见下面的 sources.name 说明。）
--   * 不碰 tag_definitions.keywords —— 用户的关键词是用户的数据。
--   * 不新建 tag —— 绑定的 4 个都是已有业务 tag；目标不存在就不建链接
--     （source_tags 有 FK 到 tag_definitions，INSERT OR IGNORE **不吞**
--     外键违例，见下）。
--
-- 为什么 sources 用 INSERT ... SELECT ... WHERE NOT EXISTS:
--   sources.name 是 UNIQUE（004_sources_and_tags.sql:15）。用户可能删掉
--   seed 建的行、用**自己的 id** 在 /config 重建了同名源（"新建 source"
--   表单允许用户填 id）→ name='xbot_feed' 已被占用。此时裸 INSERT 抛
--   UNIQUE 违例 → 整个迁移失败 → schema_version 卡在 38 → 所有
--   `moni db *` 子命令持续失败。INSERT OR IGNORE 更糟：UNIQUE 违例被
--   **静默吞掉** → 源行没建成 → 迁移却"成功"。
--   所以显式检查 id 和 name，把"用户已占用这个名字"当成**已满足**。
--
-- 为什么 source_tags 用 INSERT ... SELECT FROM sources JOIN tag_definitions:
--   source_tags.tag_name 有 FK → tag_definitions(name) ON DELETE CASCADE
--   （004_sources_and_tags.sql:35），而连接默认 PRAGMA foreign_keys=ON
--   （connection.py:121）。实测：INSERT OR IGNORE 遇到缺失的 FK 目标会
--   **直接报 FOREIGN KEY constraint failed**（不是静默忽略）。
--   从 tag_definitions 里 SELECT 天然只选存在的行，不存在的 tag 不产生
--   任何行，也就不会触发 FK 违例。
--
-- ⚠️ 字面量约束（别改坏）: 本文件的所有字符串字面量里**不得含** ";" 或 "--"。
--   migrate.py 的 _run_sql_file 先试 executescript（对字面量里的 ";" 是正确的），
--   但一旦 executescript 因任何原因失败，兜底路径是 naive sql.split(";")，
--   会把字面量从中间劈开 → "unrecognized token" → 整个迁移失败，
--   schema_version 卡在 38，所有 `moni db *` 子命令都被堵死。
--   "--" 同理: migrate.py:52 的 re.sub(r"--+.*", "", raw) 会删掉行尾之后的内容。
--   现有 37 个 .sql（36 migration + schema.sql）的字面量里 ";" / "--" / "/*"
--   各 0 命中 —— 守住这个惯例。

BEGIN IMMEDIATE;

-- S-3: INSERT ... SELECT ... WHERE NOT EXISTS，而不是 INSERT OR IGNORE。
--   sources.name 是 UNIQUE（004_sources_and_tags.sql:15）。用户可能删掉
--   seed 建的行、用**自己的 id** 在 /config 重建了同名源（"新建 source"
--   表单允许用户填 id）→ name='xbot_feed' 已被占用。此时裸 INSERT 会
--   抛 UNIQUE 违例 → 整个迁移失败 → schema_version 卡在 38 → 所有
--   `moni db *` 子命令持续失败。INSERT OR IGNORE 更糟：UNIQUE 违例被
--   **静默吞掉**，源行根本没建成，迁移却"成功"了。
--   所以显式检查 id 和 name，把"用户已占用这个名字"当成**已满足**。
INSERT INTO sources
    (id, name, display_name, source_type, url, fetch_interval_min,
     is_active, notes, fetch_config, created_at, updated_at)
SELECT
    'src-xbot-feed',
    'xbot_feed',
    'X-bot 精选',
    'json',
    'https://github.com/mariusiaowego-commits/xbot-feed.git',
    60,
    1,
    'X-bot 精选 feed (私有仓库 xbot-feed 的 feed 目录下的 json 文件)。moni 只做导入，私有仓库访问权来自机器已有的 git 凭据。',
    '{"repo_url": "https://github.com/mariusiaowego-commits/xbot-feed.git", "clone_dir": "", "bypass_keyword_filters": true, "blacklisted_handles": ["jkghzone", "sensenxnyz", "Eason3110188520", "duizhanggege", "HunchoJ01"], "topic_tags": {"wechat": ["wechat", "miniprogram"], "xcircle": ["general-tech", "ai-fintech"]}, "max_days": 30, "git_timeout": 120, "skip_existing_urls": true}',
    datetime('now'),
    datetime('now')
WHERE NOT EXISTS (
    SELECT 1 FROM sources
    WHERE id = 'src-xbot-feed' OR name = 'xbot_feed'
);

-- S-2: JOIN sources —— 与 037（037_cn_indie_existing_tags.sql:25-26）同构。
--   只在源真的存在时才建绑定，**预防**悬空链接（而不是事后检测）。
--   目标 tag 不在库里就不建链接（tag_definitions 那侧没有行 → 不产生行）。
INSERT OR IGNORE INTO source_tags (source_id, tag_name, created_at)
SELECT s.id, t.name, datetime('now')
FROM sources s
JOIN tag_definitions t
  ON t.name IN ('wechat', 'miniprogram', 'general-tech', 'ai-fintech')
WHERE s.id = 'src-xbot-feed';

-- S-1: 守卫数**生效绑定**（is_active = 1），与 037 完全同构。
--   旧写法只检查"源行存在"，注释说"不会谎称成功"但实测它就是谎称：
--   未 seed 的库（tag_definitions=0）跑完 039，源行在、绑定 0 条，
--   cleanup_log 却记了 039。
--   而这 4 个绑定恰恰决定 tag 闸门是否 pass-through
--   （tag_filter.py:215-219）—— 正是 037 要防的失效模式。
--   apply_tag_keyword_filter() JOIN tag_definitions 时用 is_active = 1，
--   所以 4 个**停用**的 tag 同样会让闸门开着，日志不能记成功。
CREATE TABLE IF NOT EXISTS schema_cleanup_log (
    migration   TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_cleanup_log (migration, applied_at)
SELECT '039_xbot_feed_source', datetime('now')
WHERE (
    SELECT COUNT(*) FROM source_tags st
    JOIN tag_definitions t ON t.name = st.tag_name
    WHERE st.source_id = 'src-xbot-feed'
      AND t.is_active = 1
) >= 4;

COMMIT;
```

### 5.4.1 ⚠️ 字面量里不得含 `;`（`--` 也一样）—— runner 兜底路径是 naive `split(';')`

**这是 039 的 SQL 的一条硬约束，实施时必须遵守。**

`moni/db/migrate.py:44-75` 的 `_run_sql_file` 是**两段式**的：

```python
try:
    conn.executescript(sql)      # ① 先试整体执行
    return
except sqlite3.Error as exc:
    _rollback_if_needed(conn)
    ...
for stmt in sql.split(";"):      # ② 兜底：naive split
    conn.execute(stmt)
```

`executescript` 对字面量里的 `;` 是**正确的**（实测 `'x; y'` 正常入库）。
但**兜底路径的 `sql.split(";")` 会把字面量劈开**。

**实跑复现**（用代理对象强制 `executescript` 失败、逼 runner 走兜底路径）：

```
=== with ';' in literal ===
   FALLBACK PATH RAISED: OperationalError: unrecognized token:
       "'X-bot 精选 feed (私有仓库 xbot-feed 的 feed/*.json)。moni 只做导入"
   EXECUTESCRIPT PATH: OK
=== no ';' in literal ===
   FALLBACK PATH: OK    sources: 1  links: 4
   EXECUTESCRIPT PATH: OK
```

**为什么"正常情况没事"也必须防**：`executescript` 只要因为**任何原因**失败
（例如 `sources.name` 上已有的 UNIQUE 冲突、`BEGIN IMMEDIATE` 的锁冲突、
或任何一条语句报错），runner 就自动落到兜底路径 —— 此时 `;` 字面量**必然炸**，
而且炸在 `migrate()` 里 → `schema_version` 卡在 38 → **每次跑都失败**。
因为 `moni/cli/main.py:778-779` 是 `if args.subcmd == "db": migrate()`，
**所有 `moni db *` 子命令（`stats` / `init` / `seed-config` / `export`）都会被堵死**。

**逐字符实测**（同一套兜底路径）：

| notes 字面量里的内容 | 兜底路径 | `executescript` 路径 |
|---|---|---|
| 无 `;` / `/*` / `--` | ✅ OK | ✅ OK |
| 含 `/*`（如 `feed/*.json`） | ✅ OK | ✅ OK |
| **含 `;`** | ❌ **OperationalError: unrecognized token** | ✅ OK |
| **含 `--`** | ❌ **syntax error**（`migrate.py:52` 的 `re.sub(r"--+.*", "", raw)` 会把行尾之后全删掉） | ❌ syntax error |

→ **`;` 和 `--` 都不能进字面量。** `/*` 技术上安全，但仓库惯例也避开（见下）。

**仓库惯例**：扫了现有 **37 个** `.sql`（36 个 migration + `schema.sql`），
**所有字符串字面量里 `;` / `--` / `/*` 各 0 命中** —— 这个惯例值得守。

**具体做法**：

1. `notes` 文案改成**不含 `;` / `--`** 的版本（§5.4 的 SQL 草案已改）：

   ```
   旧：X-bot 精选 feed (私有仓库 xbot-feed 的 feed/*.json)。moni 只做导入; 私有仓库访问权来自机器已有的 git 凭据。
   新：X-bot 精选 feed (私有仓库 xbot-feed 的 feed 目录下的 json 文件)。moni 只做导入，私有仓库访问权来自机器已有的 git 凭据。
   ```

   （`feed/*.json` → `feed 目录下的 json 文件`：顺手把 `/*` 也避开了。）

2. **`moni/db/seed_config.py:311` 的同一个字符串要一起改** —— 用例 11
   （`test_039_matches_seed_config_values`）断言两处 notes 逐字相同，
   只改一处会让用例 11 变红。改的是文案，不是 seed 的逻辑。

3. **加一条测试（用例 18）**：扫 `039_xbot_feed_source.sql` 的所有字符串字面量，
   断言里面**不含** `;` 和 `--`（防止以后有人改文案时把 `;` 加回去）。
   再加一条（用例 19）强制走兜底路径，断言不抛异常。

### 5.5 几个必须想清楚的决定

#### (a) 用户主动删掉了这个源 —— 要不要恢复？

**决定：恢复（重新插入）。** 理由：

- "删掉源"在 moni 里的正常路径是**停用**（`is_active=0`），不是删行。
  `USER_GUIDE.md:186` 明确写："**停用源**：不删, is_active 设为 false 即可"。
  删行的入口是 `/config` 的删除按钮（`config_sources.py:306-314`），会 CASCADE
  掉 `source_tags`。
- **恢复 = 回到 moni 的出厂源目录**。moni 每次 `db init` 都会跑 `seed()`，
  也就意味着**每次 `db init` 都会把这个源加回来**（`seed_config.py:488-512`
  是 upsert）。如果 migration 选择"不恢复"，就会出现
  "seed 会加回来，migration 不加" 的**不一致** —— 用户重启一次 `db init` 源又出现了。
- **恢复的代价极低**：用户想停用，改 `is_active` 即可（迁移不会碰它）。

**但**：这确实是一个"用户明确删过的东西被迁移加回来"的情况，
**属于需要 Mttt 拍板的语义决定**（Q-O-2）。备选方案是
「只在 `sources` 表里没有任何 `src-xbot-feed` 痕迹时才插入」——
但这个条件跟"源不存在"完全等价，实际上就是"总是插入"，没有意义。
**另一个备选**是"删过就不再恢复"（用一个 tombstone 表记录）—— 过度设计，
moni 现有机制里没有 tombstone 概念。

#### (b) 新库（fresh DB）的执行顺序

- `migrate()`（`moni/db/migrate.py:78-121`）先跑 `schema.sql`，再按版本号顺序跑
  `migrations/*.sql`，最后记 `schema_version`。
- **migration 在 seed 之前跑**（`moni/cli/main.py:45-50`：`cmd_init()` 先 `migrate()`
  再 `seed_config_seed()`）。
- 所以 fresh DB 上的顺序是：039 插入源（此时 `tag_definitions` **是空的**，
  因为 `tag_definitions` 的行来自 `seed()`，不是 migration）→ 4 个 tag 绑定
  **一条都建不上** → 接着 `seed()` 插入 10 个 tag + 14 个源 + 34 个绑定。
- **结果是对的**（`seed()` 的 `source_tags` 插入是 `ON CONFLICT DO NOTHING`，
  seed 里那 4 条绑定会补上迁移没建成的链接）。
- **但有个副作用**：fresh DB 上跑完 039 时，`tag_definitions` 是空的
  （那些行来自 `seed()`，不是 migration），所以 **4 个 tag 绑定一条都建不上**。
  → **S-1 修法生效**：守卫数的是 `is_active = 1` 的**生效绑定**，
  fresh DB 上这个数是 0（< 4）→ **039 不记 `schema_cleanup_log`**。
  这是**正确**的：日志记录的是"这个迁移真正完成了它要做的事"，
  而不是"源行落库了"。随后的 `seed()` 会把 4 个绑定补上
  （`ON CONFLICT DO NOTHING`），但那时 039 已经跑完、日志已经确定。
  → 代价是：**fresh DB 的 `schema_cleanup_log` 里不会有 039 这一行**
  （迁移在 seed 之前跑）。这是 037 同样的取舍（037 也是数生效绑定），
  接受它。
- **另一个后果**：fresh DB 上 `migrate()` → `seed()` 之后，039 的
  `INSERT OR IGNORE` 对源行是 no-op（seed 已经插了），对 4 个绑定也是 no-op。
  **零冲突，幂等。**

#### (c) `is_active` 怎么处理

- **只在 INSERT 时给 1**。已有行**一个字段都不碰** —— 用户停用了就还是停用。
- 这是 §5.2 里"INSERT OR IGNORE 而不是 ON CONFLICT DO UPDATE"的直接后果。
- **不要**写成 `ON CONFLICT(id) DO UPDATE SET is_active=1`（那会重新启用用户停用的源），
  也**不要**写成 `DO UPDATE SET is_active=sources.is_active`（那看着安全但没意义）。

#### (d) `tag_definitions` 缺失时怎么办

- **不建链接**（§5.4 的 `SELECT ... FROM tag_definitions` 天然满足）。
- **不新建 tag**。4 个 tag（`wechat` / `miniprogram` / `general-tech` / `ai-fintech`）
  都是 moni 的**基础业务 tag**，在 `seed_config.py:78-100` 的 `_DEFAULT_TAGS` 里
  （`wechat` L84、`miniprogram` L90、`general-tech` L83、`ai-fintech` L81）。
  **新建一个关键词为空的 tag 会让 tag 闸门变成透传**（migration 037 的教训，
  `moni/db/migrations/037_cn_indie_existing_tags.sql:1-21`）。
- **一个 DB 上 4 个 tag 全缺的情况**：现实中不可能（`db init` 会 seed），
  但如果真发生了，源会落库、绑定建不上 → 闸门会因为"没有绑定 tag"
  而变成透传（`tag_filter.py:215-219`：关键词集为空 → pass-through）。
  **这个失效模式跟 037 修掉的那个一模一样**，所以：
  - **S-1 修法**：039 的 `schema_cleanup_log` 守卫数 `is_active = 1` 的
    生效绑定，**不足 4 条就不记成功** —— 不谎报（对齐 037 与 AGENTS.md §1.4）；
  - **S-2 修法**：`source_tags` 的插入 JOIN `sources` → 源行不存在就不建绑定，
    **预防**悬空链接（037 的写法）；
  - **加一条测试**：4 个 tag 缺失时，039 不抛异常、不建悬空链接（§6.1 的
    `test_039_skips_links_when_tags_missing`），
    外加一条断言"守卫不记成功"（`test_039_guard_requires_effective_bindings`）。

#### (e) schema 版本号

- `moni/db/migrate.py:21` 的 `_CURRENT_VERSION` 从 **38 → 39**。
- 6 处写死 38 的断言（5 个文件）+ 1 处报错文案要同步（§2.2）。
- `migrate()` 在 `applied >= _CURRENT_VERSION` 时早退（`migrate.py:88-90`），
  所以**回滚代码不会降级 schema** —— 039 是纯 INSERT，残留的源行无害
  （跟 v1.0.65 的回滚结论一致，`STATUS.md:104-105`）。

### 5.6 幂等与不覆盖的实测（PLAN 阶段已在真实 schema 上跑过）

用真实 `migrate()` 建库 + 上面这份 SQL 草案，`_run_sql_file` 实跑：

| 场景 | 结果 |
|---|---|
| A. v38 库（无本源，有 4 个 tag） | 源插入成功（`is_active=1`、interval 60），4 条绑定建成 |
| B. 再跑一遍 | 源 1 行、绑定 4 行 —— **完全幂等** |
| C. 用户改过源（`is_active=0` + 自定义 `display_name` + 自定义 `fetch_config`）+ 删掉一条绑定，再跑迁移 | `is_active` 仍 0、`display_name` 仍自定义、`fetch_config` 仍自定义；**被删的绑定被补回** |
| D. 用户把源行删了，再跑迁移 | 源**被重新创建**（§5.5(a) 的决定 + Mttt Q-O-2 拍板） |
| E. 4 个 tag 全从库里删掉，再跑迁移 | 源还在，绑定 0 条，**不抛异常**，**守卫不记成功**（S-1） |
| **F. 用户用自己的 id 重建了同名源**（`name='xbot_feed'` 已被占用） | 迁移**不抛异常**、不插入、不建悬空绑定、守卫不记成功（S-3） |
| **G. 强制走 runner 兜底路径**（`split(';')`） | 迁移**不抛异常**，源在、4 条绑定在（B-2） |
| **H. migrate-only（`python3 -m moni.db.migrate`）** | 用户改过的字段**逐字段不变**，别的源的编辑也不变（B-1） |

> **C 场景的"绑定被补回"是刻意的**，跟 `seed()` 的语义一致
> （`USER_GUIDE.md:1348-1351` 已经写明"seed-config 会把默认绑定列表里缺的条目补回来"）。
> 迁移跟 seed 对齐，用户只需要知道一条规则：**绑定只增不删**。
> 如果 Mttt 认为"取消的勾选不该被补回"，那 seed 也得改，**那是另一个决定**（Q-O-3）。

---

## 6. 测试计划（scope 4）

### 6.1 新文件 `tests/test_migration_039_xbot_feed.py`

照 `tests/test_migration_038_cv_acronyms.py` 的结构（fixture + `_run_sql_file` + 断言）。

| # | 用例 | 断言 | 对应场景 |
|---|---|---|---|
| 1 | `test_039_version_constant_is_39` | `_CURRENT_VERSION == 39` | 版本号 |
| 2 | `test_039_creates_source_on_v38_db` | v38 库（删掉本源）跑迁移后：`sources` 有 `src-xbot-feed`，字段值与 `seed_config` 一致（name/display_name/source_type/url/fetch_interval_min=60/is_active=1） | §5.6-A |
| 3 | `test_039_creates_the_four_tag_links` | `source_tags` 里有 `wechat`/`miniprogram`/`general-tech`/`ai-fintech` 4 条 | §5.6-A |
| 4 | `test_039_is_idempotent` | 连跑两遍：源 1 行、绑定 4 行、`created_at` 不变 | §5.6-B |
| 5 | `test_039_does_not_touch_edited_source` | 用户改过 `is_active=0` / `display_name` / `url` / `fetch_interval_min` / `notes` / `fetch_config` → 跑迁移后**逐字段相等** | §5.6-C |
| 6 | `test_039_does_not_reenable_disabled_source` | `is_active=0` 的源跑完迁移仍是 0（**显式单列，别只靠 #5 顺带覆盖**） | §5.6-C |
| 7 | `test_039_never_updates_existing_row` | 迁移前后 `sources` 行**逐字节相同**（除 `updated_at`）；用 `SELECT *` 对比 dict | 防"不小心加了 UPDATE" |
| 8 | `test_039_does_not_touch_tag_keywords` | `tag_definitions.keywords` 跑迁移前后**完全相同**（含乱序、含 NULL） | §5.1 非目标 |
| 9 | `test_039_skips_links_when_tags_missing` | 4 个 tag 全不在库里 → 源在、绑定 0 条、**不抛异常** | §5.6-E |
| 10 | `test_039_no_dangling_links` | 迁移后 `source_tags` 里 `src-xbot-feed` 的每一行，其 `tag_name` 都能在 `tag_definitions` 里找到 | FK 完整性 |
| 11 | `test_039_matches_seed_config_values` | 迁移插入的源行字段 == `seed_config._DEFAULT_SOURCES` 里 `xbot_feed` 那条（逐字段） | 防止两处漂移 |
| 12 | `test_039_fetch_config_matches_default` | 迁移写入的 `fetch_config` == `json.dumps(DEFAULT_FETCH_CONFIG, ensure_ascii=False)` | **§5.3 的"第四份拷贝"护栏** |
| 13 | `test_039_source_tags_match_seed_config` | 迁移建的 4 条绑定 == `seed_config._DEFAULT_SOURCE_TAGS` 里 `src-xbot-feed` 的那 4 条 | 同上 |
| 14 | `test_039_logs_the_cleanup` | `schema_cleanup_log` 里有 `039_xbot_feed_source` | 可观测性 |
| 15 | `test_039_fresh_db_via_migrate` | fresh DB → `migrate()` → `schema_version == 39`，且 `migrate()` 不抛异常 | 全量路径 |
| 16 | `test_039_on_real_v38_db_without_seed` | 造一个"只有 schema、没跑过 seed"的 v38 库 → 跑 `migrate()` → 源存在（**这是升级的真实场景**） | 核心场景 |
| 17 | `test_039_restores_bypass_semantics` | 迁移后 `_is_source_bypassed("xbot_feed") is True`（源行缺失时它静默变 False → 全局黑名单误杀，见 §1.0） | §1.0 |
| 18 | `test_039_sql_literals_have_no_semicolon` | 扫 `039_xbot_feed_source.sql` 的**所有字符串字面量**，断言不含 `;` 也不含 `--` | **B-2 护栏**（§5.4.1） |
| 19 | `test_039_survives_runner_fallback` | 强制 `_run_sql_file` 走**兜底路径**（用代理对象让 `executescript` 抛错）→ 迁移**不抛异常**，源在、4 条绑定在 | **B-2 护栏**（§5.4.1） |
| 20 | `test_039_migrate_only_path_preserves_user_edits` | 走 `python -m moni.db.migrate` 等价的 migrate-only 路径 → 用户改过的 `display_name`/`fetch_interval_min`/`notes`/`fetch_config` **逐字段不变** | **B-1 护栏**（§4.3.1） |
| 21 | `test_039_db_init_does_clobber_user_edits` | 走**真实 CLI 路径** `cmd_init()` → 断言用户改过的字段**被重置**（把"db init = migrate + seed"这个事实钉死，防止有人误以为它安全） | **B-1 护栏**（§4.3.1） |
| 22 | `test_039_upgrades_a_v38_db_without_seed` / `..._v38_db_that_has_the_business_tags` / `..._upgrade_path_needs_no_git` | **手写**一个最小 v38 库（`schema_version=38`、`sources` 无 `src-xbot-feed`、tag 可有可无）→ 用当前代码跑 `python3 -m moni.db.migrate` → schema 39、源在、`is_active=1`、不抛异常；**再跑一次是 no-op**；再加一条"PATH 里没有 git、cwd 不是 git 仓库"也必须升级成功 | **真实升级路径**，**不依赖 git 历史**（§6.4.1） |
| 22b | `test_039_upgrades_a_db_built_by_old_code` | 用 `git worktree` 拉 `015a2c5`（xbot_feed 落地**之前**的代码）建一个库 → 当前代码升级 → schema 39、源在；**再跑一次是 no-op** | **真实升级路径**（§6.4.1），需要该 commit 在本地克隆里（CI 已改 `fetch-depth: 0`） |
| 23 | `test_039_tolerates_user_owned_same_name_source` | 用户已用 `name='xbot_feed'` 占用（自己的 id）→ 迁移**不抛异常**、不产生悬空绑定、守卫不谎报成功 | **S-3 护栏** |
| 24 | `test_039_guard_requires_effective_bindings` | 4 个 tag 全停用 → `schema_cleanup_log` **不**记录 039（对齐 037 的 `is_active=1` 生效绑定计数） | **S-1 护栏** |
| 25 | `test_039_migrate_only_does_not_run_seed` | 走 `python3 -m moni.db.migrate` → **别的源**的编辑字段**逐字段不变**（证明没有 seed） | **B-1 护栏** |
| 26 | `test_039_does_not_touch_other_sources` | 迁移前后**除本源以外**的所有 `sources` 行逐字段相同 | 隔离性护栏 |

> **用例 22 是"真实升级"的端到端护栏**，分两层：
>
> - **22（主路径，不依赖 git）**：**手写**一个最小 v38 库（`schema_version=38`、
>   `sources` 里没有 `src-xbot-feed`、`tag_definitions` 可有可无）→ 用当前代码
>   跑 `python3 -m moni.db.migrate` → schema 39、源在、`is_active=1` → 再跑一次
>   是 no-op。这层**永远不 skip**：浅克隆、导出副本、没有 git 的环境都能跑。
> - **22b（加强层，需要 git 历史）**：用 `015a2c5` 的**真代码**建一个真库
>   （那时 `seed_config.py` 里根本没有 xbot_feed 这个源），再用当前代码迁移。
>   这是最接近"真实存量库"的形态，但需要该 commit 在本地克隆里。
>
> **⚠️ IMPL-AUDIT S-5（2026-11-06 修正）**：最初的实现只有 22b，而且
> `git worktree add` 失败时**静默 `pytest.skip`** —— CI 的 checkout 用默认
> `fetch-depth: 1`（浅克隆），`015a2c5` 不在克隆里，于是这条护栏**在 CI 上
> 永远是 skip，覆盖率为 0**。现在改成"22 为主路径（永不 skip）+ 22b 为加强层"，
> 并给 CI 的 checkout 加 `fetch-depth: 0` 让 22b 也在 CI 上真跑。

### 6.1.1 关于用例 22 / 22b 的实现说明

**22（主路径，手写 v38 库）**

- 建库：`_build_v38_db()` 手写最小 v38 schema —— `schema_version` 钉在 **38**、
  `sources`（**没有** `src-xbot-feed`，另放一个别的源用来验"其它源不动"）、
  `source_tags`、`tag_definitions`（4 个业务 tag，可选）、`schema_cleanup_log`。
  列定义照 v38 的真实形状（012 加了 `sources.fetch_config`，006 加了
  `tag_definitions.keywords`，007 加了 `tag_definitions.display_name`），
  用 `015a2c5` 的真代码建出来的库核对过。
- 升级：用当前代码跑 `python3 -m moni.db.migrate` → schema 39、源在。
- 幂等：再跑一次 → stdout 含 `nothing to migrate`，源仍然只有 1 行。
- 另有一条 `test_039_upgrade_path_needs_no_git`：**PATH 里没有 git**、
  **cwd 不是 git 仓库**，升级仍然必须成功。

**22b（加强层，旧代码建库）**

- 建库：`git worktree add --detach <tmp> 015a2c5` → 用 `<tmp>` 的代码跑
  `python3 -m moni.db.migrate`（`PYTHONPATH=<tmp>`，确保 import 的是旧代码）。
  实测旧代码的 `_CURRENT_VERSION` 是 **38**，且 `sources` 里**没有** `src-xbot-feed`。
- 升级 / 幂等：同上。
- 收尾：`git worktree remove --force <tmp>`（**必须**收，否则仓库里留垃圾）。
- 该 commit 不在克隆里时**显式 skip 并写明原因**（不静默）；worktree 建不起来
  时是 **`pytest.fail`**，不是 skip。

> **用例 16 是这个迁移存在的理由**（存量库不跑 seed-config 的真实路径），必须写。
> **用例 17 钉住 §1.0 那个静默失效**（源行缺失 → bypass 变 False → 黑名单误杀）。
> **用例 18/19 是 B-2 的护栏** —— 只测 `executescript` 正常路径是不够的，
> `;` 字面量只在兜底路径炸（§5.4.1）。
> **用例 20/21 是 B-1 的护栏** —— 一条钉住"migrate-only 不碰用户数据"，
> 一条钉住"`db init` 确实会重置用户数据"（后者是 §7.1 文档的根据）。

### 6.2 现有测试的影响

- **不需要改任何现有测试的断言语义**，只改 6 处版本号断言 + 1 处报错文案（§2.2）。
- `tests/test_xbot_feed_fetch.py:768` 的 `test_seed_defines_source_and_tags`
  **继续有效**（seed 的逻辑没改，只改了 notes 文案 —— 该用例不断言 notes）。
- `tests/test_db.py:98` 用 `_CURRENT_VERSION` 常量，自动跟随。
- ⚠️ **`seed_config.py` 的 notes 文案一改，任何断言 notes 原文的地方都要一起改**
  （实施时 `grep -rn "只做导入" tests/ moni/` 自查）。

### 6.3 跑法

```bash
# 依赖（仓库是 uv 项目）
uv venv --python 3.12 .venv && uv sync --extra dev

# 定向
.venv/bin/python -m pytest tests/test_migration_039_xbot_feed.py -q -p no:cacheprovider

# 相关回归（版本号改动的 5 个文件 + xbot_feed + seed）
.venv/bin/python -m pytest \
  tests/test_migration_039_xbot_feed.py \
  tests/test_migration_030_ghost_cleanup.py \
  tests/test_migration_036_36kr_source_id.py \
  tests/test_migration_037_cn_indie_tags.py \
  tests/test_migration_038_cv_acronyms.py \
  tests/test_llm_usage_counters.py \
  tests/test_db.py \
  tests/test_xbot_feed_fetch.py \
  tests/test_seed_config_fetch_config.py \
  -q -p no:cacheprovider

# 全量（CI 同款命令）
.venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

**基线数字**（PLAN 阶段在 `e53956c` 上实测，Python 3.12.15）：

```
1456 passed, 2 skipped, 3 warnings in 110.40s
```

→ 实施后应为 **1456 + 26 = 1482 passed / 2 skipped**（§6.1 的 26 条用例）。
**若实际条数不同，以实际为准写进 STATUS** —— 不许写"预计 1482"当结论。

> §6.1 的用例数从 17 增加到 26 —— 新增的 18/19（B-2 护栏）、20/21（B-1 护栏）
> 是 B-1/B-2 patch 补上的；22/23/24/25/26 是这次 S-1..S-6 patch 补上的
> （S-1 的守卫、S-3 的同名源冲突、真实升级路径、migrate-only 不 seed、隔离性）。
> 改动基线数字时别忘了同步。

**实施实测**（2026-11-05，本分支 `feat/sprint-30-xbot-feed-migration`）：

```
1489 passed, 2 skipped, 3 warnings in 111.89s (0:01:51)
```

> 上面的 "1456 + 26 = 1482" 是按**表格行数**算的；**落地文件里是 33 个
> test 函数**（`grep -c "^def test_" tests/test_migration_039_xbot_feed.py` → 33）。
> 差额来自"一个场景需要两种不同 fixture"的情况 —— 例如"在 unseeded 库上建源"
> 和"在 seeded 库上建源"是两条独立的 test，"删掉源后恢复"在 seeded / unseeded
> 两种库上也是两条；另外 **IMPL-AUDIT S-5** 把"旧代码建的库能升级"拆成了
> 3 条（手写 v38 库 ×2 + 无 git 依赖 ×1），旧的 git 版本保留为加强层。
> **1456 + 33 = 1489**，与实测一致。
> 完整日志见 `docs/sprint-evidence/sprint-30/pytest-full.txt`。

**rebase 后重测**（2026-11-06，本分支 rebase 到 `origin/main` @ `2ce8965`
—— PR #173 心跳/批量队列 + PR #174 文档收尾都已在 main 上）：

```
1508 passed, 2 skipped, 3 warnings in 108.58s (0:01:48)
```

> **基线跟着变**：rebase 前的基线是 `e53956c` 的 **1456**；rebase 后的基线是
> `origin/main` @ `2ce8965` 的 **1475**（#173 新增了 19 条测试）。
> **1475 + 33 = 1508**，与实测一致 —— 本 sprint 新增的仍是 **33 条**，
> 一条不多一条不少。基线数字变了，但"本 sprint 加了几条"这个事实没变。

### 6.4 数据隔离要求

`tests/conftest.py` 的 session 级 autouse fixture `_isolate_data_dir`（`:70-93`）
把 `MONI_DATA_DIR` 指到临时目录，**并在会话结束时断言仓库 `data/` 一个字节没变**
（`conftest.py:98-99` 调 `assert_data_dir_unchanged(_snapshot_data_dir(real), ...)`；
`conftest.py:31` / `:50` 是这两个 helper 的定义）。
**039 的测试必须用 `tmp_path` + `set_db_path()`**
（照 `test_migration_038_cv_acronyms.py:36-49` 的 `db_with_ai_fintech` fixture），
**绝对不能打真库**。

---

## 7. 文档与发布说明（scope 3）

### 7.1 README.md

**改 `### 关键命令` 段**（`README.md:42-58`）：

```markdown
# DB
/usr/local/bin/python3 -m moni db init              # 初始化 + migrate + seed ⚠️ 会重置你改过的源字段
/usr/local/bin/python3 -m moni.db.migrate           # 只跑迁移（升级用这个，不碰你的配置）
/usr/local/bin/python3 -m moni db stats             # 表格行数
/usr/local/bin/python3 -m moni db seed-config       # 重置 source 配置 (需先 source .env) ⚠️ 会重置你改过的源字段
```

**在 `README.md` 的「关键命令」后面加一小节**：

```markdown
## 升级到 v1.0.68 及以后

新抓取源由 **migration 落库**，不再需要跑 `moni db seed-config`。升级只要跑迁移：

```bash
/usr/local/bin/python3 -m moni.db.migrate   # 只跑迁移，不碰你在 /config 改过的任何东西
```

> ⚠️ **升级时不要跑 `moni db init`。**
> `moni db init` = **migrate + seed**（`moni/cli/main.py:45-50`），它除了迁移还会跑
> `seed-config`，而 **seed 会把你改过的源字段重置成出厂默认**
> （display_name / url / 抓取间隔 / notes / fetch_config），
> 并把你取消掉的 tag 绑定补回来。迁移只 INSERT，不覆盖任何东西。
> tag 的关键词不受影响（seed 也不动 keywords 列）。
```

> ⚠️ **为什么是 `python3 -m moni.db.migrate` 而不是 `moni db init`**：
> `migrate.py:124-133` 有现成的 migrate-only 入口（实跑确认：**全量重跑**基表
> `schema.sql`（幂等）+ 跑 pending migrations，**不 seed 任何
> sources / tag_definitions**）；`db init` 会额外跑
> `seed_llm` + `seed_config`，后者正好会抹掉用户在 `/config` 改过的东西（§4.2 实测）。
> 改 `db init` 本身的行为属于本 sprint 范围外，见 Q-O-10。

### 7.2 USER_GUIDE.md

**改 `### 3.1 抓取源 tab`**（`USER_GUIDE.md:152-188`）：源表已经列了 `xbot_feed`
（L169），只在表格下方补一句：

```markdown
> **v1.0.68 起**：新源由数据库迁移自动落库，**升级不需要跑 `moni db seed-config`**。
> 升级命令是 `python3 -m moni.db.migrate`（只跑迁移）。
> ⚠️ **不要用 `moni db init` 升级** —— 它 = migrate + seed，会把你改过的源字段重置。
```

**改 `USER_GUIDE.md:1160`**（`⚠️ moni db seed-config 会重置 fetch_config 为 seed 默认。`）——
把范围写清楚（现在只提 fetch_config，实际 display_name / url / 间隔 / notes 也会被重置，
**而且业务 tag 自己的 display_name / description / color / is_active 也会被重置** —— S-4）：

```markdown
⚠️ `moni db seed-config` 会把**你在 /config 改过的源字段重置成 seed 默认**：
display_name / url / 抓取间隔 / notes / fetch_config，并把你取消掉的 tag 绑定补回来。
**业务 tag 自己的字段也会被重置**：显示名 / 描述 / 颜色，以及 **停用状态**
（你停用过的 tag 会被重新启用 = 抓取闸门重新打开）。
不受影响的只有两样：源被**停用**的状态（is_active），以及 tag 的**关键词**（keywords）。
升级到新版本时**不需要**跑它 —— 新源由 migration 自动落库，升级用
`python3 -m moni.db.migrate`（⚠️ `moni db init` = migrate + seed，会重置这些字段，不要用来升级）。
```

### 7.3 STATUS.md

**新增 v1.0.68 状态节**（放在文件顶部 `## 当前状态` 下面，把 v1.0.66 变成历史状态）：

```markdown
## 当前状态: **v1.0.68 — xbot_feed 回填与迁移 039** (2026-11-05 · **IN REVIEW**)

**分支**: 待开 · **schema**: 39（migration 039）· **测试**: 1456 → 见下

### v1.0.68 交付要点

| 项 | 决定 | 理由 |
|---|---|---|
| 新源落库 | **migration 039 幂等插入** `src-xbot-feed` + 4 个 tag 绑定 | 升级不再依赖 seed-config |
| 覆盖策略 | 只 `INSERT OR IGNORE`，**不 UPDATE 任何已有行** | 用户改过的字段（含停用状态）一个字都不动 |
| 关键词 | 不碰 `tag_definitions.keywords` | 用户的关键词是用户的数据 |
| **升级命令** | **`python3 -m moni.db.migrate`**（migrate-only） | `moni db init` = migrate + seed，会重置用户改过的字段（§4.3.1） |
| 文档 | PR #172 审计回填 + 真实运行证据进 `docs/` | 审计报告原本只在工作区 |

### 已知遗留
- `fetch_config` 的默认值现在有**四处**副本（fetcher / seed / web / migration SQL），
  测试 `test_039_fetch_config_matches_default` 是唯一的护栏
- N-7: `moni/fetch/sources/follow_builders.py:152` 的 `url LIKE '%status/{id}%'` 前缀误命中（同 B2），不在本 sprint
```

**改 v1.0.66 那节**：`IN REVIEW` → `SHIPPED`，补上 PR / commit / tag：

```markdown
## 历史状态: **v1.0.66 — xbot_feed 源（X-bot 精选桥接）** (2026-11-03 · **SHIPPED**)

**主分支**: main @ `e53956c` · **PR**: #172（squash merge）· **schema**: 38（无新迁移）
**测试**: 1456 passed / 2 skipped · **审计**: 9 blocking + 6 should-fix 全修完，nit 不阻塞
```

### 7.4 `docs/sprint-evidence/README.md`

Contents 表加**三行**（sprint-30 一行 + **S-6 修法：顺手补上漏掉的 sprint-27 两行**）：

```
| `sprint-27/` | sprint-27 evidence（中文独立博客源独立审计） | v1.0.60/v1.0.62 中文独立博客源：审计报告 |
| `sprint-27-hotfix/` | sprint-27-hotfix evidence（失败率熔断 + 配置链修复） | v1.0.62 hotfix：审计 + 实施记录 |
| `sprint-30/` | sprint-30 evidence（xbot_feed 回填与迁移 039） | v1.0.66 xbot_feed 真实运行：203 导出 / 71 入库 / rerun 0 + 真实库副本迁移实测 |
```

> **S-6 的依据**：实测 `docs/sprint-evidence/README.md` 的 Contents 表**只列了 3 行**
> （`sprint-24/` / `sprint-25/` / `reference-reports/`），
> `grep -c "sprint-27" docs/sprint-evidence/README.md` → **0**。
> 但 `docs/sprint-evidence/sprint-27/` 和 `sprint-27-hotfix/` 两个目录**确实存在**。
> 既然要动这张表，就顺手把它们补上 —— 否则索引继续烂下去。
> 同时在 evidence README 里注明 `RUN.md` 是 sprint-30 新起的命名约定。

---

## 8. 风险

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | **migration SQL 是 `DEFAULT_FETCH_CONFIG` 的第四份拷贝** | 改了 Python 常量忘了改 SQL → DB 默认值漂移，用户在 `/config` 看到的默认值跟实际生效的不一致（正是审计 B5 修的那个坑） | 用例 12 `test_039_fetch_config_matches_default` 钉死；`test_fetch_config_has_single_source`（`test_xbot_feed_fetch.py:801`）继续覆盖前三处 |
| R2 | **用户主动删掉的源被迁移加回来** | 用户觉得"我明明删了它怎么又出现了" | §5.5(a) 的决定 + **Q-O-2 拍板**；停用是推荐做法（`USER_GUIDE.md:186`） |
| R3 | **取消的 tag 绑定被补回** | 用户在 `/config` 取消的勾选回来了 | 与 `seed()` 语义一致（`USER_GUIDE.md:1348-1351` 已写明）；**Q-O-3** |
| R4 | **版本号写死漏改** | 6 处 `== 38` 漏一处 → 全量测试红 | §2.2 列全了（5 个文件、6 处断言 + 1 处报错文案）；`grep -rn "== 38" tests/` 必须只剩 039 测试里那处刻意的 `old_version == 38`；**AGENTS.md 铁律 2** |
| R5 | **fresh DB 上 039 先于 seed 跑，4 个绑定一条都建不上** | 看起来"迁移没生效" | 实测确认：随后 `seed()` 会补上（`ON CONFLICT DO NOTHING`）；用例 15 覆盖 `migrate()` 路径；039 注释写明 |
| R6 | **`source_tags` 的 FK 违例会炸整个迁移** | `INSERT OR IGNORE` **不吞**外键违例（实测） | 用 `INSERT ... SELECT FROM tag_definitions`，目标不存在就不产生行；用例 9/10 覆盖 |
| R7 | **生产库跑迁移时代码还没合**（或反过来） | 前置 039 的老代码看到多出来的源行 → 无害（只是多一行）；老代码 `migrate()` 早退（`applied >= _CURRENT_VERSION`） | v1.0.65 已验证过这个顺序安全（`STATUS.md:104-105`）；039 是纯 INSERT，残留无害 |
| R8 | **Mac 上真实数字不是 71** | 有人拿 71 当基线 | evidence 文件里写明"测试关键词下的数字"；`USER_GUIDE.md` 不写具体条数 |
| R9 | **审计报告初版说过"已修"但当时一行没改** | 回填进仓库的 AUDIT.md 如果照抄那句话，就是把不实信息固化 | §3.3 明确要求只写**实际 commit hash**；每条都标 commit |
| R10 | `docs/plans/2026-11-03_xbot-feed-source.md` 带 Obsidian frontmatter | 改名会断 vault 链接 | §3.2 决定：不改名，只加指针 |
| R11 | **源行缺失时 `bypass_keyword_filters` 静默失效** | 存量库升级后（没跑 seed-config）全局黑名单会误杀精选内容，**抓取不报错**，只是少文章 —— 排查成本高 | 039 把源行补上；§1.0 给了实测症状；用例 16/17 覆盖这条路径 |
| **R12** | **升级指令写成 `moni db init` → 隐式跑 seed → 用户改动被重置** | sprint 的核心卖点（"升级不再依赖 seed-config"）**当场反转** —— 用户按文档升级，改动照样被抹 | §4.3.1 定死 migrate-only（`python3 -m moni.db.migrate`，实跑确认不 seed）；§7.1 / §7.2 / §11 三处统一；用例 20/21 钉住 |
| **R13** | **039 的 SQL 字面量含 `;` → runner 兜底路径 `split(';')` 劈开字面量 → 迁移炸** | `schema_version` 卡在 38，**所有 `moni db *` 子命令持续失败**（`main.py:778-779` 每个 db 子命令都先 `migrate()`），升级路径完全堵死 | §5.4.1 定死"字面量不含 `;` / `--`"；039 的 notes 文案已改（`seed_config.py:311` 同步改）；用例 18（扫字面量）+ 用例 19（强制兜底路径） |
| **R14** | **守卫只检查"源行存在" → 谎报成功**（S-1） | 未 seed 的库跑完 039：源在、绑定 0 条，日志却记了 039。读者以为 tag 闸门已生效，实际是透传 | 守卫改成数 `is_active=1` 的生效绑定（对齐 037）；用例 24 钉死 |
| **R15** | **`source_tags` 不 JOIN `sources` → 悬空绑定**（S-2） | 源行没建成但绑定建成了 → 绑定指向不存在的源 | 改成 `FROM sources s JOIN tag_definitions t`（037 同构）；用例 10 + 23 覆盖 |
| **R16** | **`sources.name` UNIQUE 冲突 → 整个迁移失败**（S-3） | 用户用自己的 id 重建了同名源 → 迁移中止 → `schema_version` 卡在 38 → 整个 `moni db *` 命令面堵死 | `INSERT ... SELECT ... WHERE NOT EXISTS (id = ... OR name = ...)`；用例 23 钉死 |
| **R17** | **文档只说"seed 重置源字段"，漏了 tag 的 `is_active`**（S-4） | 用户停用了某个 tag，跑一次 seed-config 就被**重新启用** = 抓取闸门重新打开，而文档没说 | §4.2 表 + §7.2 文案补齐 `tag_definitions` 的 4 个字段 |

---

## 9. 待 Mttt 拍板（Q-O）

| # | 问题 | 我的建议 | 影响 |
|---|---|---|---|
| **Q-O-1** | v1.0.68 的版本号 / tag 节奏？本 sprint 只做文档 + migration，是否单独发版？ | 单独发 **v1.0.68**（有 schema 变更，tag 必须跟 schema 对齐） | 决定 STATUS / README 的版本文案 |
| **Q-O-2** | **用户主动删掉 `xbot_feed` 源后，migration 要不要恢复它？** | **恢复**（§5.5(a)）。`db init` 每次都跑 `seed()`，seed 会加回来；migration 不加就出现"重启一次又出现"的不一致。停用请用 `is_active=0` | 决定 039 的 INSERT 条件 |
| **Q-O-3** | **用户在 `/config` 取消的 tag 绑定，migration 要不要补回？** | **补回**（与 `seed()` 一致，"绑定只增不删"）。要改成"只增不删也不补"，得连 `seed()` 一起改 —— 那是另一个决定 | 决定 039 的 `source_tags` 插入语义 |
| **Q-O-4** | 4 个 tag 绑定缺失时（理论上不可能），要不要 migration 自己补建 tag？ | **不补**。新建关键词为空的 tag 会让闸门透传（037 的教训）；缺失时 `seed()` 会补 | 决定 039 是否要 INSERT tag_definitions |
| **Q-O-5** | `2026-11-03_xbot-feed-source.md` 的 frontmatter `status` 改成什么？ | `已实现（PR #172 已合并 e53956c，v1.0.66）` | 一行文案 |
| **Q-O-6** | N-7（`moni/fetch/sources/follow_builders.py:152` 的 `f"%status/{tweet_id}%"` 前缀误命中，同 B2）**要不要现在修**？ | **不要**塞进本 sprint（它牵动另一个源，是独立 bug）。建议开一个独立 PR | 决定要不要加一个 sprint |
| **Q-O-7** | 生产库（Mac）上要不要在合并前先跑一次 039 的 dry-run（复制库上跑）？ | **要**。照 sprint-27 hotfix 的做法（`docs/sprint-evidence/sprint-27-hotfix/IMPLEMENTATION.md:34-56` 的"生产库副本重放"），在 Mac 的生产库副本上跑一遍，记录 before/after | 上线安全性 |
| **Q-O-8** | 真实运行证据要不要也把 11-04 / 11-05 两天的 feed 数字补进去？ | **补**。上游 `/workspace/xbot-feed/feed/` 已有 11-04 / 11-05 两个新文件，多两天能证明"闸门 + 状态文件"在多天数据上稳定 | evidence 的覆盖面 |
| **Q-O-9** | **用户用自己的 id 重建了同名源（`sources.name` UNIQUE 冲突）怎么办？** | **当成"已满足"，不报错**（S-3 修法）。039 用 `INSERT ... SELECT ... WHERE NOT EXISTS (id = ... OR name = ...)`：用户已占用 `xbot_feed` 这个名字 → 039 不插入、不报错、不建悬空绑定、守卫不记成功 | 决定 039 的 sources 插入语句形态 |
| **Q-O-10** | **`moni db init` 要不要改成不跑 seed？**（这是行为变更，影响所有现有用户） | **本 sprint 不改**。本 sprint 只把**升级指令**改成 migrate-only（`python3 -m moni.db.migrate`），并在 README/USER_GUIDE 写明 `db init` = migrate + seed、升级别用它。改 `db init` 的语义属于独立的行为变更，要单独一个 sprint + 独立审计 | 决定要不要另开 sprint 改 `db init` |

---

## 10. 实施顺序（给 IMPL）

1. **先写测试**（用例 1–21），确认它们在 **没有** 039 时是红的
   （用例 2/3/15/16/17/18/19/20/21 会红，版本号用例 1 会红）。
2. 写 `moni/db/migrations/039_xbot_feed_source.sql`（§5.4 草案）——
   **字面量里不许有 `;` / `--`**（§5.4.1）。
3. 改 `moni/db/seed_config.py:311` 的 notes 文案（与 039 逐字一致，§5.4.1）。
4. 改 `moni/db/migrate.py:21` → `_CURRENT_VERSION = 39`。
5. 改 6 处 `== 38` → `== 39` + 1 处报错文案（§2.2）；改完后 `grep -rn "== 38" tests/`
   应只剩 039 测试里那处刻意的 `assert old_version == 38`。
6. 跑定向测试 → 全量测试 → 记录实际条数。
7. 写文档：`docs/plans/2026-11-05_...-AUDIT.md`、`docs/sprint-evidence/sprint-30/RUN.md`、
   `docs/sprint-evidence/sprint-30/xbot-feed-import-stats.json`。
8. 改 `2026-11-03_xbot-feed-source.md`（加指针 + 改 status）+ `docs/sprint-evidence/README.md`。
9. 改 `README.md` / `USER_GUIDE.md` / `STATUS.md` —— **升级指令一律写
   `python3 -m moni.db.migrate`，不许出现 `moni db init`**（§4.3.1）。
10. **全量测试 + 记录真实数字**（不许写"预计"）。
11. 交审计（独立 warden，不写代码）。

---

## 11. 验收标准

- [x] `moni/db/migrations/039_xbot_feed_source.sql` 存在，`_CURRENT_VERSION == 39`
- [x] **039 的 SQL 里所有字符串字面量不含 `;` / `--`**（用例 18 钉死）
- [x] **强制走 runner 兜底路径（`split(';')`）时 039 不抛异常**（用例 19 钉死）
- [x] `moni/db/seed_config.py` 的 notes 与 039 的 notes **逐字相同**（用例 11 钉死）
- [x] 全新 DB → `migrate()` → schema 39，源存在，4 个绑定在 `seed()` 之后齐全
- [x] v38 库（无本源）→ `migrate()` → 源存在（**不跑 seed-config**）
- [x] 迁移后 `_is_source_bypassed("xbot_feed") == True`（源行缺失会让它静默变 False，见 §1.0）
- [x] 用户改过的源（含 `is_active=0`）跑完迁移**逐字段不变**
- [x] **migrate-only 路径（`python3 -m moni.db.migrate`）跑完，用户改过的字段逐字段不变**（用例 20）
- [x] 迁移连跑两遍结果完全一致
- [x] `tag_definitions.keywords` 迁移前后完全一致
- [x] 4 个 tag 缺失时不抛异常、不建悬空链接
- [x] **4 个 tag 全停用时 `schema_cleanup_log` 不记录 039**（S-1 守卫，用例 24）
- [x] **用户用自己的 id 占用 `name='xbot_feed'` 时迁移不抛异常、不建悬空绑定**（S-3，用例 23）
- [x] **迁移前后除本源以外的所有 `sources` 行逐字段不变**（用例 26）
- [x] **手写的最小 v38 库（schema 38、无本源）能被当前代码升级到 39**，且**不依赖 git 历史**
      （用例 22 —— 浅克隆 / 导出副本 / 无 git 的环境都跑，IMPL-AUDIT S-5）
- [x] **用 `015a2c5` 的旧代码建的库（schema 38、无本源）能被当前代码升级到 39**（用例 22b，加强层）
- [x] 迁移写入的 `fetch_config` == `DEFAULT_FETCH_CONFIG`（测试钉死）
- [x] `grep -rn "== 38" tests/` **只命中 `tests/test_migration_039_xbot_feed.py` 里的
      `assert old_version == 38`**（那是"旧代码建的库是 schema 38"的刻意断言）；
      5 个文件、6 处断言 + 1 处报错文案全部改成 39
- [x] `docs/plans/2026-11-05_...-AUDIT.md` 落盘，含 9/6/8 三组条目 + commit hash
- [x] `docs/sprint-evidence/sprint-30/` 落盘，含 203/71/6/126/0 与"测试关键词"说明
- [x] `docs/sprint-evidence/README.md` 的 Contents 表**同时**有 sprint-27 / sprint-27-hotfix /
      sprint-30 三行（S-6）
- [x] `2026-11-03_xbot-feed-source.md` 加了指针、status 改成已合并
- [x] `README.md` / `USER_GUIDE.md` / `STATUS.md` 写清"升级不需要 seed-config"
      + "seed-config 会重置你改过的源字段"
- [x] **升级指令三处（README / USER_GUIDE / STATUS）一律是
      `python3 -m moni.db.migrate`，且明确写了"`moni db init` = migrate + seed，
      升级不要跑它"** —— 全文 grep 不到"用 `moni db init` 升级"的表述（§4.3.1）
- [x] 全量 pytest 通过，**实际条数写进 STATUS**（基线 1456 passed / 2 skipped）→ **1489 passed / 2 skipped**
      （rebase 到 `origin/main` @ `2ce8965` 后基线变 1475 → **1508 passed / 2 skipped**）
- [ ] 独立审计 APPROVE  ← **待办**（本 sprint 的实施方不做自审）

---

## 12. 不做的事

- 不改 `moni/fetch/sources/xbot_feed.py` 的任何一行（抓取逻辑）
- 不修 N-1..N-8（nit 全部不阻塞；N-7 单独 PR，见 Q-O-6）
- 不动 `moni/fetch/sources/follow_builders.py`（N-7 的老 bug 留给独立 PR）
- 不碰 X-bot 侧（`/workspace/x-digest`、`/workspace/xbot-feed`）
- **`moni/db/seed_config.py` 的逻辑不改**（它仍是新库的来源；039 是存量库的路径）
  —— 只改 `notes` 文案里的 `;` 一个字符串（§5.4.1）
- **不改 `moni db init` 的行为**（它是 migrate + seed）—— 只改文档里的升级指令（§4.3.1、Q-O-10）
- 不改 `config/sources.yaml`
- 不新建 tag（4 个绑定 tag 都是已有的）
- 不动 `tag_definitions.keywords`
- 不重算存量文章的 tag 归属（126 篇未命中的条目不进库，改关键词后自然进）
- 不删 `articles` 里任何历史数据

---

## 13. PLAN 修订记录

- **修订日期**: 2026-11-05
- **依据**: `/workspace/moni-plan/sprint30/PLAN-AUDIT.md`（判定 GO-with-fixes，2 BLOCKING）
- **范围**: **只修 B-1 / B-2**。SHOULD-FIX（S-1..S-6）与 NIT（N-1..N-8）
  **一律未动**，留给 Mttt。
- **未改任何代码** —— 本次只改这份 plan 文档。

### B-1：升级命令不能是 `moni db init`

**问题**：§7.1 原稿让用户跑 `moni db init` 升级，但 `cmd_init()` = `migrate() + seed_llm +
seed_config`（`moni/cli/main.py:45-50`），**必然跑 seed** → seed 的 `ON CONFLICT DO UPDATE`
把用户改过的 `display_name` / `fetch_interval_min` / `notes` / `fetch_config` 重置、
把取消掉的 tag 绑定补回。这让本 sprint 的核心卖点（"升级不再依赖 seed-config"）当场反转。

**改法**：升级指令统一改成 **migrate-only** —— `python3 -m moni.db.migrate`
（`migrate.py:124-133` 的现成入口，实跑确认**全量重跑**基表 `schema.sql`（幂等）
+ 跑 pending migrations、不 seed 任何 sources / tag_definitions）。**不改 `db init` 本身的行为**（范围外，列为 Q-O-10）。

| 位置 | 改动 |
|---|---|
| §0 | 加升级命令提示（migrate-only，别用 `db init`） |
| **§4.3.1（新增）** | 完整交代 B-1：`cmd_init` 的实际行为、实跑复现（字段被重置 + 绑定被补回）、migrate-only 的实跑证据、三条硬约束 |
| §7.1 README | 升级命令 → `python3 -m moni.db.migrate`；`db init` 注释改成"初始化 + migrate + seed ⚠️ 会重置你改过的源字段"；新增 `moni.db.migrate` 一行；加"升级时不要跑 `moni db init`"警告块 |
| §7.2 USER_GUIDE | §3.1 下方补升级命令 + 不要用 `db init`；`:1160` 的警告补上 migrate-only 与 `db init` 说明 |
| §7.3 STATUS | 交付要点表加一行"**升级命令** = `python3 -m moni.db.migrate`" |
| §8 风险 | **新增 R12**（升级指令写错 → 卖点反转） |
| §9 Q-O | **新增 Q-O-10**（`db init` 要不要改成不跑 seed —— 本 sprint 不改，另开 sprint） |
| §10 实施顺序 | 第 9 步明确"升级指令一律写 migrate-only，不许出现 `moni db init`"；步骤重新编号 |
| §11 验收标准 | 新增"migrate-only 路径用户字段不变（用例 20）" + "升级指令三处统一且写明 db init 危害"两条 |
| §12 不做的事 | 明确"不改 `db init` 的行为" |
| §9 Q-O | **Q-O-9 → Q-O-10**（`db init` 语义变更另开 sprint）；**新增 Q-O-9**（用户用自己的 id 重建同名源） |
| §6.1 测试 | **新增用例 20 / 21**（B-1 护栏）：migrate-only 不碰用户数据 / `cmd_init()` 确实会重置（把文档的根据钉死） |

### B-2：SQL 字面量里的 `;`（及 `--`）会让 runner 兜底路径炸掉整个迁移

**问题**：§5.4 的 `notes` 字面量含 `;`（`只做导入;`）。`executescript` 对它是正确的，
但 `_run_sql_file` 的兜底路径是 **naive `sql.split(";")`**（`migrate.py:64`）——
一旦 `executescript` 因任何原因失败就落到兜底路径，字面量被从中间劈开 →
`unrecognized token` → 迁移失败 → `schema_version` 卡在 38 →
因为 `main.py:778-779` 每个 `moni db *` 子命令都先 `migrate()`，**整个 db 命令面被堵死**。

**改法**：字面量里去掉 `;`（`--` 一并避开），并在 plan 里立成硬约束。

| 位置 | 改动 |
|---|---|
| §5.4 SQL 草案 | `notes` 文案改掉：`只做导入;` → `只做导入，`；`feed/*.json` → `feed 目录下的 json 文件`（顺手避开 `/*`） |
| §5.4 SQL 注释头 | 加"⚠️ 字面量约束"注释块（`;` / `--` 的后果 + 现有 37 个 .sql 零命中的惯例） |
| **§5.4.1（新增）** | 完整交代 B-2：`_run_sql_file` 两段式机制、**强制走兜底路径的实跑复现**（`;` → `unrecognized token`；无 `;` → OK）、逐字符实测表（`;` / `--` / `/*`）、仓库惯例扫描结果（37 个 .sql，0 命中）、三条具体做法 |
| §2.2 文件清单 | 加 `moni/db/seed_config.py` 一行（**只改 notes 文案**）——因为用例 11 要求两处逐字一致 |
| §2.3 不动 | 改写：seed 的**逻辑**不动，但 `notes` 那一行要**跟着 039 一起改**（否则用例 11 红） |
| §5.3 值表 | `notes` 一行指向 §5.4.1 的文案 |
| §6.1 测试 | **新增用例 18 / 19**（B-2 护栏）：扫字面量断言不含 `;` / `--`；强制兜底路径断言不抛异常 |
| §6.2 现有测试 | 补一句：`seed_config.py` 的 notes 文案一改，要 `grep -rn "只做导入" tests/ moni/` 自查 |
| §6.3 基线数字 | 用例数 17 → **21**，1473 → **1477**（1456 + 21） |
| §8 风险 | **新增 R13**（`;` 字面量 → 升级路径堵死） |
| §10 实施顺序 | 新增第 3 步：改 `seed_config.py:311` 的 notes（与 039 一致） |
| §11 验收标准 | 新增两条：字面量不含 `;` / `--`（用例 18）；强制兜底路径不抛异常（用例 19）；两处 notes 逐字相同（用例 11） |
| §12 不做的事 | 明确 seed 只改 notes 一个字符串 |

### 顺带核过、确认无需改的地方

- §5.4 的 `BEGIN IMMEDIATE;` / `COMMIT;` 结构：兜底路径实测可跑通
  （naive split 切出的 6 段逐条 execute 成功，源 1 行 + 绑定 4 条）。
- `fetch_config` 的 JSON 字面量：不含 `;` / `--`，安全。
- §3.4 的 `moni db init`：那是**建全新空库**的 evidence 环境，不是存量库升级，
  已就地加注区分（升级一律 migrate-only）。

---

## 14. PLAN 修订记录（第二轮：S-1..S-6 + Mttt 拍板）

- **修订日期**: 2026-11-05 17:30（Mttt GO）
- **依据**: `/workspace/moni-plan/sprint30/PLAN-AUDIT.md` 判定 **GO-with-fixes**
  —— 2 个 BLOCKING（B-1 / B-2）+ 6 个 SHOULD-FIX（S-1..S-6）+ 8 个 NIT
- **Mttt 的决定**:
  1. **GO**（2026-11-05 17:30）。
  2. **Q-O-2（用户删掉的源要不要恢复）= 恢复** —— 保留 §5.5(a) 的设计，不改。
  3. **B-1（升级路径）= migrate-only** —— `python3 -m moni.db.migrate`，
     不改 `moni db init` 的行为（Q-O-10 仍另开 sprint）。
  4. **SHOULD-FIX 全部修**（S-1..S-6），不是"留给 Mttt 再定"。
  5. NIT（N-1..N-8）不阻塞 —— 顺手修掉 N-1 / N-8（纯文案，不改语义）。

### 14.1 逐条修法

| # | 问题 | 改法 | 落在哪 |
|---|---|---|---|
| **S-1** | `schema_cleanup_log` 守卫只检查"源行存在"，与注释**自相矛盾**（注释说"不会谎称成功"，实测它就是谎称），且违反 AGENTS.md §1.4「守卫要数 `is_active = 1` 的生效绑定」 | 守卫改成与 037 同构：数 `source_tags JOIN tag_definitions ON is_active=1` 的**生效绑定数 ≥ 4**；SQL 注释同步改成与行为一致 | §5.4 SQL 块、§5.5(b)、§5.5(d)、用例 24 |
| **S-2** | `source_tags` 的插入**不 JOIN `sources`** → 源行没建成时会留下**悬空绑定**（事后检测 ≠ 预防） | 改成 `FROM sources s JOIN tag_definitions t`（与 `037:25-26` 完全对齐） | §5.4 SQL 块、§5.5(d) |
| **S-3** | `sources.name` 的 UNIQUE 冲突被 `INSERT OR IGNORE` **静默吞掉** → 源行没建成、迁移却"成功"；裸 INSERT 则整个迁移失败 → schema 卡在 38 | 改成 `INSERT ... SELECT ... WHERE NOT EXISTS (id = 'src-xbot-feed' OR name = 'xbot_feed')`：用户已占用名字 → **当成已满足**，不插入、不报错 | §5.4 SQL 块、§5.5、**新增 Q-O-9**、用例 23 |
| **S-4** | §4.2 只列了 `sources` 表的字段，漏了 seed **还会重置 tag 的 `display_name` / `description` / `color` / `is_active`** —— 用户停用的 tag 被重新启用 = 闸门重新打开 | §4.2 实测表补 4 行（含实测 before/after）；§7.2 的 USER_GUIDE 文案同步扩写 | §4.2、§7.2 |
| **S-5** | N-7 的行号引用不准（`:149` 应为 `:152`）；§3.3 大纲里行内混排 `**` 加粗破相 | 行号统一改成 `follow_builders.py:152`；`--` 破相的加粗改成普通文字 | §3.3、§7.3 |
| **S-6** | `docs/sprint-evidence/README.md` 的 Contents 表**没有 sprint-27 / sprint-27-hotfix 两行**（`grep -c "sprint-27"` → 0），索引烂着；`RUN.md` 是新命名但没交代 | Contents 表**补三行**（sprint-27 / sprint-27-hotfix / sprint-30）；在 evidence README 里注明 `RUN.md` 是 sprint-30 新起的命名 | §2.1、§3.4、§7.4 |

### 14.2 顺带修掉的 NIT

| # | 改法 |
|---|---|
| **N-1** | §1.2 的算式改成 `203 − 6 − 126 = 71`（去掉无意义的中间量 197） |
| **N-8** | §6.4 改成"conftest 提供 `_snapshot_data_dir` / `assert_data_dir_unchanged` helper，session fixture 在**结束时**调用"（原文把 helper 的存在说成 fixture 自动断言） |

### 14.3 修订带来的**语义变化**（实施时必须知道）

1. **fresh DB 上 `schema_cleanup_log` 里不会有 039 这一行** —— 迁移在 seed 之前跑，
   那时 `tag_definitions` 是空的 → 生效绑定 0 条（< 4）→ 守卫不记成功。
   这是 S-1 修法的**必然结果**，也是 037 同样的取舍。接受。
2. **Q-O-9 成了 Q-O-10**（原 Q-O-9「`db init` 要不要改成不跑 seed」挪到 Q-O-10）。
3. **§6.1 从 21 条用例增到 26 条**（IMPL-AUDIT S-5 之后是 27 行），
   落地文件是 **33 个 test 函数**
   （1456 + 33 = **1489 passed / 2 skipped**，与实测一致；
   rebase 后基线 1475 + 33 = **1508 passed / 2 skipped**）。
4. **§8 风险表新增 R14..R17**（S-1..S-4 各自的缓解措施）。
5. **§11 验收标准新增 4 条**（守卫不谎报 / 同名源不炸 / 别的源不动 / 旧代码建的库能升级）。

### 14.4 Mttt 的拍板（Q-O-2）

> **Q-O-2 = 恢复**：用户主动删掉的 `src-xbot-feed`，migration 会**重新插入**。

理由（与 §5.5(a) 一致）：`moni db init` 每次都跑 `seed()`，而 `seed()` 是 upsert
—— 源会被加回来。migration 不加就出现"重启一次 `db init` 源又出现了"的不一致。
想停用请用 `is_active = 0`（USER_GUIDE.md §3.1 明确写"停用源：不删"）。
迁移**不会碰** `is_active`，所以停用状态永远不会被覆盖。
