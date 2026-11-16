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

## 第 2 轮：独立审计 → BLOCK → 修复

独立只读审计（HEAD `7160999`，merge-base `d7e6680`）结论 **BLOCK**：1 个 P1 + 5 个 P2。

### P1（已修）

静态解析失败 → 回退 agent → agent 正常返回 `[]`：该目标只写进 `fallbacks`，
`bank_feed._feed_is_fresh()` 只看 `errors`，于是当天 feed 被判「已完整」，
连手动点击也不补跑。修复：`main()` 在回退目标既无产出、又不在 `errors` 里时补
`{"target": id, "error": "agent returned no items after static fallback"}`。

### P2（已修）

1. 详情页失败/空正文用标题顶上仍算静态成功 → 整站无正文时抛 `TargetError`
   回退 agent；详情页解码改走 `_decode_html`（UTF-8 → GB18030）。
2. 列表只按日期截 `max_items`、无时效窗口 → 新增 `max_age_days`
   （默认 60 天，0 = 关闭）；窗口内 0 条返回空列表，不算解析失败。
3. `_report_progress` 吞掉 `TimeoutError` → 重新抛出；`base.run()` 让它穿透到
   web 端 timeout 收尾。
4. `get_batch()` 把 partial 同时放进 `results` 和 `errors` → 只进 `results`
   （带 `warning`）；前端补 `.fa-row-warn` / `.fa-warn`。
5. `moni.fetch.run()` 的 CLI `force` 没落到 `fetcher.force` → 已赋值。
   另：批量 `cron_runs.success` 在只有 partial 时也记 0。

### 复测

- 全量测试 **1773 passed / 2 skipped**；沙箱内
  `test_migration_039_xbot_feed` 因无写 `.git` 权限失败，沙箱外单跑 **1 passed**。
- bot `--no-agent`：**26 条 / 0 error**（boc 12 / abc 1 / ccb 7 / psbc 3 / 一财 3），
  最旧 2026-09-24；工行 agent：**6 条 / 136s**，首次 `session_replay_gap` 重试命中。
- 批量面板 DOM：2 行、source 唯一、`fa-row-warn` + warning 文本可见。

### 第 2 轮独立审计

结论 **APPROVE WITH NITS**：P1 与 5 个 P2 全部确认修掉，无新 P1。Nits 已在同一
PR 补齐：

- P1 的 `BotError` / 只补回部分目标 / `--no-agent` 三条分支各补一个用例
  （`test_main_marks_each_fallback_target_when_agent_call_fails` /
  `..._marks_only_the_missing_fallback_target` /
  `..._no_agent_marks_static_failure_as_error`）。
- `filter_recent(max_age_days=0)` 不过滤。
- 批量只有 partial 时 `cron_runs.success=0`
  （`test_partial_batch_is_not_recorded_as_success`）。

未处理（旧行为，非本 PR 范围）：`xwatch` / `bestblogs` /
`chinese_independent_blogs` 的进度回调仍 `except Exception: pass`。
