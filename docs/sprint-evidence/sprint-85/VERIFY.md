---
type: sprint
sprint: 26112701
status: 进行中
---

# Sprint 85 · 验收记录

## 1. 命令与输出

```text
$ /usr/local/bin/python3 -m pytest tests/ -q -k case
273 passed, 2837 deselected in 33.98s
```

`tests/test_cases_actions_backfill.py` 新增 8 例（关键词优先级 5 + CLI 3）。

## 2. 生产库 dry-run（只读，未写库）

```text
$ python -m moni.cases.backfill_actions --db /Users/mt16/dev/moni/data/news.db
[actions] 候选 432 条，需改动 312 条
  before {"launch": 432}
  after  {"launch": 301, "scale": 23, "pilot": 14, "transform": 78, "exit": 16}
```

## 3. 副本库回填 + UI 实测

```text
sqlite3 prod .backup /private/tmp/ev/news.db      # 在线备份，integrity_check=ok
python -m moni.cases.backfill_actions --db /private/tmp/ev/news.db --apply
[actions] 已写入 312 条
sqlite3 /private/tmp/ev/news.db "SELECT case_action, COUNT(*) ..."
launch|301  transform|78  scale|23  exit|16  pilot|14
```

- 预览：`MONI_DB_PATH=/private/tmp/ev/news.db ./scripts/web-8901.sh start`，
  `/api/cases/stats` → `by_action {exit:16, launch:301, …}`。
- 截图（1440×2000）：`docs/sprint-evidence/sprint-85/render/cases-actions-1440.png`。
- DOM 实测（`--dump-dom`，1440 视口）标签出现次数：
  `试点 7`、`规模化 8`、`转型/整合 7`、`退出 6`（「上线」96 次含摘要正文）。

## 4. failed / skipped

- 无 failed。
- `skipped`：生产库 `--apply` 未执行（等合并后单独授权）。

## 5. CI

合并前填 PR 的 `pytest · py3.12` 结果。
