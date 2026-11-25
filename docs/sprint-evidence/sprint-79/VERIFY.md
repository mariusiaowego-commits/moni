# Sprint 79 验收记录 — 案例库 2.0 后端

- 日期：2026-11-25
- 分支：`feature/v1.0.131-cases-library`（base `5ba7a54`）
- 范围：迁移 061–067、归一、FTS v2、抽取 cron、四维聚合、案例分享、存量回填。

## 1. 命令与输出

```bash
cd /Users/mt16/.codex/worktrees/1323/moni
PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
  -m pytest tests/test_cases*.py tests/test_case_*.py tests/test_share*.py \
  tests/test_db.py tests/test_cron.py tests/test_cron_registry.py \
  tests/test_migration_039_xbot_feed.py -q
# → 307 passed in 32.87s

PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 -m pytest -q
# → 2880 passed, 2 skipped, 2 warnings in 523.78s
```

迁移演练（生产库副本，未动生产库）：

```bash
cp /Users/mt16/dev/moni/data/news.db /private/tmp/moni-cases-verify-065.db
PYTHONPATH=. python3 -m moni.db.migrate --db /private/tmp/moni-cases-verify-065.db
# → 061 (v60→61) / 062 (61→62) / 063 (62→63) / 064 (63→64)
# → 065 (64→65) / 066 (65→66) / 067 (66→67)
# → Migration complete. schema_version = 67
```

## 2. 数字（可复算）

```sql
SELECT COUNT(*) FROM cases;              -- 432
SELECT MAX(version) FROM schema_version; -- 67
SELECT COUNT(*) FROM enterprises;        -- 116
SELECT COUNT(*) FROM enterprise_aliases; -- 154
SELECT COUNT(*) FROM case_categories;    -- 8
SELECT COUNT(*) FROM case_scenarios;     -- 10
SELECT COUNT(*) FROM case_extract_runs;  -- 273
```

回填（副本）：

```bash
python3 -m moni.cases.backfill_taxonomy --db <copy> --dry-run
# enterprise_matched=217 enterprise_unmatched=215
# by_industry: 其他 224 / 银行 121 / 互联网 83 / 保险 2 / 证券 2
# by_category: product_launch 144 / ai_adoption 72 / capital 65 / partnership 61
#              / procurement 42 / org_strategy 26 / digital_infra 16 / compliance_risk 6
# by_scenario: other 90 / ecny_stablecoin 76 / llm_agent 73 / cross_border_pay 50
#              / core_infra_cloud 37 / risk_compliance 36 / credit_supply_chain 29
#              / channel_service 15 / wealth_advisory 14 / mobile_digital_pay 12

python3 -m moni.cases.backfill_metrics --db <copy> --dry-run
# total_with_amount=208 matched=174 dropped=32 unparseable=2
```

关键样例（回填后）：`互动日活3000万 → dau / 30000000 / user`；`2.5亿美元 → investment / USD`；
`9.9万亿韩元 → contract_value / KRW`；`约6000万欧元 → deal_value / EUR`。

搜索（副本）：

```python
CaseRepository.search_fts("小程序")      # 2
CaseRepository.search_fts("支付")        # 160（<3 字 LIKE 兜底）
CaseRepository.search_fts("C++")         # 0，不抛 FTS5 语法错误
CaseRepository.search_fts("数字人民币")  # 29
CaseRepository.search_fts("跨境支付")    # 44
```

## 3. UI 实测

本轮 UI 只交付原型（`/private/tmp/moni-cases-design/prototype-v2.html`），**未进真实页面**。
实测视口 1440×900 与 1024×768，Chrome headless：

| 项 | 1440×900 | 1024×768 |
|---|---|---|
| 详情容器 | 1280px（90vw 被 max-width clamp） | 922px |
| 双栏 | 左 794 / 右 486（62/38） | 单栏堆叠 |
| 小节标题 | 结论 / 做法 / 风险与前提 / 原文证据 | 同左 |
| 侧栏顺序 | 基本信息 → 标签 → 相关案例 → 分享 | 同左（滚动可见） |
| 标签 chip | 24px 高、13px 字 | 同左 |

截图：`/private/tmp/cases-v3-detail-1440.png`、`/private/tmp/cases-detail-tags-1024-scrolled.png`。

## 4. failed / skipped

- **failed**：无（最终全量 `2880 passed / 2 skipped`）。
- 过程观察：两次较早的全量各出现 1 条不同的进程/信号类 flake
  （`tests/test_llm_desens_wiring_share.py::test_reader_interrupt_abort_flushes_without_partial_placeholder_in_db`、
  `tests/test_worker_lock.py::test_sigterm_kills_blocking_child_that_ignores_term`），
  均单独复跑通过，且与本分支改动无交集；不记为本 PR 回归。
- **skipped（有原因）**：
  - 生产库迁移：需备份 + 当轮授权。
  - 8900 部署 / 重启：需当轮授权。
  - 真实 LLM 抽取质量（Stage A v2 / Stage B）：本轮无 prompt 变更。
  - 前端真实页面接入：本轮只有原型。
  - 案例卡片 PNG、汇率表 068：未实现。
