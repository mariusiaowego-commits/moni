---
type: sprint
sprint: 26112604
status: 进行中
---

# Sprint 84 · 验收记录

## 1. 命令与输出

```text
$ /usr/local/bin/python3 -m pytest tests/test_cases_insights.py -q
7 passed in 1.28s
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

（合并后回填。）
