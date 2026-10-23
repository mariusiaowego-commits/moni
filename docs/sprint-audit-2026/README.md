# docs/sprint-audit-2026/ — 架构审计包

给继任者的阅读顺序：

1. **[SUCCESSION-PLAN.md](SUCCESSION-PLAN.md)** — 工程继任指导书（主文档）：
   五个结构性判断、目标架构、S1-S4 执行蓝图、4 个待拍板项、实施纪律、风险登记簿、"第一个 90 分钟"。
2. **[BACKLOG.md](BACKLOG.md)** — 21 张工单的完整证据链（File:Line 级），含同日第二轮只读普查的增量发现（P0-5 定时体系缺位）。
3. **[PHASE2-REFACTOR.md](PHASE2-REFACTOR.md)** — 4 个核心重构（LLM 调用层 / SPA 路径遏制 / fetch 租约 / llm_jobs 原子认领）的 Mermaid 前后拓扑 + 恶魔代言人 + 数据化收益。
4. **[CURRENT-TRIAGE-2026-10-23.md](CURRENT-TRIAGE-2026-10-23.md)** — 2026-10-23 在 `main` (v1.0.45) 上的**只读复核**：逐条判定上面 21 张工单"现在还在不在 / 还有没有价值 / 下一步做什么"。
5. **[CLEANUP-2026-10-23.md](CLEANUP-2026-10-23.md)** — 2026-10-23 分支/工作树清理记录。
6. **[LOCAL-ARTIFACTS-2026-10-23.md](LOCAL-ARTIFACTS-2026-10-23.md)** — 本地产物清单与去向。

## 复核结论（2026-10-23）

按 `CURRENT-TRIAGE-2026-10-23.md` 的复核，21 张工单里 **13 张已在 main 落地**（P0-2/3/4、P1-5/6/7/10、P2-13/15、P3-19 等），
剩下仍有价值且已在本轮排期处理的：

| 工单 | 处理方式 | PR |
|---|---|---|
| P0-1 + P1-8 predict_tags 死 provider / 无锁 | 修 3 处致命缺陷 + 进程内互斥锁 | #146 |
| P1-9 动态 href 无协议白名单 | `safeHref()` 白名单护栏 | #147 |
| P2-14 CDN 无自托管 | Alpine/GSAP 本地 vendor | #147 |
| P3-20 worker 锁 check-then-act | 原子锁 + 如实上报 | #148 |
| P2-16 无 CI | GitHub Actions pytest 流水线 | #149 |
| P0-5 定时任务体系缺位 | **暂不做**（用户拍板：手动为主，方向 = 独立 launchd 常驻进程，不嵌入 Web） | — |
| P3-18 llm → web 分层 | **暂不做**（用户拍板延后，无用户可见价值） | — |
| P1-11 / P1-12 / P2-17 | 延后或判定为不适用 | — |

> 原 README 里提到的可运行原型 `/tmp/sprint-prototypes/` 已在 2026-10-23 清理时随临时目录一并删除（`/tmp` 会被系统清理，原型不具备长期参考价值）。
> 真正被保留下来的是本目录里的**结论与证据**，不是一次性脚本。
