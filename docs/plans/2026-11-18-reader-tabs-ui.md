# 阅读器页签化 + 核查结论渲染（v1.0.102）

> Sprint 57（`sprint_id: 26111803`）· 分支 `feature/v1.0.102-reader-tabs`
> 触发：dad 2026-11-18 在生产 8900 提的 6 条 UI 反馈（4 张截图 + 1 段录屏）
> Obsidian 双写：`sprints/sprint-57-reader-tabs-2026-11-18/`

## 一、问题与根因（只读排查，base = `c55c6b1`）

| # | 反馈 | 根因 | 证据 |
|---|------|------|------|
| 1 | 摘要/核查结论没有格式渲染 | Alpine `x-text` 只塞纯文本 | `index.html:3267` `x-text="articleDetail?.summary"`、`index.html:3275` `x-text="articleDetail?.fact_check_conclusion"`；生产库 `fact_check_conclusion` 非空 80 篇、含 markdown 20 篇、最长 1549 字 |
| 2 | 摘要/核查应在标题下方做页签 | 卡片写在 `.reader-scroll` 正文末尾 | `index.html:3265-3297`；`fact_check_summary`（#236 新增的人话结论列）前端 **0 命中**，从未渲染过 |
| 3 | 联网核查报错露底 | 后端原文直接 toast | `app.js:4932` `'联网核查失败: ' + (job.error \|\| 'unknown')` |
| 5 | 放大按钮不是全屏 | 只改弹窗尺寸 | `main.css:4875` `width: calc(100vw - 24px)` |
| 6 | 窄屏「对话」是盖上去 | AI 面板绝对定位覆盖正文 | `main.css:5241-5244` `position: absolute`；`main.css:5251-5252` `visibility: hidden`（仍占位） |
| 4 | AI 流式输出闪烁 | 每 12ms 重绘整段 markdown | `reader.js:825` `readerPushDelta` 12ms/3 字 + `x-html` 整段 innerHTML 重绘；`reader.js:883` 每次强制吸底 |

第 4 条不在本 PR（另开 `feature/v1.0.102-reader-stream`），理由：它要动 Alpine 绑定方式与渲染管线，与布局改动混在一起不好审、不好回滚。

## 二、方案

**页签结构**：`.reader-split` 改两行网格 —— 行 1 是页签行（`grid-column: 1 / -1`），行 2 是正文栏 + AI 栏。

- 宽屏（≥1101px）：页签行 = `正文 | 摘要 | 事实核查`，AI 常驻右栏，「对话」页签隐藏。
- 窄屏（≤1100px）：页签行 = `正文 | 摘要 | 事实核查 | 对话`；AI 面板不再是绝对定位，改为与正文栏**同格叠放**，用 `opacity + translateX` 220ms 切换。
- `readerContentTab`（正文/摘要/事实核查）与 `readerMobileTab`（窄屏整屏）正交；切内容页签会把 `readerMobileTab` 复位为 `read`。

**事实核查页签内容**：档位徽标 → 档位说明（两档差异写给用户看）→ 人话结论（`fact_check_summary`）→ 核查详情（结论段直出 + 逐条核查折叠）→ 文章主张/核查方式/核查时间 → 证据链。

**markdown**：复用既有 `readerRenderMarkdown`（marked + DOMPurify + ALLOWED_TAGS）。核查结论改用新增的 `readerRenderMarkdownPlain` —— 同样过 sanitize，只是不挂正文引用 chip（核查文里的「」不是文章引文）。

**全屏**：`is-expanded` 改 `position: fixed; inset: 0; 100vw/100vh; border-radius: 0`，按钮图标与文案切「全屏 ↔ 还原」，Esc 先退全屏再关弹窗。

**错误文案**：前端映射（熔断 / 会话异常 / 超时 / 解析失败 → 人话），原文收进 toast 的「查看原因」，「复制错误」复制的是原文。后端契约不动。

**顺手补的坑**（同属这批 UI 的直接后果，不算扩围）：

- 打开文章时加载证据链 —— 此前只有「核查任务跑完的那一次会话」才加载，直接打开文章证据恒为 0 条。
- 迟到/失败的核查结果按 `articleId` 守卫，不再把用户正在看的另一篇盖掉。

## 三、改动清单

| 文件 | 改动 |
|------|------|
| `moni/web/static/index.html` | 页签行、三个内容面板、全屏按钮与图标、Esc 处理、窄屏 resize 钩子 |
| `moni/web/static/css/main.css` | `.reader-split` 两行网格、`.reader-tabs`、面板样式、窄屏同格切换动画、真全屏、toast 详情样式 |
| `moni/web/static/js/reader.js` | `readerContentTab` / `readerIsNarrow` / `readerAiFocused`、切页签方法、`readerFactSplit` getter、`readerRenderMarkdownPlain`、Esc 处理、打开文章加载证据链 |
| `moni/web/static/js/app.js` | `_llmErrorText` 映射、失败/成功回刷的 `articleId` 守卫 |
| `moni/web/static/js/toast.js` | `opts.detail`：「查看原因」折叠 + 复制错误复制原文 |
| `tests/test_frontend_reader_tabs.py` | 新增：钉住 9 个失败模式 |
| `tests/test_frontend_safe_href_and_vendor.py` | x-html 白名单新增 `readerRenderMarkdownPlain` |

**不改**：DB schema（无新迁移）、后端 API 契约、事实核查提示词与闸门、share 链路。

## 四、验收证据

**UI 实测**（Playwright + Chromium，视口尺寸见各项）

| 项 | 结果 |
|----|------|
| 宽屏 1440×900 页签行 | `tabBarTop=169.8` > `headBottom=158.0`（在标题下方）；「对话」页签 `display: none` |
| 宽屏 事实核查 markdown | `h2=3, strong=8, li=20`（渲染生效，非纯文本） |
| 长报告折叠 | 预览段直出（`核查结论 / 可信度：中…`），「展开逐条核查」后 `chars=980, h2=2, li=20, strong=7` |
| 联网核查档 | 徽标 `联网核查 · 有出处`、人话结论直出、证据链 `7 条证据`（修复前 0 条） |
| 全屏 | `position=fixed, inset=0 0 0 0, rect=1440×900, radius=0px, transform=none`；还原后 `rect=1215×776, radius=16px` |
| 窄屏 900×800 切换动画 | 过渡中 `paneOpacity=0.20, paneTransform=matrix(...,-17.29,0)`；结束后 `paneOpacity=0, paneVis=hidden, pointerEvents=none` |
| 窄屏 420×900 页签 | 4 个页签全部可见，任意时刻 `aria-selected=true` 只有 1 个 |
| 错误 toast | 主文案「模型服务正在熔断恢复中…」；「查看原因」内 `RuntimeError: hermes failed (failover exhausted): __profile__: breaker OPEN, reset in 600.0s` |

**测试**：定向 97 passed；新增/前端全量 `tests/test_frontend_reader_tabs.py` + 3 个前端测试文件 63 passed；全量见 sprint verify 记录。

## 五、遗留

- 第 4 条（流式闪烁）→ `feature/v1.0.102-reader-stream` 单独 PR。
- 历史 20 篇长 markdown 报告不做数据回填，靠前端「结论直出 + 逐条折叠」处理。
- 迁移号冲突未解决：`feat/content-cleaning`（`048_content_cleaning.sql`）与 `feat/bv-direction-v2`（`046_bv_direction.sql`，`_CURRENT_VERSION=46`）在本 PR 合并时需重新核对，本 PR 不引入迁移。
