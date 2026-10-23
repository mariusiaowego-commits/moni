# 前端安全链接白名单 (safeHref) 与资源自托管方案设计

- **日期**: 2026-10-23
- **版本**: v1.0.46 预备 / 安全与离线化专项
- **目标文件**:
  - `moni/web/static/js/safe_url.js` (新建)
  - `moni/web/static/vendor/` (新建目录)
  - `moni/web/static/index.html` (引用替换与 11 处 href 护栏)
  - `moni/web/static/css/main.css` (拦截状态视觉微调)
  - `tests/test_frontend_safe_href_and_vendor.py` (新建静态测试套件)
- **双写同步**: `tqob/05-Coding/project-moni/plans/2026-10-23-frontend-safe-href-and-offline-assets.md`

---

## 1. 现状与问题定位 (Baseline)

### 1.1 11 处未受控动态 href
`moni/web/static/index.html` 中存在 11 处直接绑定未经协议清洗数据的 `:href` 属性。数据源覆盖 RSS 爬虫输出、外部信源配置、LLM 联网事实核查检索结果及数据库历史数据，均为不可信输入：

| # | 行号 (`moni/web/static/index.html`) | 绑定表达式 | 上下文场景 | 数据来源风险 |
|---|---|---|---|---|
| 1 | `moni/web/static/index.html:274` | `:href="a.url"` | 文章列表数据表格行 | 爬虫解析外部网页 URL |
| 2 | `moni/web/static/index.html:318` | `:href="run.log_url"` | 抓取历史查看日志链接 | 调度器任务日志相对/绝对路径 |
| 3 | `moni/web/static/index.html:620` | `:href="s.url"` | 信源管理表格行 | 配置项用户输入或导入的源站点 |
| 4 | `moni/web/static/index.html:938` | `:href="activeBriefTask.output_url"` | 每日简报打开产物主按钮 | 本地站内产物路径 (`/artifacts/...`) |
| 5 | `moni/web/static/index.html:1239` | `:href="q.output_url"` | 分享任务队列产物链接 | 队列任务输出路径 (`/artifacts/...`) |
| 6 | `moni/web/static/index.html:1403` | `:href="basketDetail?.url"` | 菜篮详情抽屉原文按钮 | 收藏文章原始 URL |
| 7 | `moni/web/static/index.html:2123` | `:href="row.article_url"` | 案例库列表关联文章链接 | 案例关联原文 URL |
| 8 | `moni/web/static/index.html:2830` | `:href="run.log_url"` | 抓取日志第二处入口 | 调度器任务日志相对/绝对路径 |
| 9 | `moni/web/static/index.html:2970` | `:href="articleDetail?.url"` | 文章详情弹窗顶部“查看原文” | 文章原始详情页 URL |
| 10 | `moni/web/static/index.html:3026` | `:href="e.url"` | 事实核查证据链一览条目 | LLM 联网检索爬取返回证据 URL |
| 11 | `moni/web/static/index.html:3079` | `:href="e.url"` | 事实核查备选证据链列表 | LLM 联网检索爬取返回证据 URL |

**危害分析**: 若信源内容或 LLM 输出构造了 `javascript:fetch('/api/config/sources').then(...)` 或其它恶意载荷，用户在 `127.0.0.1:8900` 域下点击将导致同源脚本执行（Stored XSS），绕过 moni 所有的客户端只读控制。

### 1.2 CDN 依赖与离线白屏风险
- `moni/web/static/index.html:19`, `moni/web/static/index.html:23` 通过 `<link rel="preload">` 预加载 `cdn.jsdelivr.net` 上的 Alpine.js 3.14.1 与 GSAP 3.12.5。
- `moni/web/static/index.html:3778`, `moni/web/static/index.html:3784` 延迟加载该两项脚本。
- **现状缺陷**: 均未配置 SRI (`integrity`)；在局域网内网隔离、出网 DNS 污染或 CDN 故障时，`Alpine` 无法载入，`x-cloak` 导致全站持续空白，SPA 路由与交互全面瘫痪。

---

## 2. A 部分：safeHref 的语义决策

### 2.1 协议白名单集合与决策理由

我们建议严格收敛允许的 Scheme 集合为：**`https:`、`http:`、`mailto:` 以及同源相对路径 (Same-Origin Relative Paths)**。明确拒绝并拦截其余所有协议：

```
允许集合: { https:, http:, mailto:, same-origin relative }
明确拦截: { javascript:, data:, vbscript:, file:, blob:, about:, android-app:, intent: }
```

**逐项理由**:
1. **`https:` (必须允许)**: 现代安全网络协议标准，文章外链、信源、搜索证据链的主力协议。
2. **`http:` (必须允许)**: 大量同业 RSS 信源（如行业老牌资讯站、内部政企网关、本地测试环境）仍在使用未加密的 HTTP 协议。若强行剔除 `http:` 将导致大量合规文章与源链接无法打开。
3. **同源相对路径 (必须允许)**: moni 的产物浏览和日志查看深度依赖站内相对路径，例如简报产物 `activeBriefTask.output_url = "/artifacts/brief-xxx.html"` (`moni/web/routes/share.py:216`) 以及日志路由 `/api/fetch/runs/...`。
4. **`mailto:` (允许)**: 用于联系作者或投稿邮箱，现代浏览器将其交由本地默认邮件客户端处理，无 DOM/JS 执行权限，属于标准低风险协议。
5. **`javascript:` (坚决拦截)**: 核心 XSS 攻击向量。点击即在 `127.0.0.1:8900` 执行任意 JS 代码。
6. **`data:` (坚决拦截)**: 攻击者可通过 `data:text/html;base64,...` 构建包含任意脚本的虚拟页面，绕过域名限制。
7. **`vbscript:` (坚决拦截)**: 传统 IE / 兼容内核脚本协议，无安全上下文。
8. **`file:` (坚决拦截)**: 本地文件探测与路径穿越隐患，禁止 Web 端通过链接探测本地私有文件。
9. **`blob:` (坚决拦截)**: 虽为浏览器有效对象，但动态生成的 blob 容易逃避静态扫描并在同源下执行。

### 2.2 站内相对路径 vs 站外绝对 URL 判定规则

在处理相对路径时，存在致命陷阱：**协议相对 URL (`//evil.com`) 与反斜杠混淆 (`/\evil.com` 或 `/\\evil.com`)**。
若简单以 `url.startsWith('/')` 判定为“站内安全相对路径”，浏览器会将 `//evil.com` 解析为指向外部恶意站点的协议相对链接，造成判定逃逸。

因此，判定规则采用 **“三道防线” + WHATWG URL 标准解析器**：

```
[原始输入] 
   │
   ├─► 1. 基础校验: 非空字符串检查 + trim 去除首尾空白及 ASCII 控制字符 (\r\n\t)
   │
   ├─► 2. 伪相对路径阻断: 
   │      - 严禁以 "//" 开头 (阻止协议相对跳转外部域)
   │      - 严禁以 "/\" 或 "\" 开头 (阻止 Chromium/WebKit 将反斜杠折叠为斜杠)
   │
   └─► 3. WHATWG URL 解析 (以当前站点 origin 为基准):
          new URL(trimmed, window.location.origin)
          │
          ├─► proto ∈ ['http:', 'https:']:
          │     ├─ 若原始输入以 "/" 开头: 必须满足 parsed.origin === window.location.origin (绝对站内)
          │     └─ 若原始输入为完整 URL: 必须具备非空 parsed.hostname
          │
          ├─► proto === 'mailto:':
          │     └─ 原始输入不得以 "/" 开头，且 parsed.pathname 具备有效邮箱格式
          │
          └─► 其余一切情况: return false (拦截)
```

**纯函数判定逻辑参考实现**:
```javascript
function isSafeUrl(rawUrl, baseOrigin) {
  if (!rawUrl || typeof rawUrl !== 'string') return false;
  const trimmed = rawUrl.trim();
  if (!trimmed) return false;

  // 阻断协议相对 URL 与反斜杠跳板
  if (trimmed.startsWith('//') || trimmed.startsWith('/\\') || trimmed.startsWith('\\')) {
    return false;
  }

  const origin = baseOrigin || (typeof window !== 'undefined' ? window.location.origin : 'http://127.0.0.1:8900');
  try {
    const parsed = new URL(trimmed, origin);
    const proto = parsed.protocol.toLowerCase();

    if (proto === 'http:' || proto === 'https:') {
      // 站内绝对/相对路径：确保解析后的 origin 与本地站点完全相同
      if (trimmed.startsWith('/')) {
        return parsed.origin === origin;
      }
      // 站外绝对 URL：必须具备合法主机名
      return Boolean(parsed.hostname);
    }

    if (proto === 'mailto:') {
      return !trimmed.startsWith('/') && parsed.pathname.length > 0;
    }

    return false;
  } catch (err) {
    // 包含无效字符、格式破损的 URL 一律拒绝
    return false;
  }
}
```

### 2.3 被拦截链接的前端呈现与视觉规范 (UI 决策)

#### 决策推荐: “保留文字 + 移除链接 + 低调安全拦截标记”
- **为什么不直接隐藏链接?**: moni 是严肃的同业资讯聚合器与事实核查工具。当 LLM 或抓取源返回异常/攻击载荷时，直接隐藏会导致表格单元格空白，掩盖了数据异常的事实。保留文本有助于排查数据污染。
- **为什么不换成大块的报错警示?**: 破坏现有表格紧凑度与排版韵律，造成不必要的视觉噪音。
- **推荐策略**:
  1. 将危险链接的 `<a>` 降级为非导航的 `<span>` 元素，文字应用删除线与置灰样式。
  2. 紧跟一个带有 `[data-tooltip]` 的微型警告 Badge，明确提示原因。
  3. 操作按钮（如“查看原文”、“打开产物”）在链接不安全时呈现置灰禁用状态 (`disabled`)。

#### 视觉规范与现有 Token 映射 (严格遵循 `main.css`)
全面沿用 `moni/web/static/css/main.css` 已有 design tokens，杜绝新造变量：

| 元素 | CSS Token / 属性 | 数值 / 表现 | 说明 |
|---|---|---|---|
| 拦截文字颜色 | `var(--muted-fg)` | `oklch(0.486 0.040 87.0)` (蟹壳灰) | 弱化可读性，暗示已失活 |
| 拦截文字修饰 | `text-decoration` | `line-through` | 直观传达“已作废” |
| 拦截徽章背景 | `var(--destructive)` 衍生 | `oklch(from var(--destructive) l c h / 0.15)` | 复用已有 `.badge-danger` 背景 |
| 拦截徽章文字 | `var(--destructive)` | `oklch(0.542 0.208 19.5)` (尖晶玉红) | 复用已有 `.badge-danger` 警示红 |
| 徽章圆角与尺寸 | `border-radius: 999px;` | 字体 `0.72rem; font-weight: 600;` | 与系统全局 `.badge` 统一 |
| 气泡 Tooltip | `[data-tooltip]` 伪类 | 背景 `var(--foreground)`, 文字 `var(--background)` | 复用 `main.css:411` 的 hover 气泡 |
| Tooltip 提示文案 | 统一为 | `该链接协议不安全 (仅支持 http/https/相对路径)，已自动拦截` | 清晰说明原因及放行规则 |

#### CSS 规则定义 (追加至 `main.css`)
```css
/* ══ 链接安全拦截态 (v1.0.46 safeHref) ══ */
.url-blocked {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
}
.url-blocked-text {
  color: var(--muted-fg);
  text-decoration: line-through;
  opacity: 0.75;
  font-size: 0.82rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 兜底防御：当 a 标签失去 href 属性时的非激活态 */
a:not([href]):not(.topnav-brand):not([role="button"]) {
  cursor: not-allowed;
  color: var(--muted-fg);
  pointer-events: none;
  text-decoration: none;
}
```

### 2.4 实现架构与模板绑定示例

#### 架构选型: 独立 `moni/web/static/js/safe_url.js`
- **对比 Alpine 组件内部方法**: `app.js` 当前已有 200KB、近 5000 行，将独立的安全纯逻辑混入会加大耦合；更重要的是，独立文件可直接被 Node.js 单测与静态检查加载，无需模拟庞大的 DOM/Alpine 上下文。
- **暴露方式**:
  - `window.isSafeUrl(url)`: 校验布尔值。
  - `window.safeHref(url)`: 安全则返回合法 URL 字符串；危险则返回 `null`。
  - 兼容 CommonJS 导出 (`if (typeof module !== 'undefined') module.exports = ...`)，便于 CI / Node 探针直接运行。
  - 在 `app.js` 的 `app()` 根组件中引入混入，使 Alpine 模板内既可调用 `safeHref(...)` 也可调用 `isSafeUrl(...)`。

#### Alpine 模板 11 处绑定改造规范

##### 模式 1：文本型链接 (表行、证据链) —— L274, L620, L2123, L3026, L3079
以 `moni/web/static/index.html:274` 为例：
```html
<!-- 改造前 -->
<td class="url-cell"><a class="link" :href="a.url" target="_blank" x-text="a.url"></a></td>

<!-- 改造后: 具备直观防御指示 -->
<td class="url-cell">
  <template x-if="isSafeUrl(a.url)">
    <a class="link" :href="safeHref(a.url)" target="_blank" rel="noopener noreferrer" x-text="a.url"></a>
  </template>
  <template x-if="!isSafeUrl(a.url)">
    <span class="url-blocked">
      <span class="url-blocked-text" x-text="a.url"></span>
      <span class="badge badge-danger" data-tooltip="该链接协议不安全，已自动拦截">已拦截</span>
    </span>
  </template>
</td>
```

##### 模式 2：操作按钮型链接 —— L938, L1403, L2970
以 `moni/web/static/index.html:938` 为例：
```html
<!-- 改造前 -->
<a class="btn-primary" :href="activeBriefTask.output_url" target="_blank">打开产物</a>

<!-- 改造后 -->
<template x-if="isSafeUrl(activeBriefTask.output_url)">
  <a class="btn-primary" :href="safeHref(activeBriefTask.output_url)" target="_blank" rel="noopener noreferrer">打开产物</a>
</template>
<template x-if="!isSafeUrl(activeBriefTask.output_url)">
  <button class="btn-primary" disabled title="产物链接协议不安全，已拦截">打开产物 (已拦截)</button>
</template>
```

##### 模式 3：轻量紧凑链接 —— L318, L1239, L2830
以 `moni/web/static/index.html:318` 为例：
```html
<!-- 改造前 -->
<a x-show="run.log_url" :href="run.log_url" target="_blank" class="link-btn">查看日志</a>

<!-- 改造后: safeHref 为 null 时 Alpine 自动剔除 href 属性 -->
<a x-show="run.log_url && isSafeUrl(run.log_url)" :href="safeHref(run.log_url)" target="_blank" rel="noopener noreferrer" class="link-btn">查看日志</a>
<span x-show="run.log_url && !isSafeUrl(run.log_url)" class="badge badge-danger" data-tooltip="日志链接协议不安全">日志已拦截</span>
```

---

## 3. B 部分：自托管资源方案

### 3.1 目录拓扑与文件落位
参考 `moni/web/static/fonts/` 的做法，在 `static/` 下设立一级的 `vendor/` 专用目录，与第一方业务脚本 `js/` 彻底解耦：

```
moni/web/static/
├── css/
│   └── main.css
├── fonts/
│   ├── inter-latin-400.woff2
│   └── inter-LICENSE.txt
├── icons/
│   └── favicon.svg
├── js/               <-- 仅存放 moni 第一方业务逻辑
│   ├── app.js
│   ├── safe_url.js   <-- 新增纯函数安全模块
│   ├── modal.js
│   └── ...
└── vendor/           <-- 新增: 统一自托管第三方库
    ├── alpine-3.14.1.min.js
    ├── gsap-3.12.5.min.js
    ├── README.md
    ├── LICENSE-ALPINE.txt
    └── LICENSE-GSAP.txt
```

### 3.2 许可证法务确认与合规凭证
1. **Alpine.js 3.14.1 (MIT License)**:
   - **许可定性**: 标准宽松开源协议。
   - **合规要求**: 允许不受限制地分发、嵌入或商用，唯一要求是保留原始版权声明及许可证全文。
   - **实施方式**: 在 `vendor/LICENSE-ALPINE.txt` 完整写入 Caleb Porzio 及贡献者的 MIT 许可文本，并保留压缩文件头部的版权单行注释。
2. **GSAP 3.12.5 (GSAP Standard License / Webflow)**:
   - **许可定性**: GSAP 归属于 GreenSock (现归 Webflow)，采用标准免费商业许可（Standard "No-Charge" License）。
   - **合规依据**:
     - moni 属于同业资讯监测工具/新闻库，完全免费提供给本地使用，既不向最终用户收费，更**不是与 Webflow 竞争的可视化建站/无代码动画构建工具**（该类工具为 GSAP 许可证明令禁止的豁免范围）。
     - GreenSock 许可证明确允许在网站、本地应用程序和软件分发中直接捆绑其编译分发文件 (`gsap.min.js`)。
   - **实施方式**:
     - 必须保留 `gsap-3.12.5.min.js` 文件顶部的 GreenSock 版权与版本声明。
     - 在 `moni/web/static/vendor/LICENSE-GSAP.txt` 归档 GreenSock 官方许可证声明及官方许可查验入口 (`https://gsap.com/licensing/`)。
     - 在 `moni/web/static/vendor/README.md` 中标明各库的来源 URL、取得时间、官方 SHA-256 校验和。

### 3.3 版本钉死与防漂移策略
- **文件名包含显式语义版本**:
  - `vendor/alpine-3.14.1.min.js`
  - `vendor/gsap-3.12.5.min.js`
- **严禁无版本裸名**: 不采用 `alpine.min.js` 或软链接，避免更新或维护时发生覆盖漂移。
- **静态哈希硬断言 (Pytest)**:
  在自动化测试中固定两份文件的 SHA-256 摘要：
  ```python
  EXPECTED_HASHES = {
      "alpine-3.14.1.min.js": "<sha256_of_alpine>",
      "gsap-3.12.5.min.js": "<sha256_of_gsap>",
  }
  ```
  任何未经评审对 vendor 文件的二次修改、格式化或字节漂移都将导致测试直接标红。

### 3.4 index.html 脚本加载顺序与测试契约约束
`tests/test_frontend_render_hygiene.py` (v1.0.44 引入) 锁定了严苛的前端渲染稳健性契约：

#### 现有测试断言分析
1. `test_gsap_script_tag_has_defer` (`tests/test_frontend_render_hygiene.py:225`):
   要求包含 `gsap` 的 `<script>` 标签有且仅有 1 个，且必须包含 `defer` 属性。
2. `test_gsap_script_has_no_async` (`tests/test_frontend_render_hygiene.py:232`):
   严禁包含 `async`。
3. `test_gsap_script_precedes_all_consumer_scripts` (`tests/test_frontend_render_hygiene.py:240`):
   利用**行号序**断言 `gsap` 的 `<script>` 必须位于 `app.js` 和 `pin_anim.js` 之前。
4. `test_alpine_preload_hint_still_present` (`tests/test_frontend_render_hygiene.py:263`):
   断言 `<head>` 中必须存在 `<link rel="preload" as="script">`。

#### 结论: 无需修改现有断言代码！
只要我们在 `moni/web/static/index.html` 中保持相对位置和属性契约不变，现有 35 项 hygiene 测试将**全部保持通过 (100% 绿色兼容)**：

```html
<!-- index.html: <head> 区域 (替换原 cdn 外链) -->
<link rel="preload" as="script" href="vendor/alpine-3.14.1.min.js">
<link rel="preload" as="script" href="vendor/gsap-3.12.5.min.js">
<link rel="stylesheet" href="css/main.css?v=20261023001">

<!-- index.html: 底部脚本执行区域 (严格保持文档序) -->
<script defer src="vendor/alpine-3.14.1.min.js"></script>
<script defer src="vendor/gsap-3.12.5.min.js"></script>
<script src="js/toast.js?v=1785000000"></script>
<script src="js/modal.js?v=1785000000"></script>
<script src="js/safe_url.js?v=20261023001"></script>  <!-- 新增安全模块，先于业务脚本加载 -->
<script src="js/calendar.js?v=20260810001"></script>
<script src="js/cases.js?v=20260826023"></script>
<script src="js/stats.js?v=20260827002"></script>
<script src="js/theme.js?v=20260813019"></script>
<script src="js/app.js?v=20261023001"></script>
<script src="js/icons.js?v=1785000000"></script>
<script src="js/pin_anim.js?v=20261010002"></script>
```

### 3.5 离线可用性验证方案 (CDP 探针复用)
项目已有 `scripts/evidence/sprint24_offline_probe.mjs`，该脚本利用无头 Chrome CDP 协议启动测试，核心逻辑是调用 `Network.setBlockedURLs` 封锁外网。

#### 复用与改造计划
在 sprint-24 探针中（`scripts/evidence/sprint24_offline_probe.mjs:115`），网络阻断策略为：
```javascript
await send('Network.setBlockedURLs', {
  urls: ['*://cdn.jsdelivr.net/*', '*://fonts.googleapis.com/*', '*://fonts.gstatic.com/*']
}, sessionId);
```
在资源未自托管前，`offline` 状态下 `typeof window.Alpine` 是 `undefined`，导致页面留存 `x-cloak` 且首屏无法水合。

**验证指标验收基线 (Acceptance Baseline)**:
运行 `node scripts/evidence/sprint24_offline_probe.mjs http://127.0.0.1:8901 .` 时：
1. `offline.alpineLoaded` 必须为 `"object"` 或 `"function"`（证明 Alpine 从本地完全水合）。
2. `offline.gsapLoaded` 必须为 `"object"`（证明 GSAP 从本地加载成功）。
3. `offline.xcloakLeft` 必须严格等于 `0`（证明 Alpine 成功完成初始化并清除了隐藏标记）。
4. `offline.firstReadableMs` 必须 `< 300ms`（摆脱外网 CDN 阻塞后，本地首屏秒开）。

---

## 4. C 部分：前端测试方案 (Pytest 静态契约)

遵循 moni 既有的测试哲学（基于 pytest 进行源码级静态正则与文档序分析，保证毫秒级无依赖运行），设计新增 `tests/test_frontend_safe_href_and_vendor.py`：

### 4.1 断言清单与判定口径

#### 契约 1: 11 处动态 `:href` 无一遗漏全部纳入护栏
- **断言名称**: `test_all_dynamic_hrefs_are_guarded_by_safe_href()`
- **判定口径**:
  - 正则扫描 `index.html` 全文中的所有 `:href` 绑定属性。
  - 提取所有形如 `:href="EXPR"` 的绑定。
  - 断言匹配集合中的每一个 `EXPR` 必须满足下列两项之一：
    1. 包含 `safeHref(...)` 调用；
    2. 处于受 `isSafeUrl(...)` 或 `!isSafeUrl(...)` 包裹的 `<template x-if>` 或同级 `x-show` 保护分支内。
  - **白名单外遗漏直接阻断**: 若出现任何未包裹的 `:href="a.url"`、`:href="run.log_url"`、`:href="s.url"` 等裸绑定，测试立刻失败并输出行号。

#### 契约 2: 零 CDN 外链与纯自托管保证
- **断言名称**: `test_no_cdn_or_external_scripts_or_links()`
- **判定口径**:
  - 读取 `index.html`，剔除注释块后，断言下列外链域名出现频次必须为 `0`：
    - `cdn.jsdelivr.net`
    - `unpkg.com`
    - `cdnjs.cloudflare.com`
    - `fonts.googleapis.com`
    - `fonts.gstatic.com`
  - 检查所有的 `<script src="...">` 与 `<link rel="preload" as="script">`，其 `src` / `href` 必须以本地相对路径 (`vendor/`, `js/`, `css/`, `icons/`) 开头。

#### 契约 3: Vendor 静态资产防漂移与哈希锁定
- **断言名称**: `test_vendor_assets_integrity_and_license_presence()`
- **判定口径**:
  - 断言 `moni/web/static/vendor/alpine-3.14.1.min.js` 存在且 SHA-256 匹配。
  - 断言 `moni/web/static/vendor/gsap-3.12.5.min.js` 存在且 SHA-256 匹配。
  - 断言 `moni/web/static/vendor/LICENSE-ALPINE.txt` 与 `LICENSE-GSAP.txt` 文件存在且非空（行数 > 10）。

#### 契约 4: safe_url.js 纯函数全矩阵行为覆盖 (Node 子进程单测)
- **断言名称**: `test_safe_url_logic_matrix_via_node()`
- **判定口径**:
  - 在 pytest 内部启动 `subprocess.run(["node", "-e", ...])` 执行矩阵断言，测试向量真值表：
    ```javascript
    const matrix = [
      // 合法放行 (返回 true, safeHref 返回原值)
      { in: 'https://36kr.com/p/1', ok: true },
      { in: 'http://news.163.com/item', ok: true },
      { in: '/artifacts/brief-2026.html', ok: true },
      { in: '/fetch', ok: true },
      { in: 'mailto:contact@test.com', ok: true },
      { in: '  https://qbitai.com/feed  ', ok: true },

      // 恶意/危险协议阻断 (返回 false, safeHref 返回 null)
      { in: 'javascript:alert(1)', ok: false },
      { in: 'JAVASCRIPT:alert(document.cookie)', ok: false },
      { in: '  javascript:void(0)  ', ok: false },
      { in: 'vbscript:msgbox(1)', ok: false },
      { in: 'data:text/html;base64,PHNjcmlwdD4=', ok: false },
      { in: 'file:///etc/passwd', ok: false },
      { in: 'blob:http://127.0.0.1:8900/uuid', ok: false },
      { in: 'about:blank', ok: false },

      // 相对路径逃逸/跳板阻断
      { in: '//evil.com/phish', ok: false },
      { in: '/\\evil.com', ok: false },
      { in: '\\evil.com', ok: false },

      // 边界输入
      { in: '', ok: false },
      { in: '   ', ok: false },
      { in: null, ok: false },
      { in: undefined, ok: false }
    ];
    ```

#### 契约 5: 现有 Hygiene 测试不破坏验证
- **断言名称**: 继承运行 `pytest tests/test_frontend_render_hygiene.py`
- **判定口径**: 35 项测试全量 Pass，证明 defer 顺序、`window.gsap` 防护规则完好无损。

---

## 5. 实施步骤与迁移检查表 (Checklist)

1. **Step 1: 准备 Vendor 目录与许可证文件**
   - 创建 `moni/web/static/vendor/`。
   - 固化 `alpine-3.14.1.min.js` 与 `gsap-3.12.5.min.js`，核对 SHA-256。
   - 写入 `LICENSE-ALPINE.txt`、`LICENSE-GSAP.txt`、`README.md`。
2. **Step 2: 创建 `safe_url.js` 核心模块**
   - 实现 `isSafeUrl(url)` 与 `safeHref(url)`。
   - 导出给全局 `window` 与 CommonJS。
3. **Step 3: 更新 `index.html` 资源外链**
   - 将 preload 和 script 标签切到 `vendor/` 路径。
   - 引入 `<script src="js/safe_url.js?v=...">`。
4. **Step 4: 改造 11 处动态 `:href` 绑定**
   - 逐一替换并加入 `.url-blocked` 与 `badge-danger` 视觉反馈。
5. **Step 5: 更新 `main.css`**
   - 补充 `.url-blocked` 与 `a:not([href])` 基础降级样式。
6. **Step 6: 同步更新离线构建脚本 `scripts/build_showcase_html.py`**
   - 修正脚本正则，使其识别并剥离本地 `vendor/` 脚本标签。
7. **Step 7: 测试与证据固化**
   - 运行静态测试 `pytest tests/test_frontend_safe_href_and_vendor.py`。
   - 运行全量测试 `pytest tests/` 确保 947+ 测试不回归。
   - 执行 `scripts/evidence/sprint24_offline_probe.mjs` 截取离线证据。

---

## 6. 回滚方案 (Rollback Strategy)
本方案不涉及数据库变更（零 DDL / 零 Migration），全部改动局限在前端静态资源：
- 若因特定老旧信源 URL 格式异常触发误拦截，可在 `safe_url.js` 中微调正则放行，即时生效。
- 若需整体撤销，直接 `git revert` 对应前端提交即可，零数据污染隐患。
