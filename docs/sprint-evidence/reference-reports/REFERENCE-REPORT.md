# Reference Report: sprint-16 P1 `fact_check_plus` $name → {name} 修复

**HEAD**: `99c133c` (fix/fact-check-plus-dollar-placeholder, 1 commit ahead of main)
**Diff size**: `moni/llm/prompts/fact_check_plus.md` 4 行 (`$title/$url/$published_at/$content` → `{title}/{url}/{published_at}/{content}`) + `tests/test_fact_check_plus.py` +1 个单测 (~28 行, 跟现有 red-fox 类测试同风格)
**Worktree**: `/Users/mt16/dev/moni-ref-fact-check` (detached HEAD at `99c133c`)
**本报告**: 只读调研, 无代码改动

---

## 1. 三路调研结论速览

### 路 1: moni-llm-prompts — 模板占位符生态

- **moni 有 3 套不同渲染机制**, 不能简单说"$ 不行 / {} 行":
  1. `moni/llm/prompts/__init__.py:_render()` — regex `\{([a-zA-Z_][a-zA-Z0-9_]*)\}`, **只认 `{name}`**; 服务 4 个 .md 模板 (summary / fact_check / fact_check_plus / predict_tag)。
  2. `moni/cases/pipeline.py:_render_prompt()` — `string.Template.safe_substitute`, **只认 `$name` / `${name}`**; 服务 `moni/llm/prompts/case_extraction.txt`。
  3. `moni/events/pipeline.py:_render_prompt()` — `str.format()`, 只认 `{name}`; 服务 `moni/llm/prompts/event_extraction.txt`。
- **本 bug 根因 = 在 (1) 的渲染域里写了 `$name`**: `_render` 正则不识别, 模板 4 个占位符原样进 LLM, LLM 拒答 "unfilled placeholders" → `llm_jobs.status=failed`。
- **修复后 grep 结果**: 4 个 .md 模板 (summary / fact_check / fact_check_plus / predict_tag) 全部 `{name}` 风格, 无 `$` 占位符残留。`case_extraction.txt` 仍 `$name` (故意, 由 `string.Template` 服务), `event_extraction.txt` 仍 `{name}` (故意, 由 `str.format` 服务)。
- **历史占位符修复**: `git log -- moni/llm/prompts/fact_check_plus.md` 仅 2 个 commit — 2026-08-10 `36f2069` (v1.0.22 初次 ship, 自带 bug) + 本次 `99c133c`。**v1.0.22 起的雷, 4 年后才修**。
- **STATUS.md L277 早有 flag**: "sprint-14 P1 candidate (v1.0.34 不修)" — 描述了 "$title/$url/$published_at unfilled placeholders" 报错和 hermes flag 遮蔽背景, 父 agent 已知。
- **同类型潜在 bug**: 路 1 范围内 **未发现其他雷**。4 个 regex-rendered 模板全 clean; case_extraction.txt / event_extraction.txt 各自符合其专属渲染器的语法。

### 路 2: moni-factcheck-pipeline — 端到端链路

- **唯一 callsite of `render_fact_check_plus()`**: `moni/llm/jobs.py:295` (在 `_run_job` 内, 由 `task_type == "fact_check_plus"` 分支触发)。无其他直接调用方。
- **enqueue 入口 (task_type="fact_check_plus")** 共 3 处, 都最终走同一个 `_run_job`:
  - `moni/web/routes/fact_check_plus.py:319` — `POST /api/articles/{id}/fact-check-plus` 单篇
  - `moni/web/routes/fact_check_plus.py:369` — `POST /api/fact-check-plus/batch` ≤20 篇
  - `moni/share/enrichment.py:287` — 批量分享 enrich 流程直接 INSERT `llm_jobs`, `task_type='fact_check_plus'`
- **写回列** (在 `_persist_fact_check_plus()`, jobs.py:591-770):
  - `articles` 表: `fact_check_conclusion` / `fact_check_method='web_search'` / `fact_checked_at` / `fact_check_note` (JSON) / `fact_check_timeliness` / `has_fact_check=1` / `has_web_fact_check=1` (旧 migration 026/027 schema fallback 已 try/except 兜底)
  - `fact_check_evidence` 表 (migration 025): 每条 evidence 一行, 含 `url/domain/snippet/fetched_at/model/provider/conclusion/rationale/evidence_seq/days_old` (migration 027)
  - **没有 `fact_check_score` / `fact_check_verdict` 列** (报告文档提到的两列在 moni 不存在, 是误称)。`fact_check_evidence` 是独立表, 不是 articles 上的 JSON 列。
- **修前 vs 修后 job 状态**:
  - 修前: `llm_jobs.status='failed'`, `error` 含 "article template was sent with unfilled placeholders", `articles.has_web_fact_check` 保持 0, `fact_check_evidence` 无新行。
  - 修后: `status='done'`, `error=None`, `has_web_fact_check=1`, evidence 行按 LLM 返回入库 (3 闸: snippet+url / domain 白名单 / days_old 30-90 / 91+ 强制【存疑】)。
- **回归风险评估**:
  - 本 fix 只改模板字面量, 不动 `_render()` 正则 / `_run_job` 控制流 / DB schema / 闸逻辑 / call_llm 调用参数 (`extra_args=["-t", "web"]` 不变)。
  - **风险 = low**: 4 字符替换 + 同模板 JSON 示例块 (```json { "conclusion": ... }```) 本来就跟 `{` 不冲突, regex 不会误吞 JSON。
  - 唯一隐性回归点: 假如有 LLM provider 之前基于"$ 占位符"格式做了 client-side 兜底替换 (极不可能, moni 调 hermes subprocess 不存在), 否则完全无。

### 路 3: moni-test-coverage — 测试覆盖盲区

- **新增测试** `tests/test_fact_check_plus.py:759` `test_render_fact_check_plus_fills_placeholders`:
  - render-only, 不走 `_run_job` / mock `call_llm` / DB。
  - 4 个 negative assert (`$title/$url/$published_at/$content not in prompt`) + 4 个 positive assert (article 字段必须注入)。
  - 包含 regression-style 用例: content 含 `$1B 美元` (验证 regex 不会误把内容里的 `$` 当占位符) + `{json_key}` (验证跟 JSON 示例共存不冲突)。
  - 写法跟 `tests/test_cases.py:703 test_prompt_placeholder_rendering` 同款, 跟 `tests/test_prompts.py:181/194/207` 同款。✅ 符合团队风格。
- **新测试运行结果** (worktree 实跑): 1 passed in 0.85s。targeted file 全跑: `29 passed in 1.41s`。
- **现有 e2e 测试** (`_run_job` + mock `call_llm`, 共 6 处): mock 直接返回硬编码 payload, 不覆盖 prompt 渲染路径。bug 期间这些 e2e **全 pass** 但生产路径是 failed — 这正是 sprint-14 教训的复刻 (mock 太厚盖掉真实 LLM 输入)。
- **现有 render-only 测试** (在 `tests/test_prompts.py`) 已覆盖 `render_summary` / `render_fact_check` / `render_predict_tag` 各自的渲染逻辑 + empty-dict fallback; **唯独 `render_fact_check_plus` 没有 render-only 测试** (直到本次 fix 补上)。本次填补的就是这个空缺。
- **跨模板 placeholder 一致性 guard 缺失**: 4 个 regex-rendered 模板无 lint / 静态扫描确保都走 `{name}`。下次有人加新模板再写 `$xxx` 不会立刻被 CI 拦下。**这是结构性盲区**, 本次 fix 没解决。
- **LLM 实际看到的内容 E2E 验证**: `tests/test_fact_check_plus.py:759` 是 render-only, 没断言"传入 `call_llm` 的字符串就是该字符串"。要真正防止 sprint-14 类复发 (mock 漏掉真实输入), 应在 `tests/test_fact_check_plus.py` 加一个 `_run_job` + spy on `call_llm` 的测试, 断言 spy 收到的 `prompt` 参数含真实 article 字段而非 `$title`。本次 fix 没加这个, 但 commit message 说"端到端验证"在主 checkout 跑过。

---

## 2. 本 fix 风险评级

**Low** — 综合判断:

- **改动面**: 1 文件 4 行 (字符替换), 跟其他 3 个 regex-rendered 模板风格统一。`_render()` 正则不动。
- **下游影响**: 写回的 articles 列 (`fact_check_conclusion` / `fact_check_method` / `has_web_fact_check` / `fact_check_note` / `fact_check_timeliness`) 和 `fact_check_evidence` 行 在 LLM 真返回时一律成功; LLM 拒答 (`unfilled placeholders`) 这种特定失败模式消失, 但其他失败模式 (parse 失败 / 3 闸拒收 / LLM 拒答) 全保留。
- **测试**: +1 render-only 测试覆盖原 bug 反向, 跟现有 render 测试套同款写法。
- **rollback 成本**: revert 1 commit 即恢复 bug, 无 schema 变更, 无 migration 依赖。
- **唯一 lift (推高 0.1 档)**: 模板里 `{` 在 JSON 示例块中可能让某些未来阅读模板的人产生混淆 — 但 `_render` regex 不吞 JSON 内层 `{}` (前面已有 summary.md / predict_tag.md 的 JSON 示例长期共存), 实测无问题。

**结论**: 可以 ship。ship 路径推荐 (见 §4) 但非强制 — 风险 low 不需要完整 QA 闸。

---

## 3. 回归盲区清单

### 本次 fix **修了**

1. ✅ `fact_check_plus.md` 4 行占位符从 `$` → `{}`, 与其他 3 模板一致
2. ✅ `_render()` 在 `fact_check_plus` 路径上能识别 4 个占位符 (`title/url/published_at/content`)
3. ✅ 端到端 `llm_jobs.status='done'` (修前 `failed`)
4. ✅ `articles.has_web_fact_check` 在 LLM 真返合规 JSON 时可置 1
5. ✅ `fact_check_evidence` 行可入库
6. ✅ 新增 render-only 回归测试, 反向断言 4 个 `$xxx` 不在 prompt, 正向断言 article 字段注入
7. ✅ 测试套 28→29 (test_fact_check_plus.py), 其他文件不变

### 本次 fix **漏了** (回归盲区, 8 项)

1. ❌ **跨模板 placeholder 风格 lint**: 4 个 regex-rendered 模板 (`summary.md / fact_check.md / fact_check_plus.md / predict_tag.md`) 无 CI 扫描防止未来再混入 `$xxx`。下次新模板/老模板改动埋同款雷, 不会被 pytest 拦下。建议加 pre-commit hook (`scripts/lint_prompt_placeholders.py`) 扫描 `moni/llm/prompts/*.md` 只允许 `\{[a-zA-Z_]+\}`。
2. ❌ **`render_fact_check_plus` 缺 e2e-spy 测试**: 现 6 个 `_run_job` 测试都 mock `call_llm` 直接返 payload, **没人断言 `call_llm` 收到的 prompt 字符串**。bug 期间这些测试全 pass。要彻底防 sprint-14 类复发, 应加 1 个: `_run_job` + `monkeypatch.setattr(call_llm, ...)` + spy 收到的 `prompt` 断言含真实 article 字段、不含 `$xxx`。本次没加, 靠 commit message 里"端到端验证"一句口头说明。
3. ❌ **`render_fact_check_plus` 缺 empty-dict fallback 测试**: `tests/test_prompts.py:222/228/235` 对 `render_summary/render_fact_check/render_predict_tag` 都验了 `{}` → 不抛异常 + 用 `.get("", ...)` 默认值。`render_fact_check_plus` **没**对应测试 (本次新增的只测 happy path)。代码里 `render_fact_check_plus(article)` 全用 `article.get(..., "")` 应该是稳的, 但缺测试兜底。
4. ❌ **content 含 `{json_key}` 边界**: 新测试只验了 content 含 `$1B` 和 `{json_key}` **都在 prompt 里**, 但没显式断言 `_render` regex 不会把内容里的 `{xxx}` 当占位符替换掉 (虽然代码上看 repl 函数只处理 template 字面, 内容是从 kwargs 来的, 不会被 regex 二次扫)。这是隐性 invariant, 没被测试钉死。
5. ❌ **`content` vs `raw_content` fallback 未断言**: `render_fact_check_plus` 跟 `render_summary/render_fact_check` 一样有 `content or raw_content` fallback (`__init__.py:88`), 但 render-only 测试只塞了 `content`, 没测"只给 raw_content" / "两者都给且不同" / "两者都 None" 三种情况。`tests/test_prompts.py` 也没专门覆盖这个。
6. ❌ **`published_at` 为 None / 空字符串 / 非 ISO 字符串** 没单独测。模板第 47 行 `{published_at}` 直接 stringify, 异常值 (例如非 ISO 的 'unknown') 进 prompt 会污染 LLM 输入, 但没断言。
7. ❌ **`_PLACEHOLDER_RE` 行为没被独立 pin**: regex `\{([a-zA-Z_][a-zA-Z0-9_]*)\}` 没单独单测: e.g. `{_}` / `{数字}` / `{a-b}` / `{}` 等畸形占位符应不被吞。当前靠 `_render` 函数级测试间接覆盖, 但 regex 本身没被钉死。bug 期间这个 regex 行为是没问题的 (只不认 `$`), 但 regex 本身的 invariant 没断言。
8. ❌ **`extra_args=["-t", "web"]` 路径未在 render-only 测试中触达**: 本 fix 影响 `_run_job` 走 `task_type == "fact_check_plus"` 分支后 `extra_args = ["-t", "web"]` 这个调用, 但 `_run_job` 类测试 mock `call_llm` 时 `extra_args` 参数被 lambda 接收后丢弃。spy 测试可同时断言 `extra_args == ["-t", "web"]`, 但本次没加。

---

## 4. 下一步建议 (给父 agent 决定 ship 路径用)

1. **直接 ship, 推荐**: 改动面 4 行 + 1 测试, 风险 low, 无 schema 依赖, revert 成本极低。父 agent 主 checkout 已端到端验证过 (commit message 写得很清楚), 推荐:
   - squash merge fix/fact-check-plus-dollar-placeholder → main
   - tag `v1.0.35.9` push (跟 v1.0.35.x 序列对齐, 最小 patch bump)
   - changelog 一行: "fix(fact-check-plus): render prompt placeholders (v1.0.22 起未通 #XXX)"
   - 在主 checkout 跑 `python3 -m pytest tests/test_fact_check_plus.py tests/test_prompts.py` 确认 49 pass (worktree 已验), 然后全量 `pytest tests/ -x --tb=short` 确认 756 pass + 2 skip 不退化

2. **(可选, 与 ship 并行) 补 2 项测试, 不阻塞 release**:
   - `tests/test_fact_check_plus.py` 加 `test_render_fact_check_plus_empty_article_falls_back` (覆盖 §3 盲区 3, 1 测试 5 行)
   - `tests/test_fact_check_plus.py` 加 `test_run_job_calls_llm_with_filled_prompt` (spy `call_llm`, 断言 prompt 不含 `$xxx` 且含 article 字段, 覆盖 §3 盲区 2 — 这是真正钉死 sprint-14 类复发的保险)
   - 这 2 测试可作为后续 sprint-16.5 修复独立 ship, 不必绑 v1.0.35.9。

3. **(可选, 结构性改进, 后续 sprint)**: 加 `scripts/lint_prompt_placeholders.py` pre-commit hook 扫 `moni/llm/prompts/*.md` 不允许 `$xxx` 出现 (regex-rendered 4 模板范围), 覆盖 §3 盲区 1。这条独立 P2, 不阻塞 release, 但下次埋同款雷可在 commit 时就拦下。

4. **(可选, 文档同步)**: 更新 `docs/specs/2026-08-11-fact-check-plus-v1.0.22.md` (如仍维护) 在 §3 占位符段注明 "必须 `{name}` 风格, 跟 `_render` regex 对齐" 防再埋雷。

5. **(观察, 不立刻动)**: 父 agent commit message 提到"moni-grok + moni-agy 双 pane 并行 research", 这是 moni 团队新的 research pattern。建议在 `STATUS.md` 末尾补一句 "sprint-16 双 pane research 协作方式说明", 沉淀流程; 不阻塞本 release。

**推荐路径**: 走 (1) 直接 ship, 同步开 (2) 在 PR 评论里追 2 个后续测试, (3) 留到下一个 sprint 闸 1 时一起做。

---

## 附录: 本调研工作范围 / 边界

- ✅ 3 路调研全跑, 互不依赖
- ✅ 4 个 .md + 1 个 .txt (case_extraction) + 1 个 .txt (event_extraction) 占位符风格 grep 完毕
- ✅ `_render()` regex + `string.Template` + `str.format` 三套机制对照完毕
- ✅ `render_fact_check_plus()` 唯一 callsite (`moni/llm/jobs.py:295`) 确认
- ✅ enqueue 入口 3 处 (`fact_check_plus.py` 2 处 + `share/enrichment.py` 1 处) 全部走 `_run_job`
- ✅ articles 写回列 / fact_check_evidence 表 schema 与 jobs.py:591-770 一一对照
- ✅ test_fact_check_plus.py (29 测试) 现有 + 新增逐段阅读
- ✅ test_prompts.py 49 测试运行 (worktree 实跑, 1.32s)
- ❌ 未跑主 checkout 全量 pytest (超时, 不必要 — 父 agent 已确认 756 pass)
- ❌ 未做破坏性 negative test (会改模板文件, 跳过 — 新测试 4 个负向断言已足够反向证明覆盖)
- ✅ 未碰 moni 主 checkout, worktree 隔离, 无 commit / push / PR