# 案例库筛选方案（搜索之外）

基线：`docs/sprint-evidence/sprint-79/design/prototype-v2.html`（token / 组件语言）、`copy-review.md`（文案原则）、现网 `index.html:2110-2134`（三个下拉）。
范围：只出方案。不改仓库、不跑迁移、不重启服务。

## 0. 结论先行

**推荐模式 A：顶部 facet 胶囊行 + 就地展开（单开手风琴）+ 第二行已选 token。**
一句话理由：7 个维度（企业 / 行业一级·二级 / 场景 10 / 类型 8 / 价值 3 / 时间 / 关键词）无法常驻展开，左侧栏在 1024 会吃掉列表宽度，搜索框内联 token 会牺牲可发现性与计数；只有顶部胶囊行能在 1440 与 1024 都保持「列表不被遮挡 + 30 秒可扫读 + 状态可分享」。

---

## 1. 三个候选模式

| | 模式 A 顶部 facet 胶囊行 | 模式 B 左侧折叠 facet 栏 | 模式 C 搜索框内联 token |
|---|---|---|---|
| 形态 | 顶栏下一条胶囊行（企业/行业/场景/类型/价值/时间），点开就地展开面板，已选值折叠成第二行 token | 左侧 240px 栏，维度分组纵向堆叠，每组可折叠 | 已选条件做成搜索框内的 token，输入即筛 |
| 交互路径 | Tab 到胶囊 → Enter 展开 → 面板内方向键移动、空格多选 → Esc 收起 → 列表即时刷新 | Tab 进左栏 → 逐组展开 → 勾选 → 列表在右侧刷新 | 输入文字 → 结果收窄 → 点「筛选」在框下展开维度 |
| 优点 | 单行常驻、零遮挡、维度可增到 7 个、计数与已选同屏、URL 一一对应 | 所有维度同屏可见、无需展开、类电商心智 | 最省纵向空间、状态与查询同处、移动端友好 |
| 缺点 | 展开时列表下移（可接受，非遮挡）；10 项场景需限高滚动 | 1024 下列表仅剩 ~700px，卡片换行、扫读变慢；窄屏只能改抽屉 → 变成遮挡 | 5 个以上条件时 token 挤爆输入框；维度可发现性差；计数放不下；结构化筛选与文本输入共用一个焦点语义混乱 |
| 适用条件 | 维度 ≥5、需要计数、桌面为主、要可分享链接 | 维度 >10、宽屏 ≥1440、专业用户 | 条件 ≤3、移动优先、临时收窄 |

**推荐 A。** 三条硬理由：
1. 维度已到 7 个，B 的「全展开」在 1024 不可行（列表宽度 < 700px，卡片从 2 列掉到 1 列，30 秒扫读目标失守）。
2. C 把「结构化筛选」塞进文本输入框，键盘语义（Tab/方向键/输入）互相打架，且计数无处安放。
3. A 的展开面板在文档流内（`in-flow`），列表只是下移不被覆盖，满足「不要弹窗式筛选面板」。

---

## 2. 推荐方案分档布局

### 2.1 结构（自上而下）

```
topnav（sticky, 56px）
filters（sticky, top:56px）
  ├─ filters__row：搜索框（复用 .search）｜胶囊行 .fchips｜命中数 .fhit｜清空 .fclear
  ├─ filters__tokens：已选 token（仅有条件时出现）
  └─ filters__panel：就地展开（仅一个维度，in-flow）
列表 .case-grid（面板在流内，自然下移）
```

### 2.2 1440×900

| 行 | 内容 | 尺寸 |
|---|---|---|
| 1 | 搜索框 420px + 胶囊 6 个（企业 行业 场景 类型 价值 时间） | 胶囊高 30px，间距 8px，行 padding 8px 24px |
| 2 | 已选 token（条件态才出现） | token 高 26px，可换行 |
| 3 | 展开面板 | 限高 **240px**，`overflow-y:auto`，网格 `auto-fill minmax(132px,1fr)`，间距 6px |
| 4 | 列表 | 卡片 2 列（`minmax(340px,1fr)`），面板展开时下移 |

右侧固定：命中数「137 条」+「清空」（0 条件时置灰）。

### 2.3 1024×768

| 行 | 内容 | 尺寸 |
|---|---|---|
| 1 | 搜索框 flex:1 + 胶囊行**单行横向滚动**（不换行，隐藏滚动条） | 胶囊高 30px，行 padding 8px 16px |
| 2 | 已选 token（换行） | 高 26px |
| 3 | 展开面板 | 限高 **200px**，网格 `minmax(120px,1fr)` |
| 4 | 列表 | 1–2 列，面板展开时下移 |

要点：1024 不换行、不加高，靠横向滚动保住 30px 一行；面板限高 200px 保证 768 高时列表首屏仍可见 ≥2 行卡片。

### 2.4 折叠规则

- **单开手风琴**：同时只有一个维度展开，切换维度自动收起上一个 → 面板高度恒定可预期。
- 面板 max-height 240 / 200px，超出滚动；滚动条只在面板内，列表滚动不受影响。
- 胶囊行 sticky（`top:56px`，`z-index:190`），面板不 sticky（随页面滚走）。
- 已选 token 行不 sticky，避免遮挡。
- 无已选条件时 token 行不渲染（`hidden`），不留空白。

---

## 3. 全部状态与文案

| 状态 | 触发 | 文案（≤6 字优先） | 视觉 |
|---|---|---|---|
| 默认 | 无筛选 | 胶囊只写维度名：企业 / 行业 / 场景 / 类型 / 价值 / 时间 | 卡片底 + 杏子灰边；命中数「432 条」 |
| hover | 指针悬停 | 不变 | 边框转 `--muted-fg`，无位移 |
| 展开 | 点胶囊 / Enter | 面板头：维度名 + 右侧「已选 2」 | 胶囊转黑底（`aria-expanded=true`），面板 `in-flow` 出现 |
| 已选（含计数） | 勾选任一项 | 胶囊变「行业 2」；token 行「互联网 ×」 | 胶囊黑底白字 + 计数；token 用 `--muted` 底、`--radius-sm` |
| 多选 | 面板内再勾 | 计数递增；命中数同步 | 选项 `aria-selected=true` 黑底；面板头「已选 3」 |
| 清空全部 | 点「清空」 | 「清空」 | 0 条件时 `disabled` 且 `opacity:.45` |
| 无结果 | 命中 0 | 「无匹配」+ 按钮「清除筛选」 | 列表区虚线框空态，保留筛选行 |
| 加载中 | 筛选变更 | 「加载中」 | 命中数显示「—」，列表 `aria-busy=true` + 降透明度，不做骨架闪烁 |

补充：面板内单维度无选项时显示「暂无」（2 字），不写解释。

---

## 4. 结果反馈

**命中数**：胶囊行右端，`font-variant-numeric:tabular-nums`，`aria-live="polite"`；格式「137 条」；加载中显示「—」；0 时「0 条」并触发空态。

**URL 回显（可分享）**：单值直接写，多值逗号分隔，行业二级用 `/` 连一级，空值即删除参数。

```
/cases?ent=腾讯,字节跳动&ind=互联网/社交&scene=智能助理,内容分发&type=智能体&ref=高&time=30d&q=小微
```

- 变更用 `history.replaceState`（不污染历史），面板开合不写 URL。
- 首次进入解析 query → 回填胶囊计数、token、列表；非法值忽略并丢弃。
- 「复制链接」复用详情页按钮语言，放在 token 行右端（仅条件态出现）。

**清空入口**：两处。① 胶囊行右端「清空」（常驻、0 条件置灰）；② 空态内「清除筛选」。不设第三个入口。

---

## 5. 可落地规格

### 5.1 DOM 结构

```html
<div class="filters" data-filters>
  <div class="filters__row">
    <div class="search">…（沿用原型 .search）…</div>
    <div class="fchips" role="group" aria-label="筛选维度">
      <button class="fchip" data-dim="ent" aria-expanded="false" aria-controls="fp-ent">
        企业<span class="fchip__n">2</span>
      </button>
      … 行业 / 场景 / 类型 / 价值 / 时间 …
    </div>
    <output class="fhit" aria-live="polite">432 条</output>
    <button class="fclear" disabled>清空</button>
  </div>

  <div class="ftokens" hidden>
    <button class="ftoken" data-dim="ind" data-val="互联网">互联网<svg class="ftoken__x">…</svg></button>
    <button class="fcopy">复制链接</button>
  </div>

  <div class="fpanel" id="fp-ent" hidden>
    <div class="fpanel__head">企业<span class="fpanel__sel">已选 2</span></div>
    <div class="fpanel__body" role="listbox" aria-multiselectable="true">
      <button class="fopt" role="option" aria-selected="true" data-val="腾讯">腾讯<span class="fopt__n">12</span></button>
      …
    </div>
  </div>
</div>
```

### 5.2 class 命名

`filters` / `filters__row` / `fchips` / `fchip` / `fchip__n` / `fhit` / `fclear` / `ftokens` / `ftoken` / `ftoken__x` / `fcopy` / `fpanel` / `fpanel__head` / `fpanel__sel` / `fpanel__body` / `fopt` / `fopt__n`。
状态用属性选择器：`[aria-expanded]`、`[aria-selected]`、`[disabled]`、`[hidden]`，不新增状态类名。

### 5.3 关键尺寸（px）

| 项 | 1440 | 1024 |
|---|---|---|
| 胶囊高 / 内距 | 30 / 0 12 | 30 / 0 10 |
| 胶囊圆角 / 字号 | 999 / 13 | 999 / 13 |
| 计数 `.fchip__n` | mono 12，`opacity:.6` | 同 |
| 行 padding | 8 24 | 8 16 |
| 面板限高 | 240 | 200 |
| 面板内距 / 圆角 | 12 16 / 12 | 10 12 / 12 |
| 选项高 / 网格 | 30 / `minmax(132px,1fr)` gap 6 | 30 / `minmax(120px,1fr)` gap 6 |
| token 高 / 圆角 | 26 / 6 | 26 / 6 |
| 命中数字号 | 13 tabular-nums | 同 |
| sticky 偏移 | topnav 56 + 0 | 同 |

颜色全部走 token：底 `--card`、边 `--border`、选中 `--foreground`/`--background`、hover 边 `--muted-fg`、计数 `--muted-fg`。

### 5.4 键盘交互

| 按键 | 作用域 | 行为 |
|---|---|---|
| Tab / Shift+Tab | 全局 | 胶囊 → 面板选项 → token → 清空，按 DOM 顺序 |
| Enter / Space | `.fchip` | 展开/收起该维度（单开） |
| Esc | 面板内 | 收起面板，焦点回胶囊 |
| ↑ / ↓ | 面板 `.fopt` | 选项间移动（roving tabindex，仅一项 `tabindex=0`） |
| Home / End | 面板 | 首/末选项 |
| Space / Enter | `.fopt` | 勾选/取消（多选，不收起面板） |
| ← / → | 行业二级 | 一级列 ↔ 二级列 |
| Delete / Backspace | `.ftoken` | 移除该条件 |
| ⌘K / Ctrl+K | 全局 | 聚焦搜索框（沿用原型） |

### 5.5 数据契约（后端）

`GET /api/cases/facets` 一次返回各维度选项与计数（`{ent:[{v,n}], ind:[{v,n,children}], scene:[], type:[], ref:[], time:[]}`）；列表 `GET /api/cases?ent=&ind=&scene=&type=&ref=&time=&q=&limit=&offset=`，多值逗号分隔。计数与列表同一口径，避免「筛出 0 但计数 12」。

### 5.6 静态 mock

`/private/tmp/cases-filter-pixel.html` — 单文件、无外网依赖，24 条示例子集（计数与命中数由同一数组计算，保证自洽），含展开/多选/token/清空/URL 同步/键盘；时间维度在 mock 里按月粒度（数据为月精度），真实环境按 `近 7/30/90 天 · 今年 · 自定义` 预设。

---

## 6. 三行摘要

1. 推荐顶部 facet 胶囊行 + 就地展开（单开手风琴）+ 第二行已选 token；左侧栏在 1024 吃掉列表宽度，搜索框内联 token 牺牲可发现性与计数。
2. 1440 一行胶囊（30px）+ 面板限高 240px；1024 胶囊单行横滚不换行、面板限高 200px，列表始终不被遮挡。
3. 状态文案 2–4 字（展开/已选 2/清空/无匹配/加载中），命中数「137 条」+ URL 参数回显 + 两处清空入口，键盘 Tab/Enter/Esc/方向键全可达。