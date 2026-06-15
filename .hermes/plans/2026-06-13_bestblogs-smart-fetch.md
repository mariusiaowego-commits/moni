# BestBlogs 智能抓取 + 全文导入 Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** BestBlogs 源支持时间范围选择抓取 + 用户手动导入全文做 AI 分析

**Architecture:**
- Fetcher 层：先拉 OPML 全部 feed，按 pubDate 过滤用户选定时间范围，标题命中 tag keyword 才入库
- UI 层：抓取前弹出时间范围选择 modal；文章详情页加"复制原文 URL"和"导入全文"功能
- LLM 层：摘要/事实核查支持从本地 .md 文件读取全文（替代 raw_content）

**Tech Stack:** Python, FastAPI, Alpine.js, SQLite, httpx

---

## Phase 1: 时间范围选择抓取

### Task 1: fetch API 支持 query 参数 `date_from` / `date_to`

**Objective:** 让 `/api/fetch/sources/bestblogs/run` 接受可选的时间范围参数

**Files:**
- Modify: `moni/web/routes/fetch.py:133-157`
- Modify: `moni/fetch/run.py` (run_fetcher 函数)

**Step 1:** 在 `trigger_fetch_run` 端点加可选 query 参数：

```python
@router.post("/sources/{name}/run")
def trigger_fetch_run(
    name: str,
    date_from: str | None = None,  # ISO date, e.g. "2026-06-10"
    date_to: str | None = None,    # ISO date, e.g. "2026-06-13"
) -> dict[str, Any]:
```

**Step 2:** 传递给 `run_fetcher(resolved, date_from=date_from, date_to=date_to)`

**Step 3:** `run_fetcher` 透传给 fetcher 的 `run()` 方法

**Verify:** `curl -X POST "http://127.0.0.1:8900/api/fetch/sources/bestblogs/run?date_from=2026-06-10"` 返回 200

---

### Task 2: BestBlogs fetcher 按 pubDate 过滤

**Objective:** 在 fetcher 内部，只保留 pubDate 在用户选定时间范围内的文章

**Files:**
- Modify: `moni/fetch/sources/bestblogs.py` — `fetch()` 方法

**Step 1:** `fetch()` 方法接受 `date_from` / `date_to` 参数

**Step 2:** 在 `_fetch_feed()` 返回后，按 pubDate 过滤：

```python
if date_from or date_to:
    articles = [a for a in articles if _in_date_range(a.get("published_at"), date_from, date_to)]
```

**Step 3:** 加辅助函数 `_in_date_range(pub_date, from, to) -> bool`

**Verify:** 手动调用 fetcher，确认只返回指定日期范围内的文章

---

### Task 3: 标题匹配模式（只看标题，不看摘要）

**Objective:** tag keyword 只匹配标题，避免摘要中的 boilerplate 导致误命中

**Files:**
- Modify: `moni/fetch/tag_filter.py` — `apply_tag_keyword_filter()`

**Step 1:** 检查 article source 是否为 `bestblogs`

**Step 2:** 如果是 bestblogs，haystack 只用 title（不用 content）

```python
if source == "bestblogs":
    haystack = title
else:
    haystack = title + "\n" + content
```

**Verify:** pytest 测试 bestblogs source 只匹配标题

---

### Task 4: UI 时间范围选择 modal

**Objective:** 用户点"抓取 bestblogs"时弹出时间范围选择

**Files:**
- Modify: `moni/web/static/index.html` — 加 modal HTML
- Modify: `moni/web/static/js/app.js` — 加 state + 方法

**Step 1:** 加 Alpine.js state：

```javascript
// 抓取时间范围
fetchDateRange: 'auto',  // 'auto' | 'custom' | 'days'
fetchDateFrom: '',
fetchDateTo: '',
fetchDays: 7,
fetchDateModal: false,
fetchTargetSource: '',
```

**Step 2:** 加 modal HTML（在 fetch 管理 tab 附近）：

```html
<div class="modal-backdrop" x-show="fetchDateModal" x-cloak @click.self="fetchDateModal=false">
  <div class="modal-content">
    <h3>选择抓取时间范围</h3>
    <label><input type="radio" value="auto" x-model="fetchDateRange"> 自上次抓取至今</label>
    <label><input type="radio" value="days" x-model="fetchDateRange"> 过去
      <input type="number" x-model="fetchDays" min="1" max="365" style="width:60px"> 天
    </label>
    <label><input type="radio" value="custom" x-model="fetchDateRange"> 自定义
      <input type="date" x-model="fetchDateFrom"> ~ <input type="date" x-model="fetchDateTo">
    </label>
    <button @click="confirmFetchDate()">开始抓取</button>
  </div>
</div>
```

**Step 3:** 修改 `triggerFetch(name)` — 如果 source 是 bestblogs，先弹 modal：

```javascript
triggerFetch(name) {
  if (name === 'bestblogs') {
    this.fetchTargetSource = name;
    this.fetchDateModal = true;
    return;
  }
  // 其他源直接抓取...
}
```

**Step 4:** `confirmFetchDate()` 构建 query 参数并调用 API：

```javascript
confirmFetchDate() {
  this.fetchDateModal = false;
  let params = '';
  if (this.fetchDateRange === 'days') {
    const from = new Date(Date.now() - this.fetchDays * 86400000).toISOString().slice(0, 10);
    params = `?date_from=${from}`;
  } else if (this.fetchDateRange === 'custom') {
    params = `?date_from=${this.fetchDateFrom}&date_to=${this.fetchDateTo}`;
  }
  // auto: 不传参数，fetcher 自动判断
  fetch(`/api/fetch/sources/${this.fetchTargetSource}/run${params}`, { method: 'POST', ... })
    .then(...)
}
```

**Verify:** UI 上点 bestblogs 抓取 → 弹出 modal → 选时间 → 确认 → 返回结果

---

## Phase 2: 全文导入 + AI 分析

### Task 5: 文章详情页加"复制原文 URL"按钮

**Objective:** 用户一键复制原文 URL 到剪贴板

**Files:**
- Modify: `moni/web/static/index.html:1001` (查看原文按钮附近)

**Step 1:** 在"查看原文"按钮旁加"复制链接"按钮：

```html
<button class="btn-secondary" @click="copyArticleUrl()" title="复制原文链接">
  📋 复制链接
</button>
```

**Step 2:** app.js 加方法：

```javascript
copyArticleUrl() {
  if (!this.articleDetail?.url) return;
  navigator.clipboard.writeText(this.articleDetail.url)
    .then(() => { this.copyToast = true; setTimeout(() => this.copyToast = false, 2000); })
    .catch(() => { alert('复制失败，请手动复制: ' + this.articleDetail.url); });
},
copyToast: false,
```

**Verify:** 点击"复制链接"→ 剪贴板有 URL

---

### Task 6: 文章详情页加"导入全文"输入框

**Objective:** 用户粘贴本地 .md 文件路径，moni 读取后更新 raw_content

**Files:**
- Modify: `moni/web/static/index.html` — 文章详情 modal 加输入框
- Modify: `moni/web/static/js/app.js` — 加方法
- Modify: `moni/web/routes/articles.py` — 加 API 端点
- Create: `tests/test_import_fulltext.py`

**Step 1:** 新增 API 端点 `POST /api/articles/{article_id}/import-fulltext`：

```python
@router.post("/{article_id}/import-fulltext")
def import_fulltext(article_id: str, body: ImportFulltextRequest) -> dict:
    """读取本地 .md 文件，更新文章的 raw_content"""
    # 1. 验证文件存在且是 .md
    # 2. 读取文件内容
    # 3. UPDATE articles SET raw_content = ? WHERE id = ?
    # 4. 返回更新后的字数
```

**Step 2:** Pydantic model：

```python
class ImportFulltextRequest(BaseModel):
    file_path: str  # 本地 .md 文件绝对路径
```

**Step 3:** 安全检查 — 只允许读取特定目录（如 `~/Documents/` 或 Obsidian vault 路径）

**Step 4:** UI 加输入框（在文章内容区域下方，摘要/核查按钮上方）：

```html
<div x-show="articleDetail?.source === 'bestblogs'" style="margin: 8px 0;">
  <details>
    <summary style="cursor:pointer; color:#64748b; font-size:0.9em;">📥 导入全文（用于 AI 分析）</summary>
    <div style="margin-top:8px; display:flex; gap:8px;">
      <input type="text" x-model="importFilePath" placeholder="粘贴本地 .md 文件路径"
             style="flex:1; font-size:0.85em;">
      <button class="btn-secondary" @click="importFulltext()" :disabled="importLoading">
        <span x-show="!importLoading">导入</span>
        <span x-show="importLoading">导入中...</span>
      </button>
    </div>
    <small class="muted">用 Obsidian Web Clipper 抓取原文后，粘贴 .md 文件路径</small>
  </details>
</div>
```

**Step 5:** app.js 加方法：

```javascript
importFilePath: '',
importLoading: false,
importFulltext() {
  if (!this.importFilePath || !this.articleDetail) return;
  this.importLoading = true;
  fetch(`/api/articles/${this.articleDetail.id}/import-fulltext`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({ file_path: this.importFilePath }),
  })
    .then(r => r.json().then(d => r.ok ? d : Promise.reject(d)))
    .then(data => {
      this.articleDetail = { ...this.articleDetail, content: data.content_preview };
      alert(`全文导入成功，${data.char_count} 字`);
      this.importFilePath = '';
    })
    .catch(err => { alert('导入失败: ' + (err.message || err.detail || '未知错误')); })
    .finally(() => { this.importLoading = false; });
},
```

**Verify:** 点"导入"→ 文件内容写入 DB → 文章详情刷新显示全文 → 可以正常做 AI 摘要

---

### Task 7: AI 摘要/事实核查优先使用全文

**Objective:** 当 raw_content 有全文时（导入后），LLM 分析用全文而非摘要

**Files:**
- Modify: `moni/llm/prompts/__init__.py` — render_summary / render_fact_check

**现状:** prompts 已经用 `article.get("content") or article.get("raw_content", "")`，导入全文后自动使用。

**验证:** 导入全文后点"生成摘要"→ LLM 收到的是完整文章而非一句话摘要

---

## Phase 3: 测试 + 收尾

### Task 8: 测试

**Files:**
- Create: `tests/test_bestblogs_fetch.py`
- Modify: `tests/test_tag_filter.py` — 加 bestblogs 标题匹配测试

**测试点:**
1. date_from/date_to 过滤正确
2. bestblogs source 只匹配标题
3. import-fulltext API 文件读取 + 写入 DB
4. import-fulltext 安全检查（拒绝非 .md 文件、拒绝不存在的文件）

---

### Task 9: 文档更新

**Files:**
- `STATUS.md` — 加 v1.0.5.24 进展
- `USER_GUIDE.md` — 加 BestBlogs 使用说明
- `HANDOFF.md` — 更新待办清单
- `vibe-coding-log.md` — session 记录

---

## 文件变更总览

| 文件 | 改动类型 | Phase |
|------|---------|-------|
| `moni/web/routes/fetch.py` | 修改 | 1 |
| `moni/fetch/run.py` | 修改 | 1 |
| `moni/fetch/sources/bestblogs.py` | 修改 | 1 |
| `moni/fetch/tag_filter.py` | 修改 | 1 |
| `moni/web/static/index.html` | 修改 | 1+2 |
| `moni/web/static/js/app.js` | 修改 | 1+2 |
| `moni/web/routes/articles.py` | 修改 | 2 |
| `moni/web/models.py` | 修改 | 2 |
| `tests/test_bestblogs_fetch.py` | 新建 | 3 |
| `tests/test_tag_filter.py` | 修改 | 3 |

## 风险 & 开放问题

1. **Obsidian vault 路径安全**: import-fulltext 只允许读取特定目录，防止路径遍历攻击。建议限制在 `~/Documents/` 或用户配置的 vault 路径。
2. **"自上次抓取至今"模式**: 需要读 `data/.last_run.json` 获取上次抓取时间，作为 date_from。
3. **375 feed 全量拉取耗时**: 即使加了日期过滤，仍需拉取所有 feed 的 RSS。建议后续加 feed 级别的缓存（只拉取有更新的 feed）。
4. **标题匹配 vs 内容匹配**: 标题匹配更精确但召回率低。如果用户觉得漏掉太多，可以加回内容匹配作为 fallback。
