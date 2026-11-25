# CI 性能预算基线修复（Sprint 76）

- 日期：2026-11-25
- 分支：`fix/ci-perf-budget-baseline`（base `origin/main` `11dc915`）
- 类型：仅测试阈值 + 规格口径，无运行时代码改动

## 1. 问题

main 上 4/4 次跑完的 CI 全红，失败固定为两条：

```
tests/test_desensitize_core.py::test_performance_6000_char_chinese_report_under_2ms
tests/test_desensitize_core.py::test_performance_dense_6000_char_report_p95_under_2ms
```

时间线：`f7765c3 success` → `8b0484d`（#294 引入这两条用例）`failure` → `687f98b` / `c40980a` / `27893c0` / `11dc915` 全 failure。

## 2. 根因

两条用例断言绝对 CPU 耗时 `p95 < 2.5ms` / `p50 < 2.0ms`，阈值取自
`docs/plans/2026-11-24_llm-desens-shield-plan.md` §7.3，在本机（Mac）标定为
p50≈1.7ms / p95≈1.9–2.2ms。`time.process_time_ns()` 只剥离调度等待，不消除单核
IPC / 频率差异；这段负载是正则扫 6,000 字 CJK 文本，跨机线性放大。

实测（同一份代码）：

| 机器 | p50 | p95 |
|---|---|---|
| 本机 | 1.749ms | 1.947ms |
| GitHub runner 第 1 次 | 2.835ms | 2.950ms |
| GitHub runner 第 2 次 | 2.569ms | 2.937ms |

## 3. 处置（A 案，agy 独立 review 后微调数字）

- `tests/test_desensitize_core.py`：抽出 `_PERF_P50_BUDGET_MS = 3.5` / `_PERF_P95_BUDGET_MS = 4.5`，
  两条用例共用；第一条预热补 `assert warm.masked_total >= 100`（与 dense 用例对齐，防空跑绿灯）。
- `docs/plans/2026-11-24_llm-desens-shield-plan.md` §7.3：拆成「开发机基准 2.5ms/1.7ms」与
  「CI 守卫 4.5ms/3.5ms」两档，写明依据与后续复杂度比例校验方案。
- 4.5ms 相对 runner 实测 p95 2.95ms 留 52% 余量；ReDoS / O(N²) 退化是 50–500ms 量级，仍 100% 拦得住。

## 4. 不在本次范围

- 复杂度比例校验 `t(12000字)/t(6000字) ≤ 2.4` 与 `MONI_PERF_STRICT=1` 严格档 —— 记为后续改进。
- GC 采样窗口（agy 的 P3）：不在采样内 `gc.disable()`，避免测量口径脱离生产实际。

## 5. 验收

- 本地：`pytest tests/test_desensitize_core.py -q` → 72 passed；全量见 VERIFY.md。
- CI：本 PR 必须**首次变绿**（对比基线：4/4 全红），这是本次修复的唯一硬指标。
- 之后重跑 PR #312（`gh pr update-branch 312`）并确认绿，再合并。
