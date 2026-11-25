# sprint-69 — 脱敏实验室（四栏演练台）验收记录

- **日期**：2026-11-25
- **分支**：`feat/desens-lab`（基于 `main` @ `687f98b`，PR #294 合并后的 main）
- **规格**：`docs/plans/2026-11-25_llm-desens-lab-finals.md`（决赛物料与演示脚本）
- **交付**：后端 `moni/web/routes/desensitize.py`（3 个新端点）+ 页面 `moni/web/static/desens-lab.html`
  + 内核两处向后兼容改动（`audit.py` 的 `force` / `extra_terms`，`runner.py` 的 `desens_ctx`）
  + 测试 `tests/test_desensitize_lab_api.py`（8 例）

---

## 1. 命令与输出

### 1.1 专项测试

```bash
/usr/local/bin/python3 -m pytest tests/test_desensitize_lab_api.py -p no:cacheprovider -q
```
→ `16 passed in 5.06s`

用例演进：初版 8 例 → 合并前审计后补 7 例（blocked 拦截 ×2、422 校验 ×4、模型失败 ×1）
→ 二轮复核后补 1 例（fail-closed：遮蔽器抛异常 → typed 400 且不出网），
并把「模型失败不写账本」改成「模型失败也写一行 `fallback_used='model_error'`」。

### 1.2 脱敏相关全套

```bash
/usr/local/bin/python3 -m pytest tests/test_llm_channels_smoke.py tests/test_llm_desens_wiring.py \
  tests/test_llm_desens_wiring_share.py tests/test_desensitize_core.py \
  tests/test_migration_058_llm_desensitize.py -p no:cacheprovider -q
```
→ `96 passed`（`--collect-only` 复核：96 例）

### 1.3 全量

```bash
/usr/local/bin/python3 -m pytest tests/ -p no:cacheprovider -q
```
→ `2788 passed, 2 skipped, 2 warnings in 465.09s (0:07:45)`（最终轮，0 failed）

基线 `origin/main` @ `687f98b` 为 `2772 passed, 2 skipped` → 本轮 **+15**（演练台 API 测试）。

> 中间一轮曾出现唯一 failed：`tests/test_worker_lock.py::test_sigterm_kills_blocking_child_that_ignores_term`
> —— 与本轮改动**无交集**（worker lock / SIGTERM 子进程 vs 脱敏实验室），且**单独复跑通过**：

```bash
/usr/local/bin/python3 -m pytest tests/test_worker_lock.py -p no:cacheprovider -q
```
→ `28 passed in 12.39s`

判定：机器负载下的时序 flaky（该用例断言 SIGTERM 后子进程被回收的时限），非本次回归。
最终轮全量该用例通过，**0 failed**。

### 1.4 真实模型端到端（Playwright 实测，非 mock）

页面：`http://127.0.0.1:8901/static/desens-lab.html`（dev 端口 8901，隔离库
`/private/tmp/desens-lab-dev/data/news.db`）

```
terms(prefill): '招商银行, 兴业银行'
preview c2 含占位符: True | 含机构名明文: False
run status: 完成 · 策略 standard | err: （空）
c3 含占位符: True | c3 含明文号码: False
c4 含明文号码: True
metrics: 遮蔽处数 5 还原处数 5 未还原 0 出站泄漏命中 0 残留占位符 0 遮蔽耗时 136 µs 还原耗时 98 µs 模型耗时 8775 ms
console errors: []
```

账本渲染实测（页面上原样可见）：

```
口径	调用	遮蔽	还原	未还原	泄漏命中	残留占位符	拦截	还原成功率
生产（不含实验室）	0	0	0	0	0	0	0	—
实验室	4	17	15	0	0	0	0	100.0%
call_site	task_type	遮蔽	还原	未还原	泄漏	结果	时间
desens_lab	standard	5	5	0	0	已出网	2026-11-25 01:18:04
```

四栏实际内容（真实模型回包）：

| 栏 | 内容 |
|---|---|
| ① 原文 | `招商银行与兴业银行同日发布公告：招商银行客服电话 13800138000，联系人邮箱 analyst@bankcorp.cn。请只复述上述联系方式，不要展开分析。` |
| ② 发给模型的内容 | `[系统脱敏说明：形如 {{EXAMPLE_00000000}} …]\n\n{{ORG_jcwwdnrn}}与{{ORG_lq4cr2iu}}同日发布公告：{{ORG_jcwwdnrn}}客服电话 {{PHONE_pbn2bfug}}，联系人邮箱 {{EMAIL_colbtckw}}。…` |
| ③ 模型回包（原始） | `{{ORG_jcwwdnrn}}客服电话 {{PHONE_pbn2bfug}}，联系人邮箱 {{EMAIL_colbtckw}}。其余内容按你的要求不展开。` |
| ④ 本地还原后 | `招商银行客服电话 13800138000，联系人邮箱 analyst@bankcorp.cn。其余内容按你的要求不展开。` |

> 「还原处数」可能小于「遮蔽处数」，这是**正常的**：模型只复述了它认为要复述的联系方式，
> 没被复述的编码不产生还原动作 —— 这正是「未还原 = 0」的意义（模型没说的，不算漏还原）。
> 实测两轮：一轮还原 3 处（模型只复述联系方式），一轮还原 5 处（模型多复述一句），
> 两轮「未还原 / 出站泄漏命中 / 残留占位符」都是 0。

---

## 2. UI 实测（Playwright / Chromium，真实渲染）

### 2.1 响应式（先测量再截图，避免 full_page 截图撑大视口污染测量）

| 视口宽 | 四栏元素宽度 | 实际列数 | 横向溢出 |
|---|---|---|---|
| 1600 | 379 × 4 | 4 列 | 否 |
| 1280 | 299 × 4 | 4 列 | 否 |
| 1100 | 520 | 2 列 | 否 |
| 820 | 772 | 1 列 | 否 |
| 760 | 712 | 1 列 | 否 |
| 480 | 432 | 1 列 | 否 |

判据：`document.documentElement.scrollWidth <= window.innerWidth`（每个断点均为 `overflow=false`）。

复测（审计修复后，2026-11-25 第二轮）：1600 → 4×379、1280 → 4×299、1100 → 2×520、
820 → 1×772、760 → 1×712、480 → 1×432，**六个断点 `overflow=false`**。

### 2.2 截图（本地，不进仓库）

`/private/tmp/desens-lab-dev/03-run-1600.png`（真跑一次后的四栏 + 指标 + 账本）、
`resp-1600/1280/1100/760/480.png`（各断点）。

### 2.3 交互路径亲手点过

`示例` → `只预览` → `真跑一次` → 账本自动刷新，全部在浏览器里点到；
`console` 无 error、`pageerror` 无异常。

---

## 3. 可复算数字

```sql
-- 生产口径（排除实验室调用）
SELECT count(*) AS calls, sum(masked_total) AS masked_total,
       sum(restored_total) AS restored_total, sum(unrestored_count) AS unrestored_count,
       sum(leak_scan_hits) AS leak_scan_hits, sum(residual_placeholders) AS residual_placeholders
FROM desensitize_events WHERE call_site != 'desens_lab';

-- 实验室单列
SELECT count(*) AS calls, sum(masked_total) AS masked_total,
       sum(restored_total) AS restored_total, sum(leak_scan_hits) AS leak_scan_hits
FROM desensitize_events WHERE call_site = 'desens_lab';
```

页面底部直接展示这两条口径的结果与 SQL 原文（现场可当场复算）。

---

## 4. failed / skipped

### failed（已修）

| # | 问题 | 发现方式 | 处置 |
|---|---|---|---|
| F1 | 临时敏感词输入框**初始为空** —— 预填代码被写进了「示例」按钮的回调里，页面加载时不执行 | Playwright 实测 `terms(prefill): ''` | 预填移到初始化处，`DEFAULT_TERMS` 常量 |
| F2 | 首版示例文本让模型展开分析，**回包耗时 55 秒**，现场演示等太久 | 首次实测「模型耗时 54892 ms」 | 示例改成「只复述联系方式，不要展开分析」→ 实测 11.3 秒 |
| F3 | 响应式断点 1180px 与实际栅格不符，且 `main` 栅格未设 `minmax(0,1fr)`，窄视口下内容可撑破 | 断点实测 + 截图 | `main` 改 `grid-template-columns: minmax(0,1fr)`，`.cols`/`.col` 补 `min-width: 0`，断点改 1200 / 820 |
| F4 | **`/run` 忽略 `mask_result.blocked`** —— 内核判定「遮不干净」（规则 `action=block` 或出站泄漏扫描命中）时，实验室仍会把文本发给真实模型 | 合并前独立子 agent 审计（P1） | 出网前拦截：记 `blocked=1` 账本 + 400「已拦截，未出网」；补 2 例测试 |
| F5 | `/run` 还原裸调 `restore_text`，异常会 500 且**丢审计行**（模型已调用、钱已花、账本里查不到） | 同上（P2） | 改用 `finalize_inbound`（入站 fail-open），还原失败仍记账 |
| F6 | `force=True` 只绕过总开关，**不绕过 fail-open** —— 遮蔽器异常时会把未遮蔽原文发出去，页面也看不出来 | 同上（P2） | `force=True` 时强制 fail-closed：遮蔽失败直接报错，绝不出网 |
| F7 | `extra_terms` 只限条数，单条无长度上限（可塞超长串进字典正则） | 同上（P2） | 单条 ≤64 字符校验，对齐 `rules.py` 的 `MAX_CUSTOM_PREFIX_LEN` |
| F8 | 账本最近记录用 `innerHTML` 拼服务端字段；空账本时 `restore_success_rate` 返回 1.0（冒充 100%） | 同上（NIT ×2） | 改 DOM API + `textContent`；无样本时返回 `None`，页面显示 `—` |
| F9 | 示例与测试用了真实格式手机号 `13812345678` | 同上（NIT） | 换成 `13800138000`（测试号段） |
| F10 | 模型调用失败时**已出网但账本 0 行** —— 调用次数系统性少记 | 二轮复核（P2） | 失败也记账：`ctx.fallback_override='model_error'` + `record_event` |
| F11 | `preview` 返回了 `blocked` 但页面不读，被拦截文本预览显示 200 | 二轮复核（P3） | 页面读 `blocked`：红色提示 + 状态行「已拦截（未出网）」 |
| F12 | 被拦截的行在账本里与真调用行长得一样，且被算进 `calls` | 二轮复核（P3） | 聚合加 `blocked_calls` / `outbound_calls`；页面加「拦截」列与「结果」列 |
| F13 | fail-closed 报错是裸 500，现场无法解释原因 | 二轮复核（P3） | 转 typed 400：「脱敏内核异常，已 fail-closed 拦截（未出网）」 |
| F14 | 响应里的 `fallback_used` 页面不展示 | 二轮复核（NIT） | 状态行追加「降级：xxx」 |

### skipped

| # | 项 | 原因 |
|---|---|---|
| S1 | 脱敏**开关面板**（用户自助配规则 CRUD） | 本轮范围只做演练台；面板未做，PPT 不提 |
| S2 | 生产环境（8900）上的演练台 | 本轮 PR 尚未合并；生产库 `desensitize.enabled` 仍为 `0` |
| S3 | 触屏适配 | 页面为桌面演示场景，未做触屏专项 |
| S4 | 主界面入口链接 | 决赛脚本直开 `/static/desens-lab.html`，本期不加主界面入口（避免扩围改主界面） |

---

## 5. 全量回归

| 口径 | 结果 |
|---|---|
| 基线 `origin/main` @ `687f98b` | `2772 passed, 2 skipped` |
| 本分支（演练台 + 两轮审计修复） | `2788 passed, 2 skipped, 0 failed`（465.09s） |
| 增量 | +16（`tests/test_desensitize_lab_api.py` 16 例） |
| 脱敏专项 5 文件 | `96 passed` |

中间轮 failed（已判定为时序 flaky，最终轮未复现）：
`test_worker_lock.py::test_sigterm_kills_blocking_child_that_ignores_term`。

两轮审计修复后复跑：演练台专项 `16 passed`；脱敏专项 5 文件 `96 passed`。

## 6. 证据位置

| 证据 | 路径 |
|---|---|
| 本验收记录 | `docs/sprint-evidence/sprint-69/VERIFY.md` |
| 决赛物料与分镜脚本 | `docs/plans/2026-11-25_llm-desens-lab-finals.md` |
| 页面截图（本地，不入库） | `/private/tmp/desens-lab-dev/03-run-1600.png`、`resp-*.png` |
| Obsidian sprint 记录 | `~/Library/Mobile Documents/iCloud~md~obsidian/.../project-moni/sprints/sprint-69-desens-lab-2026-11-25/` |
