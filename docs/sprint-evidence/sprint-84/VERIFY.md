---
type: sprint
sprint: 26112604
status: 进行中
---

# Sprint 84 · 验收记录

## 1. 命令与输出

```text
$ /usr/local/bin/python3 -m pytest tests/test_cases_insights.py -q
16 passed in 3.65s
```

```text
$ /usr/local/bin/python3 -m pytest tests/ -q -k case
236 passed, 2801 deselected in 29.59s
```

## 2. 真实试跑（副本库 `/private/tmp/insights-sample.db`，provider=ark / ark-code-latest）

```text
$ python -m moni.cases.backfill_insights --db /private/tmp/insights-sample.db \
    --ids MN-PI-2608-0002,MN-AI-2812-0001,MN-AI-2708-0001 --with-article --apply --show
ok=3 failed=0 insufficient=1  130s
```

- `MN-AI-2812-0001`（汇丰私有化恒生）：做法 4 条、风险 4 条、可复制 4 条，全部来自原文；
- `MN-PI-2608-0002`（兴业信用卡被罚）：做法 5 条、风险 5 条、可复制 5 条；
- `MN-AI-2708-0001`（Cloud Mate 退市公告）：正确返回 insufficient（公告无做法可拆）。

## 3. 成本与时长

- 单条约 40-45s；全量 432 条约 5 小时、输入约 50-60 万 tokens（`--with-article`）。
- 不带原文时全量约 11 分钟，但摘要普遍 <120 字，多数会 insufficient。

## 4. failed / skipped

- 无 failed。
- `skipped`：详情页 UI 与 API 返回未在本 PR（下一 PR）。
- `skipped`：全量回填未跑（需排期）。

## 5. CI

见 PR #350 的 `pytest · py3.12` check（合并前确认）。

## 6. 独立审计修复（commit 待补）

只读子 agent 审计结论「需修（无 critical / 无 SQL 注入）」，本轮已修：

- High：候选去重改为按 `(case_id, prompt_version, input_hash)`，`--with-article`
  与摘要更新后能真正触发重算；旧行标 `stale`，`cases.insight_id` 指向新行。
- Medium：SIGINT 中断返回 130 并打印剩余条数；`--limit` 必须 > 0；
  单条超时默认 300s 并可 `--timeout`；`--db` 先 `set_db_path` 再读模型配置；
  新增失败路径测试（异常→failed 行→重跑自愈）。
- Low：CLI 开 `PRAGMA foreign_keys=ON`；`save_insight` 拒绝空 body 与不存在的
  case；`parse_insight` 只接受字符串字段、拒绝顶层数组/`null`、
  `insufficient:"false"` 不再误判；失败落 `status='failed'` + `error`；
  重跑不覆盖 `created_at`；未知 `--ids` 单独提示。

### 6.1 修复后真实冒烟（副本 `/private/tmp/insights-verify.db`）

```text
$ python -m moni.cases.backfill_insights --db /private/tmp/insights-verify.db
[dry-run] 候选 432 条 | 输入约 113312 tokens

$ python -m moni.cases.backfill_insights --db /private/tmp/insights-verify.db --with-article
[dry-run] 候选 429 条 | 输入约 296549 tokens      # 3 条已有同 hash 的 insight

$ python -m moni.cases.backfill_insights --db /private/tmp/insights-verify.db \
    --ids MN-AI-2812-0001 --with-article --refresh --apply --show
[apply] provider=ark model=ark-code-latest candidates=1
  进度 1/1 ok=1 failed=0 insufficient=0 26s
[apply] done {"ok": 1, "failed": 0, "insufficient": 0, "remaining": 0}
```

432 vs 429 证明去重已按 `input_hash` 生效：不带原文与带原文是两套输入，互不遮蔽。
