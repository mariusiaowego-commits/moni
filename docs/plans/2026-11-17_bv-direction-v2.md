# Sprint 67 · 业务方向分 V2（实现记录）

状态：**进行中**
sprint id：`26112367`
设计依据：`docs/specs/2026-11-16-pin-quality-bv-harness.md` §10（V2 附录）
分支：`feat/bv-direction-v2`（base `78e3b68`，v1.0.114）
目标版本：未发布（PR #225 仍是 draft）

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

- S1（本次）：`moni/scoring/direction.py` 全部纯函数、migration 053、单测。
- S2：研究 runner（`running → passed/failed` 落库、旧 `passed → expired`）、配置读写 API。
- S3：方向打分的缓存与写入、简报/卡片 UI、Pin 侧不受影响。

## 2. 表（migration 053 + 054）

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

只加表，不改既有列，不回填历史分。`_CURRENT_VERSION: 52 → 53`。

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
- 全量 `tests/` 不得低于 main 基线。**实测（2026-11-23，rebase 到 `78e3b68`）**：`pytest tests/ -p no:cacheprovider -q` → **2437 passed / 2 skipped / 1 failed（363s）**；唯一失败 `tests/test_migration_039_xbot_feed.py::test_039_upgrades_a_db_built_by_old_code` 是沙箱禁止写 `.git/worktrees`，放开权限单跑 **1 passed**，非代码问题。

S2/S3 另行补充。

## 8. 默认假设（待用户否决才算变）

- 方向数量先按单个实现，表结构天然支持多个。
- `label` 唯一不强制，重名由配置层拦截。
- 研究结果保存完整 `rubric_json`，不另建维度表；版本号从 1 递增。
- 方向分与通用分都只在简报链路产生，不在 Pin 页面触发模型调用。

## 9. S2 / S3 落地计划（待用户裁决后才开工）

S1 只交付纯函数与建表，**没有入口、没有 runner、没有 UI**——合进去是一套跑不到的功能。
S2/S3 的具体落点如下（均为现有代码里的真实位置）：

### S2 — 研究 runner + 配置 API

1. `moni/scoring/direction_store.py`（新增）：`create_direction` / `update_direction` /
   `list_directions` / `start_research`（插 `running`，版本号 = 该方向最大版本 +1，并把旧
   `passed` 改 `expired`）/ `finish_research`（落 `passed` / `failed` 与 `rubric_json` 或
   `error`）/ `latest_research_rows`（喂给现成的 `usable_research`）。
2. `moni/web/routes/directions.py`（新增路由，挂 `moni/web/routes/__init__.py`）：
   `GET/POST /api/directions`、`PATCH /api/directions/{id}`、
   `POST /api/directions/{id}/research`（触发）、`GET /api/directions/{id}/research`（查状态）。
   写接口沿用现有 `LocalOriginGuard` + CSRF 约定（同 `moni/web/routes/pins.py`）。
3. 研究作业进现有 job 队列：`moni/llm/jobs.py` 的 `_VALID_TASK_TYPES` 增加
   `bv_direction_research`；作业体 = `build_research_prompt(label, brief)` → 模型 →
   `parse_research` → 落库。模型调用仍走 `moni/llm/client.py` 的 `call_llm()`（hermes 子进程，
   moni 不持 key），预算/超时沿用 `moni/scoring/bv_pipeline.py` 的 `_Caller` 墙钟预算口径。

### S3 — 方向分缓存写入 + UI

4. `moni/scoring/direction_pipeline.py`（新增）：`score_direction_bv(snap, rubric, llm, budget_s)`，
   直接复用 `score_general_bv` 的取证 → 校验 → 复核 → 审核链路，只把 rubric 换成方向的
   （`research_to_rubric` 已给出带版本号的 rubric）。落 `article_bv_direction`，缓存键 =
   `content_hash + direction_id + research_version`。
5. 触发点：简报链路（`moni/share/enrichment.py` 里 `score_general_bv` 的调用点）——用户在简报里
   选了方向才对这批文章跑方向分；不选方向就不跑，保持「方向分不在 Pin 页面触发模型调用」。
6. UI：卡片 chip `方向 · {label} {n}`（`moni/share/brief_html.py` 通用分 chip 同处）；
   简报副标题「按方向 · {label} 业务价值排序」；方向配置页（在 `moni/web/static/` 现有页面里
   加一项）。方向研究不可用（`usable_research` 返回拒绝）时整列隐藏，不显示旧版本分数。
7. 不需要新迁移：表已在 053。

### 需要用户拍板的四件事

| # | 问题 | 我的建议 |
| --- | --- | --- |
| a | 方向配置入口放哪 | 「配置管理」下加一项，不新开一级页面 |
| b | 研究怎么触发 | 保存配置后不自动跑，改为显式点「重新研究」，避免每次改字就烧模型 |
| c | 方向分要不要进 Pin 列表 | 不进；方向分只服务简报，保持 Pin 只有质量分 |
| d | S2/S3 排期 | 拆两个 PR（S2 内核+API、S3 打分+UI），各自带审计与 tag |

工作量估计：S2 约 350 行 + 40 用例；S3 约 600 行 + 30 用例 + 浏览器实测。

## 10. 独立审计后的加固（2026-11-23）

只读审计结论 **PASS WITH NITS**（无 P0）。本轮修掉 P1×2 / P2×3 / P3×5（除 P3-3）：

| 编号 | 问题 | 修法 |
| --- | --- | --- |
| P1-1 | `_int()` 把无法解析的版本号吞成 0，损坏行被当作 v0 排在 v1 之后，最新行根本没被检查 → 静默回退旧版（违反「不回退旧版」） | `_positive_int()` 严格解析；任何一行版本号不可解析 / ≤0 / 非整数 → 整批判「研究版本号损坏」并拒绝，不降级 |
| P1-2 | `usable_research` 不按 `direction_id` 过滤，多方向时可能取到别的方向的版本 | 加可选 `direction_id` 形参；给了就只在该方向的行里取最大版本 |
| P2-1 | `parse_research` 的 label/brief 取自模型输出，而提示词从不要求模型输出这两项 → 恒为空；`to_payload()` 也不含这两字段 | `parse_research(raw, *, label="", brief="")` 改用调用方入参，忽略模型输出 |
| P2-2 | `article_bv_direction` 无外键 → 删文章/删方向留孤儿行（仓库其余 7 处子表都有 CASCADE） | 迁移 053 补 `articles(id)` 与 `bv_direction(id)` 两个 `ON DELETE CASCADE` |
| P2-3 | `prompt_id` 存了不校验 → 旧提示词产出的研究被判为可用 | `usable_research` 比对 `prompt_id != DIRECTION_PROMPT_ID` 即拒 |
| P3-1 | `_text()` 对任何值 `str()`，int/True 被静默接受 | 改为只收真字符串，否则返回空串 → 由「问题为空」「锚点有空档」拦下 |
| P3-2 | 理由模板不校验 `{…}` 占位符 | 模板必须同时含 `{` 与 `}` |
| P3-4 | scope / out_of_scope 不做禁词检查 | 一并过 `banned_hits` / `writing_hits` / `BANNED_PATTERN` |
| P3-5 | 锚点接受 list 按位置映射，反序会整体错位；dict 多余档位被静默丢弃 | 只接受 dict，档位必须正好是 0/3/6/10 |
| P3-6 | `research_to_rubric` 不校验入参，可造出 999 权重 rubric | 校验维度 key/label/权重仍等于 `RUBRIC_V1`；版本号必须为正整数，否则 `ValueError` |
| P3-7 | 缺 `tests/test_migration_053_bv_direction.py` | 补 14 个用例：表/索引/唯一键/两个外键的 CASCADE/幂等 |
| P3-8 | Sprint 号 4 处不一致 | 迁移文件注释与测试 docstring 统一为 Sprint 67 |

**P3-3 未修**：`BANNED_PATTERN` 允许换行或超长间隔绕过（`moni/scoring/rubric.py:30`，main 已有），
属 main 的问题、不是本分支引入，按「不顺手修相邻问题」留在待办。

`parse_research` 的签名变了（多了 keyword-only 的 label/brief），`usable_research` 多了一个
keyword-only 的 `direction_id`。两者都是本分支新引入的 API，尚未进入 main，没有兼容负担。

### 修完后的测试证据

```
$ /usr/local/bin/python3 -m pytest tests/test_scoring_direction.py -p no:cacheprovider -q
41 passed in 0.39s

$ /usr/local/bin/python3 -m pytest tests/test_migration_053_bv_direction.py -p no:cacheprovider -q
14 passed in 1.29s

$ /usr/local/bin/python3 -m pytest tests/ -p no:cacheprovider -q
2469 passed, 2 skipped, 2 warnings in 369.09s (0:06:09)
```

## 11. 第二轮回归审计后的加固（2026-11-23）

第一轮审计的 12 条 finding 全部修完后，再做一轮独立回归审计，结论 **FAIL（1 条 P1）**。
本节是第二轮的修复记录。

| 编号 | 问题 | 修法 |
| --- | --- | --- |
| P1-1 | `_positive_int()` 用 `str.isdigit()` 判定，但 `'²'`/`'³'`/`'①'` 等 `isdigit()` 为真而 `int()` 抛 `ValueError`；SQLite 的 `INTEGER` 只是亲和类型，`'²'` 真的能存进 `research_version` → `usable_research` 直接炸栈 | 改为 `text.isascii() and text.isdigit()` 双判，全角/阿拉伯数字一并拒 |
| P2-1 | 迁移 053 补外键对**已跑过初版 053 的库无效**：`CREATE TABLE IF NOT EXISTS` 跳过已存在的表，且 v53 已记进 `schema_version`，永不再跑 → 该库永远缺两个外键 | 新增 `054_bv_direction_fk.sql`：重建表 + 搬数据；复制期间 `foreign_keys=OFF`（旧表可能已有孤儿行，否则升级会中断）；`_CURRENT_VERSION 53 → 54` |
| P2-2 | 写读校验不对称：`parse_research` 查 `scope`/`out_of_scope`，`usable_research` 不查 | `usable_research` 补同样的非空 + 长度校验 |
| P3-1 | 禁词套到 scope/out_of_scope 上会反复打回合法描述（「深度报道」「有观点的评论」都是模型很自然的写法），S2 runner 会永远跑不出研究 | scope/out_of_scope 改为**只校验非空 + 长度（≤80 字）**，不套写作禁词表——回归审计自己的建议 |
| P3-2 | 模板占位符校验太松：`'}{'`、`'{{}}'` 能过 | 改用正则 `\{[^{}]+\}` |
| P3-3 | `prompt_id` 全等会误伤带空白的行 | 比较前 `strip()` |
| P3-4 | `direction_id` 过滤依赖 rows 带该列；缺列时静默返回「该方向还没有研究版本」 | 保留行为，docstring 写明调用方必须带 `direction_id` 列（字符串 `'1'` 也可，`_positive_int` 归一） |
| P3-5 | `to_payload()` 不落 label/brief；`rubric_id` 在 version=0 时产出 `bv_direction_v0` 哨兵 | 按设计保留：label/brief 存 `label_snapshot`/`brief_snapshot` 两列；v0 哨兵已无实际可达路径（`research_to_rubric` 拒 0） |

**P3-3（BANNED_PATTERN 换行绕过）仍不修**：属 main 的 `moni/scoring/rubric.py:30`，本分支
未引入该文件（`git diff --stat origin/main...HEAD -- moni/scoring/rubric.py` 为空），按
「不顺手修相邻问题」留待办。

