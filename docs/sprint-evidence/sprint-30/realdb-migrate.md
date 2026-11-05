# sprint-30 / v1.0.67 — 真实库副本上的 migration 039 实测

- **日期**: 2026-11-05
- **代码**: `feat/sprint-30-xbot-feed-migration` @ `507c2b2`（基线 `e53956c`）
- **被测迁移**: `moni/db/migrations/039_xbot_feed_source.sql`
- **被测数据库**: 真实库的**副本**（`/workspace/moni-plan/sprint30/realdb/news.db`，105,996,288 字节）
- **命令**: `MONI_DB_PATH=<副本> python -m moni.db.migrate`（migrate-only，**不跑 seed**）

> **数据隔离**：本文件**不含任何文章内容**。只记录行数、source id、
> tag 名和字段级 before/after diff。真实库的副本跑完即删，
> **原始 `news.db` 全程只读**（跑完仍是 schema 38，见文末「原始库状态」）。

---

## 0. 真实库起点

| 项 | 值 |
|---|---|
| schema_version | **38** |
| sources 行数 | 17 |
| source_tags 行数 | 54 |
| `src-xbot-feed` | **存在**，`is_active=1`，4 条 tag 绑定齐全 |

真实库本身已经是 v1.0.66 的状态（源在库里）。三个场景分别覆盖
"库原样"、"源被删"、"用户改过源"。

---

## 1. 场景 A —— 库原样（源已在）

**准备**：直接复制，不做任何改动。

| 检查点 | before | after（run 1） |
|---|---|---|
| schema_version | 38 | **39** |
| sources 行数 | 17 | 17 |
| source_tags 行数 | 54 | 54 |
| `src-xbot-feed` 存在 | 是 | 是 |
| `is_active` | 1 | **1**（未被改动） |
| tag 绑定 | ai-fintech / general-tech / miniprogram / wechat | 同上（4 条） |
| **其它 source 变动行数** | — | **0** |
| source_tags 新增 / 移除 | — | **0 / 0** |
| 行数变化 | — | `schema_cleanup_log` 4 → 5，`schema_version` 34 → 35 |

**结论**：039 在已有源的库上是**纯 no-op**（除了记版本和 cleanup_log）。
用户的源一个字都没动。

**run 1 输出**：

```
Applying base schema from schema.sql ...
Applying migration 039_xbot_feed_source.sql (v38 → v39) ...
Migration complete. schema_version = 39
```

**run 2（幂等）**：`rc=0`，`DB schema already at version 39, nothing to migrate.`
→ sources / source_tags / schema_version **逐行相同**，**no-op**。

---

## 2. 场景 B —— 源被删（"升级后没跑 seed-config"的存量库）

**准备**：

```sql
DELETE FROM source_tags WHERE source_id = 'src-xbot-feed';
DELETE FROM sources      WHERE id = 'src-xbot-feed';
```

| 检查点 | before | after（run 1） |
|---|---|---|
| schema_version | 38 | **39** |
| sources 行数 | 16 | **17**（源被恢复） |
| source_tags 行数 | 50 | **54**（4 条绑定建上） |
| `src-xbot-feed` 存在 | **否** | **是** |
| `is_active` | — | **1** |
| tag 绑定 | — | ai-fintech / general-tech / miniprogram / wechat（**4 条**） |
| **其它 source 变动行数** | — | **0** |
| source_tags 新增 | — | 4 条，全部是 `src-xbot-feed` 的 |
| source_tags 移除 | — | **0** |
| 行数变化 | — | `sources` 16→17，`source_tags` 50→54，`schema_cleanup_log` 4→5，`schema_version` 34→35 |

**新增的源行字段**（与 `seed_config._DEFAULT_SOURCES` 的 `xbot_feed` 条目逐字段一致）：
`id` / `name` / `display_name` / `source_type` / `url` / `fetch_interval_min` /
`is_active` / `notes` / `fetch_config` —— 9 个字段全部由 migration 写入。

**结论**：**被删的源被恢复，且带上正确的 4 条 tag 绑定**（Mttt Q-O-2 的决定）。
恢复后 `_is_source_bypassed("xbot_feed")` 重新为 `True` —— 精选内容不再被
全局黑名单静默误杀（这是 039 存在的理由，见 plan §1.0）。

**run 2（幂等）**：`rc=0`，**no-op**（第二次跑源仍是 1 行、绑定仍是 4 条）。

---

## 3. 场景 C —— 用户改过源（核心安全性质）

**准备**（模拟用户在 `/config` 里的编辑）：

```sql
UPDATE sources SET display_name = 'MY-XBOT', is_active = 0,
                   fetch_interval_min = 15, notes = '我的备注'
WHERE id = 'src-xbot-feed';
DELETE FROM source_tags WHERE source_id = 'src-xbot-feed'
                          AND tag_name = 'ai-fintech';
```

| 检查点 | before | after（run 1） |
|---|---|---|
| schema_version | 38 | **39** |
| `src-xbot-feed` 存在 | 是 | 是 |
| `is_active` | **0**（用户停用了） | **0** —— **没有被重新启用** |
| `display_name` | `MY-XBOT` | **`MY-XBOT`** —— 用户改的名字保住了 |
| `fetch_interval_min` | 15 | **15** —— 没被重置成 60 |
| `notes` | 我的备注 | **我的备注** —— 没被重置成 seed 默认 |
| **其它 source 变动行数** | — | **0** |
| source_tags 新增 | — | 1 条（`ai-fintech` —— 用户取消的那条勾选被**补回**） |
| source_tags 移除 | — | **0** |
| 行数变化 | — | `source_tags` 53→54，`schema_cleanup_log` 4→5，`schema_version` 34→35 |

**结论**：用户的**每一个编辑字段都逐字保留**，包括**停用状态**。
唯一的变化是**用户在 `/config` 取消的那条 tag 勾选被补回** ——
这是**刻意**的，语义与 `seed()` 一致（"绑定只增不删"），
`USER_GUIDE.md:1327-1329` 已经写明这条规则。

> 如果 Mttt 认为"取消的勾选不该被补回"，那 `seed()` 也得一起改 ——
> 那是另一个决定（plan Q-O-3）。

**run 2（幂等）**：`rc=0`，**no-op**。

---

## 4. 三个场景的汇总

| 检查项 | A（原样） | B（源被删） | C（用户改过） |
|---|---|---|---|
| run 1 退出码 | 0 | 0 | 0 |
| schema_version before → after | 38 → **39** | 38 → **39** | 38 → **39** |
| 源存在 | 是（不变） | 否 → **是** | 是（不变） |
| `is_active` | 1（不变） | 1 | **0（不变）** |
| 用户改过的字段 | — | — | **逐字段保留** |
| tag 绑定 | 4 条（不变） | 0 → **4 条** | 3 条 → **4 条** |
| **其它 source 变动** | **0** | **0** | **0** |
| source_tags 被移除 | 0 | 0 | 0 |
| run 2 | **no-op** | **no-op** | **no-op** |

**全部符合预期**：升级只做加法，从不改用户已有的东西。

---

## 5. 原始库状态（跑完之后）

真实库**全程只读**，跑完仍然是：

| 项 | 值 |
|---|---|
| schema_version | **38**（未升级 —— 升级要在真实机器上由用户执行） |
| sources | 17 |
| source_tags | 54 |
| schema_cleanup_log | 4（未加 039 那条） |

所有副本（`news-a.db` / `news-b.db` / `news-c.db` 及其 `-wal` / `-shm`）
在各自场景跑完后**立即删除**。

---

## 6. 复现命令

```bash
# 复制（注意：WAL 库要连 -wal / -shm 一起拷）
cp /workspace/moni-plan/sprint30/realdb/news.db /tmp/news-copy.db

# migrate-only 升级
cd /workspace/moni-sprint-xbot
MONI_DB_PATH=/tmp/news-copy.db python -m moni.db.migrate

# 幂等：再跑一次应该是 no-op
MONI_DB_PATH=/tmp/news-copy.db python -m moni.db.migrate

# 检查
sqlite3 /tmp/news-copy.db \
  "SELECT COALESCE(MAX(version),0) FROM schema_version;
   SELECT id, is_active FROM sources WHERE id='src-xbot-feed';
   SELECT tag_name FROM source_tags WHERE source_id='src-xbot-feed';"
```

**不要用 `moni db init` 升级** —— 它是 `migrate() + seed_llm + seed_config`，
`seed_config` 会把用户改过的字段重置（plan §4.2 / §4.3.1 的实测）。

---

## 7. 相关测试

同一套性质由 `tests/test_migration_039_xbot_feed.py` 钉死（30 条），
其中 `test_039_upgrades_a_db_built_by_old_code` 用 `015a2c5`（xbot_feed
落地**之前**的代码）建一个真库再升级 —— 覆盖"存量库升级"的完整链路。

- 全量：`1486 passed / 2 skipped`（基线 1456 + 30）
- 日志：`docs/sprint-evidence/sprint-30/pytest-full.txt`
