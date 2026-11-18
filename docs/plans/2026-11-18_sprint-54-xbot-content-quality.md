# sprint-54 — xbot_feed 劣质内容治理（阶段一）

> **状态：代码与测试已就绪，停在 commit gate 等 dad 批准。**
> 未 commit / push / PR / merge / tag / 部署 / 未改生产配置 / 未对生产写。
> sprint-54 · id `26111703` · 2026-11-17 ~ 2026-11-18 · base `fd611761027d5bcacfffb19ee34ec76baf06c477`

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

## ✅ schema 版本落差：已通过合并处理

✅ **schema 落差已处理**：合并前本地是 **45**、`origin/main` 已是 **46/47**
（`046_fact_check_summary.sql` / `047_fact_check_gate.sql`）⇒ 本分支**未新建、未重编号**迁移，
已通过 merge 干净继承 main 的 **`_CURRENT_VERSION` = 47** ⇒ **不会带着旧版本号**合并。

## 已知限制

1. **草标不是 dad 真值** ⇒ 没有真值就没有精确率/召回率/误杀率。
2. **媒体类硬盲区**：上游 feed 零媒体字段 ⇒ 不覆盖，也不得声称「已清干净」。
3. **basket 恢复有损**；**损失性 execute 未实现**（属阶段二）。
4. **FTS 只报告不自愈**；规则阈值全是**暂定值**。

## 测试与审计

- 全量 **2114 passed / 2 skipped**（p7 独立复跑一致）
- 5 个既有文件**原位**单跑 **61 passed**（机制已消除 ⇒ 绿灯可直接引用）
- 新增 7 个测试文件 **120 passed**
- 变异负控打中（原位跑真在测 worktree 代码）
- p7 warden 五轮 + 13:44 最终 diff 复核（26 个文件，含 14 个 untracked）

## 下一道 dad GO

commit → push/PR → merge → tag/部署 → G2 配置 → G3 批量处置。
**本轮一件都没做。**

交接包：`handoff-xbot-content-quality-2026-11-18.md`（vault 索引同 sprint 目录）。
