# Sprint 79 验收记录 — 脱敏入口归位配置管理 + 配置区可读写

- 日期：2026-11-25
- 分支：`feature/v1.0.134-desens-config-ui`（base `88a4aa7`，并入 main `b739650` / #322）
- 改动范围：`moni/llm/desensitize/audit.py`、`moni/web/routes/desensitize.py`、`moni/web/static/**`、新增测试
- 无迁移、无 schema 变更、不改既有端点契约

## 0. 审计发现与修正（独立只读审计，结论 NEEDS FIX）

| 级别 | 问题 | 修正 |
|---|---|---|
| P1 | 「脱敏」面板被插进 `currentView === 'fetch'` 的 section：配置页里是空白，切到信源管理还会漏出 | 面板移进配置 section；复验加了「可见性」断言（原先只查存在，是漏检根源） |
| P2 | `invalidate_settings_cache()` 只清本进程；share worker / 定时任务各持一份 30 秒缓存 | UI 文案写明「本进程立即生效，其它进程最多 30 秒内跟上」；缓存加 `_SETTINGS_EPOCH`，旧快照不再写回 |
| P2 | 写入模型未禁未知字段：`PUT {"enable": true}` 返回 200 但配置没变 | `extra="forbid"` + `StrictBool`，打错字段名/传字符串数字一律 422 |
| P2 | 未迁移库上 `GET`/`PUT` 500；且 PUT 可能「已写入却返回 500」 | 缺表返回 400；规则表查询包 try，规则表缺失只影响规则列表 |
| P3 | 布尔非严格：`"false"` / `1` / `"yes"` 会被当成布尔 | 改 `StrictBool`，测试补 6 个反例 |
| P3 | 字段→键用位置 zip，键顺序一变就静默写错 | 保持单一来源 + 新测试：五个键写不同值后逐个断言（含 L1/L2 互换会被抓出） |
| P3 | 缓存竞态：读线程可能把旧快照写回缓存 | `_SETTINGS_EPOCH` 版本号，读期间有写则不落缓存 |
| P3 | 规则表无横向滚动容器；`.config-tabs` 第 8 个页签在窄屏撑破 | `#cfgRules` 加 `overflow-x:auto`；`.config-tabs` 改 `flex-wrap: wrap` |
| P3 | `init()` 无条件拉配置；`saveConfig` 无请求序号 | 改成配置子页懒加载；写请求加序号，乱序响应丢弃 |

## 0.1 复审发现与修正（第二轮审计，结论 APPROVE WITH NITS → 6 条 P3 全修）

| 级别 | 问题 | 修正 |
|---|---|---|
| F1 | 字段→键是位置 zip，且读写三处真源；测试五个值只有两组，键互换仍有 12 种置换能过 | 新增 `SETTING_KEYS: dict[str, str]` 单一真源，读写都走它；新测试「一次只写一个字段，逐个断言只有那一个键变了」（5 个参数化用例） |
| F2 | 缺列未覆盖：`app_settings` 少 `updated_at` → 500；`desensitize_rules` 少列 → 规则查询异常把脱敏整体 fail-open 成「关闭」（安全方向偏差） | 路由层用 `PRAGMA table_info` 校验列，缺表/缺列一律 400 并说明原因；`load_settings` 把规则查询单独兜底，规则读不出来只丢用户规则，**不关脱敏开关** |
| F3 | 页头 pill 只靠 `/stats`，`/stats` 挂时按钮显示「已开启」但 pill 还是「总开关 …」 | pill 改由配置结果驱动（`renderPill`），不再等 `/stats` |
| F4 | `loadConfig` 吞掉 400，页面只显示「…」，「请先跑数据库迁移」没上屏 | 读失败把接口 `message` 写进 `cfgMsg`；保存失败同样显示服务端原因 |
| F5 | 写请求乱序只挡了 UI 覆盖，服务端处理顺序仍可能与显示不一致 | 保存期间禁用开关与勾选控件 |
| F6 | 30 秒滞后提示只写在「已开启」分支 | 「已关闭」分支也写明 worker / 定时任务最多 30 秒内跟上 |

## 1. 命令与输出

```bash
/usr/local/bin/python3 -m pytest tests/ -p no:cacheprovider -q
# => 2980 passed, 2 skipped, 2 warnings in 509.13s (0:08:29)

/usr/local/bin/python3 -m pytest tests/test_desensitize_config_api.py \
  tests/test_desensitize_lab_api.py -p no:cacheprovider -q
# => 36 passed in 11.91s（新增 20 个接口用例 + 既有 16 个实验室用例）

# 上轮 sprint-78 记为 failed/error 的两个顺序污染项，本轮全量即通过，单独复跑也通过
/usr/local/bin/python3 -m pytest tests/test_migration_039_xbot_feed.py \
  "tests/test_xwatch_syndication.py::TestFetchTweet::test_empty_id_returns_none_without_calling_network" \
  -p no:cacheprovider -q
# => 34 passed in 4.34s
```

**failed / error**：本轮 `0 failed / 0 error`。sprint-78 那两个 failed/error 是全量顺序污染
（`conftest.py:95` 临时目录 WAL 残留），本轮全量即通过。

## 2. UI 实测（Playwright 真实 Chromium，8901 独立实例 + 独立测试库）

| 项 | 实测 |
|---|---|
| 顶栏 | 11 条链接，**无**「信息脱敏」 |
| 配置管理子页 | 8 个页签，含「脱敏」；面板**可见**，内容：状态徽章「关」、级别 `L1✓ L2✓ L3✗`、入口 `/desens-lab.html` |
| P1 回归 | 切到「信源管理」页，脱敏面板 `is_visible = False`（不再漏出） |
| 脱敏页 | 标题「信息脱敏」；分段切换「配置 / 脱敏实验室」；默认显示配置区、实验室区隐藏 |
| 配置区 | 总开关「已关闭」；级别 `L1✓ L2✓ L3✗`、严格模式 ✗；规则表（灌 1 条测试规则）5 列正常渲染 |
| 点总开关 | 变「已开启」+「已保存，立即生效」；页头 pill 同步为「总开关 开（L1✓ L2✓ L3✗）」 |
| 切到实验室 | 配置区隐藏、实验室区显示；四栏 4 个 |
| 390px 视口 | 脱敏页 `scrollWidth - innerWidth = 0`；配置页 4px 来自 `.topnav-right`（**既有问题**，生产 8900 同样存在）；`.config-tabs` `scrollWidth == clientWidth`（页签已换行，不撑破） |
| console | 脱敏页 0 error；主站 1 条 `Transition was skipped` —— 生产 8900 首页同样有，属既有问题 |

## 3. 接口契约（新增，既有端点不动）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/desensitize/config` | 总开关、三级级别、严格模式、用户规则（只读）；缺表 → 400 |
| PUT | `/api/desensitize/config` | 写开关/级别；`null` 表示不改；严格布尔 + 禁未知字段 → 422 |

写入 `app_settings`，写完清进程内设置缓存：**本进程立即生效、不用重启、不用发版**；
share worker / 定时任务是独立进程，最多 30 秒内跟上（已在 UI 文案写明）。

## skipped

- 用户规则 CRUD：只读展示，排期见 `docs/plans/2026-11-25_desens-config-panel-plan.md`
- 生产 8900 未部署（本轮只改仓库）
- 总开关打开后的生产端到端验证（摘要 / 事实核查）未做：默认仍是关，等明确授权再开
- `.topnav-right` 在 390px 下的 4px 横向溢出：既有问题，不在本轮范围

## 4. pstack 合并前审计（interrogate：3 个独立 adversary + 逐条复跑）

范围 `git diff c3968a6...c8659c0`（8 文件 / +673）。只有能被命令复现的 finding 计数。

| # | 严重度 | finding | 复现命令 | 修复 |
|---|---|---|---|---|
| 1 | P2 | `/desens-lab.html` 无 `Cache-Control`，改版后旧页面可能被启发式缓存继续展示 | `curl -D - /desens-lab.html` 无 cache-control（`/` 有） | `server.py` 对 `.html` 统一 `no-cache` |
| 2 | P3 | 配置 tab 在 `GET /config` 400 时静默空白，无错误无重试 | 空库启动后 `curl` 400，页面 `desensitizeConfig` 停在 `null` | `app.js` 记录错误 + 面板显示错误与「重试」 |
| 3 | P3 | 保存失败后复选框不回滚，UI 与服务端不一致、重试语义反转 | PUT 注入 500 后 L3 仍为 `true` | `desens-lab.html` 失败时 `renderConfig(currentConfig)` 回滚 |
| 4 | P3 | `_schema_problem` 漏检 `desensitize_rules.id`，缺列时 200 + 规则静默丢弃 | `probe6.py`：缺 id 列 → 200 / `rules_count=0` / 机构名原文出站 | `_REQUIRED_COLUMNS` 补 `id`；现返回 400 `缺列（id）` |
| 5 | P3 | `test_rules_table_broken_does_not_disable_masking` 命中 30s 缓存，未覆盖兜底分支 | 删掉兜底代码后用例仍通过 | 用例加 `invalidate_settings_cache()`；变异后**失败** |
| 6 | P3 | 键映射用例与写入共用 `SETTING_KEYS`，L1↔L2 互换仍全绿 | 源级互换后 20 用例全过 | 新增字面量键名用例；互换后**失败** |

修复后复验：

```
$ uv run --quiet python -m pytest tests/test_desensitize_config_api.py \
    tests/test_desensitize_lab_api.py tests/test_web_static_headers.py \
    tests/test_web_csrf_guard.py -q -p no:cacheprovider
43 passed

$ uv run --quiet python -m pytest tests/ -q -p no:cacheprovider \
    -k "desens or web or static or csrf or share or reader or summary"
381 passed, 2604 deselected
```

Playwright 复验（8901）：400 时错误文案与「重试」可见；PUT 500 后 `#cfgL3` 回滚为 `false`、
控件恢复可用；正常路径开关「关→开→关」与页面 pill 同步，账本 9 列 `x` 偏差全 0，390px 无溢出。

## 5. 生产部署验收清单（8900，2026-11-26）

| # | 检查项 | 判据 | 结果 |
|---|---|---|---|
| 1 | 进程 | launchd `com.moni.web` 重启成功 | ✅ pid 28438 |
| 2 | 首页 | `GET /` → 200 | ✅ 200 |
| 3 | 配置接口 | `GET /api/desensitize/config` → 200 且 `enabled=false` | ✅ 200 / `false` |
| 4 | 默认级别 | `L1✓ L2✓ L3✗ strict✗` | ✅ 一致 |
| 5 | HTML 缓存 | `/`、`/desens-lab.html` 均 `Cache-Control: no-cache` | ✅ 两处均 no-cache |
| 6 | 顶栏 | 11 条链接，无「信息脱敏」 | ✅ 11 / 无 |
| 7 | 配置管理 | 8 个页签，含「脱敏」；面板可见 | ✅ 8 页签 / 可见 |
| 8 | 面板归位 | 切到「信源」页签时脱敏面板隐藏 | ✅ `is_visible=false` |
| 9 | 实验室分区 | 默认配置区可见、实验室区隐藏；互相切换 | ✅ |
| 10 | 总开关状态 | 页头 pill「总开关 关（L1✓ L2✓ L3✗）」 | ✅ |
| 11 | 账本对齐 | 表头与数据行逐列 `x` 偏差 | ✅ 表0 9/9、表1 8/8，偏差 0 |
| 12 | 窄屏 | 390px `scrollWidth - clientWidth` | ✅ 0 |
| 13 | 大模型通道 | 实验室「真跑一次」→ HTTP 200，手机号遮蔽、回包还原 | ✅ 200 / `{{PHONE_…}}` / 7.2s |
| 14 | 前端报错 | console `pageerror` | ⚠️ `Transition was skipped`，与改造前生产基线一致（既有问题） |

未跑项（skipped）：用户规则 CRUD（下一 sprint）；生产总开关打开态验证（保持默认关）。
