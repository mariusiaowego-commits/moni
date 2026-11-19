# 独立只读审计报告：PR #256（已核查文章提供重跑入口）

## 1. 结论
**APPROVE WITH NITS**

本次改动以 2 个文件（+33 / −3）的极小修改，精准解决了白名单扩充或 prompt 调优后「已核查文章无法重跑」的线上痛点。显示条件逻辑清晰，测试钉住了 DOM 契约，测试集全部通过。针对用户并发点击与快速核查后端防呆缺口存在 1 处中低风险（P2），但不阻碍 PR 合并。

---

## 2. 重点核对点逐项结论

### 1. 显示条件与字段状态判断
- **结论**：**成立且严密**。
- **事实依据**：
  - 查验 `moni/llm/jobs.py` 第 478 行，`task_type == "fact_check"`（快速核查）执行的 SQL 为：
    ```sql
    UPDATE articles
    SET fact_check_conclusion = ?, fact_check_method = 'llm_inference', fact_checked_at = ?, has_fact_check = 1
    WHERE id = ?
    ```
    它**绝不会将 `has_web_fact_check` 置为 0**（仍保持原有的 1）。
  - 若在已有联网核查结果的文章上开放「快速核查」，会导致 `fact_check_conclusion` 被替换为单模型推断，但前端 `has_web_fact_check` 徽标、证据链依然存在，造成「徽标显示已联网核查、正文却是模型推测」的数据混乱。
  - 因此：
    - **联网核查**：无 `x-show`，**无条件常驻**（未核查显示「联网核查」，已核查显示「重新联网核查」）；
    - **快速核查**：`x-show="!articleDetail?.has_web_fact_check"`，仅在未跑过联网核查时提供（未核查显示「事实核查」，已快核过显示「重新核查」）。
  - 这一状态机分级设计非常精准。

### 2. 重跑是否会留下脏状态
- **结论**：**不会残留脏状态或永久不一致**。
- **分析**：
  - **联网核查重跑**：
    1. 前端点击后立即触发乐观占位符 `⏳ 联网核查中...`（见 `_articleJobPlaceholder`），`fact_check_conclusion` 变为占位符；
    2. 后端执行 `_persist_fact_check_plus` 时，在写入新结果前显式执行 `DELETE FROM fact_check_evidence WHERE article_id = ?`，并批量写入新证据链；
    3. 前端 watcher 轮询到 `job.status === 'done'` 后，先调用 `loadArticleEvidence(articleId)` 刷新证据，再调用 `_reloadArticleAfterJob` 回读文章详情，两者均受 `isCurrentReaderArticle` 守卫保护；
    4. 即使任务 failed/cancelled，`_reloadArticleAfterJob` 也会回读恢复真实 DB 数据，不会永久悬挂空证据或卡死占位。
  - **快速核查重跑**：
    由于它被限定在 `!has_web_fact_check` 才会渲染，能跑快速核查的文章原本就没有证据链，因此不存在残留旧证据链的污染问题。

### 3. 点击频率、并发与重入分析
- **结论**：**联网核查存在跨进程/跨端 409 防呆，但快速核查后端缺 409 防呆（详见 Finding P2）**。
- **分析**：
  - **联网核查（`fact_check_plus`）**：
    前端在单页内使用 `:disabled="factCheckLoading"` 防重复点击；后端 `fact_check_plus_article`（[moni/web/routes/fact_check_plus.py:271](file:///Users/mt16/.codex/worktrees/f2c1/moni/moni/web/routes/fact_check_plus.py#L271)）显式调用 `_has_active_job(article_id)`：
    ```python
    if _has_active_job(article_id):
        raise ConflictError(message=f"Article {article_id!r} already has active fact_check job")
    ```
    即使用户换标签页或刷新重进重新点击，后端也会返回 HTTP 409 Conflict，前端捕获后优雅提示错误，不会造成任务重复入队。
  - **快速核查（`fact_check`）**：
    后端 `fact_check_article`（[moni/web/routes/articles.py:575](file:///Users/mt16/.codex/worktrees/f2c1/moni/moni/web/routes/articles.py#L575)）**没有**调用 `_has_active_job`。如果用户正在跑快速核查时刷新页面，重新点击「重新核查」，可能会向 `llm_jobs` 插入两个重叠的 pending 任务。

### 4. 测试有效性
- **结论**：**测试有效钉住了核心语义约束**。
- `tests/test_frontend_article_job_watch.py::test_rerun_entry_stays_visible_after_a_check_exists` 解析 `index.html` 中的 DOM 结构，明确验证了：
  1. `!has_fact_check` 隐藏逻辑不再存在；
  2. 快速核查必须带 `x-show="!articleDetail?.has_web_fact_check"`；
  3. 联网核查不得有 `x-show` 隐藏条件；
  4. 动态文本表达式 `'重新联网核查' : '联网核查'` 存在。
  测试不是复述静态标签，而是验证了状态机的显示互斥约束。

### 5. a11y / i18n / 文案审查
- **结论**：**合格**。
- 文案使用自然中文（`重新核查` / `重新联网核查` / `核查中...`）；
- 两个按钮均补充了悬停 `title` 说明：
  - `已核查过的文章可以重跑，旧结论会被替换`
  - `已联网核查过的文章可以重跑，旧证据链会被新结果替换`
  使用户在点击前能明确知晓这是覆盖式重跑，降低误操作顾虑。
- 按钮在 loading 状态下 `factCheckLoading` 保持 disabled，并有文字变更为 `核查中...`，无盲区。

---

## 3. Findings 清单

### [P2] 快速核查接口 `POST /api/articles/{id}/fact-check` 缺少 `_has_active_job` 冲突拦截
- **路径**：[moni/web/routes/articles.py:575-605](file:///Users/mt16/.codex/worktrees/f2c1/moni/moni/web/routes/articles.py#L575-L605)
- **原因**：
  在 `moni/web/routes/fact_check_plus.py` 中有完善的 `_has_active_job` 检查（返回 409 Conflict），防止同篇文章重复提交任务。但 `articles.py` 中的快速核查接口未接入该防呆。若用户在快速核查任务未执行完前刷新页面，按钮解禁后再次点击，会向队列提交重复的 `fact_check` 任务。
- **严重度**：P2（中低）。因为快速核查耗时通常较短（几秒至十几秒），且当前入口主要推荐联网核查，触发窗口较窄。
- **建议**：后续 PR 可将 `_has_active_job` 提取为通用依赖，在 `articles.py` 的 `fact_check_article` 入口补齐 409 防呆。

---

## 4. 实际审计操作与执行记录

1. **查验 Git 状态与改动**：
   - `git -C /Users/mt16/.codex/worktrees/f2c1/moni status`
   - `git -C /Users/mt16/.codex/worktrees/f2c1/moni diff origin/main...HEAD`
   - `git -C /Users/mt16/.codex/worktrees/f2c1/moni show f5a58ae`
2. **后端落库与字段链路审查**：
   - 查看 `moni/llm/jobs.py` 中 `fact_check` 与 `fact_check_plus` 的更新字段差异；
   - 确认 `fact_check` 是否影响 `has_web_fact_check`（确认不影响）；
   - 查看 `fact_check_plus.py` 与 `articles.py` 的重入检查逻辑。
3. **单元与前端测试**：
   - `/usr/local/bin/python3 -m pytest tests/test_frontend_article_job_watch.py -p no:cacheprovider -q`（**9 passed**）
   - `/usr/local/bin/python3 -m pytest tests/test_frontend_render_hygiene.py -p no:cacheprovider -q`（**36 passed**）
   - `/usr/local/bin/python3 -m pytest -k "fact_check" -p no:cacheprovider -q`（**107 passed, 2230 deselected**）
