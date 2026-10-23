# moni 深度架构审计 Backlog — 2026-09-06

- 审计对象: `/Users/mt16/dev/moni` @ branch `exhaustive-mode` (clean, main HEAD 3a5cc00 之后)
- 审计人: 独立架构审计 ( Principal Architecture Auditor mode)
- 红线遵守: 全程只读业务代码与 `.git/`；产出仅写入本目录与 `/tmp/sprint-prototypes/`
- 方法: 逐文件精读核心并发链路 (db/connection, llm/client, llm/jobs, fetch/jobs, cron/jobs, cron/scheduler, web/routes/fetch, share/worker*, fetch/base, web/server, web/routes/service) + 全库危险模式 grep (181 处 except Exception / shell=True=0 / f-string SQL=3 处表名白名单插值) + 前端 Explore 扫描 + **对运行中 8900 的只读 PoC 验证** (curl --path-as-is)
- 定级: P0 正确性/崩溃/安全 > P1 健壮性 > P2 工程化 > P3 演进 >【需拍板】触及既有架构决策

---

## 一、总评 (3 句)

1. **抓取→入库→分享主干可用，但三条"任务状态机" (fetch_jobs / llm_jobs / cron_runs) 全部缺少"活性证据"机制**——判定任务死亡看的是墙钟起点而非心跳，判死后原线程还活着，天然催生重复执行与状态互踩。
2. **AI 集成层是全项目最脆弱的一层**：运行时无 failover、无熔断、无退避，prompt 走 argv 有 E2BIG 上限，子进程超时只杀直杀进程留孤儿；且 v1.0.35.19 的 `resolve_active_model` 统一改造漏掉了 `predict_tags_pending`——每日打标 cron 至今打在已 429 死亡的 minimax-cn 上。
3. **一个实测可利用的任意文件读**（SPA fallback 绝对路径拼接）+ 前端 `:href` 无 scheme 白名单，是仅有的两处安全级问题，其余安全面（secrets 不落前端、LLM 输出落库前 redact、CORS 收紧、127.0.0.1 绑定）做得不错。

---

## 二、Ticket 全表

### P0 — 正确性 / 崩溃 / 安全

#### P0-1 【AI 集成】每日打标 cron 打在死亡 provider 上，LLM 参数被静默丢弃
- **证据**: `moni/cron/jobs.py:343-348` — `llm.chat(prompt, model="claude-3-5-sonnet-latest", max_tokens=256, temperature=0.3)`；`moni/llm/client.py:298-302` — 弃用 shim `ChatClient.chat()` 里 `provider = kwargs.get("provider", "minimax-cn")`（硬编码默认），且 `max_tokens`/`temperature` **根本不透传**，被无声丢弃；`model` 传的是 claude 系模型名配 minimax provider 的错配组合。
- **底层影响**: minimax-cn 自 2026-08-24 起 429 配额耗尽（AGENTS.md v1.0.35.13 / v1.0.36 坑#3 实录）→ 工作日 9:25 的 `predict_tags_pending` 每篇文章 2 次尝试全灭，10 篇 = 20 次无效外呼 + cron_runs 每日 failed；自动打标功能静默瘫痪 ≥13 天。同文件 `extract_events_pending` (cron/jobs.py:483-487) 已用 `resolve_active_model`，证明统一入口改造漏掉了这一处——纯一致性修复，风险极低。
- **定级**: P0（每日功能静默全灭 + 配置错配）

#### P0-2 【安全】SPA fallback 路径拼接 → 任意绝对路径文件读（已实测）
- **证据**: `moni/web/server.py:116-126` — `candidate = STATIC_DIR / full_path; if candidate.is_file(): return FileResponse(candidate)`。当 URL 为 `//etc/hosts` 时 starlette `path` converter 给出的 `full_path` 带前导 `/`，`Path("/…/static") / "/etc/hosts"` = `Path("/etc/hosts")`——绝对路径右操作数直接丢弃 base。
- **底层影响**: 本审计对运行中 8900 做了只读 PoC：`GET //etc/hosts` 返回 200 / 213B（真实文件内容），对照组任意不存在路径返回 200 / 216024B（index.html）。相对 `..` 遍历被 uvicorn 的路径规范化挡住（`/../../AGENTS.md` 返回的是 index.html），但绝对路径向量成立——可读仓库根 `.env`（内含微信 RSS JWT）。边界：服务只绑 127.0.0.1，利用面 = 本机任意进程 + 供应链级（本机恶意脚本窃 token）。
- **定级**: P0（实测可利用的信息泄露）

#### P0-3 【数据流/并发】fetch_jobs 僵尸判定看 `started_at` 不看心跳，判死后无 fencing → 重复抓取线程 + 状态互踩
- **证据**: `moni/fetch/jobs.py:421-429` — `sweep_zombies()` 用 `started_at < cutoff`（cutoff = max(timeout)+300, fetch/jobs.py:63-69）判死；`moni/web/routes/fetch.py:327-330` — 超时仅靠 `progress_callback` 内抛 TimeoutError，httpx 挂死不回调则线程永不退出；被 mark_stale 的**活线程**随后 `mark_done()` (fetch/jobs.py:155-162) 无条件覆盖 status；`has_running()` (fetch/jobs.py:394-411) 判 False 后 `/run` 立即可再拉新线程 (routes/fetch.py:224-230)。
- **底层影响**: ① 同源双线程并发外呼（bestblogs 375 feed 场景 = 双倍流量 + SQLite 写竞争）；② 旧线程 `mark_done`/新线程 `update_progress` 对同一行交错写 → UI 状态机脏读闪烁；③ v1.0.35.17 的 `get_stale_threshold()` 是把补丁钉在症状上——阈值永远追着最慢源跑，结构性问题没解。v1.0.7.6-hotfix.1 注释 (routes/fetch.py:209-212) 自己也承认 `has_running` 与 `create` 之间有 race。
- **定级**: P0（并发正确性：重复执行 + 状态互踩）

#### P0-4 【AI 集成】hermes 子进程超时只杀直杀进程，孙进程成孤儿继续烧配额
- **证据**: `moni/llm/client.py:174-184` — `subprocess.run(cmd, capture_output=True, timeout=timeout)` 无 `start_new_session`/`killpg`；`moni/share/worker_llm.py:251-262` — Popen + `proc.kill()` 同病。`fact_check_plus` 走 `-t web` agent 模式 (llm/jobs.py:302)，hermes 内部必 spawn web 工具子进程 = 孙进程。
- **底层影响**: `subprocess.run` 超时只 `Popen.kill()` 直杀 PID（CPython 行为，GitHub issue #81605 同型）；hermes 的孙进程被 reparent 到 launchd 继续跑——孤儿 LLM agent 进程继续消耗 API 配额与 CPU，直到自然结束。8900 进程本身重启频繁（AGENTS.md 记录 PID 14325→48790→9661→34329…），daemon=True 的 fetch 线程死时其 hermes 子进程同样成孤儿。本项目已知的"超时假警报"事件 (v1.0.35.17) 的另一半代价就藏在这里。
- **定级**: P0（资源泄漏 + 配额烧钱，随 LLM 任务量线性放大）

### P1 — 健壮性

#### P1-5 【AI 集成】LLM 运行时零 failover、零熔断、重试无退避
- **证据**: `moni/llm/client.py:50-102` — `resolve_active_model` 只在**解析时**做静态回退链；运行时 429/超时直接 raise，无备选 provider；`moni/llm/jobs.py:75` `MAX_ATTEMPTS=2`，`llm/jobs.py:342-365` 重试是"回 pending 立即重跑"（下轮 poll 间隔 2s），对 429 配额类错误等价于立即重打已死的 provider。
- **底层影响**: AGENTS.md v1.0.36 坑#3/#4 实录：LLM 全 429 时 geo 回填 204 条"假警报完成"。配额耗尽场景下每条任务固定烧 2×调用时长，无降级路径；缺 provider 健康记忆导致每条新任务都重新撞墙。
- **定级**: P1（批量任务在 provider 故障时的雪崩放大）

#### P1-6 【AI 集成】prompt 走 argv，超长文章触发 E2BIG 且异常未捕获
- **证据**: `moni/llm/client.py:145-149` — `cmd = ["hermes", "-z", prompt, …]`（全文正文直接进 argv）；`client.py:181-188` 只捕获 `TimeoutExpired`/`FileNotFoundError`。macOS 单参数上限 ~256KB（ARG_MAX 总量 1MB），bestblogs 全文导入的长文可直接越限 → 裸 `OSError: [Errno 7] Argument list too long`，调用方按 generic Exception 处理，日志无归因。
- **底层影响**: 长文类 LLM 任务（summary/fact_check_plus 都拼 `raw_content`）在内容超长时 100% 失败且失败原因失真；这是数据量增长的定时炸弹。
- **定级**: P1（边界正确性，数据规模增长必触发）

#### P1-7 【状态管理】llm_jobs 认领是 SELECT→UPDATE 竞态窗口，且 `running` 行无僵尸回收
- **证据**: `moni/llm/jobs.py:234-257` — 注释自认 "Two workers would race here, but we run exactly one per process"；但 scheduler 是独立进程 (cron/scheduler.py:3-7 docstring)、8900/8901 双端口常并存、CLI 也会跑 LLM 批处理 → 单进程假设已被部署形态打破；claim 后进程死亡则行**永久 running**——全模块无任何 sweep（对比 fetch_jobs 有 sweep_zombies）。8900 高频重启下每次重启都制造悬挂行。
- **底层影响**: ① 双 worker 抢单 → 双份 LLM 调用双份计费；② 悬挂 running 行永远不 done 不 retry，UI 永远转圈，只能手工修 DB。
- **定级**: P1（并发正确性 + 可恢复性）

#### P1-8 【数据流】predict_tags 无任务级互斥 + APScheduler 串行长任务挤掉后续触发
- **证据**: `moni/cron/jobs.py:297-307` — 待打标文章查询无 claim/锁，重叠触发会重复处理同一批（双倍 LLM 成本）；`moni/cron/scheduler.py:72-79` — `max_instances=1, misfire_grace_time=60`；`fetch_all_sources` 串行 11 源（bestblogs 预算 1800s，cron/jobs.py:118-210 串行 loop）+ `predict_tags_pending` 每篇最长 300s×10，单实例可占满 30+ min → 下一触发点 misfire 且 grace 只有 60s 直接跳过。
- **底层影响**: 长尾日 fetch_all 未跑完时，9:25 打标 job 被 APScheduler 静默 skip（只在日志里一行 warning），功能"随机失灵"难以归因。
- **定级**: P1

#### P1-9 【安全/前端】动态 `:href` 无 scheme 白名单 — LLM/抓取内容可注入 `javascript:` 链接
- **证据**: `moni/web/static/index.html:3020, 3073`（LLM fact-check evidence URL——最典型的 prompt-injection 注入面）、`:268, 614, 932, 1233, 1397, 2824, 2964` 等十余处 `:href="…" 绑定 DB 字段；Alpine v3 原样置 href，无 scheme 校验；多数未加 `rel="noopener"`。
- **底层影响**: RSS 文章/LLM 输出里持久化一个 `javascript:…` URL，用户在 UI 点击即 stored XSS（同源执行，可打所有 /api）。127.0.0.1 绑定降低了远程面，但内容源本身是**不可信外部输入**。
- **定级**: P1

#### P1-10 【安全】微信 RSS JWT 明文存 `sources.url` 并原样返回前端
- **证据**: `moni/web/routes/config_sources.py:102, 118` — `url=r["url"]` 原样出栈（GET /api/config/sources）；微信聚合 RSS 的 token 嵌在 URL query（AGENTS.md 记载 token=JWT）。`moni/llm/redaction.py` 只保护 LLM 输出落库路径，不覆盖 config API。
- **底层影响**: 任何能打开 8900 UI 的进程/人都拿到长期有效的 RSS 凭据；showcase 生成器已经做了 `token=SHOWCASE` 打码 (AGENTS.md 离线展示节)，说明打码逻辑存在但只应用在 demo——生产 API 反而裸奔。
- **定级**: P1

#### P1-11 【后端】`PUT /api/fetch/sources` 把单源更新扇出到全部源（潜伏雷）
- **证据**: `moni/web/routes/fetch.py:153-163` — `for cfg in sources:` 无任何目标过滤，body 里的 `name/base_url/…` 会应用到 **YAML 里所有 source**；且整个文件读-改-写无锁 (fetch.py:149-167)，并发写丢更新，`open("w")` 非原子（crash 撕裂 YAML）。
- **底层影响**: 当前 SPA 未调用该端点（实测 grep app.js 只用 /run 与 /ping）→ 现阶段是"一触即发的 API 地雷"：任何一次按直觉调用（按 body.name 只想改一个源）= 全源配置损坏。
- **定级**: P1（潜伏正确性 + 非原子写）

#### P1-12 【可观测性】llm_jobs `running` 悬挂无告警 + `except Exception` 181 处吞噬
- **证据**: 全库 181 处 `except Exception`（grep 实测），典型：`moni/fetch/jobs.py:58-59`（`_global_max_timeout` 吞掉一切 → 阈值悄悄回退 600）、`moni/web/routes/fetch.py:825-826`（cron_runs 收尾 UPDATE 失败静默 pass，运行记录永久显示 running）。无 CI 无 metrics，日志是唯一观测面，而日志里大量路径被吞。
- **底层影响**: "假警报/假完成"已是本项目两次 ship 的根因 (v1.0.35.17 / v1.0.36 坑#3)——吞噬型异常是同类事件的温床。
- **定级**: P1（可观测性，但直接对应已发生的生产事故模式）

### P2 — 工程化

#### P2-13 【前端】轮询生命周期与详情竞态四连
- **证据**: `moni/web/static/js/app.js:4296-4303` — `loadLlmJobs` 自续 setTimeout 永不停止（首次访问 LLM tab 后每 5s 永久打 /api/llm/jobs）；`app.js:3724-3735` — `showArticleDetail` 无 AbortController/时序 guard，快慢响应错序覆盖（全库 fetch 103 处、AbortController 0 处）；`app.js:4388-4396` — fact-check 详情无 job id 比对，A 的证据链渲染进 B 的 modal；`app.js:3723/4227` — `articleEvidenceList` 换文章不重置，先渲染上一篇文章的 LLM 证据；`app.js:186-201` — restartService 轮询 interval 句柄闭包私有，重复点击可叠加并行 poller。
- **底层影响**: 恒定后台流量 + 证据张冠李戴（内容正确性问题，用户可见）。
- **定级**: P2（其中"证据串门"建议提到 P1 边缘）

#### P2-14 【前端架构】CDN Alpine/GSAP 无 SRI、无 vendored 副本 — 离线即白屏 + 供应链 XSS
- **证据**: `moni/web/static/index.html:19, 3772-3773`（jsdelivr alpinejs@3.14.1 + GSAP），`:20-22` Google Fonts；`moni/web/` 下无任何 vendored JS（find 实测）。与项目自己的"单文件离线 demo 交付"约束直接冲突（AGENTS.md v1.0.35.3）。
- **底层影响**: jsdelivr 不可达 = 整个 SPA 无交互（只有静态 DOM）；CDN 投毒 = 管理面板 instant XSS。`integrity=` 都没加。
- **定级**: P2（demo 场景 P1）

#### P2-15 【前端】共享 confirm modal 的 title 走 innerHTML 未转义（潜伏 sink）
- **证据**: `moni/web/static/js/modal.js:235-246` — `opts.title` 与按钮文案进 `wrap.innerHTML`；现有调用点全传静态字面量，但 `app.js:4605/4625` 已在 message 里插值 provider.name——第一个传动态 title 的人就引爆。代码库自己的 `_escapeHtml` 约定 (app.js:2660-2665) 在此处失守。
- **定级**: P2

#### P2-16 【质量基建】无 CI — 780 tests 只活在开发者本机
- **证据**: `.github/workflows/` 不存在（ls 实测）；pytest 780 pass 依赖本地 3.12 + `-p no:cacheprovider`（AGENTS.md sprint-07 教训）。测试间纪律靠"注释约定"（connection.py:115-119 明说 tests must commit() before HTTP——纪律型设计，无护栏）。
- **定级**: P2

#### P2-17 【后端】f-string SQL 3 处（表名插值）与杂项
- **证据**: `moni/cli/main.py:452, 481, 495` — `f"SELECT … FROM {t}"`，`t` 来自内部白名单枚举而非用户输入，当前不可注入；`moni/fetch/jobs.py:434-441` — `_atomic_update` 列名 f-string 拼接，字段源自内部 dict。均为低危惯性风险，建议加 lint 钉死。
- **定级**: P2

### P3 — 演进

#### P3-18 【架构边界】llm 层反向 import web 层
- **证据**: `moni/llm/jobs.py:60-67` — `from moni.web.routes.fact_check_plus import (DOMAIN_WHITELIST, …)`。domain 层依赖 web 路由层，层级倒挂；fact_check_plus 路由一旦 import llm.jobs（启动 worker）就形成包级循环依赖，现在靠 import 顺序侥幸成立。
- **定级**: P3（演进：白名单/时效逻辑下沉到 moni/llm/ 或 moni/domain/）

#### P3-19 【DB 层】connection.py 伪锁、retired fd 悬挂、过时注释
- **证据**: `moni/db/connection.py:151-158` — `_CREATE_LOCK` 包住的是**线程私有** `_LOCAL` 槽位，锁本身无竞争可保护（伪锁，纯开销）；`connection.py:146-150` — 路径自愈把**未关闭**的旧连接塞进 `_RETIRED` (maxlen=8)，最多 8 个连接 fd 悬挂到 atexit/逐出；`moni/llm/jobs.py:28-30` — docstring 仍宣称 "check_same_thread=False (the connection module is configured that way)"，与 v1.0.37.5 后的现实 (connection.py:29-30) 直接矛盾——注释即谎言，误导下一个改线程模型的人。
- **定级**: P3（正确性文档问题应尽快改）

#### P3-20 【DevOps】share worker 锁 check-then-act + service restart fire-and-forget
- **证据**: `moni/share/worker.py:54-98` — PID 文件检查与 spawn 之间有 TOCTOU 窗口（bash 侧 flock 兜底，Python 侧无）；`moni/web/routes/service.py:169-175` — restart 是 `sleep 1 && stop && start` 的 detached bash，无健康回滚（start 失败 = 服务下线无人知）。
- **定级**: P3

---

## 附：增量审计发现（同日第二轮只读普查，2026-09-06 PM）

普查手段：`ps`/`crontab`/`launchctl` 进程清点 + `sqlite3 -readonly` 数据分布 + `gh api` 仓库设置。零写入。

#### P0-5【增量】定时任务体系整体缺位 — schedule.yaml 是死配置
- **证据**: 进程表无任何 APScheduler/scheduler 进程；`crontab -l` 为空；`launchctl list` 无 moni 项；`web/server.py:32-37` lifespan 为空（web 进程不拉调度）。DB 实证：`cron_runs` 里 `predict_tags_pending` 与 4 个 `extract_events_pending` **零记录**（schedule.yaml 注册 6 job，5 个自上线从未执行）；`fetch_all_sources` 最近 3 条（09-01 10:06 / 08-26 14:42 / 08-25 08:58）时间不规则且 11 源齐跑，是手动"全部抓取"经 `web/routes/fetch.py:693` 复用同一 job_id 写入。
- **底层影响**: "自动打标/自动事件抽取/定时抓取"的产品承诺当前全靠人点按钮；P0-1 修好后依然没有触发者。数据断窗风险 = 无人点"全部抓取"的节假日全源停摆。
- **附带现场证据**: ① 此刻进程表有 2 个活的 `hermes --provider minimax-cn`（429 死 provider，P0-1/P0-4 正在发生）；② `llm_jobs` failed 73 / (done 98+failed 73) ≈ **43% 失败率**，且 1 条 running 悬挂 >15min（P1-7 现场）；③ `fetch_jobs` stale **187 条**（P0-3 判死机制的累计现场）；④ `pyproject.toml` 无 ruff/mypy 配置（P1-12 无门禁）。
- **定级**: P0（功能系统性缺位）。与 P0-1 合并处理：S1 必须先拍板"定时体系归宿"（APScheduler 常驻进程 / 系统 launchd / 承认手动并删 schedule.yaml）。

---

## 三、Sprint 建议切分

| Sprint | 内容 | 退出标准 |
|---|---|---|
| S1（止血） | **P0-5 定时体系拍板**、P0-1、P0-2、P0-4 + P1-7 的 running 回收 | 定时体系有归宿并落一行决定；打标链路恢复绿；路径遍历 PoC 返 404；超时后无孤儿进程；重启后无悬挂 running |
| S2（并发正确性） | P0-3 lease/fencing、P1-5 failover+breaker、P1-6 stdin、P1-8 misfire | 双击/重启不再产生双线程；provider 挂掉时批量任务 fail-fast 或降级 |
| S3（安全+前端） | P1-9 scheme 白名单、P1-10 url 打码、P2-13/14/15 | XSS 面清零；轮询有 teardown |
| S4（工程化） | P2-16 CI、P1-12 except 治理（ruff BLE001）、P3-18/19 | CI 绿门禁；层界干净 |

阶段二对 **P0-1/P0-4/P1-5/P1-6（合并为 LLM 调用层重构）**、**P0-2**、**P0-3**、**P1-7** 做了架构级交付，见 `PHASE2-REFACTOR.md` 与 `/tmp/sprint-prototypes/`。
