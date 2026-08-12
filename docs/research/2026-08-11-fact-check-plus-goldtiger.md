# 事实核查+ 联网 model 选型 (gold-tiger)

> 作者: gold-tiger (grok worker) | 日期: 2026-08-11  
> 任务: 联网事实核查 model 选型 + 准确性/速度/成本/中文能力 4 维评分  
> Brief: `docs/plans/2026-08-11-fact-check-plus-research.md`  
> 对照: `docs/research/2026-08-11-fact-check-plus-redfox.md`  
> 查询日期: **2026-08-11**

## 证据层级约定

| 标记 | 含义 |
|------|------|
| **[官方能力表]** | 厂商/平台官方文档或控制台能力表原文 (本 worker 实际打开或检索摘要可交叉) |
| **[实测验证]** | 本 worker 本轮实际执行的检索 / 页面抓取, URL + snippet 可复现 |
| **[推断]** | 基于官方能力 + 公开定价 + 检索后端特性的分析判断, 非厂商承诺 |

汇率口径 (成本估算用): **1 USD ≈ 7.2 CNY** (2026-08-11 约值, [推断]).  
成本假设: **强需求联网核查 1000 篇/月**, 每篇约 1 次联网 + 判断 (input ~4k tokens / output ~1k tokens, 含证据).

---

## TL;DR (拍板)

| 角色 | 模型 | 理由 (一句话) |
|------|------|---------------|
| **主推 (联网 A 兜底 / 中文权威)** | **qwen-plus 或 qwen3-max** (`enable_search: true` + DashScope `enable_source: true`) | 百炼官方支持联网 + 中文源 + 可返回引用; 与 hermes/国内链路可对齐 |
| **主推 (C 架构判断模型, 与 red-fox 对齐)** | **MiniMax-M3 (现役)** | 不联网也够做"只读证据判断"; 成本最低; moni seed 已默认 |
| **Fallback (联网)** | **deepseek-v4-pro (百炼挂联网)** 或 **gemini-2.5-pro + Google Search grounding** | 前者中文+百炼同通道; 后者 seed 已有、引用强、中文 gov 次之 |
| **不推 (联网核查主模型)** | 阿里云托管 **MiniMax-M3** 做联网; **openrouter:gemini-*** 当原生 grounding; **纯 Grok** 做监管类; **纯 GPT-5 search 全量** | 见第 6 节 |

与 red-fox 架构拍板一致: **C 为主 (应用侧检索) 时判断模型用 MiniMax-M3; A 兜底时用 Qwen enable_search; 不要用百炼 MiniMax-M3 当联网引擎.**

---

## 1. 关键盲点 1: MiniMax-M3 是否"不支持联网搜索"?

### 结论 (分层)

| 渠道 | 联网搜索 | 证据 |
|------|----------|------|
| **阿里云百炼托管版** (`MiniMax/MiniMax-M3`) | **不支持** | **[官方能力表]** |
| 百炼"支持联网的第三方模型"列表 | 仅列 **MiniMax-M2.1**, **无 M3** | **[官方能力表]** |
| MiniMax 官方 API / Agent 工具链 | Function Calling 有; 部分第三方称 Token Plan 可挂 search | **[官方能力表]** tool 能力; 联网细节 **[推断]** 非百炼同参 |
| moni 默认 seed 路径 | `minimax-cn:MiniMax-M3` 走 hermes, **无 enable_search 透传** | moni 代码 `seed_llm.py` + `call_llm` 薄壳 |

### 官方原文要点

1. 百炼 MiniMax-M3 模型信息页能力表: **联网搜索 = 不支持**  
   URL: https://help.aliyun.com/zh/model-studio/minimax-m3  
   查询日期: 2026-08-11  
   **[官方能力表]** 原文能力行: `联网搜索 | 不支持`

2. 百炼 MiniMax 总页"其它功能"表:  
   `MiniMax/MiniMax-M3 | ... | 联网搜索 | 不支持`  
   URL: https://help.aliyun.com/zh/model-studio/minimax-api-by-minimax  
   查询日期: 2026-08-11  
   **[官方能力表]**

3. 百炼联网搜索"支持的模型"第三方列表:  
   `DeepSeek: deepseek-v4-pro ...` / `Kimi: Moonshot-Kimi-K2-Instruct` / `MiniMax: MiniMax-M2.1`  
   **没有 MiniMax-M3**  
   URL: https://help.aliyun.com/zh/model-studio/web-search  
   查询日期: 2026-08-11  
   **[官方能力表]**

4. 百炼错误码: 对不支持模型设 `enable_search: true` 会报  
   `This model does not support enable_search`  
   URL: https://help.aliyun.com/zh/model-studio/error-code  
   查询日期: 2026-08-11  
   **[官方能力表]**

5. 定价 (百炼 M3, 非联网): 输入 **¥4.2 / 百万 tokens**, 输出 **¥16.8 / 百万 tokens**, 缓存命中 ¥0.84  
   URL: https://help.aliyun.com/zh/model-studio/minimax-m3  
   **[官方能力表]**

### 回答 brief 问题

> moni 当前 default model (MiniMax-M3 阿里云托管版) 是否真的"不支持联网搜索"?

**是. 在 moni 实际使用的阿里云/百炼托管路径上, 官方明确不支持联网搜索.**  
不是"某些渠道不支持、某些支持"的模糊说法: **百炼能力表写死不支持; 联网白名单里也没有 M3.**  
若走 MiniMax 官方 API 做 agent tool use (B), 那是另一条集成路径, **不是 moni 现役 seed 默认, 且不能等同于 `enable_search` 一键联网.** **[推断]**

---

## 2. 关键盲点 2: moni 现成 4 个 seed model 联网能力

Seed 定义见 `moni/db/seed_llm.py` (provider/model 四元组).

| Seed | 联网? | 怎么开 | 实际能力 vs 营销 | 证据 |
|------|-------|--------|------------------|------|
| **minimax-cn:MiniMax-M3** | **否 (百炼)** | 无 `enable_search` | 营销: coding/agent/多模态强; **联网不在百炼能力表** | 见 §1 **[官方能力表]** |
| **google:gemini-2.5-flash-lite** | **是 (官方 Gemini API)** | Grounding with Google Search tool (`google_search` / grounding) | 支持; 便宜快; 中文 gov 召回依赖 Google 索引 | https://ai.google.dev/gemini-api/docs/google-search **[官方能力表]**; Flash 系 grounding 见 pricing 页 |
| **google:gemini-2.5-pro** | **是** | 同上 | 支持; seed 已标 `use_for: fact_check`; 中文综合强于 flash-lite **[推断]** | 官方 supported models 表含 Gemini 2.5 Pro **[官方能力表]** |
| **openrouter:google/gemini-2.5-flash-lite** | **不确定 / 不可依赖** | OpenRouter 不保证透传 Google Search grounding | 社区 issue 显示 via OpenRouter 开 grounding 不完整 | StackOverflow / OpenRouter issues **[第三方]**; 当 moni 联网主通道 **不推** |

### Gemini 定价 (联网相关)

- Grounding with Google Search (Gemini 2.5 系): 约 **1,500 RPD free (共享), 其后 $35 / 1,000 grounded prompts**  
  URL: https://ai.google.dev/gemini-api/docs/pricing  
  查询日期: 2026-08-11  
  **[官方能力表]** (摘要自定价页与第三方交叉)

### moni 集成现实 **[推断]**

- moni 薄壳 `hermes chat --provider X -m Y` **默认不传** Gemini grounding / 百炼 `enable_search`.  
- 要启用 A 模式, 必须改 client 透传 extra args (green-frog 落地项), 或改走应用侧 C 检索.

---

## 3. 国内候选 (enable 方式 / 延迟 / 单价)

### 3.1 qwen-plus / qwen3-max (百炼) — **主推 A**

| 项 | 内容 | 层级 |
|----|------|------|
| 怎么开 | Chat Completions: `extra_body={"enable_search": True}`; DashScope: `enable_search=True`; 引用: `search_options.enable_source=true` (DashScope); 策略: `search_strategy` = `turbo` (默认) / `max` / `agent` / `agent_max` | **[官方能力表]** https://help.aliyun.com/zh/model-studio/web-search |
| 域过滤 | 文档写 DashScope 支持"限定搜索来源站点" | **[官方能力表]** 同页功能表 |
| Token 价 (量级) | Qwen-Plus 约 **¥0.8 入 / ¥2–8 出 / 百万** (版本与活动浮动); Qwen3-Max 更高 (约 ¥2.5+ 入, 旗舰档) | **[第三方/控制台]** 实施前以 https://help.aliyun.com/zh/model-studio/model-pricing 复核 |
| 联网附加费 | 基础 turbo 联网随模型; `agent` / `agent_max` **每次调用额外收费** (文档锚点"计费说明"); 独立"千问联网检索 Agent"产品基础 **¥22/千次** | **[官方能力表]** web-search 页 + https://help.aliyun.com/zh/model-studio/web-search-agent-guide |
| 延迟 P50/P95 | turbo: **[推断]** P50 ~8–20s, P95 ~30–45s; agent/agent_max: P50 ~20–40s, P95 60s+ | 无公开 SLA; 官方写 max/agent 更慢 **[官方能力表]** |
| 中文 | 国内搜索后端 + 中文训练, 监管/财经类相对最强 **[推断]** | 官方示例返回含中文站链接 **[官方能力表]** |

### 3.2 deepseek-v4-pro

| 项 | 内容 | 层级 |
|----|------|------|
| 原生 DeepSeek API | **无官方内置 web_search 参数** (定价/模型页未列 search tool) | **[官方能力表]** https://api-docs.deepseek.com/zh-cn/quick_start/pricing/ |
| 百炼挂联网 | **支持**: 第三方模型列表含 `deepseek-v4-pro` + `enable_search` | **[官方能力表]** web-search 页 |
| 单价 | 官方: 输入(缓存未命中) **¥3 / 百万**, 输出 **约 ¥2–6 量级** (中文定价表 cache miss 入 3 元; 国际 $0.435/$0.87 per 1M) | **[官方能力表]** DeepSeek pricing |
| 延迟 | 思考模式默认, **[推断]** 联网后 P50 15–35s, P95 45–70s | 无官方 P50 表 |
| 适合 moni | **Fallback A**: 同百炼通道、便宜、推理强; 引用字段依赖百炼 `enable_source` | **[推断]** |

### 3.3 文心 4.0 Turbo / ERNIE 系

| 项 | 内容 | 层级 |
|----|------|------|
| 怎么开 | 千帆/AI Studio: body `web_search: { enable: true, enable_citation, enable_trace, search_num, ... }` | **[官方能力表]** https://ai.baidu.com/ai-doc/AISTUDIO/rm344erns (文档示 ernie-4.5 等) |
| 单价 (历史/转售) | ERNIE 4.0 Turbo 量级约 **¥20–60 / 百万 tokens 入出** (转售表); 联网约 **¥0.008/次** 量级 (第三方转售) | **[第三方]** 需千帆现价复核 |
| 中文 | 百度搜索生态, 中文召回理论强 | **[推断]** |
| moni 适配 | moni seed **无** 文心 provider; 需新 hermes provider + key | **[推断]** |
| 拍板 | **本轮不推为主推** (集成成本 + API 形态与 hermes 薄壳摩擦), 可作远期候选 | |

### 3.4 Kimi K2 / K2.5 / K2.6 / K3

| 项 | 内容 | 层级 |
|----|------|------|
| 怎么开 | `tools: [{ type: "builtin_function", function: { name: "$web_search" } }]`, 调用方回传 arguments 即可 | **[官方能力表]** https://platform.kimi.ai/docs/guide/use-web-search |
| 单价 | K2.5 约 **$0.60/$3.00 per 1M**; K2.6 约 **$0.95/$4.00**; web search **$0.005 / successful call** + 结果 tokens 计入下次 input | **[官方能力表]** https://platform.kimi.ai/docs/pricing/tools |
| 注意 | 部分 K3 文档写 web_search "currently being updated, not recommended near term" | **[官方能力表]** pricing/chat-k3 页警示 |
| 百炼 | `Moonshot-Kimi-K2-Instruct` 可挂百炼联网 | **[官方能力表]** web-search |
| 延迟 | tool 多轮, **[推断]** P50 15–40s | |
| 拍板 | **强 Fallback / 研究型**, 中文好; moni 需新 provider 或百炼 ID | |

### 国内单价速查 (¥ / 1k tokens 换算, 约)

| 模型 | 输入 ¥/1k tok | 输出 ¥/1k tok | 联网附加 (约) |
|------|---------------|---------------|---------------|
| MiniMax-M3 (百炼) | 0.0042 | 0.0168 | 不支持联网 |
| deepseek-v4-pro (官) | ~0.003 | ~0.002–0.006 | 百炼 enable_search 另计策略费 |
| qwen-plus (量级) | ~0.0008 | ~0.002–0.008 | turbo 含/agent 加收 |
| Kimi K2.5 | ~0.0043 ($0.60/M) | ~0.022 ($3/M) | $0.005/次 ≈ ¥0.036/次 |

---

## 4. 国外候选 4 维

### 4.1 OpenAI GPT-5 系 + web search

| 维度 | 评分 (1–5) | 依据 |
|------|------------|------|
| 准确性 | 4 | 引用 + 域过滤 (allowed/blocked domains) **[官方能力表]** https://developers.openai.com/api/docs/guides/tools-web-search ; 中文监管源排序 **[推断]** 弱于国内 |
| 速度 | 3 | reasoning + search 可调; 文档提示 multi-search 可升 latency |
| 成本 | 2 | Web search **$10 / 1k calls** + search content tokens 按模型价 **[官方能力表]** https://developers.openai.com/api/docs/pricing |
| 中文 | 2.5 | 能搜到中文, 但 gov.cn 优先级无承诺 **[推断]** |
| **总分** | **11.5 / 20** | |

### 4.2 Anthropic Claude (Sonnet/Opus 5 系) web search

| 维度 | 评分 | 依据 |
|------|------|------|
| 准确性 | 4.5 | 强制 citations; **allowed_domains / blocked_domains**; dynamic filtering 减噪声 **[官方能力表]** https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool |
| 速度 | 3 | 多轮 server tool, 简单 1–3 次 search |
| 成本 | 2 | **$10 / 1k searches** + token; 检索结果 encrypted 对应用侧审计不友好 **[官方能力表]** + 第三方 Firecrawl 对比 |
| 中文 | 2.5 | 同 GPT, 中文金融次优 **[推断]** |
| **总分** | **12 / 20** | 审计: encrypted_content 限制应用侧二次校验 |

### 4.3 xAI Grok (grok-4.5 / 4.x) web_search

| 维度 | 评分 | 依据 |
|------|------|------|
| 准确性 | 3 | web_search + 可选 allowed_domains (max 5); X search 强 **[官方能力表]** https://docs.x.ai/developers/tools/web-search |
| 速度 | 3.5 | 实时定位, 无统一 P50 |
| 成本 | 3 | Web Search **$5 / 1k calls** (低于 OpenAI/Claude); grok-4.5 文本 $2/$6 per 1M **[官方能力表]** https://docs.x.ai/developers/pricing |
| 中文 | 2 | 政企/人行/银监命中 **一般** (与 red-fox 一致) **[推断]** |
| **总分** | **11.5 / 20** | moni 银行同业主场景 **不推为主** |

### 国外汇总

| 模型 | 准确 | 速度 | 成本 | 中文 | 总分 | moni 角色 |
|------|------|------|------|------|------|-----------|
| Claude + web search | 4.5 | 3 | 2 | 2.5 | **12** | 英文长尾 / 域白名单 A 兜底 |
| GPT-5 + web search | 4 | 3 | 2 | 2.5 | **11.5** | 同左, moni 无 seed |
| Grok + web_search | 3 | 3.5 | 3 | 2 | **11.5** | X 舆情补充, 非监管主路径 |

---

## 5. 关键盲点 5: 中文权威源命中 — 真实 query

> 说明: moni 本环境**未对 8 家模型逐家打付费 API** (无统一 key 矩阵).  
> 以下 **[实测验证]** = 本 worker 对公开网页的检索结果 (与模型厂商搜索后端高度相关: 国内引擎 ≈ Qwen/文心/Kimi 倾向; Google ≈ Gemini grounding).  
> 模型层最终 snippet 需 sprint 内用 moni 测试账号做 **A/B 实测矩阵** (见 §8 待办).

### Query A (监管类): `人行 2026 数字人民币最新政策` / `央行 数字人民币 2.0 运营机构`

| 来源 | URL | Snippet (摘要) | 权威度 |
|------|-----|----------------|--------|
| 新华网 | https://www.news.cn/20251229/442a77288543426294939796802ac43e/c.html | 人行行动方案: 2026-01-01 起新一代数字人民币计量框架等启动; 从数字现金迈入数字存款货币 | 官方媒体 |
| 人民日报 | http://paper.people.com.cn/rmrb/pc/content/202512/31/content_30128236.html | 2026 年起钱包余额可计息; 行动方案政策速递 | 官方媒体 |
| 上海金融官网 | https://jrj.sh.gov.cn/ZXYW178/20260403/7c7eb17d655d4b46a6d4bb3335d5d8a2.html | 人行新增 12 家数字人民币业务运营机构 | 政府站 |
| 央广网 | https://finance.cnr.cn/ycbd/20260403/t20260403_527572834.shtml | 新增中信/光大/华夏等 12 家; 扩至股份行+城商行 | 官方媒体 |

跨源一致事实 **[实测验证]**:  
(1) 2026-01-01 数字人民币 2.0 / 计息;  
(2) 2026-04 前后运营机构扩容 +12 家.  

**对 model 的含义 [推断]**:

| 后端类型 | 命中预期 |
|----------|----------|
| 百炼 Qwen / 文心 / Kimi 国内搜索 | 高概率 top 结果含新华/人日/政府站 |
| Gemini Google grounding | 可命中, 但排序常夹杂国际转载 / 百科 |
| GPT/Claude/Grok 通用 web | 可命中, 权威排序不可控; Claude/Grok 可用 allowed_domains 锁 `gov.cn`/`pbc.gov.cn` 等 |

### Query B (银行数字类): `招行 2026 Q1 AUM 零售客户`

| 来源 | URL | Snippet | 权威度 |
|------|-----|---------|--------|
| 财联社 | https://www.cls.cn/detail/2364629 | 招行一季度 AUM 约 **178,551.65 亿元**, 较上年末 +7,726.46 亿, +4.52% | 财经媒体 (数据对齐财报) |
| 华尔街见闻 | https://wallstreetcn.com/articles/3771124 | 零售客户 AUM 17.86 万亿, 单季净增 7,726.46 亿, +4.52% | 财经媒体 |
| 招行 PDF (公司材料) | https://s3gw.cmbimg.com/.../cmbir/20260408/...pdf | 年报语境 AUM 17.08 万亿 (2025 年末口径, 注意与 Q1 区分) | 一手公司披露 |
| 雪球转述 | https://xueqiu.com/1620956658/403287023 | 2026-03 末 AUM 17.86 万亿, +4.52% | 二级转载 |

跨源一致数字 **[实测验证]**: Q1 AUM **~17.86 万亿 / 178,551.65 亿元**, 增幅 **4.52%**.  
注意: 年报 17.08 万亿 vs 季报 17.86 万亿是**不同时点**, 核查必须锁报告期 — 这是幻觉高发点.

**对 moni 的含义**: 具体数字类必须 **≥2 独立源交叉 + 报告期标注**, 与 red-fox 双闸一致; 模型 alone 不够.

### "按 model 抓检索结果" 诚实边界

| 要求 | 本报告状态 |
|------|------------|
| 每个 model 的真实 API 返回 URL+snippet | **未完成付费矩阵** — 需 moni hermes 账号 sprint 内跑 |
| 等价替代 | 公开检索 + 官方"搜索返回 search_info"能力证明 Qwen DashScope 可返回 title/url 列表 **[官方能力表]** |
| 建议验收 query | 上列 A/B 两 query 固定进 moni 回归 fixture |

---

## 6. 4 维对比总表 (联网事实核查场景)

评分: 5 最优. 总分 20. 场景 = moni 银行同业中文 claim 联网核查.

| 模型 | 准确 | 速度 | 成本 | 中文 | **总分** | 角色 |
|------|------|------|------|------|----------|------|
| **qwen-plus / qwen3-max + enable_search** | 4.5 | 3.5 | 4 | **5** | **17** | **主推 A** |
| **deepseek-v4-pro + 百炼 search** | 4 | 3 | 4.5 | 4 | **15.5** | Fallback A |
| **gemini-2.5-pro + Google grounding** | 4 | 4 | 2.5 | 3.5 | **14** | Fallback A (seed 已有) |
| **kimi + $web_search** | 4 | 3 | 3.5 | 4.5 | **15** | Fallback A (需新 provider) |
| **MiniMax-M3 (仅判断, 不联网)** | 4* | 4 | **5** | 4.5 | **17.5*** | **主推 C 判断** (*准确依赖应用侧证据) |
| Claude + web_search | 4.5 | 3 | 2 | 2.5 | 12 | 英文/域过滤兜底 |
| GPT-5 + web_search | 4 | 3 | 2 | 2.5 | 11.5 | 不推全量 |
| Grok + web_search | 3 | 3.5 | 3 | 2 | 11.5 | 不推监管主路径 |
| 文心 4 turbo + web_search | 4 | 3.5 | 3 | 4.5 | 15 | 远期, 本轮不推集成 |
| **MiniMax-M3 当联网主引擎** | 1 | — | — | — | **失格** | **不推** |
| openrouter gemini 当 grounding | 2 | 3 | 3 | 3 | **11** | **不推** |

\* MiniMax-M3 作为 C 架构判断模型时, "准确"来自证据约束而非内置搜索.

---

## 7. 成本估算 (1000 篇/月强需求联网)

假设: 每篇 1 次联网 + 1 次综合输出; 证据 token 计入 input.

| 路径 | 组成 | 约月费 (CNY) | 层级 |
|------|------|--------------|------|
| **C 主路径** (DDG/自有 fetch + MiniMax-M3 判断) | 检索≈0 + M3 ~4k in + 1k out ×1000 | **¥20–60** | **[推断]** 与 red-fox 同量级 |
| **A: Qwen-plus turbo + enable_search** | token + 联网(基础) | **¥80–250** | **[推断]** |
| **A: Qwen agent 策略 / 联网 Agent 产品** | agent 额外 + token | **¥200–500+** (Agent 基础 ¥22/千次 → ¥22 起) | **[官方能力表]** + 推断 |
| **A: Gemini 2.5 Pro grounding** | $35/1k prompts 超 free 后 + token | **¥200–400+** (超 free quota 后贵) | **[官方能力表]** + 推断 |
| **A: Claude/GPT web search 全量** | $10/1k search ≈ ¥72 + token | **¥150–400+** | **[官方能力表]** |
| **A: Grok web_search** | $5/1k ≈ ¥36 + token | **¥80–250** | **[官方能力表]** |
| 反方假想"5 万/月" | 仅当 **全量每篇多轮 agent_max + 旗舰模型 + 无分流** | 可达数千元级, **难到 5 万** 除非全库无差别联网 + 人工编辑部 | **[推断]** |

**结论**: 按 brief 只对强需求类型联网, **合理月费应在 ¥50–300**, 远低于 5 万. 成本爆炸来自"全量篇 + agent_max + 无缓存", 不是选型本身.

---

## 8. 关键盲点 6: 评审反方三问 (正面回答)

### Q1: "你 moni 怎么保证检索源权威?"

**不靠模型自觉, 靠架构分层** (与 red-fox 一致, 补 model 层):

1. **检索层**: 域白名单 (`pbc.gov.cn` / `gov.cn` 监管域 / 交易所 / 上市公司公告 / 认证官媒). Claude/Grok 的 `allowed_domains`、百炼"限定搜索来源站点"是 A 路径的厂商支持; **C 路径白名单由 moni 自己 enforce, 更可控.**  
2. **模型层**:  
   - A 兜底优先 **Qwen + enable_source**, 强制落库 search_info URL.  
   - 判断模型 (MiniMax-M3) prompt: **只能引用证据列表 URL, 禁止编造**.  
3. **后验闸**: 引用 URL ⊆ 证据表; 数字类 ≥2 源交叉; 否则降级【待核实】/【存疑】.

业界对应: FactCheck.org 一手源优先 (https://www.factcheck.org/our-process/) **[官方能力表/方法学]**.

### Q2: "幻觉怎么办?"

1. **生成前**: 无证据不得【已核实】; MiniMax-M3 不联网则**无法用训练记忆冒充检索**.  
2. **生成后**: URL 映射校验 + 数字交叉 + 报告期一致性 (Query B 年报/季报陷阱).  
3. **model 选型贡献**: 选能返回 **结构化 search_info / citations** 的通道 (Qwen DashScope, Claude citations, Gemini grounding metadata), 拒绝"只吐结论无 URL"的模式.

### Q3: "5 万/月成本值不值?"

**不需要 5 万.**  
按 §7: C 主路径几十元; A 兜底百元级.  
5 万/月对应的是"全量每篇多轮旗舰 agent + 人工编辑部"级别, moni v1.0.22 范围 (仅强需求类型) **不需要**.  
值不值: 强需求监管/数字 claim 若错, 同业简报风险远高于 ¥200 级 API — **值得做分流联网, 不值得无差别全量联网.**

---

## 9. moni 拍板 (可执行)

### 9.1 主推

| 用途 | Model | Provider 建议 |
|------|-------|----------------|
| **联网检索综合 (A 兜底)** | **qwen-plus** (日常) / **qwen3-max** (高难度 claim) | 新增 hermes/百炼 `dashscope` 或现有兼容通道; `enable_search=true`, DashScope `enable_source=true`, 策略默认 `turbo`, 失败升级 `max` |
| **证据判断 (C 主路径)** | **MiniMax-M3** (保持 seed 默认) | `minimax-cn` 现役; **只喂证据, 不开联网** |
| **claim 抽取 / 弱需求** | MiniMax-M3 或 gemini-2.5-flash-lite | 成本优先 |

### 9.2 Fallback

1. **deepseek-v4-pro + 百炼 enable_search** — 同通道, 便宜, 推理好.  
2. **gemini-2.5-pro + Google Search grounding** — moni seed 已有; 适合跨境/英文辅证; 注意 grounding 单价.  
3. **kimi $web_search** — 中文好; 需新 provider.  
4. **Claude allowed_domains** — 仅当 C+Qwen 都 miss 且需要硬域过滤时.

### 9.3 不推 (明确)

| 不推项 | 理由 |
|--------|------|
| **MiniMax-M3 作联网主模型** | 百炼官方 **联网搜索=不支持** **[官方能力表]** |
| **openrouter gemini 当原生 grounding** | 透传不可靠 **[第三方]** |
| **Grok 作监管/银行数字主核** | 中文权威源弱; 更适 X 舆情 **[推断]** |
| **GPT-5 search 全量 1000 篇** | 搜索费 + 中文弱 + moni 无 seed |
| **文心为主推 v1** | 集成摩擦 > 收益; 能力可, 节奏不合适 |
| **无分流全量 agent_max** | 成本/延迟失控 |

### 9.4 与 red-fox 对齐一句话

> **C 路径判断 = MiniMax-M3; A 兜底联网 = Qwen enable_search; 禁止把默认 M3 误认为已联网.**

### 9.5 Sprint 待办 (给 green-frog / arch)

1. hermes/client 支持透传 `enable_search` / Gemini grounding tools.  
2. seed 增加 `qwen-plus` (或 dashscope) 作为 `fact_check_plus` 专用 model.  
3. 固定 A/B 两 query 做 3 模型 API 回归 (Qwen / DeepSeek-百炼 / Gemini-pro), 落 `fact_check_evidence` 样例.  
4. 延迟打点: moni 内 P50/P95 (本报告延迟列为推断).

---

## 10. 主要引用清单 (URL + 查询日期 2026-08-11)

| 主题 | URL | 层级 |
|------|-----|------|
| MiniMax-M3 能力/定价 (联网不支持) | https://help.aliyun.com/zh/model-studio/minimax-m3 | 官方 |
| MiniMax 百炼功能表 | https://help.aliyun.com/zh/model-studio/minimax-api-by-minimax | 官方 |
| 百炼联网搜索 (模型白名单/参数) | https://help.aliyun.com/zh/model-studio/web-search | 官方 |
| 百炼错误码 enable_search | https://help.aliyun.com/zh/model-studio/error-code | 官方 |
| 千问联网检索 Agent 定价 | https://help.aliyun.com/zh/model-studio/web-search-agent-guide | 官方 |
| Gemini Google Search grounding | https://ai.google.dev/gemini-api/docs/google-search | 官方 |
| Gemini pricing | https://ai.google.dev/gemini-api/docs/pricing | 官方 |
| Claude web search | https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool | 官方 |
| OpenAI web search + pricing | https://developers.openai.com/api/docs/guides/tools-web-search ; https://developers.openai.com/api/docs/pricing | 官方 |
| xAI web search + pricing | https://docs.x.ai/developers/tools/web-search ; https://docs.x.ai/developers/pricing | 官方 |
| Kimi $web_search | https://platform.kimi.ai/docs/guide/use-web-search ; https://platform.kimi.ai/docs/pricing/tools | 官方 |
| DeepSeek pricing | https://api-docs.deepseek.com/zh-cn/quick_start/pricing/ | 官方 |
| 文心 web_search 参数 | https://ai.baidu.com/ai-doc/AISTUDIO/rm344erns | 官方 |
| moni seed | `moni/db/seed_llm.py` | 代码 |
| 实测 Query A/B | 见 §5 表内 URL | 实测 |

---

## 附: 对 arch 聚合稿的输入 (备忘)

1. **主推 A**: qwen-plus / qwen3-max + `enable_search` + `enable_source`.  
2. **主推 C 判断**: MiniMax-M3 (现状, 不联网).  
3. **Fallback**: deepseek-v4-pro (百炼) > gemini-2.5-pro grounding > kimi $web_search.  
4. **不推**: 百炼 M3 联网; openrouter grounding 当主; Grok/GPT 全量中文监管.  
5. **成本**: 分流后月费应 **¥50–300** 量级, 不是 5 万.  
6. **延迟**: 公开 SLA 缺失; moni 必须自建 P50/P95 埋点后才能写 SLA.  
7. **与 red-fox**: 完全兼容 D = C 主 + B 补 + A 兜底; 本文补齐 model 维.

---

**Status**: DONE gold-tiger research.  
**输出文件**: `docs/research/2026-08-11-fact-check-plus-goldtiger.md`
