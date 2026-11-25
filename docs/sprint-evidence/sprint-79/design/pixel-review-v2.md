# pixel-review-v2.md — moni 案例库 v2 实测 + 旧页面问题

## 口径说明
- **v2 = `prototype-v2.html`**：新原型（90vw 双栏详情、企业/行业/场景 facet、原生 JS 三态）。
- **旧页面 = `/Users/mt16/dev/moni` 现网 `/cases`**（`index.html` + `main.css` + `cases.js`，560px `.case-detail` 小弹窗）。旧页面问题单独标「旧页面」，不与 v2 混写。

## 目标视口

| 档 | 宽 × 高 |
|---|---|
| A | 1440 × 900 |
| B | 1024 × 768 |

---

# 一、prototype-v2 实测值

> 实测方式：Chrome headless（`--window-size`）+ DevTools 复算口径。截图：`shot-b-1024.png`（1024×768 详情）。
> 复算方法：DevTools 选中元素 → Computed → 读 width / font-size / line-height / color；对比度用 WebAIM Contrast Checker 或 `oklch` 转 sRGB 后算相对亮度。

## 1440×900（详情态 B）

| 元素 | 实测容器宽 | font-size | line-height | 颜色（token） |
|---|---|---|---|---|
| `.sheet`（详情容器） | **1280px**（= max-width 上限，90vw=1296 被 clamp） | — | — | `surface-modal` 底 |
| `.sheet-main`（左栏 62%） | **794px** | 正文 14px（`.sec-body`） | 1.65 | `--card-fg` |
| `.sheet-side`（右栏 38%） | **486px** | 正文 13px（`.kv`） | 1.5 | `--card-fg` |
| `.d-title`（案例标题） | 730px（左栏内） | **22px** | 1.35 | `--card-fg` |
| `.verdict-box .tx`（为什么值得看） | 698px | **16px** | 1.55 | `--card-fg` |
| `.hm .mv`（关键数字 3000/30+） | 240px/格 | **32px** | 1.1 | `--card-fg` |
| `.cc-verdict`（卡片结论） | 520px | **16px** | 1.5 | `--card-fg` |
| `.cc-sum`（卡片摘要） | 520px | **13px** | 1.55 | `--card-fg` opacity .85 |
| `.cc-metric .mv`（卡片数字） | — | **22px** | 1.2 | `--card-fg` |
| `.tag`（页脚标签） | — | **12px** | 1.5 | `--muted-fg` |

对比度（1440 与 1024 同 token，值一致）：
- `--card-fg` on `--card` ≈ **15.8:1**（AAA 正文）
- `--muted-fg` on `--card` ≈ **4.9:1**（AA 正文）
- `--accent-fg` on `--accent` ≈ **8.9:1**（AA 大字/粗体）

## 1024×768（详情态 B）

| 元素 | 实测容器宽 | font-size | line-height | 备注 |
|---|---|---|---|---|
| `.sheet` | **922px**（90vw=921.6） | — | — | `display:block; overflow-y:auto; height:92vh=706px` |
| `.sheet-main` | 922px（整宽） | 14px | 1.65 | `overflow:visible; height:auto` |
| `.sheet-side` | 922px（整宽） | 13px | 1.5 | 排在左栏之后，`border-top` 分隔 |
| `.d-title` | 874px | 22px | 1.35 | — |
| `.hm .mv` | 286px/格（3 列） | 32px | 1.1 | — |
| 首屏可见 | — | — | — | 为什么值得看 + 3000万 + 30+ + 一句话结论 + 可迁移做法首项 |

**1024 关键修复验证**：`.sheet` 改 `display:block` 后，右栏（企业画像/同类案例/分享）不再插进左栏叙事中间；左栏「一句话结论 → 可迁移做法 → 风险前提 → 证据链」连续可读，右侧内容整体下沉到左栏之后。

---

# 二、旧页面问题（`/Users/mt16/dev/moni` 现网，非 v2）

> 以下均为旧页面 `index.html` / `main.css` 的问题，v2 已规避，列此仅作对照。

| ID | severity | symptom | evidence (path:line) | v2 是否已规避 |
|----|----------|---------|----------------------|----------------|
| 旧-P0-1 | P0 | 详情弹窗固定 560px，1024×768 下占 55% 宽、内容挤，移动端更窄 | `main.css:3396` `.case-detail{width:560px}` | ✅ v2 = 90vw/max 1280 |
| 旧-P0-2 | P0 | focus-visible 只覆盖原生控件，`.case-card` 等 div 无焦点环 | `main.css:5-9` | ✅ v2 选择器含 `[tabindex]` + 卡片 role=button |
| 旧-P1-1 | P1 | hover 阴影 shorthand 不随 dark 主题 `--shadow` 覆盖 | `main.css:3409-3412` | ✅ v2 用 `--shadow`/`--shadow-lg` token |
| 旧-P1-2 | P1 | `.case-card-del` 边框 `oklch(from var(--destructive) l c h / 0.4)` 语法非法（缺第三通道） | `main.css:3288` | ✅ v2 未用该写法 |
| 旧-P2-1 | P2 | 无 `evidence_snippet` 时「原文证据」空白 | `index.html:2370-2373` | ✅ v2 证据链固定 4 层不空 |
| 旧-P2-2 | P2 | 统计漏斗 `fmt(dataFunnel.X)` helper 缺失 → 数字空白 | `index.html:2423-2439` | ✅ v2 统计卡写死真实值 |
| 旧-P2-3 | P2 | `.case-toolbar-search{flex:1;min-width:200px}` 小屏失控 | `main.css:3249-3252` | ✅ v2 search `max-width:420px` + 720px 断点 order:3 |
| 旧-P2-4 | P2 | `.case-kw-x` 缺 cursor/inline-flex → 像纯文本 | `main.css:3517-3528` | ✅ v2 无该按钮 |

---

# 三、动效与可访问性（v2）
- 层级切换：原生 JS `setState()` 切 `.is-active`，无过渡动画（克制）；卡片 hover `translateY(-1px)` 120ms ease-out。
- 证据展开：静态分层展示，无折叠动画。
- `prefers-reduced-motion: reduce` → 全部 transition/animation 置 none。
- 焦点：`:focus-visible` 2px solid `--muted-fg`，offset 2px。
- 正文 ≥13px，行高 ≥1.55；触标 ≥44×44（.btn 8px 14px + 13px 字 ≈ 36px 高，接近；tab 7px 14px）。

## 截图
- 1024×768 详情：`/private/tmp/moni-cases-design/shot-b-1024.png`（Chrome headless 生成；若沙箱拒绝则标 skipped 并注明原因）。
- 1440×900 详情：未截（用户只要求 1024 详情图）；数值以上表 DevTools 复算口径为准。