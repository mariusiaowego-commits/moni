# STATUS — moni

> 项目状态与功能清单。每个 Phase 完成时更新日期 + 阶段描述。

---

## 当前状态: **v1.0.5.7 — publisher 字段抽取** (2026-06-07)

**主分支**: main @ db366ea (v1.0.5.7 tag @ db366ea, 已 push gh, 16 个 tag)
**Web 服务**: `127.0.0.1:8900` (200 OK)
**数据库**: `data/news.db` — 10 张表, 113 articles (mpaypass 56 + 36kr_rss 35 + qbitai 12 + wechat_rss 4 + wechat_rss_a 4 + wechat_rss_b 2 + 其他 legacy)
**测试**: 244/244 pytest pass (2 skip)
**Worktree**: 1 (`.worktrees/wt-v1.0.5.7-publisher`)

**2026-06-07 v1.0.5.7 进展** (publisher 字段抽取 — 修 v1.0.5.6 P0):
- **问题**: waytomaster RSS 没 dc:creator / author, 历史 wechat 文章 publisher 全空
- **方案**: title 前缀 `[公众号名] 文章标题` 抽公众号名作 publisher fallback
- **改动**:
  - `moni/fetch/sources/wechat_rss.py`: 加 `_extract_publisher_from_title()` helper, regex 支持 ASCII / full-width brackets, 可选分隔符, 1-50 字符 sanity bound
  - author fallback 链: `dc:creator → author → title prefix` (现有 dc:creator/author 源不受影响)
  - `moni/cli/main.py`: 加 `backfill-publisher` subcommand (idempotent 回填)
  - `tests/test_publisher_extraction.py`: 17 新单测 (6 happy / 6 reject / 5 生产真实 title)
- **回填结果**: 4 篇 legacy wechat_rss publisher = `未央科技weiyangx`; 1 篇新 wechat_rss_b publisher = `财金吃瓜党` 自动抽出
- **新增 tag v1.0.5.7** (db366ea)

**2026-06-07 v1.0.5.6 进展** (wechat_rss 多账号支持):
- **动机**: waytomaster 免费账户上限 2 公众号, 多个 token 可以追踪 4+ 公众号
- **改动**:
  - `moni/fetch/sources/wechat_rss.py`: 单 `FetcherWechatRSS` 类通过动态 subclass 注册支持 5 个 variant (wechat_rss_a..e); `_resolve_url()` 优先级: DB sources.url (含 token) > env > .env
  - `moni/db/seed_config.py`: 加 wechat_rss_a / wechat_rss_b 两条 source, 旧 wechat_rss 保留
  - `config/sources.yaml`: 同步 2 条新源
  - `tests/test_wechat_multi.py`: 10 个新单测 (alias 注册, _resolve_url 优先级, parse 戳 source_name, idempotency)
- **架构亮点**: token 不再走 env var, 直接嵌在 source.url — 前端配置管理可见; 单一代码路径, 通过 per-instance source_name 区分
- **新加 source 是简单前端操作**: 配置管理 → 抓取源 → + 新建 source (type=wechat_aggregator, url 含 token)
- **验证**: wechat_rss_a 4 篇 + wechat_rss_b 1 篇入库, 227 pytest pass + 2 skip
- **新增 tag v1.0.5.6** (5b86616)

**2026-06-07 v1.0.5.5 进展** (36kr captcha bypass 落地):
- **核心**: 36kr fetcher 改走真 Chrome (Profile 7 + port 9223) → browser-harness subprocess 读 `body.innerText` → 避开 TTGCaptcha fingerprint 检测
- **新模块**:
  - `moni/fetch/chrome_daemon.py` — Chrome daemon 生命周期管理 (port 9223 隔离 x-watch 9222, Profile 7 隔离 x-watch Profile 6)
  - `moni/fetch/browser_harness.py` — `js()` 包装读 body.innerText (注意: `document` 在 browser-harness REPL 里不是 Python builtin, 必须用 `js("...")`)
  - `scripts/manual_captcha.py` — headed 模式一次性过 captcha 脚本, cookie 落 Profile 7
- **fetcher 改动**: kr_rss + kr_newsflash 走 browser-first 路径, 失败 fallback httpx
- **36kr_newsflash 标 disabled** — `/newsflashes` 是 React 客户端渲染, body.innerText 只能看到 nav/footer, 拿不到 flash items; 36kr_rss 已覆盖内容需求
- **测试**: 15 新单测 (captcha detection, parser paths, env override, body extraction); 2 集成测试标 skipif (需要 MONI_36KR_INTEGRATION=1 + Profile 7 cookie)
- **Hermes sandbox 坑修**: `chrome_daemon.CHROME_USER_DATA_DIR` 不用 `Path.home()` (会被 sandbox HOME 重定向), 硬编码 `/Users/mt16/Library/Application Support/Google/Chrome/Profile 7`
- **验证**: 36kr_rss 0 → 23 articles (一次 fetch); pytest 217 pass + 2 skip; 全 5 端点 200 OK
- **新增 tag v1.0.5.5** (3d18821)
- **Cookie 失效后重抓**: 跑 `MONI_36KR_HEADED=1 python3 scripts/manual_captcha.py` 重过 captcha 一次

**2026-06-06 v1.0.5.4 + 36kr wip 进展** (昨晚用户睡了, 今晚继续):
- **v1.0.5.4** tag-keyword 过滤 (54e80ae) — 详见下方
- **wip(36kr) 7b6fe86** — cookie loader 持久化 + fetcher 注入 cookie + User-Agent, 落了一半, cookie bypass 实际不生效 (TTGCaptcha 检 client fingerprint)

**2026-06-06 v1.0.5.4 进展** (tag-keyword 过滤):
- 详见下方 Phase 进度 (v1.0.5.4)

**2026-06-06 v1.0.5.2 进展** (LLM 异步化 + 进度面板):
- **3 个 bug 统一根因**: 摘要/事实核查之前是同步调用, 60s timeout 直接报错给用户, 无进度反馈
- **后端**:
  - 005 migration: `llm_jobs` 表 (id/task_type/article_id/provider/model/status/result/error/attempts/duration_ms/created_at/started_at/finished_at)
  - `moni/llm/jobs.py`: 一次性 daemon thread, 2s 轮询 pending, 调 LLM, 写 status/result/error
  - `MAX_ATTEMPTS=2` (1 次重试); 修中途 user cancel: 检查 status 避免覆盖 cancelled
  - `_TIMEOUT_SECONDS` 60 → 300 (MiniMax-M3 thinking 慢)
  - `/api/articles/{id}/summary` `/fact-check` 改 202, 立即返 job_id + poll_url
  - `/api/llm/jobs` 端点 (list/stats/{id}/cancel)
- **前端**:
  - 新菜单 "LLM 任务" (第 6 菜单, 抓取管理后, 分享前)
  - 任务表格 + 状态徽章 (pending/running/done/failed/cancelled) + 自动轮询 (有任务 1s, 无任务 5s)
  - 摘要/事实核查按钮点击后跳转到 LLM 任务面板 (替代之前的同步等待)
  - nav-chip 显示 in-flight 数量; running 行高亮 + 脉冲动画
- **Bug fix**: fact_check writeback SQL 占位符数量错 (3 个 ? 但传 4 个 params) → 修复
- **新增 tag v1.0.5.2** (ea238b2)

**2026-06-06 v1.0.5.1 进展** (3 个 UX bug 修复):
- **Bug 1 文章无分类** — `ArticleRepository.dedupe_insert()` 入库时不复制 `source_tags` -> `article_tags`, DB 里 article_tags 0 条
  - 修法: 加 `_copy_source_tags_to_article()`, fetcher 入库时自动调
  - 加 `backfill_article_tags_from_source_tags()` 一次性回填 (供 moni cli)
  - 验证: mpaypass 56 篇 -> 56*3=168 article_tags (banking+payment+regulation), qbitai 10 篇 -> 10*1=10 (ai-fintech)
- **Bug 2 连通性测试 405** — 前端 `pingSource()` 调 `/api/fetch/sources/{id}/ping`, 后端只有 `/health` 路由
  - 修法: 后端加 `/ping` alias 路由 (同 handler)
- **Bug 3 立即抓取 404** — 前端传 DB 风格 id (`src-36kr-rss`), 后端 yaml 找 `source_name` (`36kr_rss`) 找不到
  - 修法: 加 `_resolve_source_name()`, 同时认 YAML name 和 DB id (通过 sources.name 表 mapping)
- **新增 tag v1.0.5.1** (4574e68)

**2026-06-06 v1.0.5 进展** (P0 + P1 全做完):
- **P0-1 修 60 pytest fail** → 197 pass (后随 captcha cooldown 解除 → 199 pass)
  - `crud.py:_row_to_article()` 漏改 `source` 字段 (1 行 patch)
  - `test_mpaypass_crawler.py` 删硬编码 worktree path (23 test 全过)
  - `test_cron.py` autouse fixture 加 teardown 恢复 sys.modules (snapshot+restore)
- **P0-2 文档同步** — STATUS.md / HANDOFF.md / AGENTS.md / vibe-coding-log.md 全部更新
- **P0-3 删 13 worktree** — 释放 ~75 MB
- **P1 mpaypass deep crawl 修好** — 2 个 bug 修复, 56 篇入库
  - `_NEWS_PATH_RE.match()` 锚点问题 (live site 用绝对 URL) → 改 `.search()` 去掉 `^`
  - `_collect_results` gate bug: --source 模式不该被 scheduler gate 拦 → 加 `force_targeted` 参数
- **新增 tag v1.0.5** (fa78f55)

**2026-06-06 v1.0.4-eval 进展** (昨天, 已 commit 3e25f7a):
- 41 个 kanban 任务逐个 review: 18 个 ghost/verify 已 complete/archive, 6 个 PRD 过时已 archive
- 9 个 v1.0.4 frontend bug 修复 (merge commit 7556795)
- 5 源端到端生产验证 PASS (commit 3e25f7a): 5/5 源真抓, 关键词 filter 真 drop 7 篇
- wechat_rss 加 .env fallback (单用户本地友好)
- T10 范围重拆: 薄壳模式下不做 Factory 重写, 改为"LLM 设置"web UI 只显示当前 provider
- ✅ **t_c2db711a 完成 (T7)**: scripts/default_worker.sh (348 行) + scripts/install_cron.sh (81 行) + docs/CRON.md; 5min cron 注册; sample manifest `~/.moni/queue/test-001.json` (id=test-task-001, format=markdown) + share_tasks pending row + test-article-001 文章已预置; pytest 2 个 pre-existing 失败 (test_fetch_integration.py, 36kr URL P0-C 已知)

---

## Phase 进度

### ✅ v1.0 (基础架构, 5b10b00)

- ✅ 5 菜单 FastAPI SPA (文章查阅 / CRUD / 跑批日志 / 抓取管理 / 分享)
- ✅ 5 抓取源 (config/sources.yaml)
- ✅ 11 分享格式
- ✅ 文章 CRUD + LLM 摘要/核查 (5b10b00 stub)
- ✅ 8 share manifest 端点
- ✅ cron scheduler (APScheduler)
- ✅ 65/65 tests pass

### ✅ v1.0.1 (5 源抓取修复, dafb05a)

- ✅ mpaypass URL 修复 (加 www.) — 66 篇入库
- ✅ wechat_rss URL 改聚合端点
- ✅ web.sh 自动 source .env
- ✅ web/routes/llm.py prefix 修复 (`/api/llm/*`)
- ✅ summary/fact-check stub → 真调 call_llm
- ✅ 65/65 tests pass

### ✅ v1.0.2-A (业务过滤 schema, d39bc85)

- ✅ 3 张新表: `sources` / `source_tags` / `source_keyword_filters`
- ✅ 5 个 tag_definitions (banking / payment / ai-fintech / regulation / general-tech)
- ✅ 5 个 sources + 6 source_tags + 3 filter seed
- ✅ CLI 集成 (`moni db init` / `seed-config`)
- ✅ 65/65 tests pass

### ✅ v1.0.2-B (过滤串入抓取, 4ee2de6)

- ✅ `moni/fetch/filters.py` (168 行) — blacklist/whitelist
- ✅ `moni/fetch/base.py` run() 串入 filter 步骤
- ✅ 验证: 36kr 30→24 (drop 6 业务无关)
- ✅ 65/65 tests pass

### ✅ v1.0.2-C (8 config CRUD API, f4a7084)

- ✅ 8 Pydantic model + 3 route 文件 + 8 API 端点
- ✅ CASCADE 验证
- ✅ 65/65 tests pass

### ✅ v1.0.2-F (配置管理 UI, 10435ce)

- ✅ 改 pyproject.toml (exclude wt*)
- ✅ 修 editable install bug (重装主 checkout)
- ✅ 3 tabs CRUD UI (抓取源 / 业务 tag / 关键词过滤)
- ✅ 4 modal forms (tag / source / sourceTags / filter)
- ✅ 65/65 tests pass

### ✅ v1.0.2-f.1 (按钮修复, 15e8020)

- ✅ config CRUD methods inline 到 app() scope (Alpine.js)
- ✅ 删 config.js (-218 行)
- ✅ 修 openConfigModal 对 source/filter 也设 configModalTargetOriginal
- ✅ 65/65 tests pass

### ✅ v1.0.3-html (HTML 清洗, f1b9d47)
- ✅ `moni/fetch/cleaner.py` (197 行) — `extract_text(raw_html) -> str`
- ✅ BeautifulSoup + lxml 解析; 丢 `<script>/<style>/<nav>/<aside>/<footer>/<header>/<iframe>` 整子树
- ✅ 保留 `<p>/<h1-6>/<li>/<div>/<blockquote>` 作为段落; 段内空白折叠为单空格
- ✅ `moni/fetch/base.py` run() 串入 cleaner (跑在 keyword filter 之前); 返回值加 `cleaned` 字段
- ✅ `ArticleRepository.clean_all_html()` 一次性 retroactively 清洗历史文章 (LIKE '%<%' 过滤, 幂等)
- ✅ 新 CLI `moni clean-articles` (idempotent, 二次跑=0/0/0)
- ✅ 26 个新单测 (extract_text 24 个 + repository 集成 2 个) — 全过
- ✅ 抽样: 一篇 1587 字符 36kr HTML → 1313 字符纯文本 (10 篇全部清洗, with_html 10→0)

### ✅ v1.0.3-cron (default worker + cron installer, 0a71c88)
- ✅ `scripts/default_worker.sh` (348 行) — 扫 `~/.moni/queue/*.json`, 处理 pending share_tasks via `hermes -z <prompt> --profile default`; PID-file 锁防并发; 5min per-task timeout
- ✅ `scripts/install_cron.sh` (81 行) — install / uninstall / status / show, 5min crontab 条目
- ✅ `docs/CRON.md` (217 行) — 完整 manual testing 步骤 (1 个成功路径 + 1 个失败路径) + format→skill 映射 + tuning env vars
- ✅ Sample manifest `~/.moni/queue/test-001.json` + share_tasks `test-task-001` row + `test-article-001` 文章已预置
- ✅ worker 端到端验证: 跑一次后 status=running, started_at 已写入 (脚本逻辑正确; 完整 LLM 调用需要 T8 hermes 端)
- ⚠️ 149 tests pass, 2 pre-existing failures (test_fetch_integration.py — 36kr URL P0-C 已知, 与本次改动无关)

### ✅ v1.0.5.5 (36kr captcha bypass, 3d18821)
- ✅ `moni/fetch/chrome_daemon.py` — Profile 7 / port 9223 隔离 x-watch (Profile 6 / 9222); 不依赖 `Path.home()` (hermes sandbox 会重定向)
- ✅ `moni/fetch/browser_harness.py` — subprocess 调 `browser-harness -c`, 用 `js("document.body.innerText")` (不是裸 `document`)
- ✅ `scripts/manual_captcha.py` — headed 模式过 captcha 一次性脚本, `MONI_36KR_HEADED=1` 触发
- ✅ `kr_rss.py` / `kr_newsflash.py` 改走 browser-first 路径, 失败 fallback httpx (逻辑同 v1.0.5.4 cookie_loader)
- ✅ 36kr_newsflash 标 `enabled: false` (React 渲染, body.innerText 拿不到内容)
- ✅ 15 新单测 (`tests/test_36kr_browser.py`); 2 集成测试标 `skipif` 条件 (需 `MONI_36KR_INTEGRATION=1`)
- ✅ 217/217 pytest pass + 2 skip
- ✅ 36kr_rss 0 → 23 articles 入库 (一次 fetch)

### ✅ v1.0.5.6 (wechat_rss multi-account, 5b86616)
- ✅ `FetcherWechatRSS` 单类 + 动态 subclass 注册支持 5 variant (wechat_rss_a..e)
- ✅ `_resolve_url()` 优先级: DB sources.url (含 token) > env > .env
- ✅ `parse()` 强制戳 `self.source_name` (dedupe 按 (source, url) 联合唯一)
- ✅ 10 新单测 (`tests/test_wechat_multi.py`): alias 注册 / URL 解析优先级 / parse source_name / idempotency
- ✅ 227/227 pytest pass + 2 skip
- ✅ wechat_rss_a 4 + wechat_rss_b 1 articles 入库 (token 1+2 验证)
- ✅ DB sources 表加 2 行 (legacy wechat_rss 保留)

### ✅ v1.0.5.7 (publisher 字段抽取, db366ea)
- ✅ `_extract_publisher_from_title()` regex: ASCII/全角 brackets + 可选分隔符 + 1-50 字符 sanity bound
- ✅ author fallback 链: dc:creator → author → title prefix (现有源不受影响)
- ✅ `moni backfill-publisher` CLI: 一次性回填历史, idempotent
- ✅ 17 新单测 (`tests/test_publisher_extraction.py`): 6 happy / 6 reject / 5 生产真实 title
- ✅ 244/244 pytest pass + 2 skip
- ✅ 4 篇 legacy wechat_rss publisher 回填 → `未央科技weiyangx`
- ✅ 1 篇新 wechat_rss_b publisher 实时抽 → `财金吃瓜党`

---

## 数据现状 (2026-06-07 v1.0.5.6)

```
articles by source:
  mpaypass        56  (v1.0.5 修好 deep crawl, fetch URL 锚点 + CLI gate)
  36kr_rss        35  (v1.0.5.5 captcha bypass 落地, 真 Chrome + body.innerText; v1.0.5.6 累加)
  wechat_rss       4  (v1.0.4-eval legacy, env var 路径)
  wechat_rss_a     4  (v1.0.5.6 token 1, [未央科技weiyangx] 4 篇)
  wechat_rss_b     1  (v1.0.5.6 token 2, 1 篇 dedupe 后)
  qbitai          12  (v1.0.3 改 /feed RSS, 稳定抓; v1.0.5.6 累加 2)
  36kr_newsflash   0  (v1.0.5.5 标 disabled, React 渲染待 v1.0.6 重做)
  TOTAL          112
```

---

## 功能完成度

| 功能 | 状态 | 备注 |
|---|---|---|
| 5 菜单 SPA | ✅ | 6 菜单 (含配置管理) |
| 抓取流程 | ✅ | 4/5 源跑通, filter 生效, HTML 清洗 OK |
| LLM 摘要 | ✅ | 后端 OK, 前端按钮待加 (v1.0.2-F 暂用 curl) |
| LLM 事实核查 | ✅ | 后端 OK, 同上 |
| LLM 设置 web UI | ❌ | v1.1 计划 (T10) |
| 业务过滤 (DB CRUD) | ✅ | web UI + API 全通 |
| 分享 manifest 队列 | ✅ | 后端写盘 + default profile worker (T7) — pytest 60/60 |
| cron 自动跑批 | ✅ | install_cron.sh 注册 5min crontab (T7), daemon 文档见 docs/CRON.md |
| 端到端可视化 | ✅ | 浏览器可看 30 36kr (clean) + 66 mpaypass |

---

## 待办 (按优先级)

### 🔴 高 (影响内容质量)
- [x] mpaypass deep crawl (详情页 content + published_at) — v1.0.5 完成 (56 篇入库)
- [x] 过滤 mpaypass `/download/` 路径 (10 篇误抓) — v1.0.5 完成 (regex 只匹 /news/)
- [x] 修 36kr_newsflash URL (首页是 nav) — f46c501 已 ff 进 main
- [x] 修 qbitai URL (品牌页 403) — f46c501 已 ff 进 main

### 🟡 中 (业务体验)
- [ ] 填 .env 完整 WECHAT_RSS_TOKEN — v1.0.4-eval 已完成 (4 篇入库)
- [ ] LLM 设置 web UI (PRD §4.7 第 6 菜单) — T10 重拆后
- [ ] 摘要/事实核查前端按钮

### 🟢 低 (清理)
- [x] 删 `wt/*` 目录 (30+ MB, 已 .gitignore) — v1.0.5 完成 (13 worktree 删)
- [ ] 修 `moni/share/formats.py:198` datetime deprecation
- [ ] share manifest default profile worker — **T7 已实装 (0a71c88), T8 commit 90b1912 (22 test) 未 ff, 用户决定**
- [ ] cron daemon 启动 — **T7 install_cron.sh 已实装**
- [ ] T8 commit 90b1912 / af4cefb ff 进 main — 用户决定

---

## 文档清单

- **README.md** — 入口 (简)
- **USER_GUIDE.md** — 用户使用文档 (12 节)
- **HANDOFF.md** *(本地, 不上 GH)* — 接手 agent 看的代码层细节
- **AGENTS.md** *(本地, 不上 GH)* — 项目惯例 + 收尾 workflow
- **vibe-coding-log.md** *(本地, 不上 GH)* — Session 历史
- **STATUS.md** — 本文件, 功能 + 阶段进度
- **PRD** (Obsidian) — 完整需求定义

---

## Git Tags

| Tag | Commit | 描述 |
|---|---|---|
| v1.0.5 | fa78f55 | v1.0.5 cleanup + mpaypass 修好 — 199 pytest pass, 5 源 100% 抓通 (150 articles), 13 老 worktree 删 |
| v1.0.4-eval | 3e25f7a | v1.0.4 5 源端到端生产验证 PASS (filter drop 7) + wechat_rss .env fallback |
| v1.0.4 | 7556795 | v1.0.4 frontend bug fixes (9 个 bug 修复) |
| v1.0.3-html | f1b9d47 | v1.0.3 HTML 清洗 (cleaner.py + base.py 串入 + retroactive CLI) |
| v1.0.3-cron | 0a71c88 | v1.0.3 cron + manifest — scripts/default_worker.sh + install_cron.sh + docs/CRON.md + sample manifest (T7) |
| v1.0.5.5 | 3d18821 | 36kr captcha bypass — Chrome Profile 7 + body.innerText 抓 RSS, 23 articles 入库, 217 pytest pass |
| v1.0.5.6 | 5b86616 | wechat_rss multi-account — wechat_rss_a/b/c/d/e 5 variant, token 嵌 source.url, 4+1 articles, 227 pytest pass |
| v1.0.5.7 | db366ea | publisher 抽取 — title prefix [公众号名] regex fallback, + backfill-publisher CLI, 17 tests, 244 pytest pass |
| v1.0.5.4 | 54e80ae | tag-keyword 过滤 (tag_definitions.keywords JSON 数组, OR substring 匹配) |
| v1.0.5.3 | 70ef4f5 | LLM 任务结果折叠 (查看全文/收起 按钮) |
| v1.0.2-f.1 | 15e8020 | v1.0.2-F 按钮修复 (Alpine scope) |
| v1.0.2-f | 10435ce | v1.0.2-F 配置管理 UI (3 tabs) |
| v1.0.2-c | f4a7084 | v1.0.2-C 8 config CRUD API |
| v1.0.2-b | 4ee2de6 | v1.0.2-B 关键词过滤串入抓取 |
| v1.0.2-a | d39bc85 | v1.0.2-A 业务过滤 schema + seed |
| v1.0.1 | dafb05a | v1.0.1 4 源抓取修复 + 摘要按钮跑通 |
| v1.0.2-final | 1264c92 | v1.0.2 收尾 (3 tabs CRUD UI + alpine fix) |

---
