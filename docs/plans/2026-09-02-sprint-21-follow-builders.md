---
id: 26090201
type: sprint
version: v1.0.41
start_date: 2026-09-02
end_date: 2026-09-03
status: 进行中
priority: 中
summary: sprint-21 v1.0.41 接入 follow-builders 中央精选 X 推文信源（feed-x.json），跨源软去重 + 全链路双层门控直通打 AI tag
tags: [sprint, project-moni, tech-spec, fetcher]
---

# sprint-21 TECH-SPEC: 接入 follow-builders 信源 (v1.0.41)

> **Sprint ID**: 26090201  
> **目标版本**: v1.0.41  
> **状态**: 进行中 (TECH-SPEC v2 已修复 BLOCKER，待实现)  
> **Appetite**: 1 天  
> **上游数据源**: `https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-x.json` (26 位精选 AI builders，中央日更 JSON，24h lookback)  
> **设计定位**: 试验性轻量源，默认关闭（`enabled: false`），用户在 `/config` 显式开启后抓取。

---

## 1. 约束与边界（Explicit Out-of-Scope）

- **不改动 `BaseFetcher.run()` 主管道**：保持既有的 `fetch() → parse() → cleaner → source_keyword_filters → tag_keyword_filter → dedupe_insert` 经典流转。
- **不改动数据库 schema**：`articles` 表的 `UNIQUE(source, url)` 物理约束严格保持不变，不新建 migration 改动表结构。
- **不碰 8900 prod 端口**：开发与测试均在本地环境和 8901 验证。
- **不搬迁外部 digest/Telegram/Obsidian 脚本**：仅接入推文入库，下游摘要、简报、检索复用 moni 现有标准能力。
- **不 clone follow-builders skill**：纯 Python 标准库 + httpx 接入。

---

## 2. 预计变动文件清单

| 操作类型 | 文件路径 | 变更要点 |
| :--- | :--- | :--- |
| **CREATE** | `moni/fetch/sources/follow_builders.py` | 实现 `FetcherFollowBuilders(BaseFetcher)`，包含 HTTP 拉取、推文解析、URL 归一化、跨源软去重 |
| **CREATE** | `tests/fixtures/feed_x_sample.json` | 真实 `feed-x.json` 裁剪切片（含引用推文、特殊表情、空列表、字段缺失等边缘用例） |
| **CREATE** | `tests/test_follow_builders_fetch.py` | 纯离线单元测试套件（严格 mock HTTP，验证 parse/归一化/去重/双层 bypass/异常处理） |
| **CREATE** | `docs/plans/2026-09-02-sprint-21-follow-builders.md` | 项目内 dual-write 镜像规范文档 |
| **MODIFY** | `moni/fetch/sources/__init__.py` | 增加 `from . import follow_builders` 注册新 Fetcher |
| **MODIFY** | `config/sources.yaml` | 注册 `follow_builders`，设 `enabled: false`，配置 `fetch_interval_min: 360` |
| **MODIFY** | `moni/db/seed_config.py` | `_DEFAULT_SOURCES` 增加配置（`is_active: 0`），`_DEFAULT_SOURCE_TAGS` 绑定 `ai-fintech` |
| **MODIFY** | `moni/fetch/filters.py` | 增加 `bypass_keyword_filters` 机制：在 `apply_keyword_filters` 头部短路，跳过全局/源级白黑名单 |
| **MODIFY** | `moni/fetch/tag_filter.py` | 增加 `bypass_keyword_filters` 机制：在 `apply_tag_keyword_filter` 头部跳过 tag 关键词匹配并全量合成打上绑定 tag |
| **MODIFY** | `moni/web/routes/config_sources.py` | 在 `_FETCH_CONFIG_DEFAULTS` 补充 `follow_builders` 默认配置参数字典 |

---

## 3. 上游数据格式与 Parse 字段映射

### 3.1 上游 JSON 样例 (`feed-x.json`)
```json
{
  "generatedAt": "2026-09-01T06:42:22.120Z",
  "lookbackHours": 24,
  "x": [
    {
      "source": "x",
      "name": "Thibault Sottiaux",
      "handle": "thsottiaux",
      "bio": "Codex & ChatGPT @OpenAI",
      "tweets": [
        {
          "id": "2094588317245509959",
          "text": "If you still haven't tried Codex...",
          "createdAt": "2026-09-01T00:49:05.000Z",
          "url": "https://x.com/thsottiaux/status/2094588317245509959",
          "likes": 2344,
          "retweets": 42,
          "replies": 1816,
          "isQuote": false,
          "quotedTweetId": null
        }
      ]
    }
  ]
}
```

### 3.2 Canonical 字段映射表

| moni Canonical 字段 | `feed-x.json` 来源 | 处理与兜底规则 |
| :--- | :--- | :--- |
| `source` | 固定值 | `"follow_builders"` |
| `url` | `tweet["url"]` 或 `tweet["id"]` | 执行 URL 归一化（统一为 `https://x.com/{handle}/status/{id}`，去除 query） |
| `title` | `tweet["text"]` | 生成策略：截取正文有效文本前 40 字符；若有换行取首行；超过加 `...`；若空则回退为 `f"@{handle} on X"` |
| `publisher` | `builder["name"]` / `builder["handle"]` | 格式化为 `f"{builder['name']} (@{builder['handle']})"` |
| `published_at` | `tweet["createdAt"]` | 标准 ISO8601 字符串（若解析失败则设为 `None`） |
| `content` | `tweet["text"]` + 互动数据 | 组合格式：正文文本 + `\n\n[❤️ {likes} | 🔁 {retweets} | 💬 {replies}]`（为下游 LLM 评估提供元数据） |

---

## 4. URL 归一化与跨源软去重（Cross-Source Soft Dedupe）

### 4.1 URL 归一化算法
X 推文在不同源中（如 `bestblog-x`、`xwatch`）可能表现为：
- `https://twitter.com/user/status/123456?s=20`
- `https://x.com/user/status/123456`
- `http://mobile.twitter.com/...`

实现 `normalize_tweet_url(raw_url: str, handle: str = "", tweet_id: str = "") -> str`:
1. 从 URL 或显式字段中提取纯数字 `tweet_id`（正则 `r"/status/(\d+)"`）。
2. 若提取失败，清理 URL 中的 query params (`url.split("?")[0]`) 并替换 `twitter.com` 为 `x.com`。
3. 若提取成功，统一规范化为：`https://x.com/{handle or '_'}/status/{tweet_id}`。

### 4.2 跨源软去重实现（不修改 DB 约束）
在 `FetcherFollowBuilders.parse()` 或 `fetch()` 中执行软查重：
```python
def _is_url_exists_globally(self, canonical_url: str, tweet_id: str) -> bool:
    """检查 articles 表中是否已存在同推文（无论 source 为何）"""
    try:
        from moni.db.connection import get_db
        conn = get_db()
        # 1. 精确匹配规范化 URL
        # 2. 匹配可能存在的 twitter.com 历史变体或包含 tweet_id 的 URL
        row = conn.execute(
            """
            SELECT 1 FROM articles
            WHERE url = ? 
               OR url LIKE ?
            LIMIT 1
            """,
            (canonical_url, f"%status/{tweet_id}%"),
        ).fetchone()
        return row is not None
    except Exception as exc:
        logger.warning("[follow_builders] check global url exists failed for %s: %s", canonical_url, exc)
        return False
```
- 若已存在，在 `parse()` 阶段直接返回 `None`（`stats["skipped_existing"] += 1`），既不打扰后续管道，又防止与 `bestblog-x` 重复。

---

## 5. 关键词与 Tag 双层门控全链路直通机制（Bypass Keyword & Tag Filters）

### 5.1 痛点诊断与 BLOCKER 根因
moni 的抓取流水线在 `BaseFetcher.run()` 中依次经历两道独立的过滤关卡：
```
BaseFetcher.run():
  ① apply_keyword_filters(source, parsed)      ← 全局/源级 白黑名单（filters.py）
  ② apply_tag_keyword_filter(source, filtered) ← Tag 关键词 OR 门控（tag_filter.py）
  ③ dedupe_insert(filtered, keyword_hits)      ← 入库与写 tag
```
- **原设计缺口 (BLOCKER)**：若只在 ② 挂载 bypass，当系统配置了**全局白名单**（如要求必须含"银行/支付"）或**全局黑名单**时，`follow_builders` 的英文推文在 ① 阶段就会被识别为不满足白名单（`dropped_global_whitelist`）丢入 `basket`，**根本流转不到 ②**，导致文章全军覆没，更无法打上 AI tag。
- **核心需求**：必须使用统一标志，在 ① 和 ② 两道关卡同时生效短路。

### 5.2 统一配置标志: `bypass_keyword_filters`
在 `sources.fetch_config` 中配置 `"bypass_keyword_filters": true`（代码同时兼容别名 `"bypass_tag_filter": true`）。

#### 5.2.1 第一关短路：`moni/fetch/filters.py`
在 `apply_keyword_filters()` 入口处增加判定：
```python
def _is_source_bypassed(source_name: str) -> bool:
    try:
        conn = get_db()
        row = conn.execute("SELECT fetch_config FROM sources WHERE name = ?", (source_name,)).fetchone()
        if row and row["fetch_config"]:
            cfg = json.loads(row["fetch_config"])
            return bool(cfg.get("bypass_keyword_filters") or cfg.get("bypass_tag_filter"))
    except Exception:
        pass
    return False

# 在 apply_keyword_filters() 头部:
if _is_source_bypassed(source_name):
    logger.info("[%s] bypass_keyword_filters enabled — skipping global/source black/whitelists", source_name)
    stats["kept"] = len(articles)
    return articles, stats, []
```
- **效果**：跳过全局黑名单、源黑名单、全局白名单、源白名单，文章 100% 存活且不写 basket，零损耗进入第二关。

#### 5.2.2 第二关短路与合成打 Tag：`moni/fetch/tag_filter.py`
在 `apply_tag_keyword_filter()` 入口处增加判定：
```python
# 在 apply_tag_keyword_filter() 头部:
if _is_source_bypassed(source):
    logger.info("[%s] bypass_keyword_filters enabled — bypassing tag filter and synthesizing hits", source)
    bound_tags = _load_bound_tag_names_for_source(source)  # 查 source_tags 获取如 ['ai-fintech']
    synthetic_hits = []
    for art in articles:
        art_url = art.get("url", "")
        for tag_name in bound_tags:
            synthetic_hits.append({
                "article_url": art_url,
                "keyword": "(bypass)",
                "tag_name": tag_name,
                "filter_id": None,
                "hit_field": "bypass",
            })
    stats["kept"] = len(articles)
    stats["tags_used"] = len(bound_tags)
    return articles, stats, synthetic_hits
```
- **效果**：跳过 tag keywords 匹配，全量保留推文，同时产出包含 `tag_name="ai-fintech"` 的 `synthetic_hits`。
- **下游闭环**：`repository.py` 的 `_insert_hit_tags()` 读取 `synthetic_hits`，精准将 `("article_id", "ai-fintech")` 写入 `article_tags` 表。

#### 5.2.3 他源安全隔离
- 其他源（如 `36kr_rss`、`bestblog-x` 等）在 `sources.fetch_config` 中无此键（评估为 `False`），继续走严格的黑白名单与 Tag 关键词门控，**100% 互不干扰，零误伤**。

---

## 6. 抓取调度与默认禁用验证（Scheduler & Config）

### 6.1 调度器验证
已核查 `moni/fetch/scheduler.py`：
```python
def should_fetch(self, source_name: str) -> bool:
    cfg = self._source_config(source_name)
    if not cfg.get("enabled", True):
        return False  # 已原生支持 enabled 门控
```
且 `enabled_sources()` 仅遍历 `cfg.get("enabled", True)`。

### 6.2 默认状态设置
1. `config/sources.yaml`：
   ```yaml
   - source_name: follow_builders
     type: json
     feed_url: https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-x.json
     fetch_interval_min: 360
     enabled: false
     crawler_type: rss
   ```
2. `moni/db/seed_config.py`：
   - 插入 `sources` 时显式指定 `is_active = 0`（不可误沿用 hardcoded 1）。
   - `fetch_config` 初始化预置：
     ```json
     {
       "bypass_keyword_filters": true,
       "timeout_sec": 30,
       "http_timeout": 15.0,
       "skip_existing_urls": true
     }
     ```
3. 用户在 Web 界面 `/config` 开启开关后，`is_active` / `enabled` 被写回，调度器方开始轮询。

---

## 7. 抓取超时与间隔（Timeout & Interval）

- **抓取间隔**：上游按天更新，设 `fetch_interval_min: 360`（6 小时一次），极大节省流量并规避 GitHub API 限制。
- **全局任务超时 (`_job_timeout`)**：配置 `"timeout_sec": 60`，远低于默认 600s，避免单次拉取挂死。
- **HTTP 单次请求超时**：`httpx.get(..., timeout=15.0)`。

---

## 8. 测试策略与用例要点

### 8.1 测试原则
- **严格离线**：禁用一切 live network 请求，Mock `httpx.get` / `httpx.Client.get`。
- **独立 DB 隔离**：使用 tmp sqlite 配合 `migrate()` 与 teardown 自动清理。

### 8.2 Fixture 准备 (`tests/fixtures/feed_x_sample.json`)
包含以下结构化用例：
1. **Case A (标准推文)**：单条标准推文，验证所有字段映射。
2. **Case B (多行及长推文)**：验证 `title` 截断（40 字符 + `...`）与首行提取。
3. **Case C (带追踪参数的 twitter.com 链接)**：验证 URL 归一化为 `https://x.com/...`。
4. **Case D (Quote 推文)**：验证 `isQuote=true` 解析健壮性。
5. **Case E (空 tweets / 缺失字段)**：验证防御解析不抛异常。

### 8.3 核心单测清单 (`tests/test_follow_builders_fetch.py`)
1. `test_parse_normal_tweet`: 验证 Canonical 结构正确（含互动数拼接）。
2. `test_url_normalization`: 针对 `twitter.com/foo/status/123?s=20` 与 `x.com/foo/status/123` 输出一致结果。
3. `test_cross_source_dedupe`: 向 DB 先写入一条 `source='bestblog-x'` 的同推文，验证 `follow_builders` 自动 skip。
4. `test_bypass_with_active_global_whitelist_and_blacklist` (BLOCKER 核心验收):
   - 向 DB 注入 active 的全局白名单（keywords=['银行']）和全局黑名单（keywords=['crypto']）；
   - 输入包含英文/非银行词汇的推文，配置 `bypass_keyword_filters: true`；
   - 验证推文顺利穿透 ① `filters.py` 和 ② `tag_filter.py`，无 drop 产生，且 `article_tags` 成功写入 `ai-fintech`。
5. `test_other_sources_still_filtered`: 验证在同一 DB 下，其他源（如 36kr_rss）依然受到全局白黑名单和 tag 过滤拦截，不发生策略泄漏。
6. `test_http_error_graceful`: Mock 404 / 500 / Timeout，验证返回空结果且不导致 runner 崩溃。
7. `test_scheduler_disabled`: 验证在 `enabled: false` 状态下 `should_fetch()` 返回 `False`。

---

## 9. 风险与验收清单

- [ ] `tests/test_follow_builders_fetch.py` 全绿通过（pytest 无警告）。
- [ ] 运行 `moni db seed-config` 后，新源状态为 `disabled / is_active=0`。
- [ ] 单元测试验证跨源 URL 软去重生效。
- [ ] 单元测试验证在配置全局白名单/黑名单情况下，`follow_builders` 依然全收并成功赋予 `ai-fintech` tag。
- [ ] 单元测试验证其他源的关键词与 tag 过滤不受任何影响。
- [ ] 全套现有测试 suite 无任何破坏回归（780 passed）。
