# STATUS — moni

> 项目状态与功能清单。每个 Phase 完成时更新日期 + 阶段描述。

---

## 当前状态: **v1.0.7.3 — BestBlogs 系列信源扩展** (2026-06-18)

**主分支**: main @ v1.0.7.3
**Web 服务**: `127.0.0.1:8900`
**数据库**: `data/news.db` — schema_version=15, 10 sources
**测试**: 350 passed, 2 skipped

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
