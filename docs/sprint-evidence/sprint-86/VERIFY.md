---
type: sprint
sprint: 26112702
status: 进行中
---

# Sprint 86 · 验收记录

## 1. 命令与输出

```text
$ /usr/local/bin/python3 -m pytest tests/test_cases_insight_api.py tests/test_share_cases.py -q
15 passed in 2.59s

$ /usr/local/bin/python3 -m pytest tests/ -q -k "case or share" -p no:cacheprovider
405 passed, 2708 deselected in 51.16s
```

新增 `tests/test_cases_insight_api.py` 6 例：指针命中 / 无 insight / stale 指针回退 /
双 done 时指针优先 / insufficient 透传 / 脏 JSON 降级。`tests/test_share_cases.py`
+2 例：v1 键渲染 / insufficient 不渲染。

agy 复审（只读）提 2 条 P1，均已修：

- 原「stale 指针回退」用例把指针指向 done 行，实际没测回退；改为指向 stale 行，
  并补「双 done 时指针优先」。
- `how` / `risks` / `reusable` / `fit` 部分为空时 240px 空列与孤立分隔线；改为
  `cvHasInsightSide()` + `.cv-deep-cols.is-single`。

## 2. 真实回填（副本库 `/private/tmp/ev/news.db`）

```text
$ python -m moni.cases.backfill_insights --db /private/tmp/ev/news.db \
    --ids MN-AI-2611-0007,MN-AI-2611-0006,MN-AI-2611-0005 --with-article --apply
ok=3 failed=0 insufficient=2  32s

$ ... --ids MN-AI-2611-0004,MN-AI-2611-0003 --with-article --apply
ok=2 failed=0 insufficient=1  20s
```

`MN-AI-2611-0005`（混元 Hy4）与 `MN-AI-2611-0003`（招行信用卡接入小微）产出完整解读；
短讯类案例正确返回 insufficient。

## 3. UI 实测（8901，副本库）

- 截图：`docs/sprint-evidence/sprint-86/render/detail-insight-1440.png`、
  `detail-insight-1024.png`。
- 1440 computed：`.cv-deep-cols` → `464.359px 240px`；`.cv-sheet-main`
  `scrollWidth 792 = clientWidth 792`（无横向溢出）。
- 1024 computed：`.cv-deep-cols` → `896.547px`（单列）；`.cv-deep-step p` 476px、
  `.cv-deep-list` 442px（行宽 ≤34 字）；`scrollWidth 961 = clientWidth 961`。
- 字号：导语 `20px/600`、编号 `12px`、正文 `14px`、侧栏 `13px`（pixel 走查后统一）。
- 空态实测（只注入 `what`）：`.cv-deep-cols` = `cv-deep-cols is-single`，
  `grid 728.359px` 单列，侧栏 `display:none`、`border-left:0`。
- 渲染字段：lead 29 字、steps 2、risks 3、reusable 2、fit 26 字，全部来自真实 LLM 输出。

## 4. 静态资源

- `css/main.css?v=20261127002`、`js/cases.js?v=20261127002`（在 #358 的 001 之上再 bump）。

## 5. failed / skipped

- 无 failed。
- `skipped`：生产 8900 未重启、生产库未回填（等合并授权）。
- `skipped`：全量 432 条回填未跑（约 5 小时，单独排期）。
