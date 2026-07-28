---
source: ai-agent
created: 2026-07-03
type: audit
scope: full-stack
version: 1.0
---

# moni 全栈系统性评估 (2026-07-03)

> 评估视角: 从第一性原理出发, 围绕四个提升目标 — **精致程度 / 交互逻辑 / 内容来源丰富度 / 系统稳定性**。
> 基线: v1.0.9.11, main @ 0819e26, 428 pass + 2 skip。
> 上一次全面审计: 2026-06-26 (AI-moni-full-audit-2026-06-26.md)。本次不重复其结论, 聚焦增量与四目标。

## 一、执行摘要

| 指标 | 数值 |
|---|---|
| 后端 Python | 13,423 行 / 65 文件 |
| 前端 | app.js 3,302 行 + index.html 2,183 行 + main.css 1,010 行 |
| API 端点 | 75 个 / 14 路由模块 |
| DB | 1,498 篇文章 / 10 源 / schema v19 / WAL 已开 |
| 测试 | 428 pass + 2 skip / 21 文件 |
| LLM 摘要覆盖率 | 26/1498 = **1.7%** |
| bb_resource_id 覆盖率 | **0/1498** |
| 最近 3 个 share task | **全部 failed** (hermes -z no final response) |
| 生产页面 console error | **10 个** (fetchAllResult null 访问) |

**核心判断**: moni 的骨架是健康的 — 端口分离、PR 流程、428 测试、WAL、异步 fetch_jobs 都是同类自用项目少见的纪律性。但它现在处在"功能堆满、体验粗糙、数据能力闲置"的状态: 抓回来 1498 篇文章, 只有 26 篇有摘要、0 篇有 BB 增强、Pin 了 10 篇、分享产物最近全失败。**系统在勤奋地抓取, 但下游价值链断了**。四个提升目标里, 最高杠杆的不是再加信源, 而是先修复"抓取→理解→产出"这条链路, 再谈丰富度。

---

## 二、技术架构

```
                        ┌─────────────────────────────┐
                        │ Alpine.js SPA (index.html)   │
                        │ app.js 3302 行单文件          │
                        └───────────┬─────────────────┘
                                    │ fetch /api/*
                        ┌───────────▼─────────────────┐
   APScheduler ────────▶│ FastAPI (uvicorn :8900)      │
   (进程内, 无持久化)     │ 75 端点 / 14 路由             │
                        └───────┬──────────┬──────────┘
                                │          │ Popen
                    ┌───────────▼──┐  ┌────▼────────────────┐
                    │ SQLite (WAL) │  │ default_worker.sh    │
                    │ news.db 11表 │◀─┤ 921 行 bash          │
                    └───────▲──────┘  │ + 7 段 Python heredoc│
                            │         └────┬────────────────┘
                    ~/.moni/queue/*.json ◀──┘ (文件队列双轨)
                                          │ subprocess
                                     hermes chat CLI (LLM 薄壳)
```

### 好的部分
- 薄壳 LLM 设计 (moni 不持 key) 干净, `moni/llm/client.py` 职责单一
- fetcher 注册表 + BaseFetcher 管线 (fetch→parse→clean→filter→dedupe) 结构清晰, 加新源边际成本低
- WAL + busy_timeout=5000 + foreign_keys 已配 (`moni/db/connection.py:60-63`)
- 异步化改造 (llm_jobs / fetch_jobs 表 + 202 + poll) 方向正确

### 需要改的部分 (按稳定性目标排序)

1. **文件队列 + DB 状态双轨** (`~/.moni/queue/*.json` + `share_tasks` 表) — zombie task 的根源。没有事务能跨两个存储, crash 后必然不一致。历史上已修过 4 轮 (v1.0.9.1/9.3/9.5/9.6) 都是打补丁。根治: 队列体入 DB, 单一事实源, `UPDATE ... RETURNING` 原子领取 + `locked_at` 租约。文件队列整个删掉。工作量 ~1 天 + 迁移。
2. **default_worker.sh 921 行 bash + 7 段 Python heredoc** — env 传递踩坑 (v1.0.9.3 两个 bug)、无法单测、引号转义地狱都源于此。重写为 `python -m moni.worker` + pydantic-settings 显式加载 .env。这是消灭"env 加载 SOP"这条 AGENTS.md 铁律的唯一办法 — 好的架构不需要 SOP 提醒。工作量 ~2 天。
3. **调度器无进程管理** — cron_runs 显示抓取靠手动/session 内触发 (7-01 到 7-03 之间无 run), APScheduler 进程没有 launchd 托管, crash 即停。bestblog-yt/podcast 已 3 天没抓。加 launchd plist (`KeepAlive=true`) 一次性解决。工作量 ~2 小时。
4. **依赖声明失真** — pyproject.toml 只列 apscheduler + pyyaml, 但 24 个文件 import httpx/pydantic/fastapi/bs4。换机器 `pip install -e .` 必挂。补全 dependencies。工作量 ~15 分钟。
5. **连接单例 check_same_thread=False 无锁** (`connection.py:57`) — FastAPI 线程池 + APScheduler + worker 三方写同一连接。busy_timeout 挡住了大部分, 但写事务应显式 `BEGIN IMMEDIATE`。低频隐患, 中期修。

---

## 三、UI/UX 评估 (精致程度目标)

### 3.1 实测发现 (浏览器 + 截图分析, 2026-07-03)

**生产页面正在报 10 个 Alpine console error** — `fetchAllResult.status`、`fetchResultObj.dropped_articles` 等 null 访问。用户看不到, 但这是精致度的地基问题: 模板在数据未就绪时求值。修法统一为 `fetchAllResult?.status` 或外层 `x-if`。~1 小时。

**视觉秩序问题 (截图评估, 按严重度)**:

| # | 问题 | 位置 | 修法 |
|---|---|---|---|
| 1 | 行高剧烈波动 — 标题长度 + 标签数量撑行, 表格呈锯齿状 | 文章列表 | 固定行高, 标题 max 2 行 + ellipsis, hover 显全文 |
| 2 | 标签堆叠 — 一行 3-4 个分类标签换行撑高整行 | 分类列 | 最多显 2 个 + "+N" 折叠 |
| 3 | 状态色噪音 — fresh(绿)+new(紫)+分类(蓝) 同行争夺注意力 | 标题列 | new/fresh 合并为单一 dot 指示, 色彩预算留给分类 |
| 4 | 操作列过弱 — "查看"两个字无热区无图标 | 操作列 | 图标化 + 增大点击区 |
| 5 | 31 处 `alert()` 而 toast 系统缺失 | app.js 全局 | 统一 toast 组件 (undo 型操作用 action toast) |

### 3.2 对照研究结论 (docs/research/uiux-reader-dashboard-best-practices-2026.md)

- **层级靠字重+灰度而非字号**: 现在 main.css 的 oklch token 底子很好 (中国传统色主题有辨识度), 缺的是三级文字灰度 token 和字号收敛到 4-5 档
- **键盘导航缺失**: j/k 上下、p Pin、/ 聚焦搜索 — 阅读器类产品的核心留存交互, Alpine `@keydown.window` 实现成本 <1 天, 是**性价比最高的交互升级**
- **骨架屏**: 文章列表首屏加载用骨架屏 (布局可预测), 目前 skeleton 出现 2 次基本没用上
- **动效克制方向正确**: 粒子 modal 放在抓取这种低频高情绪节点是对的; 列表层面的动效应收敛到 150-250ms transform/opacity
- **a11y 接近零** (1 处 aria) + 移动端只有 1 条 @media — 单人桌面自用可降级, 但键盘可达性与 a11y 有重叠, 做键盘导航时顺手补

### 3.3 产出物审美 (分享 artifacts)

7 格式管线设计完整 (formats.py 407 行 schema 校验很规范), 但:
- 产出物样式由 LLM prompt 生成, **没有共享 design token** — 每次产出风格漂移
- 研究给出的落点: 小红书长图 (1080px/标题56-64px/卡片圆角24px)、经济学人图表 token、晚点单栏 640-680px — 应把这些 token 写进 `moni/share/prompts/format-*.md`, 让 LLM 在固定设计系统内生成
- 最近 3 个 share task 全失败且无告警 — 产出物审美的前提是产出物能出来 (见 §五)

---

## 四、前端代码审查

- **app.js 3,302 行单文件**: 按研究的迁移判断标准 (≥3 信号才迁移: >3000 行✓ / 深层状态同步✗ / 需要路由✗ / >500 项列表✗ / 需要单测✗), 当前只命中 1 条, **不迁移框架**。但应做 `Alpine.data()` 模块化拆分: articles / fetch-modal / share / config / llm 五个模块, 可再撑 1-2 倍复杂度。js/ 目录下 articles.js/share.js/fetch.js 都是 1 行占位文件 — 拆分的目录结构早就预留了, 一直没做。
- **index.html 2,183 行 + 97 处 modal 引用**: 9 个 view 全部内联在一个文件。模板无法复用, 修一个 modal 要滚屏找。Alpine 没有组件系统, 但 `<template x-if>` + fetch HTML 局部引入可以缓解。P2。
- **错误处理**: 77 处 catch 但落点是 alert/console — 统一错误 toast + 分级 (网络错误可重试 / 4xx 显示 detail)。
- **fetch() 无封装**: 每个调用手写 headers/catch, 应抽一个 `api()` helper (~30 行) 统一错误、loading 状态。

## 五、后端代码审查 + 价值链断点

**价值链数据** (这是本次审计最重要的发现):

| 环节 | 状态 |
|---|---|
| 抓取 | ✅ 健康, 391 篇/最近一周, 10 源 |
| LLM 摘要 | ⚠️ 26/1498 (1.7%) — 手动逐篇触发, 没人用 |
| LLM 打标签 | ✅ cron 有 predict_tags_pending, 在跑 |
| BB 增强 | ❌ bb_resource_id = 0/1498 — v1.0.9.4 修好了 API 但历史数据从未回填, 新抓取似乎也没写入 |
| 事实核查 | ❌ 字段存在, 无使用痕迹 |
| Pin | ⚠️ 10 篇 |
| 分享 | ❌ 最近 3 task 全 failed (`hermes -z: no final response`), 6-25 之后没有成功产出 |

**推论**: 摘要覆盖率 1.7% 说明"逐篇手动点摘要"这个交互模型失败了。研究 (Meridian/Folo) 给出的替代模型是: **抓取后自动批量处理 + 每日跨源简报**, 用户消费的是简报而不是 1498 行表格。这是交互逻辑目标的核心重构方向。

**bb_resource_id 全 0 需要立刻查**: 要么 backfill 脚本 (scripts/backfill_bb_resource_id.sh) 从没跑过, 要么 fetcher 写入路径断了。这是已修 bug (v1.0.9.4) 的效果验证缺失。

**share task 连续失败无告警**: `hermes -z no final response` 从 7-01 持续至今, 没有任何通知机制。稳定性目标的第一课: **失败必须有人知道**。

## 六、API 与安全 (简)

- 75 端点无鉴权, 唯一防线是 127.0.0.1 绑定 + CORS 白名单 — 单机自用可接受, 但 `import-fulltext` 端点能读任意 ~/Documents 下文件, 若未来暴露到局域网就是 LFI。挂一个 `X-Moni-Token` 中间件的成本是 30 分钟, 建议做。
- crud.py 的两步删除 (preview token + confirm) 设计好, 值得保持。
- SQL 全部参数化 ✅。`articles.py:176` 手工拼 JSON 的 GROUP_CONCAT 子查询脆弱 (tag 含引号会产坏 JSON), 建议改 Python 侧组装。

## 七、测试审计 (简)

428 测试对 13k 行代码是好水平。缺口: (1) default_worker.sh 的 bash 逻辑本身无法测 — worker 重写为 Python 后自然解决; (2) 前端零测试 — Alpine 拆模块后核心逻辑 (分页/筛选状态机) 可用 vitest 补; (3) fetcher 网络层用 mock, 没有 contract test 检测上游 feed 结构漂移 — 熔断 + 告警比测试更实际。

## 八、内容来源丰富度 (对照研究)

研究结论 (docs/research-news-aggregation-2025-2026.md) 里对 moni 最有价值的排序:

| 优先 | 渠道 | 理由 | 成本 |
|---|---|---|---|
| P1 | **自托管 RSSHub** | 一次部署解锁数百个中文源路由 (微博/知乎/B站/公众号备选通道), 现有 BaseFetcher 直接消费其 RSS 输出, 零架构改动 | Docker 半天 |
| P1 | **trafilatura 全文层** | 现有源大量 snippet-only (498 篇 <200 字), 全文是摘要质量的前提 | 1 天 |
| P2 | **Newsletter 收件邮箱** | mailgun/cloudflare email worker 转发 → 解析入库, 解锁一等信源 | 1-2 天 |
| P2 | **无 RSS 网页 diff 监控** | 同业官网/监管机构公告页 (对 banking/regulation tag 是刚需), changedetection.io 思路 | 2 天 |
| P3 | Bluesky 接入 | X API 收费后的开放替代 | 1 天 |
| P3 | 播客转写 (faster-whisper) | bestblog-podcast 已有 100 篇, 转写后才可摘要 | 2 天 |

**注意顺序**: 在价值链修复 (§五) 之前加源只会让 1498 变 3000, 摘要覆盖率变 0.8%。先修链路。

## 九、圆桌讨论 (领域专家模拟)

**专家: LF, 做过 Readwise Reader 早期增长、现独立开发信息工具**

**议题 1 — "你的用户每天打开 moni 看什么?"**
LF: 表格是数据库视图, 不是阅读入口。Folo 和 Meridian 都收敛到同一个答案: 早晨打开看到的应该是"今天发生了什么"的合成简报, 5 分钟读完, 想深入再点进原文。moni 有 cron、有 LLM、有 tag 体系, 离每日简报只差一个 job + 一个 prompt。
现状: 打开是 1498 行表格 + 手动筛选。
建议: `daily_digest` cron job — 取近 24h 文章, 按 tag 聚组, hermes 生成跨源简报, 存 DB + 首页顶部卡片展示。这一个功能同时回答精致度、交互逻辑、LLM 闲置三个问题。

**议题 2 — "为什么摘要覆盖率 1.7%?"**
LF: 因为你让用户为每篇文章付一次点击 + 90 秒等待。AI 功能的正确形态是默认发生 (fetch 后自动排队批量摘要), 用户只在结果上做选择。Feedly Leo 从不让用户"点一下生成摘要"。
建议: fetch_all_sources 完成后自动 enqueue 摘要 job (预算控制: 只处理命中 tag 的文章, 每天上限 N 篇)。

**议题 3 — "分享功能连续失败两天没人知道, 说明什么?"**
LF: 说明系统没有心跳。单机自用工具最容易死于"坏了三周才发现"。healthchecks.io + 一条 curl, 或者失败时 Bark/Telegram 推一条, 半天工作量, 是稳定性投入里回报最高的一笔。

**议题 4 — "精致度从哪开始?"**
LF: 不从视觉开始, 从**确定性**开始: console 零 error、行高稳定、每个操作有即时反馈 (toast 而非 alert)、失败可重试。这些做完, 界面自然"感觉精致"。然后才是键盘导航和 design token 收敛。用户感知的精致 = 可预测性 × 响应速度。

**议题 5 — "1.0.10 应该是什么?"**
LF: 一句话版本: "moni 从抓取工具变成简报工具"。每日简报 + 自动摘要 + 失败告警 + 表格视觉修复。信源丰富度放 1.0.11, 那时候新源进来直接进入自动化管线。

## 十、路线图

### P0 — 修复与止血 (本周, ~2 天)
1. 修 10 个 Alpine console error (`fetchAllResult?.` 可选链) — 1h
2. 查 bb_resource_id=0 根因, 跑 backfill, 验证新抓取写入 — 2h
3. 查 share task 连败根因 (`hermes -z` 环境/timeout), 修复 — 2h
4. 失败告警: share/fetch 失败推 IM (Bark/Telegram 一条 curl) — 3h
5. APScheduler 进程挂 launchd (KeepAlive) — 2h
6. pyproject 依赖补全 — 15min

### P1 — 交互逻辑重构 (1-2 周)
7. **每日跨源简报** (cron job + prompt + 首页卡片) — 2 天
8. **抓取后自动批量摘要** (tag 命中 + 每日预算) — 1 天
9. 表格视觉修复: 固定行高 / 标签 +N 折叠 / 状态色收敛 / 操作列图标化 — 1 天
10. toast 系统替换 31 处 alert — 0.5 天
11. 键盘导航 j/k/p// — 0.5 天

### P2 — 稳定性根治 (1 个月)
12. 队列迁 SQLite 单一事实源 + 租约, 删文件队列 — 1.5 天
13. default_worker.sh → `python -m moni.worker` (pydantic-settings + structlog) — 2 天
14. httpx 显式 Timeout + tenacity 重试 + conditional GET (ETag) + per-source 熔断 — 2 天
15. `VACUUM INTO` 每日备份 + /healthz 端点 — 0.5 天
16. app.js 拆 Alpine.data() 模块 (预留的 js/ 文件终于用上) — 2 天

### P3 — 丰富度扩张 (1.0.11+)
17. RSSHub 自托管 + 首批路由接入
18. trafilatura 全文抽取层
19. Newsletter 收件邮箱 / 网页 diff 监控 / Bluesky / 播客转写 (按需)
20. 分享产物 design token 化 (进 format-*.md prompts)
21. 语义去重 (sqlite-vec) → 事件聚类 (远期)

## 附录: 研究文档索引
- docs/research-news-aggregation-2025-2026.md — 标杆产品与信源渠道
- docs/research/uiux-reader-dashboard-best-practices-2026.md — UIUX token 与迁移判断
- FastAPI+SQLite 架构研究 — 全文见本文档 §二/§五 引用 (subagent 输出, 未单独落盘)
