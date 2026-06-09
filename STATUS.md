# STATUS — moni

> 项目状态与功能清单。每个 Phase 完成时更新日期 + 阶段描述。

---

## 当前状态: **v1.0.5.14 — 6 用户问题 + 4 follow-on 全修** (2026-06-09)

**主分支**: main @ `a483588` (v1.0.5.13 tag, 25 个 tag) — **v1.0.5.14 在 PR #6 待 merge, feature/v1.0.5.14-six-fixes @ `05ca5bb`**
**Web 服务**: `127.0.0.1:8900` (200 OK, 跑 v1.0.5.14 + 手动改的 C.4/C.5/C.6 修复, PID 18738)
**数据库**: `data/news.db` — schema_version=7 (migration 007 加 `tag_definitions.display_name`), 200+ articles
**测试**: 246 pytest pass + 2 skip (新 +13 测试覆盖 display_name + pagination)
**Worktree**: 0 (v1.0.5.10+ 改用 feature branch on main checkout, 不用 wt)

**2026-06-09 v1.0.5.14 进展** (6 用户问题 + 4 follow-on 全修, 14 文件, +781/-123):
- **用户原报 (6 个) — 全部修好**:
  1. LLM 任务结果单列展示太长 → modal 弹窗 (modal-content-wide) + 复制按钮 + Escape 关闭
  2. 业务 tag 名称不能改 → `display_name` 模式 (跟 sources.display_name 同模式; `name` 是 PK 不可改, `display_name` 可改中文)
  3. wechat_rss_a 抓后查不到 → articles API 加分页 (limit/offset) + COALESCE 排序 + source filter
  4. 分享页找不到刚抓的 wechat → 分享页加 source filter + 分页
  5. 量子位 (qbitai) 抓后看不到 → 同 #3, 加 source filter
  6. 分享页加入队列 HTTP 400 → shareFormat 11 个 value 对齐后端 FormatEnum + html_landing/ppt_slides 加 title 字段
- **Follow-on fixes (4 个, 用户验证时发现)**:
  - A: ShareTask Pydantic model 跟 DB schema 不齐 (返 `article_id=0/token=id/view_count=0` 假字段) → 改回跟 DB 一致
  - B: 前端 `loadShareQueue` 读 `data.tasks`, 后端返 `data.items` → 三种 shape 都接受
  - C.1: worker `article_ids` 嵌套读 (`payload.article_ids` 不是顶层) → 修
  - C.2: **BSD grep bug** — `[^ \n]+` 把路径在 `n` 处截断 → 改用 POSIX char class `[[:alnum:]_./-]+`
  - D: .gitignore 加 LLM 输出文件 (infographic/ / *.html / *.svg / *.png / share_output/), 防止 worker 落到 repo 根污染
- **真跑通的 worker E2E**: 3 task status=done, output_path 完整写入 (html_landing × 2, info_graphic_png × 1)
- **PR**: #6 (`feature/v1.0.5.14-six-fixes`, 3 commits, OPEN, 待 user review → squash merge → force-push tag v1.0.5.14)
- **教训**:
  1. BSD grep `[^ \n]+` 路径截断 — 永远用 POSIX char class
  2. share.py v1.0.5.11 重构忘了同步 frontend ShareTask model → 后端改 model 时必须 grep 前端
  3. `git checkout -- index.html` 会静默还原之前 patch — 改大文件前先 `git stash` 或精确 anchor
  4. **PR 推送后必须 post-commit self-verify** (rule #29): pytest+e2e 不够, 真浏览器点才算
  5. `.replace_all=True` 在 HTML 是灾难 — 一次 18 处替换全坏, 靠 `git checkout --` 救回. 永远精确 anchor
  6. **修前端 + 后端时, Pydantic 强契约 优先** (用户偏好: 前端 form 加必填字段 > 后端 fallback)
  7. 下一版 ≤ 3 个 UI bug 一次 (rule #20 锁定)

**2026-06-09 v1.0.5.13 进展** (datetime.utcnow() deprecation 修):
- **问题**: `moni/share/formats.py:198` `ManifestPayload.created_at` default_factory 调 `datetime.utcnow()`, Python 3.12+ 跑出 55 warnings/run
- **修法**: `datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')` (保留 RFC 3339 'Z' 后缀, 字符串格式不变)
- **改动**:
  - `moni/share/formats.py`: +`timezone` import + 1 行 default_factory 改
  - `tests/test_share_manifest.py`: +`import warnings` + 新测试 `test_created_at_is_timezone_aware_utc_iso8601` (assert 无 DeprecationWarning + ts 以 'Z' 结尾 + parse 出 tzinfo=UTC)
- **验证**: 246 pytest pass + 2 skip (从 245 → 246, +1 新测试); `python3 -W error::DeprecationWarning` 不再炸; 0 warnings/run
- **PR**: #4 squash merged → main @ `04a21b5`
- **新加 tag v1.0.5.13** (`04a21b5`)

**2026-06-08 v1.0.5.10 + v1.0.5.11 + v1.0.5.12 进展** (3 联版 UI overhaul):
- **背景**: 6 个前端 UI bug (Obsidian docs/前端问题.md), 加 dev/prod 端口分离 (8900/8901), 加 PR workflow = feature branch (不再 worktree)
- **v1.0.5.10** (`55511ed` 后端 + `b744623` 前端 + `55511ed` dev 脚本): articles filter (source/tag/keyword) + sidebar 独立 URL (pushState/popstate) + tag chip tooltip + POST /api/articles (修 ArticleCreate.source_id int→str schema drift) + `scripts/web-8901.sh` 120 行
- **v1.0.5.11** (`1617095`): CRUD modal (create/edit/delete) + share card grid (11 格式变 1 卡 1 文); 修 #2 + #5
- **v1.0.5.12** (`9a3a6d1`): 6 UI bug fix (share POST 405 path + body schema drift + 查看按钮 wrap + 搜索框 UX + tag 颜色渲染 4 层流水线 + config 选色器从 uiux-asset-library 抽调色板 + name 字段 :disabled 陷阱)
- **PR**: #2 (v1.0.5.11) + #3 (v1.0.5.12) + #4 (v1.0.5.13) 都走 user review + squash merge
- **教训**: 6 bug 一次提 + 每步 e2e + vision + pytest 太重, "为什么会卡住"反馈 → **之后 ≤ 3 个 UI bug 一次** (锁定在 moni-development skill rule #20)

**2026-06-07 v1.0.5.9 进展** (legacy wechat_rss 退役):
- **问题**: legacy `wechat_rss` 源 is_active=1 但实际不抓 (DB url 无 token, env var fallback 需要 WECHAT_RSS_TOKEN, 用户不再设)
- **用户决策**: 删 legacy `wechat_rss` 源, 保留 FetcherWechatRSS 类 (向后兼容 `moni fetch --source wechat_rss` CLI)
- **改动**:
  - `moni/db/seed_config.py`: 加 `wechat_rss` 进 _RETIRED_SOURCES (v1.0.5.8 模式扩展), 跑 `moni db init` 自动 DELETE 旧行
  - `config/schedule.yaml`: 删 `fetch_wechat_rss` cron job
  - `moni/db/schema.sql` + `004_sources_and_tags.sql`: 注释更新 (legacy 退役)
  - `tests/test_wechat_multi.py` + `verify_fetchers.py`: 注释更新
  - 修 seed docstring "4 sources" → 5 sources 准确数
- **保留**: FetcherWechatRSS class + @register_fetcher (向后兼容 legacy CLI 调用)
- **历史数据**: 4 篇 `source='wechat_rss'` 文章保留 (DB articles 表不删, 只删 sources 表 catalog 行)
- **新增 tag v1.0.5.9** (2eebbfd)
- **5 active 源全抓** ✅: 36kr_rss (30 fetched/1 inserted) + mpaypass (56/0) + qbitai (10/0) + wechat_rss_a (4/0) + wechat_rss_b (1/1)

**2026-06-07 v1.0.5.8 进展** (36kr_newsflash 删源 — 用户决策):
- **背景**: 36kr_newsflash React 客户端渲染 + captcha 限制, v1.0.5.5 captcha bypass 后 RSS 通了, newsflash 仍抓不到
- **用户决策**: 36kr_newsflash **彻底删除** (不是 disabled), 36kr_rss 已覆盖 36kr 全部内容
- **改动**:
  - 删 `moni/fetch/sources/kr_newsflash.py`
  - 删 `moni/fetch/sources/__init__.py` import 行
  - `moni/db/seed_config.py`: 删 source + source_tag + filter
  - `config/sources.yaml`: 删 source 行
  - `config/schedule.yaml`: 删 cron job
  - `scripts/manual_captcha.py`: 删 /newsflashes URL
  - `verify_fetchers.py`: 删 import + 列表项
  - `tests/test_url_fix.py`: 删 Test36krNewsflashParser + TestFetcherHeaders.test_36kr_newsflash_uses_user_agent
  - `tests/test_36kr_browser.py`: 删 TestKRNewsflashParser + SAMPLE_NEWSFLASH_HTML
  - `moni/db/migrations/004_sources_and_tags.sql`: 注释更新
  - `moni/fetch/run.py`: CLI help 文本更新
  - `docs/BROWSER_FETCHER_PATTERN.md`: 加 v1.0.5.8 outcome 注释
- **保留**: TestFetcherHeaders.test_qbitai_uses_user_agent (qbitai 测试保留)
- **新增 tag v1.0.5.8** (1aada5e)

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

### ✅ v1.0.5.10 (3 联版 UI overhaul 第一弹, 55511ed / b744623)
- ✅ **后端** (`ebdf5b6`): `list_articles()` 加 `keyword` Query 参数 (修历史 BUG) + `POST /api/articles` + `ArticleCreate.source_id: int → str` (DB TEXT schema drift)
- ✅ **前端** (`b744623`): articles filter (source + tag `<select>`) + sidebar 7 个独立 URL + pushState/popstate + tag chip popover (CSS :hover+tabindex+focus-within, 不用 Alpine)
- ✅ **dev 脚本** (`55511ed`): `scripts/web-8901.sh` 120 行 (端口分离 8900/8901)
- ✅ 245 pytest pass + 2 skip
- ✅ 5+ 新单测 (filter_by_source/tag/keyword + post_create_201 + post_create_idempotent + post_create_unknown_source_400)

### ✅ v1.0.5.11 (CRUD modal + share card, 1617095)
- ✅ 后端 CRUD modal: create/edit/delete 三步确认 (复用 articleDetail modal 模式)
- ✅ share 页: 11 格式 table → card grid (1 卡 1 篇文章, hover tooltip 全文)
- ✅ PR #2 squash merged

### ✅ v1.0.5.12 (6 UI bug fix, 9a3a6d1)
- ✅ share POST `/api/share` 405 → 路径修 + body 改 `{format, payload: {article_ids}}` 嵌 payload
- ✅ "查看"按钮 wrap → `white-space: nowrap`
- ✅ 搜索框回车 + 清除 (×) + 🔍 icon
- ✅ tag 颜色按 `tag_definitions.color` 渲染 (4 层流水线: Pydantic TagRef + SQL JOIN + HTML inline style + CSS vars)
- ✅ config tag 选色器: 8 色 swatch radio grid (从 uiux-asset-library 抽调色板, 紫罗兰边框选中态)
- ✅ `:disabled="!!obj"` 永远 true 陷阱 → 检查具体字段 `!!target.name`
- ✅ PR #3 squash merged

### ✅ v1.0.5.13 (datetime deprecation 修, 04a21b5)
- ✅ `moni/share/formats.py:198` `datetime.utcnow()` → `datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')` (保留 'Z' RFC 3339 后缀)
- ✅ +`timezone` import
- ✅ +回归测试 `test_created_at_is_timezone_aware_utc_iso8601` (assert 无 DeprecationWarning + ts 以 'Z' 结尾 + parse tzinfo=UTC)
- ✅ 246 pytest pass + 2 skip (从 245 → 246)
- ✅ 0 DeprecationWarning/run (从 55)
- ✅ PR #4 squash merged

---

## 数据现状 (2026-06-09 v1.0.5.13)

```
articles by source:
  36kr_rss        83  (v1.0.5.5 captcha bypass 落地, 真 Chrome + body.innerText, 累加)
  mpaypass        82  (v1.0.5 修好 deep crawl, fetch URL 锚点 + CLI gate, 累加)
  qbitai          15  (v1.0.3 改 /feed RSS, 稳定抓, 累加)
  wechat_rss_a     9  (v1.0.5.6 token 1, 5 篇累加 — 未央科技 weiyangx 公众号)
  wechat_rss_b     5  (v1.0.5.6 token 2)
  wechat_rss       4  (v1.0.4-eval legacy, env var 路径, historical 保留)
  TOTAL          198
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
- [x] 修 36kr_newsflash URL (首页是 nav) — v1.0.5.8 用户决策彻底删源
- [x] 修 qbitai URL (品牌页 403) — v1.0.5.4 改 /feed RSS
- [x] 36kr captcha bypass — v1.0.5.5 真 Chrome Profile 7 抓 23 篇

### 🟡 中 (业务体验)
- [x] 填 .env 完整 WECHAT_RSS_TOKEN — v1.0.4-eval 已完成 (4 篇入库)
- [x] share/CRUD 前端 UI (PRD §4.7) — v1.0.5.10 + v1.0.5.11 完成
- [x] tag chip popover tooltip (CSS :hover+focus-within) — v1.0.5.10 完成
- [x] share card grid 改版 (11 格式 → 1 卡 1 文) — v1.0.5.11 完成
- [x] 6 个 UI bug 修复 (manual review) — v1.0.5.12 完成
- [x] datetime.utcnow() DeprecationWarning 修 — v1.0.5.13 完成
- [ ] 2 个 skip 测试改 mock → pytest 全绿 0 skip (P1)
- [ ] mpaypass / qbitai fetch 间隔调长 (30min → 60min, 抓多没消化) (P2)
- [ ] LLM 设置 web UI (T10, PRD §4.7 第 6 菜单) (P2)

### 🟢 低 (清理)
- [x] 删 `wt/*` 目录 (30+ MB, 已 .gitignore) — v1.0.5 完成
- [x] 修 `moni/share/formats.py:198` datetime deprecation — v1.0.5.13 完成
- [x] share manifest default profile worker — T7 已实装 (0a71c88)
- [x] cron daemon 启动 — T7 install_cron.sh 已实装
- [x] PR workflow 改造 (v1.0.5.10+ feature branch, 不用 wt)
- [x] 3 个错向 tag 修 (v1.0.5.10/11/12) — 上次 session 自我修复 + 本 session 验证全对

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
| v1.0.5.8 | 1aada5e | 36kr_newsflash 删源 — 用户决策, 36kr_rss 已覆盖, 删 source + tests + config |
| v1.0.5.9 | 2eebbfd | legacy wechat_rss 退役 — env var 路径废, a/b 已覆盖, 删 catalog 行 + cron |
| v1.0.5.10 | 55511ed | 3 联版 UI overhaul 第一弹 — articles filter (source/tag/keyword) + sidebar URL + tag popover + POST /api/articles + scripts/web-8901.sh |
| v1.0.5.11 | 1617095 | CRUD create/edit modal + share card grid (PR #2 squash) |
| v1.0.5.12 | 9a3a6d1 | 6 UI bug fixes (PR #3 squash) — share POST path + body schema + 查看 wrap + 搜索 UX + tag 颜色 4 层 + swatch palette + :disabled 陷阱 |
| v1.0.5.13 | 04a21b5 | datetime.utcnow() → datetime.now(timezone.utc) — 修 Python 3.12+ DeprecationWarning (PR #4 squash) |
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
