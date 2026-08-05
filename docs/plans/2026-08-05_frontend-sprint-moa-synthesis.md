---
source: ai-subagent
created: 2026-08-05
type: reference-recommendation
scope: frontend-only, no backend / schema
basis: docs/full-audit-2026-06-26.md, docs/full-audit-2026-07-03.md, docs/research/uiux-reader-dashboard-best-practices-2026.md
status: reference-only — no edits produced
---

# moni 前端 Sprint 方向 — MoA 综合参考 (2026-08-05)

> 角色: 模拟 MoA 综合/参考 panel, 把已有审计 (2026-06-26 / 2026-07-03 / UIUX 研究) 收口成 **3 个** 互斥的设计方向, 用同一组维度横比, 推一个 **sprint-sized** 的执行方向.
> 边界: 只做参考建议, 不编辑代码; 不动 backend / schema; 不动 `moni/db/seed_config.py` (当前 dirty change).

---

## 0. 已知基线 (审计一致结论)

| 维度 | 当前 | 来源 |
|------|------|------|
| 文章屏表格行数 | 62 行 (桌面默认 page_size) | index.html:189 `articlesPageSize` |
| 筛选控件尺寸差 | 搜索 input 35px vs select 27px (inline padding 4×8) | index.html:71-86 inline `style=` |
| 表格视觉噪声 | 来源/分类/状态/时间多列同行, fresh(绿)+new(紫)+tag(蓝) 争色 | full-audit-2026-07-03 §3.1 |
| 行高锯齿 | 标题长度 + 标签数量撑行 | 同上 |
| 标签堆叠 | 一行 3-4 个 tag 换行 | 同上 |
| 模态对话原语 | `alert()` 58 处 (app.js 主导), `confirm()` 散布; 12 个 modal Escape/focus 不齐 | app.js: alert/confirm 计数 |
| topnav | 9 个平铺链接 (文章/CRUD/跑批/抓取/LLM任务/分享/菜篮子/配置/LLM设置) | index.html:25-37 |
| 配置 / 抓取屏 | 仍是 table-heavy, 缺 form | full-audit-2026-07-03 §3.1 |
| 分享屏 | 暴露 raw ID / raw status, 失败无降级 | 同上 |
| 应用身份 | 中国传统色 OKLCH 双主题, GSAP+Canvas 抓取庆祝 modal, 整体冷色极简 | main.css + index.html 内联 |
| 当前文件规模 | app.js 3612 / index.html 2355 / main.css 1209 / js/*.js 7 个 1-行占位 | ls 验证 |
| Alpine 极限判断 | 仅命中 3000 行单文件 1 条, **不迁移框架** | uiux-research §5 |
| 已有预留 | js/{articles,fetch,share,cron,crud,icons,pin_anim,theme}.js 全部占位 1 行 | ls 验证 |
| 业务连续性约束 | `moni/db/seed_config.py` 当前 dirty, **必须保留不动** | git status |
| PR / 部署纪律 | feature branch on main checkout, gh PR → squash → tag → 手动启 8900 | AGENTS.md |

---

## 1. 三个互斥的设计方向

> 三方向**不互斥于设计系统**, 都遵守 moni 现有 OKLCH 中国传统色 + 冷色极简 + 不刺眼 + GSAP/Canvas 仅用于低频高情绪节点 (抓取完成) 的视觉身份. 互斥点是 **优先解决哪类用户痛点**.

### 方向 A — **"扫读速度"** (列表 / 表格本身的视觉与节奏)

> 把 62 行数据表变成可扫读的三层信息层级. 聚焦文章查阅屏 + 列表层 (含 Pin/全部 子 tab). 视觉不动品牌, 修节奏.

**核心动作**:
- 列表行重排: 主标题 (15-16px / 500-600w) → 元信息 (12-13px / 60% 灰, 来源+时间+tag) → 可选摘要 (13-14px / 2 行截断, hover 全文)
- 标签列: 最多显 2 个 + `+N` 折叠, 不撑行
- 状态色收敛: `fresh` (绿) + `new` (紫) 合并为单一 dot 指示, 色彩预算让给分类
- 字号收敛到 4-5 档 (12/13/14/16/20), 三级灰度 token 化 (`--text-primary/-secondary/-tertiary`)
- 行高 / 内边距统一到 8px 基准网格
- 操作列图标化 + 增大点击区
- 搜索 input 高度 35px → 与 select 同高 27px (或反向统一), 移除 inline style

**不触及**: modal 交互, topnav, 键盘流, 抓取庆祝动效, 后端 / schema, 抓取/分享/配置屏

### 方向 B — **"对话确定性"** (alert → toast, modal Escape/focus, 抓取/分享失败降级)

> 把 42 处 alert/confirm + 12 个 modal 的不可预测性变成确定性反馈. 修 **人机对话的契约**, 不改视觉品牌.

**核心动作**:
- 引入统一 toast 组件 (已存在样式, 缺系统化): 信息 / 成功 / 错误 / **undo** 四档, 自动消失 (4s) + 失败保持 (sticky, 带 "复制错误" / "重试" 按钮)
- 替换 `alert()` → toast (成功/错误) + `confirm()` → modal 确认 (仅当 destructive, 如批量 unpin / 删除源)
- 12 个 modal 统一: Escape 关闭 / Tab focus trap / open 时锁定 body scroll / 关闭后焦点归还触发元素
- 抓取 modal: 按源展示失败原因 + "重试该源" 按钮 (Nielsen #9 错误恢复), 而非全局失败
- 分享 modal: 失败时 fallback (raw ID / raw status → "复制 Markdown 列表" / "导出为 JSON"), 不暴露数据库原文
- 错误 toast 分级: 网络错误可重试 / 4xx 显 detail / 5xx 推 IM

**不触及**: 表格视觉, topnav, 键盘流, 设计 token, 后端 / schema

### 方向 C — **"键盘 + 导航骨架"** (topnav 9 链接, 键盘流, 信息架构)

> 把"打开 moni = 看 1498 行表 + 9 个平铺 tab" 改成"打开 = 简报视角 + 键盘可达". 修 **信息架构的骨架**, 风险面最大.

**核心动作**:
- topnav 收敛: 9 → 4 主区 + 1 工具区 (文章 / 抓取 / 分享 / 配置 ; 工具 = LLM 设置 / 主题 / 服务状态)
- 引入键盘流: `j/k` 上下 (列表焦点) / `p` Pin / `/` 聚焦搜索 / `?` 快捷键面板 / `g g` / `G` 跳首尾 / `Esc` 关闭 modal (与方向 B 联动)
- 文章屏首屏加载骨架屏 (布局可预测, 与真布局同形)
- 微交互: 列表 hover/选中态统一, 150-250ms `transform/opacity`, 尊重 `prefers-reduced-motion`
- 空状态即教程: 无文章时展示 "如何添加源" 引导 (Nielsen #10)
- "抓取进度" modal 阶段文案 (抓取/解析/打标/摘要) 强化 (这是已有 GSAP+Canvas modal 的扩展)

**不触及**: 表格视觉细节, toast 系统本身 (与方向 B 协同但不重复), modal Escape/focus (协同), 后端 / schema

---

## 2. 横比矩阵

| 维度 | A — 扫读速度 | B — 对话确定性 | C — 键盘 + 导航 |
|------|--------------|---------------|---------------|
| **用户价值** | 高 (62 行表 → 视觉清爽, 扫读快; 单屏扫 60+ 条不疲劳) | 中-高 (消除 alert 弹窗打断; modal 可逃逸; 失败可恢复) | 高-中 (留存型改进, 键盘流是阅读器类核心留存; 但单人桌面自用, 频率取决于习惯) |
| **价值兑现速度** | 立竿见影 (刷一次列表就能感受到) | 立竿见影 (下一次 modal 操作就生效) | 需 2-3 次使用习惯才显效 |
| **覆盖广度** | 窄 (聚焦文章屏 + 列表 token) | 中 (12 modal + 42 alert, 触达 70% 用户路径) | 中-宽 (topnav + 键盘 + 骨架 + 空状态 + 抓取 modal, 但功能增多) |
| **范围 / LOC 估算** | 3-4 天, ~400-600 LOC (CSS token + index.html 列表段 + 1-2 个 Alpine helper) | 4-5 天, ~600-900 LOC (toast 组件 + alert 替换 + modal 焦点/转义统一 + 抓取分源错误) | 5-7 天, ~700-1100 LOC (topnav 重组 + 键盘监听 + 骨架屏 + 空状态) |
| **实施风险** | 低 (纯 CSS + 内联修改, 无 JS 状态机变化) | **中** (alert→toast 要全量替换 42 处, 每处上下文语义不同; modal focus trap 自己写, 漏一处回归就回弹) | **中-高** (topnav 9→4 是信息架构决策, 单人项目无对照用户; 键盘流与现有 `<input>`/select 默认行为有冲突边界) |
| **可逆性** | 高 (纯样式, 单 PR 回滚即可) | 中 (toast 替换涉及 42 处, 涉及 "destructive 操作" 仍需 modal 确认, 需对照设计; 但代码层面是无依赖增量) | 中-低 (topnav 重组改的是导航契约, 用户习惯重置; 但物理上是 HTML/CSS 改动, git revert 即可) |
| **视觉身份契合** | 高 (复用 OKLCH + 冷色极简, 字号/灰度收敛是研究 §一 推荐) | 高 (toast 不增视觉负担, 是减少弹窗打断) | 中-高 (topnav 收敛可能引发 "moni 变了" 的体感; 保留品牌色, 形态调整) |
| **是否动 backend/schema** | 否 | 否 | 否 |
| **依赖新增** | 0 | 0 | 0 |
| **是否踩 `seed_config.py`** | 否 | 否 | 否 |
| **是否动抓取 GSAP+Canvas modal** | 否 (保留) | 仅增量 (失败分源展示, 不改粒子动效) | 仅增量 (阶段文案, 不改动效) |
| **测试影响** | 0 (前端无测试) | 0 (前端无测试) | 0 (前端无测试) |
| **PR / 标签节奏** | 1 个 PR, 1 个 tag (例 v1.0.13.0-list-rhythm) | 1 个 PR, 1 个 tag (v1.0.13.0-dialog) | 1-2 个 PR, 1 个 tag (可拆 topnav vs 键盘流) |
| **后续杠杆** | 高 (token 化后方向 B/C 的视觉增量零成本) | 中-高 (toast 系统是后续所有功能的反馈底座) | 中 (键盘流需在每次新增功能时保持一致, 有回归成本) |

---

## 3. 推荐: **方向 B — "对话确定性"** 作为本 Sprint 主线

### 3.1 为什么是 B

| 理由 | 说明 |
|------|------|
| **痛点最直接** | 42 处 `alert()` 是用户每天 100% 会撞到的打断, 12 个 modal Escape 缺失是 "不能退出" 的死循环体验. 失败可恢复是审计 §五 价值链修复的入口. |
| **范围可控** | 4-5 天, 单一前端增量, 不动 backend/schema, 不引入新依赖. |
| **零品牌风险** | 不改视觉风格, 不改 OKLCH, 不动抓取 GSAP+Canvas 庆祝动效, 不动 topnav. moni 用户不会觉得 "moni 变了", 只觉得 "终于不弹窗了". |
| **后续杠杆** | toast 组件是后续所有新功能的反馈底座 (方向 A 的标签折叠也好, 方向 C 的键盘流也好, 都需要统一反馈层). 先做 B, A 和 C 各自独立 Sprint 时, 反馈通道已经就位. |
| **审计闭环** | 2026-07-03 审计 §十 P0 第 4 项 "失败告警" + P1 第 10 项 "toast 系统替换 31 处 alert" 直接命中. |
| **避免泛 SaaS 化** | 不引入组件库, 不换框架, 不重做导航, 不抄 Linear/Notion. 只把现有破碎的对话原语补齐. |
| **不动脏 change** | 完全不碰 `moni/db/seed_config.py`. |
| **可逆** | alert/confirm → toast 是单 PR 可 revert 的纯前端替换, 即使回归也只是回到 alert, 不影响数据/后端. |

### 3.2 不推荐 A / C 的原因 (明确)

- **不推荐 A (扫读速度) 优先**: 文章屏表格问题真实, 但用户**已经能扫完 62 行**; 痛点是 "看不下去" 还是 "弹窗打断"? 实际前者是次要诉求, 后者才是主诉求. 视觉节奏是增量, 不是止血. 而且 A 一旦先做, B 的 42 处 alert 还会继续打断, 用户体验提升是"看的时候清爽, 但每次操作都被弹窗击碎".
- **不推荐 C (键盘 + 导航) 优先**: 单人桌面自用场景, 键盘流价值兑现速度慢; topnav 9→4 是信息架构决策, 无对照用户, 风险面最大. C 适合作为**下一个 Sprint** 的主线 (与 B 协同: B 给 toast 底座, C 在此之上加键盘流与首屏骨架).

### 3.3 非目标 (explicit non-goals)

| 不做 | 原因 |
|------|------|
| ❌ 改 OKLCH 中国传统色色板 | moni 视觉身份, 保留 |
| ❌ 改 GSAP+Canvas 抓取庆祝 modal | 低频高情绪节点, 研究 §二 #3 推荐保留 |
| ❌ 改 topnav 9 链接 | 信息架构决策, 单人无对照用户, 留下一 Sprint |
| ❌ 引入键盘流 (j/k/p/?) | 与 C 重叠, 留给下一 Sprint |
| ❌ 改文章屏表格行高/标签折叠/状态色 | 与 A 重叠, 留给 A Sprint |
| ❌ 引入组件库 (Radix / Headless UI / shadcn) | 单人自用, Alpine 仍在甜蜜区 (审计 §四) |
| ❌ 迁移到 Vue/React/Svelte | 不满足 ≥3 迁移信号 (研究 §五) |
| ❌ 拆分 app.js 为多 Alpine.data 模块 | 是 P2, 留给 v1.0.14+ 路线 |
| ❌ 任何 backend / schema / API 改动 | 审计已指出后端 13k 行 + 75 端点现状稳定, 本 Sprint 不动 |
| ❌ 改 `moni/db/seed_config.py` (dirty change) | 当前用户的脏 change, 必须保留 |
| ❌ 新增 npm/pip 依赖 | Alpine/CDN 已够, 0 新依赖 |
| ❌ 加单测 / vitest | 前端零测试是已知缺口, 不在本 Sprint 范围 |
| ❌ 动 SQL / migration | schema v22 已稳定, 不动 |

### 3.4 分阶段验收标准 (phased acceptance criteria)

> **Sprint 长度**: 5 个工作日. 每天一个阶段, 第 5 天为收尾 (PR + 手动启 8900 + tag + curl 5 端点 200).

#### Phase 1 (D1, ~6h) — Toast 组件化与原语冻结

**做**:
- 把 `moni/web/static/index.html` + `js/` 里已经写过的 toast div 抽成 `js/toast.js`, 暴露 `moniToast.success/error/info/undo(msg, opts)` 全局 API
- 设计 4 档: `info` (4s 自动消失) / `success` (4s) / `error` (sticky, 带 "复制" 按钮) / `undo` (8s, 带动作按钮)
- 视觉: 复用 OKLCH 现有变量, 不新增 token, 不改色板
- ARIA: `role="status"` / `role="alert"`, 焦点不抢, 不强制 tab

**验收 (DoD)**:
- [ ] `js/toast.js` 落地, `app.js` 顶部 `import` 或全局挂载
- [ ] 至少 3 处手写 `alert()` 被替换, `curl 8900/articles` 端点 + 浏览器控制台 0 新增 error
- [ ] 现有 toast 视觉一致 (无视觉抖动, 无新增 hover 状态)
- [ ] 不动 `main.css` 第 1-200 行色板定义

#### Phase 2 (D2, ~6h) — 剩余 alert/confirm 全量替换 (38 处)

**做**:
- app.js 58 处 + crud.js 3 处 + 其他 0 处的 `alert` / `confirm` 全量替换
- 分类规则: `alert("成功")` → `toast.success`; `alert("错误")` → `toast.error` (sticky); `confirm("删除?")` → **保留** modal 确认 (destructive 操作)
- 一处一处 diff, 保留原语语义, 不批量 sed

**验收**:
- [ ] `grep -c "alert\(\|confirm\(" moni/web/static/js/*.js` 总数从 61 降到 ≤5 (残留的 ≤5 应该是注释 / 测试 fixture)
- [ ] 浏览器实测: CRUD 删源 / 删 tag / 批量 unpin 仍弹 modal 确认 (不静默 destructive)
- [ ] 网络错误 → toast.error (sticky, 含 "重试")
- [ ] 业务错误 → toast.error 含后端返回的 `detail`
- [ ] 成功保存 / 抓取完成 / 分享成功 → toast.success, 4s 自动消失

#### Phase 3 (D3, ~7h) — 12 个 modal 焦点 / Escape / 滚动锁统一

**做**:
- 抽 `js/modal.js`, 暴露 `openModal(selector) / closeModal()` + 自动:
  - Escape 关闭
  - Tab focus trap (焦点在 modal 内循环)
  - open 时 `document.body.style.overflow = 'hidden'`, close 时还原
  - 关闭后焦点归还触发元素
- 12 个 modal 逐一接入 (文章详情 / CRUD / 抓取配置 / 主题管理 / 分享确认 / Pin 详情 / LLM 设置 / 等)

**验收**:
- [ ] `js/modal.js` 落地, 提供统一 API
- [ ] 12 个 modal 全部 Escape 关闭 (浏览器手测)
- [ ] Tab 在 modal 内循环, 不逸出到 background
- [ ] 关闭 modal 后, 焦点回到触发按钮 (用 `lastFocusedElement` 缓存)
- [ ] 抓取完成 modal (GSAP+Canvas) 行为不变 (粒子动效保留)
- [ ] 视觉零回归 (截图 diff: 现有 12 个 modal 外观一致)

#### Phase 4 (D4, ~6h) — 抓取 / 分享失败降级 (审计 §五 价值链入口)

**做**:
- 抓取 modal: 失败时按源展示 (源名 + 错误信息 + "重试该源" 按钮), 而非全局 "抓取失败"
- 分享 modal: 失败时 toast.error (含 detail) + 不暴露 raw ID / raw status (审计 §五 "share exposes raw IDs/statuses and failures")
- 抓取分源错误: 不引入新的错误码, 只把现有 `fetchResultObj` 的 `dropped_articles` / `errors` 字段**显式**展示在 modal 内

**验收**:
- [ ] 抓取失败时, modal 内可见 "源 X 失败: 401, 点重试" 而非裸 alert
- [ ] 分享失败时, toast.error 含可读错误 + 不显示 raw task ID
- [ ] 抓取分源重试按钮只重试该源 (后端是否已有? 若无, 本 Sprint 只展示错误, 重试留 v1.0.14)

#### Phase 5 (D5, ~5h) — 收尾

**做**:
- `STATUS.md` 更新 "前端对话层重做" 阶段
- `USER_GUIDE.md` 同步: 移除 "会弹 alert" 的描述, 改为 "右下角 toast"
- `vibe-coding-log.md` append
- `HANDOFF.md` 更新待办
- `git checkout -b feature/v1.0.13-dialog-certainty` → commit → push → `gh pr create --base main` → user review → squash merge → force push tag `v1.0.13`
- 手动 `/usr/local/bin/python3 -c "import uvicorn; from moni.web.server import create_app; uvicorn.run(...)" &` 启 8900
- `curl 5 端点` 验证 200 OK (articles / crud / cron / fetch / share / config)
- 不动 `moni/db/seed_config.py` (确认 git status 不再 dirty 或只保留其 dirty)

**验收**:
- [ ] `git tag v1.0.13` 已存在, force push 完成
- [ ] 8900 健康, 端点 5/5 = 200
- [ ] `git status` 干净 (除 `seed_config.py` 若是用户保留)
- [ ] `grep -c "alert\(\|confirm\(" moni/web/static/js/*.js` 总数 ≤5
- [ ] 12 个 modal Escape / focus / scroll-lock 行为齐
- [ ] 抓取失败按源展示, 分享失败不暴露 raw ID
- [ ] AGENTS.md Session 收尾 Checklist 9 项全勾

---

## 4. Sprint 内交叉风险与缓解

| 风险 | 触发 | 缓解 |
|------|------|------|
| alert 替换语义错位 | 把"成功"alert 改成 error toast, 反之亦然 | Phase 2 逐处人工 diff, 不批量正则; 每个 PR review 看 5-10 处 |
| modal focus trap 漏掉第三方 (Alpine `$dispatch` 触发) | `@keydown.escape` 监听不到 nested 元素 | Phase 3 写最小测试 (jsdom 模拟 Tab 序列), 无单测时人工录 1 分钟 4 个 modal 的键盘行为 |
| 抓取 GSAP+Canvas modal 与新 modal helper 冲突 | 该 modal 是 `<div>` 监听 + GSAP 动画, 直接接入新 helper 可能误关 | Phase 3 把抓取 modal 标为 "legacy escape handle", 单独分支 |
| 0 前端测试 | 任何回归都靠人工 | Phase 5 收尾时人工录屏 5 分钟 12 modal + 抓取流程, 归档到 `docs/test/2026-08-05_dialog-sprint.mov` |
| 用户中途改需求 | "想顺便改 topnav" | 在 PR description 写明 non-goals, 非目标列表贴 review 顶 |

---

## 5. Sprint 间建议 (本 Sprint 不做, 下一 Sprint 可接)

| Sprint | 方向 | 杠杆点 |
|--------|------|--------|
| **Sprint 2** | 方向 A — 扫读速度 | B 给的 toast 底座 + A 的视觉 token 化, 1 周完成 |
| **Sprint 3** | 方向 C — 键盘 + 导航 | topnav 9→4 + 键盘流 + 骨架屏, 风险面最大, 单独立 Sprint |
| **Sprint 4+** | 后端 value chain (审计 §十 P0 #2-5) | share 失败根因, fetch queue DB 化, 抓取自动摘要, 文件不在本参考范围 |

---

## 6. 自我边界声明

- 本文档**仅是参考建议**, 未编辑 `moni/` 下任何源码; `git status` 不应因本任务变化.
- 当前 dirty change `moni/db/seed_config.py` 全程未被读写.
- 三个方向都基于已有审计 (2026-06-26 / 2026-07-03) + UIUX 研究, 未做新的源码逐行审计; 数字 (62 行 / 35px / 27px / 12 modal / 42 alert) 沿用父任务的上下文.
- 实施时建议: 先在 8901 dev 端口完成 1-4 阶段, 端到端手测, 再合并 → tag → 手动启 8900 (沿用 AGENTS.md 端口分离纪律).