# Sprint 48 — bank_feed 可靠性：瞬时重试 / 部分成功 / 官网确定性解析（v1.0.88）

## 背景（2026-11-16 生产实测）

用户在 web 端点「抓取 bank_feed」，弹窗显示 **失败 · 已抓 0 篇 · 0/0**，
底部红条 `feed 不完整: 1 个目标失败 (psbc-news)`。

复核生产库后的事实：

- `articles` 里当天 bank_feed **实际入库 19 篇**（中行 12 / 建行 5 / 一财 2）；
  只是 job 被记成 `failed`，前端又只读 `articles_so_far`（恒为 0）。
- 根因是 `psbc-news` 的 agent 调用退出码 1：`Error: session_replay_gap`
  —— hermes 网关事件流断开后无法回放（瞬时故障）。11-13 同一错误落在
  `boc-news` 上，那天邮储抓到 10 条，**不是站点封禁**。
- 第二次点击 0.06s 失败：`retry_after_min: 30` 冷却期内复用那份不完整
  feed，不会重跑失败目标。

## 目标

| # | 交付 | 验收 |
|---|---|---|
| P1 | agent 瞬时失败（`session_replay_gap` / 超时 / 连接断）自动重试 1 次，只重跑失败目标 | 单测：首次抛瞬时错误、二次成功 → items 正常、errors 为空 |
| P2 | 目标级失败但已入库 → job 记 `partial`，前端显示「已入库 N 篇 · 1 个目标失败」，`articles_so_far` 有真实值 | 单测 + 8901 DOM 实测 |
| P3 | boc / abchina / ccb / psbc 四个官网改确定性解析，失败自动回退 agent；工行保持 agent | 实抓 4 站产出条目，工行走 agent，回退路径有单测 |

## 非目标

- 不改 `peer-banks` 关键词集合，不改 tag 闸门规则。
- 不新增调度：`batch_enabled: false` 保持，仍由 web 端点击触发。
- 不引入新依赖（复用 httpx + 现有 `extract_article_text`）。

## 设计要点

### P1 重试

`scripts/bank_bot/run_bot.py` 新增 `call_agent_with_retry()`：仅对**可判定的
瞬时错误**（`session_replay_gap`、`timed out`、`Connection reset`、
`Broken pipe`、`ECONNRESET`、`EOF occurred`、`RemoteProtocolError`）重试，
共 2 次尝试，间隔 5s。非瞬时错误（如 4xx / 解析失败）不重试。

每目标本来就是一次独立调用，重试天然只覆盖失败目标。

### P2 部分成功

- `FetcherBankFeed.run()`：`inserted > 0` 时把目标失败写进
  `result["warning"]`，不再写 `result["error"]`；`inserted == 0` 仍写
  `error`（fail-closed，红线不变）。
- `FetchJobStore.mark_partial()`：`status='partial'` + `result_json` + `error`
  （存 warning 文本）。
- `/api/fetch/sources/{name}/run` 与批次循环：`warning` → `partial`。
- `get_batch()` 聚合：`partial` 计入 `done_sources` 与 `failed_sources`
  两栏，但批次整体状态为 `partial` 而非 `failed`。
- 前端单任务终态列表补 `partial`，标签「部分完成」，完成摘要给出
  「已入库 N 篇 · N 个目标失败」。
- `FetcherBankFeed` 上报 `progress_callback`：`total_feeds` =
  本轮待导入条数，`done_feeds` / `articles_so_far` 在入库后回写。
- 判定用「今天 feed 是否有条目」而不是「本轮新插几条」：同一天点第二次
  时 `inserted` 必然是 0，但库里已有数据，仍应记 `partial`。
- 手动点击（`base.force`）跳过 `retry_after_min` 冷却 —— 用户点「立即抓取」
  就是要立刻补跑失败目标；冷却只约束非手动路径。

### P3 确定性解析

- 目标配置新增可选字段 `"static": true|false`（缺省 false，显式开启）。
- `parse_static_list()`：扫描 `<a href>`，从 URL 取日期
  （`t20261112_` / `/2026-11/16/`），同域、去重、按日期倒序，
  标题过滤招标 / 中标 / 采购 / 招聘 / 竞价 / 牌价。
- `collect_official_static()`：列表页解析 + 详情页正文
  （复用 `fetch_detail_text`，取不到就用标题兜底）。
- 静态解析失败（0 条 / HTTP 错误）→ 自动回退 agent，并在 feed
  `fallbacks` 里留痕；`--no-agent` 时不回退、直接记错。
- `config/bank_targets.json`：boc / abc / ccb / psbc 标 `static: true`，
  icbc 保持 `static: false`（列表是 JS 渲染，实测 curl 拿不到条目）。

## 风险

- 静态解析依赖站点结构，改版需更新规则；失败自动回退 agent 兜底。
- `partial` 是新状态值，需覆盖 batch 聚合、前端终态、cron 日志三条路径。

## 验收命令

```bash
/usr/local/bin/python3 -m pytest tests/ -p no:cacheprovider -q
/usr/local/bin/python3 scripts/bank_bot/run_bot.py \
  --output-dir /private/tmp/moni-v1088/data/bank_feed --date 2026-11-16
```
