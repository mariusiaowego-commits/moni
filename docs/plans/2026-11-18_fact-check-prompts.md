# 事实核查提示词与人话结论（v1.0.101 / PR-B）

## 问题（实测）

1. 分享图里的事实核查像"证据清单"：出现 `温度 [0,2]`、`top_p [0,1]`、
   "与文档一致"这类参数与比对口吻，读者看不出能不能引用。
2. 基础档（离线推断）允许标【已核实】，没有证据也敢下结论。
3. 详情页 `GET /api/articles/{id}` 不返回任何 fact_check 字段，
   20 篇有结论的文章在前端永远是空白。
4. 分享图勾了"深度事实核查"仍只跑离线推断，联网档没有介入。

## 方案

- `fact_check.md`：1-3 条最值得核的主张；新增"依据类型"四档
  （文内矛盾/公开常识/模型记忆/无依据）与判断纪律——无证据只能
  "无法验证"，不许判"错误"；全文 ≤600 字。
- `fact_check_plus.md`：`conclusion` 只出标签；新增 `thesis`（≤40 字）；
  `summary` 2-3 句 ≤150 汉字，回答"能不能引用/依据多硬/注意什么"；
  evidence 新增 `source_date`；数字翻成含义，翻不出就省略。
- `jobs.py`：落库 `fact_check_thesis`（migration 048）；摘要上限 120→150；
  【无需核查】档也写 thesis。
- `enrichment.py`：离线档禁用【已核实】；勾选"深度事实核查"时补齐联网核查。
- 详情页：`Article` 增加 9 个 fact_check 字段；结论框标注"联网核查·有出处"
  或"快速核查·未联网"；证据项显示 verdict / claim / source_date。
- 分享估算：新增 `articles_missing_summary` 与
  `articles_missing_web_fact_check`，入队前告知还要补几篇。

## 验收

- 定向：`tests/test_fact_check_plus.py`、`tests/test_share_fact_check_prompt.py`、
  `tests/test_brief_web_mode.py`、`tests/test_api.py` 共 183 passed。
- 全量：2037 passed + 2 skipped + 1 个沙箱内 `git worktree add` 失败
  （沙箱外 33 passed）+ 1 个 worker_lock 用例在满载下偶发、单跑通过。
- 迁移：048 只加列，历史行 NULL。

## 影响面 / 回滚

- 改 prompt、写库字段、详情页展示与分享预检；不改闸门判定规则（PR-A）。
- 回滚：revert 本 PR；048 新列留着不影响旧代码。
