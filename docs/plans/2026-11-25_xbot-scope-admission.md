# X-bot scope 准入（traceability 补记，2026-11-25）

> **本文是合并期补记（retro-traceability），不是开工时写的 PLAN。**
> 写于 2026-11-25 16:xx 的 t116u 整合轮，目的是把已发生的决策/验收/证据对齐到一处，
> 便于 reviewer 与下一位 agent 追溯。**不虚构开工记录**：开工时的原始方案文与审查文
> 在 worktree 本地的 `t112u-*` / `t114u-*` / `t116u-*` md 里（未入仓）。
> 相关 PR：[#322](https://github.com/mariusiaowego-commits/moni/pull/322)

## 1. Sprint 归属

- 本轮工作挂在**已有 sprint-54 家族**下，**未新开 sprint、未升产品版本**：
  - 仓内 PLAN：`docs/plans/2026-11-18_sprint-54-xbot-content-quality.md`（sprint-54 劣质内容治理；本 PR 是其「准入层」后续）
  - 旧决策（已标注被本 PR 取代）：`docs/plans/2026-11-12_xbot-scope-thresholds.md`
  - vault：`05-Coding/project-moni/sprints/sprint-54-xbot-content-quality-2026-11-17/`
- 本地 worktree 名 `sprint-55-xbot-scope-admission`、本地分支名 `feature/v1.0.131-xbot-scope-admission`
  都**只是历史命名，不是发版承诺**：`v1.0.131` 已被 PR #313 使用并部署，
  本 PR 的远端分支刻意不带版本号（`feature/xbot-scope-admission`）。

## 2. PLAN（要做什么）

把 `fetch_config.scope`（`wechat_regex` / `xcircle_accounts`）从「展示副本」接成 moni 的**入库准入门**，
不改变采集侧（X-bot 上游）行为。

## 3. 验收标准（AC）与对应测试

| # | 验收标准 | 落点（真实测试名） |
|---|---|---|
| AC-1 | 闸门默认关闭时，入库 URL 集合与改动前**逐条相同** | `tests/test_xbot_scope_admission_ingest.py::test_scope_absent_ingests_identical_pre_change_set` |
| AC-2 | 上游导出 scope 仍不参与准入（只展示） | `tests/test_xbot_scope_api.py::test_scope_admission_behavior_off_vs_on`（第 2 跑：上游导出另一份显示值 → 集合仍相同） |
| AC-3 | 显式存储后**严格多收**，且只多 scope 命中 | `test_xbot_scope_admission_ingest.py::test_scope_stored_admits_scope_only_items` + `test_scope_admission_behavior_off_vs_on`（第 3 跑） |
| AC-4 | 二次运行幂等（`scope_admitted == 0`） | `test_xbot_scope_admission_ingest.py::test_second_run_is_idempotent_and_scope_admitted_zero` |
| AC-5 | 回滚（删键）不删已入库文章 | `test_xbot_scope_admission_ingest.py::test_scope_removed_keeps_imported_articles` |
| AC-6 | 作者屏蔽 / 门槛 / 跨源 URL 去重**不被 scope 绕过** | `tests/test_xbot_scope_gate.py::test_author_block_wins_over_scope` / `test_threshold_wins_over_scope` / `test_url_dedupe_wins_over_scope`（+ ingest 级 `test_author_block_still_drops_even_with_scope`） |
| AC-7 | 空值/非法正则/非 dict ⇒ 该维度不参与（绝不放通） | `test_xbot_scope_gate.py::test_empty_scope_fields_do_not_admit_everything` / `test_invalid_regex_is_inactive_and_does_not_crash` / `test_non_dict_scope_is_ignored` |
| AC-8 | scope-only 命中挂 `xbot-scope`，不冒充领域关键词命中 | `test_xbot_scope_gate.py::test_scope_hits_never_claim_keyword_field` / `test_scope_admitted_hits_carry_scope_hit_field`（ingest）+ `tests/test_migration_061_xbot_scope_admission_tag.py` |
| AC-9 | `stored` / `active` 两个布尔如实区分「没存」与「存了但空」 | `tests/test_xbot_scope_api.py::test_put_accepts_valid_regex_and_scope_stored_turns_true` / `test_scope_admission_inactive_when_only_empty_fields` |
| AC-10 | 非法正则保存即 400 且**库零写入** | `test_xbot_scope_api.py::test_put_rejects_invalid_regex_and_does_not_write_db` |
| AC-11 | `{"scope": null}` = 删键（恢复默认展示），停用准入且**不把兜底写入准入** | `test_xbot_scope_api.py::test_put_scope_null_deletes_stored_scope` / `test_put_scope_null_stops_admission_and_shows_fallback` / `test_put_scope_null_does_not_persist_fallback_as_admission` / `test_put_scope_null_is_noop_when_nothing_stored` |
| AC-12 | 非 xbot 信源发 `scope: null` 仍走整体替换（哨兵不外溢） | `test_xbot_scope_api.py::test_put_scope_null_on_other_source_keeps_replace_semantics` |
| AC-13 | 正则引擎非法入参不炸不放通；2000 字截断 | `tests/test_keyword_match_scope_regex.py` |
| AC-14 | UI 三态状态行 / 保存守卫 / 恢复按钮 / 结果面板漏斗 | `tests/test_xbot_scope_ui.py` |

## 4. 决策记录（decision log）

- **D1 准入是 OR，不是 AND**：离线复算显示存活段里领域词表命中为 **0**；用 AND 等于「接入但一条不多收」。
- **D2 只读 moni 显式存储的 scope**：上游导出值与 `DEFAULT_SCOPE` 兜底**只用于展示**。否则上游一导出就静默改语义，新装库会被一份手抄名单全放通。
- **D3 scope-only 救回挂专用 tag `xbot-scope`**（`keywords='[]'`、不绑源），不冒充领域关键词命中。
- **D4 `{"scope": null}` = 删键 = 恢复默认展示 = 停用准入**；不会把兜底名单/正则写进准入。三个写法语义不同：omit（不动）/ `{}`（存了但空）/ `null`（删键）。
- **D5 空值 ≠ 全放通**：空串 / 空数组 / 非法正则 / 非 dict ⇒ 该维度不参与。
- **D6 迁移号**：061 在开工与每次 merge 前均核过 `origin/main` 最大为 060、无人占用；`_CURRENT_VERSION` 60→61。
- **D7 t116u 条件 GO（2026-11-25）**：**独立 audit 通过后**才允许合并本 PR；合并前必须先整合实时 `origin/main` 并做冲突管理（同 repo 多 actor 并行）；**不部署、不打 tag、不重启 8900、不写生产**。本轮 t116u 只做整合 + 验证 + push 更新 PR。

## 5. 验证证据（按树绑定）

| head | 证据 |
|---|---|
| `55c4fad` | 全量 `2912 passed / 2 skipped / 0 failed`（`/tmp/sprint55-t115u-final2.log`）；同日首跑出现 1 次既有 flake `test_worker_lock::test_sigterm_kills_blocking_child_that_ignores_term`（与本 PR 无 diff 关联），复跑全绿 |
| `a965c87`（PR head，push 时） | 受影响集 `196 passed`；CI `pytest · py3.12` 当时 pending |
| `b47920e`（t116u 整合后 head） | 见 PR 评论 / `t116u-integrate-notes-20261125.md` |

隔离复算（非生产）：`fetched=2860 → parsed/inserted=2763`（xcircle 2686 + wechat 77），在**拷贝副本**上跑，未写生产库、未部署。

## 6. STATUS 对齐

仓内 `STATUS.md` 的发布条目对应 v1.0.130 / v1.0.131 / v1.0.132 的**已发布**内容；
本 PR **未发布**，因此**不写发布条目**（避免把未部署内容写成已上线）。发布记录留到真正 tag+部署那一轮。

## 7. 已知缺口

- vault `sprints/decision-log.md` 的本次追加**未落盘**：Obsidian 写入必须走 `obsidian vault=tqob … silent`，该 CLI 调用在本轮触发审批超时被拦，按规矩不重试、不改磁盘直写。
- 没有独立 `TEST-PLAN` 文档（AC→测试映射已在 §3）。
- UI 契约是静态断言 + 变异负控，**未在浏览器里真点**（P2，留给 reviewer/下一个 sprint）。
