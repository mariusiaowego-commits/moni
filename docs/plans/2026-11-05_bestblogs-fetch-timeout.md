# BestBlogs 抓取任务以 lease heartbeat expired (grace=90s) 异常中止根因分析

> 文档创建日期：2026-11-05  
> 分析目标：解释 2026-11-05 在「抓取管理」对 BestBlogs 触发立即抓取后，任务以 `lease heartbeat expired (grace=90s)` 结束的完整成因链，并给出最小改动修复方案对比与相邻缺陷报告。

---

## 1. 现象

2026-11-05，用户在 Web 端「抓取管理」页面对「BestBlogs 公众号聚合」信源点击「立即抓取」：
1. **任务中断并报错**：抓取任务执行数分钟后未正常完成，最终状态变为 `stale`，错误信息显示为 `lease heartbeat expired (grace=90s)`。
2. **任务关键状态记录**（`fetch_jobs` 表中记录）：
   - 任务 ID：`00651ad1-16f9-407a-a6bb-48bf6a1a3dd9`
   - 信源：`source = 'bestblogs'`
   - 启动时间：`started_at = 2026-11-05T07:54:35.368490+00:00`（本地时间 15:54:35）
   - 结束时间：`finished_at = 2026-11-05T08:00:40.383Z`（本地时间 16:00:40）
   - 运行耗时：`elapsed_seconds = 284`（约 4 分 44 秒）
   - 进度指标：`total_feeds = 375`，`done_feeds = 95`，`current_feed = '少数派'`，`articles_so_far = 475`
   - 租约信息：`fence = 1`，`batch_id` 为空，`lease_owner` 与 `lease_heartbeat_at` 在回收时已被置空（NULL）
3. **入库影响**：本地数据库 `articles` 表中该时间段 BestBlogs 新增文章数为 0 篇，已拉取的 475 篇文章全部丢失。

---

## 2. 证据链

### 2.1 进程更替与意外退出证据
- **系统日志（macOS Unified Log）明确记录旧进程退出与新进程拉起**：
  - `2026-11-05 15:59:22.690 launchd[1] [gui/501/com.moni.web [16247]:] signaled service: Terminated: 15`
  - `2026-11-05 15:59:22.908 launchd[1] [gui/501/com.moni.web [16247]:] exited due to SIGTERM | sent by Python[16247], ran for 156996092ms`
  - `2026-11-05 15:59:22.921 launchd[1] [gui/501/com.moni.web [83307]:] Successfully spawned python3[83307] because inefficient`
- **PID 文件与实际监听 PID 不一致**：
  - 本地文件 `data/.web.pid` 记录的 PID 为 `16247`（修改时间为 11 月 3 日）。
  - `lsof -i :8900` 实际监听进程为 PID `83307`（启动于 15:59:22）。
- **运行日志断层与重建输出**：
  - 在 `/private/tmp/moni-8900-v1.0.52.log` 中，第 22~44 行均为旧进程执行 BestBlogs 抓取时的网络日志（第 44 行为 `[bestblogs] fetch 随机小分队 attempt 1/3 failed ...`）。
  - 第 45~46 行紧接着出现新进程启动时的初始化日志：`[chrome_profile] Using legacy Chrome user-data-dir ... Profile 7 for daemon ...`。
  - 第 47 行记录：`[leases] swept 1 stale row(s) from fetch_jobs`。

### 2.2 租约与心跳执行机制证据
- **心跳守护线程与工作线程生存周期耦合**：
  - 代码位置：`moni/web/routes/fetch.py:350-375`。
  - 单源抓取在 `_run_fetch_job` 中启动后台守护线程 `lease-hb-{source_name}-{job_id[:8]}`，每隔 30s 调一次 `leases.heartbeat(handle)`（`moni/db/leases.py:129`）。
  - 由于心跳线程是旧进程（PID 16247）内部的 Python daemon thread，15:59:22 宿主进程被 SIGTERM 杀死时，该线程被操作系统瞬间销毁。
- **进度保存停滞时间点与进程销毁完全对齐**：
  - 抓取任务在 `moni/fetch/sources/bestblogs.py:185-194` 每次处理完一个 feed 会触发 `progress_callback`，更新 `elapsed_seconds`（`moni/web/routes/fetch.py:390-414`）。
  - 任务记录的最后 `elapsed_seconds = 284`，`15:54:35 + 284s = 15:59:19`，与进程被终止的时间点 `15:59:22` 相距仅 3 秒，证实工作线程在死前仍在正常跑，并未提前报错退出。

### 2.3 被动 Sweep 触发证据
- **轮询触发被动回收**：
  - 代码位置：`moni/fetch/jobs.py:293-296` 与 `moni/fetch/jobs.py:459-465`。
  - 前端 SPA 维持约 5s 轮询，每次调用 `jobs.get(job_id)` 或 `jobs.list_recent()` 均会率先执行 `self.sweep_zombies()`，即 `leases.sweep_expired("fetch_jobs", grace_sec=90)`（`moni/db/leases.py:206`）。
  - 15:59:22 进程终止后，旧进程遗留的心跳时间戳停在约 15:59:10。
  - 80 秒内（15:59:22 ~ 16:00:40），前端轮询虽然执行了 `sweep_expired`，但因未超过 90s grace，行状态保持 `running`。
  - 16:00:40（心跳过去整 90s），前端轮询命中 `lease_heartbeat_at < strftime('now', '-90 seconds')`，`sweep_expired` 执行 UPDATE 将其标记为 `stale`，并填入 `error = 'lease heartbeat expired (grace=90s)'`。

---

## 3. 根因判定

### 3.1 直接原因判定（三种可能性评估）
- **可能性 (a)：进程重启/退出（launchd KeepAlive）导致心跳线程消亡**
  - **判定结论：极强（确证为直接根因）**。
  - **证据支撑**：macOS 系统统一日志证实 `com.moni.web` 原进程 PID 16247 于 15:59:22 收到 SIGTERM 退出，launchd 立即拉起新进程 PID 83307；原进程所有线程（含抓取线程与心跳守护线程）被系统一次性回收；新进程未接管在途任务，导致心跳自然中断。
- **可能性 (b)：心跳线程因单次 SQLite 异常或 `heartbeat()` 返回 False 永久退出**
  - **判定结论：弱（非本次事故触发因素，但属代码既有设计脆弱点）**。
  - **证据支撑**：`moni/web/routes/fetch.py:358-368` 确实存在异常即 `return` 的逻辑缺陷；但若此项先发生而任务继续存活，日志应记录后续 feed 抓取。实际日志停在第 95 个 feed，且最后有效进度时间戳与进程销毁时刻一致（仅差 3s），证明心跳与任务是随进程消亡同时灭失的。
- **可能性 (c)：fence/owner 被并发任务改写**
  - **判定结论：完全排除**。
  - **证据支撑**：数据库中该任务 `fence` 保持为 `1`，`batch_id` 为空；在 `sweep_expired` 介入前无任何其他并发操作更新该任务，租约未被篡夺。

### 3.2 为什么 UI 表现为“超时”？
1. **词义与心智混淆**：
   - `moni/db/leases.py:231` 写入的错误字符串是 `'lease heartbeat expired (grace=90s)'`（心跳租约过期）。
   - 前端 `moni/web/static/js/app.js:1806` 与 `app.js:2127` 将 `stale` 映射为「已中止」，但在结果弹窗面板（`app.js:1825`）将 `data.error` 原样呈现；由于错误文案含 `expired`，且任务耗时接近 5 分钟，用户心智自然理解为抓取超时。
2. **5s 轮询与 90s Grace 造成的“假存活”现象**：
   - 进程在 15:59:22 就已死亡，但因 `sweep_expired` 必须等待 90s grace（`moni/db/leases.py:235`），在 15:59:22 至 16:00:40 之间，前端每 5 秒轮询查询任务依然返回 `status: running`。用户界面继续展示动画和 feed 名字，直到 90 秒后突然变红失败，进一步强化了“卡顿后超时”的主观印象。

### 3.3 网络层 SSL EOF 大量重试的作用界定
- **定性：致慢诱因，但非致命原因（逻辑正交）**。
- **详细机理**：
  1. 微信公众号 RSS 代理站（`wechat2rss.bestblogs.dev`）偶发断连返回 `[SSL: UNEXPECTED_EOF_WHILE_READING]`（`moni/fetch/sources/bestblogs.py:313-320`）。
  2. `moni/fetch/sources/bestblogs.py:329-348` 实现了 3 次重试与阶梯式退避（1.5s、3.0s），所有网络异常均被 `except (httpx.TimeoutException, httpx.TransportError)` 内部消化，未向外冒泡，未打崩工作线程。
  3. 重试拉长了单 feed 耗时（平均 ~3s/feed），导致 284 秒内仅完成 95 个 feed。它使任务在遭遇进程外部退出时恰好处于漫长抓取的中途；但即便网络全绿，只要遇到 15:59 的进程重启，任务同样会因心跳断绝被判定为 `stale`。

### 3.4 用户可见影响总结
1. **任务实际是否还在跑**：**未在跑**。旧进程已死，新进程无后台执行逻辑。
2. **是否会重复启动**：**不会**。`stale` 为终态（`moni/db/leases.py:164`），后台无自动重试调度器；且由于 `has_running('bestblogs')` 检查已解除，用户可随时重新发起抓取。
3. **`stale` 与 `timeout` 的本质区别**：
   - `timeout`：任务进程健康存活，工作线程在超过业务预算时间（BestBlogs 配置为 1800s，`moni/web/routes/config_sources.py:45`）后主动调用 `jobs.mark_timeout()` 优雅自裁（`moni/web/routes/fetch.py:421`）。
   - `stale`：进程在未知状态下异常蒸发，外部通过心跳超时（90s）由读轮询被动收尸。
4. **文章是否已入库**：**0 篇入库**。
   - `moni/fetch/sources/bestblogs.py:163-221` 采用“全部 feed 抓取至 `all_articles` 内存列表后再统一交付基类 `dedupe_insert()` 入库”的模式（`moni/fetch/base.py:263`）。
   - 抓取中途进程夭折，内存暂存的 475 篇文章随之灭失，未触发过数据库插入。

---

## 4. 修复方案对比

### 方案 1：心跳线程容错防抖 + 服务优雅停机（最小改动，推荐）
- **修改位置**：
  1. `moni/web/routes/fetch.py` 的 `_heartbeat_loop`（约 352~369 行）。
  2. `moni/web/server.py` 的 FastAPI lifespan context manager。
- **改动说明**：
  1. **心跳重试机制**：心跳线程遇到异常不直接 `return` 退出；增加 `consecutive_failures` 计数器，仅在连续失败 >= 3 次（即持续 90 秒均无法连接数据库）时才退出。单次 `sqlite3.OperationalError` 仅记警告并等待下个周期。
  2. **优雅停机通知**：在 FastAPI shutdown 事件中，查询当前进程持有的 `running` 状态 fetch jobs，显式将其标记为 `failed`（error 写入 `'Service shutting down / restarting'`），避免让用户在重启后等待 90s grace。
- **失败模式**：若宿主被 `kill -9` 强杀，shutdown hook 无法执行，仍走 90s grace 的 `sweep_expired` 兜底。
- **回归风险**：极低。不改动数据表结构，不影响任务正常入库流程。
- **需补测试**：
  - `tests/test_fetch_leases.py`：测试心跳线程模拟数据库单次抛出 `database is locked` 时不会自杀，下一次循环续约成功。

### 方案 2：分批流式入库（Chunked Ingestion）+ 心跳增强
- **修改位置**：
  1. `moni/fetch/sources/bestblogs.py` 的 `fetch()`（约 168~200 行）。
  2. `moni/fetch/base.py` 的 `BaseFetcher.run()`（约 143~270 行）。
- **改动说明**：
  - 引入分批入库回调机制。针对 BestBlogs 这种超大 Feed 集合（375 feeds），每抓取满 N 篇（例如 50 篇）或每完成 10 个 feed，即刻调用 `dedupe_insert()` 刷盘入库，并提交数据库事务。
- **失败模式**：若中途进程退出，已完成的分批文章已落库；重新发起抓取时由 URL 唯一约束去重跳过。
- **回归风险**：中。需要调整 `BaseFetcher` 结果统计数据（`inserted / deduped` 累加逻辑）与日期过滤的边界。
- **需补测试**：
  - `tests/test_bestblogs_fetch.py`：模拟在抓取第 50 个 feed 时抛异常中断，断言前 49 个 feed 的文章已存在于 `articles` 表中。

---

## 5. 验证计划

> 验证方案均基于只读分析与针对性自动化测试，不触碰生产 8900 端口。

### 5.1 自动化测试命令
在本地使用规范 Python 3.12 运行针对租约与抓取控制器的单测集：
```bash
# 1. 验证租约生命周期与 sweep_expired 逻辑
/usr/local/bin/python3 -m pytest tests/test_db_leases.py -p no:cacheprovider -v

# 2. 验证任务管理与状态流转
/usr/local/bin/python3 -m pytest tests/test_fetch_jobs.py -p no:cacheprovider -v

# 3. 验证超时配置解析与回退预算
/usr/local/bin/python3 -m pytest tests/test_fetch_timeout_config.py -p no:cacheprovider -v
```

### 5.2 手工复现与行为验证步骤（需在独立端口 8901 进行）
1. 在 8901 测试服务启动前加载环境变量：
   ```bash
   set -a && source .env && set +a
   scripts/web-8901.sh restart
   ```
2. 触发一次抓取并观察心跳续约：
   ```bash
   # 发起单源抓取
   curl -X POST http://127.0.0.1:8901/api/fetch/sources/bestblogs/run
   # 查询运行中的心跳更新
   sqlite3 data/news.db "SELECT id, status, fence, lease_heartbeat_at FROM fetch_jobs WHERE source='bestblogs' ORDER BY started_at DESC LIMIT 1;"
   ```
3. 模拟进程被杀并验证 90s grace 行为：
   ```bash
   # 获取 8901 进程 PID 并使用 kill -15 杀死
   kill -15 $(cat data/.web-8901.pid)
   # 立即查询（<90s），状态应依然为 running
   sqlite3 data/news.db "SELECT id, status, error FROM fetch_jobs WHERE source='bestblogs' ORDER BY started_at DESC LIMIT 1;"
   # 等待 95s 后再次调用 status 接口触发 sweep
   sleep 95
   curl -s http://127.0.0.1:8901/api/fetch/sources/bestblogs/status
   # 再次查询，状态应转为 stale，错误为 lease heartbeat expired
   sqlite3 data/news.db "SELECT id, status, error FROM fetch_jobs WHERE source='bestblogs' ORDER BY started_at DESC LIMIT 1;"
   ```

---

## 6. 相邻问题：批量抓取（Batch Fetch）完全缺失心跳与租约机制

### 6.1 缺陷事实核对
- **代码位置**：`moni/web/routes/fetch.py:757-865`（`_run_batch_job` 函数）。
- **事实成立**：
  1. 在批量抓取入口，第 787~793 行预先为所有启用的源创建了 `fetch_jobs` 行：
     ```python
     for cfg in enabled:
         source_name: str = cfg["source_name"]
         jid = jobs.create(source_name, batch_id=batch_id)
         jobs.update_progress(jid, current_feed="(排队中)")
         job_ids[source_name] = jid
     ```
  2. `jobs.create()`（`moni/fetch/jobs.py:84`）默认写入 `status = 'running'`，并赋予初始 `lease_heartbeat_at = strftime('now')`。
  3. **核心漏洞**：`_run_batch_job` 内部**没有任何心跳守护线程**，既未对当前执行源进行心跳续约，更未对后续处于 `(排队中)` 的源进行心跳刷新。
  4. **爆炸链**：批量任务依次串行执行；一旦排在前面的源耗时超过 90 秒（例如 36kr 爬虫或公众号抓取耗时 120s），前端发起的任何一次 5s 轮询都会触发 `sweep_expired()`。此时所有排队中的后续源因 `lease_heartbeat_at` 停在 90 秒前，将**全部被批量标记为 `stale`（错误记录为 `lease heartbeat expired`）**！等到前序源跑完轮到它们时，它们在数据库中早已被判定为死尸。

### 6.2 处置建议
> [!IMPORTANT]
> **裁决提醒**：批量路径缺失租约心跳属于严重且高危的相邻缺陷。按照交付规范，本报告明确指出该问题，**需用户裁决是否在后续 PR 中一并修复**，当前分析任务严格遵守 Scope，不自行扩围修改。
