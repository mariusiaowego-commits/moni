---
id: 26110301
type: sprint
version: v1.0.66
start_date: 2026-11-03
end_date: 2026-11-03
status: 已实现（PR #172 已合并 e53956c，v1.0.66）
priority: 中
summary: 新增 xbot_feed 源 —— 把 X-bot 每天挑好的内容（微信话题 + 推特圈）经私有 git 仓库导入 moni
tags: [sprint, project-moni, fetcher, xbot-feed]
---

# xbot_feed 源（X-bot 精选桥接）

> **审计与真实运行**：见 `docs/plans/2026-11-05_sprint-30-xbot-feed-backfill-and-migration-AUDIT.md`
> （PR #172 独立审计回填）与 `docs/sprint-evidence/sprint-30/RUN.md`（真实运行数字）。
>
> **迁移补齐（v1.0.67 / sprint-30）**：本方案落地时**没有 migration** ——
> 新源只进了 `seed_config.py` + `config/sources.yaml`，没进任何存量数据库。
> migration 039 补上了这一步，见
> `docs/plans/2026-11-05_sprint-30-xbot-feed-backfill-and-migration.md`。

> **上游方案**：`/workspace/moni-plan/moni-wechat-source-plan.md`（原名 `x_wechat`，
> 最终改名 `xbot_feed`，见该文档开头的「最终决定」）
>
> **X-bot 侧工作流**：`/workspace/moni-plan/wechat-workflow.md` 第 14 节

---

## 1. 目标与边界

把 X-bot 每天挑好的内容导入 moni，作为第 14 个抓取源。

| 项 | 值 |
|---|---|
| 源名 (`sources.name`) | `xbot_feed` |
| DB id (`sources.id`) | `src-xbot-feed` |
| 显示名 (`display_name`) | 「X-bot 精选」 |
| fetcher 类 | `FetcherXbotFeed`（`moni/fetch/sources/xbot_feed.py`） |
| `fetch_interval_min` | 60 |
| schema | 38（无新迁移） |

**不在范围内**：不碰 X API（不花 API 额度）、不新增 articles 列、不合并本 PR
（合入和发版要 Mttt 确认）、不动 `AGENTS.md`（本地 gitignore 文件）。

---

## 2. 命名：为什么不叫 `x_wechat`

1. **源不限微信**。上游 feed 有两个 topic：`wechat`（微信/小程序）和 `xcircle`
   （推特圈固定账号），两个都导。源名带 `wechat` 会误导。
2. **避免与 `xwatch` 语义打架**。moni 已有 `xwatch`（X 关键词监控，自己抓），
   `xbot_feed` 是"导入 X-bot 已经挑好的内容"，两者来源不同。
3. **对得上上游**。上游仓库就叫 `xbot-feed`。

---

## 3. 数据链路

```
X-bot 抓取（/workspace/x-digest/wtoday*.txt + atoday*.txt）
   ↓
xbot-feed 导出器（独立仓库 /workspace/xbot-feed）
   ↓  git commit → 私有仓库 mariusiaowego-commits/xbot-feed
   ↓  feed/YYYY-MM-DD.json + feed/latest.json
moni FetcherXbotFeed
   ↓  <MONI_DATA_DIR>/xbot-feed/ 下的本地 clone
   ↓  git clone（没有）/ git pull --ff-only（有）
   ↓  读 feed/*.json → 按 tweet id 去重 → topic→tag → handle 黑名单
articles 表（source='xbot_feed'）
```

**moni 不持有任何 key**。私有仓库的访问权来自**机器上已有的 git 凭据**
（SSH agent / credential helper）。这跟 `moni/llm/client.py` 的薄壳原则一致：
moni 消费上游的产物，不直接调用需要鉴权的第三方 API。

---

## 4. topic → 候选 tag → 关键词闸门

| topic | 内容 | **候选** tag | 理由 |
|---|---|---|---|
| `wechat` | 微信/小程序话题 | `wechat` + `miniprogram` | 就是微信生态话题，小程序是核心议题 |
| `xcircle` | 推特圈（28 个固定中文账号） | `general-tech` + `ai-fintech` | 中文技术/投资圈的一般讨论；其中"AI × 金融/投资"归 `ai-fintech` |

候选名单写在 `fetch_config.topic_tags`（可在 web 端改），默认就是上面这张表。

**不新建 tag**。理由两条：

- 新建一个**关键词为空**的 tag 会让 `apply_tag_keyword_filter()` 变成透传，
  且每篇文章会带一个语义空泛的标签 —— migration 037 踩过这个坑。
- 现有 4 个 tag 的关键词并集正好构成入库闸门。

---

## 5. 关键实现：tag 由**关键词真实命中**决定（v1.0.66 审计 B1 重做）

> **这一节在 2026-11-03 的独立审计里被整段推翻并重写。**
> 原设计（按 topic 硬打 tag）见下面的「原设计（已废弃）」小节。

现在的机制：

```
item.topic
   ↓  fetch_config.topic_tags（默认表，可改）
候选 tag 名单
   ↓  tag_definitions.keywords 里该 tag **当前**的关键词
   ↓  moni/fetch/keyword_match.py::keyword_matches（标题 + 正文）
命中的 tag 名单 + 真实命中关键词
   ↓
只挂命中的 tag；零命中 → 该条不入库
```

规则：

1. **候选名单**来自 `fetch_config.topic_tags`；没配过的 topic 若有**同名
   active tag** 就用它（上游加 topic 的最小代价路径），否则跳过 + warning。
2. **关键词**从 `tag_definitions.keywords` 读，每次抓取都重读 —— 用户在
   `/config` 改完关键词，下一轮立刻生效。
3. **匹配只用 `moni/fetch/keyword_match.py`**（AGENTS.md 铁律 1：关键词匹配
   只能走这一个引擎）。
4. **只挂命中的 tag**，命中关键词写进 `article_keyword_hits`（前端高亮的是
   用户自己配的那个词，不是 `(topic:xxx)` 合成串）。
5. **零命中 → 不入库**，不产生零 tag 的文章（AGENTS.md 铁律 3）。
6. **被关键词挡掉的条目不写进已导入 state** —— 改完关键词下一轮还能进。

`bypass_keyword_filters: true`（同 `follow_builders`）现在只短路**全局/源级
黑白名单**。tag_filter 的 bypass 分支仍会合成一批 `hit_field == "bypass"`
的假命中，`dedupe_insert()` 把它们丢掉，只透传 fetcher 自己算出来的真实命中。

### 原设计（已废弃）

~~`bypass_keyword_filters` 打开后，`moni/fetch/tag_filter.py` 的 bypass 分支
会给**每一篇**文章合成**全部**绑定 tag 的命中。`FetcherXbotFeed` 因此覆写
`dedupe_insert()`，丢掉那批合成命中，换成按 topic 硬打的命中。~~

废弃原因（审计 B1）：

- tag 关键词闸门一行都不跑 —— 用户在 `/config` 改 `wechat` / `miniprogram` /
  `general-tech` / `ai-fintech` 的关键词对本源**零效果**，与已确认的设计冲突；
- `xcircle` 的每一条推文都被挂上 `ai-fintech`，前端按 tag 筛全是噪音；
- tag 决策完全不经过 `keyword_match` 引擎，违反 AGENTS.md 铁律 1。

---

## 6. 去重（三层）

1. **物理约束**：`articles` 表的 `UNIQUE(source, url)` 不动。
2. **tweet id 去重**（源内）：导入状态记在 `<MONI_DATA_DIR>/.xbot_feed_state.json`。
   状态**在入库成功之后**才落盘 —— git 失败或入库失败都不会把条目标记成
   "已导入"，否则这批内容就永久丢了。被闸门挡掉的条目不在 id 列表里，所以
   闸门配置变了以后它们还有机会入库。
3. **跨源软去重**：同一条推文可能已经被 `bestblog-x` / `xwatch` 存过
   （URL 变体：`twitter.com` vs `x.com`、带 `?s=20`）。沿用 `follow_builders`
   的 `normalize_tweet_url()` + 全局 EXISTS 检查。
   **v1.0.66 审计 B2**：旧查询 `url LIKE '%status/<id>%'` 的结尾 `%` 让它变成
   **前缀**匹配 —— id `…123` 会把已入库的 `…1234` 当成同一条，真条目被软去重
   吃掉（漏采）。现在改成**精确尾部匹配**：`/status/<id>` 之后只能是字符串
   结束 / `?` / `#` / `/`，不能是另一个数字。

---

## 7. 其他决定

| 事项 | 决定 | 理由 |
|---|---|---|
| 未知 topic | **不入库**，记 warning | 上游加新 topic 时宁可少收也不要错打 tag |
| handle 黑名单 | 内置 5 个（刷量/营销/灰产号），可在 `fetch_config` 改 | 这是**按作者**的窄兜底，不是内容闸门（内容闸门统一走 `keyword_match` 引擎） |
| git 失败 | 记 `error`，**绝不静默返回 0 篇** | 静默 0 篇跟"今天真的没内容"看起来一模一样，但含义完全相反 |
| git 输入校验 | `repo_url` 只接受 `https://github.com/<owner>/<repo>[.git]`；`clone_dir` 必须落在数据目录内 | 审计 B-6：`/config` 可改的字段直接进 git argv / 写文件路径 |
| git 输出脱敏 | URL 里的 `user:token@` → `***` | 审计 B-7：git 认证失败时会把带 token 的 URL 打进 stderr |
| 黑名单账号 | `jkghzone` / `sensenxnyz`（代购代付灰产）、`Eason3110188520`（签到打卡接单）、`duizhanggege`（小程序副业营销号）、`HunchoJ01`（关键词误命中的噪音号） | 判据来自 2026-11-03 首日 feed 实测（46 条 wechat + 157 条 xcircle） |
| 频率 | 60 分钟 | 抓取本身很轻（一次 git pull + 读 JSON） |
| `max_days` | 按**文件名里的日期**判断（最近 N 天） | 审计 S-2：旧实现是"最近 N 个文件"，依赖排序，语义不对 |
| fetch_config 默认值 | 单一来源 `moni/fetch/sources/xbot_feed.py::DEFAULT_FETCH_CONFIG` | 审计 B5：三处抄本会漂移 |

---

## 8. 改动清单

| 操作 | 路径 | 要点 |
|---|---|---|
| CREATE | `moni/fetch/sources/xbot_feed.py` | fetcher 本体 |
| CREATE | `moni/web/static/icons/sources/xbot_feed.png` | 源图标 |
| CREATE | `tests/test_xbot_feed_fetch.py` | 离线测试 |
| CREATE | `tests/test_obsidian_push.py` | 微信生态 tag 判定测试（B4/S-3） |
| CREATE | `tests/fixtures/xbot_feed/` | 2026-11-03 首日真实产出的样本 |
| MODIFY | `moni/fetch/sources/__init__.py` | 注册 fetcher |
| MODIFY | `moni/fetch/base.py` | `dedupe_insert` 的可变默认参数 → None（B3） |
| MODIFY | `config/sources.yaml` | 加条目（interval 60，enabled true，crawler_type json） |
| MODIFY | `moni/db/seed_config.py` | source + 4 个 tag 绑定 + `bypass_keyword_filters: true`；默认值引用 `DEFAULT_FETCH_CONFIG` |
| MODIFY | `moni/web/routes/config_sources.py` | `_FETCH_CONFIG_DEFAULTS` 引用 `DEFAULT_FETCH_CONFIG` |
| MODIFY | `moni/web/static/js/app.js` | icon / label / color / sortOrder |
| MODIFY | `moni/obsidian_push.py` | 微信生态判定改成按文章 tag |
| MODIFY | `README.md` / `STATUS.md` / `USER_GUIDE.md` | 源表与状态同步 |
| DELETE | ~~`scripts/make_xbot_feed_icon.py`~~ | 审计 B-9：一次性工具，与其他 12 个源图标不符，删掉只留 png |

---

## 9. 测试

```bash
uv venv --python 3.12 .venv && uv sync --extra dev
.venv/bin/python -m pytest tests/ -p no:cacheprovider -q
```

**结果：见 `docs/plans/2026-11-03_xbot-feed-source.md` 的「审计修复后的测试结果」
一节（按独立审计的 15 条修复逐条补测试后的数字）。**

新测试全程离线：git 操作全在 `tmp_path` 的本地 bare repo 上跑，不联网，
不硬编码 `/Users` 路径。覆盖解析、候选 tag → 关键词命中、id 去重、重复导入
幂等、跨源软去重（精确 id 匹配）、黑名单、git 失败报错、clone vs pull 分支、
git 输入校验与输出脱敏、零命中不入库、改关键词后下一轮能进、
`max_days` 按文件名日期、Obsidian 微信生态判定。

---

## 10. Mac 上首次运行

**这台机器的 git 必须能访问私有仓库 `mariusiaowego-commits/xbot-feed`。**

```bash
git ls-remote https://github.com/mariusiaowego-commits/xbot-feed.git
```

拉不到时 fetcher 记 `error`（`git clone/pull failed — cannot read upstream feed`），
**不会静默返回 0 篇** —— 所以第一次跑看到 error，先查 git 凭据，不是 moni 的 bug。

clone 落在 `<MONI_DATA_DIR>/xbot-feed/`，导入状态在
`<MONI_DATA_DIR>/.xbot_feed_state.json`。换位置改 `fetch_config.clone_dir`。

---

## 11. 审计修复后的测试结果

2026-11-03 的独立审计（`/workspace/moni-plan/pr172-audit.md`）提出 9 条
blocking + 6 条 should-fix。修复分 6 个 commit 落在 `feat/xbot-feed-source`
分支上：

| Commit | 修的条目 |
|---|---|
| `59553f2` | B1（tag 闸门改成关键词真实命中）+ B-8（去掉多余的 bypass 命中过滤） |
| `75983af` | B2（跨源去重精确 id 匹配）+ B3（可变默认参数 / 实例属性） |
| `329814d` | B-6（git 输入注入防护）+ B-7（git 输出凭据脱敏） |
| `9b520f9` | B5（fetch_config 默认值单一来源） |
| `c171dfe` | B4 + S-3（Obsidian 微信生态判定按 tag） |
| 本次提交 | B-9（删图标生成脚本）+ S-1（删死字段 `files`）+ S-2（`max_days` 按文件名日期）+ S-4（app.js sortOrder）+ S-5（`crawler_type: json`）+ S-6（本文档 / STATUS.md 同步） |

**全量测试**：

```bash
.venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

**结果：1450 passed / 2 skipped**（基线 1414 + 新增 36 条审计回归测试，
无退步）。CI 用同一条命令（`.github/workflows/ci.yml`）。
