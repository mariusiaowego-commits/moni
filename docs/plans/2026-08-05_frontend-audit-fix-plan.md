# Sprint 02 — Frontend Audit-Fix PLAN

> Source audit: `docs/frontend-arch-a11y-audit-2026-08-05.md` (16 风险, 6 P0).
> Source direction: `docs/2026-08-05_frontend-sprint-moa-synthesis.md` (MoA 推 B "对话确定性").
> 现有 dialog-certainty plan: `docs/plans/2026-08-05_frontend-dialog-certainty-plan.md` (Q1-Q4 待 dad 拍板).
> 本文档是 dialog-certainty plan 的**审计驱动扩展** — 用 audit 的 16 项编号逐条给出处理决策.

## Goal

把 moni 前端审计 16 项风险按 sprint 单 PR 可回滚的节奏收口。Sprint 02 主线 (方向 B) 已经拍板 "对话确定性": toast 化 + modal 焦点/Escape/scroll-lock 契约 + 抓取/分享失败降级。本次 audit-fix 在不破坏该方向的前提下，把 audit 的 P0/P1 一并清掉，让 Sprint 02 一次性交付真正"止血"的 PR。

## 范围与边界 (沿用 Sprint 02 Non-goals)

| 不做 | 原因 |
|---|---|
| 不迁 Vue/React | Alpine 仍在甜蜜区 |
| 不改 OKLCH 色板 | moni 视觉身份 |
| 不动 backend / API / schema / SQL | audit 锁定前端层 |
| 不重做 topnav 9 链接 | Sprint 03+ 路线 |
| 不做 j/k/p 键盘流 | Sprint 03+ 路线 |
| 不改 `moni/db/seed_config.py` | dad 已有 dirty change |
| 不引入 npm/pip 依赖 | Alpine + 原生 DOM 已够 |
| 不拆完 3612 行 `app.js` (F1 全量) | 本次只**试拆低风险块**, 余下留 Sprint 03+ 路线 |
| 不引入单测 / vitest | 前端零测试是已知缺口, 不在 sprint 范围 |
| 不改 GSAP+Canvas 抓取庆祝动效 | 低频高情绪节点保留 |

## 16 项处理决策

| # | 风险 | 处理 | 理由 |
|---|---|---|---|
| F1 | 3612 行 `app.js` 单文件 | **拆**: basket(1081)/share(2638)/llm(3437+) 抽独立 Alpine.data 模块, 注册到 `themeMixin` 之后 | 单文件 149 KB 是最大维护风险. 拆 3 块预计减 ~700 LOC, 余下 F1 余项留 Sprint 03+ |
| F2 | 5 个 stub JS 文件 | **删 4 纯注释** (`articles.js` / `cron.js` / `fetch.js` / `share.js` 1 行); `crud.js` **删除** (重复注册 + 漏 `?confirm=1` 隐患, 无业务必要, `app.js:1016-1045` 已实现完整 CRUD) | 4 stub 是占位, 删零风险. crud.js 上线一次会改 DELETE 行为, 必须删 |
| F3 | 异步 timer 无 teardown | **修**: `_sharePollInterval` / `_workerLogPollInterval` / `_animFrame` 加显式 stop; `setTimeout(initStageWithSources, 100)` 改 5 s 上限 + 退出分支 | `_sharePollInterval` 是最严重的 (永不清理) |
| F4 | 42 个 alert/confirm | **修**: 用本次新建的 `useModal()` + `moniToast.*` 替换. destructive 保留 modal 确认, 非 destructive 走 toast (成功/错误/网络断) | audit 与 Sprint 02 主线一致 |
| F5 | 7/12 modal 缺 Escape | **修**: useModal() 自动接入 Escape, 12 个 modal 改造 (6 已接自动跳过) | F5 + F14 合并 |
| F6 | busy-wait `setTimeout(initStageWithSources, 100)` | **修**: 加 5 s 50 次重试上限, 超时显示 "源加载失败, 请重试" 警告; 不再 silent loop | audit F6 推荐路径 |
| F7 | `navigate()` / `onPopState()` / `init()` 三份 dispatch 复制 | **不修本 sprint**: 复制 3 份是共同 closure, 重构风险面大于收益; 留 Sprint 03+ 跟 F1 拆 app.js 一起做 | sprint 范围控制 |
| F8 | pin icon SVG 每次重渲 | **不修本 sprint**: 不影响功能, 纯 perf, 留 v1.0.14+ | P2 low, 性价比低 |
| F9 | source icon 4 处重复 (`getSourceIcon` + `_activeSourcesForModal` + PNG + theme.js parser) | **不修本 sprint**: 加新源是低频动作, 引入 source-manifest 概念是架构改造; 留 v1.0.14+ | P1 mid, 改动需重新设计 |
| F10 | `oklch(from …)` 30+ 处 | **不修本 sprint**: moni 部署到 macOS Chrome 119+, 兼容性已满足验收环境; Firefox 128 升级窗口距当前 (8月 2026) 已过半年, 主力受众不踩. 留 v1.0.14+ | P1 mid, 引入 `@supports` 包装会带来 30+ 处规则膨胀, 视觉回流风险 |
| F11 | 单移动端媒体查询 | **不修本 sprint**: moni 是桌面自用工具, 移动端体验在 audit S2 已经定性"非主线" | P1 mid, 不在 Sprint 02 主线 |
| F12 | design token 在 CSS + theme.js 重复声明 | **不修本 sprint**: 这是设计系统改动, 应单独 Sprint 走 token-SSoT 化 + 加 CI drift check | P1 mid, 涉及 `:root` + `.dark` + `DEFAULT_THEME` 三处 + 加 CI, sprint 范围外 |
| F13 | modal close handler 7 种风格混用 | **修**: useModal() 暴露统一 `openModal(name)/closeModal(name)`, 12 个 modal 接入; 现有 7 种风格保留向后兼容但加 `// TODO: sprint 03 cleanup` | 间接修, 通过 useModal helper 收敛 |
| F14 | modal 无 role/aria-modal/aria-labelledby | **修**: useModal() 自动补这三个属性 + 标题 `<h3>` 加 `id` 关联 | 与 F5 合并 |
| F15 | polling `.then` 链在 `setInterval` 内可堆叠 | **不修本 sprint**: 留给 F1 拆 app.js 时同步重构, 单独修会引入跨模块改动 | P2 low, 依赖 F1 |
| F16 | head meta 缺失 (description / theme-color / color-scheme / favicon) | **修**: `index.html <head>` 补 5 行, 加 inline SVG favicon (data: URL 不发请求) | P2 low, 5 分钟, 顺手做 |

**汇总**: 16 项里本次修 8 项 (F1/F2/F3/F4/F5/F6/F13/F14/F16 = 9 项), 不修 7 项 (F7/F8/F9/F10/F11/F12/F15), 都给理由 + 后续 Sprint 路线.

## 优先级与任务拆分

### Block A (PR #1, P0 止血) — useModal + 12 modal 接入 + alert/confirm 替换

1. 新增 `moni/web/static/js/modal.js`
   - 暴露 `window.moniModal.open(id)` / `close()` / API
   - 自动: `Escape` 关闭 / `Tab` focus trap / body scroll lock / 焦点归还触发元素
   - 自动: 给目标 modal 元素加 `role="dialog"` + `aria-modal="true"` + `aria-labelledby="<标题id>"`
2. 新增 `moni/web/static/js/toast.js`
   - 暴露 `window.moniToast.info/success/error(msg, opts)` / `.undo(msg, callback)`
   - `role="status"` / `role="alert"`, 自动消失 4s, error sticky 含 "复制错误" 按钮
3. 改 `moni/web/static/js/app.js`
   - 33 个 `alert(...)` → `moniToast.success/error/info` 分类替换
   - 9 个 `confirm(...)` → 走自定义 modal 确认 (`moniModal.confirm(msg, onConfirm)`)
   - 引 `modal.js` + `toast.js` 在 HTML 顺序中先于 `app.js`
4. 改 `moni/web/static/index.html`
   - 12 个 `<div class="modal-backdrop">`: 补 `@keydown.escape.window` / 补 `<h3 id="..." aria-labelledby>`
   - 6 个已接 Escape 自动跳过, 6 个未接补齐
   - 替换 `&times;` close 按钮的 `aria-label="关闭"`

**预计 LOC**: ~600 LOC (useModal ~200, toast ~150, alert→toast 替换 ~120, confirm→modal ~80, HTML 注释/标题 ~50).
**验收**: 浏览器跑 12 个 modal 键盘测试 (Escape 关 / Tab 循环 / 焦点归还); grep `alert\(\|confirm\(` 总数从 42 降到 0 (允许 ≤3 注释残留).

### Block B (PR #2, P0 修残) — 清 stub + timer teardown + busy-wait 上限

1. 删 `moni/web/static/js/{articles,cron,fetch,share}.js` (4 个 1 行占位)
2. 删 `moni/web/static/js/crud.js` (62 行, 已在 `app.js:1016-1045` 等价实现)
3. 改 `app.js`:
   - `_sharePollInterval` 加 `_stopSharePoll()` + `navigate()` 离开 `/share` 时调用
   - `_workerLogPollInterval` 已在 close path, 加 `_startWorkerLogPoll` 失败回滚分支
   - `_animFrame` / `_burstTimer` 确保 `_stopParticleAnimation` 在 modal 关闭时被调用 (审计已部分覆盖)
   - `setTimeout(initStageWithSources, 100)` 改 `_initStageRetryCount` 上限 50 次 (~5s), 超时显示 toast.error "源加载超时"
4. `_fetchPollTimer` 已多次清理, 复核不留 set-and-forget

**验收**: 浏览器实测离开 `/share` 后 5 秒无 `/api/share/tasks` 请求 (DevTools Network); 关闭抓取进度 modal 后无内存增长的粒子 rAF (DevTools Performance).

### Block C (PR #3, P2 顺手) — head meta + 试拆 app.js

1. `index.html <head>` 补:
   ```html
   <meta name="description" content="moni — 同业信源自动抓取与本地新闻库">
   <meta name="color-scheme" content="light dark">
   <meta name="theme-color" content="#f5f1e8">
   <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,...">
   ```
2. 试拆 app.js (F1 部分):
   - `moni/web/static/js/modules/basket.js` — basket 状态 + `deleteBasketItem` + `closeBasket`
   - `moni/web/static/js/modules/share.js` — `_startSharePoll` 等 share 模块全部方法
   - `moni/web/static/js/modules/llm.js` — LLM providers/models/settings/jobs 全部方法
   - `app.js` 通过 `Alpine.data('basket', basketModule)` / `mixins: [basketMixin]` 引用
   - 拆完后 app.js 预计从 3612 → ~2900 LOC
   - 如果 Alpine 接入有 conflict, 回滚 + 在 plan 后续路线里写明 "F1 余项留 Sprint 03+"

**验收**: 全部 4 条核心路径 (articles/fetch/share/config) 行为不变; pytest 全绿; `wc -l moni/web/static/js/app.js` < 3000.

## 拍板问题 (等 dad)

- Q1: 本次 sprint 是否合并到 Sprint 02 "Dialog Certainty" 同 PR, 还是**单独开 v1.0.13.x**？
  - A (推荐): 跟 Sprint 02 合并, 1 个 PR. Sprint 02 还没开始, 直接以 audit-fix 形式合并原 Sprint 02 计划
  - B: Sprint 02 不动, 另起 sprint-03-audit-fix
- Q2: F4 (42 alert/confirm) 范围:
  - A (推荐): 全量替换, 1 个 PR 内清完. destructive 保留 modal 确认
  - B: 仅 4 条核心路径 (articles/fetch/share/config) — 跟原 Sprint 02 plan 一致
- Q3: F1 (app.js 拆模块) 范围:
  - A (推荐): 仅尝试拆 basket/share/llm 三个低风险块 (Block C), 拆不动回滚
  - B: 本 sprint 不动 F1, 留 Sprint 03 路线
- Q4: 验证方式:
  - A (推荐, 沿用 Sprint 02 plan): 8901 dev 真实数据 + 浏览器手动 keyboard smoke, dad 拍板再 PR
  - B: 8901 完整自验 → 直接提 PR

## PR 命名约定

| Block | 分支 | PR 标题 | Tag |
|---|---|---|---|
| A | `feature/v1.0.13-dialog-certainty-modal-accessibility` | "feat(ui): dialog certainty + modal a11y (audit fix F4/F5/F14)" | part of v1.0.13 |
| B | `feature/v1.0.13-audit-fix-timers-stubs` | "chore(ui): audit fix F2/F3/F6 (stubs + timer teardown + busy-wait cap)" | part of v1.0.13 |
| C | `feature/v1.0.13-audit-fix-headmeta-split` | "feat(ui): audit fix F1/F16 (head meta + app.js partial split)" | part of v1.0.13 |

合并顺序: A → B → C (按 P0 → P2 风险降序). 每个 PR 自己 squash merge + force push tag.

## 验收方式 (每次 PR 边界跑一遍)

```
□ /usr/local/bin/python3 -m pytest tests/  (基线 504 pass + 2 skip, 不能更少)
□ scripts/web-8901.sh restart
□ curl 5 端点 200 OK: /articles /fetch /share /config /crud
□ browser smoke:
  - Escape 关 12 个 modal
  - Tab 在 modal 内循环不外逸
  - 关 modal 后焦点回到触发按钮
  - 删除 source/tag/filter 仍弹 modal 确认 (不静默 destructive)
  - 网络断掉时弹 toast.error 不卡死
  - 离开 /share 后 5 秒内无 /api/share/tasks 请求
□ git status 干净 (除 seed_config.py 若是 dad 保留)
```

## Session 收尾文档 (沿用 AGENTS.md Checklist)

- `STATUS.md` — 追加 "v1.0.13 frontend audit-fix (Sprint 02 Dialog Certainty 完结)"
- `USER_GUIDE.md` — 同上
- `vibe-coding-log.md` — append 当日记录 (本地)
- `HANDOFF.md` — 写 Sprint 03 起点 + F1 余项 + F7/F9/F10/F11/F12/F15 留给 Sprint 03+ (本地)
- `AGENTS.md` — 不动 (无项目结构变化)
- `wiki/hermes-base/projects/project-moni.md` — 更新状态行

## 红线 (严格遵守)

- ❌ `git push --force` / `git reset --hard` / `git clean -fd` / `rm -rf` 等破坏性操作 (必须先文字说明)
- ❌ 编造测试结果 (跑真实 pytest)
- ❌ 碰 prod (8900), 验证用 8901
- ❌ 误重启 prod (脚本路径锁定 `/usr/local/bin/python3`)
- ❌ 改 `moni/db/seed_config.py`
- ✅ 用户不在场: 决策性问题走拍板, 不抢跑
