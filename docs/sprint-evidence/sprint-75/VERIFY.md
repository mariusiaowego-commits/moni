# Sprint 75 验收记录 — LLM 用量卡片修复 + verify-moni

- 日期：2026-11-25
- 分支：`feature/v1.0.130-llm-usage-state-fix`（base `f838ae0`，已合入 main `11dc915`）
- 版本：待发 `v1.0.130`

## 1. 命令与输出

```bash
/usr/local/bin/python3 -m pytest tests/ -p no:cacheprovider -q
/usr/local/bin/python3 .codex/skills/verify-moni/scripts/verify_moni.py
```

- pytest 全量：`2818 passed, 2 skipped`（2026-11-25 复跑）
- verify_moni.py：`{"total": 21, "pass": 19, "fail": 0, "skipped": 2}`

说明：首轮全量出现 1 条 `tests/test_worker_lock.py::test_sigterm_kills_blocking_child_that_ignores_term`
失败（`fake worker never started its child`）。该用例与本改动无交集（本 PR 只碰静态资源 + 一个断言文件），
单独复跑通过（2.87 s），且在未改动的 `/Users/mt16/dev/moni` checkout 上同样通过 —— 判定为
负载相关的时序 flake，不记为本 PR 回归。

## 2. UI 实测（真实 Chromium，8901，视口 1600×1000）

| 检查 | 实测 |
|---|---|
| 12 个 SPA 路由 | 全部 `http=200`，`nav_links=11`，`console_unexpected=0` |
| 文章列表 | `tbody_rows=92` |
| 量化看板 LLM 用量卡片 | `card='59 次调用'`，`/api/stats/llm-usage` `api_calls=59` |
| 脱敏演练台 | `cols=4`，`c1_plain=True`，`c2_plain=False`，`c2_placeholder=True`，`c3=—`，`status=遮蔽 5 处 · 策略 standard · 349 µs`，`pill=总开关 关` |
| 响应式 | 1600/1280/1100/820 全部 `scrollWidth == innerWidth` |

修复前对照（生产 8900，同一视口）：

```
card = "0 次调用"
errors = 6 × "Cannot read properties of null (reading 'totals')"
       + 3 × "Cannot read properties of null (reading 'by_model')"
       + 1 × "Transition was skipped"
```

## 3. 可复算数字

```bash
# 卡片必须与接口一致（修复后 59 = 59；修复前 0 ≠ 57）
curl -s "http://127.0.0.1:8901/api/stats/llm-usage?period=30d" | jq .totals

# 状态名必须只在 stats.js 出现
rg -c "statsLlmUsage" moni/web/static/js/stats.js
rg -c "llmUsage" moni/web/static/index.html
```

## 4. 真模型三段（各 1 次，已授权；8901 + 库副本）

| 通道 | 端点 | 结果 |
|---|---|---|
| ① 摘要（`runner.py`） | `POST /api/articles/{id}/summary` | `done`，15.6 s，`articles.summary` 204 字 |
| ② 联网核查（`stream.py` + `web_search_safe`） | `POST /api/articles/{id}/fact-check-plus` | `done`，238.0 s，`fact_check_evidence` 14 行，`fact_check_method=web_search` |
| ④/⑤ 分享简报（独立子进程 + ContextVar） | `POST /api/share/brief` | `done`，145.3 s，artifact 2016 字节 markdown |

请求体注意：`/summary` 需要 `{"article_id": "<id>"}`，空体返回 422 且不入队。

## 5. failed 与 skipped

- **failed**：无（首轮 flake 见第 1 节，已单独复跑通过并交叉验证）。
- **skipped**（脚本内显式记录，不计为通过）：
  - 文章摘要 / 事实核查 / 简报的**自动化**回归 —— 花额度，改为上面的手工抽样；
  - 主界面 1024px 窄屏表格行为 —— 本轮未覆盖。

## 6. 已知但未修（既有，8900 可复现，另行跟进）

- `/config → 大模型设置 → 用量统计` 点击后 7 条 `Cannot read properties of undefined (reading 'after')`
  （Alpine `x-for` key 相关），8900 同样 7 条。
- `/basket` 2 条 `422 (Unprocessable Entity)`，8900 同样 2 条；已作为 `KNOWN_BASELINE` 写进验证脚本。
