---
id: 26111206
type: plan
version: v1.0.76
start_date: 2026-11-12
status: 进行中
sprint: "[[sprint-41-web-ui-dedupe-modal-2026-11-12]]"
---

# Sprint 41 PLAN — 去重与弹窗结构

- 分支 `feat/web-ui-polish`（连续提交，PR #186）
- 实现 agy（w52:p9）· 验收 orchestrator
- 依据：体检报告 UI-03 / UI-04 + unslop Step 5 第 6 遍（Details/Layout）

## 1. Pin 去重

- `articlesSubTab === 'pinned'` 时：`<th class="pin-col-head">` 与 `<td class="pin-col">` 加 `x-show="articlesSubTab === 'all'"`。
- 行操作列在 pinned 态补一个 `取消 Pin`（`.link-btn`，`@click="togglePin(a.id)"`），保证单篇可解。
- 「Pin 时间」列保持不变（pin 视图下才有该列，属有效信息）。

## 2. fresh + new 合并

```html
<span class="badge badge-new" x-show="a.is_fresh || isNew(a.created_at)"
      :title="a.is_fresh ? '本次抓取新增' : '24 小时内抓取'">新</span>
```

删除 `badge-fresh` 那颗。`badge-new` 保持既有样式；不允许两个徽标并排。

## 3. 双刷新

删除 `index.html:835`（`.llm-stats` 内那颗），只留标题栏右侧。

## 4. 文章详情弹窗

- 合并动作条：`生成摘要`(Primary) · `事实核查`(Secondary，`x-show="!articleDetail?.has_fact_check"`) · `联网核查`(Secondary) · `提取案例` · `查看原文` · `复制链接`。
- 删除 `.fact-check-plus-actions` 整块（其中「事实核查」与上面重复，「联网核查」并入上条）。
- 把动作条移到 `modal-body` 末尾（证据链之后），并：
  ```css
  .article-actions {
    position: sticky; bottom: -24px;
    background: var(--card); margin: 0 -24px -24px;
    padding: 12px 24px 16px; border-top: 1px solid var(--border);
    flex-wrap: wrap; gap: 8px;
  }
  ```
  （`.modal-content` 的 padding 是 24px，负 margin 抵消到边）
- 主次顺序：Primary 在最左，其余 Secondary 依次右排；`复制链接` 的「已复制」提示保留。

## 5. z-index

| 位置 | 现值 | 改为 |
|---|---|---|
| `.modal-backdrop` | 100 | `var(--z-modal)` |
| `.toast` / 浮层 | 9999 | `var(--z-toast)` |
| cases 遮罩内联 | 9998 | `var(--z-modal)` |

## 6. Acceptance（orchestrator 执行）

1. `pytest` → ≥1630 passed / 2 skipped。
2. pinned 子 tab 下 `document.querySelectorAll('td.pin-col:not([style*="none"])').length === 0`；all 子 tab 下 > 0。
3. `index.html` 中 `badge-fresh` 出现次数 = 0；标题行 `.badge` 每行 ≤ 1。
4. 详情弹窗：`document.querySelectorAll('.modal-content .btn-primary').length === 1`；`事实核查` 文案出现次数 = 1；`.article-actions` 的 `position` 为 `sticky`。
5. 弹窗截图必须真的打开弹窗：`/private/tmp/ui-audit/after-s41/modal_article_detail.png`（点「查看」后截）+ `modal_delete_confirm.png` + `modal_job_detail.png`。
6. 层叠：`getComputedStyle(.modal-backdrop).zIndex === '500'`，toast 为 `1000`。
7. 改动文件 ⊆ `index.html`、`css/main.css`。
