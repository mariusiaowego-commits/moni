# moni Frontend Review — Audit Draft

> AI Agent (Hermes) 生成；等待 MoA synthesis 与 dad 拍板，尚未进入开发。

## Review coverage

已对真实 8900 页面进行四轮浏览器审查：

1. `/articles`：阅读层级、筛选、Pin、表格密度
2. `/fetch`：抓取操作、运行状态、URL 展示、操作安全
3. `/share`：Pin → 简报 → 队列 → 产物的价值链
4. `/config`：配置扫描、标签密度、删除风险、术语一致性

源码证据范围：

- `moni/web/static/index.html`：2355 行
- `moni/web/static/css/main.css`：1209 行
- `moni/web/static/js/app.js`：3612 行
- `articles.js` / `fetch.js` / `share.js` / `cron.js` 仍为占位文件

## Consolidated findings

### P0 addendum from completed MoA audit

- Article detail, the primary reading modal, has no Escape handler or dialog semantics (`index.html:1637`).
- The service restart control is disabled when the service is stopped, making the in-app recovery action unreachable (`index.html:42-44`, `app.js:86-111`). This is a real UX deadlock but may require backend/service-policy review, so it is not silently folded into the frontend Sprint.
- Article source identifiers wrap because column widths are content-driven; multi-tag rows vary from roughly 73px to 101px.
- Filter changes call `loadArticles()` instead of the existing page-reset helper, so filtering from later pages can produce a false empty state (`index.html:71,79`, `app.js:963-966`).
- Long-running poll/animation timers lack one common teardown contract; the share poll persists after leaving the view and fetch-stage source initialization can retry without a cap.

### S1 — 产品价值链被九个平级导航项打散（P1）

用户核心路径应为“看文章 → Pin → 生成简报 → 打开产物”，但当前顶栏把文章、CRUD、日志、抓取、LLM、分享、菜篮子、配置、LLM 设置全部平铺。主要工作与管理工具没有层级差异。

### S2 — 所有页面都使用重型数据表，阅读与管理没有区分（P1）

- `/articles` 首屏是 62 行表格，标题、fresh/new、tags、来源、双时间列争夺注意力。
- `/fetch` 和 `/config` 适合表格，但状态、URL、标签和操作都在同一视觉权重。
- `/share` 直接展示任务 ID、英文格式名、raw `done/failed` 和底层错误，用户需要自行翻译系统状态。

### S3 — 交互确定性不足（P1）

- 源码约 42 个 `alert()` / `confirm()` 调用，反馈模式分裂。
- 12 个 modal 中 Escape 与焦点管理不一致。
- 错误、空态、加载态和可重试动作缺少统一语法。
- `/articles` 同一筛选栏中搜索框约 35px，高度与两个约 27px 的 select 不一致。

### S4 — Design token 有辨识度，但组件层没有完全收敛（P2）

中国传统色 OKLCH 主题值得保留；问题不是“换皮”，而是组件节奏不统一：圆角、控制高度、阴影、状态色、纵向间距和 inline style 漂移。

### S5 — 前端单体结构放大了视觉漂移风险（P2）

`app.js` 与 `index.html` 已超过 6000 行，预留模块仍为空。但本 Sprint 不应迁框架，也不应先做大规模模块化；应只抽本次触及的通用 UI primitives，避免把视觉 Sprint 变成架构 Sprint。

## Preliminary solution boundary

推荐把首个 Sprint 限定为“核心消费路径精修”，而不是全站重做：

1. 重建文章页视觉层级与稳定行高
2. 收敛筛选、状态、标签、操作和分页组件
3. 让 Pin → 分享的下一步更明确
4. 建立统一 toast / empty / loading / error 语法，仅迁移本 Sprint 触及路径
5. 保留现有主题、后端/API/schema、抓取粒子 modal

明确非目标：

- 不迁 React/Vue
- 不改数据库或 API contract
- 不重做所有九个页面
- 不在本 Sprint 拆完 3612 行 app.js
- 不引入新依赖

## Blocking boundary

当前 `main` 有与本任务无关的未提交修改：`moni/db/seed_config.py`。UI Sprint 必须保持该文件不变、不提交、不清理。

## Pending

等待 MoA synthesis reference 给出 3 个设计方向、风险和推荐方案后，形成正式 Sprint PLAN + 3–4 个拍板问题。
