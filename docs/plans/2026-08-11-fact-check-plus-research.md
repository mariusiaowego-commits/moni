# 事实核查+ research brief (v1.0.22 准备)

> 写于 2026-08-11 moni 同业信源 sprint 准备阶段。
> 派给 3 worker (red-fox/gold-tiger/green-frog) 的研究 brief。
> Arch (blue-owl) 同步 prep 完直接派, 不再二次确认.

---

## 0. moni 现状 (arch 亲自 grep 摸出来, 已确证)

### 0.1 三套事实核查调用链 (dual code path)

| 入口 | 文件 | Prompt | 输出字段 |
|------|------|--------|---------|
| 一键简报 (brief 模式) | `moni/share/enrichment.py:33` `FACT_CHECK_PROMPT` + `_ensure_fact_check()` | 内嵌**短版**, 三档 【已核实】/【待核实】/【存疑】, 限 2 claim, JSON 写 `conclusion/method/note` | `fact_check_conclusion`/`fact_check_method`/`fact_checked_at`/`fact_check_note` + `has_fact_check=1` |
| 单篇手动核查 (同步) | `moni/web/routes/llm.py:88` `POST /api/llm/fact-check` 调 `render_fact_check(article)` | 调 `moni/llm/prompts/fact_check.md` **长版** (200-400 字 + 引用风险 + 结论分高/中/低) | `fact_check_conclusion`/`fact_check_method='llm_inference'` (routes 写 llm, enrichment 写 llm_inference — **不一致**) |
| 后台 worker (异步) | `moni/llm/jobs.py:274` `task_type='fact_check'` → `render_fact_check(article_dict)` | 同长版 | 同上 |

**3 套调用, 2 套 prompt (短 vs 长), method 字段两套值 ('llm' vs 'llm_inference'). 任何 sprint 实施都必须先收敛这 3 套入口.**

### 0.2 schema 现状 (migration 020/021/022 + schema.sql)

```sql
-- articles 表已有的 fact_check_* 列 (schema.sql L22-25)
fact_check_conclusion   TEXT,        -- 结论短文本, NULL = 未做
fact_check_method       TEXT,        -- schema 预留 'llm_inference' | 'web_search' | 'human'
                                        -- ⚠️ 实际只有 'llm'/'llm_inference' 在用, 'web_search' 是空值占位
fact_checked_at         TEXT,
fact_check_note         TEXT,
```

### 0.3 moni 现有 LLM 链路 (薄壳模式)

- `moni/llm/client.py:call_llm(provider, model, prompt)` → subprocess `hermes chat -q <prompt> --provider X -m Y --profile Z -Q`
- moni **不持有任何 API key**, 全在 `~/.hermes/config.yaml` / `~/.hermes/.env`
- seed 默认 provider (3 个) + model (4 个): `minimax-cn:MiniMax-M3` / `google:gemini-2.5-flash-lite` / `google:gemini-2.5-pro` / `openrouter:google/gemini-2.5-flash-lite`
- `routes/llm.py:11` `_DEFAULT_MODEL = os.environ.get("MONI_LLM_MODEL", "claude-3-5-sonnet-latest")` — 路由默认指 claude-3-5 但 seed 没 seed 这个 (潜在 latent bug)
- routes 端点: `/api/llm/summary` / `/api/llm/fact-check` / `/api/llm/providers[CRUD]` / `/api/llm/models[CRUD]` / `/api/llm/settings` / `/api/llm/usage` / `/api/llm/token-log`

### 0.4 已有"应用侧检索"通道 (可复用)

- `moni/fetch/` 已有 11 个 source fetcher: `mpaypass` (银行一手) / `qbitai` / `36kr_rss` / `huxiu` / `wechat_rss_a/b` / `bestblogs` (375 公众号 OPML) / `bestblog-podcast/x/yt` / `xwatch` (DDG lite + syndication)
- `xwatch` v1.0.11+ DDG lite 通道可外部检索 (现成的"应用侧检索"基础)
- `moni/web/static/css/main.css` 已有 GSAP 动效 + Alpine SPA, 前端基础够

### 0.5 视角系统 (按 tag 差异化)

- `moni/llm/prompts/perspectives.py` (二进制 read_file 误判, 需 terminal cat 读) — 按 tag (banking / wechat / ai-fintech 等) 选不同 perspective 的 summary prompt
- 这是 "事实核查+ 按 tag/来源差异化" 的现成范式, 复用成本低

### 0.6 现有测试覆盖

- `tests/test_prompts.py` 8+ 个 case 覆盖 `render_fact_check` (可导入 / 模板填充 / 含 fact-checking 关键词 / 空 dict 仍 render)
- `tests/test_api.py` 覆盖 `include_fact_check` payload 字段
- `tests/test_brief.py` 覆盖 `include_fact_check=True` 的 brief payload
- 现状: 655 passed + 2 skip (v1.0.21 PR #82 merge 后)

---

## 1. 联网事实核查的核心研究问题

### 1.1 业界联网核查模式 (research agent 必答)

业界做事实核查 + 联网能力的产品/技术模式是什么? moni 该学哪种?

候选维度 (research 必覆盖, 不限于):
- **A. 模型内置联网**: gpt-5 search-preview / claude-4 with web search / grok-4 real-time / 文心 4 turbo / 通义 qwen-max-search / kimi-k2 search
- **B. Agent tool use**: 模型本身不联网, 系统给 search/fetch 工具 (function calling), 模型判断何时查
- **C. 应用侧 RAG/检索**: moni 自己 fetch + 索引 (DDG / Firecrawl / 央行公告 API / Wind 公开 API), 模型只做证据综合判断
- **D. 混合**: A+B+C 组合, 按内容类型走不同路径

### 1.2 内容类型差异化 (research agent 必答)

什么类型内容**必须**联网核查+, 什么类型**不必**?

候选分类:
- **强需求联网核查+**:
  - 监管处罚 (人行 / 银保监 / 证监 公告原文)
  - 央行公告 (数字人民币 / 跨境支付 / 准备金率调整)
  - 银行/支付机构并购重组
  - "首家 / 第一 / 突破" 排名声明
  - 数字人民币 / 稳定币 / CBDC 政策
  - 具体数字 (AUM / 营收 / 用户数 / 市占率)
- **弱需求 (现有 LLM 推断够用)**:
  - 行业评论 / 趋势分析 (主观 + 公开知识)
  - AI 论文 / 技术博客 (公开知识可推)
  - 个人观点 / 深度长文
  - 已核实过的官方稿件二次传播
- **不适配 (跳过联网)**:
  - 纯观点文 (LLM 标"无需核查")
  - 内容太短 (< 200 字)
  - 公众号软文 / PR 通稿

### 1.3 模型选型 (model agent 必答)

联网事实核查 + 适合用哪个 model? 4 维度评分:

| 维度 | 关键问题 | 评分项 |
|------|---------|--------|
| 准确性 | 检索来源权威性 (gov / 一手新闻 / 维基) vs 自媒体 | 引用可追溯性 + 错误率 |
| 速度 | 联网 + LLM 综合 30s 内 vs 60s+ | P50/P95 latency |
| 成本 | 每篇 ¥0.X | 月费成本估算 (假设 1000 篇/月) |
| 中文能力 | 国内权威源支持 (gov.cn / pbc.gov.cn / 央行 / 监管) | 中文检索 + 中文回答质量 |

候选模型 (4 家起步):
1. **gpt-5** (openai) — 联网 search-preview
2. **claude-4-sonnet** (anthropic) — web search tool
3. **grok-4** (xai) — real-time (X/Twitter 强, 政企一般)
4. **国内模型** — qwen3-max-search / 文心 4 turbo / kimi-k2 / deepseek-v4-pro (若有 search)
5. **moni 现成 MiniMax-M3** — 是否已自带联网 (待核)

### 1.4 审计 + 可追溯 (research agent 必答)

事实核查+ 的结论**必须**可被评审/同事质疑时反查。证据链存什么?

必存项:
- 检索 query (用了什么关键词)
- 来源 URL (命中的检索结果)
- 来源 snippet (引用证据片段)
- 模型判断 (结论 + 依据)
- 时间戳 (核查时间)
- model + provider (用什么查的)

存哪里? 复用 `articles.fact_check_note` (json 化) 还是新建 `fact_check_evidence` 表?

---

## 2. 3 worker 分工 (3 specialist 视角)

### 2.1 red-fox (codex) — **业界联网核查产品/技术模式研究**

**任务**: 调研业界 8-10 个联网事实核查产品/技术方案, 输出"moni 该学哪种" 拍板建议.

**reference 必看**:
- 业界事实核查平台: Snopes / FactCheck.org / PolitiFact / Ground News / Snark.news / Meedan Check / FactStream / Tracebook / Deverify / ClaimBuster
- LLM 联网模式文档: gpt-5 search-preview / claude-4 web search / grok-4 real-time / kimi-k2 search / 文心 search
- Agent tool use 框架: LangChain ReAct / AutoGen / Hermes tool calling

**输出物** (写到 `docs/research/2026-08-11-fact-check-plus-redfox.md`):
- 8-10 个参考, 每个 3-5 句话 (URL + 核心做法 + moni 可借鉴/不可借鉴)
- 5 大模式 (A/B/C/D + 混合) 对比矩阵
- moni 推荐方案 (哪 1-2 个模式) + 理由
- 风险/限制 (评委反方角度: "你怎么保证引用权威?" / "幻觉怎么办?")

**禁 emoji. 引用必带 URL.**

### 2.2 gold-tiger (grok) — **联网 model 选型 + 速度/准确性/成本/中文 4 维评分**

**任务**: web search 调研 5-8 个候选 model 的联网能力, 4 维评分, 拍板 moni 该用哪个/哪几个.

**reference 必看**:
- 5-8 个候选: gpt-5 search / claude-4 web search / grok-4 / qwen3-max / 文心 4 turbo / kimi-k2 / deepseek-v4-pro / MiniMax-M3 (待核)
- 官方 pricing 页 + benchmark 页 (中文权威源支持率 / 联网准确率)
- 国内监管源 (gov.cn / pbc.gov.cn / csrc.gov.cn / cbirc.gov.cn) 在各家 model 的检索命中率

**输出物** (写到 `docs/research/2026-08-11-fact-check-plus-goldtiger.md`):
- 5-8 个 model 对比表 (4 维评分 + 总分)
- 每 model 真实测试 query 1-2 个 (例: "人行 2026 年数字人民币最新政策" + "招行 2026 Q1 AUM")
- 拍板 moni 主推 model + fallback model
- 成本估算 (假设 1000 篇/月)

**禁 emoji. 引用必带 URL. 必须有真实测试 query 证据, 不能只引官方宣传.**

### 2.3 green-frog (hermes worker) — **moni 内部基础设施 + 联网核查落地最小侵入点**

**任务**: 读 `moni/llm/` + `moni/fetch/` + `moni/share/` + `moni/db/migrations/` + `moni/web/routes/` 相关文件, 输出 "moni 加联网事实核查+ 的最小侵入点 + schema 改动 + 接口改动" 拍板建议.

**必读清单** (你不需要读我已经读过的, 但必须从架构角度二次审视):
- `moni/llm/client.py` (薄壳模式, 看 extra_args 怎么传)
- `moni/llm/prompts/fact_check.md` (现有长版 prompt)
- `moni/llm/prompts/perspectives.py` (tag → perspective 视角选择, 终端 cat 读)
- `moni/share/enrichment.py:_ensure_fact_check()` (简报路径)
- `moni/share/enrichment.py:run_enrichment()` (orchestration)
- `moni/llm/jobs.py` (后台 worker 路径)
- `moni/web/routes/llm.py:88` (单篇手动路径)
- `moni/fetch/base.py` + xwatch fetcher (DDG lite 通道怎么复用)
- `config/sources.yaml` (11 个 source 的类型)

**输出物** (写到 `docs/research/2026-08-11-fact-check-plus-greenfrog.md`):
- 现有 3 套调用链收敛建议 (统一走哪个, 怎么降 method 字段不一致)
- 新 schema 改动 (新表 vs 复用现有字段)
- 新端点建议 (单篇手动 / 批量 / 后台 worker)
- 是否需要新 llm_jobs task_type ('fact_check_plus'?)
- 前端 UI 改动 (新 fact_check_plus tab / 简报 UI 加 / 看板)
- 风险评估 (成本 / 延迟 / 幻觉 / 评审反方)

**禁 emoji. 引用必带 file:line.**

---

## 3. 派活 + 同步协议

3 worker 并行, 每 worker 收到 prompt = (1) 这份 brief + (2) moni 现状节选 (避免 worker 重新读全部). arch 不二次确认, 等 worker done 聚合.

- 启动超时: 每 worker `--timeout 300000` (5 min)
- prompt 同步 wait: `herdr agent prompt <name> "..." --wait --timeout 600000` (10 min wait)
- 完成后 worker 把 markdown 写到 `docs/research/2026-08-11-fact-check-plus-<name>.md` 然后回 "DONE: <name>"
- arch 收到 3 份 markdown 后合成 `docs/research/2026-08-11-fact-check-plus-aggregate.md` 给 dad 拍板

---

## 4. 时间盒

- arch prep + 派活: 已完成 (本文件)
- 3 worker 并行研究: 1-2 小时
- arch 合成: 30 分钟
- 拍板 + 写 tech spec: 1 小时
- 总预算: 4 小时内到"产品 feature 方案 + tech spec" 给 dad 评估

---

**Status**: prep 完, 立即派 3 worker (A 方案 user 拍板).
