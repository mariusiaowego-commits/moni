---
type: sprint
sprint: 26112702
status: 进行中
---

# Sprint 86 · 案例「深度解读」接入详情页

## 背景

- #350 已合并 `case_insights` 生成器与回填 CLI，但生产 0 行，API / UI 无消费方。
- 用户诉求：详情页要能 10 秒判断「这个案例值不值得看」。

## 方案（经 agy 审阅、pixel 出 UI 方向 A「编辑部报告」）

### 后端

- `CaseRepository.get_case` 单条点查附带 `insight`：优先 `cases.insight_id`，
  回退该 case 最新 `status='done'`；`insufficient` 原样透传；脏 JSON 降级 `None`；
  只挂在详情，不进列表（避免 50 条大 JSON 与 N+1）。
- 不加写接口：生成走 CLI。
- 同步修 `moni/share/case_brief.py::_render_insight`：
  支持 `what/how/risks/reusable/fit`，`insufficient` 直接不渲染（否则回填后简报出空框）。

### 前端

- 详情抽屉 hero 指标与「为什么值得看」之间插入 `深度解读`：
  `what` 结论导语、`how` 编号步骤、`risks` / `reusable` 侧栏、`fit` 脚注。
- 1440 双列（主区 + 240px 侧栏），≤1180 单列堆叠；只用现有 cv-* token。
- 无 insight 或 `insufficient` 时整块不渲染（不显示「尚未生成」占位）。

### 数据

- 决赛前定向回填高价值案例；全量 432 条单独排期。

## 非目标

不流式生成、不在线编辑、不改 `case_insights` 表结构、不新增动作/类型枚举。

## 验收

- 单测：insight 指针/回退/insufficient/脏 JSON；case_brief 新旧键与 insufficient。
- 8901 副本库：真实回填 3 条 → 详情截图 1440/1024，DOM 无横向溢出。
