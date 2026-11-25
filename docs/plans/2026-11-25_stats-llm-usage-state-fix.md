# 量化看板 LLM 用量卡片修复 + verify-moni 验证 skill（Sprint 75）

- 日期：2026-11-25
- 分支：`feature/v1.0.130-llm-usage-state-fix`（基于 `f838ae0`，已合入 main `11dc915`）
- 类型：前端状态修复 + 验证基建，无 schema / 无 API 变更

## 1. 要修的是什么

两个现象，同一个根因：

1. **量化看板「LLM 用量」卡片永远 0**：`GET /api/stats/llm-usage?period=30d` 返回
   `totals.calls=57`、`input_tokens=4,073,676`，页面卡片却显示「0 次调用」。
2. **任意页面停留即出 9 条 console error**：6 条 `reading 'totals'` + 3 条 `reading 'by_model'`。

## 2. 根因

同名状态在两个 Alpine 模块里被定义，后定义覆盖前定义：

| 位置 | 定义 | 数据源 | 结构 |
|---|---|---|---|
| `moni/web/static/js/stats.js:50` | `llmUsage` | `/api/stats/llm-usage` | `{totals, by_model}` |
| `moni/web/static/js/app.js:7047` | `llmUsage: null` | `/api/llm/usage` | `{summary, by_module, by_model, by_date}` |

`app.js:158` 用 `...statsModule()` 展开，随后 `app.js:7047` 的 `llmUsage: null` 覆盖对象初值，
`app.js:7109` 的 `loadLlmUsage()` 覆盖 `stats.js:155` 同名方法。于是 `/stats` 的卡片拿到的
是 `/api/llm/usage` 的载荷 —— 没有 `totals`，恒渲染 0；首屏 `llmUsage === null` 时模板
`llmUsage.totals?.calls`（可选链在 `totals` 之后）直接抛错。

引入 commit：`3be0a25`（v1.0.32 加卡片时未带前置 `?.`），冲突在 `/config → 大模型设置 → 用量`
引入同名字段后生效。本次上线 diff 未触碰这两个文件，属既有 bug。

## 3. 修法

- `stats.js`：`llmUsage` → `statsLlmUsage`，`loadLlmUsage()` → `loadStatsLlmUsage()`（4 处）。
- `index.html:2803-2834`：9 处改指向 `statsLlmUsage`，并补前置 `?.`。
- `app.js` 不动：`llmUsage: null` 是 `/config` 用量页「未加载」空态（`index.html:3451 x-show="!llmUsage"`）的语义依赖。
- `tests/test_llm_usage_counters.py:442-450`：断言改为新名字，并把「为什么不能同名」写进 docstring。

## 4. 验收

- 全量 `pytest`：0 failed。
- Playwright（真实 Chromium，8901）：12 个 SPA 路由 `console_real=0`；`/stats` 卡片文本与
  `/api/stats/llm-usage` 的 `totals.calls` 一致（实测 59 = 59）。
- 对照生产 8900（未修复）：卡片「0 次调用」+ 9 条 llmUsage 报错，证明确为修复而非环境差异。
- 真模型三段链路各 1 次（已授权）：摘要 15.6 s / 204 字；联网核查 238 s / 14 条证据 /
  `fact_check_method=web_search`；分享简报 145 s / `status=done` / 2016 字节 markdown。

## 5. 边界与风险

- 只改前端状态名与模板取值，无 API / schema / 迁移改动；回滚为单 commit revert。
- `/config` 用量页保持原行为（仍读 `/api/llm/usage`），两处状态不再互相污染。
- 已发现但**不在本 PR 范围**的既有问题（生产可复现）：
  - `/config → 用量统计` 点击后 7 条 `reading 'after'`（Alpine `x-for` key 问题）。
  - `/basket` 2 条 `422` 资源错误。
  两者在 8900 上同样存在，另行跟进。
