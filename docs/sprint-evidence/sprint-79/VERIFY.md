# Sprint 79 验收记录 — 脱敏入口归位配置管理 + 配置区可读写

- 日期：2026-11-25
- 分支：`feature/v1.0.134-desens-config-ui`（base `88a4aa7`）
- 改动范围：`moni/llm/desensitize/audit.py`、`moni/web/routes/desensitize.py`、`moni/web/static/**`、新增测试
- 无迁移、无 schema 变更、不改既有端点契约

## 1. 命令与输出

```bash
/usr/local/bin/python3 -m pytest tests/ -p no:cacheprovider -q
# => 2844 passed, 2 skipped, 2 warnings in 527.88s (0:08:47)

/usr/local/bin/python3 -m pytest tests/test_desensitize_config_api.py \
  tests/test_desensitize_lab_api.py -p no:cacheprovider -q
# => 21 passed in 8.53s（新增 5 个接口用例 + 既有 16 个实验室用例）

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
| 配置管理子页 | 8 个页签，含「脱敏」；状态徽章「关」；级别 `L1✓ L2✓ L3✗`；入口 `/desens-lab.html` |
| 脱敏页 | 标题「信息脱敏」；分段切换「配置 / 脱敏实验室」；默认显示配置区、实验室区隐藏 |
| 配置区 | 总开关「已关闭」；级别 `L1✓ L2✓ L3✗`、严格模式 ✗；规则区「暂无自定义规则。」 |
| 点总开关 | 变「已开启」+「已保存，立即生效」；页头 pill 同步为「总开关 开（L1✓ L2✓ L3✗）」 |
| 切到实验室 | 配置区隐藏、实验室区显示；四栏 4 个 |
| 390px 视口 | `scrollWidth - innerWidth = 0` |
| console | 脱敏页 0 error；主站 1 条 `Transition was skipped` —— 生产 8900 首页同样有，属既有问题 |

## 3. 接口契约（新增，既有端点不动）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/desensitize/config` | 总开关、三级级别、严格模式、用户规则（只读） |
| PUT | `/api/desensitize/config` | 写开关/级别；字段为 `null` 表示不改；非布尔 → 422 |

写入 `app_settings`，写完清进程内设置缓存（30 秒 TTL），**立即生效、不用重启、不用发版**。

## skipped

- 用户规则 CRUD：只读展示，排期见 `docs/plans/2026-11-25_desens-config-panel-plan.md`
- 生产 8900 未部署（本轮只改仓库）
- 总开关打开后的生产端到端验证（摘要 / 事实核查）未做：默认仍是关，等明确授权再开
