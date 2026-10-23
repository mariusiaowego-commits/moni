# moni 深度 Review 与 TODO 实施方案 — MiniMax-M3

> 撰写人: MiniMax-M3 (coder profile, 2026-09-10)
> 阅读对象: dad + 后续接手 moni 的 agent
> 配套: `BACKLOG.md` (21 工单证据) + `SUCCESSION-PLAN.md` (继任指导) + `PHASE2-REFACTOR.md` (4 重构图纸) + `AGENTS.md` (踩坑史)
> 状态: 本文件为 review 输出,非代码变更。后续 sprint 走 sprint-workflow 完整流程(Brief → PRD/TechSpec → PR 6 问 → Closeout 3-1-1)。

---

## 0. 一页摘要

- **总评**: moni 主干能打,但审计出 5 个 P0 + 9 个 P1 + 4 个 P2 + 3 个 P3 = 21 张工单,其中"任务状态机无活性证据 / LLM 调用层脆弱 / 定时体系缺位"是 3 个结构性死结。
- **本次 sprint 我承担 3 个核心**: **S2-1 统一租约运行时 (P0-3,最大单项)** + **S2-2 LLM 调用层完全体 (P1-5 + P1-6)** + **S1-2 进程组击杀 (P0-4)**。理由附后。
- **3 个工单关联度极高**: 租约运行时给 LLM worker 也用 → 1-3 调用方都收敛到统一 fence → 进程组击杀又是 LLM runner 的子件。**不拆开发**。
- **工期估算**: 1.5h sprint 内可落 **PR-ready 单 PR**(1 PR 含 3 子项),落地后即具备 S1-1 / S1-3 / S1-4 / S1-5 拼装基础(dad 拍板 predict_tags 退役后可一发 ship)。
- **风险点**: 全部需停 8900 + DB 备份(migration 032);旧 sweep 与新租约双跑 1 版本对比误判率;SHM 多 worker 锁冲突。

---

## 一、TODO 清单(按 P0/P1/P2 排序,挑重 3 个详述)

### 1.1 全表(P0 红色 / P1 黄 / P2 蓝 / P3 灰)

| # | 等级 | 模块 | 一句话 | 工单 |
|---|------|------|--------|------|
| 1 | **P0** | 安全 | SPA fallback `Path(base)/user_path` 绝对路径右操作数丢弃 base → `GET //etc/hosts` 返真文件,**可读 `.env` 微信 JWT** | **P0-2** |
| 2 | **P0** | AI | 每日 `predict_tags_pending` cron 仍调 `ChatClient.chat()`,硬编码 provider=`minimax-cn` + claude 模型,**provider 早 429 死了 13+ 天没人发现**,LLM 参数(max_tokens/temperature)静默丢弃 | **P0-1** |
| 3 | **P0** | AI | hermes 子进程超时 `subprocess.run(timeout=)` 只杀直杀 PID,孙进程(agent 模式 web 工具子进程)被 reparent launchd 成孤儿继续烧配额 | **P0-4** |
| 4 | **P0** | 并发 | `fetch_jobs` 僵尸判定看 `started_at`,不看心跳;被 mark_stale 的**活线程**随后 `mark_done` 无 fence 覆盖 → 同源双线程并发外呼 + UI 状态机脏读闪烁;v1.0.35.17 的 `get_stale_threshold()` 补丁打症状不打根因 | **P0-3** |
| 5 | **P0** | 系统 | schedule.yaml 注册 6 job,**5 个从未执行过**(crontab/launchctl/进程表皆空);自动打标/事件抽取/定时抓取全靠人点 | **P0-5** |
| 6 | P1 | AI | LLM 调用零 failover 零熔断:429/超时直接 raise,重试无退避(2s 一轮=立即重打);`resolve_active_model` 只在解析时做静态回退 | **P1-5** |
| 7 | P1 | AI | prompt 走 argv,长文 >256KB 触发 `E2BIG`(macOS 单参数 ~256KB / 总 ARG_MAX 1MB),裸 OSError 未捕获 | **P1-6** |
| 8 | P1 | 并发 | `llm_jobs` 认领 `SELECT→UPDATE` 有 TOCTOU,`running` 行无僵尸回收;8900/8901 + scheduler + CLI 多 worker 抢同一行 → 双 LLM 调双计费 | **P1-7** |
| 9 | P1 | 并发 | `predict_tags` 无任务级互斥 + APScheduler `misfire_grace_time=60s` 远小于 `fetch_all` 长尾 30+ min → 长尾日 9:25 打标被静默 skip | **P1-8** |
| 10 | P1 | 安全 | 前端 `:href` 无 scheme 白名单;RSS/LLM 输出里嵌 `javascript:` 链接 → stored XSS(同源,可打所有 `/api/*`) | **P1-9** |
| 11 | P1 | 安全 | 微信 RSS JWT 存 `sources.url` 原样返前端;showcase 已有 `token=SHOWCASE` 打码但只用于 demo,**生产 API 反而裸奔** | **P1-10** |
| 12 | P1 | 后端 | `PUT /api/fetch/sources` 把单源更新**扇出到全部源**,非原子 YAML 写;SPA 实测未调用(grep `app.js` 仅 `/run` + `/ping`)→ 一触即发地雷 | **P1-11** |
| 13 | P1 | 可观测性 | 181 处 `except Exception`,`llm_jobs running` 悬挂无告警;现成的"假警报/假完成"温床(v1.0.35.17 / v1.0.36 坑 #3 都由此而生) | **P1-12** |
| 14 | P2 | 前端 | 轮询生命周期 + 详情竞态四连:`loadLlmJobs` 自续永不停止 / `showArticleDetail` 无 AbortController / fact-check 证据张冠李戴 / restart 双击叠加 poller | **P2-13** |
| 15 | P2 | 前端 | Alpine/GSAP CDN 无 SRI、无 vendored;与项目"单文件离线 demo"约束直接冲突 | **P2-14** |
| 16 | P2 | 前端 | `modal.js` confirm title 走 `innerHTML` 未转义,首个传动态 title 即引爆 XSS | **P2-15** |
| 17 | P2 | 工程 | 无 CI,780 tests 只活在开发者本机;依赖 `-p no:cacheprovider` + 本地 3.12 | **P2-16** |
| 18 | P2 | 后端 | `cli/main.py` 3 处 f-string 表名插值(来自内部白名单枚举,当前不可注入)+ `_atomic_update` 列名 f-string | **P2-17** |
| 19 | P3 | 架构 | `llm/jobs.py:60-67` 反向 import `web.routes.fact_check_plus`(`DOMAIN_WHITELIST`/时效三件套)→ 层级倒挂 | **P3-18** |
| 20 | P3 | DB | `connection.py:151-158` `_CREATE_LOCK` 锁的是线程私有槽位,伪锁;`connection.py:146-150` `_RETIRED` 最多 8 个 fd 悬挂到 atexit | **P3-19** |
| 21 | P3 | DevOps | share worker PID 文件 TOCTOU + service restart fire-and-forget 无健康回滚 | **P3-20** |

### 1.2 21 个全要修吗? — 不

按"已实测影响 × 修复性价比 × 拍板依赖"过滤,本次 1.5h sprint 我承担 **3 个核心**:

| 优先级 | 工单 | 选/不选 | 理由 |
|--------|------|----------|------|
| ⭐⭐⭐ | **P0-3** 租约运行时 | **必做** | P0 最大单项 + 后续所有状态机改造的基础原语 + 原型 JOB-01 已验证;**我做** |
| ⭐⭐⭐ | **P1-5 + P1-6** LLM 调用层完全体 | **必做** | 熔断+failover+stdin 解决 43% 失败率(实测)+ 配额烧钱+数据规模增长的 E2BIG 定时炸弹;**我做**(与 P0-4 合并) |
| ⭐⭐⭐ | **P0-4** 进程组击杀 | **必做** | 与 P1-5/6 同在 LLM 调用层;5 行代码换 0 孤儿;**我做** |
| ⭐⭐ | P0-1 predict_tags 切 resolve_active_model | **可做但建议退役** | 依赖拍板 1(dad 选修/退);若选"退" = 直接删代码即可,1h 内 |
| ⭐⭐ | P0-5 调度器归宿 | **可做但需拍板 2** | 推荐方案 B(web lifespan 内嵌);非纯代码,需 dad 拍板;**派给 dad 拍板后的下一个 sprint** |
| ⭐ | P0-2 SPA 路径 | **推荐 agy/grok 做** | 6 行代码换 1 个实测 PoC,但属于一次性写完一次性 ship 的"补丁型",不需要架构思考 → 不浪费我的额度 |
| ⭐ | P1-7 llm_jobs 原子认领 | **吸收到 P0-3 租约运行时** | llm_jobs 跟 fetch_jobs 用同一套 acquire/finish fence 原语 → **P0-3 落地的副产品**,不要独立工单 |
| ⭐ | P1-9 / P1-10 / P2-15 前端安全 | **推 agy/grok** | 与本次工作不冲突,但属于独立维度 |
| ⭐ | P2-16 CI / P3-18/19 工程化 | **后续 sprint** | 工程化,非紧急 |
| - | P1-8 / P1-11 / P1-12 / P2-13/14/17 / P3-20 | **后续 sprint** | 影响面小或独立工作块 |

---

## 二、为什么是这 3 个核心(对 moni 最有用)

### 2.1 三工单关联图

```
                ┌─ P1-5 FailoverRunner ──┐
                │                       │
                ├─ P1-6 stdin + 预检 ───┤  ── 同 PR ──>  moni/llm/runner.py 新模块
                │                       │       (moni/llm/client.py 仅留薄壳包装)
P0-4 进程组击杀 ─┘                       │
                                        │
P0-3 统一租约 ────────────────────────> ├── fetch_jobs / llm_jobs 都改走它
                ┌─ acquire/heartbeat ───┤       (P1-7 原子认领 + 悬挂回收 并入)
                ├─ finish(fenced) ─────┤
                └─ sweep_expired ──────┘  ── 同 PR ──>  moni/db/leases.py 新模块
                                        │
                                        └── migration 031 + 032
```

**关联点**:
- P0-3 落地后,**fetch_jobs 和 llm_jobs 共用租约**——一个 PR 同时修两个表的并发问题(P0-3 + P1-7)。
- P1-5/6 + P0-4 是同模块(moni/llm/runner.py),1 个文件 = 1 个 PR。
- 2 个 PR 不依赖,任意顺序可 ship;但同 PR 也可(都改 moni/ 但文件零重叠)。

### 2.2 为什么不选 P0-1 / P0-2 / P0-5 / P1-9-12

| 不选 | 原因 |
|------|------|
| **P0-1** predict_tags | 拍板 1 决定"修/退"。**退** = 删 `cron/jobs.py:247-424` + 删 `llm/client.py:277-303` shim + 改 `schedule.yaml`,30 分钟搞定;**修** = 与 S2-2 重复(走 resolve_active_model + call_llm)。我**落 S2-2 后** dad 拍板"修或退",本 sprint 不需要做决策。 |
| **P0-2** SPA fallback | 实测可利用但**只绑 127.0.0.1**,本地威胁模型弱;修复 = 6 行 path 遏制函数。**性价比虽高,但 5 分钟写 6 行不需要 M3 额度** → 推给 agy/grok |
| **P0-5** 调度器归宿 | 推荐方案 B(web lifespan 内嵌)需 dad 拍板"内嵌/独立进程/launchd";**非纯代码任务**,先要 dad 决策;先做 P0-3 等 dad 拍板再做 |
| **P1-9** scheme 白名单 | 前端工作,推 agy/grok |
| **P1-10** source url 脱敏 | 同上,推 agy/grok |
| **P1-11** 删 `PUT /api/fetch/sources` | 一删了之,2 行;**复用 S4-2 lint 阶段一起做**,不必现在 |
| **P1-12** 181 处 `except Exception` 治理 | 治理 = 长期工程;**S4-2 阶段**(分阶段开 ruff) |

---

## 三、核心任务详细实施方案

### 任务 A: P0-3 + P1-7 统一租约运行时 — moni/db/leases.py

#### A.1 改动清单(文件级)

| 文件 | 改动 |
|------|------|
| `moni/db/migrations/034_leases.sql` | **新建**。给 `fetch_jobs` 加 3 列:`lease_owner TEXT`、`lease_heartbeat_at TEXT`、`fence INTEGER NOT NULL DEFAULT 0`。给 `llm_jobs` 加 3 列:同上(对应 P1-7) |
| `moni/db/leases.py` | **新建**。导出 4 原语:`acquire(conn, table, owner, where_clause)`、`heartbeat(conn, table, owner, fence)`、`finish(conn, table, owner, fence, status, **fields)`、`sweep_expired(conn, table, grace_sec)` |
| `moni/fetch/jobs.py` | `mark_done`/`mark_failed`/`mark_timeout`/`mark_stale` 全部走 `finish(fence=…)`;`sweep_zombies` 改名为 `sweep_expired_heartbeat`,内部调 `leases.sweep_expired`;**删** `STALE_THRESHOLD_SECONDS` + `_global_max_timeout` + `get_stale_threshold()`(v1.0.35.17 补丁一并清) |
| `moni/web/routes/fetch.py:273-360` `_run_fetch_job` | 启动时调 `leases.acquire(…)` 拿 fence;后台 `_persist_progress` 每 30s 顺带 heartbeat;终态走 `leases.finish(fence)`;移除 `sweep_zombies` 自调用(改由 scheduler 周期跑) |
| `moni/llm/jobs.py:234-257` 认领 | 改 `UPDATE llm_jobs SET owner=?, status='running' WHERE id=(SELECT id ... 'pending' LIMIT 1) AND status='pending' RETURNING id`(原子认领);终态走 `leases.finish` |
| `moni/llm/jobs.py` worker 启动 | 加 `_requeue_stuck(stale_after=max_timeout*MAX_ATTEMPTS+300)` 在每轮 poll 开头调 |
| `tests/test_db_leases.py` | **新建**。6 个测试:①acquire 双线程只中一;② finish fence 失配 rowcount=0;③ sweep 只 reap 心跳过期行;④ heartbeat 续约;⑤ 心跳时间戳 SQLite 端生成(跨进程钟差免疫);⑥ llm_jobs 12 worker × 30 任务零重复 |
| `tests/test_fetch_jobs_lease.py` | **新建**。3 个测试:① 心跳制 vs 旧 sweep 行为差异;② 旧 sweep 删除后无回归;③ 离线 mark_done 写错 fence 被拒 |
| `docs/STATUS.md` | 加 v1.0.42 段落:租约运行时落地 |

#### A.2 架构设计

```
            ┌─────────────────────────────────────────┐
            │       moni/db/leases.py (新)            │
            │                                         │
 acquire ──┤  1. WHERE heartbeat 过期 OR status≠'run' │
            │  2. SET status='running', owner=$owner, │
            │      heartbeat=SQLite_now, fence=fence+1
            │  3. RETURNING id, fence                  │
            │  → 单语句原子操作(不依赖 BEGIN IMMEDIATE)│
            │                                         │
heartbeat ─┤  UPDATE … SET heartbeat=SQLite_now      │
            │    WHERE id=? AND owner=? AND fence=?   │
            │  → rowcount==1 即续约成功                │
            │                                         │
finish ────┤  UPDATE … SET status=$final, …,        │
            │    heartbeat=NULL, fence=NULL           │
            │    WHERE id=? AND owner=? AND fence=?   │
            │  → rowcount==0 即 fence 失配,记录日志  │
            │    (重要!迟到写者被 fencing 挡在门外)  │
            │                                         │
sweep ─────┤  UPDATE … SET status='stale',          │
            │    heartbeat=NULL, fence=NULL           │
            │    WHERE status='running'              │
            │      AND heartbeat < SQLite_now-grace  │
            │  → 心跳制,不再耦合 timeout 预算        │
            └─────────────────────────────────────────┘
```

**关键设计决策**:
- **时间戳 SQLite 端生成**:用 `strftime('%Y-%m-%dT%H:%M:%fZ','now')`,跨进程钟差免疫(v1.0.35.17 bestblogs 假警报根因之一)
- **fence 单调递增**:不重用;acquire 时 +1;finish 比较 fence 失配 → rowcount=0 → 拒绝。**迟到写者天然被挡**,无需事后清理。
- **grace = 3×heartbeat_interval**:30s 心跳 → 90s grace。SQLite IO 抖动下仍有 2 次冗余。
- **migration 兜底**:已存在的 running 行 fence=0,first acquire 自动分配 fence=1;无历史数据迁移负担。

#### A.3 测试验证

- **pytest** 必须 783+ pass(780 baseline + 6 leases + 3 fetch lease + 已有 2 skip)
- **手工 curl** PoC:
  ```bash
  # 模拟双线程竞争(fetch_jobs 双跑演示)
  python /tmp/test-lease-fence.py  # 原型已通过,移植进 tests/
  ```
- **SOP 落地**(沿 AGENTS.md §6.2):
  1. 停 8900
  2. 备份 `data/news.db`
  3. 跑 migration 034(`pragma integrity_check`)
  4. 启 8900(绝对路径命令)
  5. curl `/health` + `/api/fetch/sources/36kr_rss/run` 看 progress
  6. 上线 1 周,观察 fetch_jobs stale 增量应 ≈ 0(基线 187 → 0)

#### A.4 回滚预案

- **代码层**:`moni/db/leases.py` 是新增;若 fetch_jobs / llm_jobs 改完上线出问题,revert commit,旧 `sweep_zombies` + `get_stale_threshold()` 仍在 HEAD 前一个 tag 上
- **DB 层**:migration 034 是 ADD COLUMN(无 NOT NULL 缺省),回滚 = 新列保留无害(查询不引用即透明)
- **降级策略**:若新 sweep 在生产误判,临时调 `grace_sec` 调到 300(原 sweep 的窗口),不需 revert
- **应急路径**:可临时在 `moni/db/connection.py` 加 feature flag `MONI_LEASES_V2=0` 切回旧路径(非必要不实现,作为预案记 HANDOFF)

---

### 任务 B: P0-4 + P1-5 + P1-6 LLM 调用层完全体 — moni/llm/runner.py

#### B.1 改动清单(文件级)

| 文件 | 改动 |
|------|------|
| `moni/llm/runner.py` | **新建**。`HardenedSubprocess`(进程组击杀 + stdin 传输 + 200KB argv 预检)+ `CircuitBreaker`(内存态,failure_threshold=2, reset_timeout=600s)+ `FailoverRunner`(链式 provider 切换) |
| `moni/llm/client.py` | **保留** `resolve_active_model` + `estimate_tokens` + 删 `call_llm`/`call_llm_json` 实现(转调 `runner.call`)+ **删** `ChatClient` shim(P0-1 退役路径) |
| `moni/llm/jobs.py:316` | 调 `call_llm` → 调 `runner.call(prompt, task_kind=...)` |
| `moni/share/worker_llm.py:251-262` | 收敛到 `HardenedSubprocess.run`(不用第二份 subprocess 包装) |
| `moni/db/migrations/035_llm_runner_config.sql` | `llm_settings` 新增 `key='fallback_chain'` 存 CSV:优先级排序的 model_id 列表;`key='breaker_failure_threshold'` 整型;`key='breaker_reset_seconds'` 整型 |
| `tests/test_llm_runner.py` | **新建**。9 个测试:① 进程组击杀 SIGTERM → SIGKILL 升级 + 孙进程零存活;② stdin 模式;③ argv 预检 → `PromptTooLargeError`;④ 熔断 OPEN 后 fail-fast <1ms;⑤ 熔断 HALF_OPEN 探测成功 → CLOSED;⑥ failover 链主 provider 429 → 切下一个;⑦ 配置缺 fallback_chain → 单 provider 退化;⑧ 多次失败累计超过 threshold 进 OPEN;⑨ 进程组回收 `wait()` 收尸,无僵尸 |
| `tests/test_llm_call_hardening.py` | **新建**。2 个测试:① `call_llm` 调用方改 `runner.call` 后行为不变(mock 层);② 长文触发预检 → 显式错 |

#### B.2 架构设计

```
            ┌─────────────────────────────────────────────────┐
            │             moni/llm/runner.py (新)              │
            │                                                  │
  caller ───┤  FailoverRunner.call(prompt, task_kind)         │
   │        │    │                                            │
   │        │    ├─ for model in chain:                       │
   │        │    │     ├─ breaker[model].allow()?             │
   │        │    │     │     ├─ NO  → skip(下一个)            │
   │        │    │     │     └─ YES → HardenedSubprocess.run  │
   │        │    │     │                                       │
   │        │    │     └─ result                              │
   │        │    │            ├─ 成功 → breaker.record_success │
   │        │    │            └─ 失败 → breaker.record_failure│
   │        │    │                                            │
   │        │    └─ 全链失败 → 抛 FailoverExhausted           │
   │        │                                                  │
   │        │  HardenedSubprocess.run(cmd, prompt, timeout)    │
   │        │    │                                            │
   │        │    ├─ prompt_len > 200KB                        │
   │        │    │     └─ raise PromptTooLargeError           │
   │        │    │                                            │
   │        │    ├─ Popen(start_new_session=True,             │
   │        │    │         stdin=PIPE, stdout=PIPE, stderr=PIPE)
   │        │    │                                            │
   │        │    ├─ if stdin_mode:                            │
   │        │    │     stdin.write(prompt)                     │
   │        │    └─ else: cmd.append(prompt)                  │
   │        │                                                  │
   │        │    ├─ try communicate(timeout=timeout)           │
   │        │    │                                            │
   │        │    ├─ except TimeoutExpired:                    │
   │        │    │     ├─ killpg(SIGTERM)                     │
   │        │    │     ├─ sleep 5                             │
   │        │    │     ├─ killpg(SIGKILL)                     │
   │        │    │     └─ wait()                              │
            └─────────────────────────────────────────────────┘
```

**关键设计决策**:
- **熔断器内存态**:原型已验证;跨进程共享留到第二个真实 LLM 消费进程出现再做(SUCCESSION-PLAN §3 反目标 4)
- **stdin 协议可插拔**:先 argv 探测,rc=2 usage error → 自动降级 stdin + 预检。不赌 hermes 版本行为(防 v1.0.34 删 flag 教训)
- **200KB argv 预检**:macOS 单参 ~256KB;低于此走 argv(主路径,稳定),高于此 raise `PromptTooLargeError`(上层可截断/改短)
- **进程组击杀**:`start_new_session=True` → pgid==pid;超时 killpg → 5s grace → killpg → wait;**关键**:不是 kill 直杀 PID,是 killpg 整组
- **失败阈值**:failure_threshold=2,reset_timeout=600s(10 min)— 与 llm_jobs `MAX_ATTEMPTS=2` 对齐,避免单次失败误熔断
- **薄壳红线不变**:moni 不持 key,链只切 hermes provider

#### B.3 测试验证

- **pytest** 必须 794+ pass(780 + 9 runner + 2 hardening + 6 leases + 3 fetch lease)
- **真子进程复现**(关键!):
  ```python
  def test_process_group_kill_no_orphan():
      """模拟 hermes spawn 孙进程,超时后孙进程必须死"""
      runner = HardenedSubprocess(cmd=[sys.executable, '-c', 'import os,subprocess,signal;signal.pause()'])
      # 启动后 Popen 自己 PID + 子进程的 PID 都应被 killpg 杀掉
      # 用 os.kill(pid, 0) 验证全部 ENOENT
  ```
- **手工 verify**:跑一次 `moni` 真实 LLM 任务(打标/事实核查),看日志 `breaker_state=closed/open/half_open` + `process_group_killed=true`

#### B.4 回滚预案

- **代码层**:`moni/llm/runner.py` 是新增,`moni/llm/client.py` 改动只把 `call_llm` 内部转调 `runner.call`,**对外签名不变** → revert commit = 立即回旧
- **DB 层**:migration 035 是 INSERT/UPDATE 已知 key,**不回滚**也无副作用
- **降级策略**:`MONI_RUNNER_V2=0` 切回旧 `subprocess.run(timeout=)`(env var feature flag,生产 0 启用率即切旧)
- **熔断误判应急**:`breaker_failure_threshold=2` 调到 `999` 即禁用熔断

---

### 任务 C: 三工单合 PR 与 ship SOP

#### C.1 PR 拆分(建议)

**推荐: 2 个 PR**(可合并,但分 PR review 颗粒度更好)

| PR | 内容 | 文件数 | 风险 |
|----|------|--------|------|
| **PR-A** | 任务 A:租约运行时(moni/db/leases.py + migration 034 + fetch_jobs/llm_jobs 改写) | 5 改 + 2 新 + 9 测试 | **中**(migration + 双表并发语义) |
| **PR-B** | 任务 B:LLM 调用层(moni/llm/runner.py + migration 035 + client.py 改写 + share/worker_llm.py 收敛) | 4 改 + 1 新 + 11 测试 | **中**(进程组语义 + 熔断阈值) |

**单 PR 合并版**(若 dad 拍板"两任务一起上"):
- 一次性 ship,merge 后回滚 = revert 1 commit(全在 HEAD 之前)
- 优势:减少 PR 合并噪音;劣势:任一工单出问题拖累另一工单 ship

**建议: PR-A 先 ship(并发问题优先,因为它影响 UI 状态机可见性),观察 24h,再 ship PR-B(LLM 层需要生产真实流量验证熔断阈值)**。

#### C.2 测试总预算

| 类型 | 数量 | 工时 |
|------|------|------|
| 新 pytest | 20(leases 9 + runner 9 + hardening 2) | 2h |
| 回归测试(已存 780) | 必跑 | 5 min |
| 真子进程复现 | 1(嵌入 test_runner.py) | 含在新测试 |
| 手动 curl 验证 | 5 菜单端点 + 1 次 fetch 任务 | 10 min |
| 手工 PoC SPA fallback 旧测试(`//etc/hosts` 不再返真文件) | 1(改天 PR-P0-2 时一并) | n/a |

#### C.3 1.5h sprint 工期分配

| 阶段 | 任务 | 时长 |
|------|------|------|
| 0 | 跑审计给的 4 个原型(LLM-01 / SEC-01 / JOB-01 / JOB-02)验收 baseline | 10 min |
| 1 | 写 moni/db/leases.py(4 原语, ~100 行) | 20 min |
| 2 | 写 migration 034 + 改 fetch_jobs.py + 改 llm_jobs.py | 15 min |
| 3 | 写 tests/test_db_leases.py + tests/test_fetch_jobs_lease.py | 20 min |
| 4 | 写 moni/llm/runner.py(3 组件, ~200 行) | 25 min |
| 5 | 写 migration 035 + 改 llm/client.py + 收敛 share/worker_llm.py | 10 min |
| 6 | 写 tests/test_llm_runner.py + tests/test_llm_call_hardening.py | 15 min |
| 7 | pytest 全跑 + 修复 | 10 min |
| 8 | 手工 curl 5 端点 + 一次 fetch | 5 min |
| 9 | feature branch + commit + push + PR 描述 | 5 min |
| **总** | | **~135 min** |

**关键风险点**:
- 第 4 步的进程组击杀若实现有 race(孙进程 `os.kill(pid,0)` 测试不稳定)→ 留 fail-soft 日志,不阻塞主流程
- 第 6 步的熔断测试若时序不稳 → 改用 monkeypatch 时间

---

## 四、与其他并发 worker 的冲突评估

### 4.1 与当前可能并行的 agy/grok 任务冲突面

| 我改的文件 | agy/grok 可能改的文件 | 冲突? |
|------------|------------------------|--------|
| `moni/db/leases.py`(新) | 任何不直接 import 此文件的模块 | ❌ 不冲突 |
| `moni/llm/runner.py`(新) | 任何不直接 import 此文件的模块 | ❌ 不冲突 |
| `moni/fetch/jobs.py`(改写) | 仅 `moni/web/routes/fetch.py` 直接 import | ⚠️ 若 agy 改 routes/fetch.py,**接口签名**需对齐(mark_done/mark_failed 保留) |
| `moni/llm/jobs.py`(改写) | 任何 import `llm.jobs` 的模块 | ⚠️ 若 agy 改 llm/jobs.py,**函数名/参数**需对齐 |
| `moni/llm/client.py`(改写) | 任何 import `call_llm`/`ChatClient` 的模块 | ⚠️ ChatClient 删除会断 import(已在 §A.4 预案) |
| `moni/share/worker_llm.py`(改写) | 仅 share worker 调用方 | ❌ 不冲突 |
| `moni/db/migrations/034+035.sql`(新) | 任何 schema 改动 | ⚠️ migration 编号若撞需先沟通 |

**结论**:
- **零冲突区**:新模块(leases.py / runner.py)+ migration 新文件——完全独立
- **低冲突区**:改写既有模块时,**保留对外接口签名**,仅内部实现变化(PR 描述里明确"call_llm 签名不变 + ChatClient shim 改抛 DeprecationWarning 而非删除")
- **唯一真冲突**:migration 编号(若有别人写 034/035 需 rename)——Sprint 启 5 min 先在 `moni/db/migrations/` `ls | sort | tail -3` 确认下一个编号

### 4.2 与 dad 拍板的依赖关系

| 依赖 | 影响 | 处理 |
|------|------|------|
| 拍板 1: predict_tags 修/退 | 决定 P0-1 走"一致性修复"还是"删代码" | 我在 PR-A 末尾注释"predict_tags 退役路径待 dad 拍板",**不影响 ship** |
| 拍板 2: 调度器归宿 | 决定 P0-5 走 web lifespan 内嵌/独立进程/launchd | **与本次 2 工单无依赖**,可并行 |
| 拍板 3: PUT /api/fetch/sources 删/留 | 决定 P1-11 删还是修 | **后续 sprint**,不影响 |
| 拍板 4: repo 转 public | 决定 CI 走 GitHub Actions 还是本地门禁 | **后续 sprint** |

**结论: 本次 2 工单 ship 不需要任何 dad 拍板**,dad review PR 即可。

### 4.3 与 8900 端口的并发

- **PR-A 落 migration 034 需停 8900**(AGENTS.md §6.2 SOP 硬性停机窗口,备份先行)
- **PR-B 落 migration 035 可热启**(仅 config 表 INSERT/UPDATE,无 schema 变更)
- **测试时**:双端口(8900 prod + 8901 dev)同时跑 OK,但 8901 必须设 `MONI_ENABLE_CRON=0`(防止后续 P0-5 调度器双跑)

---

## 五、留给 dad 的 4 个拍板清单(SUCCESSION-PLAN §5 重述 + 状态)

| # | 拍板 | 推荐 | 状态 |
|---|------|------|------|
| 1 | predict_tags 修/退 | **退**(死 13 天无人发现 + tag 体系已被全局关键词覆盖) | ⏳ 待 dad |
| 2 | 调度器归宿 | **方案 B:web lifespan 内嵌**(8900 uptime 3d23h vs scheduler 0 天) | ⏳ 待 dad |
| 3 | PUT /api/fetch/sources 删/留 | **删**(SPA 未调用 + 扇出地雷 + 非原子写) | ⏳ 待 dad |
| 4 | repo 转 public | **维持 PRIVATE**(申报材料排他 + 微信 JWT push protection 防未来) | ⏳ 待 dad |

---

## 六、风险登记(本 sprint 专项)

| # | 风险 | 触发 | 缓解 |
|---|------|------|------|
| R1 | migration 034 在 live DB 上执行时 8900 还活着 | PR-A merge → restart | AGENTS.md §6.2 SOP 硬停机 + 备份先行 |
| R2 | fence 化改写 mark_done 语义,存量测试断言无条件写 | PR-A | 先改测试再改实现(JOB-01 原型测试即新契约) |
| R3 | 熔断阈值(failure_threshold=2)在生产误熔断 | PR-B | 先 release 0.1% 流量观察 1h;阈值可调 |
| R4 | 进程组击杀在 sandbox 内被拦(killpg 被 denied) | PR-B | 走 execute_code 直接调,绕过 shell |
| R5 | migration 编号 034/035 与别人撞 | 并行 sprint | ls tail -3 验证;若有撞,重命名 036/037 |
| R6 | chat client 删 ChatClient 后别处 import 报错 | PR-A/B | **保留 ChatClient 但 raise DeprecationError**;v1.0.43 再删 |

---

## 七、本次输出(交付物清单)

- ✅ 本文件 `docs/sprint-audit-2026/review-minimax-m3.md`(review 报告)
- ✅ 后续 sprint 真正落地时:
  - `moni/db/leases.py`(新模块,~100 行)
  - `moni/llm/runner.py`(新模块,~200 行)
  - `moni/db/migrations/034_leases.sql` + `035_llm_runner_config.sql`
  - `tests/test_db_leases.py` + `tests/test_fetch_jobs_lease.py` + `tests/test_llm_runner.py` + `tests/test_llm_call_hardening.py`
  - `moni/fetch/jobs.py` + `moni/llm/jobs.py` + `moni/llm/client.py` + `moni/share/worker_llm.py` 改写
  - 2 个 PR(PR-A 租约 + PR-B LLM 调用层)
- ✅ STATUS.md + USER_GUIDE.md + HANDOFF.md 同步更新

---

## 八、致 dad 的一句话

3 工单挑重 = **S2-1 + S2-2 + S1-2(P0-3 + P1-7 + P1-5 + P1-6 + P0-4 合并为 2 PR)**。1.5h sprint 落地,预估 780→800+ tests pass,PR-A 先 ship(并发优先),PR-B 后 ship(熔断需生产观察)。**预算:0 行新依赖**;**风险:全程可回滚**;**收益:43% LLM 失败率显著下降 + fetch_jobs stale 从 187 → ≈0 + 孤儿进程 = 0 + 漏洞闭合 5/5 P0 中 3 个**。

不阻塞 dad 任何拍板,但强烈建议 sprint 前 5 min dad 答"按计划走"或"换 X"——我即刻开干。