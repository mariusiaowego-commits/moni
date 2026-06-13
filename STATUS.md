# STATUS — moni

> 项目状态与功能清单。每个 Phase 完成时更新日期 + 阶段描述。

---

## 当前状态: **v1.0.5.24 — BestBlogs 智能抓取 + 全文导入** (2026-06-13)

**主分支**: main @ `v1.0.5.24` (36 个 tag)
**Web 服务**: `127.0.0.1:8900` (200 OK)
**数据库**: `data/news.db` — schema_version=12, 318 articles, 6 sources
**测试**: 300 passed, 2 skipped

### v1.0.5.24 进展 (2026-06-13)

**新功能**:
- BestBlogs 智能抓取：时间范围选择（自动/过去N天/自定义日期）
- 全文导入：文章详情页粘贴本地 .md 路径 → 更新 raw_content → AI 摘要/核查使用全文
- 复制原文链接：一键复制到剪贴板
- tag filter: bestblogs 只匹配标题（避免摘要 boilerplate 误命中）

**测试**: 300 passed (新增 19 tests: _in_date_range + _clean_description + bestblogs 标题匹配)

---

### v1.0.5.22 进展 (2026-06-13)

**Bug 修复**:
- 黑名单模式记录 keyword_hits（之前 blacklist 命中不记录）
- `scripts/backfill_keyword_hits.py` — 历史文章 keyword_hits 补录

**数据清理**:
- 262 篇无 keyword_hits 文章 → 补录 190 篇，删除 72 篇
- 文章 360→288，覆盖率 27%→100%

---

### v1.0.5.21 进展 (2026-06-12)

**新功能**:
- PDF 日报分享格式（7 种格式之一）
- 版式配置化（`moni/share/templates/pdf-style.yaml`）

---

### 历史版本

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
