# 信息脱敏「配置面板 + 规则 CRUD」规划（下一 sprint，本轮不开发）

- 日期：2026-11-25
- 状态：**部分已交付** —— 开关/分级/严格模式 + 配置读写在 v1.0.137 上线；规则 CRUD 待排期
- 前置：v1.0.132 把「信息脱敏」入口与状态徽章接进主站；v1.0.137 把入口归位「配置管理 → 脱敏」，并交付配置区与 `GET/PUT /api/desensitize/config`
- 相关：`docs/plans/2026-11-24_llm-desens-shield-plan.md` §11（UI 契约）、`docs/plans/2026-11-25_llm-desens-lab-finals.md` §5（未就绪清单）

## 1. 为什么现在做不了、以后要做什么

现状：脱敏能力（内核 + 5 条通道 + 演练台）已上线，但**没有任何写入侧的界面或接口**。

| 能力 | 今天怎么改 | 缺什么 |
|---|---|---|
| 总开关 / L1·L2·L3 / 严格模式 | **已在「配置管理 → 脱敏 → 配置」里改（v1.0.137）** | ✅ 已交付 |
| 自定义规则 | `INSERT INTO desensitize_rules` | 规则 CRUD + 正则校验（下一 sprint） |
| 清空内存本本 | 重启进程 | 进程内操作接口（多 worker 需广播） |
| 轮换 salt | 手工换文件 | 受控接口 + 在途任务保护 |
| 查看账本 | `/api/desensitize/stats`、演练台 | `/stats` 第 8 块（可选） |

目标：让非开发人员能在 `/config` 里安全地开关、分级、配规则，且每次改动可追溯、可回滚。

## 2. 范围（建议一个 sprint）

**做**

1. 设置读写：`GET/PUT /api/desensitize/settings`
2. 规则 CRUD：`GET/POST/PATCH/DELETE /api/desensitize/rules`
3. 管理动作：`POST /api/desensitize/salt/rotate`、`POST /api/desensitize/vault/clear`（后者按进程广播，取不到就 409）
4. `/config` → 大模型设置 → 第 3 个子分段「信息脱敏」：四区面板（见 §3）
5. 变更审计：新表 `desensitize_admin_log`（谁、何时、改了什么、改前/改后）；不记录任何原值
6. 规则校验：正则编译、`kind` 白名单、L1 不允许整体关闭（只允许逐个 kind 禁用）、`action=block` 仅限 L1

**不做**

- 本地 NER 模型、90 天归档、跨进程文件槽（沿用原方案的 M3）
- 规则导入/导出、批量迁移
- 任何「自动学习规则」类猜测功能

## 3. UI 契约

位置：`/config` → 「大模型设置」→ 子分段「信息脱敏」（与现有 `.segmented-control` 并列）。

- **第一区 概览**：4 张卡（防线状态 / 累计拦截处数与去重实体 / 还原成功率 / 泄漏命中必须 0）+ 总开关
- **第二区 分级**：L1 / L2 / L3 三行开关；L3 展开 4 个 KIND；`PATH_USER` 标注「随 L2 生效」
- **第三区 规则表**：列 `KIND / 规则 / 同义别名 / 泛称 / 动作 / 状态 / 操作`；行内操作用 `.row-actions`，相邻按钮 `.btn + .btn`
- **第四区 危险操作**：轮换 salt、清空本本；二次确认 + 明示影响（旧编码不可还原 / 在途任务会失败）
- 窄屏 390×844 折两列；亮暗两套令牌；`?v=` 递增
- 演练台入口保持在顶部导航（v1.0.132），面板里再加一条「去演练台」链接

## 4. API 契约（新增，纯增量）

```text
GET    /api/desensitize/settings        -> {enabled, level_l1, level_l2, level_l3, strict_fail_closed}
PUT    /api/desensitize/settings        <- 同结构（部分字段可选）  -> 200 + 生效值
GET    /api/desensitize/rules           -> [{id, rule_type, kind, pattern, aliases, pseudonym, action, is_active, notes}]
POST   /api/desensitize/rules           -> 201 + 新行
PATCH  /api/desensitize/rules/{id}      -> 200 + 改后行
DELETE /api/desensitize/rules/{id}      -> 204（软删 is_active=0，保留审计）
POST   /api/desensitize/salt/rotate     -> 200 {rotated_at, affected}   // 需 confirm=true
POST   /api/desensitize/vault/clear     -> 200 {cleared}                // 需 confirm=true；多进程取不到返回 409
```

约束：

- 所有写接口要求 `confirm=true`（POST/PUT/PATCH），并写 `desensitize_admin_log`
- 写入后立即失效设置缓存（`audit.py` 的 30s TTL 改为「写后主动 invalidate + 继续保留 TTL」）
- 正则规则在写入前编译一次，失败返回 422 并指出第几组
- `PUT settings` 不允许在 `enabled=false` 时提交规则改动（避免"以为开了其实没开"）

## 5. 数据与迁移

- 新表：`desensitize_admin_log(id, actor, action, target, before_json, after_json, created_at)`
- `actor`：本机无登录体系，先记 `local` + 请求来源 IP；不伪造用户名
- 迁移号接着 `_CURRENT_VERSION`（当前 60）往后排；备份 WAL 三件套后再执行

## 6. 验收

- 接口：pytest 覆盖 6 类失败模式（非法正则、未知 kind、L1 全关、confirm 缺失、软删后列表、salt 轮换后旧编码不可还原）
- UI：8901 真浏览器，1440 / 1100 / 820 / 390 四档无溢出；面板数字与 `/api/desensitize/stats` 逐位一致
- 一致性：改完设置 ≤1s 内新请求读到新值（缓存已失效）
- 回归：全量 pytest 全绿；8900 行为不变（默认仍关）
- 证据：`docs/sprint-evidence/sprint-NN/VERIFY.md` 写命令与输出

## 7. 风险

| 风险 | 影响 | 处置 |
|---|---|---|
| 面板把 L1 全关 | 凭据裸发 | 接口层禁止；UI 只允许逐 kind |
| 自定义正则灾难性回溯 | 每次出站卡死 | 编译 + 长度上限 + 单条超时熔断 |
| 轮换 salt 时在途任务 | 回包编码无法还原 | 拒绝轮换（检测在途 job）或标记 `restore_error_raw` |
| 多 worker 清空本本 | 只清了一个进程 | 单进程部署时可用；多进程返回 409 并说明 |
| 审计表膨胀 | 磁盘 | 只记变更，不记调用；按月归档 |

## 8. 排期建议

1 个 sprint（约 3–4 个工作日）：接口 + 校验 + 审计（1.5d）→ 面板（1.5d）→ 端测与回归（1d）。
