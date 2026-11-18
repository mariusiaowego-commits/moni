# 事实核查闸门修复（v1.0.100 / PR-A）

## 问题（实测）

1. **白名单闸门可被说明文字绕过**：`jobs.py` 用 `conclusion == "【已核实】"` 判定，
   而 `fact_check_plus.md:48` 允许「标签 + ≤20 字说明」。线上 36 篇联网核查里
   20 篇【已核实】，其中 14 篇带后缀，整条绕过 Gate 2。
2. **只要求 1 个白名单域**，prompt 却要求 ≥2 个独立来源。
3. **时效闸从未生效**：`fetched_at` 缺省取 `finished_at`，217 条证据 `days_old` 全为 0。
4. **没有"怎么核的"**：结论只有标签和 summary，来源条数/类型/日期无法追溯。

## 方案

### 1. 白名单分三档（`config/fact_check_domains.toml`）

- **A 一手**：监管/政府（`*.gov.cn`、`nfra`、`pbc`、`csrc` 等）、交易所、巨潮公告、
  公告 wire、arXiv 论文、业务方/主办方官方页。
- **B 编辑制媒体**：央媒、财经主流、门户财经、科技与同业媒体。
- **C 不计**：百科、知乎、头条、维基、微博、雪球等。
- 判定按 **eTLD+1 注册域去重**：`contest.weixin.qq.com` 与 `news.qq.com`
  都算 `qq.com`，同一集团不算独立来源。

### 2. 两条判定路径

1. ≥2 个不同注册域且 ≥1 个 A；
2. ≥3 个不同 B 且口径一致。

都不满足 → 最高【待核实】。

### 3. 标签归一

`_conclusion_tag()` 用正则取档位，闸门一律用标签判定；降级时用
`_with_tag()` 保留模型原来的说明文字。

### 4. 来源日期

evidence 新增 `source_date`。年龄按 `source_date` 算；模型没给日期时
`days_old = NULL`、时效 `unknown`，不再伪造成"今天"。

### 5. 留痕

- `articles.fact_check_gate`：JSON，记录 authority 判定、一手/媒体域名、
  是否降级、时效档。
- `articles.fact_check_how`：代码派生的「几条证据 / 几类来源 / 最新来源日期 /
  时效」，不交给模型自述。

## 只读回扫（生产库，未回写）

20 篇【已核实】按新规则会降级 3 篇：

| 文章 | 原因 | 可计入域 |
|---|---|---|
| `88203fb1` | 无一手源，媒体不足 3 个 | cls.cn / sina.com.cn |
| `893f1a07` | 只有 1 个可计入注册域 | qq.com（百科/知乎不计） |
| `b1789846` | 只有 1 个可计入注册域 | mpaypass.com.cn |

其余 17 篇满足路径 1 或路径 2。

## 验收

- `tests/test_fact_check_plus.py` 54 passed（新增 6 条：分档、去重、两条路径、
  带后缀放行/降级、缺日期 unknown、source_date 驱动时效）。
- 全量：2028 passed + 2 skipped（`test_migration_039` 在沙箱内因
  `git worktree add` 无权限失败，沙箱外 33 passed）。

## 影响面 / 回滚

- 只改联网档写库路径与配置；不动 prompt、不动 UI（PR-B）。
- 迁移 047 只加列，历史行 NULL。
- 回滚：revert 本 PR；新列留着不影响旧代码（旧代码不读）。

