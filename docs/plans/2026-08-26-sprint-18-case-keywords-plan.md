# 案例关键词 + 双维度标签系统 — Sprint-18 实施计划 (v2)

日期: 2026-08-26
状态: v2 (agy plan v1 + grok 审计 17 发现综合, 待 dad 拍板 3 项)
目标版本: v1.0.36 (sprint-18)
评审: agy → /tmp/moni-plan-keywords-agy-20260826.md / grok → /tmp/moni-plan-keywords-grok-20260826.md

## 一、需求 (dad 8-26)

1. 关键词 ≠ tag: 关键词 = LLM 从文章/案例抽取的业务细粒度词 (跨境支付/智能投顾/反欺诈); tag = 宏观分类 (banking/payment/ai-fintech)
2. 双维度独立落库 + 数据库管理
3. 高价值案例榜单反选剔除 (不展示含某关键词/某 tag 的案例)
4. 今天只写 plan, 下次 session 直接开干

## 二、核心设计 (双 agent 共识)

### 数据模型 (agy 决策 1/2 + grok K2/K3)
| 维度 | 表 | 粒度 | 写入方 |
|---|---|---|---|
| 关键词 | `case_keywords` (031 已建) | 细: 跨境支付/智能投顾 | LLM 抽取 (auto_llm) + 人工 (manual_user), 已有 CRUD API |
| 分类 tag | `case_tags` (024 已建但空) | 粗: banking/payment | **需拍板**: A 补 pipeline 写入 (案例插入时继承 article_tags) / B 暂用 article_tags 并在 UI 标明"来源标签" / C 本轮只做关键词一维 |

**P0 铁律 (grok K1/K2)**:
- 停用 substring fallback (排除"支付"会误伤"支付宝", "银行"会干掉半张榜) — 只按结构化 keywords/case_tags 精确匹配
- 归一化放写入侧 (KEYWORD_ALIAS_MAP: 跨境清算→跨境支付), 不在反选侧做 substring

### LLM 打标流水线 (agy §3, P0)
1. `case_extraction.txt` prompt 加 `"keywords": ["智能投顾","财富管理"]` (3-5 个, 禁泛词: 银行/金融/系统)
2. `normalize.py` 加 `normalize_keywords()`: 去空/去单字/别名对齐/去重截断 5 个
3. `repository.insert_cases` 同事务: 主表 + case_keywords (auto_llm) + case_tags 继承 (若拍板 A)
4. 存量回填 CLI: `moni extract keywords --dry-run/--limit/--overwrite-auto` (95 条, 轻量 prompt 只读 subject+counterparty+summary+evidence, ~200 tokens/条)

### 榜单反选升级 (grok §2/3/4, P2)
1. **后端**: `cases_dashboard_stats` rank_sql 带 `keywords` + `case_tags` (GROUP_CONCAT, 零 N+1)
2. **facet 端点**: `/api/stats/case-facets` → {keywords: [{keyword,count}], tags: [{name,display_name,count}]} (只聚合 reference_value=high, 防勾了 medium 词榜单不动)
3. **前端**: `excludedKeywords` / `excludedTags` 两维 state; 面板一组下拉分两块 (业务 tag + 关键词); 徽章拆维 `已排除 1 tag · 2 关键词 · 榜单 10→7`
4. **localStorage v2**: `moni_excluded_case_filters = {v:2, keywords:[], tags:[]}`; 旧 `moni_excluded_case_tags` 数组迁到 keywords (grok K6)
5. **排除后补位**: 本轮前端滤 TOP10 不补位 (hint 写明), 后端 WHERE 补位留后置

## 三、工作量 (agy §7)
| 阶段 | 内容 | 文件 | 估算 |
|---|---|---|---|
| P0 打标+回填 | prompt + normalize_keywords + insert 同事务 + CLI | case_extraction.txt/normalize.py/repository.py/cli | 1.5 人天 |
| P1 tag 维度 | case_tags 写入 (若拍板 A) + 联合读取 | repository.py/pipeline.py | 0.5 人天 |
| P2 反选升级 | dashboard 带维 + case-facets + stats.js 重写 + index.html 面板 + 测试 | stats repository/stats.js/index.html/tests | 1.0 人天 |
| 合计 | | 8 关键文件 | 3.0 人天 |

## 四、测试计划 (agy §6)
- test_case_keywords_normalize.py (清洗/别名/截断)
- test_cases_pipeline_tagging.py (打标 JSON 解析/同事务/keywords 落库)
- test_cli_extract_keywords.py (dry-run 不写库/manual_user 不覆盖)
- test_stats_dashboard_keywords.py (ranking 带 keywords+tags 字段/无 N+1)

## 五、待 dad 拍板 (grok §"给 dad 的 3 个拍板")
1. **第二维用谁**: A case_tags (正统, 补 pipeline 写入) / B 暂用 article_tags (UI 标"来源标签") / C 本轮只做关键词一维, tag 维后置 — **推荐 A** (agy 方案已含继承逻辑, 0.5 人天)
2. **排除后榜单补位**: A 仅隐藏当前 10 条 (hint 写明) / B 后端 WHERE 补满 10 — **推荐 A** (第一刀)
3. **localStorage 迁移**: 旧数组一律迁 keywords 维, 不迁 tags — **推荐按 grok K6 执行**

## 六、下一步 (下次 session 入口)
1. 读本 plan + /tmp/moni-plan-keywords-agy-20260826.md + /tmp/moni-plan-keywords-grok-20260826.md
2. dad 拍板 3 项 → 从 P0 开始: prompt 改 → normalize → insert → CLI 回填 → P2 前端
3. 分支: feature/v1.0.36-case-keywords (从 main 开, PR #133 已包含 v1.0.35.19 基础)
