# BestBlogs 信源取舍配置（v1.0.69）

> 文档创建日期：2026-11-06 · PR #176 · tag `v1.0.69` · main `0dbe7e2`

## 背景

BestBlogs 公众号聚合把两个 OPML 合并去重后是 **506 个 feed**，抓取侧只有
`max_feeds=375` 的头部截断 —— 想少抓一个号、或多抓一个号，都没有配置入口，
只能改代码或直接改 DB 行。

2026-11-06 dad 用本地 triage 页面（`/private/tmp/bestblogs-feeds-triage.html`，
按 `articles.publisher` 统计每个号的总篇数 / 近 90 天 / 最后发文）做了首次取舍：
**删 28 个、加 5 个**。

## 方案

新增 `config/feed_overrides.json`，按 source 名分块：

```json
{
  "bestblogs": {
    "exclude_feeds": [{"name": "刘小排r", "url": "https://…/feed/<hash>.xml"}],
    "extra_feeds":   [{"name": "牛油果烤面包", "url": "http://rsshub…/podcast/<id>"}]
  }
}
```

语义（实现见 `moni/fetch/sources/bestblogs.py`）：

| 键 | 行为 | 为什么 |
|---|---|---|
| `exclude_feeds` | 按 **feed URL 精确匹配** 删除；配置了但 OPML 里没有的项 → `warning` 点名 | 名字可能重名，URL 是唯一键；失效删除项必须可见 |
| `extra_feeds` | **插到列表最前面**，再走 `max_feeds` 截断 | 5 个播客原本在合并列表 502–506 位，永远进不了 375 窗口 |
| 容错 | 文件缺失 / 坏 JSON / 块值非 list / url 非字符串 → 一律降级为空 | 可选配置不能把抓取搞崩 |
| 作用域 | 按 `source_name` 取块；`bestblog-x` 有独立 fetch，不读该文件 | 避免把公众号的删除项套到播客/YouTube 上 |

## 为什么不改 DB / seed_config

- `fetch_config` 存在 `sources` 表里，改它需要动存量库（migration 或手写 SQL），
  而 8900 由 uvicorn 直接拉起、不跑 `migrate()`，容易"合并了但没生效"。
- 文件在抓取时按需读取 → 重启即生效，diff 里能逐条 review 名称与 URL。
- `seed()` 的 `COALESCE` 语义会在 `moni db init` 时整体覆盖 `fetch_config`，
  把用户后续在 `/config` 的修改冲掉；配置文件没有这个风险。

## 已知边界

- **不改变单轮覆盖率**：`max_articles_total=500` ÷ `max_articles_per_feed=5` ≈ 95 个 feed/轮，
  28 条删除项位于合并序号 107–370，前 100 个里一条都没有 —— 删它们只是让位，不会跑得更快。
- 合并后仍有 **103 个 feed 落在 375 窗口外**（506 − 28 = 478 > 375）。
- 想让全部跑完需调 `max_feeds` 与 `max_articles_total`（单轮约 24 分钟），属另一个决策。

## 验证

- `pytest tests/test_bestblogs.py` → 45 passed（新增 10 个用例，覆盖缺文件 / 坏 JSON / 按源隔离 /
  非 list 块值 / 非字符串 URL / extra 置顶去重 / exclude 命中 / 失效删除项 / yt 隔离 / 端到端顺序）
- `pytest tests/ -p no:cacheprovider -q` → 1518 passed / 2 skipped
- 独立只读审计两轮：轮 1 APPROVE WITH NITS（P2 块值非 list 抛 `TypeError`、P2 覆盖率口径未写、
  P3 注释口径 / 缺文件静默 / url 未校验 / 测试缺口）→ 当轮全修；轮 2 APPROVE。
- 运行态：8900 重启后 `_load_feed_overrides("bestblogs")` → `exclude 28 / extra 5`；
  `bestblog-yt` → 空（隔离生效）。
