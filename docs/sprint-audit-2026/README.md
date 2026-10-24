# docs/sprint-audit-2026/ — 架构审计包

> ## 📌 这份文档包的状态（2026-10-23）
>
> 这是 **2026-09-06 一次只读架构审计**的完整归档，外加 **2026-10-23 的二次复核**。
>
> - **审计日**：2026-09-06，分支 `exhaustive-mode`
> - **复核日**：2026-10-23，基线 `main` @ `0f8c056`（v1.0.46）
> - **文中所有 File:Line / 数字都是那两天的快照**，之后代码一直在动 —— 读的时候以**符号名**为准，**不要**照抄行号
> - 当前项目状态看根目录的 [`STATUS.md`](../../STATUS.md) 和 [`USER_GUIDE.md`](../../USER_GUIDE.md)
>
> ⚠️ **复核层本身也被复核过**：本目录的 2026-10-23 复核结论经过独立审计，
> **发现 17 处事实错误并已全部修正**（包括 1 张 P1 工单被查错端点后误判为"不存在"）。
> 修正记录见 [CURRENT-TRIAGE-2026-10-23.md](CURRENT-TRIAGE-2026-10-23.md) 顶部。

## 阅读顺序

**先看事实，再看方案。**

1. **[CURRENT-TRIAGE-2026-10-23.md](CURRENT-TRIAGE-2026-10-23.md)** — 逐条判定 21 张工单
   "现在还在不在 / 还有没有价值 / 下一步做什么"。**这是当前最准的一份**，先读它。
2. **[SUCCESSION-PLAN.md](SUCCESSION-PLAN.md)** — 工程继任指导书：
   五个结构性判断、目标架构、S1-S4 执行蓝图、4 个待拍板项、实施纪律、风险登记簿、"第一个 90 分钟"。
   ⚠️ 这是一份 **2026-09-06 的方案**，S1/S2 的大部分**后来已经落地**了（见 triage），
   而且它的"调度器归宿"主张**已被 2026-10-23 的用户决策取代** —— 顶部有说明。
3. **[BACKLOG.md](BACKLOG.md)** — 21 张工单的完整证据链（File:Line 级），
   含同日第二轮只读普查的增量发现（P0-5 定时体系缺位）。
4. **[PHASE2-REFACTOR.md](PHASE2-REFACTOR.md)** — 4 个核心重构
   （LLM 调用层 / SPA 路径遏制 / fetch 租约 / llm_jobs 原子认领）的 Mermaid 前后拓扑 +
   恶魔代言人 + 数据化收益。
5. **[review-deepseek-flash.md](review-deepseek-flash.md)** — 独立评审报告（DeepSeek-Flash 视角，447 行）。
6. **[review-minimax-m3.md](review-minimax-m3.md)** — 独立评审报告（MiniMax-M3 视角，398 行）。
   > 5 / 6 两份是**视角对照**：同一批工单，两个模型各自独立判定，结论一致的地方可信度高。
7. **[CLEANUP-2026-10-23.md](CLEANUP-2026-10-23.md)** — 2026-10-23 分支/工作树清理记录。
8. **[LOCAL-ARTIFACTS-2026-10-23.md](LOCAL-ARTIFACTS-2026-10-23.md)** — 本地产物清单与去向。

## 复核结论（2026-10-23）

21 张工单的分发（**实测，不是估的**）：

| 状态 | 张数 | 工单 |
|---|---|---|
| 已修复（审计前就在 main） | 8 | P0-2 P0-3 P0-4 P1-5 P1-6 P1-7 P1-10 P2-15 |
| 本轮 PR 修 | 7 | P0-1 P1-8 P1-9 P1-11 P2-14 P2-16 P3-20 |
| 大部分已修 / 部分存在 | 3 | P2-13（生命周期治理）、P3-19（连接层，"注释即谎言"那条还在）、P1-12（告警） |
| 仍在，价值低或已拍板延后 | 3 | P0-5（调度器）、P3-18（llm→web 分层）、P2-17（f-string SQL） |

> 上一版这份 README 写的是"**13 张已在 main 落地**"—— **多算了**：
> 它把 P1-11（当时误判为不适用）和 P1-12（只部分存在）也算成了已落地，
> 还把 P0-1 同时列进"已落地"和"本轮排期"两张表。

### 本轮 PR 映射

| 工单 | 处理方式 | PR | 版本 |
|---|---|---|---|
| P0-1 + P1-8 predict_tags 死 provider / 无锁 | 修 3 处致命缺陷 + 进程内互斥锁 | [#146](https://github.com/mariusiaowego-commits/moni/pull/146) | v1.0.47 |
| P1-9 动态 href 无协议白名单 | `safeHref()` 白名单护栏 | [#147](https://github.com/mariusiaowego-commits/moni/pull/147) | v1.0.48 |
| P3-20 worker 锁 check-then-act | 原子锁 + 如实上报 | [#148](https://github.com/mariusiaowego-commits/moni/pull/148) | v1.0.49 |
| P2-16 无 CI | GitHub Actions pytest 流水线 | [#149](https://github.com/mariusiaowego-commits/moni/pull/149) | v1.0.50 |
| P1-11 `PUT /api/fetch/sources` 扇出 | 删端点 + 封死测试改生产配置 | [#151](https://github.com/mariusiaowego-commits/moni/pull/151) | v1.0.51 |
| P0-5 定时任务体系缺位 | **暂不做**（用户拍板：手动为主，方向 = 独立 launchd 常驻进程，不嵌入 Web） | — | — |
| P3-18 llm → web 分层 | **暂不做**（用户拍板延后，无用户可见价值） | — | — |
| P1-12 / P2-17 | 延后 | — | — |

### 一条方法论教训

P1-11 被判定为"不存在"，是因为审计员**查错了端点** —— 看了
`config_sources.py::update_source`（按 id 改一行，正确），没看
`fetch.py::update_fetch_sources`（真正扇出全源的那个）。

**教训**：判定"某个缺陷不存在"之前，必须把**同名路由的所有实现**都过一遍，
不能只看最像的那一个。

## 关于 `/tmp/sprint-prototypes/`

原 README 提到过 4 个可运行的验证原型，放在 `/tmp/sprint-prototypes/`。

**它们已经不在了** —— `/tmp` 会被系统清理，2026-10-23 清理时随临时目录一并删除。

本目录里**没有任何文档再把原型当"可运行的验收基线"**（旧版有 16 处活引用，
包括给继任者的"第一个 90 分钟"里的操作步骤，已全部改掉）。真正被保留下来的是
**结论与断言的描述**，不是一次性脚本。

原型的关键断言已按语义**重新描述**在
[`review-deepseek-flash.md`](review-deepseek-flash.md) §4 —— 要重写测试时照着它写，
不要去找 `/tmp`。

> 这条本身就是一条教训，已写进 `review-deepseek-flash.md` 的风险登记簿 **R9**：
> **原型放 `/tmp` 会随重启消失**，应该落 `docs/<项目>/prototypes/`。
> 本次审计**没有执行这条教训**（原型已删且未重建），所以实现时按 §4 的描述重写。

## 本目录之外

- 往届（sprint-24 / sprint-25）的验证证据：[`../sprint-evidence/`](../sprint-evidence/)
- 本轮 sprint-23 的历史草案（**从未执行**）：
  [`../plans/2026-09-17-sprint-23-browser-session-lifecycle.md`](../plans/2026-09-17-sprint-23-browser-session-lifecycle.md)
- 浏览器验证脚本（可运行）：`scripts/evidence/s26_safehref_offline_probe.mjs` 等

