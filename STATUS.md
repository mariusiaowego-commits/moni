# STATUS — moni

> 项目状态与功能清单。每个 Phase 完成时更新日期 + 阶段描述。

---

## 当前状态: **v1.0.2-f.1** (2026-06-05)

**主分支**: main @ 15e8020 (已 push gh, 5 个 tag)
**Web 服务**: `127.0.0.1:8900` (pid 48486 上次)
**数据库**: `data/news.db` — 10 张表, 96 articles
**测试**: 65/65 pass

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

---

## 数据现状 (2026-06-05)

```
articles by source:
  36kr_rss   30   (含 HTML 未清洗)
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
| 抓取流程 | ✅ | 4/5 源跑通, filter 生效 |
| LLM 摘要 | ✅ | 后端 OK, 前端按钮待加 (v1.0.2-F 暂用 curl) |
| LLM 事实核查 | ✅ | 后端 OK, 同上 |
| LLM 设置 web UI | ❌ | v1.1 计划 (T10) |
| 业务过滤 (DB CRUD) | ✅ | web UI + API 全通 |
| 分享 manifest 队列 | ⚠️ | 后端写盘 OK, default profile worker 未实装 (T8) |
| cron 自动跑批 | ⚠️ | scheduler OK, daemon 启动方式待 v1.1 |
| 端到端可视化 | ⚠️ | 浏览器可看 30 36kr + 66 mpaypass, 但 36kr HTML 杂质影响阅读 |

---

## 待办 (按优先级)

### 🔴 高 (影响内容质量)
- [ ] mpaypass deep crawl (详情页 content + published_at)
- [ ] 过滤 mpaypass `/download/` 路径 (10 篇误抓)
- [ ] 修 36kr_newsflash URL (首页是 nav)
- [ ] 修 qbitai URL (品牌页 403)
- [ ] HTML 清洗 (36kr raw_content 是 HTML)

### 🟡 中 (业务体验)
- [ ] 填 .env 完整 WECHAT_RSS_TOKEN (13 字符占位符)
- [ ] LLM 设置 web UI (PRD §4.7 第 6 菜单)
- [ ] 回溯过滤老 30 篇 36kr
- [ ] 摘要/事实核查前端按钮

### 🟢 低 (清理)
- [ ] 删 `wt/*` 目录 (30+ MB, 已 .gitignore)
- [ ] 修 `moni/share/formats.py:198` datetime deprecation
- [ ] share manifest default profile worker (T8)
- [ ] cron daemon 启动 (现手动跑)

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
| v1.0.2-f.1 | 15e8020 | v1.0.2-F 按钮修复 (Alpine scope) |
| v1.0.2-f | 10435ce | v1.0.2-F 配置管理 UI (3 tabs) |
| v1.0.2-c | f4a7084 | v1.0.2-C 8 config CRUD API |
| v1.0.2-b | 4ee2de6 | v1.0.2-B 关键词过滤串入抓取 |
| v1.0.2-a | d39bc85 | v1.0.2-A 业务过滤 schema + seed |

---
