# STATUS — moni

> 项目状态与功能清单。每个 Phase 完成时更新日期 + 阶段描述。

---

## 当前状态: **v1.0.6.1 — 抓取结果结构化解释 + 前端重构** (2026-06-15)

**主分支**: main @ `v1.0.6.1`
**Web 服务**: `127.0.0.1:8900`
**数据库**: `data/news.db` — schema_version=13, 6 sources
**测试**: 322 passed, 2 skipped

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

## 抓取源 (6 active)

| 源 | 间隔 | 类型 | 状态 |
|---|---|---|---|
| 36kr_rss | 15min | html | ✅ |
| mpaypass | 30min | html | ✅ |
| qbitai | 30min | rss | ✅ |
| wechat_rss_a | 60min | wechat_aggregator | ✅ |
| wechat_rss_b | 60min | wechat_aggregator | ✅ |
| bestblogs | 120min | rss (OPML 聚合) | ✅ (375 号, 三层限制) |
