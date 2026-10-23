# moni 后续 TODO 深度 Review — deepseek-flash

- **日期**: 2026-09-10
- **Reviewer**: deepseek-flash (worker B 泳道候选)
- **对象**: `/Users/mt16/dev/moni` @ branch `exhaustive-mode` = main @ `3a5cc00` (tag `v1.0.41`), working tree 仅 1 个 untracked (`docs/sprint-audit-2026/`)
- **红线**: 全程只读业务代码 + `sqlite3 -readonly` + `git log`；唯一写入 = 本文件
- **基线实测**: `/usr/local/bin/python3 -m pytest tests/ -q -p no:cacheprovider` → **889 passed, 2 skipped, 1 warning in 197.73s** (与 STATUS.md v1.0.41 记载一致)

---

## 0. TL;DR（先看这 6 行）

1. 审计包 (`docs/sprint-audit-2026/`, 2026-09-06) 的 21 张工单，**4 天后 (09-10) 现场复核：0 张落地**，4 个 P0 全部代码级仍然成立。
2. **现场新增 P0-现场-1：prod 8900 未运行**（`curl /health` → 000，进程表无 uvicorn:8900）。停机原因不明，**不要擅自重启**（见 §3.1 风险）。
3. **现场新增 P0-现场-2：ghost sources 回归**——`mp_a883be18` / `mp_a883be18_b` (`is_active=1`, `url=https://example.com/mp_*`) 不在 `sources.yaml`、0 articles / 0 tags / 0 fetch_jobs，`created_at=2026-08-18T07:47:18`，即 **migration 030 上线 (08-17) 的第二天又长出来一批**——同一个 bug 第二次出现，而 030 是一次性 `DELETE WHERE id IN (4 个硬编码 id)`，没有结构性防护。
4. 数据库当前"活性"现场：`llm_jobs` failed 73 / (done 98 + failed 73) = **42.4% 失败率**、1 条 `running` 悬挂；`fetch_jobs` **stale 187 条**；`cron_runs` 里除 `test_job` 外只有 1 条手动 `fetch_all_sources`，**5 个 schedule.yaml job 从未执行**（P0-5 复核成立）。
5. 我的泳道建议（与 MiniMax-M3 的重型后端核 **文件级互斥**）：**安全原语 + 前端生命周期 + 测试隔离护栏** —— 零 DDL、零迁移、零 `moni/llm/*`、零 `moni/fetch/*`。**已对着他 14:43 落盘的 `review-minimax-m3.md` 逐文件对账：两条泳道当前零共享文件**（他主动排除了 P0-2 / P1-9~12，见 §5.3）。
6. 1.5h 只装得下 **1 个 PR**：推荐 **安全原语 PR**（P0-2 + P1-10 + P2-15，约 85 min），P1-9 (safeHref) 视余额决定去留（见 §6 工期表，含超支预警）。

---

## 1. 材料核查清单（本轮实际读了什么）

| 材料 | 状态 | 关键取值 |
|---|---|---|
| `HANDOFF.md` (996 行) | 已读头部 + v1.0.41 段 | 最后 ship = v1.0.41 (09-02, PR #140 @ `023c1e3`) |
| `STATUS.md` (1733 行) | 已读 v1.0.41/40/39/37.6 段 | 主分支 `023c1e3`，prod pid 6346（**已失效**） |
| `AGENTS.md` | 已读全文（项目上下文注入） | 源数 11-12、schema v30/v33、踩坑史 |
| `docs/sprint-audit-2026/BACKLOG.md` | 已读全文 | 21 工单 (P0-1..P0-5, P1-5..P1-12, P2-13..P2-17, P3-18..20) |
| `docs/sprint-audit-2026/SUCCESSION-PLAN.md` | 已读全文 | S1-S4 蓝图 + 4 拍板项 + §6.2 迁移 SOP |
| `docs/sprint-audit-2026/PHASE2-REFACTOR.md` | 已读全文 | LLM-01 / SEC-01 / JOB-01 / JOB-02 四重构图纸 |
| `docs/sprint-audit-2026/README.md` | 已读 | 审计包索引 |
| 代码库 | 复核：`db/` `llm/` `fetch/` `cron/` `web/`（含 routes + static） | 见 §2 逐条证据 |
| `/tmp/sprint-prototypes/` | ❌ **已不存在**（`ls` → No such file） | **4 个"验收基线"原型已丢失**，见 §8 风险 R9 |

---

## 2. 现场复核结果：审计声称 vs 09-10 实测

**复核级别定义**：`代码级` = 直接读了那一行源码；`DB 级` = `sqlite3 -readonly` 查到现场数据；`运行时级` = 对跑着的服务发请求。
**诚实声明**：P0-2 的运行时 PoC **本轮无法复现**，因为 8900 已停机（审计当时是 curl 实测 213B）。我只在代码级确认漏洞路径一字未改，并把复现命令留在 §9。

| 工单 | 审计声称 | 09-10 复核 | 证据 |
|---|---|---|---|
| **P0-1** | 每日打标 cron 打在死亡 provider 上，LLM 参数被静默丢弃 | ✅ 成立（代码级） | `moni/cron/jobs.py:39` `from ..llm.client import ChatClient`；`:343-348` `llm.chat(prompt, model="claude-3-5-sonnet-latest", max_tokens=256, temperature=0.3)`；`moni/llm/client.py:298-303` shim 里 `kwargs.get("provider","minimax-cn")` 硬编码 + `max_tokens/temperature` **不透传** |
| **P0-2** | SPA fallback 绝对路径拼接 → 任意文件读（审计实测） | ✅ 成立（代码级）；运行时待 8900 起 | `moni/web/server.py:122-126` `candidate = STATIC_DIR / full_path` + `candidate.is_file()`，**与审计日完全一致，未改** |
| **P0-3** | fetch_jobs 僵尸判定看 `started_at` 不看心跳 → 重复线程 + 状态互踩 | ✅ 成立（代码级 + DB 级） | `moni/fetch/jobs.py:27-69` `STALE_THRESHOLD_SECONDS=900` + `get_stale_threshold()=max(900, max_timeout+300)`（v1.0.35.17 补丁仍在）；DB `fetch_jobs` status=**stale 187** |
| **P0-4** | hermes 子进程超时只杀直杀进程，孙进程成孤儿 | ✅ 成立（代码级） | `moni/llm/client.py:174-180` `subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)` — **无 `start_new_session` / 无 `killpg`**；`moni/share/worker_llm.py:310` 第二个同型调用点 |
| **P0-5** | 定时任务体系整体缺位，schedule.yaml 是死配置 | ✅ 成立（DB 级 + 进程级） | `cron_runs` 除 `test_job` 外仅 1 条 `fetch_all_sources`（2026-09-01T10:06 手动全量，11 源）；`predict_tags_pending` / `extract_events_pending` **零记录**；进程表无 scheduler |
| **P1-5** | LLM 运行时零 failover、零熔断、重试无退避 | ✅ 成立（代码级 + DB 级） | `moni/llm/jobs.py:73` `MAX_ATTEMPTS=2`；`:234-257` 认领后立即 `_run_job`，无退避；DB 失败率 **42.4%** |
| **P1-6** | prompt 走 argv，超长文章触发 E2BIG 且异常未捕获 | ✅ 成立（代码级） | `moni/llm/client.py:145-149` `cmd = ["hermes","-z",prompt,...]`；`:181-188` 只捕获 `TimeoutExpired` / `FileNotFoundError` |
| **P1-7** | llm_jobs 认领 SELECT→UPDATE 竞态 + `running` 无回收 | ✅ 成立（代码级 + DB 级） | `moni/llm/jobs.py:234-257` 注释自认 "Two workers would race here, but we run exactly one per process"；DB `llm_jobs` **running=1 悬挂**，且全模块无 sweep（对比 `fetch/jobs.py` 有 `sweep_zombies`） |
| **P1-8** | predict_tags 无任务级互斥 + APScheduler 串行长任务挤掉后续触发 | ✅ 成立（代码级） | `moni/cron/jobs.py:297-307` 待打标查询无 claim；`moni/cron/scheduler.py` `max_instances=1, misfire_grace_time=60` |
| **P1-9** | 动态 `:href` 无 scheme 白名单 → `javascript:` stored XSS | ✅ 成立（代码级，量化） | `index.html` 共 **11 处 `:href=`**：`268 / 312 / 614 / 932 / 1233 / 1397 / 2117 / 2824 / 2964 / 3020 / 3073`；其中 **3020 / 3073 是 LLM fact-check evidence URL**（prompt-injection 注入面）；仅 `2117 / 3073` 带 `rel=noopener` |
| **P1-10** | 微信 RSS JWT 明文存 `sources.url` 并原样返回前端 | ✅ 成立（代码级 + DB 级） | DB：`wechat_rss_a` / `wechat_rss_b` `url` 均含 `?token=eyJhbGci…`；`moni/web/routes/config_sources.py:102` `url=r["url"]`（list 出栈），`:187 / :211 / :173` 同型；`moni/llm/redaction.py` 只护 LLM 落库路径，**不覆盖 config API** |
| **P1-11** | `PUT /api/fetch/sources` 单源更新扇出全源 + 非原子写 | ✅ 成立（代码级） | `moni/web/routes/fetch.py:153-163` `for cfg in sources:` 无目标过滤；`:166-167` `config_path.open("w")` 直接覆写（非 tmp+rename） |
| **P1-12** | `except Exception` 吞噬 + 无 CI 无 metrics | ✅ 成立（代码级 + DB 级） | 本轮复跑 pytest 复现一条**daemon 线程泄漏**告警：`_run_fetch_job` 线程在测试 tmp-DB 拆除后仍写 `fetch_jobs` → `no such table`（P0-3 的线程逃逸在测试里现场可见） |
| **P2-13** | 轮询生命周期与详情竞态四连 | ✅ 成立（代码级） | `app.js:4296-4303` `loadLlmJobs` 自续 `setTimeout` 无 teardown；`app.js:186-201` `restartService` 的 `setInterval` 句柄闭包私有；`app.js:3723/4227` `articleEvidenceList` 换文章不重置；全库 **`AbortController` 出现 0 次** |
| **P2-14** | CDN Alpine/GSAP 无 SRI、无 vendored 副本 | ✅ 成立（代码级） | `index.html:19` `cdn.jsdelivr.net/npm/alpinejs@3.14.1`（`<link rel=preload>`，无 `integrity`）；`:20-22` Google Fonts；`moni/web/static/js/` 下**无 `vendor/`**（仅 app/calendar/cases/icons/modal/pin_anim/stats/theme/toast） |
| **P2-15** | 共享 confirm modal 的 title 走 innerHTML 未转义 | ✅ 成立（代码级） | `moni/web/static/js/modal.js:238` `${opts.title \|\| '请确认'}` 直插 `wrap.innerHTML`；注意 **message 已转义**（`:234` `safeMsg`），只有 title / 按钮文案裸奔 |
| **P2-16** | 无 CI，889 tests 只活在开发者本机 | ✅ 成立 | `.github/workflows/` **不存在** |
| **P2-17** | f-string SQL 3 处（表名白名单插值） | ⚪ 未逐条复核（审计已定"当前不可注入"，低优） | — |
| **P3-18** | llm 层反向 import web 层 | ✅ 成立（代码级） | `moni/llm/jobs.py:60-67` `from moni.web.routes.fact_check_plus import (DOMAIN_WHITELIST, …)` |
| **P3-19 / P3-20** | connection 伪锁/fd 悬挂/过时注释；share worker TOCTOU | ⚪ 未复核（P3，本轮不在范围） | — |

### 2.1 现场新增发现（审计包没有的）

**NEW-1 [P0-现场] prod 8900 未运行**
```
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8900/health   → 000
pgrep -fl "uvicorn.*8900"                                              → 无输出
```
`STATUS.md` 记载 pid 6346 已不存在。**未擅自重启**，原因见 §3.1。

**NEW-2 [P0-现场] ghost sources 回归（同一 bug 第二次出现）**

| name | source_type | is_active | created_at | url | 在 sources.yaml? | articles | tags | fetch_jobs |
|---|---|---|---|---|---|---|---|---|
| `mp_a883be18` | rss | **1** | 2026-08-18T07:47:18 | `https://example.com/mp_a883be18` | ❌ | 0 | 0 | 0 |
| `mp_a883be18_b` | rss | **1** | 2026-08-18T07:47:18 | `https://example.com/mp_a883be18_b` | ❌ | 0 | 0 | 0 |
| `kepano` | rss | **1** | 2026-08-18T06:43:32 | `https://stephango.com/feed.xml` | ❌ | **0** | **0** | **0** |

- DB `sources` 共 **15 行**，`sources.yaml` 只有 **12 个 `source_name`** → 差集正是上面 3 行。
- 三行**全部** `articles=0 / source_tags=0 / fetch_jobs=0`（已用 Python sqlite3 逐行核对，非估算）。
- **最刺眼的数字**：`is_active=1` 的行共 **12** 行，其中 3 行是上面这些不在 YAML 里的幽灵行 → **`/fetch` 与 `/config` 的"启用中源"列表里 25% 是幻影**（真·启用 = 9 个）。
- `schema_cleanup_log` 只有一条：`030_drop_mp_ghost_sources | 2026-08-17 08:26:20`。
  **ghost 的 `created_at` 是 08-18 07:47 —— migration 030 跑完的第二天新长出来的。**
- 030 的 SQL 是 `DELETE FROM sources WHERE id IN ('src-mp_3be50e90', …, 4 个硬编码 id)` → **只能清一次性名单，不拦新行**；而 `mp_<hex>[_b]` + `example.com` 这个命名特征在 08-18 又出现了一次。
- 影响与 v1.0.34.2 同型（AGENTS.md 已记）：`/config` 的 `GET /api/config/sources` 直读 DB 不过滤 YAML（`config_sources.py:113-140`）→ ghost 与真源同屏；且 `is_active=1`，所以它们同时污染 `/fetch` 的源列表与统计。
- **根因推断（需一步确认）**：`created_at` 落在测试时段特征明显，最可能是**测试打真库**写进去的（与 v1.0.41 踩坑 #1「test_global_filters 打真库」同一类），也可能是 `/config` 页面的手工残留。**仓库里没有 `conftest.py`（`tests/conftest.py` 与根 `conftest.py` 均不存在）**，所以"测试隔离"目前完全靠各 test 文件自己 `set_db_path(tmp_path)` 的自律，没有任何全局护栏。

**NEW-3 [P1-现场] 全量 pytest 现场泄漏一条 daemon 线程**
跑完 889 passed 的同时，pytest 报 `PytestUnhandledThreadExceptionWarning`：`_run_fetch_job` 的 daemon 线程在 tmp-DB 拆除后仍去写 `fetch_jobs` → `sqlite3.OperationalError: no such table: fetch_jobs`（`fetch/jobs.py:439`）。
这是 **P0-3「判死后线程还活着」在测试环境里的现形** —— 线程逃逸不是理论，是可复现的。也是 CI 上线（P2-16）前必须先处理的噪音源。

---

## 3. TODO 清单（P0 / P1 / P2，附"为什么它对 moni 最有帮助"）

### 3.1 P0 — 不做就会继续烂/继续漏

| # | 事项 | 为什么是 P0（对 moni 的价值） | 估时 | 归属 |
|---|---|---|---|---|
| **P0-A** | **确认 8900 为什么停机并恢复**（NEW-1） | moni 只有一个常驻进程，它停了 = 抓取/入库/分享/UI 全停。先弄清是"被人为停掉（例如给迁移让路）"还是"崩了" —— **审计 §6.2 SOP 要求迁移前停 8900**，若 MiniMax-M3 正在准备 034/035 迁移窗口，我贸然重启会破坏他的停机窗口 + 撞 migration。**这是 review 结论里唯一需要 dad 先回答的问题**（见 §7 拍板 Q1）。 | 10 min | 待拍板 |
| **P0-B** | **P0-2 SPA 路径穿越遏制**（方案 A1） | 唯一**已被实测证明可利用**的漏洞（任意文件读）。能读仓库根 `.env` = 微信 RSS 长期 JWT；泄露后要做「微信后台重发 token + 全源 re-seed」的人工轮换。修复 = 6 行纯函数，收益/成本比全表第一。 | 30 min | **我** |
| **P0-C** | **P0-1 predict_tags 打死亡 provider**（+ 拍板退役与否） | 每工作日 9:25 拿 10 篇 × 2 attempts 全打在 429 已死的 `minimax-cn` 上，**功能静默瘫痪 ≥13 天无人发现**（`cron_runs` 零记录）。同文件 `extract_events_pending` 已走 `resolve_active_model()`，这是**纯一致性修复**——但要先拍板"修还是退役"（审计推荐退役；我同意：tag 体系已由 `tag_filter` + v1.0.40 全局关键词覆盖主要入口，重建成本约 0.5 天）。 | 1h + 拍板 | MiniMax-M3 |
| **P0-D** | **P0-4 子进程进程组击杀**（killpg） | 每次 LLM 超时都留一个孙进程被 reparent 到 launchd 继续烧配额与 CPU，**随任务量线性放大**；AGENTS.md 已两次记 LLM 配额事故，孤儿进程是其中的隐性成本。v1.0.35.17 的"超时假警报"另一半代价正藏在这。 | 2h | MiniMax-M3 |
| **P0-E** | **P0-3 fetch_jobs 心跳租约 + fencing** | 结构性病根：判死看墙钟不看心跳 → 同源双线程并发外呼（bestblogs 375 feed = 双倍流量）+ 旧线程 `mark_done` 覆盖新线程状态（UI 脏读）。现状 `stale 187` 就是累计误伤。**它是"所有状态机补丁之母"**：不做，`get_stale_threshold()` 这种补丁会一直追着最慢源跑。 | 1-1.5 天 | MiniMax-M3 |
| **P0-F** | **P0-5 调度器归宿落地**（+ 拍板） | `schedule.yaml` 注册 6 个 job，**运行期一个调度进程都没有，5 个从未执行**。"自动打标/自动事件抽取/定时抓取"目前全靠人点按钮 → 数据断窗风险 = 节假日全源停摆。审计推荐**方案 B（web lifespan 内嵌 + `MONI_ENABLE_CRON=1` 门控）**；我同意，理由：8900 uptime 3d23h vs scheduler 0 天，需要单独伺候的进程在这个项目活不下来。 | 2h + 拍板 | MiniMax-M3 |
| **P0-G** | **P1-7 llm_jobs 原子认领 + 悬挂回收** | 42.4% 失败率 + 1 条 `running` 永远悬挂（UI 永远转圈，只能手工修 DB）。认领改成单语句 `UPDATE … WHERE id=(SELECT …) AND status='pending' RETURNING …`（本项目 py3.12 的 SQLite 是 **3.45.3**，`RETURNING` 可用 ✅ 已实测）。 | 2-3h | MiniMax-M3 |

### 3.2 P1 — 健壮性与安全

| # | 事项 | 为什么 | 估时 | 归属 |
|---|---|---|---|---|
| **P1-A** | **P1-10 source url 出栈脱敏**（方案 A2） | 任何能打开 8900 UI 的进程都拿到长期有效 RSS 凭据。**打码逻辑在 showcase 生成器里早已存在且成熟**（`scripts/build_showcase_html.py:265 _TOKEN_QS`），生产 API 反而裸奔 = 两套实现、错的那套在线上。 | 30 min | **我** |
| **P1-B** | **P1-9 `:href` scheme 白名单**（方案 A3） | RSS 文章 / LLM 输出都是**不可信外部输入**，持久化一个 `javascript:` URL 就是 stored XSS（同源可打所有 `/api`）。11 处绑定一次性收敛到 1 个纯函数。 | 20 min | **我** |
| **P1-C** | **ghost sources 回归治理**（NEW-2，方案 B） | 同一个 bug 第二次出现 = 说明"删数据"不是解，"挡写入 + 挡出栈"才是。这直接对应 dad 的申报材料观感（`/config` 源列表混着 `example.com` 假源）。 | 40 min | **我** |
| **P1-D** | **测试隔离护栏**（NEW-3 / 审计"根 conftest tmp-DB"）| 889 个测试目前**没有全局 DB 护栏**，靠每个文件自律 `set_db_path(tmp_path)`；v1.0.41 已实测发生过"测试把 prod 源改成 example.com"。护栏（阻断测试进程连真库）是 NEW-2 的根因防线，也是 CI（P2-16）的前置条件。 | 30 min | **我** |
| **P1-E** | P1-5 failover + 熔断 + P1-6 stdin 传输 | 43% 失败率的雪崩放大器；配额死时每条任务固定烧 2× 调用时长。 | 1 天 | MiniMax-M3 |
| **P1-F** | P1-8 打标互斥 + `misfire_grace_time` 60→300 | 9:25 打标被 APScheduler 静默 skip，功能"随机失灵"难归因。 | 0.5 天 | MiniMax-M3 |
| **P1-G** | P1-11 `PUT /api/fetch/sources` 删或修 | 扇出全源 + 非原子 YAML 写；当前 SPA 不调用 = **一触即发的地雷**。审计推荐**删**（我同意：改便宜的是删，一个无人使用的端点不值得配 4 项加固）。 | 15 min | MiniMax-M3（routes/fetch.py 归他） |
| **P1-H** | P1-12 结构化 job 事件日志（首批 2 处 `except: pass`） | "假警报/假完成"已是本项目两次 ship 的根因（v1.0.35.17、v1.0.36 坑#3）——吞噬型异常是同类事件的温床。 | 1h | 待定 |

### 3.3 P2 — 工程化（不急但要排）

| # | 事项 | 为什么 | 估时 | 归属 |
|---|---|---|---|---|
| **P2-A** | **P2-15 modal title 转义** | 2 行；`message` 已转义、只有 title 裸奔，属"第一个传动态 title 的人就引爆"的潜伏 sink。**顺路做，成本≈0**。 | 5 min | **我** |
| **P2-B** | **P2-13 前端生命周期治理**（方案 C，缩小版） | 恒定后台流量 + **证据张冠李戴**（A 的证据链渲染进 B 的 modal）——后者是用户可见的内容正确性问题。全库 `AbortController` 0 次，缺的是 `pollManager` + 序号守卫这一层原语。 | 0.5-1 天 | **我**（下一 sprint） |
| **P2-C** | **P2-14 vendored Alpine/GSAP + SRI** | 与项目自己的"单文件离线 demo 交付"约束直接冲突（AGENTS.md v1.0.35.3）：jsdelivr 不可达 = SPA 全无交互；CDN 投毒 = 管理面板 instant XSS。 | 30 min | **我**（下一 sprint） |
| **P2-D** | P2-16 CI（`.github/workflows/ci.yml`）+ P1-12 ruff 分阶段 | 889 tests 只活在开发者本机；注意：**全量 pytest 实测 197.73s**，CI 预算充裕（private repo 2000 min/月）。分支保护 PRIVATE 不可用（审计已实测 403），CI 只能当看板。 | 1-2 天 | 待定 |
| **P2-E** | P3-18 层界（`llm → web` 反向 import）+ P3-19 connection 伪锁/fd 悬挂/过时注释 | 注释即谎言（`llm/jobs.py:28-30` 宣称 `check_same_thread=False`，与 v1.0.37.5 后的现实矛盾）会误导下一个改线程模型的人。 | 0.5 天 | 待定 |

**排序依据（为什么是这个顺序）**：
1. **先止"漏"再止"乱"**：P0-B（已实测可利用的信息泄露）与 P1-A（凭据裸奔）都是**对外暴露面**，修一次永久有效；而 P0-E/P1-E 是"运行期还会反复发生"的类别，修复成本高、必须配迁移窗口，不能塞进 1.5h。
2. **先做"一次修复消灭一整类"的**：P1-B（11 处 `:href` 收敛成 1 个纯函数）、P1-C（ghost 从"删数据"改成"挡写入+挡出栈"）、P1-D（1 个护栏保护 889 个测试）——都是原语型修复，不是点补丁。这正是 SUCCESSION-PLAN §判断 4 的核心主张。
3. **给 MiniMax-M3 让路**：他做的是 P0-C/D/E/F/G（LLM 层 + 租约 + 调度器），**每一个都带 migration 或长期停机窗口**，是 1.5h 装不下、且必须串行的部分。我在同一时间做**零 DDL、零迁移、零共享文件**的安全/前端/测试护栏，两条泳道天然不打架。

---

## 4. 核心方案落地细节（2 个必做 + 1 个顺延）

### 方案 A —— 安全原语 PR（推荐本次 1.5h 的主交付）

**一句话**：把三个安全缺陷收敛成**三个纯函数原语**，在唯一入口强制走，消灭"逐点修"的补丁模式（SUCCESSION-PLAN §判断 4）。

#### A1. P0-2 SPA 路径遏制 — `resolve_static_path()`

- **改动文件**：`moni/web/server.py`（唯一改动点；`:122-126`）
- **新增签名**（放 `server.py` 内的模块级纯函数，便于测试直调）：

```python
def resolve_static_path(root: Path, rel: str) -> Path | None:
    """把 URL rel 路径解析成 root 下的真实文件；越界/非法一律返回 None。

    拒绝: NUL / 反斜杠 / '..' 段 / 绝对路径 / symlink 逃逸出 root。
    """
```
- **设计要点**（照 SEC-01 原型语义重写，因为原型已丢失）：
  1. `Path(base) / user_path` 是**绝对路径右操作数直接丢弃 base** 的 API 陷阱 —— 这是根因，不是"忘了过滤 `..`"。
  2. 先做纯 POSIX 语义分解：拒绝含 `\x00` / `\\` / 任一段 `== ".."` / 以 `/` 开头的输入。
  3. 再 `candidate = (root / rel).resolve()`，**root 也 resolve**，同坐标系下比较 → 用 `candidate.is_relative_to(root.resolve())`（**不用字符串前缀比较**，前缀比较会被 `/root-evil` 绕过）。
  4. `resolve()` 一并解决 **symlink 逃逸**（static 里放个指向外的软链是同型漏洞）；`is_relative_to` 在同一坐标系下天然放行"root 内合法软链"。
  5. 越界与"文件不存在"**统一返回 None → 404**，不区分，不泄露路径信息。
- **回归风险**：合法静态文件必须照常服务（`/js/app.js`、`/css/main.css`、`/` → index.html）。SPA fallback 的 `else → index.html` 分支保持不变。
- **测试**（新文件 `tests/test_spa_fallback.py`）：
  - 14 向量真值表：`//etc/hosts`、`/etc/passwd`、`..%2f..%2f`、`/../../AGENTS.md`、`/root-evil`（前缀绕过）、`\x00` 截断、反斜杠、绝对路径、symlink 逃逸、root 内合法 symlink（**放行**）、`/js/app.js`（放行）、`/`（放行）、空串、超长路径。
  - 断言方式：纯函数单测（不经 HTTP）+ 一个 FastAPI `TestClient` 集成断言 `//etc/hosts` **不再**返回 213B 真文件。
- **手工验证**（8900 起来后，审计 §8 原样）：
  `curl -s --path-as-is -o /dev/null -w '%{http_code} %{size_download}' http://127.0.0.1:8900//etc/hosts` → 期望 **200 + 216024 字节的 index.html**（而非 213B 的 hosts）。
- **回滚**：单函数 revert（一行调用点改回 `STATIC_DIR / full_path`）。**零 DB、零迁移、零 schema**。

#### A2. P1-10 source url 出栈脱敏 — `sanitize_source_url()`

- **改动文件**：
  - 新增 `moni/web/redact.py`（**web 层**，与 `moni/llm/redaction.py` 的 LLM 落库脱敏职责分开，不混用）
  - `moni/web/routes/config_sources.py`：list (`:102`) / create (`:173`) / update (`:224-244`) / get-by-id (`:187`) / 其它 `url=r["url"]` 出栈点统一过原语
  - `moni/web/routes/fetch.py:135`（`GET /api/fetch/sources` 走 YAML）同样过一遍 —— 该文件归 MiniMax-M3，**改为"接口约定 + 他一并改"或我改前先报备**（见 §5.3 共享文件协议）
- **签名**：

```python
def sanitize_source_url(url: str | None) -> str | None:
    """把 query 里 token 类凭据的值替换为 '…'，保留参数名（提示"这里有凭据"）。
    非 http(s) / 无 query / 无凭据 → 原样返回（幂等、向后兼容）。
    """
```
- **设计要点**：
  1. **只打码值，保留参数名** —— 用户能看出"这个源带 token"，但不拿到值。
  2. **幂等**：已打码的值再次传入不变。
  3. **向后兼容**：无 `token=` 的 URL 原样返回 → **不会破坏现有测试**（已核实 `tests/test_api.py:449` 断言 `"https://example.com/created-via-post"` 原样返回 ✅）。
  4. **PUT/POST 语义（最容易做错的地方，先写测试再写实现）**：
     - 收到**打码值**（含 `…`）→ 判定为"未修改"，保留 DB 原值；
     - 收到完整新 URL → 覆盖；
     - 打码值的判定用**参数名 + 值形态**双条件，不能只 `'…' in url`（避免用户真的传了一个含 `…` 的合法 URL 被误判）。
  5. **消灭两套实现**：showcase 生成器的 `_TOKEN_QS` / `_JWT` 打码逻辑迁来复用（`scripts/build_showcase_html.py:265-266`），demo 与生产走同一原语。
- **测试**（新文件 `tests/test_source_url_redaction.py`）：
  - 真值表：`?token=eyJhbGci…`（打码）、`?token=eyJ…&a=1`（只打 token）、无 token（原样）、多凭据（`token` + `secret` + `key`）、大写 `TOKEN`、URL-encoded 值、空/None。
  - 幂等断言（跑两遍结果一致）。
  - PUT 语义：打码值 → DB 原值不变；完整新 URL → 覆盖（断言 DB 实际内容，不只断言响应）。
  - 集成：`GET /api/config/sources` 响应里 **不含 `eyJ`**（对 `wechat_rss_a/b` 两行的强断言）。
- **回滚**：删 `redact.py` + revert 出栈点。**零 DB、零迁移**（只改读路径，不动 `sources.url` 存值 —— 存值打码会破坏抓取功能）。

#### A3. P1-9 `:href` scheme 白名单 — `safeHref()`

- **改动文件**：新增 `moni/web/static/js/safe-url.js`；`index.html` 11 处 `:href` 改为 `:href="safeHref(...)"`（或 `x-show` + 降级为纯文本渲染）；`index.html` 引入该脚本。
- **实现要点**：仅放行 `http:` / `https:`（**大小写不敏感**，先 `trim()` 再判定）；其余（`javascript:` / `data:` / `vbscript:` / `java\tscript:` 变体 / 相对协议）返回 `null` → 模板降级成纯文本；顺手补 `rel="noopener noreferrer"`（当前 11 处只有 2 处有）。
- **测试**：`node` 单测跑真值表（`javascript:`、大小写混淆、`java\tscript:`、`data:text/html`、`vbscript:`、正常 https → 放行、`/relative/path` → 按策略），**磁盘级 `node --check`** 不能只靠 `git diff --stat`（demo 16.2 的教训）。
- **回滚**：`index.html` 是纯静态，revert 即回到 `:href` 直绑。**注意 `scripts/build_showcase_html.py` 若引用同一批绑定需同步**（R6 风险）。

#### A4. P2-15 modal title 转义（顺路，5 min）

- `moni/web/static/js/modal.js:238,240-246`：`opts.title` 与按钮文案走已有的 `_escapeHtml`（`app.js:2660-2665` 有现成实现，抽到 `modal.js` 或复制同一函数）。
- **风险**：极低（现有调用点全传静态字面量）；收益 = 消灭一个"第一个传动态 title 的人就引爆"的 sink。

**方案 A 交付形态**：1 个 PR = 4 个 commit（A1 / A2 / A3 / A4），文件互不重叠，任何一个可单独 revert。

---

### 方案 B —— ghost 回归治理 + 测试隔离护栏（本次 1.5h 装不下，建议紧随其后 40 min）

#### B1. ghost sources 回归（NEW-2）

- **第一步（需 dad 拍板）**：确认 `mp_a883be18` / `mp_a883be18_b` / `kepano` 三行的性质。
  - `mp_*` 两行：**建议删**（0 articles / 0 tags / 0 fetch_jobs / 不在 YAML / url=example.com）。
  - `kepano`：**需 dad 确认**是否是他有意加的源（`https://stephango.com/feed.xml`，真实的 Obsidian 作者博客），不在 YAML → 走 YAML 才是唯一真源。
- **第二步（结构性，才是重点）**：030 的失败教训是"DELETE-by-id 只清名单不拦新行"。改成两件事：
  1. **写入侧**：源头定位 —— 找出谁在 08-18 07:47 往真库写了这两行（大概率是测试；`ls tests/conftest.py` 为空、无全局护栏）。护栏见 B2。
  2. **出栈侧**：`GET /api/config/sources` 增加 `WHERE url NOT LIKE 'https://example.com/%'` 或 `is_fetchable` 派生字段（灰 badge 展示"未在 YAML 注册"），让 ghost 即使再长出来也**不与真源同屏**。这是防御 UX，AGENTS.md v1.0.34.2 已把它列为 out-of-scope 的下一步。
- **测试**：扩展现有 `tests/test_migration_030_ghost_cleanup.py` 的思路 → 新增"**模式级**"断言：
  - 任何 `url LIKE 'https://example.com/%'` 且 `NOT EXISTS` 于 `sources.yaml` 的 DB 行 = 0 行；
  - `migrate()` 幂等跑两遍后该断言仍成立（防"新 ghost 又进来"）。
- **迁移编号**（关键，防撞车）：MiniMax-M3 已领 **034 / 035**（`fetch_jobs` 租约三列、`llm_jobs` owner/fence）。本轮 ghost 清理若需 DDL/DML 迁移，**必须排到 036**。见 §5.3。

#### B2. 测试隔离护栏（NEW-3 / 审计"根 conftest tmp-DB"）

- **新增根 `conftest.py`**（当前**不存在**，这是系统性缺口）：
  - session 级 autouse fixture：把 `MONI_DB_PATH` 指向 `tmp_path_factory` 下的 `news.db` 副本（`data/news.db` → 临时副本，跑 `migrate()`），**测试全程不可能碰真库**。`db/connection.py:90-92` 已支持 `MONI_DB_PATH`（已实测），无需改生产代码。
  - 加固：把真库路径加入"禁止写入"断言（万一有测试绕过 `MONI_DB_PATH` 直连 `data/news.db`，**fail loudly** 而不是静默污染）。这是把"纪律型设计"（`connection.py:115-119` 的自律注释）换成"护栏型设计"。
- **测试**：新增 `tests/test_test_isolation.py`：断言 `get_db_path() != ROOT/data/news.db`、且真库 mtime 在跑完整测试后未变。
- **风险 R**：autouse 全局 fixture 会动到所有测试的行为 → **必须放独立 PR + 跑全量 889（197s）验证零回归**；若有 test 依赖真库预置数据，逐个改成显式建 fixture，**不要一把梭**。
- **顺带**：NEW-3 的 daemon 线程泄漏（`_run_fetch_job` 在 tmp-DB 拆除后仍写库）建议在 P0-E 租约改造时一并收掉（它的根因就是"线程没有 teardown 契约"），本轮只在 conftest 里对它做 `filterwarnings` 白名单或直接列为已知告警，不掩盖为"通过"。

---

### 方案 C —— 前端生命周期治理（P2-13，缩小版，顺延到下一 sprint）

- **改动**：`app.js` 新增 `pollManager`（keyed start/stop，view 切换统一 teardown）；`loadLlmJobs`（`:4296-4303`）改为受管轮询；`showArticleDetail`（`:3728-3735`）与 `openJobDetail`（`:4380`）加 `AbortController` + 序号守卫；`articleEvidenceList` 在 open/close 重置（`:3723 / :4227`）；`restartService`（`:186-201`）先 `clearInterval` 旧句柄。
- **为什么顺延**：审计估 1 天，且**无自动化设施**，验收要列 8 条手工清单（点击 A→B 快速切换、重启按钮双击、LLM tab 停留后切走查 Network 面板归零）—— 属于 dad 真机验证范畴（而 dad 不在本次 1.5h 窗口内）。**1.5h 里塞它 = 必然半成品**，违背 §6 的"ship less scope"。
- **可单独先做的最小切片**（若 dad 想先止流量）：仅 `loadLlmJobs` 的 teardown（约 20 min），其余留给完整 sprint。

---

## 5. 与 MiniMax-M3 的并行可行性（怎么做到"完全不撞"）

### 5.1 文件所有权矩阵（硬边界）

| 区域 | MiniMax-M3（重型后端核） | **我（deepseek-flash）** |
|---|---|---|
| `moni/llm/**`（client / jobs / runner / prompts / redaction） | ✅ 独占 | ❌ 不碰 |
| `moni/fetch/**`（jobs / base / sources） | ✅ 独占 | ❌ 不碰 |
| `moni/cron/**`（jobs / scheduler） | ✅ 独占 | ❌ 不碰 |
| `moni/db/leases.py`（新）、`moni/db/connection.py` | ✅ 独占 | ❌ 不碰 |
| `moni/db/migrations/034*`, `035*` | ✅ 领号 | ❌ 不领 |
| `moni/web/routes/fetch.py`（`_run_fetch_job` 租约化） | ✅ 独占 | ❌ 不碰（含 `:135` / `:153-163`） |
| `moni/web/server.py` | ⚠️ 仅 lifespan 段（`MONI_ENABLE_CRON`） | ⚠️ 仅 `spa_fallback` 段 |
| `moni/web/routes/config_sources.py` | ❌ | ✅ 独占 |
| `moni/web/redact.py`（新）、`moni/web/static/**` | ❌ | ✅ 独占 |
| `conftest.py`（新）、`tests/test_spa_fallback.py`、`tests/test_source_url_redaction.py` | ❌ | ✅ 独占 |
| `moni/db/migrations/036*`（ghost 清理，若拍板） | ❌ | ✅ 领号 |
| `moni/web/routes/service.py`（报 scheduler 存活） | ✅ | ❌ |

**结论**：**只有 `moni/web/server.py` 一个文件是共享的**，且两段的函数体互不相邻（`spa_fallback` 在 `:116-126`，lifespan 在 `:32-37` 附近）。协议见 §5.3。

### 5.2 DB / 锁冲突分析（本泳道零风险的关键）

| 风险 | MiniMax-M3 | 我 | 规避手段 |
|---|---|---|---|
| 迁移独占窗口 | migration 034/035 需**停 8900 + 备份 + 跑迁移**（SUCCESSION-PLAN §6.2） | **零 DDL、零迁移**（A1/A2/A3/A4 全是纯函数 + 静态文件 + 读路径） | 我完全不进入迁移窗口期；他的停机窗口与我的工作天然串行无交 |
| SQLite 写锁 | 改 `fetch_jobs` / `llm_jobs` 写路径（心跳 UPDATE 0.1 wps，审计已验证无感） | **零写路径改动**（脱敏只改出栈） | — |
| **pytest 并行打同一 DB** | 全量 pytest 会跑真库的测试自律路径 | 同上 | **用 `MONI_DB_PATH` 隔离**：两个 agent 各自 `cp data/news.db /tmp/moni-test-<agent>.db` + `export MONI_DB_PATH=/tmp/…`，`db/connection.py:90-92` 已原生支持（✅ 已核实）。**更强的做法**：B2 的根 conftest 落地后，测试再也不碰真库，并行 pytest 零冲突 |
| checkout 争用 | — | — | **必须 worktree 隔离**（AGENTS.md 双 Agent SOP）：`git worktree add ../moni-sec-prim -b feature/v1.0.42-safety-primitives` + 他 `../moni-llm-core`。**注意**：当前已有 2 个遗留 worktree（`moni-ref-fact-check`、`moni-sprint-16.6-hotfix`），新建前先确认端口/目录不撞 |
| 端口 | 8900 prod / 8901 dev | 我另开 **8902**（只读验证用），不碰 8900/8901 | 双端口分离约定（v1.0.5.10+）扩展到 8902 |
| 测试时长 | 全量 **197.73s** | 同 | **约定全量 pytest 串行化**（同一时刻只有一个 agent 跑全量），单文件 pytest 可并行 |

### 5.3 与 `review-minimax-m3.md`（14:43 已落盘）的实测对账

**这不是假设分工，是对着他已经写好的方案逐文件比对的结论。** 他在 `1.2` / `2.2` 明确**主动排除**了 P0-1（待拍板）、**P0-2**、**P0-5**（待拍板）、**P1-9~12** —— 也就是说，我这条泳道恰好是他**主动让出来的互补面**。

| MiniMax-M3 声称要动的文件（任务 A + B） | 我要动 | 重叠? |
|---|---|---|
| `moni/db/leases.py`（新）、`migrations/034_leases.sql`（新）、`migrations/035_llm_runner_config.sql`（新） | — | ❌ 无 |
| `moni/fetch/jobs.py`、`moni/web/routes/fetch.py`、`moni/llm/jobs.py` | — | ❌ 无 |
| `moni/llm/runner.py`（新）、`moni/llm/client.py`、`moni/share/worker_llm.py` | — | ❌ 无 |
| `tests/test_db_leases.py` / `test_fetch_jobs_lease.py` / `test_llm_runner.py` / `test_llm_call_hardening.py`（均新建） | `tests/test_spa_fallback.py` / `test_source_url_redaction.py`（新建） | ❌ 无 |
| — | `moni/web/server.py`（**他未claim, P0-5 顺延**）、`moni/web/redact.py`(新)、`moni/web/routes/config_sources.py`、`moni/web/static/**`、`conftest.py`(新)、`migrations/036*`(ghost) | ❌ 无 |

**结论：两条泳道当前零共享文件。** 我 §5.3 原本担心的 `moni/web/server.py` 竞争**不存在**（他把调度器（P0-5）顺延到 dad 拍板后的下一个 sprint）。因此 `server.py` 本轮**归我独占**（仅 `spa_fallback` 段），下面三条约定改成"为将来预留"：
1. 我只改 `spa_fallback` 的 `candidate = STATIC_DIR / full_path` → `resolve_static_path(...)` + 追加模块级纯函数；将来他做 lifespan 时不得触碰该函数体。
2. 双方都只**追加** import，不重排（reorder 会产生整段冲突）。
3. 合并顺序：我（安全，零迁移）**先合先 tag**；他（含 migration，需停机窗口）后合。

**唯一真实存在的跨泳道风险（必须让 dad 知道）**：他的任务 B 要删 `ChatClient` shim，而 shim（若 拍板 1 = 退役）会导致 `cron/jobs.py:39` 的 import 消失，但**另两个 import 点仍在**：
- `moni/web/routes/llm.py:16` `from moni.llm import ChatClient`（`:25-31` `_get_client()` 还在用它，文件头注释写"ChatClient shim kept above for /summary compatibility"）
- `moni/llm/__init__.py:8, 16` re-export

他已在 R6 给了缓解（**保留 `ChatClient` 但 `raise DeprecationError`**）—— 我复核后**同意该缓解**，并补一条：`moni/web/routes/llm.py` 的 `/summary` 路由必须在本轮**同步切到 `call_llm`**，否则 shim 一旦变 raise，`/summary` 会从"能跑但参数被丢"变成"直接 500"（**比现在更坏**）。这条应作为他 PR-B 的验收项，或单列给我（该文件无其他人 claim）。

### 5.4 对 P0-A（8900 停机）的新证据

MiniMax-M3 的 **PR-A 要跑 `migrations/034_leases.sql`**，而 SUCCESSION-PLAN §6.2 的 SOP 第 1 步就是"**停 8900**"。**这极可能就是 8900 现在停着的原因**（他的报告 14:43 落盘，`data/news.db` mtime 14:45）。
→ 我原来"不要擅自重启"的判断被进一步坐实：**若他已进入迁移窗口，我重启 8900 = 撞 migration**。Q1 照问，并且请 dad 顺带确认"MiniMax-M3 的 PR-A 是否已在跑迁移"。


---

## 6. 1.5 小时 sprint 工期预算

**前提**：1.5h = 90 min（不含 dad 拍板等待）。**全量 pytest 实测 197.73s ≈ 3.3 min**，ship 前后各跑一次 ≈ 7 min，必须计入。

| 时段 | 动作 | 分钟 | 备注 |
|---|---|---|---|
| 0:00-0:10 | 拍板 ack + vault sprint folder 3 stub + feature branch/worktree + `MONI_DB_PATH` 隔离 | 10 | sprint-workflow Phase 1 Step 0 硬门 |
| 0:10-0:40 | **A1** P0-2 `resolve_static_path` + `tests/test_spa_fallback.py` 14 向量 | 30 | 纯函数，无依赖 |
| 0:40-1:10 | **A2** P1-10 `sanitize_source_url` + 出栈点 + `tests/test_source_url_redaction.py` | 30 | PUT 语义先写测试 |
| 1:10-1:20 | **A4** P2-15 modal title 转义（5 min）+ 单文件 pytest 快跑 | 10 | 低成本顺路 |
| 1:20-1:31 | 全量 pytest（3.3 min）+ 手工 curl 验证 A1（8900 起后） | 11 | 手工验证依赖 dad 先答 Q1 |
| 1:31-1:40 | PR + **6 问 review packet** 发 dad | 9 | Phase 2 终点 = open PR |
| **合计** | **A1 + A2 + A4 + PR** | **≈ 100 min** | ⚠️ **超 1.5h 约 10 min** |

**剪裁建议（推荐 = C1）**：

| 方案 | 内容 | 时长 | 评价 |
|---|---|---|---|
| **C1（推荐）** | A1 + A2 + A4 + PR | ≈100 min | 超 10 min，可接受（两处都是 30 min 的硬块，压缩空间小） |
| C2 | A1 + A4 + PR（砍 A2） | ≈70 min | **不推荐**：A2 是"凭据裸奔"，安全价值 ≥ A1，砍它换时间不划算 |
| C3 | A1 + A2 + A3(safeHref) + A4 + PR | ≈120 min | ❌ 明确装不下 → A3 顺延或接受 2h |
| C4 | 仅 A1（P0-2 单点） | ≈50 min | 最小可 ship 切片，适合"今天只想清掉实测漏洞" |

**A3（P1-9，20 min）与 B（ghost/护栏，40 min）**：明确标注**下一 sprint**，除非 dad 允许本次超时到 2h。

**注意**：**手工验证 A1 依赖 8900 起来**（当前停机）。若 dad 回答 Q1 前不重启 8900，则 A1 只能交付"纯函数单测 + TestClient 集成断言"，curl 运行时 PoC 验证挂"待 8900 复起"（**必须在 PR 6 问里如实写"运行时未验证"**，不能拿单测冒充运行时证据）。

---

## 7. sprint-workflow 合规（本次必须走完整三步）

### Phase 1 — Brief + 拍板（先 ack 再动码）

**Step 0 硬门**：先建 Obsidian vault sprint folder + 3 stub，再写任何代码/PLAN：
`~/Library/Mobile Documents/iCloud~md~obsidian/Documents/tqob/05-Coding/project-moni/sprints/sprint-26091001-safety-primitives/`
内含 `plan-*.md` / `sprint-26091001-*.md` / `verify-*.md`，各自 YAML frontmatter（`id: 26091001`、`type: sprint`、`status: 待启动`、`priority: 高`，wikilink 值**加引号**）。
> ⚠️ vault 真身是 `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/...`，`~/Documents/iCloud~md~obsidian/` 是**假目录**。写 vault **必须用 `write_file` 或 `terminal`**，`execute_code` 的 `open(w)` 写沙箱副本不落盘，写完 `read_file` 复查 marker。
> 主仓镜像 `docs/plans/2026-09-10-sprint-26091001-safety-primitives.md` **双写**（两个位置都要）。

**拍板问题（≤4 项，推荐项置首）**：

- **Q1（阻塞性，必须先答）**：**8900 现在是停的**。→ A. **你确认 MiniMax-M3 的 PR-A 是否正在跑 migration 034（停机 SOP 第 1 步就是停 8900，我判断 8900 停机极可能就是它）**，若是则等他跑完再起（**推荐**，避免撞迁移窗口）/ B. 我立刻按 AGENTS.md 绝对路径命令重启 8900 / C. 先不管，本次只交付纯函数 + 单测，运行时验证挂起。
- **Q2**：本次 1.5h 的切法？→ A. **C1：安全原语 PR（P0-2 + P1-10 + P2-15），约 100 min（推荐）** / B. C4：只做 P0-2（约 50 min，最稳）/ C. C3：含 safeHref，约 2h（超时）/ D. 换方向（先做 ghost/测试护栏 B，40 min）。
- **Q3**：**ghost sources 处置**（NEW-2）→ A. **删 `mp_a883be18` ×2 + `kepano` 另议（推荐）** / B. 三行全删 / C. 都保留，只在出栈层灰显"未在 YAML 注册" / D. 先只做出栈过滤，删数据留到专门 sprint。
- **Q4**：**P1-11 `PUT /api/fetch/sources`**（扇出全源地雷）→ A. **删端点（审计推荐，我同意）** / B. 留待 MiniMax-M3 修（routes/fetch.py 归他） / C. 本次不管。

### Phase 2 — 码 + PR

1. 写 **PRD / TECH-SPEC / TEST-PLAN** 到 sprint folder（dad 不读，供下一个 agent）。
2. worktree 隔离 → feature branch → 4 commit（A1/A2/A4 + 文档）。
3. 自验：单文件 pytest → **全量 889（197s）** → （8900 起后）`curl --path-as-is` 手工验证。
4. **`node --check` 磁盘级 JS 语法**（凡动了 `index.html` / `modal.js` 必跑；demo 16.2 教训：不能只 `git diff --stat`）。
5. 开 PR → 发 **6 问 review packet**（大白话、无 md 语法，< 500 字符）：变了什么 / 没变什么 / 改了哪些文件 / 凭什么说它能用（真实 pytest + curl 输出）/ 残留风险 / 你要手点哪一步。
6. **停在 open PR，等 dad merge**（修复类 PR 才可自主 merge；本次涉及安全语义 + 出栈脱敏契约，走 dad review 更稳）。

### Phase 3 — Closeout（7 项，缺一不算完）

1. sprint doc `status: 已完成` + `end_date` + 回顾行 + commit/PR。
2. `verify-YYYY-MM-DD.md` **3-1-1**（3 条 ship 了什么 + 1 条学到什么）。
3. `decision-log.md` 追加（含"为什么"列）。
4. `STATUS.md` 新段 + Obsidian mirror **双写（md5 一致）**。
5. `git tag v1.0.4x`（**新 tag，不 `--force`**）+ push。
6. Obsidian mirror 双写校验。
7. **shipped 交付清单**（`shipped_tag` / `shipped_short`）—— **等 dad 拍板 closeout OK 后才激活**；`shipped_short` 单行 quoted、<500 字符、无 `: `、无 md 语法。
8. 顺带：`HANDOFF.md` / `vibe-coding-log.md` prepend（本地，不上 GH）；清理本次 sprint 临时进程与 `/tmp` 脚本（Closeout Step 7）。

---

## 8. 风险与回滚预案

| # | 风险 | 触发点 | 缓解 / 回滚 |
|---|---|---|---|
| R1 | **擅自重启 8900 撞上 MiniMax-M3 的迁移停机窗口** | P0-A | **先问 Q1，不动手**；若要起，用 AGENTS.md 绝对路径命令，起前 `pgrep -f "uvicorn.*8900"` 确认无残留 |
| R2 | A1 containment 误杀合法软链 / 大小写不敏感 FS | `resolve().is_relative_to()` | root 也 resolve（同坐标系）；测试里放"**root 内合法 symlink → 放行**"断言防回归 |
| R3 | A2 在 PUT 语义上做错（打码值当成新 URL 覆盖真值） | `config_sources.py` update | **先写测试再写实现**；打码值判定用双条件（参数名 + 值形态）；集成断言跑完查 **DB 实际内容** |
| R4 | A2 误伤其它出栈点 / 破坏现有断言 | 全量 pytest | 无 `token=` 的 URL 原样返回（已核实 `test_api.py:449` 不受影响）；**全量 889 必须绿** |
| R5 | A3 `safeHref` 破坏静态渲染 / showcase 生成器脱节 | `index.html` 11 处 | `node --check` 磁盘级 + 浏览器实测（不靠解析 HTML）；`build_showcase_html.py` 引用点同步 |
| R6 | B2 全局 conftest 引发大批测试回归 | autouse fixture | **必须独立 PR**；先 dry-run 全量；逐个改显式 fixture，不一把梭 |
| R7 | ghost 删除误删真人数据 | `sources` DELETE | 删前 `sqlite3 .backup`；只删"0 articles / 0 tags / 0 fetch_jobs / 不在 YAML / url=example.com"五行特征全中的行；走 migration + `schema_cleanup_log` 审计 |
| R8 | 两个 agent 并行 pytest 互相污染真库 | 并行跑全量 | `MONI_DB_PATH` 隔离（已原生支持）；全量 pytest **串行化**约定；B2 落地后根治 |
| R9 | **审计原型 `/tmp/sprint-prototypes/` 已丢失** | A1/A2/A3 的"验收基线"没了 | 已在本报告按原型语义**重新描述关键断言**（§4）；实施时按 §4 重写测试，不依赖 `/tmp` 资产。**教训：原型放 `/tmp` 会随重启消失，应落 `docs/sprint-audit-2026/prototypes/`** |

---

## 9. 附录：复核命令（可原样重跑）

```bash
cd /Users/mt16/dev/moni

# 基线
/usr/local/bin/python3 -m pytest tests/ -q -p no:cacheprovider        # → 889 passed, 2 skipped (197.73s)

# P0-2（需 8900 在跑）
curl -s --path-as-is -o /dev/null -w '%{http_code} %{size_download}\n' http://127.0.0.1:8900//etc/hosts
#   213B 左右 = 漏洞仍在；216024B = 已修（返 index.html）

# P0-3 / P1-7 现场
sqlite3 -readonly data/news.db "SELECT status,COUNT(*) FROM fetch_jobs GROUP BY status;"   # stale 187
sqlite3 -readonly data/news.db "SELECT status,COUNT(*) FROM llm_jobs GROUP BY status;"     # failed 73 / done 98 / running 1

# P0-5 调度体系
sqlite3 -readonly data/news.db "SELECT job_id,COUNT(*) FROM cron_runs GROUP BY job_id;"
pgrep -fl "apscheduler|moni.*cron"      # 期望: 无输出

# NEW-2 ghost 回归
sqlite3 -readonly -header -column data/news.db \
  "SELECT name,is_active,created_at,url FROM sources WHERE url LIKE 'https://example.com/%';"
sqlite3 -readonly data/news.db "SELECT * FROM schema_cleanup_log;"
grep -c "source_name" config/sources.yaml    # 12  vs  DB 15 行

# P1-10 凭据
sqlite3 -readonly data/news.db "SELECT name,substr(url,1,60) FROM sources WHERE url LIKE '%token=%';"

# ⚠️ 环境怪癖（本轮实测）: 某段时间 `sqlite3` CLI 对 data/news.db 报
#   "Error: in prepare, unable to open database file (14)"，但**文件未损坏** —
#   用 Python 的 URI 只读模式可正常读，属 CLI 层异常，不是数据问题：
#   /usr/local/bin/python3 -c "import sqlite3;c=sqlite3.connect('file:data/news.db?mode=ro',uri=True);print(c.execute('SELECT COUNT(*) FROM sources').fetchone())"

# P1-9 href 面
grep -c ':href=' moni/web/static/index.html                 # 11
grep -rn "AbortController" moni/web/static/js/ | wc -l      # 0

# P2-16 CI
ls .github/workflows/    # No such file or directory
```

---

## 10. 一句话给 dad

**审计包的 21 张工单 4 天没动，我复核后 P0 全部成立；另外发现 8900 是停的（我怀疑是 MiniMax-M3 的 migration 034 停机窗口）、ghost 假源又长出来一批（`/config` 里 12 个"启用中"源有 3 个是幻影，同一个 bug 第二次）。我这一泳道最适合在 1.5h 内交付"安全原语 PR"（P0-2 路径穿越 + P1-10 凭据脱敏 + P2-15 转义，≈100 min）——已对着 MiniMax-M3 的方案逐文件对账，**两条泳道零共享文件**。开工前只需你答 4 个问题，其中 Q1 是"8900 停机是不是他跑迁移"。**
