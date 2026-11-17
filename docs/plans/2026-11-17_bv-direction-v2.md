# Sprint 53 · 业务方向分 V2（实现记录）

状态：**进行中**
sprint id：`26111702`
设计依据：`docs/specs/2026-11-16-pin-quality-bv-harness.md` §10（V2 附录）
分支：`feat/bv-direction-v2`（base `fd61176`，v1.0.96 / Sprint 52 已上线）
目标版本：`v1.0.97`

## 1. 做什么

V1 的通用业务价值分不动。新增一条独立链路：用户配置一个业务方向（如「微信生态」），
先让模型为这个方向重写十维问题并存档版本，再用这套新题目对每篇文章重打一次分，
结果落在独立的表里。同一篇文章因此可能有两个分：

| 列 | 题目来源 | 存储 | 什么时候有 |
| --- | --- | --- | --- |
| 通用 | `RUBRIC_V1` 固定十问 | `articles.business_value_*` | 生成简报时 |
| 方向 · {label} | 该方向已通过校验的研究版本 | `article_bv_direction` | 第一次在简报里选该方向时 |

方向分不是第 11 维，也不是给通用分乘系数。两列题目不同，分数不能互比。

本 sprint 分三段交付，第一段是纯函数核心 + 建表 + 测试：

- S1（本次）：`moni/scoring/direction.py` 全部纯函数、migration 046、单测。
- S2：研究 runner（`running → passed/failed` 落库、旧 `passed → expired`）、配置读写 API。
- S3：方向打分的缓存与写入、简报/卡片 UI、Pin 侧不受影响。

## 2. 表（migration 046）

```
bv_direction             id, label, brief, enabled, created_at, updated_at
bv_direction_research    id, direction_id, research_version, status,
                         label_snapshot, brief_snapshot, prompt_id,
                         scope, out_of_scope, rubric_json, error,
                         model, started_at, finished_at
                         UNIQUE(direction_id, research_version)
article_bv_direction     article_id, direction_id, research_version,
                         score, note, status, error, breakdown_json,
                         content_hash, rubric_id, scored_at
                         PRIMARY KEY (article_id, direction_id, research_version)
```

只加表，不改既有列，不回填历史分。`_CURRENT_VERSION: 45 → 46`。

## 3. 研究（纯函数，S1 交付）

`DIRECTION_PROMPT_ID = "bv_direction_research_v1"`，输入只有 `label` 和 `brief`，
输出 `scope` / `out_of_scope` / 十个维度（问题、0/3/6/10 锚点、理由模板）。
提示词写死五条硬句：

1. 每个问题保留原 key 的意思，只把对象换成本方向。
2. 问题一律从银行或支付机构角度问：这件事改变我方什么。
3. 锚点只写正文可见的事实：机构、文号、金额、日期、流程。
4. 不得评价写作，禁止出现：深度、可读、文笔、观点、洞察。
5. 范围外内容一律记 0，不许为本方向牵强关联给分。

校验（任一不过 → `failed`，错误原文落 `error`）：

- JSON 对象可解析，`scope` / `out_of_scope` 非空；
- 十个 key 一个不少、不多；
- 每维 `question` 非空且 ≤30 字；
- 每维 0/3/6/10 四档锚点齐全且非空；
- 每维 `template` 非空；
- `banned_hits` / `BANNED_PATTERN` / `writing_hits` 对 question、anchors、template 全部无命中。

配置校验：`label` 去空白后 1–6 字且过禁词/写作词；`brief` 去空白后非空。

权重与 label 不交给模型：从 `RUBRIC_V1` 原样继承（合计 100），只替换
`question` / `anchors` / `template`。生成的 rubric id 为 `bv_direction_v{n}`。

## 4. 版本门控（S1 交付）

唯一键 `(direction_id, research_version)`。保存或点「重新研究」时插 `running`
并把旧 `passed` 改 `expired`；返回后改 `passed` / `failed`。

可用性只看 `research_version` 最大的一行：

| 最新行 | 结果 |
| --- | --- |
| `passed` 且快照 == 当前 label/brief | 可用 |
| `running` / `failed` / `expired` | 拒绝，不回退旧版 |
| `passed` 但快照不符 | 拒绝 |

## 5. 打分缓存（S3）

缓存键 = 正文哈希 + `direction_id` + `research_version`。同键正文变了 `UPDATE`，
新研究版本才 `INSERT`。通用分与方向分互不影响。

## 6. UI（S3）

- 卡片 chip：`方向 · {label} {n}`，tip「按{label}研究 v{n} 的 10 问打分，题目不同」。
- 简报选方向时副标题「按方向 · {label} 业务价值排序」；排序与排除只用该版本方向分。
- 方向研究不可用时，该列整体不出现，不显示旧版本分数。

## 7. 验收

S1：

- `pytest tests/test_scoring_direction.py` 全绿；
- 提示词含五条硬句，且不含稿件正文输入；
- 缺 key / 三档锚点 / 禁词 / 写作词 / 超 30 字 / 非 JSON 各自返回 `failed` 原因；
- `research_to_rubric` 权重合计 100、key 与 `RUBRIC_V1` 一致；
- 门控：最新 `running` / `failed` / `expired` / 快照不符一律返回 `None`；
- `score_general_bv` 注入方向 rubric 能正常打分且总分由代码计算；
- 全量 `tests/` 不得低于 Sprint 52 基线（1992 passed / 2 skipped）。

S2/S3 另行补充。

## 8. 默认假设（待用户否决才算变）

- 方向数量先按单个实现，表结构天然支持多个。
- `label` 唯一不强制，重名由配置层拦截。
- 研究结果保存完整 `rubric_json`，不另建维度表；版本号从 1 递增。
- 方向分与通用分都只在简报链路产生，不在 Pin 页面触发模型调用。
