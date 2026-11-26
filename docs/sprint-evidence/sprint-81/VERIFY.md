---
type: sprint
sprint: 26112601
status: 进行中
---

# Sprint 81 · 验收记录

## 1. 命令与输出

环境：worktree `feat/cases-ui-v3`；DB 副本 `MONI_DB_PATH=/private/tmp/ev/news.db`；预览 8901。

### 定向测试

```text
$ /usr/local/bin/python3 -m pytest tests/test_cases.py tests/test_cases_taxonomy.py \
    tests/test_cases_evidence.py tests/test_cases_frontend_contract.py \
    tests/test_cases_p0_v2.py tests/test_cases_v3_api.py -p no:cacheprovider -q
128 passed in 16.29s
```

新增 `tests/test_cases_v3_api.py`（9 用例）覆盖：stats 同口径、单/多选筛选、q 命中
keyword+evidence、脏日期月份、limit/offset 无重复、facets 排除自身维度、详情
enterprise/industry/scenarios/related、404。

### 全量测试

见 §5（CI 与本地全量结果在此节回填）。

### 数字复算

```text
$ curl -s :8901/api/cases/stats
{total: 432, high_value: 137, enterprise_count: 305, scenario_count: 10,
 category_count: 8, pending_review: 73}
# 同库 SQL 逐项复算，结果一致：432 / 137 / 305 / 10 / 8
```

```text
industry=银行                         -> total 121
industry=银行,保险                    -> total 123
industry=其他                         -> total 224（空值桶走 COALESCE）
enterprise=ent_tencent                -> total 10
month=2026-11                         -> total 6
q=小程序                              -> total 2
value=high                            -> total 137（= reference_value=high）
scenario=other                        -> total 90（= 场景 facet「其他业务场景」）
```

`/api/cases/facets`：企业 `其他/未归类 215 · 腾讯 10 · Visa 10 · 汇丰 7`；
行业 `其他 224 · 银行 121 · 互联网 83 · 保险 2`；场景 `其他 90 · 数币 76 · 大模型 73 · 跨境 50`；
类型 `产品与服务创新 144 · AI与大模型应用 72 · 资本与运作 65 · 生态合作 61`。
给 `industry=银行` 时，industry 维度仍返回 `银行 2 / 证券 1`（自身维度不收敛），
enterprise 只返回 `ent_cmb`。

## 2. UI 实测（Playwright / Chromium）

视口 1440×900 与 1024×768：

- `documentElement.scrollWidth == innerWidth`（1440/1440、1024/1024），无横向滚动。
- 分布标签截断检测（`scrollWidth > clientWidth`）= 0。
- 首屏 100 张卡；点「加载更多」→ 200 张，文案 `已显示 200 / 432 条`。
- 筛选：`行业 → 银行` 后 hit `121`、token `银行 ×`、URL `?industry=银行`；清空回到 `432`。
- 搜索「小程序」→ hit `2`、cards `2`；清空恢复。
- 详情：无重复「结论」块；「原文证据」在有 `evidence_context` 时只渲染上下文（引用句加粗），
  不再重复一行 quote；指标名中文（`效率提升 / 约80%的广告变现`、`日活用户 / 互动日活3000万`），
  同行字号统一 24px；`参考价值` 同 24px。
- 标签：添加「自动化探针」→ 列表出现并带删除按钮；删除后恢复 4 个原标签。
- 分享：详情「生成案例简报」→ `/share`，队列出现 pending 任务与「案例简报已入队」提示。
- 控制台：`/`、`/articles`、`/cases`、`/stats`、`/share` 五条路由 pageerror / console error 均为 0
  （原 `Transition was skipped` 已由 `app.js` 首帧保护修掉）。
- 暗色：`body.dark` 生效，`--background` = `oklch(0.170 0.004 164.5)`，页面无新增暗色专写规则。
- 浮层：打开详情 `document.body.style.overflow = hidden`，关闭恢复空串；遮罩走 `--scrim`
  token（亮 `0.42` / 暗 `0.60`），`cv-*` 样式零硬编码颜色。

对比度（computed color 实测）：

| 元素 | 亮色 | 暗色 |
|---|---|---|
| 卡片摘要 | 17.24 | 14.08 |
| 卡片著录行 | 6.33 | 5.04 |
| 分布标签 | 5.74 | 5.53 |
| 搜索框文字 | 17.24 | 14.08 |
| 命中数 | 5.17 | 4.56 |

全部 ≥ 4.5（AA）。

截图（`docs/sprint-evidence/sprint-81/render/`）：

`v3-cases-1440.png`、`v3-cases-1024.png`、`v3-cases-dark-1440.png`、`v3-cases-dark-1024.png`、
`v3-filter-panel-1440.png`、`v3-dash-filter-1440.png`、`v3-search-miniapp-1440.png`、`v3-loadmore-1440.png`、
`v3-detail-1440.png`、`v3-detail-1024.png`、`v3-detail-dark-1440.png`、
`v3-detail-tags-1440.png`、`v3-share-queue-1440.png`。

## 3. 已知数据债（不是本次代码失败）

- `case_action` 全库 432/432 = `launch`，动作维度无区分度；UI 不提供动作 chip。
- `by_type` 仅 `AI 429 / PI 3`；页面的「类型」用 8 类 `case_category`，不是 `case_type`。
- `case_date` 有 `relativ` 等脏值；facets 用 `GLOB 'YYYY-MM*'` 过滤，列表卡片对非法日期不显示月份。
- 行业 `保险 2 / 证券 2`；`case_metrics` 存在语义错配（如 `user_count` 配「32.6亿元」），
  属抽取数据质量，本次不在前端做猜测性改写。

## 4. failed / skipped

- `skipped`：生产 8900 未重启、未跑生产库 UI（按边界只动 8901 + DB 副本）。
- `skipped`：`case_insights` 的「做法 / 风险」段落未展示（表未回填，PRD 非目标，不伪造）。
- `skipped`：加载更多只抽测一次追加（100 → 200），未在测试里点满 432。
- `skipped`：统计页 `/api/stats/cases-dashboard` 仍只排除 `__processing__`，与案例库
  `stats` 的 archived 口径分叉；属相邻页面问题，本次不改。

## 5. 全量测试与 CI

本地全量（`/usr/local/bin/python3 -m pytest -q -p no:cacheprovider`）：

```text
1 failed, 3008 passed, 2 skipped, 2 warnings in 516.53s (0:08:36)
```

唯一 failed 是环境限制，不是本 PR 引入：

```text
tests/test_migration_039_xbot_feed.py::test_039_upgrades_a_db_built_by_old_code
Failed: cannot create worktree at 015a2c5:
fatal: could not create directory of '/Users/mt16/dev/moni/.git/worktrees/old':
Operation not permitted
```

该用例要在主 checkout 的 `.git` 下建 worktree，当前沙箱只读；CI 在可写 clone 中执行。

首轮全量曾出现 4 个前端契约 failed（hover 守卫 / overlay 覆盖 / `safeHref`），
已在本 PR 修复并定向复跑：

```text
tests/test_frontend_microinteraction.py tests/test_frontend_modal_scroll_lock.py
tests/test_frontend_safe_href_and_vendor.py -> 53 passed
tests/test_frontend_motion.py tests/test_web_static_headers.py -> 11 passed
```

CI 结果：合并前回填 run id 与结论。

## 6. 独立审计与修复

独立只读审计按 PR head 复跑 128 passed，提出 2×P1 / 6×P2 / 8×P3。已修：

- P1 `value` 参数与后端 `reference_value` 不一致 → 路由加别名，KPI「高价值」、价值 chip、
  token、URL 回填统一生效（实测 137）。
- P1 静态资源版本号未 bump → `main.css / cases.js / app.js` 统一到 `?v=20261126004`。
- P2 facets 空值桶点了必 0 → `industry/category/value` 改 `COALESCE`，`scenario=other`
  改「无非 other 链接即命中」；实测 224 / 90。
- P2 action facet skip 维度名不一致 → `case_action` 条件归入 `action`。
- P2 显式 `case_lifecycle=archived` 恒 0 → 仅未显式指定时才默认排除 archived。
- P2 分页同 `created_at` 无 tie-breaker → `ORDER BY created_at DESC, case_id DESC`。
- P2 编辑对手方不回显/不能清空 → PATCH 用 `exclude_unset` 允许 `null`，前端本地补丁回写。
- P2 列表/详情/标签异步后到覆盖 → 加请求序号，关闭详情后迟到响应不再重开浮层。
- P2 详情浮层 a11y → `role="dialog" aria-modal="true" aria-label`，Esc 关闭，滚动锁复用。
- P2 企业 facet `LIMIT 30` 长尾不可选 → 提到 200。
- P3 详情深链 → 打开即写 `?case=`，关闭即移除；legacy `case_type` 不再误映射到 category。
- P3 卡片内删除按钮 Enter 冒泡 → keydown 仅 `$event.target === $event.currentTarget` 时打开。
- P3 `is-set` 无样式 → 补 chip 边框；`cvDetailLoading` 仍保留但未加占位（不影响功能）。
- P3 测试表演型断言 → `scenario_count` 改字面量 3，新增 value 别名、other 桶、archived 三组用例。
