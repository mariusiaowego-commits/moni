# Plan: BestBlogs API 按需接入 — 分享时拉取增强元数据

> v1.0.5.25, 2026-06-14

## 背景

BestBlogs OpenAPI v2 免费可用（Pro/Free 均可），提供：
- `score` (0-100) 综合质量分
- `summary` 详细摘要
- `oneSentenceSummary` 一句话摘要
- `mainPoints` [{point, explanation}] 要点列表
- `keyQuotes` ["quote1", ...] 金句
- `tags` ["AI Agent", ...] BestBlogs 标签（≠ moni tag）

Free 用户每天 50 次调用额度。Pro 体验版 17 号到期后降为 Free。

## 设计原则

1. **叠加不替代**：保留 moni 已有的 LLM 能力（AI 摘要、事实核查、外部 Markdown 导入），BestBlogs API 作为新增选项
2. **用户选择**：分享时让用户决定用哪个数据源
3. **按需拉取**：只在分享时触发，不浪费额度
4. **幂等**：已拉取的文章不重复调用（bb_fetched_at 非空即跳过）
5. **只做 bestblogs 源**：非 bestblogs 源不尝试搜索（省额度）

---

## 现有能力（保留不动）

| 能力 | 实现方式 | 位置 |
|------|----------|------|
| AI 摘要 | hermes CLI 调 LLM | summary_gen.py |
| 事实核查 | hermes CLI 调 LLM | summary_gen.py |
| 外部 Markdown 导入 | 用户复制 URL → POST /api/articles/{id}/import-fulltext | routes/articles.py |

这些能力完整保留，不删不改。

---

## 新增能力（BestBlogs API）

| 能力 | 来源 | 字段 |
|------|------|------|
| 质量评分 | BestBlogs API | bb_score (0-100) |
| 专业摘要 | BestBlogs API | bb_summary |
| 一句话洞察 | BestBlogs API | bb_one_sentence |
| 要点列表 | BestBlogs API | bb_main_points (JSON) |
| 金句 | BestBlogs API | bb_key_quotes (JSON) |
| 专业标签 | BestBlogs API | bb_tags (JSON) |

---

## 用户选择机制

### 分享时的选择面板

```
┌─────────────────────────────────────────────────┐
│ 分享选项                                        │
│                                                 │
│ 📝 摘要来源:                                    │
│   ○ moni AI 摘要（LLM 生成）                    │
│   ○ BestBlogs 摘要（专业团队，需 API 额度）      │
│   ○ 两者都展示（对比）                           │
│   ○ 不展示摘要                                  │
│                                                 │
│ ✅ 事实核查:                                    │
│   ☑ 包含（LLM 生成，仅 moni AI 摘要模式可用）   │
│                                                 │
│ 📊 BestBlogs 增强数据（需 API 额度）:           │
│   ☑ 质量评分 (score)                            │
│   ☑ 要点列表 (mainPoints)                       │
│   ☑ 金句 (keyQuotes)                            │
│   ☐ 专业标签 (bbTags)                           │
│                                                 │
│ 🔗 外部 Markdown:                               │
│   ☐ 包含（需手动粘贴 URL）                      │
└─────────────────────────────────────────────────┘
```

### 前端状态逻辑

- 非 bestblogs 源的文章 → BestBlogs 选项灰置（不可选）
- BestBlogs 增强数据勾选任一 → 触发 API 拉取（消耗额度）
- 摘要选 "moni AI" → 走 LLM（现有逻辑不变）
- 摘要选 "BestBlogs" → 走 API（不消耗 LLM 额度）
- 摘要选 "两者都展示" → API + LLM 都调用
- 事实核查 → 只在摘要选 "moni AI" 或 "两者" 时可用

---

## STEP 1: DB Schema 扩展

### Migration 013: articles 表加 bb_* 字段

```sql
ALTER TABLE articles ADD COLUMN bb_score INTEGER;
ALTER TABLE articles ADD COLUMN bb_summary TEXT;
ALTER TABLE articles ADD COLUMN bb_one_sentence TEXT;
ALTER TABLE articles ADD COLUMN bb_main_points TEXT;   -- JSON [{point, explanation}]
ALTER TABLE articles ADD COLUMN bb_key_quotes TEXT;    -- JSON ["quote1", ...]
ALTER TABLE articles ADD COLUMN bb_tags TEXT;          -- JSON ["AI Agent", ...]
ALTER TABLE articles ADD COLUMN bb_resource_id TEXT;   -- RAW_xxxxx
ALTER TABLE articles ADD COLUMN bb_fetched_at TEXT;    -- ISO8601, 拉取时间
```

### 更新 schema_version → 13

---

## STEP 2: BestBlogs API Client 模块

### 新建 `moni/fetch/bestblogs_api.py`

```python
"""BestBlogs OpenAPI v2 client — 按需拉取文章增强元数据."""

def fetch_meta(url: str) -> dict | None:
    """用 URL 搜索 BestBlogs → 拿到 resourceId → 调 /meta → 返回字段.
    
    流程:
    1. GET /openapi/v2/search?q={url_encoded}&pageSize=1
       → 匹配 URL 拿到 resourceId
    2. GET /openapi/v2/resources/{resourceId}/meta
       → 返回 score, summary, oneSentenceSummary, mainPoints, keyQuotes, tags
    
    返回 None 表示未找到或 API 错误。
    """

def fetch_and_store(article_id: str) -> dict | None:
    """拉取 BestBlogs 元数据并写入 DB.
    
    1. 查 DB 拿文章 URL + source
    2. source != 'bestblogs' → 跳过
    3. bb_fetched_at 非空 → 跳过（已拉取）
    4. 调 fetch_meta(url)
    5. 写入 bb_* 字段 + bb_fetched_at
    """
```

### 依赖
- API Key 从环境变量 `BESTBLOGS_API_KEY` 读取
- 使用 `httpx`（项目已有依赖）
- 超时：search 20s, meta 15s

### 错误处理
- 无 API Key → 跳过，日志 warning
- 搜索无结果 → 返回 None，不报错（标记 bb_fetched_at 防重试）
- 429 Rate Limit → 日志 warning，返回 None（不标记 fetched，下次重试）
- 网络超时 → 日志 warning，返回 None

---

## STEP 3: Share Pipeline 集成

### 修改 `scripts/default_worker.sh`

在 fetching 阶段（~line 340，article_content 构建完成后）插入：

```bash
# BestBlogs 按需拉取增强元数据（只在用户选了 BB 增强选项时触发）
if [ "$bb_enhance" = "true" ]; then
  for article_id in $article_ids; do
    source=$(sqlite3 "$DB_PATH" "SELECT source FROM articles WHERE id='$article_id'")
    bb_fetched=$(sqlite3 "$DB_PATH" "SELECT bb_fetched_at FROM articles WHERE id='$article_id'")
    
    if [ "$source" = "bestblogs" ] && [ -z "$bb_fetched" ]; then
      python3 -c "
from moni.fetch.bestblogs_api import fetch_and_store
fetch_and_store('$article_id')
"
    fi
  done
fi
```

### 传递给 LLM prompt

根据用户选择，动态组装文章数据块：

```
### 文章: {title}
ID: {id}
{content}

[如果选了 moni AI 摘要]
**AI 摘要**: {ai_summary}

[如果选了 BestBlogs 摘要]
**BestBlogs 评分**: {bb_score}/100
**BestBlogs 摘要**: {bb_summary}
**一句话洞察**: {bb_one_sentence}

[如果勾了 BestBlogs 要点]
**要点**: {bb_main_points}

[如果勾了 BestBlogs 金句]
**金句**: {bb_key_quotes}

[如果勾了 BestBlogs 标签]
**BestBlogs 标签**: {bb_tags}

[如果选了事实核查且选了 moni AI 摘要]
**事实核查**: {fact_check}
```

---

## STEP 4: 格式 Prompt 适配

### 4.1 content-structure.md（核心分析管道）

追加条件段落：

```markdown
## 数据来源说明

文章数据中可能包含以下来源的字段，按优先级使用：

### 摘要
- 如果有 **AI 摘要**（标注 `AI 摘要`），使用它作为主要摘要
- 如果有 **BestBlogs 摘要**（标注 `BestBlogs 摘要`），使用它作为主要摘要
- 如果两者都有，分别展示并标注来源
- 摘要来源由用户选择，不要自动替换

### 增强数据
- **BestBlogs 评分**：在标题旁显示 `⭐ {score}/100`
- **要点**：渲染为编号列表
- **金句**：渲染为引用块
- **BestBlogs 标签**：追加到 moni tags 后面，用不同样式区分（如 `[BB: AI Agent]`）

### 事实核查
- 只在有 AI 摘要时才展示事实核查结果
- BestBlogs 模式不包含事实核查
```

### 4.2 各格式具体适配

| 格式 | BB score | BB summary | mainPoints | keyQuotes | bbTags | AI摘要 | 事实核查 |
|------|----------|------------|------------|-----------|--------|--------|----------|
| **markdown** | 标题后 | 替代/并列 | 要点列表 | `>` 引用 | `[BB: x]` | 保留 | 保留 |
| **html** | 徽章 | 替代/并列 | `<ul>` | `<blockquote>` | 不同色 badge | 保留 | 保留 |
| **rich_text** | 行内 | 替代/并列 | 编号列表 | 引用格式 | 行内标签 | 保留 | 保留 |
| **pdf** | 封面 | 替代/并列 | 要点框 | 引述段 | 标签行 | 保留 | 保留 |
| **info_graphic_png** | 角标 | 不用 | 核心要点 | 不用 | 不用 | 保留 | 不用 |
| **report_png** | 数字 | oneSentence | 不用 | 不用 | 不用 | 保留 | 保留 |

### 4.3 各格式 Prompt 修改清单

1. `format-markdown.md` — 追加 BestBlogs 条件段（摘要来源选择 + 增强数据）
2. `format-html.md` — 追加 BestBlogs 条件段
3. `format-rich-text.md` — 追加 BestBlogs 条件段
4. `format-pdf.md` — 追加 BestBlogs 条件段
5. `format-info-graphic-png.md` — 只用 score + mainPoints
6. `format-report-png.md` — 只用 score + oneSentenceSummary

---

## STEP 5: 前端 UI

### 分享选项 Modal 扩展

在现有分享 Modal 中追加 BestBlogs 选项区：

```
摘要来源: [moni AI ▾] [BestBlogs ▾] [两者 ▾] [无 ▾]
事实核查: [☑] (仅 moni AI 模式可用)
BestBlogs 增强: [☑ score] [☑ mainPoints] [☑ keyQuotes] [☐ bbTags]
外部 Markdown: [☐ 包含]
```

### 前端状态管理

- `shareSummarySource`: 'moni_ai' | 'bestblogs' | 'both' | 'none'
- `shareIncludeFactCheck`: boolean
- `shareBBEnhance`: { score: bool, mainPoints: bool, keyQuotes: bool, bbTags: bool }
- `shareIncludeExternalMd`: boolean

### 状态联动
- 非 bestblogs 源 → BB 选项全部灰置
- shareSummarySource = 'bestblogs' → 事实核查自动关闭
- shareBBEnhance 任一勾选 → 标记需要 API 调用

---

## STEP 6: 额度控制

### 策略
- `bb_fetched_at` 非空 = 已拉取，跳过
- 单次分享：N 篇 bestblogs 源且未拉取的文章 = N 次 API 调用
- 日志记录每次调用（方便追踪额度使用）
- 无 API Key 时静默跳过，不影响分享流程
- 搜索无结果时标记 bb_fetched_at（防重试浪费额度）
- Rate Limit 时不标记（下次可重试）

### 未来可选
- 每日调用计数（SQLite 表 or 文件）
- 超限时自动跳过 + 提示用户

---

## STEP 7: 测试

### 单元测试
- `tests/test_bestblogs_api.py`
  - test_fetch_meta_success（mock API 响应）
  - test_fetch_meta_no_api_key（跳过）
  - test_fetch_meta_search_miss（返回 None，标记 fetched_at）
  - test_fetch_meta_rate_limit（返回 None，不标记 fetched_at）
  - test_fetch_and_store_writes_db（验证 DB 写入）
  - test_fetch_and_store_skip_already_fetched（幂等）
  - test_fetch_and_store_skip_non_bestblogs（非 BB 源跳过）

### 集成测试
- 手动测试：选中一篇 bestblogs 文章 → 点分享 → 验证 API 调用 + DB 写入
- 验证已拉取的文章不重复调用
- 验证非 bestblogs 源不触发 API
- 验证分享选项 UI 状态联动

---

## 实施顺序

```
STEP 1: DB Migration (5min)
STEP 2: bestblogs_api.py (30min)
STEP 3: default_worker.sh 集成 (20min)
STEP 4: 6 个格式 Prompt 适配 (40min)
STEP 5: 前端 UI 分享选项扩展 (30min)
STEP 6: 额度控制 (10min)
STEP 7: 测试 (30min)
STEP 8: PR + 文档 (15min)
```

总计约 3 小时。

---

## 涉及文件

| 文件 | 改动类型 |
|------|----------|
| `moni/db/migrations/013_bestblogs_meta.sql` | 新建 |
| `moni/db/migrate.py` | 改版本号 |
| `moni/fetch/bestblogs_api.py` | 新建 |
| `scripts/default_worker.sh` | 改（插入 BB 拉取逻辑） |
| `moni/share/prompts/content-structure.md` | 改（追加 BB 条件段） |
| `moni/share/prompts/format-markdown.md` | 改 |
| `moni/share/prompts/format-html.md` | 改 |
| `moni/share/prompts/format-rich-text.md` | 改 |
| `moni/share/prompts/format-pdf.md` | 改 |
| `moni/share/prompts/format-info-graphic-png.md` | 改 |
| `moni/share/prompts/format-report-png.md` | 改 |
| `moni/web/static/index.html` | 改（分享 Modal 增加 BB 选项） |
| `moni/web/static/js/app.js` | 改（分享状态管理） |
| `tests/test_bestblogs_api.py` | 新建 |
