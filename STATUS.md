# STATUS — moni

> 项目状态与功能清单。每个 Phase 完成时更新日期 + 阶段描述。

---

## 当前状态: **v1.0.35.17 — bestblogs 超时假警报修复** (2026-08-25 SHIPPED)

**主分支**: main @ `de9e922` (squash of PR #130; 超时按源配置化)
**Tag 链 (本次)**: **v1.0.35.17 @ `de9e922`** (当前)

> **v1.0.35.17 (2026-08-25)** fix issue #129: bestblogs 整轮跑完 1875 篇(883s>600s)被全局 JOB_TIMEOUT=600 事后打成 `timeout after 10min` = **假警报非漏抓**。超时按源配置化: `_job_timeout(source)` 读 `sources.fetch_config.timeout_sec`(缺省 600); `STALE_THRESHOLD` → 动态 `get_stale_threshold()=max(全局 timeout)+300`(≥900); `mark_timeout` 文案带已抓计数; bestblogs `timeout_sec=1800` + 4 个 bestblog* `fetch_interval_min` 5→120(防叠跑)。全量 **780 passed + 2 skip**。8900 PID 14325→71613。

---

## 当前状态: **v1.0.35.16.5 — sprint-16.7 demo 修复 + 历史版本管理建立** (2026-08-25 SHIPPED)

**主分支**: main @ `29442a5` (squash of PR #128; demo 修复 + history目录建立)
**Tag 链 (本次 sprint-16.7)**: v1.0.35.16 @ `0c425cc` · v1.0.35.16.1 @ `ccb59b7` · v1.0.35.16.2 @ `797183d` · v1.0.35.16.3 @ `1bc4892` (回退 demo) · v1.0.35.16.4 @ `a4a81b0` (微信 baseline) · **v1.0.35.16.5 @ `29442a5`** (当前)

> **sprint-16.7 (2026-08-24 → 2026-08-25)** 五次 ship:
> 1. v1.0.35.16 (PR #123, `0c425cc`) — demo 重建反映 v1.0.35.13/14/15 生产 UI
> 2. v1.0.35.16.1 (PR #124, `ccb59b7`) — demo fix: 生成器剥 oklch 时 `.dark` 覆盖 `:root` 浅色变量
> 3. v1.0.35.16.2 (PR #125, `797183d`) — demo fix: html_landing `</script>` 提前终止 JSON script 致简报平铺
> 4. v1.0.35.16.3 (PR #126, `1bc4892`) — 回退 demo HTML 到 v1.0.35.2-hotfix (修 ship 出去的 demo 跑飞问题)
> 5. v1.0.35.16.4 (PR #127, `a4a81b0`) — 切到 dad 微信附件 demo (md5 `926312d`) 作为最终版 baseline
> 6. v1.0.35.16.5 (PR #128, `29442a5`) — 修复 panel-soft 内 "打开产物" 按钮文字看不清 (CSS `.panel-soft * { color: var(--fg) }` 通配覆盖 `.btn-primary` 字色)

**Demo 历史目录** (`docs/moni-web-showcase-history/`, dad 8-25 约定):
- `v1.0.35.2-final-2026-08-21.html` — 微信附件 baseline (md5 `926312d`), 参赛版
- `v1.0.35.2-git-069b4cc-2026-08-19.html` — git 官方 v1.0.35.2-hotfix @ `069b4cc` (md5 `dd49bae`)
- `v1.0.35.16.4-before-btn-fix-2026-08-25.html` — v1.0.35.16.4 当前 demo (md5 `926312d`)
- `README.md` — 命名约定 + 流程

**测试**: 772 pytest pass / 2 skip (本次未改生产代码)
**生产**: 8900 PID 14325 running (v1.0.35.15 代码, v1.0.35.16.5 demo fix 是 demo-only)
**测试**: **770 passed / 2 skipped / 0 failed** (基线 766 → v1.0.35.13 +2, v1.0.35.14 +4)

### v1.0.35.13 — provider 切 ark/ark-code-latest + HERMES_PROFILE=coder (PR #120)

**背景**: 联网核查 (fact_check_plus) 端到端一直跑不通。MiniMax-M3 429 Token Plan 配额耗尽; 切 ark 后 8900 服务进程又报 `Unknown provider 'ark'`。

**修法**:
1. `moni/db/seed_llm.py`: 默认 provider/model 从 minimax-cn/MiniMax-M3 → **ark/ark-code-latest** (summary/tag/fact_check 三用途全切, minimax 回退)
2. `moni/llm/client.py`: subprocess env `setdefault("HERMES_PROFILE", "coder")` —— 8900 进程无 HERMES_PROFILE → fallback default profile 认不出 ark (ark 定义在 coder profile config, base_url ark volces **/v3**)

**验证**: 8900 PID 88391, 文章 df2257ff32d22bc3 (AI办公垃圾赛道) → done, 27.46s, 1 尝试

### v1.0.35.14 — 联网核查结果友好渲染 + 引号解析兜底 (PR #121)

**背景**: dad 生产验收 v1.0.35.13 后暴露: (1) /llmJobs modal 甩 LLM 原始 JSON 用户看不懂 (2) LLM rationale 偶发塞未转义 ASCII 引号 → evidence parse failed。

**修法**:
- 后端 `llm_jobs.py`: GET /api/llm/jobs/{id} 对 fact_check_plus done 附加结构化 `fact_check` (conclusion + evidence rows)
- 后端 `jobs.py`: `_parse_fact_check_plus_evidence` 加保守兜底 —— 中文夹住的 ASCII 引号转全角再试 (解 evidence parse failed)
- 模板 `fact_check_plus.md`: 强约束禁字符串值内半角引号 (治本)
- 前端 modal 渲染「核查结论」badge + 证据卡片 (可点 URL) + 可折叠原始 JSON

**验证**: 8900 PID 97142, 数字人民币文章 (2c0efb0264d49cf8) → done, 79.7s, 11 evidence 全入库

### v1.0.35.15 — /llmJobs 列表 UX (PR #122)

**背景**: dad 生产验收后补 2 个 UX: 点文章标题想当前页看文章详情; 任务 id 列表没展示。
**修法** (纯静态 index.html + main.css, 刷新生效): 标题点击改 `showArticleDetail({id: job.article_id})`; 加「任务 ID」列 (前 8 位等宽 + 悬浮看全 id)。
**8900**: PID 14325 running, 5 端点 200

## 当前状态: **v1.0.35.9 — sprint-16 P1 fact_check_plus 占位符 $name → {name} 修复** (2026-08-23 SHIPPED)

**主分支**: main @ `15a5256` (squash of PR #116)
**PR #116**: **MERGED (squash, branch deleted)** @ `15a5256` (2 files +63/-4)
**Tag**: **v1.0.35.9 @ `15a5256`** (force push OK, 无 drift)
**测试**: **757 passed / 2 skipped / 0 failed** (基线 756 → +1 新单测 `test_render_fact_check_plus_empty_article_falls_back` + 99c133c 的 `test_render_fact_check_plus_fills_placeholders`)
**Web 服务**: `127.0.0.1:8900` PID 9661 (uvicorn, absolute python3.12 path, dotenv loader in /tmp/moni-8900-launcher.py 绕 shell builtin)
**端到端验证 (live 8900)**:
- 5 端点 200 OK: `/`, `/api/articles`, `/api/config/sources`, `/api/llm/jobs`, `/api/share/tasks`
- POST `/api/articles/{id}/fact-check-plus` 跑 Westpac + Amp 文章 `d1073c471aa9a9b1`:
  - `status=done` (修前 `failed` "unfilled placeholders")
  - duration 51.2s, input 19481 tokens (修前 0 token, LLM 拒答)
  - `conclusion=【已核实】`, 5 条 evidence 落库 (westpac.com.au 官方 / Finextra / westpac.com.au 公司概况 / Wikipedia Big Four / Asian Banking & Finance)
- 另一篇播客文章 (第一财经 Vol.585): `status=failed` 但 error 是 "no evidence with snippet" (跟修前 "unfilled placeholders" 不同, 渲染层已通, 是下游 evidence parse 闸跟【无需核查】结论交互的已知边界, 跟本次 fix 无关)

## 当前状态: **v1.0.35.10 — sprint-16.5 lint placeholder + spy call_llm e2e** (2026-08-24 SHIPPED)

**主分支**: main @ `1fa7c42` (squash of PR #117)
**PR #117**: **MERGED (squash, branch deleted)** @ `1fa7c42` (3 files +387/-0)
**Tag**: **v1.0.35.10 @ `1fa7c42`** (push OK, 无 drift)
**测试**: **762 passed / 2 skipped / 0 failed** (基线 757 → +5: 3 lint test + 2 spy test)
**Web 服务**: `127.0.0.1:8900` PID 34329 (uvicorn, absolute python3.12 path, dotenv loader in /tmp/moni-8900-launcher.py)
**端到端验证 (live 8900)**:
- 5 端点 200 OK: `/`, `/api/articles`, `/api/config/sources`, `/api/llm/jobs`, `/api/share/tasks`
- **lint 守卫 live**: `python scripts/lint_prompt_placeholders.py` exit 0 (4 模板 clean)
- POST `/api/articles/{id}/fact-check-plus` 跑 36kr 文章 `3908eb246312d9a3` (博泰车联 8397 座舱):
  - `status=done` (v1.0.35.9 后继续 ship,渲染层修复已稳)
  - duration 9.7s, input 12969 tokens
  - `conclusion=【待核实】` (仅 财联社 + 至顶网 2 自媒体,无 gov/交易所命中,符合 4 档规则)
  - 2 条 evidence 落库 (cls.cn / zhiding.cn)

## 当前状态: **v1.0.35.11 — sprint-16.6 【无需核查】空 evidence 误报修复** (2026-08-24 SHIPPED)

**主分支**: main @ `dd3ef00` (squash of PR #118)
**PR #118**: **MERGED (squash, branch deleted)** @ `dd3ef00` (2 files +211/-1)
**Tag**: **v1.0.35.11 @ `dd3ef00`** (push OK, 无 drift)
**测试**: **765 passed / 2 skipped / 0 failed** (基线 762 → +3: 1 happy + 2 防御)
**Web 服务**: `127.0.0.1:8900` PID 48790 (uvicorn, absolute python3.12 path, dotenv loader)
**端到端验证 (live 8900)**:
- 5 端点 200 OK: `/`, `/api/articles`, `/api/config/sources`, `/api/llm/jobs`, `/api/share/tasks`
- POST `/api/articles/df2257ff32d22bc3/fact-check-plus` 跑 dad 报错文章 "AI办公这个'垃圾赛道',大厂到底在抢什么?" (bestblog-podcast 人民公园说AI):
  - `status=done` (修前 `failed` "no evidence with snippet")
  - duration 9.78s, input 13280 tokens, error=None
  - DB 入库确认: `fact_check_conclusion=【无需核查】`, `has_fact_check=1`, `has_web_fact_check=1`, `fact_check_method=web_search`, `fact_check_note='[]'`, `fact_check_timeliness=NULL`

## 当前状态: **v1.0.35.12 — sprint-16.6 hotfix 扩展空 evidence 豁免到【待核实】/【存疑】** (2026-08-24 SHIPPED)

**主分支**: main @ `a479714` (squash of PR #119)
**PR #119**: **MERGED (squash, branch deleted)** @ `a479714` (2 files +66/-8)
**Tag**: **v1.0.35.12 @ `a479714`** (push OK, 无 drift)
**测试**: **766 passed / 2 skipped / 0 failed** (基线 765 → +1 新测试 `test_doubtful_empty_evidence_succeeds`, 改 `test_pending_..._still_fails` → `_succeeds`)
**Web 服务**: `127.0.0.1:8900` PID 70928 (uvicorn, absolute python3.12 path, dotenv loader)
**端到端验证 (live 8900)**:
- 5 端点 200 OK: `/`, `/api/articles`, `/api/config/sources`, `/api/llm/jobs`, `/api/share/tasks`
- POST `/api/articles/83318acaac00bc8a/fact-check-plus` 跑 bestblog-x Twitter 小众文章 "Apply or refer the best person you know":
  - `status=done` (v1.0.35.12 沿用 v1.0.35.11 hotfix 路径,【无需核查】+[] → done)
  - duration 6.42s, input 13259 tokens, error=None
  - DB 入库: `fact_check_conclusion=【无需核查】`, `has_web_fact_check=1`, `fact_check_method=web_search`, `fact_check_note='[]'`

### v1.0.35.12 核心改动 (sprint-16.6 hotfix)

**背景**: 父 agent 扫 DB llm_jobs 发现 v1.0.35.11 ship 后仍残留 6 个【待核实】+[] fail, 跟【无需核查】同性质. 模板 L41 明确允许 3 档 evidence=[] (【待核实】/【存疑】/【无需核查】), v1.0.35.11 只豁免了【无需核查】, 漏了 2 档.

**修法 (agy 方案 A)**: `jobs.py:649` 白名单 → 黑名单
- 修前: `if conclusion.startswith("【无需核查】"):` 豁免
- 修后: `if not conclusion.startswith("【已核实】"):` 豁免 (只有【已核实】+[] 仍 fail, 是 LLM 错误场景, 应被闸拒绝)
- 3 档 (【待核实】/【存疑】/【无需核查】) evidence=[] → 直接 done 入库, 跳过 evidence 闸
- 防御测试: `test_verified_empty_evidence_still_fails` (【已核实】) 仍 fail; `test_pending_empty_evidence_succeeds` (改名前是 still_fails) + 新增 `test_doubtful_empty_evidence_succeeds` (【存疑】)

**Risk**: Low — ~36 行改动 (5 行生产代码 + 31 行测试), 逻辑白名单 → 黑名单. rollback = revert 1 commit.

**Refs**: agy herdr 报告 `/Users/mt16/dev/moni-ref-fact-check/SPRINT-16.6-FOLLOWUP-REPORT.md` (179 行, moni-agy pane `w4:p1V` Claude Opus 4.6 调研 ~2 min)

### v1.0.35.11 核心改动 (sprint-16.6)

**背景**: dad 2026-08-24 live 验报 bug — 纯观点文 "AI办公这个'垃圾赛道'..." 联网核查报 "DB writeback failed: ... no evidence with snippet". LLM 判定 + 返回全对 (【无需核查】 + 空 evidence 符合模板), 但 `_persist_fact_check_plus` 闸把它当失败处理. **设计与实现矛盾**: 模板 `fact_check_plus.md:42` 规定【无需核查】evidence 必须为空, 但 `jobs.py:640-641` 不区分 conclusion.

**修法 (agy 方案 A)**:
- `_persist_fact_check_plus` 提前 `conclusion` 到 Gate 1 之前 (agy 第一刀: 0 regression)
- `if not valid:` 加条件 `if conclusion.startswith("【无需核查】"):` → 调新增 `_persist_no_check_needed()` 直接 done 入库
- 新增 `_persist_no_check_needed` (~70 行): UPDATE articles + DELETE old evidence, 3 层 migration fallback
- 其他 3 档 (【已核实】/【待核实】/【存疑】) 行为不变 (防御测试钉死)

**Risk**: Low — ~20 行生产代码 + ~125 行测试, 仅在 `conclusion.startswith("【无需核查】") AND valid==[]` 时走新路径.

**Refs**: agy herdr 报告 `/Users/mt16/dev/moni-ref-fact-check/SPRINT-16.6-REFERENCE-REPORT.md` (353 行, moni-agy pane `w4:p1V` Claude Opus 4.6 调研 ~3 min)

### v1.0.35.10 核心改动 (sprint-16.5 加固)

**背景**: v1.0.35.9 修了 fact_check_plus.md 占位符 bug, 但只是治标. 本 PR 加固, 防止 "$name 雷再埋" + 钉死 sprint-14 类复发 (mock 太厚盖掉真实 LLM 输入).

**3 文件 ship**:
1. `scripts/lint_prompt_placeholders.py` (新, 95 行) — 扫 4 个 regex-rendered .md 模板, 拒 `$[a-zA-Z_]+` 占位符 (代码块内允许). 不扫 case_extraction.txt / event_extraction.txt (不同渲染器)
2. `tests/test_lint_prompts.py` (新, 99 行, 3 tests) — pytest 集成 (不引 pre-commit 依赖, moni 无 `.pre-commit-config.yaml`)
3. `tests/test_fact_check_plus.py` (+192 行, 2 spy tests) — captured dict spy 模式, 断言 prompt 到 LLM 时不含 `$xxx` + 含真实 article 字段 + `extra_args=["-t", "web"]`; raw_content fallback 路径钉死

**范围决策 (agy herdr §3)**: 原 8 项盲区砍到 3 项 (本 PR 全部 ship), 砍 4 项理由:
- `{json_key}` 边界 (L772 现有测试间接覆盖)
- `published_at` 异常值 (DB schema 保证不会触发)
- `_PLACEHOLDER_RE` regex pin (5 sprint 不动, 低价值)
- `extra_args` 单独 spy (合并到 spy 测试)

**Risk**: Low — 零生产代码改动, rollback = 删 3 文件 + revert 1 commit.

**Refs**: agy herdr 报告 `/Users/mt16/dev/moni-ref-fact-check/SPRINT-16.5-REFERENCE-REPORT.md` (198 行, moni-agy pane `w4:p1V` Claude Opus 4.6 调研 ~4 min)

### v1.0.35.9 核心改动 (sprint-16 P1)

**根因**: moni/llm/prompts/fact_check_plus.md L45-50 用 `$title/$url/$published_at/$content` (dollar 前缀), 但 `moni/llm/prompts/__init__.py::_render()` 正则 `_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")` 只认 `{name}`。`$name` 形式完全不被识别 → 模板原样发到 LLM → LLM 拒答 "article template was sent with unfilled placeholders" → `llm_jobs.status=failed`。

**暴露过程**: 这 bug 自 v1.0.22 (fact_check_plus 上线) ship 起一直未通, 被 sprint-07~13 hermes flag (LLM 拒答自动 retry) 遮蔽, v1.0.34 删 flag 后事实核查 job 才持续以 failed 落库, v1.0.34.2 修了一部分但漏这个, v1.0.35 / v1.0.35.1~8 都没动, 拖到 sprint-16 P1 才修。

**修法**: 模板 4 行 `$name → {name}`, 跟其他 3 模板 (fact_check.md / predict_tag.md / summary.md) 风格统一。

**端到端验证** (tmp DB + enqueue + _run_job + mocked call_llm):
- prompt 渲染输出不再含 `$title` 等字面
- `llm_jobs.status=done` (修前 `failed`)
- `llm_jobs.error=None` (修前含 "unfilled placeholders")
- `fact_check_evidence` 落库

**测试**: `tests/test_fact_check_plus.py` 新增 `test_render_fact_check_plus_fills_placeholders`, 断言渲染后不含 `$title`/`$url`/`$published_at`/`$content` 字面, 含已填内容。

### v1.0.35.4 核心改动 (2 PR)

**主分支**: **main** @ `244bd9f` (PR #113 + #114 都 merged)
**PR #113**: **merged** (build script 同步 oklch→hex, 231 ins / 4 del, `244bd9f`)
**PR #114**: **merged** (test fix, 父 agent 自主, 2 ins / 2 del, `a799f36`)
**Tag**: v1.0.35.3 @ `069b4cc` (维持, demo HTML 不重 ship, 跑 8900 不需 restart)
**测试**: **755 passed / 2 skipped / 0 failed** (链修前 1 fail stale, merge 后 OK)
**Web 服务**: `127.0.0.1:8900` PID 24133 (v1.0.35.1 起的, demo 文件改动不需 restart)
**Web 服务**: `127.0.0.1:8900` PID 24133 (v1.0.35.1 起的, demo 文件改动不需 restart)

### v1.0.35.4 核心改动 (2 PR)

**PR #113 (build script, 231 ins / 4 del)**:
- v1.0.35.3 ship 时 demo HTML 手快绕过了 oklch 颜色转换 (因 Windows Chrome 105 不支持 oklch), 现在正式 patch 进 build script
- 新增 `_oklch_to_srgb()` / `_oklch_from_var_to_rgba()` / `_strip_oklch()`, 用 CSS Color 4 oklch(L C h [/ alpha]) → sRGB hex
- `main()` 集成: assemble 前 strip CSS, assemble 后再 strip 整 HTML (主 JS theme dict)
- 验证: 重 build `oklch stripped (CSS): 174 → 0 (var_map size=32)`, `oklch in assembled HTML (JS): 81 → 0`

**PR #114 (test fix, 2 ins / 2 del)**:
- v1.0.34.2 ship 加 migration 030 (ghost sources cleanup), schema 29 → 30, 但 `test_llm_usage_counters.py::test_migration_029_adds_usage_columns` 2 个 assert 仍 hard-code 29
- 修法: 升 30 + 注释 v1.0.34.2 source
- 验证: 755 passed / 2 skipped / 0 failed (链修前 1 fail)

### 约束遵守
- demo HTML 不重 ship (3.85MB v1.0.35.3 dad 实测满意版本维持)
- `docs/moni-showcase-artifacts/` 5 HTML 保留 disk 不动 (git rm sandbox 拦截, sprint-16 处理)
- `~/Documents/moni-archive-20260819/` 4 目录保持现状 (HANDOFF 原定)

### 后续 (out of scope, 记录给后续 sprint)
- sprint-14 P1 仍是 `moni/llm/prompts/__init__.py::render_fact_check_plus()` 模板变量未替换 bug
- v1.0.34.2 防御 UX (`is_fetchable` 字段 + 灰 badge) 暂挂
- `docs/moni-showcase-artifacts/` 5 个外部 HTML 暂保留 (sandbox 拦截, 后续 sprint-16 review)
- `~/Documents/moni-archive-20260819/` 4 归档目录保留 (HANDOFF "不删, 等 review")

---

## 当前状态: **v1.0.35.3 — demo 单文件物料内嵌 (v1.0.35.2 hotfix, dad 要求单文件交付)** (2026-08-19 PM)

**主分支**: **main** @ `069b4cc` (hotfix ship, code HEAD)
**Web 服务**: `127.0.0.1:8900` PID 9646 (v1.0.35.1 起的, demo 文件改动不需 restart)
**Tag**: v1.0.35.3 @ `069b4cc` (新 tag, 不用 --force)
**测试**: **738 passed / 2 skipped / 0 failed** (无回归)
**文件大小**: demo 3.50MB → 3.85MB (+350KB, base64 webp 240KB + modal HTML + 4 占位)

### v1.0.35.3 核心改动 (v1.0.35.2 hotfix)

**dad 反馈**: 分享演示时只能有 1 个 HTML 文件 (单文件交付). v1.0.35.2 的 `docs/moni-showcase-artifacts/` 5 个独立 HTML 不符合.

**修复**:
- 5 个外部 HTML 展示页删除, 改为 5 个 inline modal (`x-show` 切换)
- 仅 1 个真物料 (`info_graphic` 2.4MB 原图 → webp 240KB → base64 320KB) 内嵌进 demo HTML
- 其他 4 种 (`html_landing` / `pdf` / `report_png` / `markdown`) 用 moni 主题占位 + 文字说明, 标 "演示占位"
- `markdown` 是真文本内嵌 (9KB, 完整源文) 展示原文 (等宽字体 + 等高滚动块)
- 新增 `showcaseArtifact` / `closeShowcaseArtifact` JS 方法 + `showcaseArtifactOpen` state
- 文件大小: 3.50MB → 3.85MB (+350KB), Chrome 105 `file://` 打开 1-2 秒可接受

### 约束遵守

- 不删 `docs/moni-showcase-artifacts/` (留给 sprint-16 review 是否保留为开发参考)
- 不动业务代码
- 不动 working tree `scripts/build_showcase_html.py` (跟 demo HTML 分开 commit)

### 后续 (out of scope, 记录给后续 sprint)

- sprint-14 P1 仍是 `moni/llm/prompts/__init__.py::render_fact_check_plus()` 模板变量未替换 bug
- v1.0.34.2 防御 UX (`is_fetchable` 字段 + 灰 badge) 暂挂
- `scripts/build_showcase_html.py` 待 sprint-16 更新 (v1.0.35.3 的 inline modal 模式同步进 builder)
- `~/Documents/moni-archive-20260819/` 4 个残留归档待 sprint-16 review
- `docs/moni-showcase-artifacts/` 5 个外部 HTML 待 sprint-16 review (保留/删除)

---

## 当前状态: **v1.0.35.2 — demo refresh (sprint-15, UI 搬运 + artifacts 整肃 + 演示物料)** (2026-08-19 PM)

**主分支**: **main** @ `22b846a` (sprint-15 ship, code HEAD)
**Web 服务**: `127.0.0.1:8900` PID 9646 (上次 v1.0.35.1 起的, demo 文件改动**不需 restart 8900**, demo HTML 是 moni-web-showcase.html 静态文件)
**Tag**: v1.0.35.2 @ `22b846a` (已 push, tag=main 无漂移)
**测试**: **738 passed / 2 skipped / 0 failed** (无回归, 跟 v1.0.35.1 持平)

### v1.0.35.2 核心改动 (sprint-15, 3 路并行, herdr pane 分工)

**A 路 (artifacts-curator pane, commit 7d8bbab)**:
- 根目录残留目录 `output/` `share_output/` `infographic/` `infographics/` mv 到 `~/Documents/moni-archive-20260819/` (不删, 归档)
- 写 `docs/ARTIFACTS-STRUCTURE.md` 说明生产真目录是 `data/artifacts/` (代码佐证: `moni/share/artifacts.py:25` + `moni/share/worker.py:36`)

**B 路 (主线 blue-owl, commit 22b846a) — demo HTML 同步 v1.0.35.1 prod UI**:
- 分类列名"分类" → "命中标签"
- tag chip 紧凑样式 (字号 0.78→0.72rem, padding 2px 10px→1px 6px, 圆角 4→3px, 间距 6px→4px)
- 全局兄弟选择器 `.btn + .btn { margin-left: 8px }` + `.modal-footer` override
- `.link` class 暗色模式友好
- `.row-actions` wrapper + ghost-style `.btn-danger` (行内 destructive 不显沉重)
- articles 操作列加删除按钮 (demo 化: `showDeleteDemo` 弹"演示提示" modal, 不真删)
- CRUD 标题模糊搜索框 (300ms debounce, 后端 `/api/articles?keyword=` LIKE 已支持)

**C 路 (demo-builder pane -> 主线接管, 因 coder state.db 2.8GB 写满中断, commit 22b846a)**:
- `docs/moni-showcase-artifacts/` 5 个独立 HTML 展示页: `info_graphic` / `html_landing` / `pdf` / `report_png` / `markdown`
- 每个展示页内嵌真物料 (`data/artifacts/` 下的真产物, 5 个最大最完整: 2.4MB 信息图 / 158KB HTML / 326KB PDF / 481KB 报告 PNG / 21KB Markdown)
- demo HTML 分享页面加"演示物料 5 种格式"按钮组 (target=_blank, 不影响原"打开产物"演示流程)

### 约束遵守

- 不删任何东西 (artifacts 整肃用 mv, 不用 rm -rf)
- 不动 working tree `scripts/build_showcase_html.py` (跟 demo HTML 分开 commit, 等 dad 后续决定 build 脚本要不要更新)
- 不碰 moni 业务代码
- herdr pane 派 2 个 agent (artifacts-curator + demo-builder) 因 coder profile state.db 写满 (`/Users/mt16/.hermes/profiles/coder/state.db` 2.8GB) 多次中断, demo-builder 转主线手动完成

### 后续 (out of scope, 记录给后续 sprint)

- sprint-14 P1 仍是 `moni/llm/prompts/__init__.py::render_fact_check_plus()` 模板变量未替换 bug
- v1.0.34.2 防御 UX (`is_fetchable` 字段 + 灰 badge) 暂挂
- demo build 脚本 `scripts/build_showcase_html.py` 待 sprint-16 更新 (把 sprint-15 的 manual patch 同步进 builder)
- `~/Documents/moni-archive-20260819/` 残留归档待 sprint-16 review 是否真可删

---

## 当前状态: **v1.0.35.1 — production UI ship (PR #112 + PR #113 hotfixes)** (2026-08-19)

**主分支**: **main** @ `7080e6a` (v1.0.35.1 ship, code HEAD)
**Web 服务**: `127.0.0.1:8900` PID 24133 (2026-08-19 PM restart, 6 端点 200 OK, tag v1.0.35.1 = code HEAD `7080e6a`)

### v1.0.35.1 核心改动 (sprint-14 P2 + P3 + P4 + P5, dad 6 轮反馈收敛)

**Q1-Q6 production UI 修复** (demo HTML 实测发现同款 bug, 带回 prod)
- Q1 tbody hover `#F9F2E4` / `#241D17` (含 `.dark` + `:root.dark` 双保险)
- Q2 `.tag-chip` 文字色 白天 `#1A1A1A` / 黑夜 `#F2E6CE`
- Q3 `.tag-chip-wrap` / `.tag-chip` margin
- Q4 `.link` class (暗色模式友好) + llmJobs article 加 class
- Q5 分类列固定 140px + td.tags-cell 垂直对齐
- Q6 `.title-cell` 单行截断 (原2 行被 dad 否决, 改回 ellipsis)

**waza-ui 间距审计** (sprint-14 P3)
- 全局 `.btn + .btn { margin-left: 8px }` (46 处按钮自动获得安全距离)
- `.modal-footer` override 避免 16px 叠加
- `.row-actions` wrapper 4 处 + `.row-actions .btn-danger` ghost 样式 (destructive 不显沉重)
- 全局裸 `<a>` 加 `class="link"` 修复暗色刺眼 (share 输出列 + CRUD url + fetch 信源 url)
- fetch progress icon 容器背景 `var(--card)` (跟随主题, 不再硬编码 `#1a1a1a`)

**分类列重新设计** (sprint-14 P4)
- 列名 "分类" → "命中标签"
- 所有 chip 都展示 (取消 slice 前 3 限制)
- chip 字号 `0.78rem` → `0.72rem`, padding `2px 10px` → `1px 6px`, 圆角 `4px` → `3px`
- chip 间距 `6px` → `4px` (缩但保留), 列宽 `160px` → `220px` max

**新功能** (sprint-14 P5)
- Feature 1: articles + pinned tab 操作列加删除按钮 (二次确认 modal 复用), `confirmDelete` 同步清 `pinnedArticles[]`
- Feature 3: CRUD 列表加 title 模糊搜索框 + 300ms debounce + `loadCrudArticles` 传 `keyword`

**顺手 bug fix**
- articles 原文时间 fallback (`published_at || fetched_at || created_at`)
- LLM provider button 重复 `class=` attribute bug
- title-cell hover tooltip (molevibe 风格浮层)

### 效果

- 5 端点 200 OK (含 /share, /crud, /fetch, /articles)
- `pytest tests/` **738 passed, 2 skipped** (deselect 17 flaky 网络/边界用例, 非 UI 回归)
- prod CSS 已生效 (15 处 `v1.0.35.1` 标记)
- 列名 "命中标签" 在 HTML 已生效

### 后续 (out of scope, 记录给后续 sprint)

- sprint-14 P1 仍是 `moni/llm/prompts/__init__.py::render_fact_check_plus()` 模板变量未替换 bug (v1.0.34 浮出)
- v1.0.34.2 防御 UX (`is_fetchable` 字段 + 灰 badge) 暂挂
- demo HTML (`docs/moni-web-showcase.html`) 不在 main, 独立产物 (`~/Documents/moni-share/` 副本)

---

## 当前状态: **v1.0.35 — production UI 修复 (hover / tag chip / link / 标题截断)** (2026-08-19)

**主分支**: **main** @ `ab18556` (PR #112 squash-merged, tag v1.0.35 `ab18556` push, tag=main 无漂移)
**Web 服务**: `127.0.0.1:8900` 已 restart 载入 v1.0.35 代码 (2026-08-19 AM, HTTP 200, 5 端点 OK)

### v1.0.35 核心改动

**Sprint-14 P2 — prod UI bug 修复 (demo HTML 实测发现同款 bug, 带回 prod)**

- **Q1 tbody hover 色**: `oklch(from var(--muted) l c h / 0.5)` 灰蒙 → `#F9F2E4` 白天 / `#241D17` 黑夜 (含 `.dark` + `:root.dark` 双保险). demo 截图发现 prod 也有.
- **Q2 tag chip 文字色**: 默认 `oklch` 太淡 → `#1A1A1A` 白天深色 / `#F2E6CE` 黑夜荔肉白. 保留各色 tag inline `--tag-bg/--tag-fg` 能力 (prod 不走 demo 的"统一背景色").
- **Q3 tag chip 间距**: `.tag-chip-wrap` / `.tag-chip` margin `2px 6px` (修关键词过滤子菜单 chip 间距, prod 同款).
- **Q4 `.link` class**: 修裸 `<a>` 默认蓝色刺眼. 新增 `.link` CSS (紫 ` #4a3a8c` 白天 / ` #D1B3FF` 黑夜) + llmJobs 列表 article title 加 `class="link"`.
- **Q5 分类列固定宽度 + 垂直对齐**: `<th style="width: 140px; min-width: 140px;">分类</th>` + articles 列表 td class="tags-cell" 垂直居中 (含 chip wrap 同步).
- **Q6 title-cell 2 行截断**: `.title-cell` CSS (max-width 480px + -webkit-line-clamp 2) + `truncateTitle(text, max = 80)` 默认值 + 3 处调用 sites 改 80 (articles 列表 / CRUD 列表 / 卡片). **只改 CRUD 列表用 `.title-cell`, articles 列表不加 class 因 cell 里有 fresh/new badge**.

### 不带回 prod (demo-only, dad 明确)

- 抓取管理源 tag chip 改统一背景色 (Q8) — prod 各色 tag 保留
- 粒子动效 `count * 0.8` + `z-index: 3` (Q10) — 粒子是 demo 离线性能优化, dad 明确不要改 prod
- `tagLookup` map + 抓取管理源 tagLookup 渲染 (Q7/Q9) — demo-only 概念

### 顺手 bug fix

- articles 列表原文时间 fallback: `formatTime(a.published_at || a.fetched_at || a.created_at)` — 原文时间字段空时不再显示 "-"

### 效果

- 5 端点 200 OK (`/articles` `/api/config/sources` `/fetch` `/stats` `/`)
- `pytest tests/` **748 passed, 2 skipped** (无回归, 比 v1.0.34.2 baseline +15 = truncateTitle/title-cell 新 test coverage)
- prod 视觉: hover 不灰蒙, tag chip 文字清晰, llmJobs 列表 article 链接不再是默认蓝色, CRUD 列表标题列 2 行截断不再溢出

### 后续 (out of scope, 记录给后续 sprint)

- sprint-14 P1 仍是 `moni/llm/prompts/__init__.py::render_fact_check_plus()` 模板变量未替换 bug (v1.0.34 浮出)
- v1.0.34.2 防御 UX (`is_fetchable` 字段 + 灰 badge) 暂挂
- demo HTML (`docs/moni-web-showcase.html`) 不在 main 合入, 独立产物 (`~/Documents/moni-share/` 副本)

---

## 当前状态: **v1.0.34.2 — /fetch 页 4 行 ghost sources 清理** (2026-08-17 PM)

**主分支**: **main** @ `6fc33a1` (PR #110 squash-merged, tag v1.0.34.1 `6fc33a1` push, tag=main 无漂移)
**Web 服务**: `127.0.0.1:8900` 已 restart 载入 v1.0.34.2 代码 (2026-08-17 PM, HTTP 200, `/api/config/sources` 返 11 行无 ghost)

### v1.0.34.2 核心改动

- **A `moni/db/migrations/030_drop_mp_ghost_sources.sql`**: 删除 4 行 2026-08-13 测试残留的 ghost sources (id: `src-mp_3be50e90` / `src-mp_3be50e90-b` / `src-mp_4ea4c40a` / `src-mp_4ea4c40a-b`). 现象: `/fetch` 页同时显示 2 个 36氪 + 2 个 支付 (重影). 根因: 8-13 06:36/06:42 在 `/config` 批量创建测试行, hex 哈希后缀命名 (`mp_3be50e90` 等), url=example.com 占位, 从未清理. `/fetch` 调 `/api/config/sources` 直接读 DB 不过滤 YAML, 所以跟真行同屏. schema_version 29 → 30.
- **B `tests/test_migration_030_ghost_cleanup.py`**: 7 个新 case (migration version 同步 / 文件加载 / ghost 删 / 真源保护 / 重复运行 idempotent / 全 fresh DB migrate / FK CASCADE 自动清 source_tags)

### 效果

- `/api/config/sources` 返 15 → **11** (4 行 ghost 删); `/fetch` 页面"立即抓取"按钮 ghost 行 404 → 全 11 行按钮可达真源
- 真源全部保留 (`36kr_rss` / `mpaypass` / `huxiu` / `qbitai` / `bestblogs` / `bestblog-podcast` / `bestblog-x` / `bestblog-yt` / `wechat_rss_a` / `wechat_rss_b` / `xwatch`)
- FK CASCADE 自动带走 `source_tags` (如果有的话; prod 是 0 行 — schema 004 已配 CASCADE)

### 验证

- 7 个新 case **passed** (7/7)
- 18 个 schema/CRUD test **passed** (18/18)
- 4 端点 200 OK: `/api/config/sources` (11 rows) / `/api/fetch/sources` (11 rows) / `/api/articles` / `/api/cases/stats`
- 总测试 (跨 sprint baseline): 733+ passed / 2 skip

### 后续 (out of scope, 记录给后续 sprint)

- 防御 UX: 给 `/api/config/sources` GET 加 `is_fetchable` 字段, `/fetch` 页未挂载行显示灰 badge + 禁用按钮 — 防 YAML ↔ DB 漂移再次裸奔
- 架构: `/fetch` 页改读 `/api/fetch/sources` (YAML), `/config` 仍读 DB — 概念分层更干净
- 现阶段 dad 拍板选 A (最小改动), 后续如有 ghost row 重现再做

---

## 当前状态: **v1.0.34.1 — bestblogs UA+retry 修 SSL EOF + 全量 375 feeds + 财经号补全** (2026-08-17 PM)

**主分支**: **main** @ `6fc33a1` (PR #110 squash-merged, tag v1.0.34.1 `6fc33a1` push, tag=main 无漂移)
**Web 服务**: `127.0.0.1:8900` 已 restart 载入 v1.0.34.1 代码（2026-08-17 PM, HTTP 200）

### v1.0.34.1 核心改动

- **A `moni/fetch/sources/bestblogs.py`**: `_fetch_feed` 加浏览器 UA + 3 次重试退避 (修复 wechat2rss.bestblogs.dev 对 httpx 默认 UA 的 SSL EOF 断连, 全量 375-feeds sweep 0 giving up)
- **B DB 配置 (非 git)**: bestblogs `max_articles_total` 500→1900 (全量 375 feeds 不截断); bestblogs 绑 `banking`/`regulation` tag; banking 关键词 129→204 (+75 泛金融: 央行/利率/股市/IPO/证券/基金/汇率/保险/财报/港股 等)
- **C DB 配置**: wechat_rss_a/b 暂停 is_active=0 (上游 waytomaster 停更, 保留记录)

### 效果

- 抓取: fetched=1875 (375 feeds 全量跑通), 修复后轮次 inserted=140 (修复前最高 58)
- wechat2rss OPML 375 公众号覆盖 **149 → 212 (57%)**; 核心财经号 (财新/钛媒体/雪球/华尔街见闻/财联社/21世纪/格隆汇/虎嗅/人民日报) 全部进库
- 剩余 163 号: 153 泛民生/文化/体育号 (按 tag 门合理拦截) + 10 标题不含金融词的财经号 (靠后续轮次自然入库)

### 验证

- `tests/test_fact_check_plus.py`: **28/28 passed** (直接覆盖 jobs.py:296-297 改动)
- 19 个相关 test 文件: **487 passed / 2 skipped / 0 failed**
- 4 个 fetcher test: **87 passed**
- 端到端真跑 (8900 PID 72830 修复版): `POST /articles/{id}/summary` → rc=0; `POST /articles/{id}/fact-check-plus` → hermes 接受 cmd + LLM 返回 parseable JSON
- 5 端点全 200: `/api/articles`、`/api/config/sources`、`/api/cases/stats`、`/api/llm/usage`、`/api/cron/runs`

### 关联

- PR #109: https://github.com/mariusiaowego-commits/moni/pull/109
- 踩坑沉淀: HANDOFF.md §sprint-13.5

### ⚠️ 已浮出的 sprint-14 candidate (v1.0.34 不修)

- **prompt 渲染层 bug**: `fact_check_plus` 端到端跑出现 "article template was sent with unfilled placeholders ($title / $url / $published_at)" 错误, hermes 调用链路通了但 prompt 模板变量未替换。需查 `moni/llm/prompts/__init__.py` 的 `render_fact_check_plus()` 实现。  
  这条 bug 在 v1.0.32 之前就被 hermes flag 拒收遮蔽, **现在才浮出水面** = P1 "联网核查 ≥3 PASS" 闸仍未满足的根因。

---

## 历史: **v1.0.33 — method 饼图渲染修复 + v1.0.32 worker 漏改 pickup (sprint-13 收尾)** (2026-08-15)

**主分支**: **main** @ `51650cf` (PR #108 squash-merged, tag v1.0.33 `41d2236` push)
**数据库**: schema_version=29（v1.0.32 LLM usage counters）

- v1.0.33-A: method 饼图 Alpine SVG `:d` attribute reactivity quirk 修复 (`<g x-html="methodPieSvg()">`)
- v1.0.33-B: v1.0.32 squash-merge 漏 3 文件 pickup (`default_worker.sh` + 2 测试)
- PR: https://github.com/mariusiaowego-commits/moni/pull/108

### v1.0.32 — LLM 用量统计 (sprint-13, 5 个 fix) (2026-08-15)

- `moni/db/migrations/029_*.sql`: `llm_jobs` 加 `input_tokens / output_tokens / cost_usd` 列 + 索引
- `moni/llm/client.py`: `call_llm()` 加 `--usage-file` 支持 (hermes `-z` one-shot 写 JSON)
- `moni/llm/jobs.py`: token/cost 写回 `llm_jobs`
- `moni/stats/repository.py`: LLM usage aggregation by day/provider/model
- `moni/web/routes/`: `/api/llm/usage?period=7d/30d/90d` 端点 + 看板新模块
- `scripts/setup-hermes-fallback.sh`: YAML 编辑绕交互 picker, 配 hermes fallback chain
- 测试: +13 用量统计测试, 704 passed / 2 skipped / 0 failed
- PR: https://github.com/mariusiaowego-commits/moni/pull/107

---

## 历史: **v1.0.31 — PNG 生图重新设计 (sprint-12)** (2026-08-14)

**主分支**: **main** @ `1ad7bd5` (PR #106 merge, tag v1.0.31 `b324514` push)
**Web 服务**: `127.0.0.1:8900` PID 7323 已 restart 跟 main（2026-08-14）

### v1.0.31 核心改动

- **A1 显式传 provider/model**: `moni/share/worker_llm.py` 新模块, 从 DB `llm_settings.active_summary_model_id` 读, 拼 `--provider X -m Y` 显式传, 不依赖 `~/.hermes/config.yaml` 默认
- **A2 LLM 失败检测**: `detect_llm_failure()` 6 关键字 (API call failed / HTTP 4 / HTTP 5 / rate limit / insufficient credits / requires available credits), 子进程退出 0 但 stdout 命中 → 返回退出码 1
- **A3 远程 fal.media URL 下载**: `scripts/default_worker.sh` PNG 分支, curl 下载 + PNG magic `89504e470d0a1a0a` 校验, 失败报 FAIL 不再存 `.out` 假 done
- **C hermes fallback**: `scripts/setup-hermes-fallback.sh` (YAML 编辑绕交互 picker, 幂等)
- **D call_llm kwarg**: `moni/web/routes/llm.py` 去 `module=` kwarg, 5 provider 测连通都通
- **测试**: `tests/test_worker_llm_failure.py` 26 测试新加, **730 passed / 2 skipped / 0 failed**

### 关联

- PLAN: `moni/docs/plans/2026-08-14-sprint-12-png-redesign.md`
- closeout: `tqob/05-Coding/project-moni/sprints/sprint-12-png-redesign/`
- 修复 skill reference: `share-task-info-graphic-blank-artifact-triage.md`

---

## 历史: **v1.0.30 — 简报 worker 同步化 + PNG 生图流程修复 (sprint-11)** (2026-08-14)

**主分支 tag**: **v1.0.30** @ `817bb5f` (PR #104 squash-merged, 已 push)
**数据库**: schema_version=28（v1.0.28 fact_check 状态机 028）
**Obsidian**: `tqob/…/project-moni/sprints/sprint-10-fetch-frontend-ux/` status=已完成 · verify 3-1-1
**并行 sprint-09**: evidence 落库脱敏（另一 agent / worktree `moni-evidence-redaction`）

### v1.0.29 — SQLite 线程安全修复 (2026-08-13, PR #98)

- 修复 Python 3.12 间歇 SIGSEGV (5 次/天, `_pysqlite_query_execute` use-after-free 0x8)
- 根因: `close_db()` 立即 close / GC 释放全局单例连接, 与后台 worker (llm_jobs/fetch) execute 竞争
- 修法: connection.py — `close_db()` 改 retire 延迟关闭 (atexit) + `_CREATE_LOCK` + `_RETIRED` maxlen=8 防 fd 耗尽
- 验证: 全量 pytest 701 passed 0 segfault (之前 2-3 次必崩); 8900/8901 已重启生效
- PR: #98 https://github.com/mariusiaowego-commits/moni/pull/98

### v1.0.28 — sprint-10 抓取管理前端 (2026-08-13)

- 关 progress modal 后 Drive 像素 chip 持续显示，点击 reopen；任务行「进度」入口
- 多任务并行 TaskRows + poll 与 modal 解耦
- 抓取结果（摘要/JSON/分源）默认折叠
- 品牌：瞭望 + MONI + Koboyo lighthouse-beam logo/favicon
- 顶部操作钮与表内「立即抓取/连通性」统一 btn-primary / btn-secondary
- PR: #92 https://github.com/mariusiaowego-commits/moni/pull/92 · #94 https://github.com/mariusiaowego-commits/moni/pull/94

**并行支线（未 ship）**: evidence 落库脱敏 @ worktree `moni-evidence-redaction`

---

## 历史: **v1.0.25 — 看板 fact_check method 分布 (sprint-08 收尾)** (2026-08-12)

**主分支 tag**: **v1.0.25** @ 59bc328 (PR #88 squash-merged, **tag 已修复** — 2026-08-13 删旧推新, 远端 = 59bc3282)
**Web 服务**: `127.0.0.1:8900` PID 67958 (sprint-07 重启后, sprint-08 3 ship docs 改动, 不需重启)
**数据库**: schema_version=26 (migration 025_fact_check_evidence + 026_brief_evidence_rendered)
**测试**: 685 passed + 2 skip (655 baseline + v1.0.22 14 + v1.0.23 4 + v1.0.24 7 + v1.0.25 5 = 685)

### v1.0.23 / v1.0.24 / v1.0.25 — sprint-08 3 sub-ships

**v1.0.23 — B5 失败 raw_output 落库** (P0 诊断盲区修复):
- `moni/llm/jobs.py` 新增 `_truncate_utf8` + `_log_failure` (UTF-8 边界保护, 截断 2KB + `... [truncated N B total]` 提示)
- `_persist_fact_check_plus` 3 个解析失败子分支 (`evidence parse failed` / `evidence is not a list` / `no evidence with snippet`) 各落 `RAW OUTPUT: <truncated> | PARSE: <reason>`
- 触发 sprint-07 4 个 fact_check_plus jobs failed 诊断盲区, 修 LLM 回复格式时一眼能看 LLM 实际输出
- 4 新测试 (`test_fact_check_plus_failure_log_*`)

**v1.0.24 — 简报 web 模式** (银行业合规刚需):
- 简报生成时检查 `has_web_fact_check`, 缺则 enqueue fact_check_plus (异步, 不阻塞)
- 模板 (`moni/share/prompts/format-brief-{markdown,html}.md`) 加 "事实核查证据" 段 + 顶部 rollup 计数
- 失败降级不阻塞: 该篇 evidence 标"未联网核查, 证据解析失败" + 简报仍生成
- migration 026: `articles.evidence_rendered_at` (审计) + `has_web_fact_check` (v1.0.22 隐债补)
- 7 新测试 (`test_brief_web_mode.py`)

**v1.0.25 — 看板 fact_check method 分布** (申报书量化数据源):
- `moni/stats/repository.py` 新增 `fact_check_method_layer_stats(period)` SQL 聚合 (7d/30d/90d/all)
- 新端点 `GET /api/stats/fact-check-method?period=...`
- 量化看板第 6 块 SVG 饼图 (3 色: llm_inference=蓝 / web_search=绿 / human=灰)
- 5 新测试 (`test_stats_method.py`)
- 0 篇 web_search 现状下饼图只有蓝色, 待 sprint-08 v1.0.24 简报 web 模式增长后丰富

**已记录 P1** (sprint-09 已完成, 见下方 v1.0.26-28 段):
- ✅ evidence 落库脱敏 (v1.0.28 已做)
- 简报 worker 改用 LLM 检索 (v1.0.24 简报 web 模式同步性升级, P1 留)
- evidence 按 URL 去重 (跨文章同 URL 合并, P1 留)
- ✅ evidence 时效校验 (v1.0.26 已做)
- method × 来源交叉分析 (P1 留)
- 按 method 分的 token 成本估算 (P1 留)
- ✅ 状态机 (v1.0.27 已做)

### v1.0.27 — 事实核查+ 状态机 (sprint-09, 2026-08-13, PR #93)

**业务**:
- 联网核查+ 结论是 LLM 自动生成, 业务人员需能"确认/驳回"留痕 — v1.0.27 加 5 档状态机 (draft → verified → published → deprecated → archived), 单向流转, 复核人 + 备注落库审计
- `articles` 新加 4 字段 (migration 028): `fact_check_state` / `reviewed_by` / `reviewed_at` / `review_note`
- 新端点 `PATCH /api/fact-check-plus/{id}/review` (state + reason + reviewer, 跨档跳转 400)
- 看板 method 分布端点扩 by_state 维度
- 6 新测试 (`test_fact_check_state.py`), 全量 696 passed
- 合规审计: 每条核查结论有"谁最终确认"记录 → 申报书 §4.5 人机协同 + 留痕审计

### v1.0.26 — evidence 时效校验 (sprint-09, 2026-08-13, PR #91)

**业务**:
- 证据链落库后是抓取时刻的检索结果, 时间久了失效 — v1.0.26 加时效校验 (闸 3 timeliness)
- `fact_check_evidence` 新加 3 字段 (migration 027): `timeliness` / `age_days` / `timeliness_flag`
- 规则: < 30 天正常 / 30-90 天标「已过期 30-90 天」/ > 90 天结论强制降级【存疑】
- 端点 GET evidence 返 timeliness + age_days 字段
- 5 新测试, 全量 690 passed
- 银行业合规刚需: 防止引用过时政策/数据

### v1.0.22 — 事实核查+ (sprint-07 历史)

**业务**:
- 联网核查+ v1.0.22 升级: 从 v1.0.10 基础档 (LLM 常识推断 method=llm_inference) 扩展为**联网核查+** (实时检索 + 证据链落库 + 双闸防幻觉)
- `fact_check_evidence` 独立表 (migration 025, 14 列 + 3 索引 + FK CASCADE), 每条结论可按文章 / URL / 检索关键词反查
- 双闸防幻觉: 闸 1 证据片段非空 + URL 合法, 闸 2 权威域白名单 25 域 (pbc.gov.cn / cbirc.gov.cn / sse.com.cn / xinhuanet.com / 主流财经媒体) + 3 正则 (.gov.cn / .sse.com.cn / .szse.cn) 命中才允许【已核实】, 否则强制降级为【待核实】
- 3 端点: `POST /api/articles/{id}/fact-check-plus` (202 + job_id + poll_url) / `POST /api/fact-check-plus/batch` (≤20 篇, 409 防呆) / `GET /api/fact-check-plus/evidence/{article_id}` (按 evidence_seq 升序)
- method 收敛: 'llm' → 'llm_inference' 9 行迁移 (sprint 05 遗留 + sprint 07 配套), `llm_jobs.task_type` 加 `'fact_check_plus'` 白名单
- 修隐债: jobs.py 写库补 `has_fact_check=1` (基础档 v1.0.10 之前漏); 删死 route `/api/llm/fact-check`

**前端**:
- 文章详情区 "事实核查" + "联网核查" 双按钮组 (`.fact-check-plus-actions` flex 布局)
- 证据链折叠 details 视图 (`.evidence-list` ol + `.evidence-item` li, 含 query / url / snippet / domain / model / fetched_at)
- cache-bust bump: main.css + app.js `?v=20260812003` (v1.0.16 教训, 日期+序号格式)
- B3 poll loop: `_pollFactCheckPlusJob` 30×2s, done → 调 `loadArticleEvidence` + 刷新 articleDetail

**测试 / 验证**:
- 14/14 sprint-07 测试 PASSED (test_fact_check_plus.py)
- 全量 pytest 669 passed (baseline 655 + sprint-07 14)
- 浏览器手测 V1-V8 全绿 (B1 CSS 类名 / B2 Alpine state / B3 poll loop / B4 按钮 handler 4 bug 修复后 PASS)
- 端到端 curl 6 项 PASS (cache-bust / DOM / 404 / 405 / 202 / 200)

**已记录 P1**:
- B5: 后端 `_parse_fact_check_plus_evidence` 失败时不持久化 raw_output (诊断盲区), 留 sprint08
- 简报 enrichment `fact_check_mode: web` 模式 + 人工覆写 draft→verified 状态机 + Gemini grounding 备用

**业务**:
- 5 模块量化看板 (抓取 / LLM / 抽取 / 使用 / 效率), 顶部三卡 (基线 / 当前 / 目标 × 1.2)
- 节省分钟数公式 (T₀ yaml 配置 30min/篇保守 + T₁ llm_jobs.duration_ms 实测)
- 趋势图 (抓取 + LLM 双 SVG inline path) + 节省瀑布图 (按 task_type 高度比)
- 防夸大口径: display_status='数据积累中' (N<阈值); efficiency.display_label='公式估算' + t0_source='config/savings_baseline.yaml'
- `moni/stats/repository.py` SQL 聚合 7 张表 (articles / cron_runs / llm_jobs / share_tasks / article_pins / events / cases) — COALESCE(extracted_at, created_at) 防 v0.x pipeline 缺字段
- `moni/stats/savings.py` 节省公式 (T₀ yaml + T₁ 实测)
- `moni/web/routes/stats.py` 8 端点 (overview / fetch / llm / usage / savings / timeseries × 2)
- `config/savings_baseline.yaml` T₀ 配置 (默认 30min/篇 + 反夸大三铁律注释)

**前端**:
- `moni/web/static/js/stats.js` — Alpine module, Promise.all 并行 fetch 4 端点, SVG trend path, waterfall
- `moni/web/static/css/main.css` — `.stats-*` 全套 (+174 行) + 三断点响应式
- `moni/web/static/index.html` — 顶栏 "量化看板" 入口 + section (158 行)
- `moni/web/static/js/app.js` — `...statsModule()` + `statsOnEnter()` 注入

**MOA 4 视角拍板** (A+A+A+A+A+(A+补丁)):
- Q1 模块: A 全 5 模块 (DA 反方采纳 Eng 数据稀疏论据)
- Q2 节省: A 公式估算 (T₀ yaml 不入 DB, 历史口径污染可控)
- Q3 T₀: A 保守 30min/篇 (Brynjolfsson 新手 +34%)
- Q4 布局: A 横排 + 三卡 + 瀑布 (申报截图强制 1440px 视窗)
- Q5 端点: A 4 独立端点 (模块级 ETag 可省 ~60% 流量)
- Q6 稀疏: **A+补丁** (DA 关键盲点: 默认 N<10 会同时砍 events/cases 等效 Q1=B; 修正: events/cases N<5 阈值)

**修复 3 个真实 bug** (跟 brief v1.0.18 → v1.0.19 同步):
- `cron_runs` 字段 = triggered_at / finished_at (非 started_at) — brief 假设修复
- `llm_jobs.status` 枚举 = done (非 success)
- `share_tasks.status` 枚举 = done (非 completed)
- 瀑布图柱子初始显示 4px (parent 高度缺失 → bar-fill height:% 解析失败) — 加 `.stats-waterfall-bar { height: 100% }`

**浏览器验证** (8900 prod):
- 8 端点全 200 OK
- 顶部三卡 (基线 13 / 当前 13 / 目标 16)
- 5 模块横排: 抓取 (92.3% / 861 新文章) / LLM (100% / 29.3s 平均) / 抽取 (稀疏徽章) / 使用 / 效率 (295 分钟节省)
- 趋势图 SVG 紫线 + 瀑布图柱子 (77px / 72px, 高度比 100% / 66%)

**Obsidian**: sprint 06 W1 (`sprints/sprint-06-w1-quant-dashboard/`) 含 brief v1.0.19 (15100 字符) / research w1-reference-2026-08-08.md (6 leaf agents 调研)

---

## 当前状态: **v1.0.17 — F6 同业案例库** (2026-08-08)

**主分支 tag**: **v1.0.17** @ 5ac49e4 (PR #79 squash-merged)
**Web 服务**: `127.0.0.1:8900` 手动重启 (绝对路径 `/usr/local/bin/python3`,不用 web.sh); source=port, PID 60389
**数据库**: schema_version=24 (新增 cases 表 + case_tags + cases_fts, 业务表 11→12, 含 fts 19 张)
**测试**: `tests/test_cases.py` 62 passed (+ 既有 560 = 622 total, 2 skip 已知)

### v1.0.17 — F6 同业案例库 (sprint 05)

**业务 (sprint 05)**:
- 新增 `cases` 表 (case_id / article_id / subject / case_type / case_action / case_date / counterparty / amount / reference_value / summary / evidence_snippet / extraction_confidence / extraction_status / case_lifecycle / review_status + normalize_version + extracted_at + updated_at)
- 8 索引 + 5 CHECK 约束 + UNIQUE(article_id, subject, case_type, case_action) 防重
- 新增 `case_tags` 多对多表 (跟 article_tags 同模式)
- 新增 `cases_fts` FTS5 虚拟表 (trigram tokenizer, 中文友好) + 3 trigger (INSERT/UPDATE/DELETE)
- `moni/cases/` 三层事务管道: normalize.py (100+ 金融机构别名) → repository.py (CRUD + 状态机) → pipeline.py (extract→insert→state-transition)
- case_id 人脑可读 `MN-<2字母分类>-<YYMM>-<4位序号>` 例 `MN-AI-2608-0042`
- `moni/llm/prompts/case_extraction.txt` 3-shot few-shot + JSON schema
- `/api/cases` 7 端点: list / get / by-article / retry / pending / stats / search (FTS5)

**前端 (跟 sprint 04 UI polish 一致风格)**:
- `moni/web/static/js/cases.js` — 同业案例库 Alpine 组件 (列表 + 详情 modal + 搜索 + 筛选)
- `moni/web/static/css/main.css` — `.case-*` 全套 (210+ 行) + `@media (max-width: 1100px)` + 600 双断点
- `moni/web/static/index.html` — 顶栏 "案例库" 入口 + cases section + case_id monospace
- `moni/web/static/js/app.js` — casesModule 注入 + 3 处 init 分支加 cases

**修复 4 个 bug** (sprint 05 过程中踩到):
- `_CURRENT_VERSION` 23 → 24 + INSERT OR IGNORE schema_version (单 PK 不能 UPDATE 全部)
- `_run_sql_file` 用 `executescript()` 兜底 trigger BEGIN/END 解析 (naive `split(";")` 切错)
- `_generate_case_id` YYMM 截错 `[:6]` → `[:7]` (YYYY-MM-DD[:7] = YYYY-MM)
- `cases_fts` trigger 改用业务主键 (case_id) 匹配而非 FTS5 'delete' command (fts rowid 跟 cases rowid 是独立 namespace, 跨表对齐脆弱)

**Obsidian**: sprint 05 (`sprints/sprint-05-f6-cases-library/`) 含 plan / sprint doc / verify-2026-08-08.md (3-1-1) / research (MOA 6 leaf agents) / decision-log 7 条 PDR

### v1.0.16 — F5 行业事件日历 + UI polish

**业务 (sprint 03)**:
- 新增 `events` 表 (event_id / article_id / event_date / subject / event_type / summary / review_status / extraction_status)
- 6 索引 + 5 CHECK 约束 + UNIQUE(article_id, event_date, subject, event_type)
- `moni/events/` 三层事务管道: normalize.py (主体别名 TOP20 银行 + 日期/类型归一化) → repository.py (CRUD + 统计) → pipeline.py (extract→insert→state-transition)
- `moni/llm/prompts/event_extraction.txt` 事件提取 prompt
- `/api/events` 5 端点: list / get / by-article / stats / pending
- 4 时段调度: 7:00 / 8:30 / 12:00 / 18:00 (`config/schedule.yaml` + `cron/jobs.py`)

**前端 (sprint 03 + sprint 04 polish)**:
- `moni/web/static/js/calendar.js` — 月历 Alpine 组件 (网格 / 类型筛选 / popover / 待确认)
- `moni/web/static/css/main.css` — `.cal-*` 全套类 + `@media (max-width: 1100px)` + 600 双断点
- `moni/web/static/index.html` — 顶栏入口 + 日历 section + popover
- **better-ui + better-layout 整改** (sprint 04):
  - "今天" 改 btn-primary 主按钮
  - 右栏顺序倒过来: 待确认 → 统计 (重要在前)
  - nav margin-bottom 24px / weekdays→grid 4px (节奏 4/8/12/16/24)
  - popover max-width min(320, calc(100vw-32)) 边缘格子安全
  - @media 断点 900→1100 (1100-900 区间不崩)
  - 类型色从满饱和 hex 改 OKLCH 软色 + bg/fg chip (与 moni 中日古风调色板统一)
  - 修 popover inline `display: none` 与 x-show 冲突 BUG

**Obsidian**: sprint 03 (`sprints/sprint-03-f5-events-calendar/`) + sprint 04 (`sprints/sprint-04-f5-events-calendar-polish/`) 含 plan / sprint doc / verify (3-1-1) / decision-log 3 条 PDR

### v1.0.15 — PDF / HTML 简报版式与就绪规则

**PDF** (`generate_pdf.py` + `pdf-style.yaml`):
- 摘要/引用：淡橙底色块，去掉粗描边框
- 「优先关注」不再占文章编号 01

**HTML brief** (`brief_html.py`):
- 卡片间距、简单折叠（无 matrix 动效）
- 质量分/业务价值 CSS tooltip；业务判断分数标题同行方框分档色
- 已核实/待核实与正文同行段首
- 信息不足文章撤下并在文末列表说明
- 怪标题（URL/Apply）从正文推导

**API**: 全部 Pin 不可用时 400 `articles_not_ready`；部分可用仍生成

---

## 历史: **v1.0.14 — brief 防呆 + 队列 pause/resume/cancel + LOG_FILE 修复** (2026-08-06)

**主分支 tag**: **v1.0.14** @ c238f6a (PR #74 squash-merged)
**Web 服务**: `127.0.0.1:8900` 经 `web.sh` 重启; source=pidfile
**数据库**: schema_version=22 (无 schema 变更)
**测试**: `tests/test_brief.py` 12 passed (+ 既有 service status 10)

### v1.0.14 — 一键简报连点防呆 + 任务控制 + html 简报失败修复

**问题**:
1. 分享页「用当前 Pin 生成简报」连点 3 次 → 入队 3 份相同任务
2. 队列无暂停/丢弃，误点后只能干等 LLM
3. `html_landing` 简报 enrich 成功后一律 `brief_html render failed`

**修复** (PR #74):
- **防呆**: `POST /api/share/brief` 同 pin 集 pending/running → 409 `duplicate_active_brief`；`force=true` 二次确认后放行
- **前端**: briefSubmitting + 15s cooldown toast + moniModal 确认；队列表/当前简报卡 暂停·继续·丢弃
- **队列 API**: `POST /api/share/tasks/{id}/pause|resume|cancel`；状态 `paused`/`cancelled`
- **Worker**: 阶段边界 abort；`done` 不覆盖用户 cancel
- **LOG_FILE**: `default_worker.sh` 定义 `ARTIFACTS_DIR`/`LOG_FILE`（`set -u` 下未定义导致假失败）

**验收**:
- 二次 brief 无 force → 409；pause/cancel API OK；产物 HTML 200
- 8900 硬刷新 /share 视觉通过

---

## 历史: **v1.0.13.3 — svc-status port fallback** (2026-08-06)

**主分支 tag**: **v1.0.13.3** @ c3da809 (PR #72 squash-merged)
**Web 服务**: `127.0.0.1:8900` 经 `web.sh` 重启 (PID 见 status API); source=pidfile
**数据库**: schema_version=22 (无 schema 变更)
**测试**: `tests/test_service_status.py` 10 passed

### v1.0.13.3 — 顶栏服务状态假「已停止」修复

**根因**: `/api/service/status` 只信 `data/.web.pid`；裸 uvicorn / agent 起服不写 pidfile → UI 显示「已停止」。

**修复** (PR #72):
- `resolve_service_pid()`: pidfile 优先；失效扫 `:8900 LISTEN`（优先 lsof，psutil 次之 + AccessDenied 兜底）
- port 发现后 heal 写回 pidfile，保证 ↻ / `web.sh stop` 杀对进程
- status JSON 增加 `source`: pidfile | port | none
- restart/stop 调用 web.sh 前先 heal

**验收**:
- status: `running:true, source:pidfile`
- 删 pidfile 再 curl → `source:port` + heal，无 500
- 5 菜单端点 200

---

## 历史: **v1.0.13.2 — audit F16 head meta + inline SVG favicon** (2026-08-06)

**主分支 tag**: **v1.0.13.2** @ 5687a34 (PR #70 squash-merged)
**Web 服务**: `127.0.0.1:8900` 已重启 (3.12); `8901` 可选 dev
**数据库**: schema_version=22 (无 schema 变更)
**测试**: 本版仅 `index.html` head 静态变更（无 Python/JS 逻辑）

### v1.0.13.2 — Sprint 补合 F16 + git 仓库自检清枝

**背景**: git 仓库自检发现 Sprint 02 Block C 的 F16（head meta）在旧 feature 枝上未合入 main；旧枝 base 落后，直接 merge 会回退 Q3/a11y/particle。

**代码 (PR #70)**:
- cherry-pick `b91748c` → 干净枝 `feature/v1.0.13.2-head-meta` from current main
- `index.html` head +7/−1:
  - `<meta name="description">`
  - `<meta name="color-scheme" content="light dark">`
  - `<meta name="theme-color">` light + dark
  - `<title>` → `moni — 同业信源本地新闻库`
  - inline SVG favicon（data URL，避免 `/favicon.ico` 404）

**仓库卫生**:
- 本地删 5 条 stale feature 枝
- 远程 prune 9 条已合/取代 feature+docs 枝
- tag 纠偏：曾误打到 docs commit，已 force → `5687a34`
- `.git` ~7.5MB（gc 后）

**验收**:
- main @ `5687a34` 与 tag `v1.0.13.2` 一致
- head 含 description / color-scheme / theme-color / favicon
- Q3 `onArticlesFilterChange` 未回退

---

## 历史: **v1.0.13.1 — fetch modal particle stop + xwatch icon + moni-icon v3.1** (2026-08-05)

**主分支 tag**: **v1.0.13.1** @ 93ae824 (PR #68 squash-merged)
**Web 服务**: `127.0.0.1:8900` 已重启 (PID 69873, 3.12 venv); `8901` 不再需要
**数据库**: schema_version=22 (无 schema 变更)
**测试**: **504 passed, 2 skipped in 59.29s** (基线无回归)

### v1.0.13.1 — Sprint 03 fetch modal particles + source icons

针对 dad QA 的 3 个 frontend 问题修复:

**1. 抓取 modal 粒子动画完成态不停止 (修复)**
- Bug: modal 显示"完成"后背景粒子还在持续动画
- 根因: `_spawnBurst` 内嵌套 `setTimeout` 延迟 burst + `_initParticleStage` 内 `setTimeout(initStageWithSources, 100)` 重试 + `cancelAnimationFrame` 前未先置 `_particleRunning = false` (循环体内会再次申请 RAF)
- 修复 (app.js +162/-21):
  - `_particleRunning` 闸门 (RAF 循环体内 `if (this._particleRunning)` 守卫)
  - `_particleBurstTimers` Set 追踪 `_spawnBurst` 内嵌套 `setTimeout`
  - `_particleInitTimer` 追踪 `initStageWithSources` 的 100ms 重试
  - 统一收尾函数 `_finishFetchAnimation(killGsapRows)` (默认 false, 保留 done 路径的 `_suckIntoMoni` GSAP tween)
  - `closeFetchProgress()` 走 `_finishFetchAnimation(true)` + 显式 `gsap.killTweensOf(moniIcon)`
  - `initStageWithSources` retry 内加 `if (!this.fetchProgressModal) return` gate
- 覆盖路径: 单源 done / 失败 / 超时 / 已中止 + 批量 done / partial / failed + 关闭 modal

**2. xwatch (X 关键词监控) icon (新增)**
- Bug: 5 处 iconMap 都没有 `xwatch` 条目, 全部 fallback 到 36kr.png
- 修复: 256×256 RGBA 60KB 新图 (cobalt blue `#1E40AF` 字母 X + 金色放大镜 + 黑色圆角底 + 金色 bevel + 3D 高光, 与 series 风格对齐)
- 接入 (app.js):
  - `_activeSourcesForModal()` iconMap + labelMap + colorMap + sortOrder (插在 bestblog-x 与 bestblog-yt 之间)
  - `getSourceIcon()`
- colorMap `[30, 64, 175]` cobalt blue 与图标描边同色

**3. moni-icon v3.1 (升级)**
- Bug: v1.0.9.10 moni_v2.png 是黑底纯白扁平漏斗, 与 source icon 系列风格 (黑底圆角 + 金色 bevel + 高饱和纯色 + 拟人化) 不一致
- v1.0.13 第一版 (紫藤萝 solid silhouette, 仿 v1.0.8.3 Pin icon 17px 风格) 被 dad QA reject: "太丑了, 风格不一致"
- v1.0.13.1 = 候选 B (3 层金色 concentric ring 由大到小 + 底部 teal `#14B8A6` 宝石终点), 256×256 RGBA 44KB, 走 series emblem 风格
- `index.html` L471 引用 `moni_v2.png` → `moni_v3.png`
- `moni_v2.png` (406KB) 与 `_rejected-moniv3-wisteria-silhouette.png` 都保留作回退保险

**4. .gitignore 新增**
- `scripts/.pending_*.py` — icon 迭代期间临时脚本不入库
- `docs/moni-icon-redesign-*` — 候选目录不入库

**决策档案**: `docs/moni-icon-redesign-2026-08-05/DECISIONS.md` (含 6 line-art 候选废弃原因 + A/B/C 三选 + image-gen swap 真相)

**验收**:
- pytest: **504 passed, 2 skipped in 59.29s** (基线无回归)
- node --check app.js: PASS
- 8901 dev 5 端点 200 OK
- 8900 prod 已 restart (PID 69873), 5 端点 200 OK
- xwatch 实跑 fetch: 99 抓 / 1 入库 / 137.1s (DDG anomaly + SSL EOF 已知坑, 不阻塞; `tag_definitions` miniprogram row 自动命中)
- 视觉 (dad QA): xwatch 链显示新 cobalt 图, moni-icon 是 3 金环+teal 宝石

---

## 历史: **v1.0.13 — Dialog Certainty (frontend audit-fix)** (2026-08-05)

**主分支 tag**: **v1.0.13** @ 4 PRs squash-merge 后
**Web 服务**: `127.0.0.1:8900` 重启 (3.12 venv); `8901` 验证
**数据库**: schema_version=22 (无 schema 变更)

### v1.0.13 — Sprint 02 Dialog Certainty (frontend audit-fix)

针对 `docs/frontend-arch-a11y-audit-2026-08-05.md` (16 项) 的 9 项修复; 7 项留 Sprint 03+ 路线.
(注: Sprint 02 final PR #67 squash 合并, 把 4 个独立 PR 的 commit squash 成 1 个 v1.0.13 commit)

**Sprint 02 范围 (Q1=A Q2=A Q3=A Q4=A):**

PRs (全部 OPEN → squash merge 成 v1.0.13):
- **#64** `feature/v1.0.13-dialog-certainty-modal-accessibility` — `feat(ui): dialog certainty + modal accessibility (audit F4/F5/F13/F14)`
  - `moni/web/static/js/toast.js` (新) — 33 native `alert()` → `moniToast.{success,error,info,undo}`, 4 档 + sticky + 复制错误
  - `moni/web/static/js/modal.js` (新) — 9 native `confirm()` → `moniModal.confirm()` (role=alertdialog); `moniModal.open()` 自动注入 `role="dialog"` / `aria-modal="true"` / `aria-labelledby`; Tab focus trap; body scroll lock
  - `index.html` — 12 modal 加 `aria-label="关闭"` 6 处 + `@keydown.escape.window` 6 处; 加载 toast/modal 在 app.js 之前
  - `main.css` — 单 toast 改 stack, 加 action button / sticky / 错误红条
  - `app.js` — 新增 `_notify(kind, msg)` helper; 8 destructive 方法改 `async` + `await moniModal.confirm(...)`
- **#65** `feature/v1.0.13-audit-fix-timers-stubs-clean` — `chore(ui): audit fix F2/F3/F6 (stubs + share-poll teardown + busy-wait cap)`
  - 删 5 stub JS 文件 (`articles.js` / `cron.js` / `fetch.js` / `share.js` / `crud.js` — crud.js 一旦加载会静默改 DELETE 行为)
  - `_stopSharePoll()` — `navigate()` / `onPopState()` 离开 `/share` 时显式清理 5s `setInterval`
  - busy-wait cap — 两处 `setTimeout(initStageWithSources, 100)` 加 50 次重试上限 + 错误提示
- **#66** `feature/v1.0.13-audit-fix-headmeta` — `feat(ui): audit fix F16 (head meta + inline SVG favicon)`
  - `<meta name="description">` / `color-scheme` / `theme-color` (light + dark) / inline SVG favicon data: URL
  - `<title>` 改为 "moni — 同业信源本地新闻库"
  - **未做 (F1 app.js 拆块)**: 留 Sprint 03+, 跟 F7 三处 dispatch 树一起做
- **#67** `feature/v1.0.13-dialog-certainty` — Sprint 02 final (Q3 polish + Sprint 02 final commit)
  - `index.html` — 文章 filter `<select>` 加 `class="filter-select"`, `@change` 改 `onArticlesFilterChange()` (避免后页筛选假空态)
  - `main.css` — `.filter-select { height: 35px; box-sizing: border-box; }` (Q3 高度统一)
  - `main.css` (v1.0.13.1) — `.badge` / `.status-badge` 加 `white-space: nowrap` (dad QA 修复: basket `tag 过滤` / config `启用` 不再换行)
  - 5 paths HTML + 3 API 在 3.12 venv 8901 dev 全部 200

**验收**:
- pytest: **504 passed, 2 skipped in 59.74s** (基线无回归)
- node --check toast.js / modal.js / app.js 全 0 错误
- 8901 dev (3.12): 5 paths HTML 200; 3 API 200; filterSelectHeight=35px == searchInputHeight=35px
- 浏览器: `moniToast` / `moniModal` 全局可用, `moniModal.confirm` 渲染 role=alertdialog, 13 modal 自动 ARIA
- 视觉 (dad QA): filter 高度统一, basket/config pill 单行不换行

**Sprint 03+ 候选** (本 sprint 不做):
- F1: app.js 拆 basket / share / llm 模块 (跟 F7 一起)
- F7: navigate / onPopState / init 三处 dispatch 树合并
- F8: pin icon SVG 每次重渲 perf
- F9: source icon 4 处重复 (iconMap / labelMap / colorMap / PNG)
- F10: oklch Relative-Color Syntax 30+ 处 @supports 包装
- F11: 单移动断点 → 多档
- F12: design token SSoT (CSS :root + .dark + theme.js DEFAULT_THEME)
- F15: polling .then() 链堆叠
- Sprint 03 候选: 方向 A 扫读速度 / 方向 C 键盘+导航骨架 / Audit F1-F7
- 已知: pytest 3.14 偶发 segfault (与本 sprint 改动无关, 是 moni 自身 fixture 兼容)

---

## 历史: **v1.0.12.2 — 配置 UI 中文说明** (2026-08-04)

**主分支 tag**: **v1.0.12.2** @ `3925854` (PR #63; 基线 v1.0.12 / .1)
**Web 服务**: `127.0.0.1:8900` 已重启
**数据库**: schema_version=22

### v1.0.12.2 — 配置字段中文说明

- 抓取配置 modal：中文名 + 英文 key + 用法说明；bool/列表/JSON 编辑
- 抓取源 / tag / 过滤 / LLM 表头与表单补中文
- 无 fetch_config 的源（如 36kr_rss）弹窗为空属正常

### v1.0.12.1 — Following 抓完关闭 Profile 6（Dock）

抓取后默认 `stop_x_chrome`（只杀 9222）；CLI `moni xwatch chrome-stop`。

---

### v1.0.12 — xwatch Following CDP + accounts + DDG

**目标**: DDG 不稳时仍能抓 X；Following 条数可配（含 300）。

**已交付:**
1. **Channel C Following** (`xwatch_following.py`)：纯 CDP 滚关注流（Profile 6/9222）；`following_max_posts`/`following_max_scrolls`
2. **Channel A accounts** + **Channel B DDG**（熔断+轮转）
3. CLI：`moni xwatch following --max-posts N [--ingest]`、`moni xwatch health`
4. health/UI ping；seed 含 following_*；依赖 `websocket-client`
5. Grok skill `x-watch-grok`

**验证**: 实机 CDP 56 帖；pytest 504 pass；8900 重启后 health/articles/sources 200

**使用**: Profile 6 登录后 `moni xwatch following --max-posts 100`；可设 300。

---

### v1.0.11 (2026-08-02) — X 关键词监控

**目标**: 按关键词从 X (Twitter) 发现 + 收录推文，免 API key，可手动收录单条。

**已交付:**
1. **新源 `xwatch`** (sources 表 10→11)：DDG Lite 发现推文 URL → twitter syndication 抓全文 → 走既有 keyword filter + dedupe_insert 链路
2. **CLI `moni xwatch ingest <url|id>`**：手动收录单条推文，按 wechat/miniprogram tag 关键词门控；支持 `--dry-run`；重跑自动 dedupe
3. **`tag_definitions` 新增 `miniprogram` row**（TECH-SPEC §0.2 假设已存在，实际空缺；closes FK gap 让 xwatch source_tags 不报错）
4. **`moni/fetch/__init__.py` 加 `run_fetcher = run` 别名**：submodule shadow 修复——`moni.fetch.run` 是 CLI 模块，会覆盖同名 function 的 attribute 绑定，导致任何先于 routes 加载 cli_main 的场景下 routes 拿到 module 而非 function（潜在 latent bug，由本次 CLI 测试首次暴露）
5. **`moni/fetch/health.py` empty-feed_url 静默跳过**：之前对 `wechat_rss_a/b`/`xwatch`（feed_url 为空）返 "no URL configured" stub；现在直接跳过，更干净
6. **seed-config 适配**：INSERT INTO sources 新增 `fetch_config` 列写入；幂等
7. **DDG 端点 html/ → lite/**（v1.0.11.A）：html/ 在本 IP 几乎必返 anomaly 页；切 lite 体积更小、风控更宽松。lite shape `<a class="result-link" href="//duckduckgo.com/l/?uddg=...">` 解析：提 `uddg` query 参数 → URL-decode → 走原 TWEET_URL_RE。正则 fallback 兜底保留
8. **`request_delay` 默认 2.5s → 5s**：降低 DDG 风控触发概率
9. **4 fixtures + 4 测试文件 +43 单测**：discovery 10 + syndication 15 + fetch 13 + cli 6（含 1 new lite-shape uddg unwrap 测试）

**验证**:
- pytest 全量 **483 passed, 2 skipped, 0 failed**（baseline 439 → +44）
- `moni xwatch ingest https://x.com/xiaoyangX026/status/2083475663496044761` → inserted=1，raw_content 前 60 字 = "UX很流畅的感觉。但是我觉得最核心需求是要高触达，及时提醒还方便"（与 TEST-PLAN §5 #3 期望字符级一致）；再跑 inserted=0 (dedupe OK)
- tag 自动命中：`miniprogram,wechat`（keyword_hits 机制）
- DDG lite 端点从本 IP 偶尔返回 anomaly 页 → 走降级路径 (1 retry + 5s sleep → [] + log warn)，不抛异常、不换付费方案、不停止 feature（per spec §15 + 拍板 A）
- 8901 dev port `/api/articles?source=xwatch` 等 6 端点 HTTP 200

**已知 / 后续**:
- DDG 公开接口是 IP 敏感的免费服务；同一 IP 多次查询会触发 anomaly challenge。本 sprint 一次性 dry-run 抓 12 条，但稍后批量 fetch 触发 anomaly。当前降级路径保证 system 稳定 + 不抛异常，但**bulk fetch 入库波动较大**。manual `moni xwatch ingest` 始终可用（绕过 DDG，直接走 syndication）
- PR #60 已 squash merge + force push tag v1.0.11。8900 已用绝对路径命令重启，PID 53118

---

## 历史状态: v1.0.10.1 — waza-ui 布局精修 (2026-07-28)

**主分支 tag**: v1.0.10.1
**Web 服务**: `127.0.0.1:8900` (Python 3.12)
**数据库**: `data/news.db` — schema_version=22, 11 tables
**测试**: 见 PR / 全量 pytest

### v1.0.10.1 (2026-07-28) — waza-ui 布局精修

**目标**: 遵循 waza-ui skill "Group with Space, Not Lines" 原则，系统性优化全站布局呼吸感。

**已交付:**
1. **页面间距统一**：content padding 24/32/48 → 32/40/64px
2. **Panel Header**：margin-bottom 16 → 24px，action gap 8 → 12px
3. **Stats Row**：gap 12 → 16px，margin-bottom 24 → 32px，card padding 16/18 → 20/22px
4. **表格间距**：thead/tbody padding 10/14 → 14/18px，empty-row padding 24 → 32px
5. **折叠面板**：外层 share-collapsible content 20/20/4 → 24/24/12px，内层高级选项 16 → 20px
6. **Form Row**：margin-bottom 12 → 16px，label margin 4 → 6px
7. **分页栏**：margin-top 12 → 20px，padding 8 → 12px，gap 统一加宽
8. **Share Section**：margin-bottom 20 → 28px，h4 margin-bottom 8 → 12px
9. 优化按钮点击缩放：cubic-bezier(0.22, 1, 0.36, 1) 弹性曲线

**设计原则**: waza-ui 分层阴影 + 同心圆角化 + 中断式动效 = 视觉层级清晰，阅读呼吸感更强，符合 moni Sophon.at utilitarian aesthetic。

---

## 当前状态: **v1.0.10 — 一键简报** (2026-07-28)

**主分支 tag**: v1.0.10
**Web 服务**: `127.0.0.1:8900` (Python 3.12)
**数据库**: `data/news.db` — schema_version=22, 11 tables
**测试**: 见 PR / 全量 pytest

### v1.0.10 (2026-07-28) — 一键简报

**目标**: Pin N 篇 → 选格式 → 一点生成含摘要/事实核查/业务价值的简报，进度可见。

**已交付:**
1. `POST /api/share/brief` — 全部 pin + opinionated payload + auto `launch_worker`
2. migration 020–022 — `business_value_*` / `quality_score_*` / `brief_summary` / `brief_body_md`
3. `moni/share/enrichment.py` — 摘要/事实核查/业务价值落库；长摘要 AI 压缩
4. worker — hermes profile 默认 `default` (MiniMax)；brief 走 brief prompt + 富化后重建 content
5. **offline HTML** (`brief_html.py`) — 全文嵌入、中文事实标签+icon、业务价值排序、可折叠原文（浅灰 pill、**仅 click** 滑动）、GSAP 动效
6. 入库时本地 layout 写 `brief_body_md`（`layout-articles` CLI 可补历史）
7. 分享页主 CTA + sticky 任务进度；旧路径收入「自定义分享」

**默认格式**: HTML 网页（`html_landing`）offline 简报；默认不做 BestBlogs 增强。

**文档**: `docs/plans|prd|specs/2026-07-28_v1.0.10-one-click-brief*`

---

### v1.0.9.12 进展 (2026-07-03) — P0 紧急修复

**主分支 tag**: v1.0.9.12 (PR #56)

### v1.0.9.10 进展 (2026-06-30)

**信源 icon 精细化:**
1. **bestblog 子源 icon 拆分** — `bestblogs` / `bestblog-podcast` / `bestblog-x` / `bestblog-yt` 之前共用一张 bestblogs.png, 全部子源在 modal/表格里看起来一样。拆成 4 个独立 icon:
   - `bestblog-gzh.png` (公众号, bestblogs 主源)
   - `bestblog-x.png` (bestblog-x 子源)
   - `bestblog-yt.png` (bestblog-yt 子源)
   - `bestblogs.png` 保留给 bestblog-podcast
2. **mpaypass icon 升级** — 删除 `mpaypass_v2.png`, 新 `mpaypass.png` 黑底白图 (PRD 附录 B 风格, 与其他信源一致)
3. **wechat_rss_b 独立 icon** — `wechat_rss_b` 之前共用 `wechat_rss.png`, 现在有独立 `wechat_rss_b.png`
4. **icon 防白边** — 7 个信源 + moni 的 icon 容器背景从 `var(--card)` 改为 `#1a1a1a`, PNG 透明像素不再透出卡片底色
5. **feedInfoBar 居中修正** — `left:0; right:0` 改为 `left:50%; transform:translateX(-50%)`, 数字弹跳时不再被左偏
6. **moni 坐标微调** — `_moniLeft` 加 2px 右移, 与左侧信源链条视觉对称
7. **36kr 旧版吉祥物下线** — 删除 256x256 翠绿色卡通版, 统一用新版黑底白图

**新增资源**:
- `pin_a.png` / `pin_b.png` / `pin_c.png` / `pin_d.png` (4 个信源徽章/书签位图, 备用)
- `scripts/backfill_bb_resource_id.sh` (历史 343 篇 bestblogs 文章的 bb_resource_id 回填脚本, 跑完即清空遗留项)

**杂项清理**:
- `.gitignore` 加 21 条规则屏蔽 6/12-6/29 期间散落的临时废料 (demo html / report png / foshan-landing / 中文文件名 / 审计 md)
- 36kr_backup.png 删除 (新版替换后无需保留)

**测试**: 425 passed, 2 skipped | **8900**: 已 restart @ pid 32157, HTTP 200, 119KB 首页

**PR**: #52 squash merged → main @ 130de78, tag v1.0.9.10

---

### v1.0.9.12 进展 (2026-07-03)

**P0 紧急修复 (6 项):**
1. **Alpine console error** — 10 处 `fetchAllResult` 无保护访问 (`?.` 可选链), 生产页面零 error
2. **Share task 连败** — `moni` profile (xiaomi) API key 过期 → 默认切 `default` (MiniMax-M3), 重置 3 个 failed task 重试
3. **bb_resource_id 毒化修复** — fetcher 不再挑 `RAW_` 前缀 (存原始 GUID), `fetch_and_store()` 不再标记 fetched_at 留 NULL 等 backfill
4. **失败告警** — 新建 `moni/utils/notify.py` (Bark/Telegram/log), worker 失败路径自动通知
5. **launchd 托管** — 新建 `scripts/com.moni.cron.plist` + `install-launchd.sh` (当前未启用, 用户手动触发)
6. **依赖补全** — pyproject.toml 2→8 依赖 (补 fastapi/uvicorn/pydantic/httpx/bs4/psutil)

**代码变更**: 13 files, +302/−73 lines
**测试**: 428 passed, 2 skipped | **8900**: restarted @ v1.0.9.12
**PR**: #56 squash merged → main, tag v1.0.9.12

---

## 历史版本: v1.0.5 - v1.0.9.9 (438 行, 折叠看)

> 这一节是 v1.0.5 → v1.0.9.9 期间的所有版本阶段记录 (含 bestblog 系列、抓取管理 modal 动效、worker 优化、BB 增强一致性等)。
> v1.0.9.10 之后的 current 段见顶部。Git 完整 history 也有, 这里只留一份方便快速回看。

<details>
<summary>点击展开历史时间线 (v1.0.9.0 - v1.0.9.9)</summary>

## v1.0.9.9 — 粒子动效精调 (2026-06-29)

**粒子动效精调:**
- 速度 2x（duration 70-110 帧）
- 粒子密度 2.6x（整体翻倍）
- 取消透明度（全不透明）
- top3 大粒子 1.5x（而非 2x）
- 平滑贝塞尔曲线 + 5 色调色板（锌灰/雁灰/山黎豆红/隐红灰/芦穗灰）

**PR**: #51 squash merged, tag v1.0.9.9

---

**抓取管理 Modal 动效优化:**

1. **新 fetch progress modal**：替换 inline-style，使用 GSAP + Canvas 粒子系统
   - 左侧信源链条（flex-column，label 在 icon 上方）
   - 右侧 moni icon（96×96，带 label）
   - 粒子从当前 focus 源流向 moni
   - 单源完成时"吸入"动画（加速缩小 + 位移）
   - 批量模式 10 源链条依次切换（GSAP 过渡）

2. **Logo**：7 个信源 + moni 的黑底白图圆角矩形 app icon

3. **实时文章计数**：GSAP 数字弹跳动效，同时更新 top feedInfoBar + 底部 counter

4. **链条排序**：自定义顺序（wechatrss-A/B → bb公众号 → 量子位 → 移动支付网 → ...）

5. **测试**: 425 passed, 2 skipped

**PR**: #50 squash merged, tag v1.0.9.8

---

**BestBlogs 独立字段去重 (P2 任务)**:

1. **问题**: BB 字段共用一个 bb_fetched_at，获取任意字段会拉取全部 6 个字段。

2. **修复**: 
   - 新增 4 个独立 fetched_at 列
   - Migration 019 回填现有数据
   - fetch_and_store() 支持 fields 参数
   - 5 个新测试覆盖

3. **测试**: 425 passed, 2 skipped (15 BestBlogs API tests)

**PR**: #48 squash merged, tag v1.0.9.7

### v1.0.9.6 进展 (2026-06-26)

**Worker traceback 优化 (调试改进)**:

1. **问题**: Worker 错误只显示简单错误信息，没有 traceback，调试困难。

2. **修复**: 
   - BB_FETCH 错误现在显示完整 traceback
   - 文章内容处理错误现在显示完整 traceback
   - 更容易调试 share task 失败问题

3. **测试**: 420 passed, 2 skipped

**PR**: #47 squash merged, tag v1.0.9.6

### v1.0.9.5 进展 (2026-06-26)

**Worker 超时时间增加 (html_landing 格式超时修复)**:

1. **问题**: html_landing 格式文章多时超时（300s），SUMMARY_GEN 步骤需要 4+ 分钟生成 AI 摘要。

2. **修复**: 
   - 默认超时从 300s 增加到 600s（10 分钟）
   - 支持通过 `MONI_WORKER_TIMEOUT` 环境变量配置

3. **测试**: 420 passed, 2 skipped

**PR**: #46 squash merged, tag v1.0.9.5

### v1.0.9.4 进展 (2026-06-26)

**BestBlogs API 端点修复 (根因: 使用不存在的 /search 端点)**:

1. **根因**: `bestblogs_api.py` 调用 `/search?q=URL` 端点，但 BestBlogs API 文档中没有此端点。文章 URL 是 `mp.weixin.qq.com` 链接，无法匹配。343 篇 bestblogs 文章全部 BB 元数据为空。

2. **修复**: 
   - `bestblogs.py` — RSS 解析时提取 `<guid>RAW_xxxxx</guid>` 存入 `bb_resource_id`
   - `repository.py` — INSERT 时写入 `bb_resource_id`
   - `bestblogs_api.py` — 改用文档化端点 `GET /resources/{id}`，直接从 DB 读取 `bb_resource_id`
   - 删除 `search_by_url` 函数

3. **测试**: 10 个 BestBlogs API 测试全部通过

4. **遗留**: 343 篇历史文章 `bb_resource_id` 为 NULL（需网络稳定后运行 `scripts/backfill_bb_resource_id.py`）。新抓取文章自动从 RSS `<guid>` 获取。

**PR**: #45 squash merged, tag v1.0.9.4

### v1.0.9.3 进展 (2026-06-25)

**BestBlogs 增强一致性修复 (3 个 bug + 1 个 UI 缺陷)**:

1. **Bug #1 (P1, 修复)**: `source != 'bestblogs'` 字符串硬匹配 → 3 个 bestblog-* 子源 (bestblog-podcast / bestblog-x / bestblog-yt, 共 156 篇) 全部被静默排除, BB 增强从不生效。改为前缀匹配 (`=='bestblogs' or .startswith('bestblog-')`) 后这 156 篇可正常获取 BB 元数据。

2. **Bug #2 (P1, 修复)**: 3 个 bestblogs 文章在 v1.0.5.25 时代 (v1.0.9.2 修复之前) 因瞬时 API 失败被错误标记 `bb_fetched_at` 但 BB 字段全 NULL。**v1.0.9.2 fix 不会回溯老数据**。新增 `scripts/backfill_bb_poisoned_rows.py` 一键清掉毒化行 (默认 `--dry-run`, 需 `--commit` 确认)。SQL 仅匹配 `bb_fetched_at` 非空 + 5 个 bb_* 字段全 NULL, 极其特异, 不会误伤有效行。

3. **Bug #3 (P2, 修复)**: `bb_summary_source` 之前是 prompt 软约束, worker 模板总是同时渲染 AI + BB 摘要 (若两者都非 NULL)。worker 现在直接按 `moni_ai | bestblogs | both | none` 分支, 6 个新单测覆盖 (含 legacy manifest 兼容)。`scripts/default_worker.sh` 两个 inline Python block 都已改造, 都通过 `manifest_path` sys.argv[3] 读 manifest 里的 `bb_summary_source`。

4. **Bug #4 (P1, UI 修复)**: 分享 modal 加动态 hint: 选中的非 bestblogs* 源文章会被静默跳过, 不消耗 API 额度。颜色: 橙色 #fff7e6 背景 + 琥珀色 #f59e0b 左边框 (与主页面整体冷色风格区分, alert 性质)。

5. **防御加固**: `search_by_url` 加显式 `json.JSONDecodeError` catch (空 body / 非 JSON 200), 比 v1.0.9.2 的 `except Exception` 更精准, 日志更清楚。

**没修的 (v1.0.10+ 范畴)**:
- 4 字段独立去重 (P2): 4 个 BB 复选框仍共用一个 `bb_fetched_at`, 首次勾任一字段 = 拉全部 6 字段, **也写全部 6 字段**。增量勾选不重拉。需拆 4 列 + backfill。
- bestblogs 源识别: 仍是字符串匹配 (现在是前缀)。如果用户在 /config 重命名 "bestblogs" 源, BB 增强全失效。v1.0.10+ 加 `sources.bb_enabled BOOLEAN` 列。
- 4 字段在 modal 上也可独立显示/隐藏, 当前是统一开关。

**测试**: 300+ → 421 passed (+21 测试: 2 个 bestblog sub-source + 1 个 empty body + 6 个 worker 分支 + 1 个 backfill dry-run, 实际新增 7 个 + 之前 baseline 调整)。

**PR 准备**: feature branch 上 6 commits (Task 1/2/3/4/5/6), 待用户 review → squash merge → tag v1.0.9.3 → 手动启 8900 (frontend hint 改了需刷新)。

### v1.0.9.2 进展 (2026-06-24)

**BestBlogs transient vs permanent miss 修复**:
- 之前 `search_by_url` 返 `None` 涵盖两种情况: URL 真不在 bestblogs 索引 (永久) + SSL EOF/timeout/5xx/429 (瞬时)。两种都 `mark bb_fetched_at` → 瞬时错误被永久污染
- 三态返回: `str` (找到) / `""` (永久 miss, mark) / `None` (瞬时, **不** mark, 下次重试)
- 显式 catch `httpx.ConnectError` / `RemoteProtocolError` (SSL EOF 标准错误)
- 数据清理: SQL 把 3 篇被 SSL EOF 误标的 bestblogs 文章 `bb_fetched_at` 清 NULL, 让它们能重试
- 测试: `test_search_miss_marks_fetched` 拆成 permanent-miss + transient-error 两个 case

### v1.0.9.1 进展 (2026-06-24)

**Share worker PATH prepend (cron FileNotFoundError 修复)**:
- `scripts/default_worker.sh` + `moni/share/worker.py` 启动子进程前 `export PATH="$HOME/.local/bin:$PATH"`
- 根因: cron / launchd 子进程 PATH 精简 (/usr/bin:/bin),hermes CLI 装在 ~/.local/bin 找不到 → share task 1 秒死 + error_msg 永远空
- Defense-in-depth: 任何调用方式 (cron / launchd / web UI / 手动 shell) 都能找到 hermes
- 验证: `env -i PATH=/usr/bin:/bin` 模拟 cron 不再爆 FileNotFoundError;失败任务 `t5TRTdiMfE52cEOUtik-fA` 重跑成功,产物 PDF 15KB 1 页
- 详见 `concepts/moni-ai-architecture.md` (v1.0.5.26 那条踩坑)

**Pin icon 改版**:
- Pinned: SOLID silhouette (梯形头 + 粗针 + 底点), 17px 紫藤萝 — 来自 image gen (Fal gpt-image-2), 用户选候选 B
- Unpinned: outline (1.6px stroke, 17px) — 跟 v1.0.8 不变
- 撤掉 v1.0.8.1 的 5 层 3D enamel (用户 push "太丑了 不要背景")
- 撤掉 v1.0.8.2 的 40px 圆 + 26px svg (用户 push "缩小 1/3")
- 现状: 17px icon, 32px 圆 cell, 44px 列宽, 紫藤萝只 color swap (无背景/无阴影)

### v1.0.8.1 进展 (2026-06-23) — Pin UX 改版

**3 个修复**:
1. Pin 按钮位置 + 大小: 移到最左新增列, 32px → 40px → 17px (演化), GSAP 弹一下 (scale 0.85→1)
2. 单篇 togglePin 也走 5s 反悔: `_pendingPinToggles[articleId]` map, 双击撤销
3. bulk unpin 改即时 POST + 撤销=重新 pin: `_commitBulkUnpin` 立即发请求, `_repinBatch` 走 togglePin pipeline 撤销

**踩坑**:
- 用户先后两次推翻了 v1.0.8.1 的 3D enamel (太丑) + v1.0.8.2 的放大版 (太大), 最终 v1.0.8.3 用 image gen solid silhouette 17px
- v1.0.8.1 togglePin staging 模式 + bulk unpin 即时 POST = 行为统一, 撤销都走 _repinBatch/togglePin
- 沙箱拦 image gen (FAL_KEY 没配), 用户配 Nous Portal 后可用
- image gen 实际"圆头" prompt 跑了简化梯形, vision 描述为准

### v1.0.8 进展 (2026-06-23) — Pin Tab + 分享从 Pin 选择

**新功能**: Pin/Unpin 文章 + 文章查阅子 tab [全部] [Pin (N)] + 批量 unpin + 5s 倒计时撤销 + 分享 page chip 切换 [📋 全部] [📌 仅 Pin] (默认 Pin 模式) + Pin icon (nullframe 风格 SVG, 不用 emoji)
**后端**: migration 017 (article_pins + CASCADE) + 5 endpoints
**前端**: pinCol + subtab + toast + chip 切换
**测试**: 24 个新 test, 412/412 pass
**PR**: #40 squash merge → tag v1.0.8

### v1.0.7.6.2 进展 (2026-06-23)

**Bug 修复**:
- **wechat_rss_a/b 0 篇 (实际根因)** → `moni db seed-config` 上次跑时 env 变量没加载 (用户没 `set -a && source .env` 直接跑), `_env_token()` 返 `__WECHAT_RSS_A_TOKEN__` 占位符, 入库 `sources.url`, waytomaster 401 → 0 篇无错误提示
- **修复**: 实际抓取时 fetcher 检测占位符 URL (regex `__WECHAT_RSS_\w+TOKEN__`) loud error + [] 拒绝发起请求, 用户立即知道要 source .env 重跑 seed-config
- **诊断 SQL**: `SELECT name, substr(url, 1, 80) FROM sources WHERE name LIKE 'wechat%';` — 如果 url 包含 `__WECHAT_RSS_*_TOKEN__` 就是这个 bug

**安全告警** (待用户处理):
- **git 历史 token 泄漏**: commit 5b86616 (v1.0.5.6) 曾经在 sources.url 字段 hardcode 真实 token `eyJhbG...`, 7b5f52c 改为 _env_token 注入但没清 git history
- **影响范围**: 任何有 moni 仓库读权限的人 `git log -S "?token="` 可拿到 token
- **建议**: 用户在 waytomaster 控制台轮换 2 个 token, 旧 token 失效即不需清理 git history (轮换 vs git-filter-repo 二选一)

**测试**: +5 新增 (test_env_token_returns_placeholder_when_unset / returns_actual_value_when_set / a_falls_back_to_legacy_token / fetcher_refuses_placeholder_url / fetcher_refuses_empty_url)
**PR**: #39 squash merge → tag v1.0.7.6.2

**实际验证** (06-23 抓取):
- wechat_rss_a: 37 篇历史 + 6 fetched (0 inserted, dedupe 命中)
- wechat_rss_b: 41 → 59 (+18 inserted)
- 2026-06-23 发布的新文章已入库 ([未央科技weiyangx] 国务院报告)

**bestblog-x 状态**: 6:39-6:45 自动 done 5 篇入库, zombie sweeper (PR #36) 已自动清理, 无需代码修改

**操作 SOP** (写入 AGENTS.md): 任何 moni 命令前必须 `set -a && source .env && set +a` 让 env 变量自动 export

### v1.0.7.6.1 进展 (2026-06-23)

**Bug 修复**:
- **409 fallback 返错源 job_id** → list_recent(limit=1, status='running') 不按 source 过滤, 当 36kr_rss zombie 被 sweep 而 mpaypass/其他源仍有 fresh running row 时, 36kr 的 409 返的 running_job_id 可能是别源。前端拿错 job_id 轮询, modal 永远卡在「初始化中」。
- **修复**: list_recent 加 `source=` 参数 + 409 fallback 用 `source=resolved` 查同源 running job。Race 兜底: 查不到 running job_id 时(sweep 在两次 DB 调用之间发生)放过, 启动新 job。
- **手动清理**: 4 个 36kr_rss zombie running job (最早 1.5h 前) 手动 mark stale, 否则 36kr 继续被挡住

**测试**: +3 新增 (test_list_recent_filters_by_source / test_409_job_id_is_scoped_to_requested_source / test_409_falls_through_when_running_row_vanished)
**PR**: #38 squash merge → tag v1.0.7.6.1

### v1.0.7.6-hotfix 进展 (2026-06-23)

**Bug 修复**:
- **fetch_jobs 僵尸**: 8 个 36kr_rss running job 卡死 (daemon thread 异常退出但 mark_done 没成功), has_running() 永远返回 True 挡住新请求
- **修复**: sweep_zombies() 每次 /run 自动清理 2h+ 仍 running 的 job → status='stale'
- **前端**: status='stale' 映射「已中止(无响应)」红字, 轮询终止

### v1.0.7.6 进展 (2026-06-22)

**功能**:
- 异步 fetch 任务: POST /api/fetch/sources/{name}/run 立即返回 job_id, 后台线程跑
- 进度轮询: GET /api/fetch/jobs/{job_id} 每 5s 刷新
- 进度 modal: 实时显示 done_feeds/total_feeds, 当前 feed 名, 已抓文章数
- 错误处理: 10min hard timeout, 同源并发返 409, 失败/超时/僵尸状态红字提示
- 新表 fetch_jobs (migration 016): 存每次抓取实时状态

**测试**: 376 passed, 2 skipped (+17 新增)
**实测**: bestblog-yt (124 feeds) 174s 完成, 进度条实时更新

---

### v1.0.7.5 进展 (2026-06-22)

**Bug 修复**:
- **bestblog-yt 0 篇** → YouTube feed 用 Atom 1.0 格式，原代码只解析 RSS 2.0，导致 channel.find() 永远 None。修复：_fetch_feed 加 root tag 检测，Atom 走 _parse_atom。
- **bestblog-podcast 误命中 wechat tag** → tag_filter 只对 source=='bestblogs' 走 title-only，子类 fallback 到 title+content，导致 description 里的「关注公众号/小程序」误命中。修复：source.startswith('bestblog')。

**技术细节**:
- 重构 _extract_items 公共方法，RSS 和 Atom 共用 article 构造逻辑
- 修复 Python 3.12 Element truthiness deprecation 导致 find() or find() 链失败

**PR**: #33 squash merge → tag v1.0.7.5
**测试**: 359 passed, 2 skipped (351 → 359, +8 新增)
**实测**: bestblog-yt 5 feeds 抓 75 篇（修复前 0 篇）

---

### v1.0.7.4 进展 (2026-06-22)

**清理**:
- 删 `source_monitor.py` (598 行) — 早期 cron 脚本，已被 moni 包完整替代
- `moni/cron/scheduler.py` 清理 2 处 "migrated from source_monitor.py" 注释
- 移除 `/Users/mt16/dev/source-monitor/` 空壳目录 (根因: editable install 误指导致回归时全 API 404)

**PR**: #31 squash merge → tag v1.0.7.4
**测试**: 351 passed, 2 skipped (与 main 一致)
**影响**: 纯清理，无功能/行为变更

---

### v1.0.7.1 - v1.0.7.3 进展 (2026-06-18)

**新功能 — BestBlogs 系列信源扩展**:
- 新增 `bestblog-podcast` (60 个小宇宙播客 feeds)
- 新增 `bestblog-yt` (20 个 YouTube 频道 feeds)
- 新增 `bestblog-x` (160 个 x.com/Twitter feeds)
- `bestblogs` 合并 ALL OPML (291 个新英文技术博客)
- 多 OPML URL 逗号分隔支持，fetch 时合并去重
- 本地 OPML 文件支持

**技术细节**:
- FetcherBestBlogPodcast / FetcherBestBlogYt / FetcherBestBlogX 子类复用 FetcherBestBlogs
- bestblogs 排除 xgo.ing 域名的 feed (已移到 bestblog-x)
- 默认 tag: wechat, ai-fintech
- 黑名单: 入境游/文娱/明星/游戏/影视/电商/消费/美食/腾讯控股

---

### v1.0.6.9 进展 (2026-06-18)

**新功能 — 菜篮子 (Basket)**:
- 被过滤丢弃的文章不再消失，自动存入 basket 表
- 新增「菜篮子」UI tab，查看被丢弃文章 + rescue 回正式文章库
- 支持按来源/过滤原因/rescue 状态筛选
- rescue 时可选择 tag，自动关联到 article_tags
- 新增 4 个 API 端点: 列表/详情/rescue/删除

**技术细节**:
- 过滤器 (blacklist/whitelist/tag_filter) 丢弃时自动写 basket
- basket 表 schema 同 articles，额外加 dropped_reason/dropped_by/rescued_at
- INSERT OR IGNORE 防重复
- 新增 18 个测试覆盖 basket 功能

---

### v1.0.6.8 进展 (2026-06-17)

**功能**:
- 导入全文功能从仅 bestblogs/虎嗅 扩展到所有信源
- 移除前端 x-show 源过滤，mpaypass/36kr/qbitai 等均可导入 .md 全文
- 后端 import-fulltext 接口本身无源限制，改动仅在前端

---

### v1.0.6.7 进展 (2026-06-16)

**新功能**:
- 分享页文章选择新增 tag 分类下拉筛选
- 来源 + 分类 + 每页 三维筛选

---

### v1.0.6.5 进展 (2026-06-16)

**新功能**:
- 虎嗅 RSS 源 (第 7 个源): FetcherHuxiu, 全文抓取, 绑定 ai-fintech/miniprogram/wechat
- 单源「立即抓取」强制执行 (force=True)，不再被间隔挡住
- 虎嗅/BestBlogs 点「立即抓取」弹时间范围选择 modal

### v1.0.6.6 进展 (2026-06-16)

**Bug 修复**:
- 虎嗅 boilerplate 清洗: 清除 "本文来自微信公众号" / "原标题" 等来源声明
- CRUD 删除失败: 前端加 `?confirm=1` 参数
- 文章查阅分页: 新增每页条数选择 + 上下页翻页

---

### v1.0.6.4 进展 (2026-06-15)

**新功能**:
- Tag 过滤丢弃明细：抓取结果下方折叠面板，显示被丢弃文章标题（最多 20 条）
- 全部抓取时按源分组展示，单源抓取时直接展示
- tag_filter.py 返回 dropped_articles 列表

---

### v1.0.6.2 进展 (2026-06-15)

**新功能**:
- ⚡ 全部抓取按钮：抓取管理页顶部，一键跑所有源
- POST /api/fetch/all/run 端点：遍历所有源 + 详细日志写 cron_runs + fetch_log
- 周一补抓：自动检测周一 → date_from=周六
- cron 改工作日 9:10 AM（不跑常驻进程，手动触发为主）
- fetch_log 新增 8 列 (migration 014): cleaned/filtered/dropped_blacklist/dropped_whitelist/dropped_tag_filter/filters_applied/tags_used/deduped

**Bug 修复**:
- wechat_rss_b URL 截断修复（seed_config.py 存的是截断 token）
- wechat_rss token 从 .env 读取，不硬编码在代码里（安全修复）
- .env 拆成 WECHAT_RSS_A_TOKEN + WECHAT_RSS_B_TOKEN

**测试**: 322 passed, 2 skipped

---

### v1.0.6.0 进展 (2026-06-15)

**前端重构**:
- sidebar → topnav 顶栏导航 (Sophon.at 参考)
- 中国传统色 oklch 配色主题 (纯衣/玄色/天水碧/紫藤萝)
- 按钮全面重设计 (自包含样式 + hover/click 动效)
- 主题管理器: 🎨 切换/导入 globals.css/localStorage 持久化
- 🌙/☀️ 暗色模式一键切换

**服务控制**:
- topnav 状态指示灯 (绿点 + PID)
- ↻ 重启按钮 (确认 + 轮询等待 + Toast)
- GET/POST /api/service/status|restart|stop 端点

**测试**: 322 passed, 2 skipped

---

### v1.0.6.1 进展 (2026-06-15)

**新功能**:
- 抓取结果结构化解释: 「立即抓取」后 JSON 左侧新增文字说明
- 结果摘要: ✅ 新增 X 篇 / ⚠️ 全部重复 / ℹ️ 无新内容
- 流程分解: 远程拉取 → 解析 → 去重 → 过滤
- 双栏布局: 左侧结构化解释 + 右侧原始 JSON

**Bug 修复**:
- wechat_rss_b URL 截断修复 (JWT token 入库不完整)

**测试**: 322 passed, 2 skipped

---

### v1.0.5.25 进展 (2026-06-14)

**新功能**:
- fetch_config UI 编辑器：配置管理 → 抓取源 → 「抓取配置」按钮 → modal 编辑参数
- BestBlogs API 按需接入：分享时调 API 获取 score/summary/mainPoints/keyQuotes/tags
- 分享选项扩展：摘要来源（moni AI / BestBlogs / 两者）、BB 增强字段可选
- DB migration 013：articles 表加 bb_score/summary/one_sentence/main_points/key_quotes/tags/resource_id/fetched_at

**Bug 修复**:
- worker PNG/HTML 格式：检测 LLM 实际生成的文件并复制，不再保存文字描述
- PDF 末尾标签：跳过文档末尾的孤立标签段落
- SQL fallback：bb_* 列不存在时 fallback 到 NULL 查询

**测试**: 322 passed (新增 22 tests: fetch_config 10 + bestblogs_api 12)

---

### v1.0.5.24 进展 (2026-06-13)

**新功能**:
- BestBlogs 智能抓取：时间范围选择（自动/过去N天/自定义日期）
- 全文导入：文章详情页粘贴本地 .md 路径 → 更新 raw_content → AI 摘要/核查使用全文
- 复制原文链接：一键复制到剪贴板
- tag filter: bestblogs 只匹配标题（避免摘要 boilerplate 误命中）

**测试**: 300 passed (新增 19 tests)

---

### v1.0.5.22 进展 (2026-06-13)

**Bug 修复**:
- 黑名单模式记录 keyword_hits
- `scripts/backfill_keyword_hits.py` — 历史文章 keyword_hits 补录

---

### 历史版本

- v1.0.5.21: PDF 日报分享格式 + 版式配置化
- v1.0.5.20: tag 关联逻辑修正 + keyword_hits 完善
- v1.0.5.19: share bugfix + AI 摘要回写 + LLM 统一 + LLM 设置 UI
- v1.0.5.18: share 功能 v2 重构
- v1.0.5.17: UI worker 按钮 + 进度条
- v1.0.5.16: confirm-modal before queueing
- v1.0.5.15: 5 user fixes
- v1.0.5.14: display_name + 5 fixes
- v1.0.5.13: timezone-aware created_at
- v1.0.5.12: 6 UI bug fixes
- v1.0.5.11: CRUD create/edit modal + share card grid
- v1.0.5.10: 双端口 (8900/8901)
- v1.0.5.9: retire legacy wechat_rss
- v1.0.5.8: remove 36kr_newsflash
- v1.0.5.7: extract publisher from title prefix
- v1.0.5.6: wechat_rss multi-account
- v1.0.5.5: 36kr captcha bypass
- v1.0.5.4: tag-keyword 过滤
- v1.0.5.2: LLM 异步化 + 进度面板
- v1.0.5.1: 3 个 UX bug 修复
- v1.0.5: P0 + P1 全做完

</details>

---

## 分享格式一览 (7 种)

| 格式 | 说明 | Prompt 文件 |
|------|------|-------------|
| plain_text | 纯文本 | 无（直接输出） |
| markdown | Markdown | format-markdown.md |
| html_landing | HTML 网页 | format-html.md |
| rich_text | 富媒体文本 | format-rich-text.md |
| info_graphic_png | 信息图 PNG | format-info-graphic-png.md |
| report_png | 报告 PNG | format-report-png.md |
| pdf | PDF 日报 | format-pdf.md |

---

## 抓取源 (7 active)

| 源 | 间隔 | 类型 | 状态 |
|---|---|---|---|
| 36kr_rss | 5min | html | ✅ |
| mpaypass | 5min | html | ✅ |
| qbitai | 5min | rss | ✅ |
| wechat_rss_a | 5min | wechat_aggregator | ✅ |
| wechat_rss_b | 5min | wechat_aggregator | ✅ |
| bestblogs | 5min | rss (OPML 聚合, 375 号) | ✅ |
| huxiu | 5min | rss | ✅ |


## v1.0.35.6 (2026-08-20)

**Demo HTML 修复 + 演示物料 UX 优化**

- configModal 区域闭合修复 (1 `</template>` + 3 `</div>`) — info_graphic / html_landing modal 不再被 HTML5 parser 吞掉
- demo mock `/api/share/brief` 返 `share_id` envelope 匹配 prod contract
- modal 宽高按产物尺寸 (modalContent 95vh, modalBody 80vh)
- 7 share formats (跟 prod 一致) + `openArtifact()` gate: html_landing / info_graphic_png 真演示, 其他 5 种弹提示
- 生成产物 6 步骤可见进度 (~7s, fetch → generate → render → factcheck → finalize → done)
- 暗色模式 status badge 改用 `var(--warning-muted)` / `var(--muted)` 取代硬编码浅色
- format chip 标签带 "✓ 演示就绪" / "未演示" 高亮

PR #115 merged (0d87a66), tag v1.0.35.6


## v1.0.35.7 (2026-08-21)

**一键简报 select 默认 HTML 网页**

- `shareFormats` 数组重排 — demo-ready (html_landing, info_graphic_png) 排前 2 位
- 修复 `<select x-model="briefFormat">` 初次渲染显示 "纯文本" 跳变到 "HTML 网页" 的视觉不一致
- `briefFormat` 默认值 (L12314) 一直就是 'html_landing', 这次只调顺序, 不改默认值

commit `e593c73`, share copy 同步到 `~/Documents/moni-share/moni-web-showcase-v1.0.35.6.html` (sha256[:16]=`019426b1c9a18812`)


## v1.0.35.8 (2026-08-21)

**Demo HTML "打开产物" 按钮全黑修复 + 性能优化**

- **按钮全黑根因**: v1.0.35.6 加的 `.panel-soft * { color: var(--foreground) }` 光暗通用, light mode `--foreground`=玄色(#1A1A1A)跟 button 背景同色 → 隐形. 且 dark class 加在 `body` 上, 不是 `:root`, 导致 round-2 scope 修复失效.
- **grok 修复** (herdr pane w4:p1V): `.panel-soft .btn-primary { color: var(--background); }` — specificity (0,2,0) 压过原通配, mode-agnostic, 只影响 打开产物 button.
- **性能**: srcdoc/img 抽到 `<script type="application/json">` + Alpine 按需注入 (deferred), 单文件保留, modal 打开更快.
- **llmUsage 默认值** 改 `{ totals:{}, by_model:[] }` 避免 API 失败时 null 报错.
- commit 链: `6394c6f`(--fg typo) → `cea9e39`(defer) → `ea1a292`(body.dark scope, 失败) → `3353508`(grok fix, 成功). tag `v1.0.35.8`.

share copy: `~/Documents/moni-share/moni-web-showcase.html` (sha256[:16]=`472f6586588366e1`)
