# 事实核查+ 业界模式研究 (red-fox)

> 作者: red-fox (codex worker) | 日期: 2026-08-11
> 任务: 调研业界 8-10 个联网事实核查产品/技术方案 + 三大架构对比 + moni 拍板建议
> Brief: `docs/plans/2026-08-11-fact-check-plus-research.md`

## TL;DR (拍板建议)

moni 该学 **C 为主 (应用侧检索/RAG), B 做补充 (agent tool use), 必要时 A 兜底 (模型内置联网)** 的混合模式, 即 brief 里的 D.

一句话理由: moni 的刚需是**审计可追溯 + 中文监管源权威性**, 这两点恰恰是 A (模型内置联网) 最弱、C (应用侧检索) 最强的地方; 而 B 负责覆盖 C 检索不到的长尾 query.

核心结论 (详见第 4、5 节):

1. 权威性只能靠**域白名单 + 证据落库**保证, 不能靠模型自觉 — 所有业界可验证的做法 (Snopes / FactCheck.org / PolitiFact) 都是"原始来源优先 + 引用列表", 没有一家靠 LLM 自检.
2. 幻觉的解法是**双闸**: 判断模型只允许基于给定证据断言 (无证据 → 【存疑】), 输出后做引用↔证据自动校验.
3. 评审反方的答案是**完整证据链落库** (query / url / snippet / fetched_at / model / conclusion), 支持一键展开与 re-run.
4. moni 现役默认模型 MiniMax-M3 在阿里云百炼托管版**不支持联网搜索** (官方渠道支持), 联网核查主模型需另选或走官方 API — 详见 2.5 与第 4 节.

---

## 1. 调研方法 + 真实 query 证据

方法: 全部结论来自公开网页检索 (本 worker 实际执行 3 组真实 query) + 官方文档通读, 不引用任何无出处的二手宣传. 每条引用标注 [官方] / [第三方] / [推断].

### 1.1 Query 1: "人行 2026 数字人民币最新政策" (政策类)

检索 top 结果:

| 来源 | URL | 权威度 |
|------|-----|--------|
| 光明网 | https://m.gmw.cn/2026-04/02/content_1304402708.htm | 官方媒体 |
| 上海金融官网 (jrj.sh.gov.cn) | https://jrj.sh.gov.cn/ZXYW178/20260403/7c7eb17d655d4b46a6d4bb3335d5d8a2.html | 政府站点 |
| 央广网 | https://finance.cnr.cn/cjtt/yw/20260403/t20260403_527572583.shtml | 官方媒体 |
| 人民网 | http://finance.people.com.cn/n1/2026/0101/c1004-40637250.html | 官方媒体 |

跨源一致的结论: 2026-04-02 人行公告新增 12 家数字人民币运营机构, 总量扩至 22 家; 2026-01-01 起数字人民币钱包余额按活期存款挂牌利率计息.

对 moni 的启示:

- 政策类 query 的 top 结果天然以 gov / 官方媒体为主, 但**混排顺序由搜索引擎决定**, 不能假设"搜出来就是权威".
- 跨源交叉一致 (4 个独立源同一事实) 是低成本高置信的验证信号, 可以直接作为"给【已核实】"的条件之一.

### 1.2 Query 2: "招行 2026 Q1 AUM 零售客户 资产 规模" (具体数字类)

检索 top 结果:

| 来源 | URL | 权威度 |
|------|-----|--------|
| 中国证券报 (cs.com.cn) | https://jnzstatic.cs.com.cn/zzb/htmlInfo/121321.html | 官方媒体 (上市公司数据) |
| 证券之星 | https://4g.stockstar.com/detail/IG2026042800067964 | 财经媒体 |
| 东方财富 | https://wap.eastmoney.com/a/202605073729866846.html | 财经媒体 |
| 网易 | https://m.163.com/dy/article/KT8OTOOR05568W0A.html | 门户自媒体 |

跨源一致的数字: AUM 178,551.65 亿元 (较上年末 +7,726.46 亿, +4.52%); 零售客户 2.27 亿户 (+1.34%); 一季度净利 378.52 亿 (+1.52%).

对 moni 的启示:

- **具体数字类 claim 的交叉验证完全可行**: 4 个独立来源数字逐字一致, 无需人工即可给出高置信结论.
- 但同时**自媒体与权威源混排**: 若只取 top 1 (网易) 会误判权威度 — 这正是"域白名单 + 多源一致性阈值"要解决的.

### 1.3 Query 3: "数字人民币 2026 试点 扩容 城市 名单 公告" (名单/枚举类)

检索 top 结果: 凤凰网 (https://news.ifeng.com/c/8s1QOfLqo6l) / 每日经济新闻 (http://www.nbd.com.cn/rss/sohu/articles/4324294.html) / 财经 (https://news.caijingmobile.com/article/detail/566547) — 7 家股份行 + 5 家城商行名单完全一致.

对 moni 的启示:

- 枚举/名单类 claim 的多源一致判定容易自动化; 反过来, 若某源名单与其他源不同, 应触发【待核实】而不是采信多数 — 需要把"分歧显示"而非"投票定论"作为默认行为.

### 1.4 检索证据的整体观察 (推断, 基于以上 3 组实测)

1. 通用搜索引擎对中文金融政策/数字类 query 的**召回质量够用**, top 10 内通常有权威源; 但权威度排序不可信, 必须二次过滤.
2. 跨源一致性 (≥2 个独立源数字/名单一致) 是 moni 能给高置信结论的最低成本信号.
3. 因此 moni 不需要自己建搜索索引, 但要自己建**权威源清单 + 证据校验层** — 这正是 C 方案.

## 2. 业界产品/技术方案 (12 个核心参考)

### 2.1 人工/新闻组织类 (核查行业原点)

#### 1. Snopes — https://www.snopes.com/transparency/ [官方]

全球最早的网络流言核查站. 核心做法是纯人工编辑部流程: 每条核查指派编辑, 优先联系 claim 来源当事人与领域专家, 检索纸质/学术/统计一手资料, 至少一名编辑复核, 每篇文末有可展开的 Sources 引用列表 (https://www.snopes.com/transparency/). moni 可借鉴两点: **结论分级 + 引用列表即审计** (moni 的 fact_check_conclusion/note 就是这个形态), 以及"尽量用非党派一手数据源 (政府统计/同行评审期刊)"的信源优先级. 不可借鉴: 人工流程 (Snopes 明确说无法用单一方法覆盖所有题材), 这是最贵也最不可自动化的部分.

#### 2. FactCheck.org — https://www.factcheck.org/our-process/ [官方]

安嫩伯格公共政策中心运营, IFCN 认证. 方法学核心三条: **一手信源优先** (国会记录/劳工统计局/国税局/SEC/国会预算办等), **举证责任在 claim 方** (当事人提供不了证据才自查), 出版前 4 人复核链 (line editor → copy editor → fact-checker → 中心主任). moni 可借鉴: "一手信源优先"是权威性判断的直接翻译 — 对银行同业场景就是 人行/金融监管总局/交易所公告 > 财经媒体 > 自媒体. 不可借鉴: 4 人复核是人力, moni 只能做"自动校验 + 人工覆写"的近似.

#### 3. PolitiFact — https://www.politifact.com/article/2018/feb/12/principles-truth-o-meter-politifacts-methodology-i/ [官方; 页面二次抓取失败, 依据检索摘要]

Truth-O-Meter 六档结论 (TRUE / MOSTLY TRUE / HALF TRUE / MOSTLY FALSE / FALSE / PANTS ON FIRE, 定义见 https://www.politifact.com/ohio/about/ 各州页). 方法学要点: 尽量还原原始语境 (不按二手转述核查), **优先找原始政府报告而非新闻报道** (https://www.politifact.com/florida/about/). moni 可借鉴: 六档粒度比 moni 现有三档 (已核实/待核实/存疑) 更细, 尤其"数字对但缺上下文"的半档场景在金融新闻里常见 (如 AUM 增长 4.52% 但口径不含理财子). 不可借鉴: 选题靠人工裁量, 无法自动化.

#### 4. Ground News — https://ground.news/rating-system [官方; 页面抓取被拦, 依据检索摘要 + Wikipedia]

不做 claim 级核查, 做**事件级多源对照**: 聚合 5 万+ 媒体对同一事件的报道, bias/factuality 评分取 AllSides / Media Bias/Fact Check / Ad Fontes 三家平均 (https://en.wikipedia.org/wiki/Ground_News; https://www.cjr.org/analysis/the-business-of-balance-ground-news.php [第三方]). moni 可借鉴: "同一事件多源对照"正是 moni 11 个源天然能做的事 — 同一事实出现在 mpaypass + bestblogs + xwatch 多条, 本身就是交叉验证信号. 不可借鉴: 评分外包第三方 + 付费墙 + 面向政治极化, 与 moni 银行同业场景错位.

#### 5. Meedan Check — https://github.com/meedan (check / check-api / fetch) [官方]

开源协作核查平台, 面向新闻编辑室核查数字媒体 (图片/视频/社交媒体). 关键资产是 **Schema.org ClaimReview 标准化输出**: check-api 提供内容管理 API, fetch 服务聚合多源核查并以 ClaimReview 暴露 (https://github.com/meedan). moni 可借鉴: ClaimReview 字段集 (claimReviewed / reviewRating / url / datePublished) 就是"证据链该存什么"的行业答案, 与 brief 1.4 的必存项高度重合. 不可借鉴: Ruby on Rails 技术栈太重, 社交内容核查场景与 moni 无关.

### 2.2 自动化/聚合类 (半自动核查)

#### 6. FactStream (Duke Reporters' Lab) — https://reporterslab.org/tag/factstream/ [官方]

Duke Tech & Check Cooperative 出品的实时核查 App, 聚合 PolitiFact / FactCheck.org / Washington Post 的核查结果在政治直播中实时推送 (https://www.niemanlab.org/2018/01/factstream-debuted-live-fact-checking-with-last-nights-sotu-howd-it-go/ [第三方]). 重要事实: 2018 年公开承认是"需要后台人工核查员的手动 App" (同上), 2024-12 停止维护 (https://reporterslab.org/2024/12/03/reporters-lab-discontinues-factstream-app/ [官方]). moni 可借鉴: **聚合已有核查结论 + 推送** 的形态 = moni 一键简报集成 fact_check 字段的做法, 方向被验证过. 不可借鉴: 依赖外部人力核查源, 自己没有判断能力 — moni 不能抄这个.

#### 7. ClaimBuster (UT Arlington) — https://civictech.guide/projects/claim-buster [第三方] + https://www.kdd.org/kdd2017/papers/view/toward-automated-fact-checking-detecting-check-worthy-factual-claims-by-cla [官方论文]

最早端到端的自动核查研究系统 (KDD 2017). 做法: 监督学习 (基于人工编码数据集) 先**识别"值得核查"的 claim** (check-worthy), 再与可信事实库匹配打分, 2016 年总统辩论实弹部署. moni 可借鉴: **claim 筛序环节** — moni 简报限 2 个 claim 的策略与 ClaimBuster"先筛后判"同源, 可以学它按可核查性/重要性排序取 top-N. 不可借鉴: 依赖人工编码数据集 + 匹配式打分, 不支持开放世界多步推理; 且它只"筛"不"判".

### 2.3 LLM 原生联网类 (架构 A 的代表)

#### 8. OpenAI GPT-5 search — https://platform.openai.com/docs/guides/tools-web-search [官方; 页面未直接抓取, 依据官方文档索引 + OpenAI 社区]

Responses API 内置 web_search 工具, 模型自行决定何时检索, 返回带引用. 定价: reasoning 模型 (含 gpt-5) $10/1k 次搜索, 非 reasoning 模型 $25/1k (https://parallel.ai/articles/openai-to-parallel-search-api [第三方]; OpenAI 社区确认 gpt-5 系 $10/1k, https://community.openai.com/t/web-search-pricing-for-reasoning-models/1377274 [第三方]), 搜索内容 token 另计. 支持 allowed_domains / blocked_domains 域过滤参数. moni 可借鉴: 零集成成本 + 域过滤参数 (与"权威白名单"同构). 不可借鉴: 搜索后端是 OpenAI 黑盒, 无法保证中文监管源召回与排序; moni 薄壳模式无 OpenAI key; 引用 URL 有自编风险 (见第 5 节).

#### 9. Anthropic Claude web search tool — https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool [官方; 页面抓取被拦, 细节依据第三方]

服务端托管工具 (web_search_20260318 等版本, GA), Claude 在 Anthropic 基础设施执行搜索并在同一响应返回带引用结果; 支持 allowed_domains / blocked_domains 域过滤 + user_location (同官方文档索引). 定价 $10/1k 次搜索 + 搜索结果 token 按模型费率另计 (https://parallel.ai/articles/claude-web-search-vs-parallel [第三方]; https://help.apiyi.com/en/claude-api-web-search-guide-en.html [第三方]). moni 可借鉴: **域白名单参数**证明"权威源约束"在架构 A 下是厂商已支持的诉求, 不是 moni 苛求. 不可借鉴: 需要 Anthropic API key (moni 薄壳无 key), 引用仍可能混入低权威源, 审计字段受限于厂商返回.

#### 10. Alibaba Qwen web search (阿里云百炼) — https://help.aliyun.com/en/model-studio/web-search [官方, 已通读]

三种调用方式: Chat Completions 传 `enable_search: true`; Responses API 加 `web_search` 工具; DashScope 同参. 关键参数 `search_strategy: agent | agent_max` (agent_max 加 web extractor) 与 `enable_source: true` (返回引用). 2025-07 后发布的 Qwen Max/Plus/Flash 自动支持; 且**第三方模型可挂百炼搜索**: deepseek-v4-pro / deepseek-v4-flash / Kimi-K2-Instruct / MiniMax-M2.1 (同官方页 Supported models 表). moni 可借鉴: (a) 国内搜索源中文召回强; (b) `enable_source` 直接把引用给到应用侧, 正是审计链的原材料; (c) **moni 换国内模型时不必换搜索通道**. 不可借鉴: 引用质量/权威排序仍是黑盒, 当前文档未提供域白名单参数, 需要应用侧二次校验.

### 2.4 Agent tool-use 框架类 (架构 B 的代表)

#### 11. LangChain ReAct agent — https://python.langchain.com/docs/ (官方) + 社区实现 (https://github.com/AbdullahAli2005/web_search_agent)

ReAct = 推理-行动循环: 模型输出 Thought/Action, 系统执行 search 工具 (Tavily / DDG / Firecrawl), 结果回灌再推理, 直到能回答. 社区模板大量使用 LLM + 外部搜索 API 组合. moni 可借鉴: **多轮 query 迭代思路** (首轮 broad query → 次轮精确到具体公告名) 是提高召回的正确姿势. 不可借鉴: 引入 LangChain 整链依赖过重, moni 薄壳 + 3 条现有调用链不需要; 且其工具调用日志默认不留审计字段, 需自己包.

#### 12. Microsoft AutoGen — https://docs.firecrawl.dev/quickstarts/autogen [官方]

多 agent 对话框架: 把 Firecrawl 的 scrape/search 包成 function tool 给 AssistantAgent, user_proxy 发起任务, 多 agent 轮流协作 (同页). moni 可借鉴: 多 agent 编排 (检索 agent / 判断 agent / 复核 agent 分角色) 在学术上是清晰的, 但 moni 场景一个顺序 pipeline 就够. 不可借鉴: 引入消息总线级框架对 moni 是过度设计; 真实案例的审计可追溯性仍取决于应用层记录.

### 2.5 其余候选 (一句话取舍)

| 候选 | 结论 |
|------|------|
| Kimi K2 (Moonshot) — https://platform.kimi.ai/docs/guide/kimi-k2-quickstart | [官方] 自带十余个内置工具 (含 web search), 原生并行工具调用; 值得做 model 候选, 但引用可追溯性需实测, 本轮无一手实测 |
| Grok real-time / Live Search — https://docs.x.ai/developers/tools/web-search [官方] | web_search + x_search (X 帖子实时检索) 是差异化; 政企/中文监管源命中一般 [推断], 对银行同业价值有限 |
| 文心 ERNIE 4.0 Turbo — https://www.chinaz.com/ainews/9891.shtml [第三方] | 百度搜索插件自 4.0 起系统级内置 [第三方: https://www.php.cn/faq/2876203.html], 中文召回理论强但 API 化程度与引用字段官方披露少, 本轮不推 |
| MiniMax-M3 (moni 现役默认) | **阿里云百炼托管版不支持联网搜索** [官方能力表: https://help.aliyun.com/zh/model-studio/minimax-m3]; 官方 MiniMax API 渠道声明支持 Web Search [第三方: https://ofox.ai/zh/models/minimax/minimax-m3]; 二选一, 联网核查主模型不应默认用它 |
| Hermes Agent (NousResearch) — https://hermes-agent.nousresearch.com/docs/zh-Hans/user-guide/skills/optional/research/research-parallel-cli | moni 薄壳本来就调 `hermes chat`, 其工具集含 web_search/web_extract; **若 CLI 非交互模式能稳定返回结构化引用, moni 零新依赖即可启用架构 B** — 需实测 [推断] |
| Snark.news / Tracebook / Deverify | 多组检索无任何可验证的官方产品资料, 疑似小众或不可公开访问; 本轮**如实排除**, 不建议 moni 参考 |

## 3. 三大架构对比矩阵

定义:

- **A. 模型内置联网**: 厂商把搜索做进模型调用 (GPT-5 search / Claude web search / Qwen enable_search), 应用只传 prompt, 拿到带引用的回答.
- **B. Agent tool use**: 系统给模型 search/fetch 工具 (function calling), 模型决定何时查、查什么, 应用执行工具并回灌 (LangChain ReAct / AutoGen / Hermes).
- **C. 应用侧 RAG/检索**: moni 自己生成 query、自己检索 (自有库 FTS5 + DDG lite + 域白名单 fetch)、证据落库, 模型只读给定证据做判断.

| 维度 | A. 模型内置联网 | B. Agent tool use | C. 应用侧 RAG/检索 |
|------|-----------------|-------------------|---------------------|
| 准确性 (引用权威) | 中. 召回靠厂商黑盒, 可设域过滤但无法保证排序; 引用 URL 存在自编风险 | 高. 检索源/域白名单可控, 模型综合能力强 | 最高. 白名单 fetch + 证据强约束 (无证据不得断言), 多源一致性可量化 |
| 速度 | 快 (单次调用, 10-30s 级) | 中 (多轮工具循环, 30-60s+, 长尾可能更久) | 中-快 (fetch 可并行, 10-40s; 预取/索引后更快) |
| 成本 (估算, 1000 篇/月) | 搜索调用 $10-25/千次 + 搜索内容 token (GPT-5 约 ¥150-350/月 搜索费; Claude $10/千次 + token) | 搜索便宜 (DDG 免费 / Tavily 约 $5-10/月), 但多轮 token 膨胀, 模型调用 2-3x | 检索近乎免费 (自有 fetch + DDG lite), 只付判断模型 token; MiniMax-M3 每篇约 ¥0.02-0.05, 月 ¥20-50 |
| 中文能力 | 分化: Qwen/文心强, GPT-5/Claude/Grok 中 (监管源命中依赖索引, 无公开指标 [推断]) | 取决于模型 + 检索后端组合; 可自由组合成"中文模型 + 国内源检索" | 取决于 moni 检索后端; DDG lite 对中文 gov 召回一般 [推断], 需补国内源白名单 fetch 或换 Qwen 通道 |
| 可控性 | 低. 搜索策略黑盒, 无法自定义排序/源加权/重跑 | 高. query 生成、工具清单、域约束全部应用侧定义 | 最高. 检索、过滤、证据选择完全自控, 可离线复现 |
| 审计可追溯 | 中. 依赖厂商返回引用, 字段受厂商限制, 需应用侧额外校验引用真实性 | 中-高. 工具调用日志可全量记录 (query/结果/时间), 但需自己建表 | 最强. 证据 (query/url/snippet/fetched_at) 存自有 DB, 可导出、可 re-run、可展示 |

成本口径: OpenAI https://parallel.ai/articles/openai-to-parallel-search-api ; Claude https://help.apiyi.com/en/claude-api-web-search-guide-en.html ; MiniMax-M3 阿里云定价 输入 ¥4.2/百万 tokens / 输出 ¥16.8/百万 tokens (https://help.aliyun.com/zh/model-studio/minimax-m3) — 均 [第三方/官方], 需实施前复核.

---

## 4. moni 拍板: C 为主 + B 补充 + A 兜底 (混合 = brief 的 D)

### 4.1 决策逻辑

1. **刚需排序**: moni 是给银行同业用户的本地新闻库, 核查结论要能被同事/评审追着问. 审计可追溯是刚需, 速度其次, 成本敏感. 按第 3 节矩阵, 这是 C > B > A.
2. **权威性**: 业界唯一被验证的权威性来源是"一手信源优先 + 引用列表" (Snopes/FactCheck.org/PolitiFact 全同). A 方案把信源选择交给厂商黑盒, 无法向评审交代 "为什么信这篇自媒体"; C 方案能回答 (域白名单 + 检索日志 + 源评分).
3. **中文监管源**: moni 强需求类型 (人行/金融监管总局/证监公告) 的命中最稳路径是国内源直接 fetch (pbc.gov.cn 公告页/交易所公告), 而不是靠任何厂商搜索索引 — 厂商对中文 gov 的索引深度无公开承诺 [推断].
4. **现有资产复用**: moni 已有 11 源 fetcher + xwatch DDG lite 通道 + FTS5 + 3 条 LLM 调用链, C 是唯一能全量复用现有代码的架构; A/B 都要新增外部依赖.
5. **成本**: 1000 篇/月 全量联网核查, C 约 ¥20-50/月, A 约 ¥150-350+/月 (搜索费), B 居中 — C 便宜一个量级 (估算, 见第 3 节).

### 4.2 推荐路径 (供 arch/green-frog 实施)

- 主路径 (C): 按内容类型分流 → 强需求类型 (监管处罚/央行公告/并购/排名声明/具体数字) 走 `query 生成 (LLM) → 检索 (moni 自有库 FTS5 优先 → DDG lite → gov 域白名单 fetch 补充) → 证据落库 → 判断模型只读证据 → 结论 + 引用`.
- 补充路径 (B): 首轮检索 top-N 无权威源命中时, 允许 agent 化二次检索 (多轮 query 迭代; 若 Hermes CLI 工具调用实测可用则直接复用, 见 2.5).
- 兜底路径 (A): 上述两者都查不到时, 才调用厂商内置联网模型 (Qwen enable_search 优先, 因中文 + enable_source), 但**其引用必须过同样的证据校验闸**, 不能直接采信.
- 收敛现有 3 套调用链: 统一 `fact_check_method` 取值 (建议: `llm_inference` | `web_search` | `human`, 与 schema 预留一致), 简报短版 prompt 与长版对齐为同一判断模型.

### 4.3 反方风险: 三个必答题

**Q1: "你 moni 怎么保证引用权威?"**

不靠模型自觉, 靠三层硬约束: (a) 检索层域白名单 (pbc.gov.cn / cbirc.gov.cn / csrc.gov.cn / samr.gov.cn / 交易所 + 上市公司公告域 / 已认证官方媒体域), 非白名单源只在零命中时降级引用并显式标注 [推断设计]; (b) 判断层 prompt 强约束"引用 URL 必须来自证据列表, 禁止编造", 且**输出后自动校验引用↔证据映射, 不匹配即拒答降级为【存疑】**; (c) 结论分级与证据权威度绑定: 只有白名单源命中才可能给【已核实】, 仅自媒体命中的最多【待核实】. 业界先例: FactCheck.org 的一手信源优先原则 (https://www.factcheck.org/our-process/) + Snopes 文末 Sources 列表 (https://www.snopes.com/transparency/).

**Q2: "幻觉怎么办?"**

双闸 + 可复现: 第一闸在生成前 — 判断模型只允许基于传入证据断言, 证据不足必须输出【存疑】/【待核实】, 并显式列出缺什么 (对应 brief 长版 prompt 的"引用风险"思路); 第二闸在生成后 — 自动校验: (i) 引用 URL 必须存在于本次证据列表; (ii) 具体数字/名单类 claim 要求 ≥2 个独立源交叉一致才给【已核实】, 不一致列出分歧; (iii) 任何校验不过 → 结论自动降级 + note 写原因. 同时存原始 query 与检索结果, 支持一键 re-run 对比 (结论漂移可见). 实测证据: 第 1.2 节显示多源数字逐字一致是可自动判定的; 而 1.1 显示权威源与自媒体混排, 正说明"校验层"而非"模型自觉"才是防幻觉闸门.

**Q3: "评审追问怎么办?"**

每次核查写完整证据链并落库 (新建 `fact_check_evidence` 表, 字段 = brief 1.4 必存项: query / url / snippet / fetched_at / model / provider / conclusion / rationale; 或按 green-frog 的 schema 结论复用 fact_check_note json 化, 但**独立表更利于查询与审计** [推断]), 评审在 UI 一键展开: 看了哪个 query、命中哪些 URL、模型怎么判断. 人工可覆写结论 (走 draft→verified 状态机, 与 case 库模式同构), 覆写记录留痕. 这对应业界 FactStream"聚合可查"与 Meedan ClaimReview 结构化输出的思路 (见 2.1/2.2), 且比两者更强: moni 连"检索过程"都可查, 不只是结论.

---

## 5. 对 arch 聚合稿的关键输入 (备忘)

1. **模式**: 拍板 D (C 主 + B 补 + A 兜底), 不是纯 A/B/C.
2. **主模型**: 联网核查主模型候选 qwen3-max / qwen-plus (enable_search + enable_source, 阿里云官方支持) 与 deepseek-v4-pro (可挂百炼搜索); **不要默认用阿里云托管版 MiniMax-M3 做联网** (官方能力表不支持联网搜索).
3. **审计**: 新建证据表或扩展 fact_check_note, 必存 query/url/snippet/fetched_at/model/conclusion; 引用校验 + 多源一致性阈值是防幻觉核心.
4. **method 字段**: 收敛为 schema 预留三值 (llm_inference/web_search/human), 消除 'llm' 与 'llm_inference' 并存.
5. **范围建议**: v1.0.22 只做强需求类型联网核查 (见 brief 1.2), 弱需求保持现 LLM 推断, 软文/短文跳过 — 控成本、控延迟.

---

## 附: 证据与引用可信度声明

- [官方] = 产品厂商/组织自营文档或页面, 本 worker 实际打开 (标注"未直接抓取"者除外).
- [第三方] = 行业媒体/转载, 与官方信息交叉印证后引用.
- [推断] = 本 worker 基于实测检索结果或公开资料的分析, 非官方声明.
- 三组真实 query 的检索结果 URL 见第 1 节表格, 均可复现.
