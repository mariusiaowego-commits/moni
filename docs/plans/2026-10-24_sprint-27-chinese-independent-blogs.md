# sprint-27 TECH-SPEC: 接入中文独立博客列表 (v1.0.60)

- **Sprint ID**: 26102401
- **目标版本**: v1.0.60
- **阶段**: sprint-27
- **日期**: 2026-10-24
- **目标仓库**: `mariusiaowego-commits/moni`
- **上游数据源**: `https://github.com/geekplux/chinese-independent-blogs`
- **上游文件**: `feed.opml` (MIT License)
- **状态**: 实现中

---

## 1. 可行性闸门 (2026-10-24 实测)

上游 `feed.opml` 共 **695 个 feed，0 个重复 URL**；其中 http 88 个、https 607 个。

完整串行探测结果：

| 指标 | 结果 |
|---|---:|
| 可解析且有文章 | **306** feeds |
| 可解析文章总量 | **13,107** 篇 |
| 有正文/摘要片段的 feed | **290** |
| 空 feed | 13 |
| HTTP 4xx/5xx | 108 |
| XML 非法 | 37 |
| 传输失败/超时/TLS | 231 |
| 中位响应时间 | 1.15 s |

结论：**值得接入**。按 306 个有效 feed 计，即使每 feed 只取 3 篇也能稳定提供约 900 篇候选内容。失败项是上游名单长期未维护的客观状态，不应把单次失败视为 moni 代码缺陷，也不能在每轮抓取时反复硬打 695 个地址。

> 探测方式提醒：并发 8 路时会大量出现 `PoolTimeout` 假失败；串行探测的 306 才是可信数字。正式实现必须限流，不做 8 路并发。

---

## 2. 设计决策

### 2.1 独立源，不并入 bestblogs

新增 source：`chinese_independent_blogs`，显示名「中文独立博客」。

不并入 `bestblogs` 的原因：

1. 两个上游的维护节奏、失效率和内容分布完全不同。
2. `bestblogs` 已绑定 `wechat` tag，而该 tag 的关键词门控会把大量独立博客文章丢弃；实测 39 个有效样本中，按 `general-tech` 关键词只看标题仅命中 1 个（2.6%）。
3. 独立源需要自己的 fetch 上限、失败退避和统计口径，不能污染现有 375-feed 聚合源的成功率与耗时。

### 2.2 复用解析，不复用抓取策略

复用：

- OPML `<outline xmlUrl>` 解析
- RSS 2.0 / Atom 1.0 解析
- HTML cleaner、URL 去重、入库管道

不复用：

- `bestblogs` 的全量每次扫描策略
- `bestblogs` 的 tag 绑定方式
- `bestblogs` 的并发/超时默认值

### 2.3 内容筛选策略：独立 tag + 可选显式放行

新增 tag `independent-blog`（独立博客），绑定到新 source。

该 tag **不配置关键词**，因此 `apply_tag_keyword_filter()` 对该源是 pass-through（保持全部文章）。这是有意为之：

- 独立博客的价值在于技术、产品、生活、写作等长尾视角，无法用银行/支付关键词收窄；
- 用 `wechat` / `general-tech` 的窄关键词筛选会丢掉绝大部分有效文章；
- 如后续需要收窄，用户可在 `/config` 为该 tag 增加关键词，机制无需改代码。

新 source 不绑定现有 `wechat` / `general-tech`，避免被既有 1000+ 关键词 OR 集误判。

### 2.4 抓取预算与失败处理

默认 `fetch_config`：

```json
{
  "max_feeds": 80,
  "max_articles_per_feed": 3,
  "max_articles_total": 240,
  "request_delay": 0.35,
  "http_timeout": 12.0,
  "min_feeds_before_failure_rate_check": 10,
  "max_failure_rate": 0.7,
  "timeout_sec": 1200
}
```

设计理由：

- 695 个全扫一轮按中位 1.15s + delay 至少 17 分钟，且会产生 231 个超时，不适合每次运行都全扫。
- `max_feeds=80` 让单轮可完成、可观测；用户可在 `/config` 调高。
- 按 OPML 顺序轮转起点（round-robin cursor），避免永远只抓前 80 个。
- 个别 feed 失败不中断；至少扫描 10 个后，网络失败率超过 70% 才提前结束。
- `timeout_sec=1200`：80 × 12s + delay 约 988s，保留约 180s 下载/解析/入库余量。
- 单 feed 失败只记录、不影响其他 feed；最终结果包含 `feeds_ok/feeds_failed/feeds_empty`。

### 2.5 安全边界

- 仅允许 `http://` / `https://` 的 OPML 与 feed URL；拒绝 `file:`、`javascript:` 等 scheme。
- 本地 OPML 路径仅允许仓库内 `config/` 目录（保持与 `bestblog-x` 相同的本地文件能力，但收窄路径）。
- 单 feed 响应体上限 5 MB，防止异常大响应拖垮进程。
- feed 标题、文章标题按现有 DB 字段长度处理；HTML 内容交给现有 cleaner。

---

## 3. 变更清单

| 操作 | 文件 | 说明 |
|---|---|---|
| CREATE | `moni/fetch/sources/chinese_independent_blogs.py` | 新 fetcher：OPML 解析、轮转、限流、RSS/Atom 解析、失败统计 |
| MODIFY | `moni/fetch/sources/__init__.py` | 注册新 fetcher |
| MODIFY | `moni/db/seed_config.py` | 新增 tag、source、source_tag |
| MODIFY | `config/sources.yaml` | 新增 source 配置（enabled，180 min） |
| MODIFY | `moni/web/routes/config_sources.py` | 补 fetch_config 默认值，供 `/config` 预填 |
| CREATE | `tests/test_chinese_independent_blogs.py` | 离线单测：OPML、RSS、Atom、轮转、失败统计、安全校验 |
| CREATE | `docs/plans/2026-10-24_sprint-27-chinese-independent-blogs.md` | 本文件 |
| MODIFY | `STATUS.md` / `USER_GUIDE.md` / `HANDOFF.md` / `vibe-coding-log.md` | 收尾文档 |
| MODIFY | `AGENTS.md` | 信源数量与抓取说明 |

---

## 4. 验收标准

### AC-1 注册与配置

- `list_fetchers()` 包含 `chinese_independent_blogs`
- DB seed 后存在 source 与 `independent-blog` tag 绑定
- `config/sources.yaml` 中该源为 enabled，间隔 180 分钟

### AC-2 OPML 解析

- 解析真实 `feed.opml` 得到 695 条 feed（离线 fixture）
- 跳过无 `xmlUrl` 的 outline
- 拒绝非 http(s) feed URL

### AC-3 RSS/Atom 解析

- RSS 2.0 正确提取 title/url/published_at/content/publisher
- Atom 正确提取 alternate link、published、summary/content
- 无 link 条目跳过；空 feed 返回 []
- HTML 描述经现有 cleaner 清洗

### AC-4 轮转与预算

- `max_feeds` 生效
- 第二次运行从上次结束位置继续（cursor 持久化）
- `max_articles_per_feed` 与 `max_articles_total` 生效
- 单轮 `request_delay` 生效（测试用 0 或 mock）

### AC-5 失败隔离

- 单个 feed 超时/404/XML 非法不抛出到 `fetch()`
- 至少扫描 10 个 feed 且网络失败率超过 70% 才提前结束
- 结果统计 `feeds_ok/feeds_failed/feeds_http_error/feeds_empty/feeds_invalid`

### AC-6 安全

- `javascript:` / `file:` feed URL 被拒绝
- 响应超过 5 MB 被拒绝
- 本地 OPML 路径越界被拒绝

### AC-7 回归

- 定向测试通过
- 全量 pytest 通过（`-p no:cacheprovider`）
- 现有 bestblogs / xwatch / 36kr 测试无回归

---

## 5. 非目标

- 不修改数据库 schema / 不加 migration
- 不自动修复上游失效 feed
- 不向上下游仓库提交 PR
- 不在本轮启用 695 全量扫描
- 不接入 LLM 评分或自动摘要（沿用 moni 现有下游）

---

## 6. 发布与回滚

发布：

1. 定向 + 全量测试
2. 独立 audit
3. PR + CI 绿
4. squash merge
5. tag `v1.0.60`
6. 重启 8900，验 5 端点
7. 手动触发一次 `moni fetch --source chinese_independent_blogs --dry-run`（验证真实网络）

回滚：

```bash
git revert <squash-sha>
launchctl kickstart -k gui/$(id -u)/com.moni.web
```

新增 source 行可保留为 disabled，不影响现有源。
