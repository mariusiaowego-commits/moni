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
→ `8 passed in 3.13s`

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
→ `2780 passed, 2 skipped, 2 warnings in 455.04s (0:07:35)`

基线 `origin/main` @ `687f98b` 为 `2772 passed, 2 skipped` → 本轮 **+8**（新增 API 测试），
**0 failed**。

### 1.4 真实模型端到端（Playwright 实测，非 mock）

页面：`http://127.0.0.1:8901/static/desens-lab.html`（dev 端口 8901，隔离库
`/private/tmp/desens-lab-dev/data/news.db`）

```
terms(prefill): '招商银行, 兴业银行'
preview c2 含占位符: True | 含机构名明文: False
run status: 完成 · 策略 standard | err: （空）
c3 含占位符: True | c3 含明文号码: False
c4 含明文号码: True
metrics: 遮蔽处数 5 还原处数 5 未还原 0 出站泄漏命中 0 残留占位符 0 遮蔽耗时 128 µs 还原耗时 256 µs 模型耗时 11344 ms
console errors: []
```

四栏实际内容（真实模型回包）：

| 栏 | 内容 |
|---|---|
| ① 原文 | `招商银行与兴业银行同日发布公告：招商银行客服电话 13812345678，联系人邮箱 analyst@bankcorp.cn。请只复述上述联系方式，不要展开分析。` |
| ② 发给模型的内容 | `[系统脱敏说明：形如 {{EXAMPLE_00000000}} …]\n\n{{ORG_jcwwdnrn}}与{{ORG_lq4cr2iu}}同日发布公告：{{ORG_jcwwdnrn}}客服电话 {{PHONE_n6rsxsij}}，联系人邮箱 {{EMAIL_colbtckw}}。…` |
| ③ 模型回包（原始） | `{{ORG_jcwwdnrn}}客服电话 {{PHONE_n6rsxsij}}，联系人邮箱 {{EMAIL_colbtckw}}。补充一句：{{ORG_jcwwdnrn}} 与 {{ORG_lq4cr2iu}} 是同日发布的公告。` |
| ④ 本地还原后 | `招商银行客服电话 13812345678，联系人邮箱 analyst@bankcorp.cn。补充一句：招商银行 与 兴业银行 是同日发布的公告。` |

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

### skipped

| # | 项 | 原因 |
|---|---|---|
| S1 | 脱敏**开关面板**（用户自助配规则 CRUD） | 本轮范围只做演练台；面板未做，PPT 不提 |
| S2 | 生产环境（8900）上的演练台 | 生产代码尚未合并本轮改动；生产库 `desensitize.enabled` 仍为 `0` |
| S3 | 触屏适配 | 页面为桌面演示场景，未做触屏专项 |

---

## 5. 全量回归

| 口径 | 结果 |
|---|---|
| 基线 `origin/main` @ `687f98b` | `2772 passed, 2 skipped` |
| 本分支（演练台） | `2780 passed, 2 skipped, 0 failed`（455.04s） |
| 增量 | +8（`tests/test_desensitize_lab_api.py` 8 例） |
| 脱敏专项 5 文件 | `96 passed` |

## 6. 证据位置

| 证据 | 路径 |
|---|---|
| 本验收记录 | `docs/sprint-evidence/sprint-69/VERIFY.md` |
| 决赛物料与分镜脚本 | `docs/plans/2026-11-25_llm-desens-lab-finals.md` |
| 页面截图（本地，不入库） | `/private/tmp/desens-lab-dev/03-run-1600.png`、`resp-*.png` |
| Obsidian sprint 记录 | `~/Library/Mobile Documents/iCloud~md~obsidian/.../project-moni/sprints/sprint-69-desens-lab-2026-11-25/` |
