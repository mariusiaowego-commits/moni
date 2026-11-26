---
type: sprint
sprint: 26112603
status: 进行中
---

# Sprint 83 · 验收记录

## 1. 命令与输出

```text
$ /usr/local/bin/python3 -m pytest tests/test_cases_pipeline_tagging.py tests/test_stats.py \
    tests/test_cases.py tests/test_cases_taxonomy.py tests/test_cases_p0_v2.py \
    tests/test_cases_v3_api.py tests/test_cli_verify_all.py tests/test_stats_geography_api.py -q
183 passed in 30.61s
```

## 2. UI 实测

不涉及 UI；行为只影响「重抽时人工关键词贴回」的选择与地理分布计数。

## 3. 数字可复算

```sql
-- 归档案例不应有恢复的人工关键词
SELECT COUNT(*) FROM case_keywords k JOIN cases c ON c.case_id = k.case_id
 WHERE c.case_lifecycle = 'archived' AND k.source_type = 'manual_user';
```

## 4. failed / skipped

- 无 failed。
- `skipped`：`ruff check` 本机无 ruff，由 CI 兜底。

## 5. CI

（合并后随 STATUS 回填 run id 与通过数。）
