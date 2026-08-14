# Sprint 11 — 简报 worker 同步化 PLAN

## Goal

v1.0.24 简报 web 模式把缺事实核查证据的文章**异步 enqueue** (不等待, 简报先出, 证据下次补全)。v1.0.22-29 全家桶后, 用户期望简报成品**直接含最新核查结果**, 不需要第二次生成才看到证据。

本 sprint 把 `_enqueue_missing_web_fact_checks` 从"异步 fire-and-forget"改为"**同步等待 job 完成** (带超时降级)": 简报生成时对缺证据文章同步跑 fact_check_plus, 等 LLM 检索完成, 简报成品直接含证据链 + rollup 计数。

**验收标准**:
1. 简报生成时, 缺 web fact check 的文章被同步核查 (等待完成)
2. 简报成品含最新证据链 (非"下次补全")
3. 超时 (默认 120s) 降级: 该篇标"核查超时, 未含证据", 简报仍生成 (不阻塞)
4. 已有证据的文章不重复核查 (跳过)
5. 全量 pytest 通过, 0 回归

## Blocking Questions

- **Q1: 同步等待方式** — **A** 轮询 llm_jobs 状态 (每 2s 查一次, 等 status=done/failed) / B 直接调 `_run_job` 同步执行 (同进程跑) — 推荐 **A** (不动 job 调度架构, 复用现有队列, 失败诊断照旧)
- **Q2: 超时降级策略** — **A** 总超时 120s, 超时后该篇标"核查超时, 未含证据"继续生成 / B 总超时 60s, 超时整批放弃 — 推荐 **A** (单篇降级, 不整批失败)
- **Q3: 并发数** — **A** 串行 (一篇一篇等) / B 并发 ≤3 篇同时跑 — 推荐 **B** (简报通常 2-5 篇 Pin, 并发 3 篇 × 30-60s ≈ 1-2 分钟, 可接受)
- **Q4: 触发条件** — **A** 默认同步 (简报 web 模式开启同步) / B 加开关 (配置项 toggle, 默认同步) — 推荐 **B** (保底可关, 兼容旧异步路径)

## Assumptions

1. **数据**: `llm_jobs` 表有 status 字段 (pending/running/done/failed), fact_check_plus job 完成后 evidence 已落 `fact_check_evidence` 表
2. **失败**: LLM 检索失败 → job status=failed → 该篇标"核查失败"继续生成 (不阻塞简报)
3. **超时**: 120s 上限, 超过单篇降级
4. **边界**: 简报 0 篇 Pin / 所有文章已有证据 (无需核查) → 不产生 job, 直接生成
5. **环境**: Python 3.12 + SQLite, 复用 `moni.llm.jobs` 现有 job 执行路径
6. **测试**: 单测 mock job 状态轮询 (不真调 LLM), 覆盖 完成/失败/超时 三态
7. **范围**: 只改 `moni/share/enrichment.py` + 测试, 不动 brief_html.py 渲染 (已支持 evidence)

## Plan

1. Modify: `moni/share/enrichment.py:247` `_enqueue_missing_web_fact_checks` — 加 `wait: bool = True` 参数
   - wait=True: enqueue 后轮询 `llm_jobs.status` (每 2s), 等 done/failed/超时
   - wait=False: 保留旧异步路径 (Q4 开关关掉时)
2. Modify: `moni/share/enrichment.py` 新增 `_wait_for_jobs(job_ids, timeout=120, max_concurrent=3)` helper
   - 并发 ≤3 篇同时等, 轮询批量 job 状态
3. Modify: 简报生成入口 (enrichment 主流程) — 调用 `_enqueue_missing_web_fact_checks(wait=True)`, 超时单篇降级
4. Test: `tests/test_brief_web_mode.py` 追加 3 测试 (同步完成 / 同步失败降级 / 同步超时降级)
5. Verify: 全量 pytest 701+ 通过, 8900 重启

**Alternative considered and rejected**: 直接调 `_run_job` 同步执行 rejected — 会绕过 job 队列, 失败诊断 (B5) 和取消机制失效, 且并发难控制.
