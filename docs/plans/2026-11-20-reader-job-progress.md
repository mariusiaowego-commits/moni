# 阅读器任务状态与两层真进度（sprint-63）

> 过程记录（PRD / TECH SPEC / TEST PLAN / VERIFY）在 Obsidian
> `05-Coding/project-moni/sprints/sprint-63-reader-job-progress-2026-11-20/`。

## 问题

1. 点「重新联网核查」→ 关弹窗 → 重开退回旧结论，任务其实还在跑或已失败。
2. 停留弹窗只有「核查中」，没有进度。
3. 弹窗打开时滚轮还能滚背后页面（16 个浮层全部漏锁）。

## 方案

- **滚动锁**：`modal.js` 改为按「是否有可见浮层」统一算一次，MutationObserver
  监听 `style/class`，`monoModal.open/close/confirm` 与 Alpine `x-show` 共用同一写者。
- **心跳**：`llm_jobs` 运行中每 30s 续约，join 早于 `leases.finish()`；补 4 处
  `_mark_failed(handle=)`；`list_jobs` 加二级排序。
- **状态机**：等待态从 `articleDetail` 剥离为 slot 级 `readerJobState`
  （`fact_check` 与 `fact_check_plus` 同槽），重开弹窗由服务端一次查询重建；
  终态含 `stale`；仅 `:disabled` 不改按钮可见性；去掉 `enqueueLlmJob` 的跳转。
- **两层进度**：第一层 = 排队位置 / 领取 / 心跳 / 已用时 / 终态 / 历史区间；
  第二层 = `hermes --format stream-json` 的 `tool_use/tool_result` → 检索次数、
  最近查询词、单次耗时、是否失败（进程内内存，无迁移）。

## PR 顺序

1. 滚动锁
2. 心跳 + handle + 二级排序
3. 状态机 + 第一层进度 + 去跳转
4. 流式第二层

## 验收

见 `tests/test_frontend_modal_scroll_lock.py`、`test_llm_jobs_contract.py`、
`test_job_view_node.py`、`test_llm_stream.py`、`test_llm_job_invariants.py`、
`test_reader_progress_e2e.py`、`test_llm_jobs_heartbeat.py`、`test_llm_jobs_duration.py`。
