# STATUS — moni

> 项目状态与功能清单。每个 Phase 完成时更新日期 + 阶段描述。

---

## 当前状态: **v1.0.5.20 — tag 关联逻辑修正 + keyword_hits 完善** (2026-06-12)

**主分支**: main @ `v1.0.5.20` (32 个 tag)
**Web 服务**: `127.0.0.1:8900` (200 OK)
**数据库**: `data/news.db` — schema_version=11
**测试**: 277 + 2 skip

### v1.0.5.20 进展 (2026-06-12)

**问题**:
1. 文章继承 source 的所有 tag，不管实际命中了哪些关键词
2. tooltip 显示所有 tag 的关键词混合，无法区分
3. keyword_hits 没有 tag_name 字段

**改动**:
- `_load_tag_keywords_for_source()` 返回 {tag_name: [keywords]}
- `apply_tag_keyword_filter()` 按 tag 独立检查命中，记录 tag_name
- `dedupe_insert()` 只插入命中的 tag（替代 _copy_source_tags_to_article）
- TagRef 独立携带 keyword_hits
- Migration 011: keyword_hits 加 tag_name 列
- SQL 按 tag_name GROUP BY 返回 JSON
- 前端 tooltip 只展示该 tag 的命中关键词

**验证**:
- 277 passed, 2 skipped
- 新抓取文章只关联命中的 tag
- 每个 tag tooltip 只显示该 tag 的命中关键词

---

### v1.0.5.19 进展 (2026-06-11)

**Bug fixes**:
1. LIMIT 1 → 取全部文章
2. output_path 正则匹配 URL 修复
3. summary_gen ID 解析兼容

**AI 摘要回写**:
- `moni/share/summary_gen.py` — 逐篇 LLM 调用 + 写回 DB

**LLM 调用统一**:
- 所有 LLM 调用走 `call_llm()` → TokenLogger
- summary_gen.py 改用 call_llm()

**LLM 设置 UI (第 6 菜单)**:
- Provider CRUD + 连通性测试
- Model CRUD + 切换活跃 model
- Settings partial update
- 用量统计

**Profile 隔离**:
- 创建 moni profile
- call_llm() 加 --profile moni

---

### 历史版本

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

## 抓取源 (5 active)

| 源 | 间隔 | 状态 |
|---|---|---|
| 36kr_rss | 60min | ✅ |
| mpaypass | 60min | ✅ |
| qbitai | 60min | ✅ |
| wechat_rss_a | 60min | ✅ |
| wechat_rss_b | 60min | ✅ (免费账户, 限 2 公众号) |
