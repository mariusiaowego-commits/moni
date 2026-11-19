# v1.0.104 — 联网核查 Gate 3 语义修正（sprint-59 / id 26111902）

## 问题（生产实测）

文章 `4a111c743cb775e3`（央行公示辽宁第四批金融科技创新应用）最新联网核查在 UI 只显示「【存疑】」两个字：

- 原始 LLM 输出：`conclusion=【待核实】`、168 字 `summary`、6 条证据。
- 6 条证据中 4 条核心证据为 13/13/13/29 天；2 条历史背景证据为 787/651 天。
- 现有 Gate 3 取全批 `max(days_old)=787`，强制 90d_demote；随后清空 `summary`，UI 只剩裸标签。
- 更严重：`summary=""` 会击穿 `enrichment.py` 的分享预检，文章刚核查完却被判“缺 summary”反复重跑。

## 修法（经 agy 只读评审，APPROVE WITH EDITS）

1. **时效聚合改代码侧确定性判断，不新增 prompt/schema 字段**：
   - 支撑主结论的 `【已核实】` 证据参与最严时效；
   - 若本批没有 `【已核实】` 证据，取 `min(days_old)`（最新一条证据距今），避免历史背景证据拖垮近期事实；
   - `days_old=None` 不参与聚合；若全部缺失则 timeliness=unknown，不降级。
2. **降级不清空 summary**：
   - Gate 3 不再 `summary=""`；
   - Gate 2 降级只对原始【已核实】改写 summary，说明放 `fact_check_summary`，不塞进 `fact_check_conclusion`；
   - `with_tag()` 保持职责单一，不加默认话术。
3. **标签语义校准**：
   - 91+ 只把【已核实】降为【待核实】；
   - 【待核实】/【存疑】保持原标签；
   - 31-90 保持现有后缀提示。
4. **UI / how 语义区分**：
   - 详情页显示 `fact_check_timeliness` 徽章：`fresh` 不显示、`30d_warn`“含 30–90 天旧证”、`90d_demote`“时效降级 · 含超 90 天旧证”；
   - `_build_how` 不再输出“最新来源超过 90 天”的自相矛盾文案，改为“最新来源距今 {min_days} 天；最早 {worst_days} 天”。

## 验收

1. `tests/test_fact_check_plus.py` 新增/更新失败模式：
   - 近期核心证据 + 历史背景证据不触发 90d_demote；
   - 全部【已核实】证据 >90 天才降为【待核实】；
   - Gate 3 降级后 `fact_check_summary` 不为空；
   - 原始【待核实】不被改成【存疑】；
   - `min(days_old)` 文案与 timeliness 一致。
2. `pytest tests/ -q` 全量通过（或明确列出非本次引入的 failed）。
3. 生产 UI 用该文章复核：结论不再是两个字、summary 可见、timeliness 显示为 fresh/30d_warn 而非 90d_demote。

## 红线

- 不新增 LLM prompt 字段；
- 不改数据库 schema；
- 不重启 8900，除非本 PR 合并后用户授权部署；
- 本 sprint 不回填历史 `fact_check_*` 数据。
