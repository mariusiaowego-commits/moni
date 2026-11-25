# Sprint 78 验收记录 — 信息脱敏入口 + 演练台主题化

- 日期：2026-11-25
- 分支：`feature/v1.0.132-desens-lab-entry`（base `7ea0235`）
- 改动范围：仅 `moni/web/static/**`（`git diff --name-only main | rg -v '^moni/web/static/'` = 空）

## 1. 命令与输出

```bash
# 全量（8901 已停，避免 WAL 干扰）
/usr/local/bin/python3 -m pytest tests/ -p no:cacheprovider -q
# => 1 failed, 2838 passed, 2 skipped, 2 warnings, 1 error in 466.70s

# 两个失败项按文件复跑
/usr/local/bin/python3 -m pytest tests/test_migration_039_xbot_feed.py \
  "tests/test_xwatch_syndication.py::TestFetchTweet::test_empty_id_returns_none_without_calling_network" \
  -p no:cacheprovider -q
# => 34 passed in 10.73s
```

**failed / error 归因（不计为 passed）**

| 项 | 全量顺序下 | 单独复跑 | 判定 |
|---|---|---|---|
| `test_migration_039_xbot_feed.py::test_039_upgrades_a_db_built_by_old_code` | failed | passed | 全量顺序污染（`conftest.py:95` 检查临时目录 `news.db-wal/shm` 残留） |
| `test_xwatch_syndication.py::TestFetchTweet::test_empty_id_returns_none_without_calling_network` | error | passed | 同上 fixture 残留；该用例自身通过 |

本次 diff 不含迁移、网络或后端代码，无法在因果上触发这两项。

## 2. UI 实测（Playwright 真实 Chromium，8901）

### 2.1 顶栏入口与徽章

| 项 | 实测 |
|---|---|
| 导航链接数 | 12（原 11 + 信息脱敏） |
| `href` | `/desens-lab.html` |
| 徽章文案/类 | `关` / `nav-chip nav-chip-state nav-chip-muted` |
| 点击后 URL | `http://127.0.0.1:8901/desens-lab.html` |
| 主站 console | 0 error |

### 2.2 演练台

| 项 | 实测 |
|---|---|
| 总开关 pill | `总开关 关（L1✓ L2✓ L3✗）` |
| 账本 pill | `账本 生产 0 次 / 实验室 1 次` |
| 只预览 | 第②栏出 5 处遮蔽（`{{ORG_jcwwdnrn}}` 等），状态 `遮蔽 5 处 · 策略 standard · 380 µs` |
| 主题 | body 背景 `oklch(0.963 0.013 97.4)`（主站浅色令牌）；`.dark` 档同源 |
| 「← 返回 moni」 | 存在（1 个） |
| console | 0 error |

### 2.3 账本对齐（用户报的问题）

`th.num` 与 `td.num` 右边缘偏差，8 列 × 2 张表全部为 0：

```text
[0,0,0,0,0,0,0,0]           # 汇总表
[0,0,0,0]                   # 最近调用 4 个数字列
```

另把「最近调用」从汇总表下方拆成独立区块（标题 + 分隔线），不再看起来像同一张表多一行表头。

### 2.4 响应式

| 宽度 | 顶栏 | 演练台列数 | 账本横向滚动 |
|---|---|---|---|
| 1440 | 1 行 | 4 | 无 |
| 1280 | 1 行 | 4 | 无 |
| 1100 | 1 行 | 2 | 无 |
| 820 | 1 行 | 1 | 无 |
| 390 | 1 行 | 1 | 有（表格自身滚动，页面无溢出） |

390px 页面 `scrollWidth 392 vs innerWidth 390`：**移除新链接后仍为 392**，属既有顶栏右侧状态区溢出，与本次改动无关。

## 3. 未做 / skipped

- 真实模型「真跑一次」未在本轮执行（会写一条实验室账本并产生调用费用）；演练台 1 次账本为历史数据
- 配置面板、规则 CRUD：按 dad 决定只写规划，不开发（`docs/plans/2026-11-25_desens-config-panel-plan.md`）
- 8900 生产未部署（需 dad 当轮授权）
