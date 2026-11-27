---
type: sprint
sprint: 26112701
status: 已完成
---

# Sprint 85 · 验收记录

## 1. 命令与输出

```text
$ /usr/local/bin/python3 -m pytest tests/ -q -k case
276 passed, 2837 deselected in 33.14s
```

`tests/test_cases_actions_backfill.py` 新增 11 例（关键词优先级 5 + CLI 6）。

## 2. 生产库 dry-run（只读，未写库）

```text
$ python -m moni.cases.backfill_actions --db /Users/mt16/dev/moni/data/news.db
[actions] 候选 432 条，需改动 282 条
  before {"launch": 432}
  after  {"launch": 328, "scale": 22, "pilot": 19, "transform": 49, "exit": 14}
```

## 3. 副本库回填 + UI 实测

```text
sqlite3 prod .backup /private/tmp/ev/news.db      # 在线备份，integrity_check=ok
python -m moni.cases.backfill_actions --db /private/tmp/ev/news.db --apply
[actions] 已写入 282 条
sqlite3 /private/tmp/ev/news.db "SELECT case_action, COUNT(*) ..."
launch|328  transform|49  scale|22  exit|14  pilot|19
```

- 预览：`MONI_DB_PATH=/private/tmp/ev/news.db ./scripts/web-8901.sh start`，
  `/api/cases/stats` → `by_action {exit:16, launch:301, …}`。
- 截图（1440×2000）：`docs/sprint-evidence/sprint-85/render/cases-actions-1440.png`。
- DOM 实测（`--dump-dom`，1440 视口）标签出现次数：
  `试点 7`、`规模化 8`、`转型/整合 2`、`退出 6`（「上线」含摘要正文，未计入）。
- 静态资源版本：`js/cases.js?v=20261127001`。

### 2.1 agy 审计修复（PR #358 第二轮）

- 回填默认跳过 `llm_enum` / `manual` 来源，避免覆盖已确认动作（`--include-llm` 才覆盖）。
- 逐条 UPDATE 捕获 `UNIQUE(article_id, subject, case_type, case_action)` 冲突，
  跳过冲突行不整批回滚。
- 上下文去掉文章标题，与实时 `normalize_case` 同口径，避免合辑标题串味。
- 删除裸词「升级」（只留战略升级/系统升级/架构升级/核心系统升级）；
  `scale` 提到 `pilot` 之前。

## 4. failed / skipped

- 无 failed。
- 生产库 `--apply` 已执行：候选 435 / 写入 282，终态
  `launch 330 / transform 49 / scale 22 / pilot 20 / exit 14`。

## 5. CI

- PR #358 → squash `0685f9f`；CI `pytest · py3.12` success（`6c33455`）。
- 本地 `pytest -q -k "case or share"` → 405 passed。
