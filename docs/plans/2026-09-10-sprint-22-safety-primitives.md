# Sprint 22 Plan（代码库镜像）— 安全原语与凭据出栈脱敏

- **sprint**: sprint-22 (id 26091002) / **版本**: v1.0.42 / **状态**: Approved (dad 2026-09-10 拍板)
- **触发**: `docs/sprint-audit-2026/` 21 张工单复核后，dad 选定 deepseek-flash 泳道 = 安全原语 PR
- **配套**: `docs/sprint-audit-2026/review-deepseek-flash.md`（复核证据链）
- **Obsidian 双写**: `tqob/05-Coding/project-moni/sprints/sprint-22-safety-primitives/plan-safety-primitives-2026-09-10.md`

> 本文件是 Obsidian PLAN 的**代码库镜像**（AGENTS.md 双写规则）。完整 4 段结构（Goal / Blocking Questions / Assumptions / Plan）以 Obsidian 版为准，此处同步同一内容。

## Goal

一次性收口 moni 的**对外暴露面**（信息泄露 + 注入 sink），交付 3 个纯函数原语并在唯一入口强制走：

| 工单 | 现象 | 原语 |
|---|---|---|
| P0-2 | `GET //etc/hosts` 返回真实文件（审计 2026-09-06 已实测 213B） | `resolve_static_path(root, rel) -> Path \| None` |
| P1-10 | `GET /api/config/sources` 原样返回微信 RSS JWT | `sanitize_source_url(url) -> str \| None` |
| P2-15 | modal `title` / 按钮文案进 `innerHTML` 未转义 | `_escapeHtml()` 复用 |

**验收**：单测绿 + 全量 889 不回归 + `node --check` 通过 + `GET /api/config/sources` 响应体不含 `eyJ`。

## Blocking Questions（dad 2026-09-10 已答）

| # | 问题 | 结论 |
|---|---|---|
| Q1 | 8900 停机原因？ | 保持停机，不要开启（= MiniMax-M3 的 migration 034 停机窗口）→ 运行时 curl 验证降级为单测 + TestClient / 临时只读端口 |
| Q2 | 本次怎么切？ | **方案 A = A1 + A2 + A4**（≈100 min）；safeHref / ghost / 测试护栏顺延 |
| Q3 | ghost sources 处置？ | 本 sprint 不处理（顺延） |
| Q4 | `PUT /api/fetch/sources` 扇出地雷？ | 本 sprint 不处理（归 MiniMax-M3 的 `routes/fetch.py`） |

## Assumptions

1. **Data**：`sources.url` 仅 `wechat_rss_a` / `wechat_rss_b` 两行带 `?token=eyJ…`（已核实），其余 13 行无凭据 → 脱敏对它们必须是**恒等变换**。
2. **Failure**：脱敏只作用于**出栈**，永不改写 DB 存值（改写会让抓取失效）。原语异常时**返回原值**，不制造 500。
3. **Boundaries**：路径遏制拒绝 NUL / 反斜杠 / `..` 段 / 绝对路径 / symlink 逃逸；**root 内合法 symlink 必须放行**；越界与"不存在"统一 404。
4. **State**：零 DDL、零 migration、零 schema 变更；`moni/db/*` 完全不碰。
5. **Environment**：测试必须 `MONI_DB_PATH=/tmp/moni-test-ds.db`；生产 8900 保持停机。
6. **Scope**：只改 4 个源文件 + 2 个新测试文件；`moni/llm/*`、`moni/fetch/*`、`moni/cron/*`、`moni/web/routes/fetch.py` 一律不碰。
7. **Testing**：A1 纯函数真值表（14 向量）+ TestClient 集成；A2 真值表 + PUT 语义（**先写测试再写实现**）+ 出栈集成。全量用 `-p no:cacheprovider`。

**Alternative considered and rejected**：在 `sources.url` **落库时**就打码 — 拒绝，因为该列是**抓取输入**，打码后 RSS 拉取直接 401；脱敏必须只发生在出栈边界。

## Plan

### A1 — `moni/web/server.py`（P0-2）

```python
def resolve_static_path(root: Path, rel: str) -> Path | None:
    """URL rel 路径 → root 下的真实文件；越界/非法返回 None。"""
    # 1) POSIX 语义拒绝: NUL / 反斜杠 / '..' 段 / 以 '/' 开头(绝对路径)
    # 2) candidate = (root / rel).resolve(); root 也 resolve (同坐标系)
    # 3) containment: candidate.is_relative_to(root.resolve())  ← 不用字符串前缀比较
    # 4) 越界/不存在 → None (统一 404, 不泄露路径信息)
```

调用点：`candidate = STATIC_DIR / full_path` → `resolve_static_path(STATIC_DIR, full_path)`；`None` → 走 SPA fallback(index.html)。
新增 `tests/test_spa_fallback.py`：14 攻击向量 + 合法路径放行。

### A2 — `moni/web/redact.py`（新）+ `moni/web/routes/config_sources.py`（P1-10）

1. `sanitize_source_url()`：仅 query 中 token 类参数（`token` / `access_token` / `secret` / `key` / `api_key`，大小写不敏感）的值替换为 `…`，**保留参数名**；无凭据 / 非法输入 / 异常 → 原样返回（幂等、恒等）。
2. 出栈点全量覆盖：`config_sources.py` list(`:102`) / create(`:173`) / update / get-by-id。
3. create/update 收到**打码值** → 判定"未修改"，保留 DB 原值（防止把 `…` 写进 DB 破坏抓取）。
4. 新增 `tests/test_source_url_redaction.py`：真值表 + 幂等 + PUT 语义（断言 DB 实际内容）+ 集成断言响应不含 `eyJ`。

### A4 — `moni/web/static/js/modal.js`（P2-15）

`opts.title` 与 `okLabel` / `cancelLabel` 走 `_escapeHtml`（与同文件 `safeMsg` 同款，抽模块内函数复用）。

### 顺序与验证

A1 → A2 → A4 → 单测 → 全量 pytest → `node --check` → commit/PR。

```bash
export MONI_DB_PATH=/tmp/moni-test-ds.db
/usr/local/bin/python3 -m pytest tests/test_spa_fallback.py tests/test_source_url_redaction.py -q -p no:cacheprovider
/usr/local/bin/python3 -m pytest tests/ -q -p no:cacheprovider        # 全量 889 基线
node --check moni/web/static/js/modal.js
```

### 回滚

三项互相独立，各自可单独 revert：A1 = 1 行调用点 + 1 纯函数；A2 = 删 `redact.py` + revert 出栈点（**零 DB 改动**，无数据回滚需求）；A4 = `modal.js` 行内 revert。

## 跨泳道边界（MiniMax-M3）

| 我的文件 | 他的文件 | 重叠 |
|---|---|---|
| `moni/web/server.py`(`spa_fallback` 段)、`moni/web/redact.py`(新)、`moni/web/routes/config_sources.py`、`moni/web/static/**`、2 个新测试文件 | `moni/db/leases.py`(新)、`migrations/034/035`、`moni/fetch/jobs.py`、`moni/web/routes/fetch.py`、`moni/llm/{jobs,runner,client}.py`、`moni/share/worker_llm.py` | **零共享文件** |

migration 编号：他领 034/035 → 我本 sprint 不需迁移。合并顺序：我（零迁移）先合先 tag。全量 pytest 串行化，DB 各自 `MONI_DB_PATH` 隔离。
