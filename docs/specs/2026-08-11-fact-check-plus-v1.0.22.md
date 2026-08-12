# 事实核查+ tech spec (v1.0.22)

> **状态**: draft, 等 dad 拍板
> **作者**: blue-owl (arch, hermes coder profile)
> **日期**: 2026-08-11
> **配套研究**: docs/research/2026-08-11-fact-check-plus-{redfox,goldtiger,greenfrog,aggregate}.md
> **拍板结果** (dad 2026-08-11): Q1=B / Q2=B / Q3=B / Q4=B

---

## 0. 背景与决策回顾

### 0.1 现状 (已实查)

moni 事实核查 v1 (v1.0.10): LLM 常识推断, 写 `articles.fact_check_*` 4 列 + `has_fact_check=1` flag。
3 条入口, 2 套 prompt, method 字段两值 (`llm` / `llm_inference`):

| 入口 | 文件 | Prompt | method 写入 | has_fact_check | 前端调用 |
|------|------|--------|-------------|----------------|---------|
| 一键简报 enrichment | `moni/share/enrichment.py:33,361` | 内嵌短版 `FACT_CHECK_PROMPT` | `llm_inference` | ✅ 写 | manifest |
| 异步 worker | `moni/web/routes/articles.py:326 → jobs.py:274,340` | 长版 `fact_check.md` | **`llm`** (隐债!) | ❌ **不写** (隐债!) | app.js:3391 (唯一) |
| 同步 route (死代码) | `moni/web/routes/llm.py:88` | 长版 | `llm_inference` | ❌ | **无调用者** (死代码) |

**实查数字** (arch 亲自 sqlite 复核, 与 green-frog 一致):

```
fact_check_method: NULL:2611 / llm:9 / llm_inference:23
llm_jobs:          fact_check done:9 / failed:1 / cancelled:1 / summary done:41 / running:1
llm_settings:      active_*_model_id=seed-model-...MiniMax-M3, temperature_fact_check=0.2, max_tokens_fact_check=1500
                   ⚠️ settings 的 max_tokens/temperature 在 ChatClient 不生效 (client.py:170-174 是 pass-through)
```

### 0.2 拍板 (Q1-Q4 = B/B/B/B)

- **Q1** 主检索通道 = **B (hermes -t web)** — green-frog 实测可用, 零新依赖
- **Q2** v1.0.22 范围 = **单篇手动 + 批量 ≤20 篇 + 修隐债** — 简报 web 模式留 P1
- **Q3** 落库 model = **Qwen-plus + enable_search (A 兜底) + MiniMax-M3 (判断)** — Gemini grounding 备选 P1
- **Q4** 评审反方准备 = **双闸防幻觉** — evidence 域白名单 + URL ⊆ 证据表 + 数字 ≥2 源交叉; 状态机留 P1

---

## 1. 范围 (In/Out)

### 1.1 IN (v1.0.22 ship)

1. **新建 `fact_check_evidence` 表** (migration 025) — 存 query/url/domain/snippet/fetched_at/model/provider/conclusion/rationale + job_id + article_id + evidence_seq + created_at
2. **新增 `llm_jobs.task_type = 'fact_check_plus'`** — 与旧 `fact_check` 并存 (向后兼容)
3. **新增 `fact_check_plus.md` prompt** — 三档标签 + 强制 evidence JSON 输出 + 引用自报
4. **`moni/llm/client.py:call_llm` 透传 `-t web` 等 extra_args** — 已有 `extra_args` 字段 (client.py:55, :80), 仅在 fact_check_plus 分支启用
5. **`moni/llm/jobs.py` 新分支** — 写库分支加 `has_fact_check=1` (修隐债); `fact_check_plus` 分支处理 evidence 落库
6. **新端点**:
   - `POST /api/articles/{id}/fact-check-plus` → 202 (复用 articles.py:326 模式)
   - `POST /api/fact-check-plus/batch` → 202 (article_ids ≤ 20)
   - `GET /api/fact-check-plus/evidence/{article_id}` → 证据列表
7. **method 收敛迁移** — `UPDATE articles SET fact_check_method='llm_inference' WHERE fact_check_method='llm'` (9 行)
8. **修隐债**:
   - jobs.py:340 写库值 `'llm'` → `'llm_inference'`
   - jobs.py 写库分支补 `has_fact_check=1` (现 enrichment 写, worker 不写)
9. **废弃死代码** — `moni/web/routes/llm.py:88-123` `/api/llm/fact-check` route 删 + `tests/test_api.py` 同步删引用
10. **前端增量** — 文章详情 fact-check 区加 "联网核查" 按钮 + 证据链折叠; 不加新 tab
11. **双闸防幻觉** — 服务端校验:
    - 引用 URL ⊆ evidence 表 (LLM 自报 URL 必须能在本次 evidence 列表找到)
    - 域白名单 (`pbc.gov.cn` / `cbirc.gov.cn` / `csrc.gov.cn` / `samr.gov.cn` / 交易所 / 上市公司公告域 / 官方媒体域) 命中才允许【已核实】, 仅自媒体最多【待核实】
    - 纯自报无 snippet 证据丢弃

### 1.2 OUT (P1+ 留 v1.0.23+)

- 简报 enrichment `fact_check_mode: web` 模式
- 人工覆写 draft→verified 状态机 (与 cases 库同构, 复用 events review 模式)
- 看板 method 分布 / 跨源一致性统计
- DDG lite 通用提取器 (C 主通道的兜底组件, 不在 v1.0.22)
- gemini-2.5-pro grounding 备用 (Q3 的 D 选项第二通道)
- 简报正文显示 evidence 链接
- stats.js method 分布 (看板增量)

### 1.3 Non-goals (本次不做)

- 不引新依赖 (LangChain / Firecrawl 自托管 / Tavily)
- 不改现有 fact_check.md 长版 prompt (现有用户感知不变)
- 不动 share worker subprocess (enrichment 路径暂不并入 llm_jobs)
- 不改 schema_version 主键模式 (migration 025 继续单 PK INSERT OR IGNORE)

---

## 2. 文件清单 (Q2=B 总工时估 2.5 天)

### 2.1 新文件 (5 个, ~340 行)

| 文件 | 行数 | 内容 |
|------|------|------|
| `moni/db/migrations/025_fact_check_evidence.sql` | 35 | 建表 + 索引 + 刷存量 + INSERT OR IGNORE schema_version 25 |
| `moni/llm/prompts/fact_check_plus.md` | 50 | 联网版 prompt: 三档标签 + evidence JSON 输出规范 |
| `moni/web/routes/fact_check_plus.py` | 80 | 批量端点 + 证据查询端点 |
| `tests/test_fact_check_plus.py` | 120 | 8-10 测试: 单篇/批量/evidence/双闸/method 收敛/路由 410 |
| `docs/specs/2026-08-11-fact-check-plus-v1.0.22.md` | (本文件) | 不上 GH |

### 2.2 改文件 (6 个, + ~110 行)

| 文件 | 改法 | 内容 |
|------|------|------|
| `moni/llm/jobs.py` | +30 | task_type 白名单加 `'fact_check_plus'`; 写库分支加 `has_fact_check=1`; `'llm'` → `'llm_inference'`; fact_check_plus 写库 + evidence INSERT |
| `moni/llm/client.py` | +10 | `call_llm` 已有 `extra_args` (client.py:55), 无须改签名; fact_check_plus 分支默认注入 `-t web -Q --max-turns 8` (在 jobs.py 调处注入) |
| `moni/web/routes/articles.py` | +25 | `POST /api/articles/{id}/fact-check-plus` (复用 326 行 async 模式, task_type=fact_check_plus) |
| `moni/web/routes/__init__.py` | +2 | 注册 fact_check_plus_router |
| `moni/web/routes/llm.py` | -25 | 删 `/api/llm/fact-check` 死 route |
| `moni/web/static/index.html` | +15 | 文章详情 fact-check 区加 "联网核查" 按钮 + 证据链折叠 |
| `moni/web/static/js/app.js` | +40 | `enqueueLlmJobFactCheckPlus` (复用现有 enqueueLlmJob 模式) + 证据展开渲染 |
| `moni/web/static/css/main.css` | +20 | `.evidence-*` 类 + 折叠样式 |

### 2.3 改测试 (1 个)

| 文件 | 改法 | 内容 |
|------|------|------|
| `tests/test_api.py` | -X | 删 `/api/llm/fact-check` 引用 (按 grep 实际命中) |

### 2.4 总计

5 新 + 8 改 + 1 改测试 = ~340 新行 + ~110 改行 = **~450 行 net add**, 与聚合稿 §3.1 估算一致。

---

## 3. 数据契约

### 3.1 新表 `fact_check_evidence` (migration 025)

```sql
CREATE TABLE IF NOT EXISTS fact_check_evidence (
    id           TEXT PRIMARY KEY,            -- uuid4
    article_id   TEXT NOT NULL,               -- FK -> articles.id
    job_id       TEXT,                        -- llm_jobs.id (可空: 人工/CLI)
    query        TEXT NOT NULL,               -- 检索 query
    url          TEXT NOT NULL,               -- 命中的来源 URL
    domain       TEXT NOT NULL,               -- url 域名 (校验/白名单用)
    snippet      TEXT NOT NULL,               -- 引用证据片段
    fetched_at   TEXT NOT NULL,               -- 检索时间
    model        TEXT NOT NULL,               -- 判断模型 (快照)
    provider     TEXT NOT NULL,               -- hermes provider (快照)
    conclusion   TEXT NOT NULL,               -- 【已核实】/【待核实】/【存疑】/【无需核查】
    rationale    TEXT,                        -- 模型依据 (可选)
    evidence_seq INTEGER NOT NULL DEFAULT 0,  -- 同一 article 内顺序
    created_at   TEXT NOT NULL,
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_fce_article ON fact_check_evidence(article_id);
CREATE INDEX IF NOT EXISTS idx_fce_url     ON fact_check_evidence(url);
CREATE INDEX IF NOT EXISTS idx_fce_job     ON fact_check_evidence(job_id);

-- 存量化债: 刷 'llm' → 'llm_inference'
UPDATE articles SET fact_check_method='llm_inference' WHERE fact_check_method='llm';

-- schema_version 单 PK 必须 INSERT OR IGNORE (sprint 05 教训)
INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (25, datetime('now'));
```

**触发器**: 不加 (fact_check_evidence 是 append-only, 不需要 sync)

### 3.2 域白名单 (服务端常量, 暂写死)

```python
# moni/web/routes/fact_check_plus.py
DOMAIN_WHITELIST = {
    # 监管机构
    "pbc.gov.cn", "cbirc.gov.cn", "csrc.gov.cn", "samr.gov.cn",
    "stats.gov.cn", "gov.cn", "miit.gov.cn",
    # 交易所
    "sse.com.cn", "szse.cn", "hkex.com.hk", "nyse.com", "nasdaq.com",
    # 主流财经媒体
    "stcn.com", "cs.com.cn", "cnstock.com", "jjckb.cn", "xinhuanet.com",
    "people.com.cn", "cctv.com", "china.com.cn", "gmw.cn",
    "cls.cn", "yicai.com", "caixin.com", "21jingji.com",
    # 央广/财经
    "cnr.cn", "jrj.sh.gov.cn",
    # 公司一手 (财报/公告) — 域名正则匹配更准, 见实现
}
DOMAIN_WHITELIST_REGEX = [
    r".*\.gov\.cn$",
    r".*\.sse\.com\.cn$",
    r".*\.szse\.cn$",
]
```

**白名单判定规则** (双闸第 2 闸):
- 完全域名匹配白名单 → 命中
- 正则匹配白名单正则 → 命中
- 都不命中 → 仅【待核实】, 不允许【已核实】

### 3.3 API 契约

#### 端点 1: `POST /api/articles/{id}/fact-check-plus`

请求体:
```json
{
  "provider": "minimax-cn",     // 可选, 默认从 llm_settings 读
  "model": "MiniMax-M3",        // 可选, 默认从 llm_settings 读
  "force": false                // 可选, 已做过联网核查是否覆盖
}
```

响应 (202):
```json
{
  "job_id": "uuid4",
  "status": "pending",
  "article_id": "...",
  "task_type": "fact_check_plus",
  "poll_url": "/api/llm/jobs/{job_id}"
}
```

错误:
- 404 Article not found
- 503 No active fact_check model configured
- 409 Duplicate active fact_check (同 article 已有 pending/running, 与 brief 防呆同模式)

#### 端点 2: `POST /api/fact-check-plus/batch`

请求体:
```json
{
  "article_ids": ["a",", "b"],  // ≤ 20
  "reason": "manual"            // 或 "auto"
}
```

响应 (202):
```json
{
  "enqueued": 2,
  "job_ids": ["uuid1",", "uuid2"],
  "skipped": 0,
  "errors": []
}
```

错误:
- 422 article_ids 长度超 20 或为空
- 503 No active fact_check model configured

#### 端点 3: `GET /api/fact-check-plus/evidence/{article_id}`

响应 (200):
```json
{
  "article_id": "...",
  "items": [
    {
      "query": "人行 2026 数字人民币最新政策",
      "url": "https://www.news.cn/...",
      "domain": "xinhuanet.com",
      "snippet": "新华社: 央行宣布...",
      "fetched_at": "2026-08-11T...",
      "model": "MiniMax-M3",
      "provider": "minimax-cn",
      "conclusion": "【已核实】",
      "rationale": "...",
      "evidence_seq": 1
    }
  ]
}
```

错误:
- 404 Article not found
- 200 + items=[] (没做过联网核查, 不算错)

---

## 4. LLM 调用链路

### 4.1 调用链 (B 架构, hermes -t web)

```
POST /api/articles/{id}/fact-check-plus
  ↓ routes/articles.py:386 (新 endpoint, 复制 326 行模式)
  ↓ enqueue_job(task_type='fact_check_plus', ...)
  ↓ llm_jobs row (status=pending)
  ↓ worker _run_job 抢到
  ↓ render_fact_check_plus(article_dict) → prompt (含 title/url/content/published_at)
  ↓ call_llm(provider, model, prompt, extra_args=['-t', 'web', '-Q', '--max-turns', '8'])
  ↓ subprocess hermes chat -q ... -t web -Q --max-turns 8 (Nous 托管 web 工具/agent 模式)
  ↓ LLM 输出: 文本 + 内嵌 evidence JSON block (query/url/snippet/conclusion)
  ↓ _parse_fact_check_plus_evidence(output)
  ↓ 双闸校验:
      闸 1: URL ⊆ LLM 自报 evidence (无 snippet 丢弃)
      闸 2: 域白名单 (命中→可给【已核实】; 不命中→最多【待核实】)
  ↓ UPDATE articles SET fact_check_conclusion, fact_check_method='web_search',
                        fact_checked_at, fact_check_note, has_fact_check=1
  ↓ INSERT fact_check_evidence (一条或多条, 按 evidence_seq 顺序)
  ↓ UPDATE llm_jobs SET status='done', result=output_text
```

### 4.2 prompt (`fact_check_plus.md` 草稿)

```markdown
# 角色
你是 moni 联网事实核查助手。读者：银行管理层。

# 任务
基于文内声明, 联网检索权威来源 (gov / 交易所 / 主流财经媒体), 给出三档标签:
- 【已核实】: ≥2 个白名单域名源 (pbc.gov.cn / 主流媒体 / 上市公司公告) 独立支持
- 【待核实】: 仅自媒体命中 或 检索结果不足
- 【存疑】: 数字与文内声明矛盾 或 检索结果冲突
- 【无需核查】: 纯观点文, 无可验证的具体主张

# 强约束
1. **不联网则不得给【已核实】**: 无证据只能给【待核实】/【存疑】
2. **每条 evidence 必须有 snippet**: 纯 URL 无 snippet 丢弃
4. **禁止编造 URL**: 引用 URL 必须来自本次检索结果, 不可训练记忆替代
5. **数字类要求 ≥2 源一致**: 单源数字最多【待核实】

# 严格 JSON 输出 (必须严格按此结构, 无其他文字)

```json
{
  "conclusion": "【已核实】|...| (≤20 字)",
  "evidence": [
    {
      "query": "本次检索的关键词",
      "url": "https://...",
      "snippet": "引用证据片段 (≤200 字)",
      "rationale": "该证据如何支持结论 (≤50 字)"
    }
  ]
}
```

# 文章
标题: {title}
URL: {url}
发布日期: {published_at}

正文:
{content}
```

注: 用 string.Template 渲染 ($title / $url / $published_at / $content), JSON 示例里的 {} 不冲突 (与 cases sprint 05 同模式)。

### 4.3 双闸实现 (jobs.py 伪代码)

```python
def _run_fact_check_plus(job_id, article_id, provider, model):
    prompt = render_fact_check_plus(article_dict)
    extra_args = ['-t', 'web', '-Q', '--max-turns', '8']
    raw = call_llm(provider, model, prompt, extra_args=extra_args)

    parsed = _parse_fact_check_plus_evidence(raw)  # 提取 evidence 数组
    if not parsed:
        _mark_failed(job_id, "evidence parse failed")
        return

    # 闸 1: snippet 非空 + URL 合法
    valid_evidence = [e for e in parsed['evidence']
                     if e.get('url') and e.get('snippet') and _valid_url(e['url'])]
    if not valid_evidence:
        _mark_failed(job_id, "no evidence with snippet")
        return

    # 闸 2: 域白名单 → 决定 conclusion 等级
    conclusion = parsed['conclusion']
    has_whitelist = any(_in_whitelist(e['domain']) for e in valid_evidence)
    if conclusion == '【已核实】' and not has_whitelist:
        conclusion = '【待核实】'  # 强制降级

    # 落库
    now = _now()
    conn.execute("""
        UPDATE articles SET
            fact_check_conclusion=?, fact_check_method='web_search',
            fact_checked_at=?, fact_check_note=?, has_fact_check=1
        WHERE id=?
    """, (conclusion, now, json.dumps(valid_evidence, ensure_ascii=False), article_id))

    for i, e in enumerate(valid_evidence):
        conn.execute("""
            INSERT INTO fact_check_evidence
                (id, article_id, job_id, query, url, domain, snippet,
                 fetched_at, model, provider, conclusion, rationale,
                 evidence_seq, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (uuid4().hex, article_id, job_id, e['query'], e['url'],
              e['domain'], e['snippet'], now, model, provider,
              conclusion, e.get('rationale', ''), i, now))
    conn.commit()
```

---

## 5. 前端契约

### 5.1 文章详情区 (index.html L2284 附近)

现有「事实核查」按钮保留, 在它**右侧**加「联网核查」按钮:

```html
<div class="fact-check-actions">
  <button class="btn btn-secondary"
          @click="enqueueLlmJob(article.id, '/summary', 'fact_check')"
          x-show="!article.has_fact_check">
    事实核查
  </button>
  <button class="btn btn-primary"
          @click="enqueueLlmJobFactCheckPlus(article.id)"
          x-show="!article.has_web_fact_check || force">
    联网核查
  </button>
</div>
```

### 5.2 证据链展开 (文章详情 fact-check 区下方)

```html
<div class="fact-check-evidence" x-show="article.has_web_fact_check">
  <details>
    <summary>
      <span class="badge web_search">联网核查</span>
      <span x-text="article.fact_check_conclusion"></span>
      <small x-text="article.fact_checked_at"></small>
    </summary>
    <ol class="evidence-list">
      <template x-for="(e, idx) in articleEvidenceList" :key="idx">
        <li class="evidence-item">
          <div class="evidence-query" x-text="e.query"></div>
          <a class="evidence-url" :href="e.url" target="_blank" x-text="e.url"></a>
          <div class="evidence-snippet" x-text="e.snippet"></div>
          <div class="evidence-meta">
            <span x-text="e.domain"></span>
            <span x-text="e.fetched_at"></span>
            <span x-text="e.model"></span>
          </div>
        </li>
      </template>
    </ol>
  </details>
</div>
```

### 5.3 app.js 新函数

```javascript
async function enqueueLlmJobFactCheckPlus(articleId) {
  const r = await fetch(`/api/articles/${articleId}/fact-check-plus`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({})
  });
  if (!r.ok) {
    moniToast.error('联网核查入队失败');
    return;
  }
  const data = await r.json();
  pollJobUntilDone(data.job_id, async (job) => {
    if (job.status === 'done') {
      const evidence = await fetch(`/api/fact-check-plus/evidence/${articleId}`).then(r => r.json());
      articleEvidenceList = evidence.items;
      article.has_web_fact_check = true;
      article.fact_check_conclusion = job.result.conclusion || '已完成';
      moniToast.success('联网核查完成');
    } else {
      moniToast.error(`联网核查失败: ${job.error || 'unknown'}`);
    }
  });
}
```

### 5.4 缓存 bust (v1.0.16 教训)

`index.html` `?v=` 改为 `?v=20260811001` (sprint v1.0.22 当日序号 001)。
每次改 app.js / index.html / main.css 必 bump。

---

## 6. 测试 (test_fact_check_plus.py)

| 测试名 | 验证 |
|--------|------|
| `test_fact_check_plus_endpoint_202_with_job_id` | 端点返 202 + job_id + poll_url |
| `test_fact_check_plus_404_unknown_article` | 未知 article_id 返 404 |
| `test_fact_check_plus_503_no_active_model` | 无 settings 时返 503 |
| `test_fact_check_plus_409_duplicate_active` | 同 article 已有 pending/running 返 409 (与 brief 同模式) |
| `test_fact_check_plus_batch_limit_20` | > 20 返 422 |
| `test_fact_check_plus_evidence_endpoint_returns_list` | GET evidence 按 evidence_seq 排序 |
| `test_fact_check_plus_evidence_empty_when_not_done` | 没做过返 200 + items=[] |
| `test_fact_check_plus_method_migration_llm_to_llm_inference` | 迁移 SQL 把 'llm' 刷成 'llm_inference' (验证 9 → 0) |
| `test_fact_check_plus_worker_writes_has_fact_check` | worker 完成后 has_fact_check=1 (修隐债验证) |
| `test_fact_check_plus_double_gate_domain_whitelist` | 无白名单源时【已核实】强制降级为【待核实】 |
| `test_fact_check_plus_evidence_table_insert` | evidence INSERT 成功 + FK CASCADE 删 article 时级联 |
| `test_old_fact_check_endpoint_returns_410` | `/api/llm/fact-check` 返 410 (废弃死代码) |
| `test_routes_init_registers_fact_check_plus_router` | routes/__init__.py 包含 fact_check_plus_router |
| `test_jobs_task_type_whitelist_includes_plus` | enqueue_job('fact_check_plus', ...) 不抛 ValueError |

预估 14 个测试, ~120 行。

---

## 7. 风险与缓解 (Q4=B 双闸)

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| provider 配额 429 (green-frog 实测 2026-08-17 重置) | 高 | 批量半失败 | 强制 pin provider (llm_settings 解析); jobs.py MAX_ATTEMPTS=2 已有; 端点返 409 让用户重试 |
| hermes -t web 引用自编 | 中 | 审计失信 | 双闸: 强制 evidence JSON + URL 域白名单 + snippet 非空校验 |
| hermes -t web 不返回证据 | 中 | 端到端空 | 解析失败 → mark_failed → 不写库 → UI 显式失败状态 |
| LLM 输出不是严格 JSON | 中 | 端到端空 | _parse_fact_check_plus_evidence 容错 (regex 抽 evidence 数组, 失败 fallback 整段) |
| 域白名单不全 (遗漏某监管源) | 中 | 【已核实】漏判 | v1.0.22 列可配白名单 (config/fact_check_plus.yaml), v1.0.23 扩 |
| evidence 表膨胀 | 低 | DB size | FK ON DELETE CASCADE 已设, 删 article 级联; article 自身走正常清理 |
| 双闸 regex 误伤合法 URL | 低 | 白名单误杀 | 正则限定 .gov.cn / .sse.com.cn / .szse.cn 顶级域, 不会误伤 |
| 客户端 `enqueueLlmJobFactCheckPlus` 失败重试 | 低 | UI 卡死 | pollJobUntilDone 有 timeout (复用现有模式) |
| test 跑慢 (LLM mock) | 中 | CI 耗时 | call_llm 用 monkeypatch + 固定 mock 返回, 测试 0 真实 LLM 调用 |
| 简报 enrichment web 模式缺失 | 低 (本版 OUT) | P1 留 | 已声明, 不在 v1.0.22 范围 |

---

## 8. 实施顺序 (Q2=B 估 2.5 天)

- **Day 1 (前半天)**: migration 025 (建表 + 刷存量 + INSERT OR IGNORE schema_version 25) + 修 jobs.py 隐债 (写库分支加 has_fact_check=1 + 'llm'→'llm_inference') + 删 llm.py:88 死 route + 删 tests/test_api.py 引用
- **Day 1 (后半天)**: jobs.py 加 fact_check_plus 分支 + prompt 文件 + call_llm extra_args 验证
- **Day 2 (前半天)**: 新端点 (articles.py + fact_check_plus.py + __init__.py 注册) + 域白名单常量
- **Day 2 (后半天)**: tests/test_fact_check_plus.py 14 个测试, 全部 pass
- **Day 3**: 前端增量 (index.html + app.js + main.css + cache-bust) + 端到端 curl 验证 3 端点
- **Day 3.5 (留 buffer)**: pytest 全量 ≥656 passed (655 + 新增 - 删的死 route 测试) + 浏览器手动 1 篇 evidence 展开验证 + 双闸场景测 (无白名单 URL 强制降级)
- **Day 4**: branch + commit + push + gh pr create + user review
- **Day 4.5**: gh pr merge --squash + git tag v1.0.22 + force push tag + kill 8900 + 重启 (绝对路径 `/usr/local/bin/python3`)
- **Day 5**: 8900 端点 200 + 申报截图 (UI 改动) + STATUS.md + USER_GUIDE.md + HANDOFF.md + vibe-coding-log

---

## 9. 验收清单 (Definition of Done)

- [ ] pytest tests/test_fact_check_plus.py 14 全 pass
- [ ] pytest tests/ 全量 ≥656 passed (655 + 新增, 允许 2 skip 已知)
- [ ] migration 025 idempotent (跑两遍 schema_version 不重复)
- [ ] `sqlite3 data/news.db "SELECT COUNT(*) FROM fact_check_evidence WHERE method='web_search'"` ≥ 1 (跑过真实入队)
- [ ] `sqlite3 data/news.db "SELECT COUNT(*) FROM articles WHERE fact_check_method='llm'"` = 0 (刷存量成功)
- [ ] curl 5 端点 200: `/api/articles/{id}/fact-check-plus` / `/api/fact-check-plus/batch` / `/api/fact-check-plus/evidence/{id}` / `/api/llm/jobs/{id}` / `/api/articles`
- [ ] 浏览器手动 1 篇 evidence 展开验证 (3 档标签正确, 域白名单降级生效)
- [ ] 双闸场景测 (无 gov.cn URL → 【已核实】降级 【待核实】)
- [ ] 8900 重启 PID 验证 + `ps eww | grep BESTBLOGS_API_KEY` 不空
- [ ] cache-bust `?v=20260811001` 已 bump
- [ ] node --check app.js PASS (JS 缩进污染教训)
- [ ] pyflakes / ruff (若用) 无新 warning
- [ ] moni-pr-workflow pitfall 8: tag 是 squash commit SHA (不是 feature tip)
- [ ] moni-pr-workflow pitfall 17: `git log origin/main -1` 实际前进
- [ ] moni-pr-workflow pitfall 19: `git tag -l | sort -V | tail` 确认 v1.0.22 是最新
- [ ] AGENTS.md / STATUS.md / USER_GUIDE.md / HANDOFF.md / vibe-coding-log.md 同步

---

## 10. 等 dad 拍板

spec 写完, **不动业务代码**, 等你 3 件事:
1. 拍板 spec (5 章你看)
2. 拍板 sprint 节奏 (Q2=B 估 2.5 天, Day 5 内 ship v1.0.22 + PR + tag + 重启)
3. 拍板 v1.0.22 branch name: `feature/v1.0.22-fact-check-plus` (沿用 moni 命名)

任一拍板有改动, 我改 spec 文档再回来; 拍板 OK 我开 feature 分支 + 写代码。

**Status**: spec draft, 等 dad 拍板.