# STATUS — moni

> 项目状态与功能清单。每个 Phase 完成时更新日期 + 阶段描述。

---

## 当前状态: **v1.0.9.2 — bestblogs transient vs permanent miss** (2026-06-24)

**主分支**: main @ v1.0.9.2 (2a688ff, PR #43 merged)
**Web 服务**: `127.0.0.1:8900` (Python 3.12, v1.0.9.1 跑的,本次未重启因为只动 share task 调用的 bestblogs 模块)
**数据库**: `data/news.db` — schema_version=18, 11 tables
**测试**: 413 passed, 2 skipped

### v1.0.9.2 进展 (2026-06-24)

**BestBlogs transient vs permanent miss 修复**:
- 之前 `search_by_url` 返 `None` 涵盖两种情况: URL 真不在 bestblogs 索引 (永久) + SSL EOF/timeout/5xx/429 (瞬时)。两种都 `mark bb_fetched_at` → 瞬时错误被永久污染
- 三态返回: `str` (找到) / `""` (永久 miss, mark) / `None` (瞬时, **不** mark, 下次重试)
- 显式 catch `httpx.ConnectError` / `RemoteProtocolError` (SSL EOF 标准错误)
- 数据清理: SQL 把 3 篇被 SSL EOF 误标的 bestblogs 文章 `bb_fetched_at` 清 NULL, 让它们能重试
- 测试: `test_search_miss_marks_fetched` 拆成 permanent-miss + transient-error 两个 case

### v1.0.9.1 进展 (2026-06-24)

**Share worker PATH prepend (cron FileNotFoundError 修复)**:
- `scripts/default_worker.sh` + `moni/share/worker.py` 启动子进程前 `export PATH="$HOME/.local/bin:$PATH"`
- 根因: cron / launchd 子进程 PATH 精简 (/usr/bin:/bin),hermes CLI 装在 ~/.local/bin 找不到 → share task 1 秒死 + error_msg 永远空
- Defense-in-depth: 任何调用方式 (cron / launchd / web UI / 手动 shell) 都能找到 hermes
- 验证: `env -i PATH=/usr/bin:/bin` 模拟 cron 不再爆 FileNotFoundError;失败任务 `t5TRTdiMfE52cEOUtik-fA` 重跑成功,产物 PDF 15KB 1 页
- 详见 `concepts/moni-ai-architecture.md` (v1.0.5.26 那条踩坑)

**Pin icon 改版**:
- Pinned: SOLID silhouette (梯形头 + 粗针 + 底点), 17px 紫藤萝 — 来自 image gen (Fal gpt-image-2), 用户选候选 B
- Unpinned: outline (1.6px stroke, 17px) — 跟 v1.0.8 不变
- 撤掉 v1.0.8.1 的 5 层 3D enamel (用户 push "太丑了 不要背景")
- 撤掉 v1.0.8.2 的 40px 圆 + 26px svg (用户 push "缩小 1/3")
- 现状: 17px icon, 32px 圆 cell, 44px 列宽, 紫藤萝只 color swap (无背景/无阴影)

### v1.0.8.1 进展 (2026-06-23) — Pin UX 改版

**3 个修复**:
1. Pin 按钮位置 + 大小: 移到最左新增列, 32px → 40px → 17px (演化), GSAP 弹一下 (scale 0.85→1)
2. 单篇 togglePin 也走 5s 反悔: `_pendingPinToggles[articleId]` map, 双击撤销
3. bulk unpin 改即时 POST + 撤销=重新 pin: `_commitBulkUnpin` 立即发请求, `_repinBatch` 走 togglePin pipeline 撤销

**踩坑**:
- 用户先后两次推翻了 v1.0.8.1 的 3D enamel (太丑) + v1.0.8.2 的放大版 (太大), 最终 v1.0.8.3 用 image gen solid silhouette 17px
- v1.0.8.1 togglePin staging 模式 + bulk unpin 即时 POST = 行为统一, 撤销都走 _repinBatch/togglePin
- 沙箱拦 image gen (FAL_KEY 没配), 用户配 Nous Portal 后可用
- image gen 实际"圆头" prompt 跑了简化梯形, vision 描述为准

### v1.0.8 进展 (2026-06-23) — Pin Tab + 分享从 Pin 选择

**新功能**: Pin/Unpin 文章 + 文章查阅子 tab [全部] [Pin (N)] + 批量 unpin + 5s 倒计时撤销 + 分享 page chip 切换 [📋 全部] [📌 仅 Pin] (默认 Pin 模式) + Pin icon (nullframe 风格 SVG, 不用 emoji)
**后端**: migration 017 (article_pins + CASCADE) + 5 endpoints
**前端**: pinCol + subtab + toast + chip 切换
**测试**: 24 个新 test, 412/412 pass
**PR**: #40 squash merge → tag v1.0.8

### v1.0.7.6.2 进展 (2026-06-23)

**Bug 修复**:
- **wechat_rss_a/b 0 篇 (实际根因)** → `moni db seed-config` 上次跑时 env 变量没加载 (用户没 `set -a && source .env` 直接跑), `_env_token()` 返 `__WECHAT_RSS_A_TOKEN__` 占位符, 入库 `sources.url`, waytomaster 401 → 0 篇无错误提示
- **修复**: 实际抓取时 fetcher 检测占位符 URL (regex `__WECHAT_RSS_\w+TOKEN__`) loud error + [] 拒绝发起请求, 用户立即知道要 source .env 重跑 seed-config
- **诊断 SQL**: `SELECT name, substr(url, 1, 80) FROM sources WHERE name LIKE 'wechat%';` — 如果 url 包含 `__WECHAT_RSS_*_TOKEN__` 就是这个 bug

**安全告警** (待用户处理):
- **git 历史 token 泄漏**: commit 5b86616 (v1.0.5.6) 曾经在 sources.url 字段 hardcode 真实 token `eyJhbG...`, 7b5f52c 改为 _env_token 注入但没清 git history
- **影响范围**: 任何有 moni 仓库读权限的人 `git log -S "?token="` 可拿到 token
- **建议**: 用户在 waytomaster 控制台轮换 2 个 token, 旧 token 失效即不需清理 git history (轮换 vs git-filter-repo 二选一)

**测试**: +5 新增 (test_env_token_returns_placeholder_when_unset / returns_actual_value_when_set / a_falls_back_to_legacy_token / fetcher_refuses_placeholder_url / fetcher_refuses_empty_url)
**PR**: #39 squash merge → tag v1.0.7.6.2

**实际验证** (06-23 抓取):
- wechat_rss_a: 37 篇历史 + 6 fetched (0 inserted, dedupe 命中)
- wechat_rss_b: 41 → 59 (+18 inserted)
- 2026-06-23 发布的新文章已入库 ([未央科技weiyangx] 国务院报告)

**bestblog-x 状态**: 6:39-6:45 自动 done 5 篇入库, zombie sweeper (PR #36) 已自动清理, 无需代码修改

**操作 SOP** (写入 AGENTS.md): 任何 moni 命令前必须 `set -a && source .env && set +a` 让 env 变量自动 export

### v1.0.7.6.1 进展 (2026-06-23)

**Bug 修复**:
- **409 fallback 返错源 job_id** → list_recent(limit=1, status='running') 不按 source 过滤, 当 36kr_rss zombie 被 sweep 而 mpaypass/其他源仍有 fresh running row 时, 36kr 的 409 返的 running_job_id 可能是别源。前端拿错 job_id 轮询, modal 永远卡在「初始化中」。
- **修复**: list_recent 加 `source=` 参数 + 409 fallback 用 `source=resolved` 查同源 running job。Race 兜底: 查不到 running job_id 时(sweep 在两次 DB 调用之间发生)放过, 启动新 job。
- **手动清理**: 4 个 36kr_rss zombie running job (最早 1.5h 前) 手动 mark stale, 否则 36kr 继续被挡住

**测试**: +3 新增 (test_list_recent_filters_by_source / test_409_job_id_is_scoped_to_requested_source / test_409_falls_through_when_running_row_vanished)
**PR**: #38 squash merge → tag v1.0.7.6.1

### v1.0.7.6-hotfix 进展 (2026-06-23)

**Bug 修复**:
- **fetch_jobs 僵尸**: 8 个 36kr_rss running job 卡死 (daemon thread 异常退出但 mark_done 没成功), has_running() 永远返回 True 挡住新请求
- **修复**: sweep_zombies() 每次 /run 自动清理 2h+ 仍 running 的 job → status='stale'
- **前端**: status='stale' 映射「已中止(无响应)」红字, 轮询终止

### v1.0.7.6 进展 (2026-06-22)

**功能**:
- 异步 fetch 任务: POST /api/fetch/sources/{name}/run 立即返回 job_id, 后台线程跑
- 进度轮询: GET /api/fetch/jobs/{job_id} 每 5s 刷新
- 进度 modal: 实时显示 done_feeds/total_feeds, 当前 feed 名, 已抓文章数
- 错误处理: 10min hard timeout, 同源并发返 409, 失败/超时/僵尸状态红字提示
- 新表 fetch_jobs (migration 016): 存每次抓取实时状态

**测试**: 376 passed, 2 skipped (+17 新增)
**实测**: bestblog-yt (124 feeds) 174s 完成, 进度条实时更新

---

### v1.0.7.5 进展 (2026-06-22)

**Bug 修复**:
- **bestblog-yt 0 篇** → YouTube feed 用 Atom 1.0 格式，原代码只解析 RSS 2.0，导致 channel.find() 永远 None。修复：_fetch_feed 加 root tag 检测，Atom 走 _parse_atom。
- **bestblog-podcast 误命中 wechat tag** → tag_filter 只对 source=='bestblogs' 走 title-only，子类 fallback 到 title+content，导致 description 里的「关注公众号/小程序」误命中。修复：source.startswith('bestblog')。

**技术细节**:
- 重构 _extract_items 公共方法，RSS 和 Atom 共用 article 构造逻辑
- 修复 Python 3.12 Element truthiness deprecation 导致 find() or find() 链失败

**PR**: #33 squash merge → tag v1.0.7.5
**测试**: 359 passed, 2 skipped (351 → 359, +8 新增)
**实测**: bestblog-yt 5 feeds 抓 75 篇（修复前 0 篇）

---

### v1.0.7.4 进展 (2026-06-22)

**清理**:
- 删 `source_monitor.py` (598 行) — 早期 cron 脚本，已被 moni 包完整替代
- `moni/cron/scheduler.py` 清理 2 处 "migrated from source_monitor.py" 注释
- 移除 `/Users/mt16/dev/source-monitor/` 空壳目录 (根因: editable install 误指导致回归时全 API 404)

**PR**: #31 squash merge → tag v1.0.7.4
**测试**: 351 passed, 2 skipped (与 main 一致)
**影响**: 纯清理，无功能/行为变更

---

### v1.0.7.1 - v1.0.7.3 进展 (2026-06-18)

**新功能 — BestBlogs 系列信源扩展**:
- 新增 `bestblog-podcast` (60 个小宇宙播客 feeds)
- 新增 `bestblog-yt` (20 个 YouTube 频道 feeds)
- 新增 `bestblog-x` (160 个 x.com/Twitter feeds)
- `bestblogs` 合并 ALL OPML (291 个新英文技术博客)
- 多 OPML URL 逗号分隔支持，fetch 时合并去重
- 本地 OPML 文件支持

**技术细节**:
- FetcherBestBlogPodcast / FetcherBestBlogYt / FetcherBestBlogX 子类复用 FetcherBestBlogs
- bestblogs 排除 xgo.ing 域名的 feed (已移到 bestblog-x)
- 默认 tag: wechat, ai-fintech
- 黑名单: 入境游/文娱/明星/游戏/影视/电商/消费/美食/腾讯控股

---

### v1.0.6.9 进展 (2026-06-18)

**新功能 — 菜篮子 (Basket)**:
- 被过滤丢弃的文章不再消失，自动存入 basket 表
- 新增「菜篮子」UI tab，查看被丢弃文章 + rescue 回正式文章库
- 支持按来源/过滤原因/rescue 状态筛选
- rescue 时可选择 tag，自动关联到 article_tags
- 新增 4 个 API 端点: 列表/详情/rescue/删除

**技术细节**:
- 过滤器 (blacklist/whitelist/tag_filter) 丢弃时自动写 basket
- basket 表 schema 同 articles，额外加 dropped_reason/dropped_by/rescued_at
- INSERT OR IGNORE 防重复
- 新增 18 个测试覆盖 basket 功能

---

### v1.0.6.8 进展 (2026-06-17)

**功能**:
- 导入全文功能从仅 bestblogs/虎嗅 扩展到所有信源
- 移除前端 x-show 源过滤，mpaypass/36kr/qbitai 等均可导入 .md 全文
- 后端 import-fulltext 接口本身无源限制，改动仅在前端

---

### v1.0.6.7 进展 (2026-06-16)

**新功能**:
- 分享页文章选择新增 tag 分类下拉筛选
- 来源 + 分类 + 每页 三维筛选

---

### v1.0.6.5 进展 (2026-06-16)

**新功能**:
- 虎嗅 RSS 源 (第 7 个源): FetcherHuxiu, 全文抓取, 绑定 ai-fintech/miniprogram/wechat
- 单源「立即抓取」强制执行 (force=True)，不再被间隔挡住
- 虎嗅/BestBlogs 点「立即抓取」弹时间范围选择 modal

### v1.0.6.6 进展 (2026-06-16)

**Bug 修复**:
- 虎嗅 boilerplate 清洗: 清除 "本文来自微信公众号" / "原标题" 等来源声明
- CRUD 删除失败: 前端加 `?confirm=1` 参数
- 文章查阅分页: 新增每页条数选择 + 上下页翻页

---

### v1.0.6.4 进展 (2026-06-15)

**新功能**:
- Tag 过滤丢弃明细：抓取结果下方折叠面板，显示被丢弃文章标题（最多 20 条）
- 全部抓取时按源分组展示，单源抓取时直接展示
- tag_filter.py 返回 dropped_articles 列表

---

### v1.0.6.2 进展 (2026-06-15)

**新功能**:
- ⚡ 全部抓取按钮：抓取管理页顶部，一键跑所有源
- POST /api/fetch/all/run 端点：遍历所有源 + 详细日志写 cron_runs + fetch_log
- 周一补抓：自动检测周一 → date_from=周六
- cron 改工作日 9:10 AM（不跑常驻进程，手动触发为主）
- fetch_log 新增 8 列 (migration 014): cleaned/filtered/dropped_blacklist/dropped_whitelist/dropped_tag_filter/filters_applied/tags_used/deduped

**Bug 修复**:
- wechat_rss_b URL 截断修复（seed_config.py 存的是截断 token）
- wechat_rss token 从 .env 读取，不硬编码在代码里（安全修复）
- .env 拆成 WECHAT_RSS_A_TOKEN + WECHAT_RSS_B_TOKEN

**测试**: 322 passed, 2 skipped

---

### v1.0.6.0 进展 (2026-06-15)

**前端重构**:
- sidebar → topnav 顶栏导航 (Sophon.at 参考)
- 中国传统色 oklch 配色主题 (纯衣/玄色/天水碧/紫藤萝)
- 按钮全面重设计 (自包含样式 + hover/click 动效)
- 主题管理器: 🎨 切换/导入 globals.css/localStorage 持久化
- 🌙/☀️ 暗色模式一键切换

**服务控制**:
- topnav 状态指示灯 (绿点 + PID)
- ↻ 重启按钮 (确认 + 轮询等待 + Toast)
- GET/POST /api/service/status|restart|stop 端点

**测试**: 322 passed, 2 skipped

---

### v1.0.6.1 进展 (2026-06-15)

**新功能**:
- 抓取结果结构化解释: 「立即抓取」后 JSON 左侧新增文字说明
- 结果摘要: ✅ 新增 X 篇 / ⚠️ 全部重复 / ℹ️ 无新内容
- 流程分解: 远程拉取 → 解析 → 去重 → 过滤
- 双栏布局: 左侧结构化解释 + 右侧原始 JSON

**Bug 修复**:
- wechat_rss_b URL 截断修复 (JWT token 入库不完整)

**测试**: 322 passed, 2 skipped

---

### v1.0.5.25 进展 (2026-06-14)

**新功能**:
- fetch_config UI 编辑器：配置管理 → 抓取源 → 「抓取配置」按钮 → modal 编辑参数
- BestBlogs API 按需接入：分享时调 API 获取 score/summary/mainPoints/keyQuotes/tags
- 分享选项扩展：摘要来源（moni AI / BestBlogs / 两者）、BB 增强字段可选
- DB migration 013：articles 表加 bb_score/summary/one_sentence/main_points/key_quotes/tags/resource_id/fetched_at

**Bug 修复**:
- worker PNG/HTML 格式：检测 LLM 实际生成的文件并复制，不再保存文字描述
- PDF 末尾标签：跳过文档末尾的孤立标签段落
- SQL fallback：bb_* 列不存在时 fallback 到 NULL 查询

**测试**: 322 passed (新增 22 tests: fetch_config 10 + bestblogs_api 12)

---

### v1.0.5.24 进展 (2026-06-13)

**新功能**:
- BestBlogs 智能抓取：时间范围选择（自动/过去N天/自定义日期）
- 全文导入：文章详情页粘贴本地 .md 路径 → 更新 raw_content → AI 摘要/核查使用全文
- 复制原文链接：一键复制到剪贴板
- tag filter: bestblogs 只匹配标题（避免摘要 boilerplate 误命中）

**测试**: 300 passed (新增 19 tests)

---

### v1.0.5.22 进展 (2026-06-13)

**Bug 修复**:
- 黑名单模式记录 keyword_hits
- `scripts/backfill_keyword_hits.py` — 历史文章 keyword_hits 补录

---

### 历史版本

- v1.0.5.21: PDF 日报分享格式 + 版式配置化
- v1.0.5.20: tag 关联逻辑修正 + keyword_hits 完善
- v1.0.5.19: share bugfix + AI 摘要回写 + LLM 统一 + LLM 设置 UI
- v1.0.5.18: share 功能 v2 重构
- v1.0.5.17: UI worker 按钮 + 进度条
- v1.0.5.16: confirm-modal before queueing
- v1.0.5.15: 5 user fixes
- v1.0.5.14: display_name + 5 fixes
- v1.0.5.13: timezone-aware created_at
- v1.0.5.12: 6 UI bug fixes
- v1.0.5.11: CRUD create/edit modal + share card grid
- v1.0.5.10: 双端口 (8900/8901)
- v1.0.5.9: retire legacy wechat_rss
- v1.0.5.8: remove 36kr_newsflash
- v1.0.5.7: extract publisher from title prefix
- v1.0.5.6: wechat_rss multi-account
- v1.0.5.5: 36kr captcha bypass
- v1.0.5.4: tag-keyword 过滤
- v1.0.5.2: LLM 异步化 + 进度面板
- v1.0.5.1: 3 个 UX bug 修复
- v1.0.5: P0 + P1 全做完

---

## 分享格式一览 (7 种)

| 格式 | 说明 | Prompt 文件 |
|------|------|-------------|
| plain_text | 纯文本 | 无（直接输出） |
| markdown | Markdown | format-markdown.md |
| html_landing | HTML 网页 | format-html.md |
| rich_text | 富媒体文本 | format-rich-text.md |
| info_graphic_png | 信息图 PNG | format-info-graphic-png.md |
| report_png | 报告 PNG | format-report-png.md |
| pdf | PDF 日报 | format-pdf.md |

---

## 抓取源 (7 active)

| 源 | 间隔 | 类型 | 状态 |
|---|---|---|---|
| 36kr_rss | 5min | html | ✅ |
| mpaypass | 5min | html | ✅ |
| qbitai | 5min | rss | ✅ |
| wechat_rss_a | 5min | wechat_aggregator | ✅ |
| wechat_rss_b | 5min | wechat_aggregator | ✅ |
| bestblogs | 5min | rss (OPML 聚合, 375 号) | ✅ |
| huxiu | 5min | rss | ✅ |
