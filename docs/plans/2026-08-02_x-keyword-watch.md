# Plan: X 关键词有价值内容抓取入库 (v1.0.11 x-keyword-watch)

> 项目: moni · 创建: 2026-08-02 · 状态: Sprint Kickoff (拍板默认项通过) · 分支: `feature/v1.0.11-x-keyword-watch`
> 工作流: sprint-workflow v2.3.0 (medium → 完整 sprint Phase 1→3)
> 配套文档: PRD `docs/prd/2026-08-02_x-keyword-watch.md` · TECH-SPEC `docs/specs/2026-08-02_x-keyword-watch.md` · TEST-PLAN `docs/test/2026-08-02_x-keyword-watch.md`

## Goal (一句话)

让 moni 能按用户定义的关键词自动发现并入库 X 上「命中关键词且有价值」的帖子/回复，服务学习型话题（例：世界怎么看微信小程序触达/服务通知能力）。

## 调研结论 (2026-08-02 全部实测)

### 1. 现在 bestblog-x 到底抓了什么

- 160 个固定 X 账号的本地 OPML（`config/bestblogs_x.opml`），每个 feed 是 `https://api.xgo.ing/rss/user/{id}`（XGo.ing 免费个人时间线 RSS）。
- 内容 = 该账号发的帖 + **该账号自己的回复**（实测：宝玉、LangChain 的回复推文都在各自 feed 里），含互动数文本（💬/🔄/❤️/👀/📊）、引用帖；入库为 `articles.raw_content`，`publisher` = 账号名。
- **没有**关键词搜索；抓不到「陌生账号的评论/回复」。

### 2. 用户给的例子

- `x.com/xiaoyangX026/status/2083475663496044761` 这条「评论」本身是 xiaoyangX026 回复 @Philo2022 的一条推文（x.com SEO 页面与 syndication 接口均拿到全文：「UX很流畅的感觉。但是我觉得最核心需求是要高触达，及时提醒还方便，在这一点上国内APP没有能比得过微信小程序的」）。
- xiaoyangX026 **不在** 160 账号 OPML 里 → 现链路 100% 抓不到。
- 结论：靠现有 bestblog-x 链路只能做「160 账号文本关键词过滤」，覆盖不了「世界怎么看某话题」这种开放发现。

### 3. 可行路径（均已实测）

| 路径 | 能力 | 成本 | 实测结果 |
|---|---|---|---|
| A. DDG 搜索发现 URL + syndication 抓全文 | 按关键词发现任意推文（含回复）；单推文全文（text/user/时间/互动/in_reply_to 链） | 免费，无 key 无登录 | ✅ `html.duckduckgo.com/html/?q=site:x.com 微信小程序 触达` 返回真实推文 URL；`cdn.syndication.twimg.com/tweet-result?id=…&token=a` 返回完整 JSON |
| B. xgo.ing OpenAPI（Plus/Pro 付费） | 关键词搜索（含回复，完整搜索语法 from:/#/min_faves/lang/-filter:replies），单次≤100 条，互动刷新 | 需 XGO_API_KEY + 会员，本机无 | 接口文档确认存在，未实测（需 key） |
| C. BestBlogs API v2 `/search` | 语义+BM25 全文搜索，但只覆盖 BestBlogs 收录池（不含任意 X 回复） | 本机已有 key | ⚠️ 实测 25s 超时未通，待重验 |
| D. 现有 bestblog-x 内过滤 | 160 账号文本命中 | 免费 | 覆盖不了示例 |

## 建议方案

**A（默认，免费可跑通）+ 手动 URL 收录；预留 B 作为升级开关**

- 新源 `xwatch`（`sources` 表一行），关键词列表放 `fetch_config`（本期不建新表，最简）。
- 抓取流水线：关键词 → DDG 发现推文 URL → 去重（`UNIQUE(source, url)` 已有）→ syndication 取全文 → 走现有 tag_filter + LLM 摘要 + business value + brief 链路。
- 手动 URL 收录：用户贴 `https://x.com/…/status/{id}` → 直接 syndication 入库（最贴合本例子）。
- B 开关：若将来买 xgo 会员，discovery backend 换成 `/openapi/v1/tweet/search`，其余不动。

## 验收

### 自动测试
- [ ] discovery 模块（DDG）解析 mock HTML → 正确抽 URL；风控/异常降级
- [ ] syndication 解析 fixture JSON → article dict 字段完整（text/user/created_at/互动/in_reply_to）
- [ ] 入库去重：重复 URL 不重复插入
- [ ] 关键词 watchlist 读写 + fetch 触发
- [ ] tag_filter/黑名单对新源生效
- [ ] 全套 pytest 不回归（439+）

### 手动
- [ ] `/usr/local/bin/python3 -m moni fetch xwatch` 跑通；sqlite 可见新文章
- [ ] 粘贴示例 URL 入库成功，LLM 摘要可用
- [ ] 8901 手测 fetch_jobs 进度

## 拆 step

### Step 1 — 契约拍板 (0.5d)
- [ ] 关键词管理形态（源级 fetch_config JSON vs watchlist 表）
- [ ] 每 query 映射 tag（例：小程序触达 → `wechat` 或新增 tag）
- [ ] 本 plan 拍板（Q1/Q2/Q3 默认项）

### Step 2 — 数据 + 管线 (1.5-2d)
- [ ] `seed_config.py` 新源行 + tag + 黑名单
- [ ] `moni/fetch/sources/xwatch.py`：`FetcherXWatch`（discovery + syndication ingest）
- [ ] `moni/fetch/syndication.py`：tweet-result 解析 + x.com SEO 页面 fallback
- [ ] `tag_filter.py`：新源加入「短摘要」处理分支（现为 `startswith("bestblog")`）
- [ ] 手动收录入口（CLI 先行，UI 可后置）

### Step 3 — 测 + 文档 + PR (0.5d)
- [ ] pytest + 8901 手测
- [ ] STATUS / USER_GUIDE 更新
- [ ] PR → review → squash → tag v1.0.11 → 启 8900

## 非目标 (本期不做)

- 整楼评论/回复树爬取（需登录态或额外 API；单条回复按关键词/URL 已可覆盖）
- x.com 登录态抓取
- X API / xgo OpenAPI 付费接入（只留 backend 抽象开关）
- UI 大改（关键词管理 UI 可放 v1.0.12）

## 风险

| 风险 | 缓解 |
|---|---|
| DDG 风控/限速（实测偶发 anomaly 页） | discovery 层节流 + 重试 + 多关键词轮换；失败降级不阻塞其他源 |
| syndication 对敏感/受限推文失效 | x.com SEO 页面 fallback；失败跳过 + 日志 |
| xgo OpenAPI 需付费才能更稳 | 本期不做，留 backend 抽象 |
| BestBlogs 搜索超时 | 待重验；不作为依赖 |

## Blocking Questions（拍板用，默认推荐项）

- **Q1 发现后端**：仅 A 免费路径（推荐，先跑通）／直接上 B 付费／A+B 都做
- **Q2 抓取粒度**：单条推文命中即收（推荐，你的例子就是单条回复推文）／还要某帖子下全部评论
- **Q3 手动 URL 收录**：本期做（推荐，最贴合你的例子）／只做关键词自动

### 拍板记录（2026-08-02，用户授权按推荐项执行）

- Q1 → **A 免费路径**（DDG 发现 + syndication 抓全文；xgo OpenAPI 留 backend 抽象，本期不接）
- Q2 → **单条推文命中即收**（不爬整楼评论）
- Q3 → **手动 URL 收录本期做**（CLI：`moni xwatch ingest <url>`）

## 关键设计决策（调研后确定，见 TECH-SPEC）

- 新源 `xwatch`，关键词放 `sources.fetch_config.queries`（**无需 migration**，schema v22 不变，source 数 7 → 8）
- 注册要动**两处**：`config/sources.yaml`（scheduler 读取）+ `moni/db/seed_config.py`（DB sources 表）
- 内容门控直接用现有 tag 机制：xwatch 绑定 `wechat` + `miniprogram` 两个已有 tag，其关键词（微信小程序/服务通知/订阅消息…）由 `tag_keyword_filter` 自动过滤 + 打 tag（v1.0.5.20 keyword_hits 机制），无需新表
- 示例推文（xiaoyangX026 回复）已实测可经 syndication 取全文，作为验收样例

## Stop signals

- 免费路径被大范围风控打不通，或需要登录态才能取到目标内容 → 停，汇报再定
- 范围膨胀（UI / 付费接入 / 多搜索引擎）超出本 plan → 停
- 需动 schema 且无法安全 migration → 先提出来

## 双写

- 项目内：`docs/plans/2026-08-02_x-keyword-watch.md`
- Obsidian：`tqob/05-Coding/project-moni/plans/2026-08-02_x-keyword-watch.md`
- Sprint 启动时：`tqob/05-Coding/project-moni/sprints/sprint-01-x-keyword-watch/`（plan/prd/tech/test 全套）
