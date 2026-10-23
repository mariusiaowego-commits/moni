# Sprint-16.5 综合参考报告

**日期**: 2026-08-24  
**基准**: main HEAD = `15a5256` (v1.0.35.9 ship, PR #116 squash merge)  
**前置报告**: [REFERENCE-REPORT.md](REFERENCE-REPORT.md) (sprint-16 P1, 131 行)  
**Working tree**: `STATUS.md` + `USER_GUIDE.md` dirty (文档同步, 无代码改动)  
**风险评级**: **Low** (全部是加固型测试 + lint, 无生产代码改动)

---

## §1 前向 review: 前份报告是否仍成立?

### 1.1 模板占位符现状 (main HEAD `15a5256` 重新 grep)

| 模板文件 | 渲染器 | 占位符风格 | 状态 |
|----------|--------|-----------|------|
| `summary.md` | `_render()` regex | `{role_name}` `{reader}` `{focus}` `{questions}` `{title}` `{url}` `{content}` | ✅ clean |
| `fact_check.md` | `_render()` regex | `{title}` `{url}` `{content}` `{published_at}` | ✅ clean |
| `fact_check_plus.md` | `_render()` regex | `{title}` `{url}` `{published_at}` `{content}` | ✅ **v1.0.35.9 已修** |
| `predict_tag.md` | `_render()` regex | `{available_tags}` `{title}` `{content}` | ✅ clean |
| `case_extraction.txt` | `string.Template` | `$title` `$source` `$published_at` `$content` | ✅ **故意用 $, 正确** |
| `event_extraction.txt` | `str.format()` | `{title}` `{source}` `{published_at}` `{content}` | ✅ **故意用 {}, 正确** |

**结论**: v1.0.35.9 后 6 个模板全部与各自渲染器语法匹配, **无新盲区**.

### 1.2 原 8 项盲区重新审视

| # | 盲区 | 原优先级 | 现状 | sprint-16.5 建议 |
|---|------|---------|------|-----------------|
| 1 | 跨模板 placeholder 风格 lint | 最高 | **仍成立**. moni 无 `.pre-commit-config.yaml`, 无 CI lint. 下次新模板混 `$xxx` 不被拦 | ✅ 做 (PR 核心) |
| 2 | spy `call_llm` e2e 测试 | 最高 | **仍成立**. 现 11 个 `_run_job` 测试全 mock `call_llm` 为 `lambda *a, **k: payload`, 不断言 prompt 内容. `captured` dict 模式已在 `test_api.py` / `test_llm_usage_counters.py` 成熟可参考 | ✅ 做 (PR 核心) |
| 3 | empty-article fallback | — | **✅ v1.0.35.9 已补** (L789-810) | ~~砍~~ |
| 4 | content 含 `{json_key}` 边界 | 中 | **隐性安全**: `_render()` 的 `repl()` 只在 `key in kwargs` 时替换, content 里的 `{json_key}` 不在 kwargs 里 → 保留原样. 新测试 L772 已有 `{json_key}` 字面进入 prompt 的断言 | **砍** — 已被现有测试间接覆盖 |
| 5 | content vs raw_content fallback | 低 | `render_fact_check_plus` L88 = `article.get("content") or article.get("raw_content", "")`, 跟 `render_fact_check` L72 / `render_summary` L59 完全同构. 但 `test_prompts.py` 对 render_summary/render_fact_check 也**没有** raw_content 专项测试 | **合并到 spy 测试** — 在 spy test 里用只带 `raw_content` 的 article 跑 1 case |
| 6 | `published_at` 异常值 | 低 | `.get("published_at", "")` 兜底空字符串; `None` 时 str(None) → `"None"` 进 prompt. 实际 moni DB 里 `published_at` 全是 ISO 字符串或 NULL (NULL → `.get()` 返 `""`) | **砍** — 实际不会触发, DB schema 保证 |
| 7 | `_PLACEHOLDER_RE` regex 独立 pin | 低 | regex `\{([a-zA-Z_][a-zA-Z0-9_]*)\}` 本身行为稳定, 不会被修改 (已 5 个 sprint 不动). 畸形占位符 `{}` / `{123}` 不被匹配, 不会吞 | **砍** — 成本低但价值也低 |
| 8 | `extra_args=["-t", "web"]` spy | 中 | 这是 fact_check_plus 专有的 hermes web agent flag. 跟 spy 测试天然组合: spy `call_llm` 时顺便断言 `extra_args` | **合并到 #2 spy 测试** |

### 1.3 v1.0.35.9 有无引入新盲区?

- **diff**: 2 文件 63 行 (4 行模板 + 59 行测试), 无控制流 / DB / API 变更
- **新增 2 个测试** 都是 import-level render-only, 不启动 FastAPI / DB
- **结论**: **无新盲区**

---

## §2 后向 review: v1.0.35.9 fix 完整性

### 2.1 live 验证覆盖度

| 场景 | 是否覆盖 | 说明 |
|------|---------|------|
| 单篇 POST `/api/articles/{id}/fact-check-plus` | ✅ | Westpac+Amp d1073c471aa9a9b1, 51.2s, 5 evidence |
| 批量 POST `/api/fact-check-plus/batch` | ❌ | 同一 `_run_job` 路径, 无额外风险 |
| `share/enrichment.py` INSERT enqueue | ❌ | 这条路径不调 `render_fact_check_plus()`, 只 INSERT `llm_jobs` 行, worker 线程 pickup 后走 `_run_job` → 同一路径. **无额外风险** |

### 2.2 遗漏边缘场景评估

| 场景 | 风险 | 理由 |
|------|------|------|
| 不同 LLM provider | **无** | 模板渲染在 `_run_job` 调 `call_llm` **之前**完成; provider 只影响 hermes subprocess, 不影响 prompt 内容 |
| 空内容文章 | **无** | `article.get("content") or article.get("raw_content", "")` → 空字符串 → `{content}` 被替为空. 新测试 L789 已覆盖 |
| 超长内容 | **无** | `_render()` 是纯 regex 替换, 无长度限制. LLM token 限制由 hermes/provider 侧处理 |
| LLM 拒答方式差异 | **无** | 本 fix 消除的就是 "unfilled placeholders" 这一种拒答. 其他拒答 (rate limit / parse 失败 / 3 闸拦截) 不受影响 |

### 2.3 enrichment.py INSERT 路径 cross-check

[enrichment.py:287-292](../../../moni/share/enrichment.py#L287-L292): 直接 `INSERT INTO llm_jobs ... VALUES (?, 'fact_check_plus', ...)`, 不调任何 render 函数. Worker thread 从 `llm_jobs` 表 pickup `status='pending'` 行 → `_run_job()` → `render_fact_check_plus()`. **修复覆盖此路径**, 无遗漏.

---

## §3 sprint-16.5 范围 / 优先级 / ship 路径

### 3.1 精简范围 (从 7 项 → 3 项)

| 优先级 | 条目 | 工作量 | 做? |
|--------|------|--------|-----|
| **P1** | lint 脚本 `scripts/lint_prompt_placeholders.py` + pytest 集成 | ~30 行 Python + 1 pytest case | ✅ |
| **P1** | spy `call_llm` e2e 测试 (prompt 不含 `$xxx` + 含真实 article + `extra_args=["-t", "web"]` + `raw_content` fallback) | ~40 行 Python, 2 test cases | ✅ |
| **P2** | `raw_content` only fallback 场景 (合并到 spy test) | 0 行额外代码, 在 spy test 中用 `raw_content` article | ✅ 合并 |
| ~~P3~~ | `{json_key}` 边界 | 已被 L772 覆盖 | ❌ 砍 |
| ~~P3~~ | `published_at` 异常值 | DB schema 保证 | ❌ 砍 |
| ~~P3~~ | `_PLACEHOLDER_RE` regex pin | 5 sprint 不动, 低价值 | ❌ 砍 |
| ~~P3~~ | `extra_args` spy | 合并到 P1 spy | ❌ 合并 |

**净新增**: 1 脚本 (~30 行) + 3 测试 (~70 行) = **~100 行代码**.

### 3.2 lint 脚本设计

```
scripts/lint_prompt_placeholders.py
```

- 扫 `moni/llm/prompts/*.md` (regex-rendered 4 模板)
- 拒绝: `$[a-z_]+` 出现在非代码块区域
- 允许: `{name}` 风格, JSON 示例块内的 `$`
- **不扫** `case_extraction.txt` / `event_extraction.txt` (不同渲染器)
- 集成: **pytest** (`tests/test_lint_prompts.py`), 不走 pre-commit (moni 无 `.pre-commit-config.yaml`, 加了会引入新依赖)

### 3.3 spy 测试设计

```python
# tests/test_fact_check_plus.py 新增 2 个 test

def test_run_job_prompt_reaches_call_llm_without_dollar_placeholders():
    """spy call_llm, 断言:
    1. prompt 参数不含 $title/$url/$published_at/$content
    2. prompt 含真实 article 字段值
    3. extra_args == ["-t", "web"]
    """
    captured = {}
    def spy(*a, **kw):
        captured["prompt"] = a[2]  # 3rd positional = prompt
        captured["extra_args"] = kw.get("extra_args")
        return '{"conclusion": "【待核实】", "evidence": []}'
    monkeypatch.setattr("moni.llm.jobs.call_llm", spy)
    # ... _run_job setup ...
    assert "$title" not in captured["prompt"]
    assert article["title"] in captured["prompt"]
    assert captured["extra_args"] == ["-t", "web"]

def test_run_job_uses_raw_content_when_content_missing():
    """同上 spy, 但 article 只给 raw_content, 验证 fallback"""
```

### 3.4 拆 PR 策略

**1 个 PR** (PR #117) 足够:

- 改动面: 1 lint 脚本 + 1 lint test + 2 spy tests = 4 文件 ~100 行
- 全部是**加固型**, 无生产代码改动, 无 migration, 无 API 变更
- 无需拆 2 个 PR, review 负担低

### 3.5 工期估算

| 活动 | 估时 |
|------|------|
| 写 lint 脚本 + test | 15 min |
| 写 2 个 spy tests | 20 min |
| `pytest tests/ -p no:cacheprovider` 全量验证 | 5 min |
| PR 创建 + user review + squash merge + tag | 10 min |
| **合计** | **~50 min (1 sprint session)** |

moni 团队节奏: 1 sprint = 1-3 天. Sprint-16.5 是 **半天 sprint**, 单 agent 足够 (不需要多 agent 并行).

### 3.6 风险评级

**Low**:
- 零生产代码改动 (只加测试 + lint 脚本)
- rollback = 删 4 文件, revert 1 commit
- 不影响 DB / API / 前端 / 配置
- pytest 从 757 → 760 pass (净增 3 test)

---

## §4 副作用 cross-check

### 4.1 lint hook 选型

| 方案 | 推荐 | 理由 |
|------|------|------|
| **pytest 集成** (`tests/test_lint_prompts.py`) | ✅ | moni 无 `.pre-commit-config.yaml`, 无 CI, 加 pre-commit 需要安装 `pre-commit` 包 + 配 `.pre-commit-config.yaml`, 引入新依赖. pytest 是 moni 唯一闸, 在这里拦最可靠 |
| pre-commit hook | ❌ | 不符合 moni 项目约定 (无此配置, 无 CI) |
| GitHub Actions CI | ❌ | moni 不用 GH Actions |

### 4.2 spy 测试对现有 mock 的影响

- **不替换现有 mock**: 现有 11 个 `_run_job` 测试保持 `lambda *a, **k: payload` 不动, 它们测的是 DB writeback / evidence 入库 / 3 闸逻辑
- **新增 2 个 spy test**: 用 `captured` dict 模式 (跟 `test_api.py:1221` / `test_llm_usage_counters.py:171` 同款), 测的是 **prompt 到 call_llm 的数据流**
- **互不干扰**

### 4.3 backlog 冲突检查

- HANDOFF.md §后续待办 A2-A6 (evidence URL 去重 / 状态机联动 / 时效重跑 / 交叉分析 / token 成本): **无冲突**, 全部是独立功能模块
- AGENTS.md out-of-scope: 无与 lint/spy 冲突项
- sprint-16 之外的 demo HTML / 展示页相关待办: **无冲突** (sprint-16.5 不动前端)

---

## §5 给父 agent 一句话总结

> **sprint-16.5 值得开, 半天即可 ship.** 范围 = 1 个 lint 脚本 + 3 个测试 (~100 行), 封杀"$name 雷再埋"的结构性风险. 第一刀切 spy `call_llm` 测试 (这是真正钉死 sprint-14 类复发的保险, 比 lint 更关键). 单 PR #117, 风险 Low, 不动生产代码.

---

## 附录: 数据来源

- `git log --oneline -10` @ main HEAD `15a5256`
- `grep -rn '\$[a-z_]' moni/llm/prompts/` — 只在 `case_extraction.txt` 命中 (故意)
- `grep -rn '\{[a-z_]+\}' moni/llm/prompts/` — 6 模板全量扫描
- [__init__.py](../../../moni/llm/prompts/__init__.py) L13 `_PLACEHOLDER_RE` + L21-33 `_render()` + L77-90 `render_fact_check_plus()`
- [jobs.py](../../../moni/llm/jobs.py#L288-L318) `_run_job` prompt→call_llm 链路
- [enrichment.py](../../../moni/share/enrichment.py#L255-L310) INSERT enqueue 路径
- [test_fact_check_plus.py](../../../tests/test_fact_check_plus.py) 30 tests / 811 行
- [client.py](../../../moni/llm/client.py#L50-L59) `call_llm()` 签名
- `git status --short` — STATUS.md + USER_GUIDE.md dirty (文档, 无代码)
- `find .pre-commit-config*` — 0 结果, moni 无 pre-commit 配置
- `grep captured tests/` — `test_api.py` / `test_llm_usage_counters.py` 成熟 spy 模式可参考
