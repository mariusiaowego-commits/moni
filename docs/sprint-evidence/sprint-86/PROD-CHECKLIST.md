---
type: sprint
sprint: 26112702
status: 已完成
---

# Sprint 86 · 生产验证清单（v1.0.147）

发布：`v1.0.147` → `60a914d`；生产 checkout 同 SHA；8900 `com.moni.web` 运行中。
以下每项都在 2026-11-27 实测过，命令可直接复制。

## 0. 前置

```bash
cd /Users/mt16/dev/moni
git rev-parse --short HEAD                 # 60a914d
launchctl print gui/$(id -u)/com.moni.web | rg "state ="   # running
sqlite3 data/news.db ".backup '/private/tmp/news-v1.0.147.db'"
```

- 全量回填终态：`[apply] done {"ok": 401, "failed": 3, "insufficient": 104}`
- 单条耗时约 10–12s，404 条用时 70 分钟。

## 1. 数据完整性

```sql
select count(*) from case_insights;                                   -- 435
select count(*) from case_insights where status='done';               -- 432
select count(*) from case_insights where status='failed';             -- 3
select count(*) from case_insights where status='done'
  and json_extract(body_json,'$.insufficient')=1;                     -- 110
select count(*) from cases where insight_id is not null;              -- 432
```

- 可用解读 322 条（432 done − 110 insufficient），覆盖 432/435 个案例。
- 幂等键无重复：

```sql
select count(*) from (
  select case_id, prompt_version, input_hash, count(*) c
  from case_insights where status='done' group by 1,2,3 having c>1
);                                                                     -- 0
```

## 2. 动作分布

```sql
select case_action, count(*) from cases
where case_action is not null group by 1 order by 2 desc;
```

期望：`launch 330 / transform 49 / scale 22 / pilot 20 / exit 14`（合计 435）。

## 3. API

```bash
curl -s http://127.0.0.1:8900/api/cases/MN-AI-2611-0003 | python3 -m json.tool | head -40
curl -s "http://127.0.0.1:8900/api/cases?limit=1" | python3 -c \
  "import json,sys; i=(json.load(sys.stdin).get('items') or [None])[0]; print(i['insight'])"
```

- 详情：`insight` 含 `what/how/risks/reusable/fit/prompt_version/model/created_at`。
- 列表：`insight` 为 `null`，不触发逐条查询；单条列表 payload ≈ 2 KB。
- 端点：`/cases` 200、`/api/cases?limit=1` 200。

## 4. UI（生产 8900）

```text
1440: .cv-deep-cols = 464.359px 240px；导语 20px；steps 4；scrollWidth 792 = clientWidth 792
1024: .cv-deep-cols = 896.547px（单列）；step p 476px；scrollWidth 961 = clientWidth 961
insufficient（MN-AI-2611-0004）：.cv-deep 不存在，抽屉其余部分正常
JS exceptions: 0
```

截图：

- `docs/sprint-evidence/sprint-86/render/prod-detail-1440.png`
- `docs/sprint-evidence/sprint-86/render/prod-detail-1024.png`
- `docs/sprint-evidence/sprint-86/render/prod-check-1440.png`（本轮复检）

手工路径：`/cases` → 点开任一卡片 → 滚动到「深度解读」→ 对照三级层级（导语 / 编号做法 / 右侧风险）。

## 5. 分享简报

```bash
cd /Users/mt16/dev/moni && python3 - <<'EOF'
import sqlite3, json
from moni.share.case_brief import _render_insight
db = sqlite3.connect("file:data/news.db?mode=ro", uri=True)
for cid, body in db.execute("select case_id, body_json from case_insights where status='done' order by id desc limit 3"):
    html = _render_insight(json.loads(body))
    print(cid, len(html), 'insight-box' in html)
EOF
```

- 完整解读 → 含 `insight-box` 与「一句话结论」。
- `insufficient` → 返回空串，简报不出现空框。
- 生成路径未改：仍是 `POST /api/share/cases` → `case_brief`，与常规简报同一管道。

## 6. 回归点

- 动作筛选现在有 5 个可选值，不再是单一 `launch`。
- `/api/cases` 列表体积未因 insight 膨胀。
- 静态资源：`main.css?v=20261127004`、`cases.js?v=20261127002`（以 `index.html` 实际值为准）。

## 7. 失败与跳过

### failed（3 条，可重跑）

| case | 原因 |
|---|---|
| `MN-AI-2608-0002` | empty/invalid JSON reply |
| `MN-AI-2607-0092` | empty/invalid JSON reply |
| `MN-AI-2605-0005` | hermes failed (failover exhausted) |

重跑：`python3 -m moni.cases.backfill_insights --db data/news.db --ids <id> --with-article --apply`

### skipped

- 110 条 `insufficient`：原文只有标题/短讯，模型判定证据不足，属预期，不重跑。
- 移动端（<768px）未做逐项视觉验收。
- 未做全量 435 条人工抽检；只抽检 1440 / 1024 与 insufficient 三种状态。

## 8. 回滚

- 代码：`git checkout v1.0.146` 后重启 8900（上一版不含深度解读 UI）。
- 数据（仅本次回填）：先备份，再

```sql
update cases set insight_id = null where insight_id is not null;
delete from case_insights where prompt_version = 'v1';
```

- 动作回填按 (action, source) 可重算，回滚用备份库覆盖。
