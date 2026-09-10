---
id: 26091002
type: plan
status: 进行中
priority: 高
title: Plan — Sprint 22 统一租约运行时 + LLM 调用层
slug: plan-leases-and-llm-runner
created: 2026-09-10
sprint: 26091001
tags: [plan, sprint-22, leases, llm-runner, p0-3, p1-5]
---

# Plan — Sprint 22

## 1. 架构概览

### 1.1 目标架构(交付后)

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
            │                                         │
sweep ─────┤  UPDATE … SET status='stale',          │
            │    heartbeat=NULL, fence=NULL           │
            │    WHERE status='running'              │
            │      AND heartbeat < SQLite_now-grace  │
            │  → 心跳制,不再耦合 timeout 预算        │
            └─────────────────────────────────────────┘

            ┌─────────────────────────────────────────────────┐
            │             moni/llm/runner.py (新)              │
            │                                                  │
  caller ───┤  FailoverRunner.call(prompt, task_kind)         │
            │    │                                            │
            │    ├─ for model in chain:                       │
            │    │     ├─ breaker[model].allow()?             │
            │    │     │     ├─ NO  → skip(下一个)            │
            │    │     │     └─ YES → HardenedSubprocess.run  │
            │    │     │                                       │
            │    │     └─ result                              │
            │    │            ├─ 成功 → breaker.record_success │
            │    │            └─ 失败 → breaker.record_failure│
            │    │                                            │
            │    └─ 全链失败 → 抛 FailoverExhausted           │
            │                                                  │
            │  HardenedSubprocess.run(cmd, prompt, timeout)    │
            │    │                                            │
            │    ├─ prompt_len > 200KB                        │
            │    │     └─ raise PromptTooLargeError           │
            │    │                                            │
            │    ├─ Popen(start_new_session=True,             │
            │    │         stdin=PIPE, stdout=PIPE, stderr=PIPE)
            │    │                                            │
            │    ├─ if stdin_mode:                            │
            │    │     stdin.write(prompt)                     │
            │    │     cmd.remove('-z') / 用 --stdin flag     │
            │    │     (fallback: argv)                       │
            │    │                                            │
            │    ├─ try communicate(timeout=timeout)           │
            │    │                                            │
            │    ├─ except TimeoutExpired:                    │
            │    │     ├─ killpg(SIGTERM)                     │
            │    │     ├─ sleep 5                             │
            │    │     ├─ killpg(SIGKILL)                     │
            │    │     └─ wait()                              │
            └─────────────────────────────────────────────────┘
```

### 1.2 数据迁移(migration 034)

```sql
-- 034_leases.sql
ALTER TABLE fetch_jobs ADD COLUMN lease_owner TEXT;
ALTER TABLE fetch_jobs ADD COLUMN lease_heartbeat_at TEXT;
ALTER TABLE fetch_jobs ADD COLUMN fence INTEGER NOT NULL DEFAULT 0;
ALTER TABLE llm_jobs ADD COLUMN lease_owner TEXT;
ALTER TABLE llm_jobs ADD COLUMN lease_heartbeat_at TEXT;
ALTER TABLE llm_jobs ADD COLUMN fence INTEGER NOT NULL DEFAULT 0;
CREATE INDEX idx_fetch_jobs_lease_heartbeat ON fetch_jobs(lease_heartbeat_at) WHERE status='running';
CREATE INDEX idx_llm_jobs_lease_heartbeat ON llm_jobs(lease_heartbeat_at) WHERE status='running';
```

回滚 = 新列保留无害(查询不引用即透明)。

### 1.3 LLM 配置(migration 035)

```sql
-- 035_llm_runner_config.sql
INSERT INTO llm_settings (key, value) VALUES ('fallback_chain', '');
INSERT INTO llm_settings (key, value) VALUES ('breaker_failure_threshold', '2');
INSERT INTO llm_settings (key, value) VALUES ('breaker_reset_seconds', '600');
INSERT INTO llm_settings (key, value) VALUES ('use_stdin_transport', '0');
```

`fallback_chain` 为空 = 单 provider 退化(向后兼容)。

## 2. 关键设计决策

### 2.1 时间戳 SQLite 端生成
跨进程钟差免疫(v1.0.35.17 bestblogs 假警报根因之一)。所有比较在 SQL 端完成。

### 2.2 fence 单调递增
- acquire 时 `fence = COALESCE(prev_fence, 0) + 1`
- finish 比较 fence 失配 → rowcount=0 → 拒绝 + 记日志
- 迟到写者天然被挡,无需事后清理

### 2.3 grace = 3×heartbeat_interval
30s 心跳 → 90s grace。SQLite IO 抖动下仍有 2 次冗余。

### 2.4 进程组击杀
`start_new_session=True` → pgid == pid;超时 killpg → 5s grace → killpg → wait。**不是 kill 直杀 PID,是 killpg 整组**。

### 2.5 熔断器内存态
跨进程共享留到第二个真实 LLM 消费进程出现再做(SUCCESSION-PLAN §3 反目标 4)。

### 2.6 stdin 协议可插拔
先 argv 探测(主路径,稳定),hermes 拒绝(rc=2)→ 自动降级 stdin + 200KB 预检。不赌 hermes 版本行为(防 v1.0.34 删 flag 教训)。

### 2.7 薄壳红线不变
moni 不持 key,链只切 hermes provider。

## 3. 文件级改动清单

详见 `sprint-22-leases-and-llm-runner-2026-09-10.md` §3。

## 4. 测试矩阵

| 测试 | 文件 | 断言 |
|------|------|------|
| acquire 双线程只中一 | test_db_leases.py | 2 threads × 100 acquire → 0 重复 |
| finish fence 失配 | test_db_leases.py | rowcount=0 |
| sweep 只 reap 心跳过期 | test_db_leases.py | grace=90s,超时行被 sweep |
| heartbeat 续约 | test_db_leases.py | 30s 内多次 heartbeat,status 仍 running |
| 心跳时间戳 SQLite 端 | test_db_leases.py | strftime('now') 写入 |
| llm_jobs 12 worker × 30 任务零重复 | test_db_leases.py | 无双计费 |
| 进程组击杀零孤儿 | test_llm_runner.py | spawn 孙进程 + 超时 + os.kill(pid,0)=ENOENT |
| stdin 模式 | test_llm_runner.py | argv 探测失败 → 降级 stdin |
| argv 预检 | test_llm_runner.py | >200KB → 显式 PromptTooLargeError |
| 熔断 OPEN | test_llm_runner.py | 2 failures → fail-fast <1ms |
| 熔断 HALF_OPEN → CLOSED | test_llm_runner.py | reset_timeout 后探测成功 |
| failover 链 | test_llm_runner.py | 主 429 → 切下一个 |
| 配置缺 fallback_chain → 单 provider | test_llm_runner.py | 向后兼容 |
| 多次失败累计 | test_llm_runner.py | threshold 边界 |
| 进程组 wait() 收尸 | test_llm_runner.py | 无僵尸进程 |
| call_llm 签名兼容 | test_llm_call_hardening.py | mock 验证 |
| 长文预检 | test_llm_call_hardening.py | 显式错 |

## 5. 部署 SOP(沿用 AGENTS.md §6.2)

```
1. 停 8900 (生产保持停机,本 sprint 不需要启)
2. 备份: sqlite3 data/news.db ".backup data/backup-pre-034-$(date +%Y%m%d).db"
3. 跑 migration 034/035
4. pragma integrity_check 干净
5. (本 sprint 不启 8900,等 dad 拍板 ship 后再启)
```

## 6. 验证清单(自验完成 → PR ready)

- [x] worktree 已建,branch 已就位
- [x] 文档双写完成(vault + docs/plans/)
- [ ] moni/db/leases.py + 4 原语实现
- [ ] moni/fetch/jobs.py + moni/llm/jobs.py 改写
- [ ] migration 034 落库 + pragma integrity_check
- [ ] moni/llm/runner.py + 3 组件实现
- [ ] moni/llm/client.py + moni/share/worker_llm.py 收敛
- [ ] migration 035 落库
- [ ] pytest 全绿(预计 780 → 800+ pass)
- [ ] 真子进程复现测试通过(进程组击杀零孤儿)
- [ ] 手工 curl 5 端点 + 一次 fetch dry-run
- [ ] PR-A 推送 + PR 描述 + 6 问 review packet
- [ ] PR-B 推送 + PR 描述 + 6 问 review packet
- [ ] STATUS.md / USER_GUIDE.md / HANDOFF.md / AGENTS.md 同步更新