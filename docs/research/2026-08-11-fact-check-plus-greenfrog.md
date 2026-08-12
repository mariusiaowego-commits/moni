# 事实核查+ moni 内部基础设施 + 联网核查落地最小侵入点 (green-frog)

> 作者: green-frog (hermes worker) | 日期: 2026-08-11
> 任务: brief `docs/plans/2026-08-11-fact-check-plus-research.md` 的 Part A (prep 盲点实查) + Part B (落地最小侵入点)
> 配套: red-fox 业界模式研究 `docs/research/2026-08-11-fact-check-plus-redfox.md`
> 所有结论基于真实 grep / sqlite3 / CLI 实测, 引用带 file:line

---

## TL;DR (拍板)

1. **检索通道选型**: 联网核查主通道 = **hermes 薄壳 `-t web` 工具调用 (架构 B)**, 实测可用且零新依赖; DDG lite **半复用** (只做发现/兜底, 不当核查主通道); 自有 FTS5 库检索作补充。与 red-fox "C 主 B 补" 的差异点见 B1。
2. **method 字段收敛**: 统一三值 `llm_inference | web_search | human`; 迁移把存量 `'llm'` 刷成 `'llm_inference'`; 新路径写 `web_search`。
3. **evidence 落库**: 新建 `fact_check_evidence` 表 (独立表, 不复用 fact_check_note JSON), 存 query/url/snippet/fetched_at/model/provider/conclusion。
4. **task_type**: 新增 `fact_check_plus`, worker 加一个分支 (jobs.py 已有 task_type 分叉, 成本 10 行)。
5. **端点**: 单篇 `POST /api/articles/{id}/fact-check-plus` (202+job_id, 复用 articles.py:326 模式) + 批量 `POST /api/fact-check-plus/batch` + 证据查询 `GET /api/fact-check-plus/evidence/{article_id}`。
6. **前端**: 不加新 tab; 文章详情 fact-check 区升级为「联网核查」双模式, 证据链可展开; 看板加 method 徽标。
7. **废弃死代码**: 同步 `/api/llm/fact-check` (routes/llm.py:88) 无任何前端调用者, 且 model 解析有 latent bug, 建议废弃。
8. **实测风险**: 默认 provider 本月已撞 429 配额 (2026-08-17 重置), 批量联网核查必须显式 pin provider + fallback。

---

## Part A: prep 文档盲点实查 (5 条)

### A1. xwatch DDG Lite 通道是否适合"事实核查"场景

**结论: 半复用 — 只适合发现/兜底, 不适合当核查主通道。**

实查证据 (moni/fetch/xwatch_discovery.py):

1. **通道是 x.com 专用, 不是通用检索**。`search_tweet_urls` 硬编码 `full_query = f"site:x.com {query}"` (xwatch_discovery.py:231), 结果提取器 `_extract_tweet_urls_from_lite_html` 只认 `TWEET_URL_RE = r"x\.com/([A-Za-z0-9_]+)/status/(\d+)"` (xwatch_discovery.py:100), 非 tweet 链接全被过滤。事实核查要查的是 pbc.gov.cn / 官方媒体 / 上市公司公告, 这个通道直接不可用 — 要么新写一个通用结果提取器 (title/snippet/href 全收), 要么另起炉灶。
2. **反爬风险是真实且已被代码承认的**。模块 docstring 明说 v1.0.11 从 `html.duckduckgo.com/html/` 切到 `lite.duckduckgo.com/lite/` 就是因为原端点被 Anomaly challenge 页打爆 (xwatch_discovery.py:5-9); 有 anomaly 检测 `_is_anomaly_page` (xwatch_discovery.py:108) + 5s sleep 重试 (xwatch_discovery.py:263) + 60min 进程级熔断器 `_open_circuit` (xwatch_discovery.py:83-91)。熔断期间所有查询直接返 `[]` (xwatch_discovery.py:218-224)。核查是"必须给出结论"的场景, 一个可能静默返空的主通道不可接受。
3. **5s 延迟限制**: 单次查询 timeout=20s, anomaly 时 sleep 5s 重试, 一次最多 ~45s (xwatch_discovery.py:234-264)。若做批量核查 (每篇 1-3 个 query), 光 DDG 就可能拖 2-3 分钟/篇, 且多 query 连发更容易触发 anomaly。
4. **自媒体占比/权威源命中率**: 未实测通用 query (现提取器只收 tweet URL, 无法统计), 但结合 red-fox 的 3 组真实检索观察 — "top 结果混排权威源与自媒体, 排序不可信" (redfox.md:72-74) — DDG 的权威度过滤必须应用侧自己做, 通道本身不提供。

**复用建议**: 把 DDG lite 的**反爬框架** (anomaly 检测 + 熔断 + 静默降级) 原样复制给"通用检索兜底通道", 但新写通用提取器; 只在 hermes web 工具不可用/配额耗尽时启用, 且结果必须过域白名单闸。

### A2. fact_check_method 字段实际值分布

实查命令:

```sql
sqlite3 data/news.db "SELECT fact_check_method, COUNT(*) FROM articles GROUP BY fact_check_method;"
```

输出:

```
|2611
llm|9
llm_inference|23
```

**red-fox prep 说 "llm 与 llm_inference 并存" 成立** (9 + 23), 但注意:

- **2611 篇 (98.8%) 是 NULL** — 现有核查覆盖极低, 主要靠一键简报 enrichment 才写。
- 9 条 `'llm'` 全部来自后台 worker (jobs.py:340 写 `"llm"`), 与 llm_jobs 表 fact_check done=9 完全吻合 (实查: `SELECT task_type, status, COUNT(*) FROM llm_jobs GROUP BY 1,2` → fact_check done 9 / failed 1 / cancelled 1)。
- 23 条 `'llm_inference'` 来自同步 route (llm.py:118 写 `"llm_inference"`) + enrichment 简报路径 (enrichment.py:376 空正文分支、:395/:399 正常分支, 默认都是 `llm_inference`)。

**修法 (三步)**:
1. 新 migration: `UPDATE articles SET fact_check_method='llm_inference' WHERE fact_check_method='llm';` 全表归一。
2. 代码层: jobs.py:340 的写库值从 `"llm"` 改为 `"llm_inference"`, 与新路径统一。
3. 新联网核查路径落 `'web_search'`; 人工复核落 `'human'` — 与 schema.sql 预留注释一致 (schema.sql L26: `'llm_inference' | 'web_search' | 'human'`)。

### A3. has_fact_check schema 来源

实查命令:

```sql
sqlite3 data/news.db ".schema articles"
```

输出 (截取相关列):

```
fact_check_conclusion   TEXT,
fact_check_method       TEXT,
fact_checked_at         TEXT,
fact_check_note         TEXT,
has_ai_summary INTEGER NOT NULL DEFAULT 0,
has_fact_check INTEGER NOT NULL DEFAULT 0,
...
```

grep 首次出现:

```
moni/db/migrations/009_add_ai_summary_fields.sql:16:--   has_fact_check   INTEGER (0/1) — flag: have we done a fact-check
moni/db/migrations/009_add_ai_summary_fields.sql:32:ALTER TABLE articles ADD COLUMN has_fact_check INTEGER NOT NULL DEFAULT 0;
```

**结论**: `has_fact_check` 在 migration 009 (v1.0.2 时代) 就加了, 与 fact_check_* 四列同批。它是"是否做过核查"的布尔 flag, 与 method 列语义不同 (flag 管 UI 显示, method 管审计)。**新路径不需要改它** — fact_check_plus 完成后同样置 1, UI 兼容。

### A4. 三条调用链真实关系 (对 arch prep 的纠偏)

arch prep (brief L15-17) 说 "routes 写 llm, enrichment 写 llm_inference"。**实查结果相反**:

| 入口 | 文件:行 | Prompt | method 实际写入 | 是否有前端调用者 |
|------|---------|--------|----------------|----------------|
| 同步单篇 `/api/llm/fact-check` | moni/web/routes/llm.py:88-123 | 长版 fact_check.md (`render_fact_check`, llm.py:102) | `"llm_inference"` (llm.py:118) | **无** (grep static/ 仅注释命中; app.js:3391 调的是 async 版) |
| 异步 worker `/api/articles/{id}/fact-check` | moni/web/routes/articles.py:326-381 入队 → moni/llm/jobs.py:274-275 render + :331-341 写库 | 长版 fact_check.md | `"llm"` (jobs.py:340) | **有** (app.js:3391 是唯一入口) |
| 一键简报 enrichment | moni/share/enrichment.py:361-404 `_ensure_fact_check` | 内嵌短版 FACT_CHECK_PROMPT (enrichment.py:33-50) | `"llm_inference"` (默认, enrichment.py:395) | share manifest include_fact_check |

**关键发现**:

1. **真正的"单篇手动"入口是 articles.py:326 的异步版, 不是 brief 说的 llm.py:88**。同步 route 是 v1.0 遗留的死代码 — 前端早切到 202 + job_id 模式了。它不读 llm_settings (见 A5), model 解析是坏的, 建议废弃。
2. **两套 prompt 的结论语义不一致**: 长版 fact_check.md 结论是 `高/中/低` 可信度 (fact_check.md:29, :48-49), 短版 enrichment 是三档标签 `【已核实】|【待核实】|【存疑】` (enrichment.py:37)。DB 里 `fact_check_conclusion` 混着两种风格 — 实查样本: `##核查结论**可信度：中**...` (llm 行) vs `claim1 [supported]: ...` (llm_inference 行)。这是比 method 值更深的"双语义"债。
3. **输出字段差异**: 三处都写 fact_check_conclusion/method/checked_at/note; 但只有 enrichment 写 has_fact_check=1 (enrichment.py:375, :400), 同步 route 和 worker **不写 has_fact_check** — 这是 worker 路径的遗漏 bug (worker 完成核查后 has_fact_check 仍为 0, UI 不显示)。
4. **model/provider 解析**: articles.py:345-365 读 `llm_settings.active_fact_check_model_id` → join llm_models → 得 (hermes_provider, model_id) 入队; llm.py:88 不读 settings, 用硬编码 `_DEFAULT_MODEL`。

### A5. route 默认 model vs seed model 不一致

实查命令:

```sql
sqlite3 data/news.db "SELECT * FROM llm_settings WHERE key LIKE '%model%';"
```

输出:

```
active_tag_model_id|seed-model-seed-provider-minimax-cn-MiniMax-M3
active_summary_model_id|seed-model-seed-provider-minimax-cn-MiniMax-M3
active_fact_check_model_id|seed-model-seed-provider-minimax-cn-MiniMax-M3
```

实查代码:

- `routes/llm.py:11` `_DEFAULT_MODEL = os.environ.get("MONI_LLM_MODEL", "claude-3-5-sonnet-latest")` — .env 无 MONI_LLM_MODEL (grep 0 命中), 所以同步 route 实际调 `hermes chat --provider minimax-cn -m claude-3-5-sonnet-latest`。
- `seed_llm.py:41-47` `_DEFAULT_MODELS` 4 个 model (MiniMax-M3 / gemini-2.5-flash-lite / gemini-2.5-pro / openrouter gemini flash), **确实无 claude**。red-fox 的 latent bug 判断成立。
- **但影响面比 prep 以为的小**: 同步 route 是死代码 (A4), 前端真实路径 articles.py:345-365 正确读 settings → MiniMax-M3 / minimax-cn。真正要修的是废弃同步 route, 而不是去 seed claude。
- 附带发现: settings 里 `max_tokens_fact_check=1500` 但同步 route 硬编码 `max_tokens=1024` (llm.py:106) 且 ChatClient.chat() 根本不接受 max_tokens 参数 (client.py:170-174 是 pass-through, kwargs 被丢弃) — settings 的 max_tokens/temperature 目前**实际没生效**, 是另一处暗债。

---

## Part B: 联网核查落地最小侵入点方案

### B1. 三条调用链收敛建议

**统一目标: 单入口 (async worker) + 单 prompt 语义 (三档标签) + 单 method 三值。**

1. **入口收敛到 llm_jobs worker**: 所有核查 (纯 LLM 推断 / 联网核查 / 简报富化) 都走 enqueue → `_run_job`。同步 route `/api/llm/fact-check` (llm.py:88) 废弃 — 返回 410 或直接删 (无调用者, 有测试的话同步改)。enrichment 简报路径 (enrichment.py:361) 保留但内部改为调 enqueue (或保持直连, 因 share worker 是独立进程, 见 B3 取舍)。
2. **prompt 语义收敛**: `fact_check_conclusion` 统一存三档标签短句 (【已核实】/【待核实】/【存疑】/【无需核查】); 长版 fact_check.md 的 `高/中/低` 详细分析降级进 `fact_check_note`。现有 enrichment 短版 FACT_CHECK_PROMPT 语义不动 (已是三档), 联网版新 prompt 继承同样标签 + 加 evidence JSON 输出。
3. **method 三值落库**: `llm_inference` (纯 LLM 推断) / `web_search` (联网核查) / `human` (人工覆写)。migration 刷存量 `'llm'→'llm_inference'`; 修 jobs.py:340; worker 和 enrichment 补写 `has_fact_check=1` (修 A4-3 的遗漏)。
4. **model 解析统一**: 新路径一律走 articles.py:345 的 settings→llm_models 解析, 不允许裸硬编码。

**与 red-fox "C 主 + B 补 + A 兜底" 的差异**: red-fox 推荐 C (应用侧自建检索) 为主。green-frog 实测后认为 **B (hermes 工具调用) 才是 moni 成本最低的主路径**, 理由:

- moni 是薄壳 (client.py:1-7 设计), 不持 key; `hermes chat` 的 `-t web` 工具集已在 CLI 层可用 (本 worker 实测, 见附), 检索后端由 hermes 的 web_search 提供 (Firecrawl/Nous 托管), 没有 DDG 反爬脆弱性, 零新依赖。
- C 的"应用侧检索"在 moni 里要新写: 通用 DDG 提取器 + 域白名单 fetch + 检索编排, 全是要维护的爬虫代码; 而 B 把这些交给 hermes 工具层, moni 只做**证据落库 + 校验闸** — 恰恰是 moni 的审计刚需, 也是 C/B/A 共通的价值层。
- red-fox 自己也标注 Hermes CLI 工具调用 "需实测 [推断]" (redfox.md:142), 本 worker 已实测通过 (附: 实测 1)。

结论: **B 主 (hermes -t web 检索) + C 补 (自有 FTS5 库内检索 + DDG lite 兜底) + A 不引入**。A (厂商内置联网模型) 需要新 key/新 provider, 与薄壳模式冲突, 跳过。

### B2. 新 schema 改动

**拍板: 新建 `fact_check_evidence` 独立表, 不复用 fact_check_note JSON。** 理由: 评审要按 query/url 维度查询展开, 独立表可 SQL 检索/聚合 (如"这篇核查看过哪些源""某 URL 被引用过几次"), JSON blob 做不到; red-fox 同判 (redfox.md:195)。

```sql
-- migration 025_fact_check_evidence.sql
CREATE TABLE IF NOT EXISTS fact_check_evidence (
    id           TEXT PRIMARY KEY,            -- uuid4
    article_id   TEXT NOT NULL,               -- FK -> articles.id
    job_id       TEXT,                        -- llm_jobs.id (可空: 人工/CLI 写入)
    query        TEXT NOT NULL,               -- 检索 query (模型自报或应用生成)
    url          TEXT NOT NULL,               -- 命中的来源 URL
    domain       TEXT,                        -- url 的域名 (校验/白名单用, 冗余存)
    snippet      TEXT,                        -- 引用证据片段
    fetched_at   TEXT NOT NULL,               -- 检索时间
    model        TEXT,                        -- 判断模型 (快照)
    provider     TEXT,                        -- hermes provider (快照)
    conclusion   TEXT,                        -- 该证据对应的结论标签
    rationale    TEXT,                        -- 模型依据 (可选)
    evidence_seq INTEGER NOT NULL DEFAULT 0,  -- 同一 article 内顺序
    created_at   TEXT NOT NULL,
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_fce_article ON fact_check_evidence(article_id);
CREATE INDEX IF NOT EXISTS idx_fce_url     ON fact_check_evidence(url);
```

配套改动 (同一 migration):
- `UPDATE articles SET fact_check_method='llm_inference' WHERE fact_check_method='llm';` (存量归一)
- 不加 articles 新列 — method/has_fact_check 语义已够, evidence 数量用 JOIN 查。

字段对应 brief 1.4 必存项: query / url / snippet / fetched_at / model / provider / conclusion / rationale 全覆盖, 外加 job_id 关联任务审计。

### B3. 新端点建议

**拍板 3 个端点, 全部复用现有 worker 线程 (jobs.py ensure_worker_started), 无新基础设施。**

1. **单篇手动** — `POST /api/articles/{id}/fact-check-plus` → 202 `{job_id, status, poll_url}`
   直接复制 articles.py:326-381 的 async 模式, 差异: task_type=`fact_check_plus`。前端在文章详情加按钮。
2. **批量** — `POST /api/fact-check-plus/batch` body `{article_ids: [...], reason: "manual"|"auto"}` → 202 `{job_ids: [...], enqueued: n}`
   校验: article_ids ≤ 20 (200 limit 规则同 FastAPI 422 教训, moni skill), 逐条 enqueue, 不阻塞。强需求类型筛选 (处罚/公告/首家/数字声明) 由调用方 (share 简报/cron) 先筛好, 端点本身不做内容分类。
3. **证据查询** — `GET /api/fact-check-plus/evidence/{article_id}` → `{items: [{query, url, domain, snippet, fetched_at, conclusion, rationale}]}`
   评审展开用, 按 evidence_seq 排序。

**enrichment 简报路径取舍**: run_enrichment (enrichment.py:859) 是独立 subprocess 跑的 (share worker), 它内部直连 hermes (summary_gen._invoke_hermes), 不经过 llm_jobs。要让简报也支持联网核查, 最小改法: `_ensure_fact_check` 加一个 `mode` 参数 (`llm` | `web`), web 模式把 prompt 换成联网版 + 调 `hermes chat -t web` + 解析 evidence 写新表。不推倒重来。**不强求简报默认开 web 模式** — 默认仍 llm_inference (便宜快), 用户 manifest 里 `fact_check_mode: web` 才走联网。

### B4. 是否新 llm_jobs task_type

**拍板: 新增 `task_type='fact_check_plus'`。** 理由:

- jobs.py:270-278 已有 task_type 分支 (summary / fact_check), 加一个 elif 分支成本 ~10 行, 语义干净。
- 与旧 fact_check 并存: 旧 task_type 保持向后兼容 (已有 11 条历史 job), 新代码只入队 fact_check_plus。
- enqueue_job 的 task_type 白名单校验 (jobs.py:80) 加 `"fact_check_plus"`。
- 不需要新 worker 线程 / 新表队列 — llm_jobs 一个表够, worker 是通用的。
- jobs.py:331-341 的写库分支加 `fact_check_plus` → 写 fact_check_conclusion (三档标签) + method='web_search' + has_fact_check=1 + 逐条 INSERT fact_check_evidence。

### B5. 前端 UI

**拍板: 不加新 tab, 不加新 view — 在现有文章详情 + 简报区做增量。**

1. **文章详情 fact-check 区升级** (index.html:2284 附近的 summary-box): 
   - 现有「事实核查」按钮旁边加「联网核查」按钮 → 调 `POST /api/articles/{id}/fact-check-plus`, 复用 app.js:3391 的 enqueue+poll 模式 (enqueueLlmJob)。
   - 核查完成后展示三档标签 + method 徽标 (web_search 蓝 / llm_inference 灰 / human 绿) + 「证据链」折叠区: 展开显示 query/url/snippet 列表 (GET /api/fact-check-plus/evidence), 每条 URL 可点。
2. **简报 UI**: 一键简报 manifest 的 include_fact_check 语义扩展 — 加 `fact_check_mode: llm|web` 开关; 简报正文 fact_check 明细 (enrichment.py:1102-1115) 在 web 模式追加上 evidence 链接。
3. **看板**: 文章卡片/列表加小徽标显示 method (有值的才显示), 不新增统计块。stats.js 可加一行 "联网核查完成数" (可选, P2)。

### B6. 风险评估

| 风险 | 评估 | 缓解 |
|------|------|------|
| 成本 | 每篇 1 次 hermes 调用, MiniMax-M3 token 便宜 (redfox.md:157 估 ¥0.02-0.05/篇), web_search 走托管工具免费额度。multi-turn 会膨胀 2-3x token | max-turns 限制 4-6; 批量限 20 篇/批; 弱需求类型不联网 (brief 1.2 分流) |
| 延迟 | 实测单篇 ~2 分钟 (含多轮检索), 同步不可行 | 全走 async worker + 202 + poll (现有模式); 批量串行在 worker 里排队, UI 用进度提示 |
| 配额 (实锤) | 本 worker 实测: 默认 provider 已撞 `HTTP 429 monthly quota, 2026-08-17 重置` (附: 实测 2) — 批量跑如果 pin 错 provider 会整批失败 | 强制走 llm_settings 解析的 provider (已 pin minimax-cn); worker 已有 MAX_ATTEMPTS=2 重试 (jobs.py:58); 支持 fallback provider 链 |
| 幻觉 / 引用自编 | LLM 可能编 URL | 双闸: (1) prompt 强制输出 evidence JSON (query/url/snippet 自报); (2) 应用侧校验 — URL 域名解析合法 + 域白名单 (pbc.gov.cn/cbirc.gov.cn/csrc.gov.cn/交易所/官方媒体域) 命中才允许【已核实】, 非白名单最多【待核实】; 纯自报无 snippet 的证据丢弃; 可选对关键 URL 用 web_extract 抽查 (P2) |
| 评审反方 | "你怎么证明查过/凭什么信这个源" | fact_check_evidence 全落库 + UI 一键展开 (query/url/snippet/fetched_at/model 全可见) + 人工覆写 (method='human') |
| 反爬 | DDG lite 有 60min 熔断 (A1) | 主通道用 hermes 托管 web 工具, 不碰 DDG; DDG 仅作兜底 |
| 内容类型误判 | 强需求类型筛选误伤/漏网 | 单篇由用户显式触发 (无成本风险); 批量/简报默认只在 regulation/banking tag + 关键词命中时建议联网, 用户可关 |

---

## 附: 本 worker 实测证据

**实测 1 — hermes 薄壳 + web 工具零依赖可用 (架构 B 可行性)**:

```bash
cd /Users/mt16/dev/moni && hermes chat -q "用 web_search 查: 中国人民银行 2026 数字人民币最新政策。只输出 2 个最权威来源的 URL 和一句话摘要, 不要其他文字。" --provider minimax-cn -m MiniMax-M3 -t web -Q --max-turns 8
```

输出 (截取):

```
1. https://www.news.cn/20251229/442a77288543426294939796802ac43e/c.html — 新华社：中国人民银行宣布数字人民币2.0将于2026年1月1日启动...
2. http://paper.people.com.cn/rmrb/pc/content/202512/31/content_30128236.html — 人民日报：央行副行长陆磊撰文解读《行动方案》...
```

意义: (a) `-t web` + `-Q` (安静模式) + 非交互 `-q` 组合可用, 无 TTY 审批卡点 — 直接塞进 client.py 的 `extra_args` (client.py:55, :80-81) 即可; (b) MiniMax-M3 当判断模型 + hermes 工具做检索的组合成立 — red-fox "MiniMax-M3 不支持联网" 指的是阿里云托管 API 的内置搜索, 与薄壳 B 架构不冲突; (c) 返回 URL 是真实可追踪的 (news.cn / people.com.cn), 审计链原材料在。

**实测 2 — 默认 provider 配额 429 (风险实锤)**:

```bash
hermes chat -q "..." -t web -Q    # 不带 --provider, 走默认
→ API call failed after 3 retries: HTTP 429: You have exceeded the monthly usage quota. It will reset at 2026-08-17 23:59:59 +0800 CST.
```

意义: 批量联网核查绝不能依赖默认 provider; moni 现有 llm_settings→llm_models 解析 (articles.py:345-365) 已经显式 pin minimax-cn, 是对的, 新路径必须继承同一解析。

**实测 3 — 三链 method 分布与 llm_jobs 对应**:

```sql
sqlite3 data/news.db "SELECT fact_check_method, COUNT(*) FROM articles GROUP BY fact_check_method;"
-- NULL:2611  llm:9  llm_inference:23
sqlite3 data/news.db "SELECT task_type, status, COUNT(*) FROM llm_jobs GROUP BY task_type, status;"
-- fact_check done:9  fact_check failed:1  fact_check cancelled:1  summary done:41  summary running:1
```

llm=9 与 fact_check done=9 完全吻合 → 9 条 'llm' 全部出自 worker 路径 (jobs.py:340)。

---

## 给 arch 聚合稿的关键输入 (备忘)

1. 检索通道: B 主 (hermes `-t web`, 实测可用) + C 补 (自有库/DDG 兜底), 不引入 A。
2. method 收敛: 三值 llm_inference/web_search/human; migration 刷 'llm'→'llm_inference'; 修 jobs.py:340 + worker 补写 has_fact_check=1。
3. 新表 fact_check_evidence (独立表, 字段= brief 1.4 必存项 + job_id); migration 025。
4. task_type 新增 fact_check_plus; 端点 3 个 (单篇 202 / 批量 202 / 证据 GET); 前端文章详情增量 + method 徽标, 不加 tab。
5. 废弃死代码: 同步 /api/llm/fact-check (无调用者 + model 解析 bug + max_tokens/temperature settings 不生效)。
6. 风险头条: provider 配额 429 必须显式 pin + fallback; 引用自编用域白名单 + evidence 落库双闸。
7. 范围建议: v1.0.22 只做"单篇手动联网核查 + evidence 落库 + UI 展开"; 批量/简报 web 模式放 P1/P2。
