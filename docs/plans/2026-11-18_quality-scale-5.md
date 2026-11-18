# 2026-11-18 · 质量分改 5 分制 + Pin 列头 + 分页修复

状态：**已发布 v1.0.97；§1–§4 记录当时口径（两位小数），上线后改为一位小数，见 §6**
分支：`feat/quality-scale-5`（base `fd61176`，v1.0.96）
目标版本：`v1.0.97`

## 1. 改了什么

### 1.1 质量分 0–90 → 0–5，两位小数

用户拍板：质量分与 0–100 的业务价值分、BestBlogs `bb_score` 要一眼区分，改成 5 分制。

映射（保持单调，不改变排序含义）：

| 项 | 旧（0–90） | 新（0–5） |
| --- | --- | --- |
| 底分 | 45 | 2.50 |
| 长度 ≥5000 / ≥2000 / ≥800 / ≥200 | +25 / +18 / +10 / +4 | +1.40 / +1.00 / +0.60 / +0.20 |
| 长度 <200 | −20 | −1.10 |
| 数字 ≥3 / 1–2 | +8 / +4 | +0.40 / +0.20 |
| 分段 ≥4 | +4 | +0.20 |
| 数字+分段合计上限 | 12 | 0.60 |
| 发布方 | +5 | +0.30 |
| 发布时间 | +3 | +0.20 |

`total = round(clamp(0, 5.00, base + Σdelta), 2)`；四项全满 = 5.00，仍然不设调整分。

公式名 `pin_heuristic_v2 → pin_heuristic_v3`。这一步是必须的：`needs_quality_refresh`
按公式名判过期，不升版本会把库里 0–90 的旧值当成 5 分制直接显示。

### 1.2 Pin 列头

「质量」→「质量分」（`moni/web/static/index.html`）。

### 1.3 Pin 分页

根因：文章表的分页条写死绑在「全部文章」状态上。

- 页码显示用 `articlesPage` / `articlesTotal`
- 按钮调 `articlesPrevPage()` / `articlesNextPage()`
- 每页条数绑 `articlesPageSize`

Pin 子标签的数据在 `pinnedArticles` + `pinnedPage` / `pinnedPageSize` / `pinnedHasMore`，
两边完全不通，所以 36 篇 Pin 永远只看到第一页（20 条），按钮点了只翻「全部」。

修复：分页条按 `articlesSubTab` 分叉。

- Pin：`pinnedPageSize` + `pinnedChangePageSize()`、`pinnedPrevPage()`、`pinnedNextPage()`，
  文案「第 N 页 · 共 M 篇」。
- 全部：保持原行为。

后端 `GET /api/articles/pinned?page=&page_size=` 本来就正确，另有既有测试
`tests/test_pins.py::test_pagination`；本次补了一条「两页无重叠且并集等于全量」的断言。

## 2. 验证

- `tests/test_scoring_heuristic.py`：5 分制上下限、两位小数、substance 上限 0.60、
  `reading_heuristic` 仍为 0–100（93）。
- `tests/test_scoring_integration.py`：Pin 后 `4.80 / pin_heuristic_v3`；短稿 `1.70`；
  改正文后重算 `4.80`；`GET /api/articles` 仍不带质量字段。
- `tests/test_scoring_store.py`：缓存键改用 `heuristic.PIN_FORMULA`，不再写死版本名。
- `tests/test_pins.py`、`tests/test_brief_html.py`、`tests/test_scoring_bv.py` 全绿。
- 全量 `tests/` 见 PR 说明。

## 3. 已知影响

- 存量 `pin_heuristic_v2` 行在 Pin 页加载/refresh 后重算为 5 分制；旧分数不再展示。
- `quality_score` 列是 INTEGER affinity，SQLite 会无损存 REAL（已实测 `4.72 → typeof=real`），
  不新增迁移。
- `reading_heuristic` 是简报卡「阅读分」，仍是 0–100，不与 Pin 列混用。
- 分享/简报不读 `quality_score`（`brief_html._quality` 只用 `bb_score` 或 `reading_heuristic`）。

## 4. 默认假设

- 展示精度改为 1 位小数（`4.6`），因为公式每一项都是 0.1 的整数倍，第二位恒为 0；见 §6。
- 负数下限 0，5.00 封顶；不出现 4.999 的显示。
- 分页每页条数沿用现有选项 `[20, 50, 100, 200]`。

## 5. 独立只读审计（2026-11-18）

独立审计结论 **PASS WITH NITS**：无 P0/P1/P2，4 个 P3。处理如下：

| 级别 | 问题 | 处理 |
| --- | --- | --- |
| P3 | `enrichment._ensure_quality` 仍把 0–100 估算写进 Pin 专属列，且 `compute_heuristic_quality` 读回时 `int()` 截断（4.8→4） | 改为只处理 Pin 集合内的行，委托 `moni.scoring.ensure_quality`（0–5 + 公式名）；集合外跳过 |
| P3 | `brief_html` 导入 `compute_heuristic_quality` 但全文件无调用 | 删除该函数与死导入 |
| P3 | Pin 子标签下顶部「共 5330 篇」与分页「共 36 篇」并排 | 顶部总计在 Pin 子标签隐藏 |
| P3 | migration 020 注释仍写 `0-100` | 历史迁移不改，本记录说明列语义已变 |

## 6. 精度微调（2026-11-18 上线后）

- 公式 `round(..., 1)`，展示 `toFixed(1)`，tooltip 明细同样 1 位。
- 取值不变：长度档 1.4/1.0/0.6/0.2/-1.1，数字结构 0/0.2/0.4/0.6，发布方 0.3，时间 0.2，底分 2.5 全是 0.1 的整数倍，所以不触发重算，旧数据直接可读。
- `index.html` 的 `js/app.js?v=` 同步推进到 `20261118001`，避免浏览器拿旧脚本。
