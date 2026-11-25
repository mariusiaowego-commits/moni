# Sprint 74 — 业务方向「读者视角」与澄清追问

日期：2026-11-25
分支：`feature/v1.0.128-bv-reader-perspective`
目标 release：v1.0.128

## 1. 为什么做

方向研究提示词里写死了第二条硬性要求：

> 2. 问题一律从银行或支付机构角度问：这件事改变我方什么。

于是「读者视角」被锁死成银行/支付机构。但用户真正想要的方向常常是
「银行新媒体渠道从业者」——他关心的是哪些内容能变成公众号选题、
短视频脚本，而不是「这件事改变我方什么」。视角一错，十维问题全错。

第二个问题：一句话说不清一个视角。「银行新媒体渠道从业者」这七个字，
没有说清他更在意获客还是合规、关心多长的内容、在公众号还是视频号。
模型拿这句话去改写十维打分问题，改出来的东西用户不敢用。

## 2. 大白话方案

1. 方向编辑里多一个「读者视角」输入框：写一句「这个方向的文章是写给
   谁看的、要站在谁的立场评价」。
2. 填了视角，模型先反问你最多 5 个问题（问清这个视角具体是什么），
   你回答完保存。
3. 研究时把「读者视角 + 澄清问答」一起喂给研究员，十维问题按这个人的
   立场改写，不再写死银行。
4. 打分时提示词里的「读者是银行/支付数字化从业者」也换成这个视角，
   问的问题和打的分是同一个立场。
5. 视角或回答改了 → 已通过的研究立刻作废，必须重新澄清、重新研究，
   不会出现「问题按旧视角写的、分按新视角打的」。

没填视角的老方向**行为完全不变**：提示词逐字节不变，线上「微信生态」
已通过的 v3 研究继续可用，不需要重新研究。

## 3. 契约变更

| 项 | 变更 |
|---|---|
| 迁移 | `060_bv_direction_reader_perspective.sql`，`_CURRENT_VERSION` 59 → 60 |
| `bv_direction` | 新增 `reader_perspective TEXT NOT NULL DEFAULT ''` |
| `bv_direction_research` | 新增 `perspective_snapshot`、`clarification_snapshot`（均默认 `''`） |
| 新表 | `bv_direction_clarification`（direction_id / version / status / questions / answers） |
| API | `POST/PUT /api/directions/{id}/clarification`、`GET /api/directions/{id}/clarification`、`PUT /api/directions/{id}/clarification/answers` |
| API 字段 | `Direction.reader_perspective`、`Direction.clarification` |
| 提示词 | 新增 `bv_direction_clarify_v1`；新增 `bv_direction_research_v2`（**仅**在有视角时使用） |

旧行三列都是 `''`，与空快照比较相等 → 线上方向不受影响。

## 4. 关键决策

- **两个研究提示词 id，而不是改 v1**：没有视角的方向继续用
  `bv_direction_research_v1`（逐字节不变），有视角的用
  `bv_direction_research_v2`。改 v1 会让线上唯一可用方向的已通过研究
  立刻失效，用户必须重新研究一次——不值得。
- **澄清是研究的闸门，不是可选项**：填了视角但没澄清完就点研究，
  API 直接 409「读者视角还没澄清过，请先生成澄清问题」。
- **回答也算研究输入**：回答变了 → 研究作废。否则会出现「问题按旧回答
  写的、分按新回答打的」。
- **通用分（无方向）保持银行语境**：方向是叠加层，通用 rubric 是 moni
  的产品定位，不在本次范围。

## 5. 测试计划

- 单测：`tests/test_direction_reader_perspective.py`（16）、
  `tests/test_migration_060_bv_direction_reader_perspective.py`（6）、
  `tests/test_direction_clarification_api.py`（6）
- 回归：全量 `pytest tests/ -p no:cacheprovider -q` → 2799 passed /
  1 failed（沙箱假红：`test_039_upgrades_a_db_built_by_old_code` 需要在
  仓库 `.git/worktrees` 下建 worktree，沙箱不允许；非沙箱下该文件
  33 passed）/ 2 skipped
- UI 实测：8901 dev 端口，方向编辑弹窗截图 + 实点

## 6. 这次没做

- 澄清问题不做多轮追问（用户可以再点一次「生成澄清问题」开新一轮）。
- 方向分缓存键不变（方向分本来就按 research_version 分代）。
- 通用 rubric 的银行语境没有放开。
