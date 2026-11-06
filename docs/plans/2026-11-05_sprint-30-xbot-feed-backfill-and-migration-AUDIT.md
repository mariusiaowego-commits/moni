# sprint-30 AUDIT: PR #172 xbot_feed 源独立审计回填

- **审计对象**: PR #172（`feat/xbot-feed-source` → squash merge `e53956c`，2026-11-03）
- **原始审计报告**: `/workspace/moni-plan/pr172-audit.md`（509 行，2026-11-03）
- **基线测试**: 1414 passed / 2 skipped（审计起点 `08ae8da`）
- **修复后测试**: 1456 passed / 2 skipped（合并前）
- **本次回填**: 2026-11-05，sprint-30
- **结论**: 9 blocking + 6 should-fix 全修完 → 可合并（**已合并 `e53956c`**）

> **本文件是"回填"，不是新审计。**
> 原始报告全文 509 行在 `/workspace/moni-plan/pr172-audit.md`（工作区，不在仓库里）。
> 本文件压缩成可核查的结论，**每条都标实际 commit hash** —— 不抄原报告里
> 那句不实的"已全部修掉"（原报告 §0 的补记说明了原因：初版声称修完时
> **一行代码都没改**）。

---

## 1. 结论摘要

| 严重度 | 数量 | 说明 |
|---|---|---|
| **blocking** | 9（B1–B5 + B-6/B-7/B-8/B-9） | 必须修完才能合并 |
| **should-fix** | 6（S-1–S-6） | 必须修完才能合并 |
| **nit** | 8（N-1–N-8） | 已清理或记录为建议 |

**判定**：修完 blocking + should-fix 后可合并。**已合并**（squash `e53956c`）。

---

## 2. Blocking（9 条）

> **⚠️ 关于下面表格里的 commit hash（不可独立核验）**
>
> PR #172 是 **squash merge**（`e53956c`），下面 §2 / §3 两张表里的 6 个短 hash
> （`59553f2` / `75983af` / `329814d` / `9b520f9` / `c171dfe` / `a857ea7`）
> 来自**合并前** `feat/xbot-feed-source` 分支上的原始 commit ——
> **它们不在 main 的历史里**，仓库里的读者无法独立核验
> （`git cat-file -t 59553f2` → `fatal: Not a valid object name`；
> `git rev-parse --is-shallow-repository` → `false`，不是浅克隆，是真的没有）。
>
> **这些 hash 不改** —— 回填是忠实的，改了才是造假。能核验的是**合并后的最终状态**：
> 见本文件 §6 的真实运行记录，以及 `docs/sprint-evidence/sprint-30/`。
> 原始审计报告在 `/workspace/moni-plan/pr172-audit.md`（工作区，不在仓库里）。

| # | 问题 | 修法 | Commit |
|---|---|---|---|
| **B1** | **tag 闸门被 bypass 完全架空**：PR 用 `bypass_keyword_filters: true` + 在 `dedupe_insert()` 里按 topic **硬打** tag。`tag_filter.py:183-202` 的 bypass 分支直接 `return articles`，**tag 关键词闸门一行都不跑** → 用户在 `/config` 改关键词对本源零效果；而且 PR 自己覆写 `dedupe_insert()` 把 bypass 合成命中换成硬打，等于把「关键词驱动」换成「topic 驱动」 | tag 闸门改到 fetcher 内：按 topic 的**候选 tag** → 读该 tag **当前**关键词 → 只挂**真实命中**的 tag；**零命中不入库**；被挡条目**不写 state** | `59553f2` |
| **B2** | `LIKE '%status/{id}%'` **前缀误命中**（`123` 会命中 `1234`） | 改成精确尾部匹配 | `75983af` |
| **B3** | **可变默认参数 / 类属性共享可变状态** | `keyword_hits=None` 默认 + 改成实例属性 | `75983af` |
| **B4** | **obsidian_push 把整个 `xbot_feed` 当微信生态**（它是混合源） | 移除 `xbot_feed` from `WECHAT_SOURCES`；改按**文章实际 tag** 判断 | `c171dfe` |
| **B5** | **`fetch_config` 默认值抄三处** → 改一处忘另两处就会出现「web 面板显示的默认值」≠「seed 写进库的」≠「fetcher 实际生效的」 | 抽 `DEFAULT_FETCH_CONFIG` 单一来源（`xbot_feed.py:170`），三处引用 | `9b520f9` |
| **B-6** | **git `repo_url` / `clone_dir` 注入**：URL 直接进 `git clone` argv，可伪装成 `--upload-pack=` / `-c core.sshCommand=`；`clone_dir` 可越出数据目录 | `repo_url` 只接受 `https://github.com/<owner>/<repo>[.git]`（拒绝 `-` 开头 / 非 github 协议 / 空白控制字符）；`clone_dir` 必须落在 moni 数据目录内；git argv 用 `--` 分隔 | `329814d` |
| **B-7** | **git 输出凭据脱敏**（`user:token@` 泄漏进日志） | 输出进日志前把 URL 凭据段替换成 `***` | `329814d` |
| **B-8** | **多余的 bypass-hit 过滤** | 直接透传 fetcher 自己算出来的真实命中 | `59553f2` |
| **B-9** | `scripts/make_xbot_feed_icon.py` 不符合惯例 | 删脚本，只留 png | `a857ea7` |

---

## 3. Should-fix（6 条）

| # | 问题 | 修法 | Commit |
|---|---|---|---|
| **S-1** | 删掉死的 `state.files` 字段 + 修正 docstring（**nothing ever read it**） | 状态文件只存 `imported_ids` | `a857ea7` |
| **S-2** | `max_days` 改成按**文件名里的真实日期**判断（原实现按"最近 N 个文件"切片，语义不是"最近 N 天"） | 按 `YYYY-MM-DD` 解析后与今天比 | `a857ea7` |
| **S-3** | `_source_label` 语义修正 | 随 B4 一起修 | `c171dfe` |
| **S-4** | `app.js` `sortOrder` 归到"导入型"分组 + 注释 | 分组修正 | `a857ea7` |
| **S-5** | `config/sources.yaml` `crawler_type: rss` → `json` | 改类型 | `a857ea7` |
| **S-6** | `STATUS.md` / 方案文档 / `USER_GUIDE` 同步新机制 | 三处同步 | `a857ea7` |

---

## 4. Nit（8 条）

| # | 内容 | 处置 |
|---|---|---|
| **N-1** | 保留重复（follow_builders 的 URL 归一逻辑没抽公共） | **保留**，文档注明 |
| **N-2** | `topic_tags()` 语义改为"候选" | 已改 docstring |
| **N-3** | 类属性 → 实例属性 | 部分修（随 B3） |
| **N-4** | fixture `latest.json` 与日期文件逐字节相同（4.4 KB） | 保留 |
| **N-5** | 图标 19 KB 偏小 | 保留 |
| **N-6** | `WECHAT_SOURCES` 硬编码 → `WECHAT_TAGS` | 随 B4 |
| **N-7** | `moni/fetch/sources/follow_builders.py:152` 同款 `LIKE '%status/{id}%'` 前缀误命中 | **不在本 PR 范围** —— 与 B2 同源但牵动另一个源，建议单独 PR（plan Q-O-6） |
| **N-8** | `loop.sh` 的 `$?` 被 echo 吞掉 | 已由编排者修（`rc=$?` 先存再写日志） |

---

## 5. 未修项与后续

### N-7 —— `follow_builders.py:152` 的 LIKE 前缀误命中

`moni/fetch/sources/follow_builders.py:152`：

```python
(canonical_url, f"%status/{tweet_id}%"),
```

与 B2 **同源**（前缀 LIKE 会让 `123` 命中 `1234`），但改它牵动另一个源，
本 PR 不做。**建议单独 PR，不要塞进别的 sprint。**

### N-8 —— `/home/box/.local/share/xbot-feed/loop.sh` 退出码记录

已由编排者于 2026-11-03 修（`rc=$?`）。**本仓库不涉及** —— `loop.sh`
在 moni 之外。

### v1.0.68 补的一条（sprint-30 新发现）

**v1.0.66 没有 migration** —— 新源只进了 `seed_config.py` + `config/sources.yaml`，
没进任何存量数据库。后果（实测见 plan §1.0）：

- 从没跑过 `moni db seed-config` 的存量库 → `/config` 看不到这个源；
- `_is_source_bypassed()` **只读 DB** → 源行缺失时它静默返回 `False` →
  全局/源级黑白名单闸门重新生效 → **精选内容被黑名单误杀，且抓取不报错**。

已由 **migration 039**（sprint-30 / v1.0.68）修掉，见
`docs/plans/2026-11-05_sprint-30-xbot-feed-backfill-and-migration.md`。

---

## 6. 真实运行

见 `docs/sprint-evidence/sprint-30/RUN.md`（本 sprint 重跑并扩展到三天数据）
与 `docs/sprint-evidence/sprint-30/xbot-feed-import-stats.json`。

原始报告 §8 的数字（2026-11-03 单日）：

| 项目 | 数值 |
|---|---|
| 导出 | 203（wechat 46 + xcircle 157） |
| 作者黑名单过滤 | 6（全是 wechat topic） |
| 关键词未命中（不入库） | 126（wechat 9 + xcircle 117） |
| **第 1 次 inserted** | **71**（wechat 31 + xcircle 40）= 203 − 6 − 126 |
| 零 tag 文章 | **0** |
| tag 分布 | wechat 28 / miniprogram 21 / general-tech 37 / ai-fintech 7（合计 93） |
| 跨 topic 误打标签 | **0** |
| **第 2 次（rerun）inserted** | **0** |

> **这些数字是"测试关键词"下的** —— 真实库的关键词不同，条数就不同。
> **机制**是真的，**数字不是承诺**。

---

## 7. 结论

- 9 个 blocking + 6 个 should-fix 全修完，逐条对过 diff
  （commits `59553f2` `75983af` `329814d` `9b520f9` `c171dfe` `a857ea7`）。
- **安全**：git 只用 argv 列表（无 shell）、`--` 分隔、`repo_url` 限定
  `https://github.com/<owner>/<repo>`、clone 目录限定在 data dir 内、
  git 输出脱敏；diff 里没有密钥，没有 github.com 以外的网络，没有外发。
- **符合 AGENTS.md**：匹配只走 `keyword_match.keyword_matches`；
  零 tag 不入库；`sources.yaml` + `seed_config` 都注册了。
- **测试**：本地 1456 passed / 2 skipped；CI pass。
- **真实运行**：数字对得上，tag 按 topic 正确，rerun 插 0，无报错。
- Nit（N-1–N-8）不阻塞；N-7 不在本 PR 范围，留作后续。
- **判定：可以合并（squash）。** —— **已合并 `e53956c`**（2026-11-03）。
