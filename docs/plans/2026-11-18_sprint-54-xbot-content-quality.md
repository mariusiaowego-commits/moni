# sprint-54 — xbot_feed 劣质内容治理（阶段一）

> **状态：阶段一已完成 —— PR #237 已 squash 合入 `main`（`10f39571e33a2a39244950d0a9e313a6abbe0440`，
> 2026-11-19 12:39:54 CST）。** 本轮（2026-11-19 15:39 t97u/t98u）**只做文档收尾**，
> 不实施功能、不改功能文件、不 merge 本文档 PR、不 tag/deploy/生产写。
> sprint-54 · id `26111703` · 2026-11-17 ~ **2026-11-19（end_date）**
>
> **合并坐标（口径）**
> - PR head `271a35bfe26212173bcbf8445ce90c9abb9a754d`（2026-11-19 12:29:59）
> - squash merge `10f39571e33a2a39244950d0a9e313a6abbe0440`（单亲 `9b07c9f`）
> - **树等价**：`271a35b^{tree}` == `10f3957^{tree}` == `8d917c4387c5b515e655763c0d809618ea5ec17f`
> - **squash 后 ancestry 用 `10f3957` 验证**（`git merge-base --is-ancestor 10f3957 <main>`）；
>   **不要**把 PR head `271a35b` 说成 main 的祖先。
> - **版本证据**：含本 sprint 代码的**首个正式 tag 是 `v1.0.104`**（tag 对象 `6dd9301`，peeled → `c0363e3`，
>   由 **#246** 发布但**已包含** #237）；`v1.0.105`（peeled → `26a3502`）与 `v1.0.106`（peeled → `b9d8b15`）
>   **同样包含** #237
>   ⇒ 本 sprint **不是**任一 tag 的专属发布，只是**随**这些 tag 进入正式发布。
>
> **合并当时的 base**（历史快照，非当前）：`e5975a3593c69a88c2828f70739b18e4c2adc6c0`
> （合并前曾为 `fd611761027d5bcacfffb19ee34ec76baf06c477`）。
> ⚠️ **当前** repo main 与生产运行版本是**更晚的快照**（生产 schema 已到 **51**）——
> 两者**不要**混成同一个「当前」。

## 一句话

把「主题相关」与「准入许可」**拆两层**：只读文本规则判劣质 ⇒ **只产候选清单**给 dad 审，
**不自动处置**。另把 `bypass` 两个闸门拆成独立开关（**零行为变化**）。

## 六个交付

| # | 交付 | 路径 | 边界 |
|---|---|---|---|
| I3 | 只读候选预演器 | `scripts/xbot_quality_candidates.py` | 只 SELECT；不含正文；不报准确率 |
| I4 | 脱敏产物落盘 | `scripts/xbot_quality_artifacts.py` → `artifacts/xbot-quality-2026-11-18/` | 只含元数据+标签；**已 gitignore** |
| I5 | 离线规则验证器 | `scripts/xbot_quality_validate.py` | **无人工标注 ⇒ 一个准确率数字都不报**（`None`，不是 0） |
| I8 | 只读 dry-run 端点 | `moni/web/routes/moderation.py` | `GET /api/moderation/xbot/candidates`；**无写方法**；候选 ≠ 处置 |
| I9 | 只读 FTS 探针 | `scripts/verify_fts_index_criteria.py` | 只 SELECT/PRAGMA；**禁** integrity 写命令 / rebuild |
| — | 决策纯函数 + 保护资产护栏 | `moni/fetch/moderation.py`、`moni/fetch/protected_assets.py` | 纯函数 / 纯只读；**零新表** |

## 关键设计决定

- **主题 ≠ 准入**：命中 tag 关键词只说明「相关」；劣质判定是**另一层**。
- **新键 `bypass_tag_or_gate`**：与旧 deprecated alias `bypass_tag_filter` **不冲突**；
  旧 alias 的归一 / pop **一行未动** ⇒ **零行为变化**。
- **`keyword_audit` 仍排除 `xbot_feed`**：它走自实现 `_match_topic_tags`，永不跑通用 tag 闸门。
- **弱特征不自动封号**：弱内容命中**永不**计入作者候选的强样本数。
- **保护资产只读标记**：输出必含 `protected_article_ids` / `is_protected`（10 类资产）。
- **`article_id` 是 TEXT**（24 位 hex），不是 INTEGER ⇒ 用 int 查**一条都命中不了**。
- **中文 token 口径**（R11）：按**空白切分的整段连续串**；纯标点/零宽空格段**不是** token。
- **`articles_fts` 是 external-content**：对它 `SELECT` 读的是 `articles`
  ⇒ 索引孤儿**只能**用 `fts5vocab` 在**副本**上查（生产零写入）。

## ✅ schema 版本落差：已通过合并处理（**合并当时口径**）

✅ **schema 落差已处理**：合并前本地是 **45**、`origin/main` 已是 **46/47**
（`046_fact_check_summary.sql` / `047_fact_check_gate.sql`）⇒ 本分支**未新建、未重编号**迁移，
已通过 merge 干净继承 main 的 **`_CURRENT_VERSION` = 47** ⇒ **不会带着旧版本号**合并。
⚠️ **47 是合并当时的快照**；**当前** repo main 与生产 schema 是**更晚的快照**（生产已到 **51**）——
不要把两者混成同一个「当前」。

## 已知限制

1. **草标不是 dad 真值** ⇒ 没有真值就没有精确率/召回率/误杀率。
2. **媒体类硬盲区**：上游 feed 零媒体字段 ⇒ 不覆盖，也不得声称已清干净。
3. **basket 恢复有损**；**损失性 execute 未实现**（属阶段二）。
4. **FTS 只报告不自愈**；规则阈值全是**暂定值**。
5. **本 sprint 无前端入口**：`GET /api/moderation/xbot/candidates` 只能经 API 调用
   ⇒ **不得**称「阶段二执行已开发」。

## 测试与审计

> ⚠️ 以下全部是 **2026-11-19 14:28 p7 warden 对已合并树 head `271a35b` 的独立实跑**
> （该 head 与 squash merge `10f3957` **树等价**）。**本轮（t97u/t98u）文档收尾本地测试 = NOT_RUN**。

- 全量 **2277 passed / 2 skipped**（399.59s，一次跑绿）
- 5 个既有文件**原位**单跑 **61 passed**（机制已消除 ⇒ 绿灯可直接引用）
- 新增 7 个测试文件 **123 passed**
- 变异负控打中（原位跑真在测 worktree 代码）
- p7 warden 多轮 + 13:44 最终 diff 复核（28 个文件，含 untracked）
- **2026-11-19 14:2x 对已合并树再审**：`GATE: PASS / P0=0 / P1=0 / P2=3`
  （3 条 P2 全是 `STATUS.md` 叙述过时，不影响代码 / 配置 / 迁移 / 生产行为）

## 下一道 dad GO

**阶段一已关闭**。本 sprint 范围内**未做**、各自需**单独 GO** 的：
阶段二（自动拦截 / 屏蔽作者 / 归档 execute）、G2 配置、G3 批量处置。

⚠️ **阶段一关闭 ≠ 上述限制解除**：缺 dad 人工真值 ⇒ 仍无准确率；
上游零媒体字段 ⇒ 媒体盲区仍在。

交接包：`handoff-xbot-content-quality-2026-11-18.md`（vault 索引同 sprint 目录）。
Closeout 报告：`verify-2026-11-19.md`（3-1-1，vault 同目录）。
