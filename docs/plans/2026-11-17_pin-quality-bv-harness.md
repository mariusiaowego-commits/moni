# Sprint 52 · Pin 质量列与业务价值 harness（实现记录）

状态：**开发完成，待独立 audit 与用户授权合并**
sprint id：`26111701`
设计依据：`docs/specs/2026-11-16-pin-quality-bv-harness.md`（v4，已拍板 B/C/C/B）
分支：`feat/pin-quality-bv-harness`（base `60a2a06`，v1.0.95 / Sprint 51）
目标版本：`v1.0.96`

## 1. 做什么

同一套评分模块接两个场景，彼此独立：

| 场景 | 触发 | 是否调模型 | 产出 |
| --- | --- | --- | --- |
| Pin 质量分 | `POST /pin`、Pin 页发现公式过期、正文写回 | 否（纯 Python，<5ms/篇） | `articles.quality_*`，0–90，Pin 列表「质量」列 |
| 通用业务价值 | 生成简报时 | 是（单篇 ≤5 次调用） | `articles.business_value_*`，0–100，简报卡「通用 n」+ ≤3 个维名标签 |

明确不做（V1）：方向分与方向配置/研究（V2 附录）、历史分回填、`GET /api/articles` 暴露质量字段、把质量分交给模型、检查员改总分。

## 2. 架构

新包 `moni/scoring/`，纯函数优先，写库只在一个文件：

```
moni/scoring/
  snapshot.py     ArticleSnapshot + from_row（sqlite3.Row / dict → 不可变输入）
  heuristic.py    pin_heuristic_v2（0–90）+ reading_heuristic + 指纹
  rubric.py       10 维唯一来源（权重合计 100）、锚点、禁词、Rubric 对象
  bv_pipeline.py  取证 → 补抽 → 打分 → 检查 → 重跑；返回 BvResult，不碰 DB
  store.py        质量/业务价值列读写、缓存判定
  direction.py    V2 占位（不建表、不调用）
```

调用方只做三件事：行转 snapshot、调 scoring、写回或渲染。

- `moni/share/enrichment.py`：`_ensure_business_value` 换成 harness（`needs_bv_rescore` 判缓存）；`_ensure_quality` 不再把 `bb_score` 抄进 `quality_score`；三支 `_article_rows` SELECT 补列。
- `moni/share/brief_html.py`：阅读分 chip（BestBlogs 优先，否则 `reading_heuristic`，不读 `quality_score`）；通用分 chip + 维名标签 + 待复核 + 决策窗口过期标注；排序 ready → 旧行 → needs_review → 排除；页脚免责。
- `moni/web/routes/pins.py`：Pin 响应带质量字段；`GET /pinned` 每项带 `quality` dict；新增 `POST /api/articles/pins/quality-refresh`（只处理仍在 Pin 集合内的 id，上限 200，响应 `{updated,unchanged,skipped}`）。
- `moni/web/routes/articles.py` + `moni/db/repository.py`：任何写 `raw_content` 的路径，若文章在 Pin 集合内，同流程重算质量分。
- 前端 `index.html` / `app.js` / `main.css`：Pin tab 新增「质量」列（右对齐、等宽数字），公式不对显示 `…` 并对该 id 发一次 refresh，`failed` 显示 `—`，悬停才出七行公式 tooltip。

## 3. 数据（migration 045）

`moni/db/migrations/045_scoring.sql`，只加列，不回填：

- 质量：`quality_status`、`quality_error`、`quality_formula`、`quality_breakdown_json`、`quality_input_hash`（沿用 `quality_score`、`quality_score_at`）
- 通用：`business_value_status`、`business_value_error`、`business_value_breakdown_json`、`business_value_hash`、`business_value_rubric`（沿用 `business_value_score`、`business_value_note`、`business_value_at`）
- `_CURRENT_VERSION: 44 → 45`；多份写死 44 的测试改为 `>= 44` 或 `== _CURRENT_VERSION`。

状态机：

```
quality:   ready →（公式/指纹变化）→ 重算；failed 保留错误，下次刷新重试
bv:        pending → ready | needs_review | failed | budget_skipped
           insufficient 为终态（同正文哈希命中缓存）
           ready/needs_review 在正文哈希变化时回到 pending
           failed/budget_skipped 不命中缓存，下次仍调模型
```

## 4. 打分规则

**Pin 质量分** `pin_heuristic_v2`（上限 90）：

| 项 | 规则 |
| --- | --- |
| 底分 | 45 |
| 正文长度（strip 后） | ≥5000 +25；≥2000 +18；≥800 +10；≥200 +4；否则 −20 |
| 数字与结构 | 数字 ≥3 处 +8，1–2 处 +4；分段 ≥4 +4；合计 ≤12 |
| 发布方 / 发布时间 | +5 / +3 |

指纹 = 公式名 + 正文 + 发布方 + 发布时间；摘要、核查、来源、`bb_score` 不影响。

**通用业务价值**：10 维各 0–10 整数，权重合计 100（规则约束 14、同业动作 12、产品规则 10、系统实现 8、客户杠杆 10、资金路径 10、下行损失 10、适用对象 8、决策窗口 8、落地门槛 10）。程序算 `score/10*weight`，总分四舍五入，模型给的 `score/total/weight` 一律丢弃。

证据闸门：≤8 条引文，NFKC + 标点折 + 去空白后子串校验，通过后 ≥12 字；无有效引文且分 >0 封顶 3；非整数按 0 记 `non_integer`；缺 key 按 0 记 `missing`；禁词与「不是X而是Y」命中即该轮不通过，说明拼装前再滤一次。

预算：单篇 ≤5 次（取证 1、补抽 ≤1、打分 1、检查 1、重跑 ≤1）；简报按 `剩余预算 / 剩余篇数` 分配，单篇上限 120s，低于 60s 直接 `budget_skipped` 并落库（分数 NULL），下次仍重试。

决策窗口过期（§6.5 提案 C）：渲染时读 breakdown 的 `scored_on` 与 `decision_window_dates`，评分时不足 90 天且今天已过 → 标「窗口已过（评分时 N 天）」；不改分、不改 status、不改排序。

## 5. 验收与实测

单测/集成（`/Users/mt16/dev/moni/.venv/bin/python -m pytest ... -p no:cacheprovider`）：

- `tests/test_scoring_heuristic.py` 9 passed
- `tests/test_scoring_bv.py` 18 passed（自定义 Rubric 注入、5 次调用硬上限、补抽引文编号唯一、带空格日期、长文窗口保留尾段）
- `tests/test_scoring_integration.py` 13 passed（Pin 响应形状、refresh 幂等与正文敏感、`GET /pinned` 只读、预算不足落 `budget_skipped` 且分数 NULL、CRUD PUT 后重算、快速 hermes 不烧预算、`/api/articles` 不带质量字段、正文过短排除、hash 过期排除、ready 先于 needs_review、legacy 与 ready 同档、标签 ≤3 不带数字、窗口过期标注不改分）
- `tests/test_scoring_store.py` 2 passed（公式/指纹漂移、rubric 与正文哈希缓存键）
- `tests/test_pins.py`、`tests/test_brief_html.py`、`tests/test_brief.py` 全绿
- 全量 `tests/`：**1988 passed / 2 skipped**；2 项与本改动无关的环境性失败（见 §6）

浏览器实测（本地临时实例 8799 + 临时 DB，未触碰生产 8900；静态简报页 8798）：

- Pin tab 表头顺序 `标题 / 来源 / 命中标签 / 质量 / 原文时间 / 抓取时间 / 操作`；质量列 `text-align:right`、`font-variant-numeric:tabular-nums`、`white-space:nowrap`，实测单元格宽 140px（auto 布局分配，设计目标 ~72px）。
- 三行实测值：`failed → —`、旧行（公式为空）→ 载入后自动 refresh 变 `30`、长文 → `87`（5493 字 / 数字 20 处 / 5 段 / 有发布方 / 无发布时间）。
- tooltip 默认 `display:none`，样式表规则 `.tag-chip-wrap:hover .tip { display: block; }` → 悬停才出现；内容含底分与四项明细。
- 简报页：副标题「按通用业务价值排序」；`通用 72` + `规则约束、同业动作、决策窗口` + `窗口已过（评分时 52 天）`；`通用 90 · 待复核`；阅读 chip 为 `阅读 90`；未纳入区显示 `正文过短（不足 200 字）`；页脚免责句存在。

## 6. 已知限制

- `tests/test_frontend_safe_href_and_vendor.py` 需要 Pillow（当前 venv 未安装，与本改动无关）。
- `tests/test_migration_039_xbot_feed.py::test_039_upgrades_a_db_built_by_old_code` 需要在生产仓库创建 git worktree，沙箱内被拒绝。
- 质量列实际宽 140px：表格 `table-layout:auto` 会把余量摊到各列；数字仍右对齐，未改全局表格布局。
- 方向分（V2）未实现；`moni/scoring/direction.py` 只是占位，不建表、不调用。

## 7. 独立审计与修正（2026-11-17）

独立只读审计（subagent，基于 commit `7722e45`）判 **FAIL**：4 个 P1 + 8 个 P2。已全部修正：

| 级别 | 问题 | 修正 |
| --- | --- | --- |
| P1 | 全局预算按「请求超时」扣减，秒回的 fake hermes 也会把 900s 预算耗尽 | 改为按实际墙钟耗时扣减（`enrichment.py` llm 闭包） |
| P1 | `PUT /api/articles/{id}` 写 publisher/published_at/raw_content 后不重算 Pin 质量 | 该路由与 `backfill-publisher` 都补 `recompute_quality_if_pinned` |
| P1 | 长文取证只取前 12000 字，尾段日期/金额拿不到 | 实现 §6.2：≤3000 字/窗，首、尾 + 数字/日期密度最高的两窗，拼进同一次取证 |
| P1 | 补抽 + 重跑 + 检查可发 6 次调用 | `_Caller` 硬上限 5 次，超出即不再调用；测试覆盖 6 次路径 |
| P2 | 补抽引文从 e1 重新编号导致 id 冲突 | 合并后去重并按顺序重编号 |
| P2 | 旧行（status 为空）单独一档，排到所有 ready 之后 | 与 ready 同档，按分数降序 |
| P2 | `2026 年 12 月 1 日`（带空格）解析不出日期 | `_DATE_RE` 允许年/月/日两侧空白 |
| P2 | `needs_bv_rescore` 不比对 rubric 版本 | 缓存键加 `business_value_rubric` |
| P2 | 检查员 prompt 没有引文，无法判断「理由没有事实」 | 审计 prompt 加入已校验引文 |
| P2 | 有 `bb_score` 时 chip 文案 `BestBlogs n` 偏离 §7.4 | 统一 `阅读 n`，tip 说明来源 |
| P2 | 渲染不校验 `business_value_hash`，正文变了仍按旧分纳入 | `_bv_usable` 加 hash 校验，过期行归「业务价值未完成」 |

## 8. 回滚

SQLite `ADD COLUMN` 不可逆。回滚 = 恢复执行 045 前的库备份；新列留空时旧代码可正常工作（首次部署即此状态）。代码回滚到 `60a2a06` 即可，无需反向迁移。
