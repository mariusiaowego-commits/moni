# 事实核查+ 研究聚合稿 (arch 合成, 待 dad 拍板)

> **作者**: blue-owl (arch, hermes coder profile)
> **日期**: 2026-08-11
> **状态**: research 完成, 等 dad 拍 Q1-Q4 后进 sprint
> **输入**: red-fox (214 行) + gold-tiger (404 行) + green-frog (282 行) 三份独立 research
> **arch 已实查核对的数字**:
> - `fact_check_method` 分布 = `NULL:2611 / llm:9 / llm_inference:23` (与 green-frog 一致)
> - `llm_jobs` 状态 = `fact_check done:9 / failed:1 / cancelled:1 / summary done:41 / running:1` (与 green-frog 一致)
> - `llm_settings` = `active_*_model_id=seed-model-...MiniMax-M3` 三值全在; `temperature_fact_check=0.2`, `max_tokens_fact_check=1500` (实际不生效, 见 A4 隐债)

---

## TL;DR

moni v1.0.22 加"事实核查+" (联网能力), **范围** = 单篇手动触发 + evidence 落库 + UI 展开 + 简报可选 web 模式. **不引新依赖**, 复用 moni 薄壳 + hermes 工具. **3 份 report 共识**:
- 审计可追溯是刚需 (业界 + 评审反方共同答案)
- method 字段必须收敛三值 (llm_inference / web_search / human)
- 现有 2 条隐债必须先修 (worker 不写 has_fact_check=1 + 同步 route 是死代码)

**4 个需要 dad 拍板**:
- **Q1** 主检索通道 (B 主 vs C 主 vs 混合 vs A 主 + Qwen enable_search)
- **Q2** v1.0.22 范围 (只单篇 vs 加批量 vs 加简报 web vs 全做)
- **Q3** 落库 model (MiniMax-M3 / Qwen enable_search / Gemini grounding / 双通道)
- **Q4** 评审反方准备深度 (UI 展开 / 双闸防幻觉 / 人工覆写状态机 / 组合)

---

## 一. 三份报告一致性表 (arch 交叉验证)

### 1.1 共识 6 条 (3 份都同意)

| # | 共识 | red-fox | gold-tiger | green-frog |
|---|------|---------|------------|------------|
| 1 | moni 是薄壳 + 不持 key + 审计刚需 | ✅ L1 / §4 | ✅ §1.5 | ✅ A4 / B6 |
| 2 | 现 MiniMax-M3 (阿里云百炼托管) 不支持内置联网搜索 | ✅ §2.5 | ✅ §1 (5 实证) | ✅ 实测 1 |
| 3 | 审计证据必落库, 含 query/url/snippet/fetched_at/model/conclusion | ✅ §5 / Q3 | ✅ §8 | ✅ B2 |
| 4 | method 字段需收敛 schema 预留三值 (llm_inference/web_search/human) | ✅ §5 / B2-4 | ✅ §9 | ✅ A2 / B1-3 |
| 5 | 多源一致性 (≥2 独立源) 是 moni 强置信信号 | ✅ §1.2/1.3 | ✅ Query A/B | (沿用) |
| 6 | 现有 2 处事实核查隐债必须先修 (worker has_fact_check + 死 route) | ✅ §5 (隐含) | (未提) | ✅ A4 |

### 1.2 冲突 1 条 (核心: 主检索通道)

**red-fox** (L9, §4) 推: **C 为主 (应用侧检索) + B 补充 + A 兜底**
- 依据: §3 矩阵 C 准确性 5/可控性 5/审计 5; 业界先例 (Snopes / FactCheck.org / Meedan ClaimReview) 全是"原始来源优先 + 引用列表"
- 落地: moni 新建 query 生成 → DDG lite + gov 域白名单 fetch → 证据落库 → 模型只读判断

**green-frog** (TL;DR L12, B1) 推: **B 为主 (hermes -t web) + C 补充 (DDG 兜底 + 自有 FTS5) + 不引 A**
- 依据: 附实测 1 — `hermes chat -q ... -t web -Q --max-turns 8` 实测返回 news.cn / people.com.cn 真实 URL + snippet, 零新依赖
- 落地: client.py extra_args 加 `-t web`; jobs.py 加 task_type `fact_check_plus` 分支

**gold-tiger** (§9.1) 推: A 兜底 = Qwen-plus / qwen3-max + enable_search + enable_source
- 依据: §6 评分 Qwen 17 / deepseek 15.5 / Gemini 14 / Claude 12 / GPT 11.5; 中文能力 Qwen 5/5; 成本 C 主 ¥20-60 / A ¥80-250 / 5 万/月 是"全量+agent_max"乱跑才达

**冲突本质**: "moni 内部谁来做检索" — red-fox 让 moni 自己干 (C), green-frog 让 hermes 工具层干 (B), gold-tiger 让百炼模型干 (A)。三者互斥于"主通道"位置, 但 B+C+A 三层都可同时存在。

### 1.3 互补信息 (不冲突, 各补一块)

| 信息 | 来源 | 关键补充 |
|------|------|---------|
| 业界 12 个产品方法学 | red-fox §2 | Snopes 6 档分级 / FactCheck.org 一手源 / PolitiFact Truth-O-Meter / Meedan ClaimReview schema |
| 12 个候选 model 4 维评分 + 真实 query 命中 | gold-tiger §3/§4/§5 | Qwen 17 分 / Gemini grounding 单价 / 实测 query A/B 跨源一致 |
| moni 5 条 prep 盲点实查 | green-frog Part A | DDG lite 通道 x.com 专用不适合核查 / method 9+23 分布 / has_fact_check 来源 migration 009 / 三调用链真实关系 / route 默认 model 死代码 |
| hermes 薄壳 -t web 实测 | green-frog 附实测 1 | 架构 B 可行性验证, 含真实输出 |
| provider 配额 429 实锤 | green-frog 附实测 2 | 批量必须显式 pin provider + fallback |
| 成本估算 (1000 篇/月) | red-fox §3 + gold-tiger §7 | C ¥20-60 / A ¥80-250 / 5 万是乱跑上限 |
| 延迟 P50/P95 | gold-tiger §3.1 (推断) | turbo 8-20s / agent 20-40s / moni 自建埋点后才能定 |

---

## 二. arch 共识结论 (待 dad 拍板)

### 2.1 已确定 (不需拍)

| 项 | 拍板 | 依据 |
|----|------|------|
| evidence 落库表 | **新建独立表 `fact_check_evidence`** | 3 份报告一致 (red-fox L195 / gold-tiger §8 / green-frog B2), 独立表可 SQL 检索聚合 (按 URL/按 article_id), JSON blob 做不到 |
| evidence 字段 | query / url / domain / snippet / fetched_at / model / provider / conclusion / rationale + job_id + evidence_seq + article_id | 覆盖 brief §1.4 全部必存项 |
| method 收敛 | `llm_inference` / `web_search` / `human` 三值 | schema.sql L26 已预留, 跟现有写法对齐 |
| 存量 `'llm'` 迁移 | migration 025 + `UPDATE articles SET fact_check_method='llm_inference' WHERE fact_check_method='llm'` (9 行) | green-frog 实查 |
| 修 worker 不写 has_fact_check=1 | jobs.py:331-341 写库分支加 `has_fact_check=1` | green-frog A4-3 实查遗漏 |
| 废弃 `/api/llm/fact-check` 死代码 | route 返回 410 或删 (无前端调用者, model 解析坏) | green-frog A4-1 实查 + A5 |
| content 截断 + 域白名单 | 域白名单 = pbc.gov.cn / cbirc.gov.cn / csrc.gov.cn / samr.gov.cn / 交易所 / 上市公司公告域 / 官方媒体 | red-fox L187 / gold-tiger §8 / green-frog B6 |
| evidence UI 展开 | query/url/snippet 列表, 每条 URL 可点 | red-fox L195 / green-frog B5 |

### 2.2 需 dad 拍板的 Q1-Q4

#### Q1. 主检索通道 (核心冲突)

| 选项 | 描述 | 改造成本 | 维护成本 | 风险 | 评审应对 |
|------|------|---------|---------|------|---------|
| **A. C 主** (red-fox 派) | moni 自建 query + DDG + gov 域 fetch + 自有 FTS5 | 大 (新写通用 DDG 提取器 + 域白名单 fetch + 编排) | moni 维护爬虫, 与薄壳冲突 | DDG 反爬 (60min 熔断) + 5s 延迟拖批量 | "业界验证" |
| **B. B 主** (green-frog 派) | `hermes -t web` 主力检索, DDG 兜底, 自有 FTS5 补 | 小 (extra_args `-t web` + task_type 分支) | hermes 维护, moni 只做落库 + 校验 | 厂商黑盒 + 引用自编 | "实测可用, evidence 落库同严格" |
| **C. B+C 混合** | hermes -t web 主力, moni 写一套轻量 C 通道兜底, 内容类型分流 | 中 | 双通道维护 | 复杂度 | "按内容类型选最优通道" |
| **D. A 主 + Qwen enable_search** (gold-tiger 派) | 百炼 Qwen-plus / qwen3-max + enable_search + enable_source | 中大 (新增 dashscope hermes provider) | 百炼维护 | 月费 ¥80-250 vs C ¥20-60 | "中文权威源最强 + 引用字段厂商给" |

**arch 推荐**: B (green-frog 实测可用 + 零新依赖 + moni 薄壳延续); 兜底加 C 的 DDG 部分 (anomaly 熔断期); A 路线 (Qwen) 留 P1/P2 看月费接受度。

#### Q2. v1.0.22 范围

| 选项 | 范围 | 工时估 | 月成本 (1000 篇) | 评审覆盖 |
|------|------|--------|------------------|---------|
| **A. 只单篇手动** | 单篇端点 + evidence + UI + 修隐债 | 1.5 天 | ¥20-60 (C) / ¥80-250 (A) | 核心闭环 |
| **B. + 批量端点** | A + 批量 ≤20 篇 + cron 调用预留 | 2.5 天 | 3-5x A | 闭环 + 自动触发 |
| **C. + 简报 web 模式** | B + enrichment 加 `fact_check_mode: web` + 简报正文显示证据 | 3.5 天 | 10x A (全简报) | 完整 flow |
| **D. 全做 + 看板 method 徽标** | C + stats.js method 分布 + 跨源一致性看板 | 4.5 天 | 同 C | 申报 + 落地 + 看板 |

**arch 推荐**: B (单篇 + 批量 + 修隐债)。简报 web 模式留 P1 (v1.0.23), 不在 v1.0.22 范围。批 ≤20 篇同 FastAPI 422 教训 (moni skill pitfall)。

#### Q3. 落库 model 选型 (gold-tiger 评分映射到 moni)

| 选项 | Model | 评分 | moni 角色 | 需新增 |
|------|-------|------|-----------|--------|
| **A. MiniMax-M3 (现状)** | 不联网 | "准确"靠证据约束 | **C 主路径判断** | 0 (现状) |
| **B. Qwen-plus + enable_search** | 百炼 (turbo/agent 策略) | 4.5 / 3.5 / 4 / 5 = **17** | **A 兜底联网** | 新增 dashscope 或 hermes 兼容 provider |
| **C. gemini-2.5-pro + grounding** | Google | 4 / 4 / 2.5 / 3.5 = **14** | A 兜底备选 | 0 (seed 已有 google provider, 但 grounding 需 extra_args) |
| **D. B+C 双通道** | Qwen 主, Gemini 备 | 综合 15.5-17 | 完整覆盖 | 新增 dashscope + google grounding 透传 |

**arch 推荐**: D (Qwen 主联网 + Gemini 备 + M3 判断), 但 v1.0.22 只 ship B (Qwen), Gemini 备留 P1 视 P2-1 验证情况。

#### Q4. 评审反方准备深度

| 选项 | 内容 | 工时 |
|------|------|------|
| **A. UI 展开 + evidence 列表** | red-fox L195 默认; evidence 表 + GET 端点 + 前端折叠区 | 0.5 天 (已含在 Q2-A) |
| **B. + 双闸防幻觉** | red-fox Q2 答: 引用 URL ⊆ 证据表 + 数字 ≥2 源交叉 + 报告期一致性 + 不匹配降级【待核实/存疑】 | +0.5 天 |
| **C. + 人工覆写 draft→verified 状态机** | 与 cases 库同构; UI "确认/驳回" 按钮 (跟 v1.0.20 events review 同模式) | +1.5 天 |
| **D. B + C** | 防幻觉 + 状态机 | +2 天 |

**arch 推荐**: B (双闸是评审反方必答题, 必须做); C 状态机 v1.0.22 不强求 (跟 events review 同源, 留 P1 复用)。

---

## 三. arch 综合方案 (若 Q1=B + Q2=B + Q3=B + Q4=B)

### 3.1 文件改动清单 (v1.0.22 scope)

| 文件 | 类型 | 行数估 | 内容 |
|------|------|--------|------|
| `moni/db/migrations/025_fact_check_evidence.sql` | 新 | 35 | 建 fact_check_evidence 表 + 索引 + 刷存量 `'llm'→'llm_inference'` |
| `moni/llm/jobs.py` | 改 | +30 | task_type 白名单加 `fact_check_plus`; 写库分支加 `has_fact_check=1`; 新分支处理 evidence 落库 |
| `moni/llm/prompts/fact_check_plus.md` | 新 | 50 | 联网版 prompt: 三档标签 + 强制 evidence JSON 输出 (query/url/snippet 自报) |
| `moni/llm/client.py` | 改 | +10 | `call_llm` 增加 `extra_args` 默认值支持 (避免每个调用都写 `-t web`) |
| `moni/web/routes/articles.py` | 改 | +25 | 新增 `POST /api/articles/{id}/fact-check-plus` (复用 326 行 async 模式, task_type=fact_check_plus) |
| `moni/web/routes/fact_check_plus.py` | 新 | 80 | `POST /api/fact-check-plus/batch` + `GET /api/fact-check-plus/evidence/{article_id}` |
| `moni/web/routes/__init__.py` | 改 | +1 | 注册新 router |
| `moni/web/routes/llm.py` | 改 | -25 | 删 `/api/llm/fact-check` 死 route (含测试 if 有) |
| `moni/web/static/index.html` | 改 | +15 | 文章详情 fact-check 区加 "联网核查" 按钮 + 证据链折叠 |
| `moni/web/static/js/app.js` | 改 | +40 | `enqueueLlmJobFactCheckPlus` (复用现有 enqueueLlmJob 模式) + 证据展开渲染 |
| `moni/web/static/css/main.css` | 改 | +20 | `.evidence-*` 类 |
| `moni/share/enrichment.py` | 改 | +5 | `_ensure_fact_check` 加 `mode: 'llm'|'web'` 参数 (默认 llm 保持兼容) |
| `tests/test_fact_check_plus.py` | 新 | 120 | 8-10 个测试: 单篇端点 / 批量 / 证据落库 / 双闸校验 / method 收敛 / 路由 410 |
| `docs/research/2026-08-11-fact-check-plus-{redfox,goldtiger,greenfrog,aggregate}.md` | 已落 | 0 | research 阶段产出, 不上 GH |

**总估**: 5 个新文件 (~285 行) + 6 个改动文件 (~110 行) = ~395 行 net add。

### 3.2 sprint 拆分 (若 Q1-Q4 拍板如上)

- **Day 1** schema + 修隐债 (migration 025 + jobs.py has_fact_check + 删死 route)
- **Day 2** 后端核心 (jobs.py fact_check_plus 分支 + prompt + client.py extra_args)
- **Day 3** 端点 (articles.py 路由 + fact_check_plus.py 路由 + __init__ 注册)
- **Day 4** 前端 (index.html + app.js + main.css) + 测试
- **Day 5** 端到端验证 (curl 5 端点 + pytest + 浏览器手动 1 篇 evidence 展开) + PR + squash + tag v1.0.22 + 重启 8900

### 3.3 风险清单 (arch 角度)

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| provider 配额 429 (green-frog 实测 8-17 重置) | 高 | 批量半失败 | 强制 pin provider + 显式 fallback + jobs.py MAX_ATTEMPTS=2 已有 |
| hermes -t web 引用自编 | 中 | 审计失信 | 双闸: 强制 evidence JSON + URL 域白名单 + snippet 非空校验 |
| DDG lite 60min 熔断 | 中 | 兜底通道中断 | C 主备灾已显式承认; 不当主路, 影响小 |
| 域白名单不全 (遗漏某监管源) | 中 | 【已核实】漏判 | v1.0.22 列可配白名单 (config/fact_check_plus.yaml) + v1.0.23 扩 |
| evidence 表膨胀 | 低 | DB size | 同 article 可多条 evidence_seq; 按 article 清理时级联删 (FK ON DELETE CASCADE 已设) |
| 简报 enrichment 改 mode 影响现有 brief 流程 | 低 | 一键简报 regression | enrichment.py mode 默认 'llm' 保持兼容, 只在 manifest 显式指定才走 web |
| 评审反方追问 model 配置差异 | 中 | 失分 | gold-tiger report §10 引用清单可直接给出 8 个官方 URL 应对 |

---

## 四. 等 dad 拍板 (4 选)

### Q1. 主检索通道

**A.** C 主 (red-fox 派 — moni 自建 DDG + gov fetch)
**B.** B 主 (green-frog 派 — `hermes -t web` 主力)
**C.** B+C 混合 (双通道, 按内容类型分流)
**D.** A 主 (gold-tiger 派 — Qwen-plus + enable_search)

### Q2. v1.0.22 范围

**A.** 只单篇手动 + UI 展开 (1.5 天)
**B.** + 批量 ≤20 篇 (2.5 天) — **arch 推荐**
**C.** + 简报 web 模式 (3.5 天)
**D.** 全做 + 看板 (4.5 天)

### Q3. 落库 model

**A.** MiniMax-M3 (C 路径判断, 不联网) — 必选, 无成本
**B.** Qwen-plus + enable_search (A 兜底) — **arch 推荐**
**C.** gemini-2.5-pro + grounding (备选)
**D.** B+C 双通道

### Q4. 评审反方准备深度

**A.** UI 展开 + evidence 列表 (0.5 天, 默认)
**B.** + 双闸防幻觉 (+0.5 天) — **arch 推荐**
**C.** + 人工覆写状态机 (+1.5 天)
**D.** B + C (+2 天)

---

## 五. 拍板后续 (arch 即将做的事)

按你拍板的 Q1-Q4:
1. 写 `docs/plans/2026-08-11-fact-check-plus-spec.md` — tech spec (MOA 不重跑, scope 已收敛)
2. feature 分支 `feature/v1.0.22-fact-check-plus` 走 sprint workflow
3. 5 天 sprint 实施 (按 §3.2)
4. PR merge + tag v1.0.22 + 重启 8900
5. 同步 `docs/research/` (不上 GH) + `STATUS.md` / `USER_GUIDE.md` (上 GH) + `vibe-coding-log.md` (本地)

**Status**: 聚合稿完, 等 dad 4 选.