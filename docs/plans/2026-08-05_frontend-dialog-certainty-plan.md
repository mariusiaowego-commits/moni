# Sprint 02 — Frontend Dialog Certainty PLAN

## Goal

把 moni 当前分散的 `alert()` / `confirm()` / modal 行为收敛成统一、可预测的对话层。验收标准是：用户在文章、抓取、分享、配置四条核心路径中，能明确看到操作是否成功、为什么失败、下一步能做什么；所有 modal 可用 Escape 关闭、Tab 不逃逸、关闭后焦点回到触发按钮。保持现有 OKLCH 中国传统色、GSAP+Canvas 抓取动效、FastAPI/Alpine 架构和 API contract 不变。

## Blocking Questions

- Q1：本 Sprint 主方向？
  - **A：对话确定性（推荐）** — toast + modal 契约，风险中等，覆盖四条核心路径
  - B：扫读速度 — 先改文章表格层级、标签折叠和筛选控件，风险最低、视觉变化最大
  - C：导航骨架 — 9 个顶栏入口分组并加键盘流，价值高但信息架构风险最高

- Q2：对 `alert()` / `confirm()` 的迁移范围？
  - **A：先覆盖四条审查路径（推荐）** — articles/fetch/share/config；其余页面后续迁移，避免 42 处一次性大爆炸
  - B：本 Sprint 全站迁完 — 一次收口，但回归面明显增大

- Q3：文章页是否顺手做最小视觉修正？
  - **A：只统一搜索框和 select 高度（推荐）** — 修掉已确认的 35px vs 27px 漂移，不展开表格重做
  - B：完全不碰文章视觉 — 最纯粹的对话层 Sprint
  - C：同时做标签 +N、固定行高和状态色收敛 — 会把方向 A 合并进来，建议另开 Sprint

- Q4：开发验收方式？
  - **A：8901 真实数据 + browser screenshots，dad 回来后拍板再 PR（推荐）**
  - B：8901 完整自验后直接提 PR，dad 只做 PR review

## Assumptions

1. Data：前端继续消费现有真实 API 返回；不增加字段、不修改响应结构。
2. Failure：网络错误显示 sticky error toast；业务 4xx 显示后端 detail；不可安全重试的操作不显示伪“重试”。
3. Boundaries：`moni/db/seed_config.py` 是已有用户改动，Sprint 全程不得读写、stage 或提交。
4. State：toast 队列与 modal focus 状态仅存在浏览器内存，不持久化。
5. Environment：8901 为 dev/test，8900 为 production；视觉 ack 前不创建 PR。
6. Scope：首期覆盖 articles/fetch/share/config；CRUD/cron/LLM 等是否全量迁移取决于 Q2。
7. Testing：正式提交前跑现有 pytest 基线、`node --check`、8901 browser smoke 和核心 modal 键盘验收；UI 中途不反复跑全套。
8. Dependencies：不新增 npm/pip/CDN 依赖；只使用现有 Alpine、原生 DOM 和 GSAP。

## Plan

1. Create `moni/web/static/js/toast.js`
   - 提供 `window.moniToast.info/success/error/undo()`。
   - 支持队列、自动消失、sticky error、动作按钮和 `role=status|alert`。

2. Modify `moni/web/static/index.html`
   - 加 toast region，引用 `toast.js`。
   - 给四条路径相关 modal 补统一语义：`role="dialog"`、`aria-modal="true"`、标题关联。
   - 根据 Q3 决定是否移除 articles filter 的 inline 高度漂移。

3. Create `moni/web/static/js/modal.js`
   - 统一 Escape、Tab focus trap、body scroll lock、trigger focus restore。
   - 不改变 modal 外观，不接管 GSAP 粒子动画生命周期。

4. Modify `moni/web/static/js/app.js`
   - 仅按 Q2 迁移目标路径的 alert/confirm。
   - 成功 → success toast；网络/业务错误 → error toast；破坏性确认 → 统一 confirm modal。
   - 抓取/分享失败文案转换为用户可读状态，不显示 raw task ID 作为主反馈。
   - 修复已存在但未接线的文章筛选分页重置：select change 统一走 `onArticlesFilterChange()`。
   - 为 view change 增加本 Sprint 触及 poll/timer 的 teardown，且给 fetch-stage source 初始化重试加上限。

5. Modify `moni/web/static/css/main.css`
   - 增加 toast stack、状态和动作样式；全部复用现有 token。
   - 如 Q3=A，统一 filter controls 高度与 focus ring。
   - 增加 `prefers-reduced-motion` 降级，不改现有色板。

6. Verification on 8901
   - `/articles`：搜索、筛选、Pin 成功/失败、文章详情 modal。
   - `/fetch`：加载、单源操作、错误反馈、progress modal Escape/焦点。
   - `/share`：生成入口、队列失败、输出入口。
   - `/config`：编辑、保存、删除确认、错误恢复。
   - `node --check moni/web/static/js/app.js toast.js modal.js`。
   - `/usr/local/bin/python3 -m pytest tests/`，提交边界只跑一次全套。

7. Documentation and PR
   - 更新 `STATUS.md`、`USER_GUIDE.md`。
   - 本地更新 `vibe-coding-log.md`、`HANDOFF.md` 和 coder `vibe-log.md`。
   - feature branch：`feature/v1.0.13-dialog-certainty`。
   - commit → push → `gh pr create`；dad 视觉确认后才 merge。
   - merge 后验证 remote main，再 tag、重启 8900、curl 实际页面。

**Alternative considered and rejected**：本 Sprint 同时重做表格、顶栏、键盘和全部 modal。拒绝原因是它会把三个不同风险等级的方向塞进一个 PR，无法视觉验收和安全回滚。
