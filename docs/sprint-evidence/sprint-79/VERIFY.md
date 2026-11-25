# Sprint 79 验收记录 — 案例库 2.0 后端

- 日期：2026-11-25
- 分支：`feature/v1.0.131-cases-library`（rebase 后 base `b739650`）
- 范围：迁移 062–068、归一、FTS v2、抽取 cron、四维聚合、案例分享、存量回填。

## 1. 命令与输出

```bash
cd /Users/mt16/.codex/worktrees/1323/moni
PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
  -m pytest tests/test_cases*.py tests/test_case_*.py tests/test_share*.py \
  tests/test_db.py tests/test_cron.py tests/test_cron_registry.py \
  tests/test_xbot_scope*.py -q
# → 383 passed in 40.38s

PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 -m pytest -q
# → 2960 passed, 2 skipped, 2 warnings in 569.72s
```

迁移演练（生产库副本，未动生产库）：

```bash
cp /Users/mt16/dev/moni/data/news.db /private/tmp/case-mig-check/news.db
PYTHONPATH=. python3 -m moni.db.migrate --db /private/tmp/case-mig-check/news.db
# → 061 xbot_scope_admission_tag (v60→61)（main #322）
# → 062 (61→62) / 063 (62→63) / 064 (63→64) / 065 (64→65)
# → 066 (65→66) / 067 (66→67) / 068 (67→68)
# → Migration complete. schema_version = 68
# 副本复算：cases=432 / enterprises=116 / case_categories=8
```

## 2. 数字（可复算）

```sql
SELECT COUNT(*) FROM cases;              -- 432
SELECT MAX(version) FROM schema_version; -- 68
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

注：数字随副本快照时间变化（生产库仍在抓取）。2026-11-25 15:23 的副本为 `174/32`，
同日审计复算的更新副本为 `176/30`；差异来自快照时点，不是代码回归。

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

截图（已入库）：`docs/sprint-evidence/sprint-79/design/cases-v3-detail-1440.png`、`…/cases-detail-tags-1024-scrolled.png`；
原型与评审：`docs/sprint-evidence/sprint-79/design/prototype-v2.html`、`copy-review.md`、`pixel-review-v2.md`。

## 4. failed / skipped

- **failed**：无（最终全量 `2960 passed / 2 skipped`，569.72s）。
- 过程观察：两次较早的全量各出现 1 条不同的进程/信号类 flake
  （`tests/test_llm_desens_wiring_share.py::test_reader_interrupt_abort_flushes_without_partial_placeholder_in_db`、
  `tests/test_worker_lock.py::test_sigterm_kills_blocking_child_that_ignores_term`），
  均单独复跑通过，且与本分支改动无交集；不记为本 PR 回归。
- **skipped（有原因）**：
  - 生产库迁移：需备份 + 当轮授权。
  - 8900 部署 / 重启：需当轮授权。
  - 真实 LLM 抽取质量（Stage A v2 / Stage B）：本轮无 prompt 变更。
  - 前端真实页面接入：本轮只有原型。
  - 案例卡片 PNG、汇率表 069（原计划 068，已被分享表占用）：未实现。

## 5. 独立审计后的修复（round 2）

首轮独立审计结论 `REQUEST CHANGES`，本轮已修：

| 编号 | 问题 | 修法 | 复验 |
|---|---|---|---|
| P0-1 | worker 的 case 分支用未赋值的 `output_path`，任务必失败 | 分支内补 `output_path=...-case_brief_html.html`；**round 3 又发现分支内二次 claim，见 §6** | `test_worker_case_branch_sets_output_path_before_summary` |
| P1-1 | 案例分支晚于 AI 摘要，仍会触发 LLM | 整段前移到 claim 之后、正文读取之前 | 同上（位置断言） |
| P1-2 | 429 也累加 attempts，三次后文章被永久踢出 | 429/配额错误 `increment_attempts=False` | `test_429_does_not_burn_retry_budget` |
| P1-3 | `parse_metric` 把“10 个”记成 USD、“国家”记成机构、“6 个月募资”记成 6 人 | 单位只在数字 ±8 字窗口内推断；国家/月优先；时长丢弃 | `test_context_currency_does_not_leak_into_bare_count`、`test_country_is_not_institution`、`test_duration_is_not_a_metric` |
| P2-1 | 回填把更长的原值改短 | 新展示串短于原值时不覆盖 | `test_shorter_display_does_not_overwrite_richer_amount` |
| P2-2 | `test_share_manifest` 把 `~/dev/moni` 插进 `sys.path`，测试顺序敏感 | 改为本 checkout 路径 | 两文件连跑通过 |
| P2-4 | 文档基线/口径漂移 | 基线改 `5ba7a54`；FTS 重建说明；回填数字标注快照 | 文档 diff |

副本复验（2026-11-25 16:4x，生产库仍为 `schema_version=60`、`cases=432`、无 `case_metrics`）：

```
10 个 → unit=unknown（不再 USD）
覆盖178个国家和 → unit=country
超过55个国家和 → unit=country
6个月募资 → 不产生 metric
全球实时支付49%份额 → cases.amount 保持原值（未被“效率 约49%”覆盖）
```

未修（P2，接受并记录）：

- `case_extract_runs.lease_owner/lease_at` 已建但未使用；当前只有单 scheduler 进程。
- `_generate_case_id` 用 COUNT+1，非内容派生；prompt 升版重抽时可能新增重复案例。

## 6. 独立审计 round 3（rebase 后终审）

第二轮审计复验：429 预算、`parse_metric` 三条、回填不改短、`sys.path` 四条确认修好；
同时指出上表 P0-1 只修了一半，并补充了新的 P1/P2。本轮处理：

| 编号 | 问题 | 修法 | 复验 |
|---|---|---|---|
| P0 | case 分支在任务已 `running` 时再 claim 一次；`task_status_update` 的 UPDATE 带 `WHERE status='pending'`，命中 0 行返回 1，`set -e` 直接终止 worker，任务永久卡 `running` | 删掉 `scripts/default_worker.sh` case 分支内重复的 `task_status_update "$task_id" "running"`（保留进入分支前那次 claim） | 副本库 + 临时队列真跑：`CASE_BRIEF … -> …-case_brief_html.html` / `DONE` / `exit=0` / 6622 bytes / DB `status=done` |
| P1 | 上一版本文件 `VERIFY.md` 声称 P0-1 已修，与实测不符 | 改写本节并补 §6 | 本文 |
| P1 | `test_worker_case_branch_sets_output_path_before_summary` 只做字符串存在性检查，抓不到二次 claim | 增加断言：case 分支区间内 `task_status_update "$task_id" "running"` 出现 0 次 | 同测试 |
| P1 | CI 收集失败：`moni/share/case_brief.py` 依赖 jinja2，而 CI 只装 `pip install -e ".[dev]"`（jinja2 不在依赖里） | 去掉 jinja2，改纯 Python 字符串渲染；产物结构/CSS 不变；删除 `templates/case_brief.j2` | CI 复跑；本地全量见 §1 |
| P2 | `parse_metric` 的 `月` 单位在 ±8 字窗口内优先命中，`2025年11月交易额1.2亿` 被判时长丢弃 | 只认 `个月` | `test_month_in_date_is_not_a_duration` |
| P2 | `pipeline._is_rate_limit_error` 用子串 `quota` 判限流，永久性配额配置错误会无限重试 | **接受并记录**：每批命中即中止，不会烧 token；重试频率受 cron（每日 2 次）约束，暂不加计数器与迁移 | — |

worker 端到端复验（副本库 `schema_version=68`，真实队列 schema）：

```
[09:18:50] worker started — queue=/private/tmp/case-worker-check/queue timeout=90s
[09:18:50] CASE_BRIEF <share_id> -> …-case_brief_html.html
[09:18:50] DONE <share_id> -> …-case_brief_html.html
[09:18:50] worker finished
DB: status=done；产物 6622 bytes
```

迁移重编号：main #322 占用了 `061`，本分支 7 个迁移整体 +1 为 `062–068`，
`_CURRENT_VERSION = 68`；副本 001→068 全链通过。
