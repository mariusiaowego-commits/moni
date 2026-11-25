# Sprint 76 验收记录 — CI 性能预算基线修复

- 日期：2026-11-25
- 分支：`fix/ci-perf-budget-baseline`（base `origin/main` `11dc915`）

## 1. 命令与输出

```bash
/usr/local/bin/python3 -m pytest tests/test_desensitize_core.py -p no:cacheprovider -q
/usr/local/bin/python3 -m pytest tests/ -p no:cacheprovider -q
```

- 脱敏核心：`72 passed`
- 全量：`2818 passed, 2 skipped, 2 warnings`（460.40s）

## 2. UI 实测

不适用：本次无运行时代码、无前端改动。

## 3. 可复算数字

```bash
# 阈值常量（两条用例共用）
rg -n "_PERF_P50_BUDGET_MS|_PERF_P95_BUDGET_MS" tests/test_desensitize_core.py

# 防空跑断言应出现两次（原用例 + dense 用例）
rg -c "masked_total >= 100" tests/test_desensitize_core.py
```

期望：预算常量各 1 处定义、2 处引用；`masked_total >= 100` 命中 2 行。

## 4. failed 与 skipped

- failed：无（本地）。
- skipped：全量里既有的 2 条，与本次无关。
- **硬指标未完成**：CI 必须首次变绿。基线是 main 上 4/4 次跑完全红（`8b0484d` / `687f98b` / `27893c0` / `11dc915`），本 PR 的 CI 结论写入本节后才算闭环。

## 5. 未采纳项（有记录）

- agy review 的 P3（采样窗口内 `gc.disable()`）：不采纳。生产 GC 开着，为 0.1ms 尾部噪声换失真口径不值。
- 复杂度比例校验 `t(12000)/t(6000) ≤ 2.4` 与 `MONI_PERF_STRICT=1`：记为后续改进，不在本次 diff。
