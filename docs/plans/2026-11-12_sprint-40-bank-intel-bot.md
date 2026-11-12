---
id: 26111205
type: plan
version: v1.0.76
date: 2026-11-12
status: 进行中
sprint: "[[sprint-40-bank-intel-bot-2026-11-12]]"
---

# Sprint 40 — 银行同业情报 agent 管道（PRD + PLAN 双写）

> 过程记录（TECH-SPEC / TEST-PLAN / VERIFY）在 Obsidian
> `sprints/sprint-40-bank-intel-bot-2026-11-12/`。
> 调研依据：`docs/plans/2026-11-12_bank-peer-source-plan.md`（PR #185）。

---

## PRD

### 问题

汇报宣称的「同业追踪」在 moni 里没有数据支撑：

| 指标 | 实测 |
|---|---|
| 近 90 天 `banking` 文章 | 1223 篇 |
| 其中提到具体银行名 | **36 篇 = 2.9%** |
| 案例库 424 条的来源集中度 | **mpaypass 319 + 36kr 105 = 100%** |
| 银行类案例 | 135 条 / 104 家主体（每家 1.3 条） |

现有 13 个 active 源没有一个「以银行为主角」；`banking` 的 300+ 关键词是**主题词**
（六大行/监管/宏观），闸门放进来的全是宏观泛金融。

### 用户与场景

**用户**：银行业务/汇报材料撰写者。
**场景**：在 moni 里按「同业」维度检索「哪家银行做了什么」→ 沉淀案例 → 引用进汇报。

### 功能需求

| # | 需求 | 验收 |
|---|---|---|
| F1 | 抓取中/农/工/建/邮储的**资讯与新闻**（非公告） | feed 中出现 5 家行条目，字段含 `bank` |
| F2 | 支持**公众号文章**（已知 URL，浏览器渲染） | `source_type=wechat` 条目可入库 |
| F3 | 支持**媒体 RSS**（稳定公开 RSS） | 6 个 RSS 目标产出条目 |
| F4 | **web 端点击抓取**即可启动 bot | `POST /api/fetch/sources/bank_feed/run` 触发 agent |
| F5 | 后续加信源**零代码** | 改 `fetch_config.targets` 即生效 |
| F6 | 只入库真实命中 `peer-banks` 的条目 | 零命中条目不入库、无零 tag 文章 |

### 非功能需求

- 不新增 LLM 凭证：agent 走 `hermes --profile moni` → magpie（同 moni 现有链路）
- 不新增常驻服务、不新增 git 仓库、不碰微信 cookie
- agent 失败必须显式报错，不得静默 0 篇
- 抓取超时可控（`fetch_config.timeout_sec`）

### 已验证的能力（2026-11-12 实测）

- hermes web 搜索取到三大行真实新闻（标题/日期/链接）
- hermes browser 渲染 `mp.weixin.qq.com` 正文成功（`web_extract` 被 Blocked 时改浏览器）
- hermes browser 渲染中行 `/aboutboc/bi1/`（一页 20 条自带日期）、工行「工行快讯」成功
- 媒体 RSS 命中：未央网 7/30、东财 2/98、界面 1/30、FT中文 1/20、中新经纬 1/30、一财 JSON 可翻页
- 已知限制：公众号「自动发现」做不到（搜狗微信验证码 + 官微不被索引）→ 只做已知 URL 渲染

---

## PLAN

| 步 | 内容 | 状态 |
|---|---|---|
| 1 | `moni/fetch/sources/bank_feed.py`（独立源，本地目录 transport） | ✅ |
| 2 | `peer-banks` tag 进 `_DEFAULT_TAGS` + `_DEFAULT_TAG_KEYWORDS` | ✅ |
| 3 | `_DEFAULT_SOURCES` + `_DEFAULT_SOURCE_TAGS` | ✅ |
| 4 | migration 041 + `_CURRENT_VERSION` 40→41（全局 grep 同步 6 处断言） | ✅ |
| 5 | `config/sources.yaml` 加 `bank_feed` | ✅ |
| 6 | `scripts/bank_bot/run_bot.py` + `config/bank_targets.json`（11 目标） | ✅ |
| 7 | `tests/test_bank_feed.py` / `test_bank_bot.py` / `test_migration_041_bank_feed.py`（80 例） | ✅ |
| 8 | web 端点验收（8903：8901/8902 被其他 agent 占用）+ pytest 全绿 | ✅ |
| 9 | STATUS.md / USER_GUIDE.md 同步 | ✅ |
| 10 | PR → 独立 audit → 用户授权 → squash merge → tag | ⏳ |

### 实测修正（实现过程中被真实数据推翻的两处设计）

**① tag 关键词只放银行主体词，不放动作词。**
原设计想用「实体词 + 动作词双条件」挡住宏观稿，但 `tag_filter` 是纯 OR 闸门
(`moni/fetch/tag_filter.py:apply_tag_keyword_filter`)，没法表达 AND；而动作词
（发布/签约/落地）在**任何**宏观稿里都会出现，放进去等于把闸门拆了。
改为：tag 关键词 = 五家行名（全称 + 新闻常用两字简称 + 英文缩写），
「哪家银行做了什么」由 bot 的目标清单保证（一个 target 钉死一家行）。
副作用是两字简称走 `keyword_match` 的短 CJK 规则（命中标题或正文重复 2 次），
正好把「正文里一句带过」的宏观稿挡在外面。

**② 媒体条目的银行归属只看标题。**
第一版用标题 + 正文归属，实测东财 3 条大盘复盘（「A股三大指数收跌」）因为
正文提了一句工行，全被记成 `icbc`。改成只认标题后同样的输入只剩 2 条真新闻。

**③ agent 段改成一页一次调用。**
第一版把 5 个官网塞进一次 `hermes -z`，实测 600s 都没跑完（浏览器逐页渲染
5 个 SPA），5 个官网**全丢**。改成每个 target 一次调用（单目标 240s，
总预算 1200s），一个页面卡住不再拖垮其余四个。

### 关键决策

- **不做 cron**：只做 web 端点击触发（用户明确要求）
- **不做自建 RSSHub**：用户明确否决
- **transport 用本地目录**（`data/bank_feed/`）而非 git 桥接：bot 与 moni 同机，少一层 git 凭据/冲突
- **独立 fetcher 而非继承 xbot_feed**：transport、契约、tag 逻辑都不同；继承会把 xbot 的
  migration-039 逐字比对护栏带偏
- **正文里不写中文行名**：只写 `bank:ccb` 这种代码，否则 tag 闸门恒真、退化成摆设

### 风险

| 风险 | 缓解 |
|---|---|
| agent 输出格式漂移 | run_bot.py schema 校验 + 逐条 `item_problems()`，坏条目丢弃、坏 JSON 非 0 退出 |
| agent 抓不到 → 0 篇被误读成「今天没新闻」 | 全源失败且 0 条 → 退出码 2 **且不写当天 feed**；fetcher 侧再抛 RuntimeError 让 job 记 failed |
| `peer-banks` 过松 → 宏观稿混入 | 只放行名主体词（见上）；实测东财 98 条 → 2 条、中新经纬 30 条 → 1 条 |
| 官网 SPA / 改版 | agent 自行发现栏目，不写死选择器 |
