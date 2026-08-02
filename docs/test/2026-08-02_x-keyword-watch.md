# TEST-PLAN: x-keyword-watch (v1.0.11)

> 项目: moni · 创建: 2026-08-02 · 分支: `feature/v1.0.11-x-keyword-watch`
> 执行者须先读 `docs/specs/2026-08-02_x-keyword-watch.md` §0（环境事实）。

## 1. 基线

- `pytest tests/` = 439 pass + 2 skip（v1.0.10 基线；本 sprint 结束时 **439 + 新增 pass，2 skip，0 fail**）
- 测试禁用真实网络（用 fixtures + monkeypatch），保证 CI/无网环境可跑
- 新增用例目标 ≥ 15 个

## 2. Fixtures（`tests/fixtures/`）

| 文件 | 内容要求 |
|---|---|
| `ddg_results_sample.html` | 含：3 个 `x.com/<u>/status/<id>` 链接；其中 1 个与另一个重复；1 个 `x.com/<u>` 普通主页链接（不应被抽中）；1 个外部链接 |
| `ddg_anomaly.html` | 含字符串 `Anomaly` 的风控页片段 |
| `tweet_sample.json` | 普通推文 syndication 响应（有 `text/user/created_at/id_str`；`in_reply_to_*` 为 null/缺省） |
| `tweet_reply_sample.json` | 回复推文（`in_reply_to_screen_name/in_reply_to_status_id_str/in_reply_to_user_id_str` 有值），用示例 2083475663496044761 的字段形态 |

## 3. 测试矩阵

### `tests/test_xwatch_discovery.py`

| # | 用例 | 断言 |
|---|---|---|
| 1 | `_extract_tweet_urls` 抽 3 个 status 链接 | 返回 3 个 `https://x.com/.../status/<id>` |
| 2 | 重复链接 | 保序去重后 ≤3 |
| 3 | 非 status 的 x.com 链接 | 不包含 |
| 4 | `max_results=2` | 只返回 2 个 |
| 5 | 空 HTML | `[]` |
| 6 | `search_tweet_urls` mock httpx 200 + 样例 HTML | 返回期望 URL；请求 params 含 `site:x.com` |
| 7 | `search_tweet_urls` mock anomaly 页 | 重试一次后返回 `[]`（断言 httpx 被调用 2 次），不抛异常 |
| 8 | `search_tweet_urls` mock 网络异常 | 返回 `[]`，不抛异常 |

### `tests/test_xwatch_syndication.py`

| # | 用例 | 断言 |
|---|---|---|
| 9 | `tweet_id_from_url` 正常 status URL | 返回数字串 |
| 10 | `tweet_id_from_url` 非 status URL | `None` |
| 11 | `tweet_id_from_url` 纯数字串 | 原样返回 |
| 12 | `tweet_to_article(tweet_sample)` | title=首行≤80；url=`https://x.com/{screen_name}/status/{id}`；publisher=`@screen_name`；published_at 为 `+00:00` ISO；content=全文 |
| 13 | `tweet_to_article(tweet_reply_sample)` | 同上，published_at 可解析 |
| 14 | `tweet_to_article` 无 text / 无 user | `None` |
| 15 | `fetch_tweet` mock 200 | 返回 JSON dict |
| 16 | `fetch_tweet` mock 404/异常 | `None`，不抛异常 |

### `tests/test_xwatch_fetch.py`（monkeypatch 两个 helper + DB 临时库）

| # | 用例 | 断言 |
|---|---|---|
| 17 | 配置默认值（fetch_config 缺失） | 用 `_DEFAULTS`，queries 非空 |
| 18 | `fetch()` 2 个 query × 各 1 条（mock discovery/syndication） | 返回 2 条 raw；`seen_ids` 去重生效（跨 query 同 URL 只 1 条） |
| 19 | 某 query discovery 返回 [] | 不影响另一个 query，无异常 |
| 20 | syndication 返回 None | 该 raw parse 为 None，整体不炸 |
| 21 | progress_callback 被调用 | `total_feeds==len(queries)`，末次 `done_feeds==len(queries)` |
| 22 | `parse()` 产物字段 | title/url/source/published_at/content 齐全 |
| 23 | 空 queries / 非法类型 | 返回 [] |
| 24 | 端到端 `run()`（mock 网络 + 临时 DB） | inserted≥1；`article_tags` 含 wechat 或 miniprogram（keyword_hits 机制）；重复 run inserted=0 |

### `tests/test_xwatch_cli.py`

| # | 用例 | 断言 |
|---|---|---|
| 25 | `xwatch ingest` 无效输入 | 返回 1，stderr/print 有 ERROR |
| 26 | `xwatch ingest` mock syndication 成功 + tag 命中 | 返回 0，DB 有行，`article_tags` 有 tag |
| 27 | `xwatch ingest` tag 未命中 | 返回 0，DB 无行，输出 SKIP |
| 28 | `xwatch ingest --dry-run` | 不写库 |

## 4. 回归检查

```bash
set -a && source .env && set +a
/usr/local/bin/python3 -m pytest tests/ -q          # 439 + 新增 pass, 2 skip, 0 fail
```

重点回归：`tests/test_fetch_jobs.py`、`tests/test_bestblogs.py`、`tests/test_tag_filter.py`、`tests/test_seed_config*.py`（若有）。

## 5. 手动验收（8901 / 真实数据）

```bash
# 1) 源已注册
/usr/local/bin/python3 -m moni db seed-config
sqlite3 data/news.db "SELECT name, fetch_config FROM sources WHERE name='xwatch';"

# 2) 真实抓取（先 dry-run 后正式）
/usr/local/bin/python3 -m moni fetch --source xwatch --dry-run
/usr/local/bin/python3 -m moni fetch --source xwatch
sqlite3 data/news.db "SELECT source, count(*) FROM articles WHERE source='xwatch' GROUP BY source;"
sqlite3 data/news.db "SELECT a.title, a.publisher, t.tag_name FROM articles a JOIN article_tags t ON t.article_id=a.id WHERE a.source='xwatch' LIMIT 10;"

# 3) 手动收录示例（关键验收）
/usr/local/bin/python3 -m moni xwatch ingest https://x.com/xiaoyangX026/status/2083475663496044761
sqlite3 data/news.db "SELECT substr(raw_content,1,60), publisher FROM articles WHERE source='xwatch' AND url LIKE '%2083475663496044761';"
# 期望 raw_content 开头 = "UX很流畅的感觉。但是我觉得最核心需求是要高触达，及时提醒还方便…"

# 4) 去重
# 再次执行 ingest 同 URL → inserted=0

# 5) dev web
scripts/web-8901.sh restart
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8901/api/articles?source=xwatch   # 200
curl -s http://127.0.0.1:8901/api/fetch/jobs | head -c 300                                  # 200 JSON
```

## 6. 通过/失败判定

- **通过**：§3 全部用例绿 + §4 基线满足 + §5 手测输出符合期望
- **失败**：任一单测红、基线回归、示例推文未入库、或 8901 端点非 200 → 停下修/汇报，不允许带红 PR
- 文档验收：STATUS/USER_GUIDE/AGENTS 已更新（源数 7→8、测试数、`xwatch ingest` 用法）
