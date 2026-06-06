# STATUS — moni

> 项目状态与功能清单。每个 Phase 完成时更新日期 + 阶段描述。

---

## 当前状态: **v1.0.3-cron** (2026-06-06)

**主分支**: main @ 0a71c88 (v1.0.3-cron, 已 push gh, 7 个 tag)
**Web 服务**: `127.0.0.1:8900`
**数据库**: `data/news.db` — 10 张表, 96 articles (10 篇 36kr HTML 已被 retroactively 清洗, with_html=0)
**测试**: 149 pass (pre-existing 2 failures 来自 36kr URL P0-C, 与本次改动无关)
**Kanban**: t_c2db711a (T7 cron + manifest) 完成

**2026-06-06 进展**:
- 41 个 kanban 任务逐个 review: 18 个 ghost/verify 已 complete/archive, 6 个 PRD 过时已 archive, 1 个 CLI race 改用 archive
- 新建 3 个 P0 任务: mpaypass deep / HTML 清洗 / 修 URL
- T10 范围重拆: 薄壳模式下不做 Factory 重写, 改为"LLM 设置"web UI 只显示当前 provider
- ✅ **t_5d661a0c 完成**: tests/test_share_manifest.py — 60 pytest 覆盖 share manifest 端到端 (5 类别)
- ✅ **t_d9441040 完成**: moni/fetch/cleaner.py (197 行) + base.py 串入 + repository.clean_all_html() + `moni clean-articles` CLI; 26 单元测试全过; 10 篇 36kr 历史 HTML → 纯文本
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

---

## 数据现状 (2026-06-06)

```
articles by source:
  36kr_rss   30   (raw_content 已 retroactively 清洗, 无 HTML 残留)
  mpaypass   66   (含 10/66 /download/ 误抓)
  qbitai      0   (URL 首页问题, 未抓通)
  wechat_rss  0   (token 是 13 字符占位符)
  36kr_newsflash 0 (URL 首页是 nav, 未抓通)
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
- [ ] mpaypass deep crawl (详情页 content + published_at)
- [ ] 过滤 mpaypass `/download/` 路径 (10 篇误抓)
- [ ] 修 36kr_newsflash URL (首页是 nav)
- [ ] 修 qbitai URL (品牌页 403)

### 🟡 中 (业务体验)
- [ ] 填 .env 完整 WECHAT_RSS_TOKEN (13 字符占位符)
- [ ] LLM 设置 web UI (PRD §4.7 第 6 菜单)
- [ ] 摘要/事实核查前端按钮

### 🟢 低 (清理)
- [ ] 删 `wt/*` 目录 (30+ MB, 已 .gitignore)
- [ ] 修 `moni/share/formats.py:198` datetime deprecation
- [ ] share manifest default profile worker — **已实装 (T7, 0a71c88), T8 在其基础上跑 hermes 集成**
- [ ] cron daemon 启动 — **已实装 (T7, install_cron.sh), T8 在其基础上跑端到端**

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
| v1.0.3-html | f1b9d47 | v1.0.3 HTML 清洗 (cleaner.py + base.py 串入 + retroactive CLI) |
| v1.0.3-cron | 0a71c88 | v1.0.3 cron + manifest — scripts/default_worker.sh + install_cron.sh + docs/CRON.md + sample manifest (T7) |
| v1.0.2-f.1 | 15e8020 | v1.0.2-F 按钮修复 (Alpine scope) |
| v1.0.2-f | 10435ce | v1.0.2-F 配置管理 UI (3 tabs) |
| v1.0.2-c | f4a7084 | v1.0.2-C 8 config CRUD API |
| v1.0.2-b | 4ee2de6 | v1.0.2-B 关键词过滤串入抓取 |
| v1.0.2-a | d39bc85 | v1.0.2-A 业务过滤 schema + seed |

---
