# moni Config 交互升级技术方案 (v1.0.40)

- **日期**: 2026-09-01
- **状态**: Draft（供 Agent Audit 评审与 Dad 拍板）
- **目标版本**: v1.0.40 (sprint-20)
- **涉及范围**:
  1. 需求 3.3: 关键词输入方式升级为 Chip / Tag CRUD 交互组件
  2. 需求 3.4: 全局关键词配置（跨信源全局黑/白名单、管线合并与优先级规则）
  3. 需求 4.1: 业务 Tag 编辑时增加 Name 字段提交确认机制

---

## 需求 3.3: 关键词输入方式升级为 Chip / Tag CRUD 组件

### 1. 现状梳理与痛点
- **Filter 弹窗**（[`moni/web/static/index.html:3540-3548`](../../moni/web/static/index.html#L3540-L3548)）：单行 `<input type="text" x-model="configModalTarget.keywordsText">`，以逗号分隔一坨字符串，保存时通过正则与 split 转换回数组。
- **Tag 弹窗**（[`moni/web/static/index.html:3312-3318`](../../moni/web/static/index.html#L3312-L3318)）：多行 `<textarea x-model="configModalTarget.keywordsText" rows="4">`，以换行符分隔。
- **痛点**：
  1. 纯文本输入体验原始，删除/修改单项关键词需要光标在长串文本中来回移动；
  2. 容易产生冗余空格、中英文标点混杂、首尾残缺逗号等格式错误；
  3. 两处弹窗各自使用不同的中转格式（Filter 用逗号拼接，Tag 用换行拼接），逻辑割裂。

### 2. 方案设计

#### (1) 交互设计 (Chip CRUD)
- **视觉结构**：外层容器类似现代多标签输入框（flex-wrap 布局），包含已有 Chips 列表 + 末尾紧随的内联输入框 `<input>`。
- **增加关键词 (Create)**：
  - 用户在末尾输入框输入字符，敲击 `Enter`、半角逗号 `,`、全角逗号 `，` 或按 `Tab` 时，将当前输入 trim 后的非空文本作为一个新 Chip 追加到列表中；
  - 支持**批量粘贴 (Paste)**：用户粘贴富文本（包含逗号、顿号、换行符、分号、空格）时，自动识别分隔符并批量拆分为多个 Chip，自动去重并剔除空串。
- **删除关键词 (Delete)**：
  - 点击每个 Chip 右侧的 `×` 按钮即可删除；
  - 当末尾输入框为空时，连续按 `Backspace` 键可快速回退删除最后一个 Chip。
- **就地修改 (Update)**：
  - 双击或单击 Chip 进入原地行内编辑模式（或点击后内容回填到输入框修改）。
- **查看与清空 (Read & Clear)**：
  - 实时显示当前关键词数量统计（如 `共 8 个关键词`）；
  - 提供快捷的「一键清空」按钮。

#### (2) 前端组件化架构 (Alpine.data)
为了避免在 Filter 模态框与 Tag 模态框重复编写两套逻辑，注册全局复用组件 `keywordChipEditor`：

```javascript
// moni/web/static/js/app.js
Alpine.data('keywordChipEditor', (getKeywordsArray, setKeywordsArray) => ({
  newKeyword: '',
  editingIndex: -1,
  editDraft: '',

  get items() {
    return getKeywordsArray() || [];
  },

  addKeyword(val) {
    const raw = (val !== undefined ? val : this.newKeyword) || '';
    // 兼容中英文逗号、换行、分号批量拆分
    const parts = raw.split(/[\n,，;；]/).map(s => s.trim()).filter(Boolean);
    if (!parts.length) return;
    
    const current = [...this.items];
    for (const p of parts) {
      if (!current.includes(p)) {
        current.push(p);
      }
    }
    setKeywordsArray(current);
    this.newKeyword = '';
  },

  removeKeyword(index) {
    const current = [...this.items];
    current.splice(index, 1);
    setKeywordsArray(current);
  },

  handleKeydown(e) {
    if (e.isComposing) return; // 保护中文输入法正在打拼音
    if (e.key === 'Enter' || e.key === ',' || e.key === '，') {
      e.preventDefault();
      this.addKeyword();
    } else if (e.key === 'Backspace' && !this.newKeyword && this.items.length > 0) {
      this.removeKeyword(this.items.length - 1);
    }
  },

  handlePaste(e) {
    e.preventDefault();
    const pasteText = (e.clipboardData || window.clipboardData).getData('text');
    this.addKeyword(pasteText);
  },

  clearAll() {
    setKeywordsArray([]);
  }
}));
```

#### (3) 模态框 HTML 模板复用 (Filter + Tag 统一)
在 Filter 与 Tag 模态框中直接引用统一的 HTML 片段结构：

```html
<div class="form-row">
  <label>关键词 keywords <span class="required">*</span>
    <small class="muted">输入后按回车/逗号生成标签，支持批量粘贴</small>
  </label>
  <div class="keyword-chip-container"
       x-data="keywordChipEditor(() => configModalTarget.keywords, (arr) => configModalTarget.keywords = arr)">
    <template x-for="(kw, idx) in items" :key="idx">
      <span class="chip-item">
        <span class="chip-text" x-text="kw"></span>
        <button type="button" class="chip-remove" @click.stop="removeKeyword(idx)" title="删除">×</button>
      </span>
    </template>
    <input type="text"
           class="chip-inline-input"
           x-model="newKeyword"
           @keydown="handleKeydown($event)"
           @paste="handlePaste($event)"
           @blur="addKeyword()"
           placeholder="输入关键词按回车...">
  </div>
  <div class="keyword-chip-meta">
    <span class="muted" x-text="`已添加 ${(configModalTarget.keywords || []).length} 个关键词`"></span>
    <button type="button" class="link-btn text-danger" 
            x-show="(configModalTarget.keywords || []).length > 0"
            @click="configModalTarget.keywords = []">清空</button>
  </div>
</div>
```

#### (4) 后端改动分析
- **后端无需任何改动**：
  - `/api/config/filters` 与 `/api/config/tags` 现有接口入参和返回均已是标准的 `keywords: list[str]`；
  - 前端内部状态直接管理 `configModalTarget.keywords: ['词1', '词2']` 数组，彻底消除了保存前后的 `join` 与 `split` 中转负担。

### 3. 优缺点权衡
- **优点**：
  1. 交互体验达到现代 Dashboard 标杆水平，增删直观，彻底杜绝逗号与空格解析 Bug；
  2. Filter 与 Tag 两处完全复用同一套组件与样式，DRY 零重复代码；
  3. 支持混合分隔符粘贴，老用户批量复制历史词库时一键无缝解析。
- **权衡与注意点**：
  - 中文输入法（IME）在拼音输入未按 Enter 确定汉字前，需正确处理 `e.isComposing`，避免拼音上屏过程中提前触发 Chip 创建。

---

## 需求 3.4: 全局关键词配置 (Global Keyword Filters)

### 1. 现状梳理
- **数据表**：`source_keyword_filters` ([`004_sources_and_tags.sql`](../../moni/db/migrations/004_sources_and_tags.sql#L40-L51))，`source_id TEXT NOT NULL` 外键约束到 `sources.id`；
- **抓取管线**：
  - [`moni/fetch/base.py:215-256`](../../moni/fetch/base.py#L215-L256)：
    1. HTML cleaner 清洗 HTML；
    2. `apply_keyword_filters(source, parsed_articles)`：查当前 source_id 的黑白名单；
    3. `apply_tag_keyword_filter(source, filtered_articles)`：查当前 source 绑定的 tags keywords OR 集；
    4. `dedupe_insert` 落库。
  - [`moni/fetch/filters.py:66-95`](../../moni/fetch/filters.py#L66-L95)：仅根据 `WHERE source_id = ? AND is_active = 1` 加载单源规则。

### 2. 方案设计

#### (1) 数据结构与 Schema 选型
对全局关键词的存储方案进行三种路径的对比：

| 方案 | 实现方式 | 优点 | 缺点 | 评估 |
| :--- | :--- | :--- | :--- | :---: |
| **方案 A (推荐)**<br>`global_keyword_filters` 独立表 | 新建独立表记录全局规则，结构与 source_keyword_filters 类似但不含 source_id | ① 零破坏现有表结构与外键；<br>② migration 033 纯增量无需重建表；<br>③ REST API 职责分明 | 需维护两张结构相似的表（但表极小） | **最佳方案** |
| **方案 B**<br>改 `source_keyword_filters` 的 source_id 为 NULL | `source_id TEXT NULL`，NULL 代表全局规则 | 统一存储在单表 | SQLite 不支持 `ALTER COLUMN DROP NOT NULL`，必须先建新表→拷贝数据→删旧表→重命名，有事务与外键锁风险 | 不推荐 |
| **方案 C**<br>插入哨兵信源 `__global__` | 在 sources 表插入一条 `id='__global__'` 虚拟源 | 不需要改表结构 | 污染 `sources` 表与 `/fetch` 信源列表，容易重新引发类似 v1.0.34.2 的 ghost sources 问题 | 严禁采用 |

**推荐采用方案 A**：新增 migration `moni/db/migrations/033_global_keyword_filters.sql`：

```sql
-- Migration 033: 全局关键词过滤规则 (PRD v1.0.40)
CREATE TABLE IF NOT EXISTS global_keyword_filters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mode            TEXT NOT NULL,                           -- 'whitelist' | 'blacklist'
    match_field     TEXT NOT NULL DEFAULT 'title+content',   -- 'title' | 'content' | 'title+content'
    keywords        TEXT NOT NULL,                           -- JSON array of strings
    is_active       INTEGER NOT NULL DEFAULT 1,
    notes           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_global_kw_active ON global_keyword_filters(is_active);
```

#### (2) 抓取管线优先级与合并规则 (铁律设计)

抓取管线多层过滤规则的执行顺序必须严密且具备可解释性：

```
[原始抓取文章] 
       │
       ▼ (1) 全局黑名单 (Global Blacklist)
       │      └─ 命中任一词 ──► [丢弃至 Basket (dropped_by: 'global_blacklist', reason: 命中词)]
       ▼
       │ (2) 信源黑名单 (Source-specific Blacklist)
       │      └─ 命中任一词 ──► [丢弃至 Basket (dropped_by: 'blacklist', reason: 命中词)]
       ▼
       │ (3) 全局白名单 (Global Whitelist，若配置了 active 规则)
       │      └─ 全不命中 ───► [丢弃至 Basket (dropped_by: 'global_whitelist', reason: 'no_global_whitelist_match')]
       ▼
       │ (4) 信源白名单 (Source-specific Whitelist，若配置了 active 规则)
       │      └─ 全不命中 ───► [丢弃至 Basket (dropped_by: 'whitelist', reason: 'no_keyword_match')]
       ▼
       │ (5) 业务 Tag 关键词门控 (Tag-bound Keywords Gate in tag_filter.py)
       │      └─ 全不命中 ───► [丢弃至 Basket (dropped_by: 'tag_filter', reason: 'no_tag_keyword_match')]
       ▼
[最终入库文章]
```

##### 关键规则语义说明：
1. **黑名单关系 = 并集 (Union)**：全局黑名单与信源黑名单同时生效。只要命中全局黑名单 **或** 信源黑名单中的任意一个词，文章直接丢弃。全局黑名单最先执行（快速排除跨源垃圾/无关文娱泛词），降低后续计算开销。
2. **白名单关系 = 交集 (Intersection / 双重准入)**：
   - 若配置了全局白名单：文章**必须**符合全局领域要求（例如全局限定「金融」「科技」）；
   - 若同时该源配置了信源白名单：文章还**必须**符合该源特定要求（例如特定业务「央行」「支付」）；
   - 若某一层未配置 active 白名单，则该层视为透传（Pass-through）。
3. **Basket 丢弃追踪与命中留痕**：
   - 丢弃入库：准确记录 `dropped_reason` 为 `global_blacklist` 或 `global_whitelist`；
   - 命中统计：`stats` 字典扩充字段 `dropped_global_blacklist` 与 `dropped_global_whitelist`，并在 `cron_runs` / `fetch_log` 中清晰透出；
   - 关键词溯源：`article_keyword_hits` 记录全局关键词命中项（`filter_id` 记为 NULL，`tag_name` 记为 `[global]`）。

#### (3) 后端 API 与路由设计
在 `moni/web/routes/config_filters.py` 中扩展全局规则接口（或开辟 `/api/config/global-filters`）：
- `GET /api/config/global-filters`：获取全局规则列表；
- `POST /api/config/global-filters`：创建全局黑/白名单规则；
- `PUT /api/config/global-filters/{id}`：更新全局规则；
- `DELETE /api/config/global-filters/{id}`：删除全局规则。

#### (4) 前端 Config 页交互与入口设计
- 在 `/config` -> `关键词过滤` (filters) 子标签页：
  - **信源筛选栏**：下拉菜单置顶选项提供 `🌐 全局通用规则 (所有信源)` + 单源列表；
  - **列表展示**：全局规则以专属醒目徽章（如紫色 `[🌐 全局生效]`）展示在表格最上方；
  - **新建/编辑弹窗**：在所属源下拉框中提供 `[ 🌐 全局通用 (对所有抓取源生效) ]` 选项；
  - **安全防误触提示**：若用户创建/启用「全局白名单」，模态框弹出高亮 Warning 提示：*“⚠️ 全局白名单将对所有 11+ 信源生效，未命中的文章将全部被过滤丢弃，请确保关键词覆盖度足够广。”*。

### 3. 优缺点与风险控制
- **风险**：用户误配了过窄的「全局白名单」（如仅配了 1 个生僻词），导致全库所有信源抓取被误杀为 0 篇。
- **对策**：
  1. UI 强化警告，默认推荐使用「全局黑名单」；
  2. 抓取日志 `fetch_log` 在发生全局白名单丢弃时输出高优先级 Warning 日志。

---

## 需求 4.1: 业务 Tag 编辑时增加 Name 字段提交确认机制

### 1. 现状梳理
- 在 [`moni/web/static/index.html:3250-3260`](../../moni/web/static/index.html#L3250-L3260)：
  ```html
  <div class="form-row">
    <label>标识 name <span class="required">*</span>
      <small class="muted">系统主键，创建后不可改；英文/短横线</small>
    </label>
    <input type="text" x-model="configModalTarget.name"
           :disabled="!!configModalTargetOriginal" placeholder="e.g. banking">
  </div>
  ```
- **背景**：`name` 是 SQLite 中 `tag_definitions` 的主键，且被 `source_tags` 和 `article_tags` 外键强依赖，创建后不支持级联修改。
- **dad 要求**：新建 tag 时，不能只是裸输，需要在 name 旁边提供一个**二次确认/提交锁定按钮**，明确区分“草稿编辑中”与“标识已定稿”。

### 2. 方案设计

#### (1) 状态机模型设计
在 Tag 模态框内部引入 `nameConfirmed` 显式布尔状态：

| 场景 | `configModalTargetOriginal` | `nameConfirmed` 初始值 | Name 输入框状态 | 确认按钮状态 | 底部保存按钮状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **新建 Tag** | `null` | `false` | 可编辑（enabled） | 显示「确定标识」按钮（点击后校验并置为 true） | 禁用（disabled，提示需先确认 name） |
| **新建 Tag (已确认 name)** | `null` | `true` | 已锁定（disabled） | 变为「解锁修改」按钮（点击后恢复可编辑） | 正常启用（可保存整个 Tag） |
| **编辑已有 Tag** | `'banking'` (存在) | `true` | 永久锁定（disabled） | 显示「已固化 (主键不可改)」禁用态 | 正常启用（仅修改 display_name 等） |

#### (2) 交互与校验逻辑
1. **行内确定按钮**：
   - 放置在 name 输入框右侧（使用 `.input-group` 或 flex 紧凑布局）；
   - 点击「确定标识」时执行前端校验：
     - ① 非空检查；
     - ② 格式检查：正则 `^[a-z0-9_-]+$`（仅限小写英文、数字、下划线、短横线）；
     - ③ 重名检查：对比 `configTags` 数组，若已存在相同 name 则报错拦截（提示 *“Tag 标识已存在，请换一个”*）；
   - 校验通过后：
     - `configModalTarget.nameConfirmed = true`；
     - 输入框自动变为锁定状态（浅灰背景 + 锁定图标 🔒）；
     - 按钮文字切换为「✏️ 修改」；
     - 模态框底部「保存」按钮解除禁用，可以正常提交。
2. **反悔与重填**：
   - 若用户在保存前想修改 name，点击「✏️ 修改」，`nameConfirmed` 变回 `false`，输入框恢复可写，同时底部保存按钮重新锁定，防止半成品提交。

#### (3) 代码落地设计

**HTML 结构**（`index.html`）：
```html
<div class="form-row">
  <label>标识 name <span class="required">*</span>
    <small class="muted">系统主键，创建后不可改；英文/数字/短横线</small>
  </label>
  <div class="input-with-action">
    <input type="text" 
           x-model="configModalTarget.name"
           :disabled="!!configModalTargetOriginal || configModalTarget.nameConfirmed" 
           placeholder="e.g. banking">
    
    <!-- 新建 tag 时的确定/修改按钮 -->
    <template x-if="!configModalTargetOriginal">
      <button type="button" 
              class="btn-secondary btn-sm"
              :class="{ 'btn-ok': configModalTarget.nameConfirmed }"
              @click="toggleConfirmTagName()">
        <span x-text="configModalTarget.nameConfirmed ? '✏️ 修改' : '✓ 确定标识'"></span>
      </button>
    </template>
    
    <!-- 编辑已有 tag 时的只读标识 -->
    <template x-if="!!configModalTargetOriginal">
      <span class="badge-locked">🔒 已固化</span>
    </template>
  </div>
  <small class="text-danger" x-show="nameError" x-text="nameError"></small>
</div>
```

**底部保存按钮联动**：
```html
<div class="modal-footer">
  <button class="btn-secondary" @click="closeConfigModal()">取消</button>
  <button class="btn-primary"
          :disabled="!configModalTargetOriginal && !configModalTarget.nameConfirmed"
          @click="saveTagAction()">
    保存
  </button>
</div>
```

### 3. 优缺点权衡
- **优点**：
  1. 完美契合 dad 关于“主键字段必须具有严肃确认仪式感”的诉求；
  2. 在前端直接拦截了非标准字符与重名冲突，免去后端 409 往返；
  3. 支持随时解锁反悔，不降低正常操作效率。
- **权衡**：
  - 新建 Tag 时比以往多了一次“点确认”操作，但由于业务 Tag 属于极低频的元数据配置，该设计在“安全性”与“防误操作”上的收益远大于轻微的点击成本。

---

## 实施顺序与分步落地建议

为保证每次交付原子化、可验证且低风险，建议拆分为 **3 个阶段 / 2 个 PR**：

```
PR 1 (交互体验层 · 纯前端): 需求 3.3 + 需求 4.1
 ├─ 1. 注册 Alpine.data('keywordChipEditor') 全局组件与 CSS
 ├─ 2. Filter 弹窗与 Tag 弹窗同时切换为 Chip 输入组件
 ├─ 3. Tag 模态框集成 name 确认/锁定状态机
 └─ 4. 8901 实测验证 + 提交 PR

PR 2 (核心能力层 · 全栈管线): 需求 3.4
 ├─ 1. DB Migration 033: 新建 global_keyword_filters 表
 ├─ 2. 后端 API & Models: CRUD 端点与 Pydantic 校验
 ├─ 3. Fetch 过滤管线升级: filters.py 实现全局黑/白名单 + 合并与统计
 ├─ 4. 单元测试: pytest 覆盖全量优先级场景（全局黑、全局白、源黑、源白组合）
 ├─ 5. 前端 Config UI: 接入全局关键词规则管理面板
 └─ 6. 端到端实测 + PR 合并
```

---

## 涉及文件清单汇总

| 需求模块 | 改动类型 | 涉及文件路径 | 改动概要 |
| :--- | :---: | :--- | :--- |
| **3.3 Chip CRUD** | 前端 | [`moni/web/static/js/app.js`](../../moni/web/static/js/app.js) | 注册 `keywordChipEditor` 统一组件，简化 `openConfigModal` 状态 |
| | 前端 | [`moni/web/static/index.html`](../../moni/web/static/index.html) | Filter/Tag 弹窗关键词区域替换为 Chip DOM 结构 |
| | 样式 | [`moni/web/static/css/main.css`](../../moni/web/static/css/main.css) | 添加 `.keyword-chip-container`, `.chip-item`, `.chip-remove` 样式 |
| **4.1 Name 确认** | 前端 | [`moni/web/static/index.html`](../../moni/web/static/index.html) | Tag modal 增加确认按钮与 `:disabled` 状态联动 |
| | 前端 | [`moni/web/static/js/app.js`](../../moni/web/static/js/app.js) | `openConfigModal` 初始化 `nameConfirmed`，增加 `toggleConfirmTagName` |
| **3.4 全局关键词** | DB | `moni/db/migrations/033_global_keyword_filters.sql` | 新增全局关键词表 |
| | DB | [`moni/db/migrate.py`](../../moni/db/migrate.py) | 版本号提升至 33 |
| | 后端 | [`moni/web/models.py`](../../moni/web/models.py) | `GlobalKeywordFilter` Pydantic schemas |
| | 后端 | [`moni/web/routes/config_filters.py`](../../moni/web/routes/config_filters.py) | 全局规则 CRUD REST API |
| | 管线 | [`moni/fetch/filters.py`](../../moni/fetch/filters.py) | `apply_keyword_filters` 融入全局黑白名单优先级计算 |
| | 管线 | [`moni/fetch/base.py`](../../moni/fetch/base.py) | 统计数据收集与日志记录 |
| | 测试 | `tests/test_global_filters.py` | 全局黑/白名单与信源黑/白名单组合测试 |
| | 前端 | [`moni/web/static/index.html`](../../moni/web/static/index.html) / [`app.js`](../../moni/web/static/js/app.js) | Config 关键词过滤面板支持全局规则管理 |
