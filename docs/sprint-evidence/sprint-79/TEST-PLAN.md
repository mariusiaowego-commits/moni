# Sprint 79 测试计划 — 案例库 2.0 后端

- 日期：2026-11-25
- 分支：`feature/v1.0.131-cases-library`
- 范围：迁移 061–067、案例归一、FTS v2、抽取 cron、企业/行业/场景/类别聚合、案例分享、存量回填。

## 1. 先列失败模式，再对应测试

| # | 失败模式 | 后果 | 覆盖 |
|---|---|---|---|
| 1 | 迁移在旧库（无 `cases` / 无 `share_tasks` 扩展）上重跑崩溃 | 升级中断、半迁移 | `tests/test_migration_039_xbot_feed.py`、`tests/test_db.py` |
| 2 | 归一仍把 `pilot/scale/PI` 回落成 `launch/AI` | 类型与动作统计继续失真 | `tests/test_cases.py` |
| 3 | 主体别名叠加（`兴业银行银行`）或金额丢币种（`2.5亿美元→2.5亿元`） | 展示错误、跨币种不可比 | `tests/test_cases.py` |
| 4 | 计数指标被补“元”（`互动日活3000万元`） | 语义错误 | `tests/test_cases.py`、`tests/test_cases_backfill_metrics.py` |
| 5 | <3 字搜索 0 命中，或 `C++` 抛 FTS5 语法错误 | 搜索不可用 | `tests/test_cases_p0_v2.py` |
| 6 | 抽取 cron 重复消费同一篇文章 | 重复花 token、重复案例 | `tests/test_cases_p0_v2.py` |
| 7 | 企业未命中被模糊猜测 | 统计造假 | `tests/test_cases_taxonomy.py` |
| 8 | 分享新增格式破坏现有 7 种格式 | 既有分享回归 | `tests/test_share_manifest.py`、`tests/test_share_cases.py` |
| 9 | 案例简报在 `case_insights` 为空时崩溃 | 分享不可用 | `tests/test_share_cases.py` |
| 10 | 存量回填把证据不命中的幻觉数字写库 | 数据污染 | `tests/test_cases_backfill_metrics.py` |
| 11 | 429 也累加尝试次数，三次后文章永久不再抽取 | 覆盖率静默下降 | `tests/test_cases_p0_v2.py::test_429_does_not_burn_retry_budget` |
| 12 | 单位从同段无关币种推断（`10 个` → USD）、“国家”命中“家” | 指标语义错误 | `tests/test_cases_p0_v2.py::TestParseMetricAndActionSource` |
| 13 | worker 案例分支没有 `output_path`，或晚于摘要生成 | 分享任务必失败 / 偷跑 LLM | `tests/test_share_cases.py::test_worker_case_branch_sets_output_path_before_summary` |

## 2. 入口优先

- 迁移入口：`migrate()`（真实副本库 001→068 全链）。
- 搜索入口：`CaseRepository.search_fts()`（短词/长词/特殊字符）。
- 聚合入口：`GET /api/cases/stats`（TestClient）。
- 分享入口：`POST /api/share/cases` + `write_manifest()` + `moni.share.case_brief` CLI。

## 3. 必跑命令

```bash
PYTHONPATH=. python3 -m pytest tests/test_cases*.py tests/test_case_*.py \
  tests/test_share*.py tests/test_db.py tests/test_cron.py -q
PYTHONPATH=. python3 -m pytest -q
PYTHONPATH=. python3 -m moni.db.migrate --db /private/tmp/<copy>.db
PYTHONPATH=. python3 -m moni.cases.backfill_taxonomy --db <copy> --dry-run
PYTHONPATH=. python3 -m moni.cases.backfill_metrics --db <copy> --dry-run
```

## 4. 不覆盖 / 明确跳过

- 生产库迁移：需备份与当轮授权，本轮 skipped。
- 8900 部署与重启：需当轮授权，本轮 skipped。
- 真实 LLM 抽取质量（Stage A v2 / Stage B）：本轮无 prompt 变更，skipped。
- 前端真实页面接入：本轮只有原型，skipped。
