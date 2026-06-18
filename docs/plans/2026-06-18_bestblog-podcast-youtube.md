# Plan: bestblog-podcast / bestblog-yt 新增信源 + bestblogs 补全

**Date:** 2026-06-18
**Version:** v1.0.7.1
**Branch:** `feature/v1.0.7.1-bestblog-podcast-youtube`

## Goal

新增 2 个 BestBlogs 系列信源 (podcast + YouTube)，并补全现有 bestblogs 信源（合并 ALL OPML 中缺失的 43 个 feed）。

## 验收标准

1. `bestblog-podcast` source 可抓取 60 个小宇宙播客 feed
2. `bestblog-yt` source 可抓取 20 个 YouTube 频道 feed
3. `bestblogs` source 合并 ALL OPML 后新增 43 个英文技术博客 feed
4. 三个 source 默认 tag 关联正确
5. 黑名单过滤生效
6. pytest 全 pass

## 数据分析

| OPML 文件 | feed 数 | 说明 |
|-----------|---------|------|
| 现有 bestblogs | 375 | 公众号 RSS |
| podcast (新增) | 60 | 小宇宙播客 |
| youtube (新增) | 20 | YouTube 频道 |
| ALL (补全) | 72 | 技术博客，其中 43 个是新的 |

## 改动清单

### 1. `moni/db/seed_config.py`

- **新增 tag 关联**:
  - `bestblog-podcast` → wechat, ai-fintech
  - `bestblog-yt` → wechat, ai-fintech
- **新增黑名单**:
  - `bestblog-podcast`: 入境游, 文娱, 明星, 游戏, 影视, 电商, 消费, 美食, 腾讯控股
  - `bestblog-yt`: 入境游, 文娱, 明星, 游戏, 影视, 电商, 消费, 美食, 腾讯控股
- **新增 source 定义**:
  - `src-bestblog-podcast`: name=`bestblog-podcast`, OPML URL
  - `src-bestblog-yt`: name=`bestblog-yt`, OPML URL

### 2. `config/sources.yaml`

- 新增 `bestblog-podcast` 和 `bestblog-yt` 条目

### 3. `moni/fetch/sources/bestblogs.py`

- `_resolve_url()`: 支持逗号分隔的多 URL，返回 `list[str]`
- `fetch()`: 遍历所有 URL 调 `_fetch_opml`，合并后按 feed_url 去重
- `_load_config()`: 适配多 URL 场景

### 4. `moni/db/seed_config.py` — bestblogs 补全

- 修改 `bestblogs` source 的 url 字段，追加 ALL OPML URL（逗号分隔）

### 5. Tests

- 测试多 URL 合并去重逻辑
- 测试新 source 的 seed 数据正确性

## 执行顺序

1. ✅ 建 branch
2. 改 seed_config.py (2 个新 source + bestblogs URL 补全)
3. 改 sources.yaml
4. 改 bestblogs.py (多 URL 支持)
5. 写 tests
6. pytest 验证
7. commit + push + PR
