# STATUS — moni

> 项目状态与功能清单。每个 Phase 完成时更新日期 + 阶段描述。

---

## 当前状态: **v1.0.5.21 — PDF 日报格式 + 版式配置化** (2026-06-12)

**主分支**: main @ `v1.0.5.21` (33 个 tag)
**Web 服务**: `127.0.0.1:8900` (200 OK)
**数据库**: `data/news.db` — schema_version=11
**测试**: 281 passed, 2 skipped

### v1.0.5.21 进展 (2026-06-12)

**新功能**:
- PDF 日报分享格式（7 种格式之一）
- 版式配置化（`moni/share/templates/pdf-style.yaml`）
- PDF 生成脚本（`scripts/generate_pdf.py`，使用 reportlab）

**Bug 修复**:
- worker 脚本语法错误（多余的 fi）
- REPO_DIR → REPO_ROOT 变量名错误
- pdf 格式文件扩展名映射缺失
- format-pdf.md prompt 优化（禁止表格、明确输出格式）

**文档**:
- `moni/share/templates/README.md` — 如何修改版式
- `HANDOFF.md` — 如何修改分享格式（完整指南）

---

### v1.0.5.20 进展 (2026-06-12)

**Bug fixes**:
1. summary_gen profile 修复（--profile default → moni）
2. keyword_hits 表 + tag_name 字段
3. tag 关联逻辑修正（只关联命中的 tag）
4. info_graphic_png 支持 generation_approach
5. worker 传递 skill 参数
6. 多篇文章竖排版 + 用户自定义标题

---

### 历史版本

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

**版式配置**：`moni/share/templates/pdf-style.yaml`
**修改指南**：`moni/share/templates/README.md`

---

## 抓取源 (5 active)

| 源 | 间隔 | 状态 |
|---|---|---|
| 36kr_rss | 60min | ✅ |
| mpaypass | 60min | ✅ |
| qbitai | 60min | ✅ |
| wechat_rss_a | 60min | ✅ |
| wechat_rss_b | 60min | ✅ (免费账户, 限 2 公众号) |
