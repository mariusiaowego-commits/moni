# Current Triage — 2026-10-23

> ## ⚠️ 本文件的修正记录（2026-10-23 二次复核后）
>
> 这份 triage 本身被**独立审计过一遍**，发现 17 处事实错误。已全部修正。
> 最要紧的一条：
>
> **P1-11 原本被判成 "Not reproduced / 不适用"，那个判断是错的。**
> 判错的原因：当时拿 `config_sources.py::update_source` 当证据（那是按 id 改一行的**正确**实现），
> 漏看了 `moni/web/routes/fetch.py::update_fetch_sources` —— 那里才是把请求体
> **应用到所有源**并重写整个 `config/sources.yaml` 的那颗地雷。
>
> 其余修正：基线数字（当时写 1018 / 8 failed，实际是 **1037 / 8 failed**）、
> "立即队列第 1 项其实已经完成"、README 的工单计数、P3-19 的关闭依据、
> v1.0.46 根因叙述与 #145 实际 diff 不符、攻击向量数量三处不一致、
> 证据 hash 不可复现、README 未索引两份大报告、USER_GUIDE 引用了从不存在的 v1.0.43、
> 24 处 `file:///Users/mt16/...` 链接在 GitHub 上全部失效、"进行中"段在合并后立刻变假、
> "1045 passed" 的 HOME 敏感性。

Read-only re-verification of the 2026-09-06 audit against `main` @
`40c14b2` (v1.0.45), plus the leftovers found while cleaning branches.
**复核基线后来推进到 `0f8c056` (v1.0.46) —— 下表"是否还在"一列按 0f8c056 判定。**

This file answers three questions per item: does it still exist on current
`main`, how much value does it have, and what should happen next.

## Test baseline (measured, not claimed)

### 审计日当时测的（`40c14b2`, v1.0.45）

- `pytest tests/` -> **1037 passed / 8 failed / 2 skipped**。
  （旧版这份文件写的是 "1018 passed / 8 failed"，**少了 19**。1037 与同期的
  `STATUS.md` 记录完全一致。）
- 那 8 个失败是 v1.0.42/44/45 时代已记录的一组**测试隔离**问题：
  `test_api` 的 share 用例、`test_fetch_integration::test_dry_run_skips_insert`、
  `test_prompts::test_call_llm_logs_to_file`，以及两个 `test_share_manifest` 用例。
- `test_api.py` 单独跑 93 passed；`test_share_manifest.py` 单独跑 45 passed
  —— 所以是**隔离问题**，不是产品回归。
- `test_prompts::test_call_llm_logs_to_file` 是真的过期 mock：它 patch 的是
  `moni.llm.client.subprocess.run`，但 v1.0.42 已经把子进程执行挪到
  `moni/llm/runner.py`。

### 现在的基线（`0f8c056`, v1.0.46）

- `pytest tests/` -> **1045 passed / 0 failed / 2 skipped**（0f8c056 = PR #145，
  正是把那 8 个隔离失败修掉的那次提交）。

> ### ⚠️ 这个数字是 **HOME 敏感**的
>
> 用**干净的 HOME**（`HOME=/private/tmp/xxx`）跑是 1045 passed。
> 用**真实 HOME** 跑会因为 `~/.moni/queue` 里堆积的 8627 个历史 manifest
> 变成 **1022 passed / 23 failed** —— 失败的是队列相关用例，不是产品坏了。
>
> 引用这个数字时**必须带 HOME 条件**，否则会误导人。

## Status of audit tickets on current main (0f8c056)

| Ticket | Still present? | Evidence | Value | Next |
|---|---|---|---|---|
| P0-1 predict_tags on dead provider | **Fixed (本轮)** | `cron/jobs.py` 原本调 `ChatClient`（v1.0.42 弃用壳，`.chat()` 丢弃 kwargs）；PR #146 换成 `resolve_active_model()` + `call_llm()` | High —— 审计判定"从未真正产出过一个标签"，SQL 查了不存在的 `content` 列，每次运行直接崩 | 已修（v1.0.47） |
| P0-2 SPA path traversal | **Fixed** | `moni/web/server.py::resolve_static_path`, 31 tests | High, already shipped | None |
| P0-3 fetch_jobs no heartbeat | **Fixed** | `moni/db/leases.py`, migration 034, tests | High, already shipped | None |
| P0-4 orphan hermes child | **Fixed** | `moni/llm/runner.py` killpg | High, already shipped | None |
| P0-5 scheduler absent | **Present** | 无 `.cron.pid`；launchd plist 未安装；仓库里 `KeepAlive=true` | **High** | **暂不做** —— 用户 2026-10-23 拍板：手动为主。方向 = **独立 launchd 常驻进程，不嵌入 Web**（与 `SUCCESSION-PLAN.md` 主张的"web 内嵌"相反，**以用户决策为准**） |
| P1-5 no failover/breaker | **Fixed** | `runner.py` CircuitBreaker/FailoverRunner | High, already shipped | None |
| P1-6 prompt via argv | **Fixed** | stdin transport in `runner.py` | High, already shipped | None |
| P1-7 llm_jobs claim race | **Fixed** | leases + atomic claim | High, already shipped | None |
| P1-8 predict_tags no mutex | **Fixed (本轮)** | 原本无 per-job 锁 | Medium | 已修（v1.0.47，与 P0-1 同 PR） |
| P1-9 dynamic href no scheme allow-list | **Fixed (本轮)** | 11 个 `:href` sink，无白名单 | **Medium-high**：LLM/抓取内容能注入 `javascript:` | 已修（v1.0.48，`safeHref()` + 全 sink 扫描测试） |
| P1-10 RSS JWT leaked | **Fixed** | `moni/web/redact.py`, 34 tests | High, already shipped | None |
| P1-11 PUT source fans out | **Present** ⚠️ | **`moni/web/routes/fetch.py::update_fetch_sources`**：`for cfg in sources:` 把请求体字段应用到**所有**源，然后 `yaml.safe_dump` 重写整个 `config/sources.yaml`；`FetchSourceUpdate` 里**没有任何字段能指明改哪一个源** | **High** —— 一颗一触即发的地雷：一次 PUT 把所有源改名成同一个名字并重写生产配置。而且 `tests/test_api.py` 里那个用例**每次跑测试都会真的重写生产配置文件**（当时所有源 `crawler_type` 恰好都是 `rss` 才没炸） | 已修（v1.0.51，PR #151：删端点 + 加 `tests/conftest.py` 护栏） |
| P1-12 running-job alerts / broad except | **Partially present** | leases 给了可检测性；没有告警通道 | Medium | 延后（需要先定告警通道） |
| P2-13 polling lifecycle | **Mostly fixed** | teardown 已存在（`app.js` share/fetch/worker 轮询） | Low-medium | 只审计剩余 timer |
| P2-14 CDN no SRI / no vendored copy | **Fixed (本轮)** | 原本 `index.html` 从 jsdelivr 载 Alpine + GSAP，无 integrity | **High（离线/demo 场景）** | 已修（v1.0.48，本地 vendor） |
| P2-15 modal title innerHTML | **Fixed** | `modal.js` 转义 title/按钮 | Medium, shipped | None |
| P2-16 no CI | **Fixed (本轮)** | 原本无 `.github/workflows` | Medium | 已修（v1.0.50，GitHub Actions） |
| P2-17 ad-hoc f-string SQL | **Present** | 只在表名插值处 | Low | 延后 |
| P3-18 llm imports web | **Present** | `moni/llm/jobs.py` import `moni.web.routes.fact_check_plus` | Low-medium | 延后 —— 用户 2026-10-23 拍板：无用户可见价值 |
| P3-19 connection comments/retired fd | **Mostly fixed —— 不能 Close** | thread-local + 真实 `close()`（v1.0.37.6） | Low | ⚠️ 工单点名的"**注释即谎言**"还在：`moni/llm/jobs.py:29` 仍宣称 `check_same_thread=False`，而 `moni/db/connection.py:29` 明说默认是 True。**注释要改，票才能关** |
| P3-20 worker lock check-then-act | **Fixed (本轮)** | 原本 `share/worker.py` 先查锁再 Popen | Medium | 已修（v1.0.49，原子锁 + 如实上报） |

## 本轮 PR 映射（截至 2026-10-23，均已独立审计）

| 工单 | PR | 版本 | 状态 |
|---|---|---|---|
| P0-1 + P1-8 | #146 | v1.0.47 | 审计 APPROVE，F1/F2 已修 |
| P1-9 + P2-14 | #147 | v1.0.48 | 审计 APPROVE，条件项已修 |
| P3-20 | #148 | v1.0.49 | 首审 REJECT，F1~F6 已修 |
| P2-16 | #149 | v1.0.50 | 审计 APPROVE，P2-1/P2-2 已修 |
| P1-11 | #151 | v1.0.51 | 本轮新增 |

## Immediate queue — 当前状态

> **原第 1 项"修 8 个 flaky 测试"其实已经做完了** —— 就是 PR #145 = `0f8c056`
> = 本包的基线本身。旧版把"已完成的事"排在"立即队列第 1 项"，会让人重复劳动。

1. ~~**修 8 个 flaky 测试**~~ —— **已完成**（PR #145 / v1.0.46）
2. **P0-5 调度器** —— **暂不做**（用户拍板手动为主；方向 = 独立 launchd 常驻，不嵌入 Web）
3. ~~**P1-9 safeHref**~~ —— 已修（v1.0.48）
4. ~~**P2-14 自托管 Alpine/GSAP**~~ —— 已修（v1.0.48）
5. ~~**P0-1/P1-8 迁移 predict_tags**~~ —— 已修（v1.0.47）
6. ~~**P3-20 原子 worker 锁**~~ —— 已修（v1.0.49）
7. ~~**P2-16 CI**~~ —— 已修（v1.0.50）
8. **P1-11 扇出地雷** —— 已修（v1.0.51，本轮新增 PR）
9. **P3-19 剩下那句假注释** —— 仍未做（低价值，但注释是谎言就该改）

## Deliberately not doing now

- 重写 `P2-13` 全量：多数 timer 已有 teardown。
- `P3-18` 分层重构：无用户可见价值，触及大量 import。
- `P1-12` 告警：需要先定运维通道。
- `P2-17` f-string SQL：只表名插值，SQL 已参数化。

## 一条还没做的已知问题（休眠）

`moni/llm/runner.py` 的 fallback chain：**chain 非空时，runner 用 chain 里的 model
去构造 `-m`，但 `--provider` 仍取请求里的 provider** —— 可能出现
`hermes --provider deepseek -m ark-code-latest` 这种**跨家错配**。

当前生产 DB 里 `fallback_chain` 实测是**空字符串**，所以是休眠问题。
一旦有人给 model 配了 fallback chain，就会踩到。建议单开一个小 PR 修
（涉及共享 LLM 层，不宜搭车）。
