# TECH-SPEC: x-keyword-watch (v1.0.11)

> 项目: moni · 创建: 2026-08-02 · 分支: `feature/v1.0.11-x-keyword-watch` · 状态: 可执行
> 本文件是给另一个 agent（MiniMax-M3）1:1 执行的完整规格。**只改本文件列出的文件，不做规格外重构。**

## 0. 执行者须知（先读）

### 0.1 环境

- 项目根：`/Users/mt16/dev/moni`（当前分支 `feature/v1.0.11-x-keyword-watch`，已创建）
- Python：**必须用 3.12**：`/usr/local/bin/python3`（系统默认 python3 是 3.14，缺 yaml 依赖，不可用）
- 任何命令前：`set -a && source .env && set +a`（尤其 `db seed-config`，否则 wechat token 变占位符）
- dev 验证端口：8901（`scripts/web-8901.sh restart`）；**8900 是 prod，本 sprint 不重启**
- 工作区已 dirty：`STATUS.md` 是用户自己的修改，**不要覆盖/回滚**（收尾更新时在其上追加）
- 测试基线：`pytest tests/` = **439 pass + 2 skip**（v1.0.10）。任何新代码不得导致回归
- 网络受限时：DDG / syndication / bestblogs 请求需申请联网（只读 GET，无 key）
- 本 feature 无需新 API key、无需 migration（`sources.fetch_config` 列已存在，schema v22 不变）

### 0.2 关键代码事实（已验证，勿凭记忆）

- scheduler 读 `config/sources.yaml`（字段：`source_name/type/feed_url/fetch_interval_min/enabled/crawler_type`）；DB `sources` 表由 `moni/db/seed_config.py` 的 `seed()` 维护。**新源必须两处都注册**，否则 `moni fetch xwatch` 找不到源或注册不到 fetcher
- fetcher 注册：`@register_fetcher`（`moni/fetch/base.py`），`moni/fetch/sources/__init__.py` 顶部 import 触发
- 管线（`BaseFetcher.run`）：`fetch()` → `parse()` → HTML cleaner → `apply_keyword_filters`（黑/白名单）→ `apply_tag_keyword_filter`（tag 词门控）→ `dedupe_insert`（INSERT OR IGNORE on (source,url)，tag 由 keyword_hits 自动写入 article_tags）
- `tag_definitions` 已有 `wechat`（含 微信/微信小程序/微信公众号…）与 `miniprogram`（含 小程序/订阅消息/服务通知/搜一搜/微信小程序）关键词 → xwatch 绑定这两个 tag 即自动门控 + 打 tag
- fetch_jobs 进度回调签名：`cb(total_feeds, done_feeds, current_feed, articles_so_far)`
- 测试可用 `unittest.mock.patch` 或 monkeypatch httpx（参考 `tests/test_bestblogs.py`）

## 1. 数据流

```
sources.fetch_config.queries (list[str])
  → FetcherXWatch.fetch()
      → xwatch_discovery.search_tweet_urls(query)   # DDG HTML → 推文 URL 列表
      → xwatch_syndication.fetch_tweet(tweet_id)    # syndication JSON → dict
  → parse()                                          # canonical article dict
  → BaseFetcher.run() 现有链路
      → source_keyword_filters（xwatch 无黑名单，pass）
      → tag_keyword_filter（wechat/miniprogram 词命中 → 保留 + keyword_hits）
      → dedupe_insert → articles + article_tags + article_keyword_hits
  → 现有 LLM 摘要 / business_value / brief 链路自动生效
```

手动路径：`moni xwatch ingest <url|tweet_id>` → `xwatch_syndication.fetch_tweet` → 同 parse + dedupe_insert（带 keyword_hits）。

## 2. 要创建/修改的文件清单（精确）

| 动作 | 路径 |
|---|---|
| 新建 | `moni/fetch/sources/xwatch.py`（FetcherXWatch） |
| 新建 | `moni/fetch/xwatch_discovery.py`（DDG 搜索） |
| 新建 | `moni/fetch/xwatch_syndication.py`（syndication 抓取 + 规范化） |
| 修改 | `moni/fetch/sources/__init__.py`（import xwatch） |
| 修改 | `config/sources.yaml`（加 xwatch 条目） |
| 修改 | `moni/db/seed_config.py`（source 行 + source_tags + 默认 fetch_config） |
| 修改 | `moni/fetch/health.py`（feed_url 为空时 skip） |
| 修改 | `moni/cli/main.py`（`xwatch ingest` 子命令） |
| 新建 | `tests/fixtures/` 4 个 fixture + `tests/test_xwatch_discovery.py` + `tests/test_xwatch_syndication.py` + `tests/test_xwatch_fetch.py` + `tests/test_xwatch_cli.py` |
| 收尾 | `STATUS.md`、`USER_GUIDE.md`、`AGENTS.md`（源数 7→8、测试数）、`vibe-coding-log.md`、`HANDOFF.md` |

**不要改**：`moni/fetch/base.py`、`moni/db/repository.py`、`moni/db/migrations/*`、8900 相关。

## 3. config/sources.yaml（追加）

```yaml
- source_name: xwatch
  type: x
  feed_url: ''
  fetch_interval_min: 30
  enabled: true
  crawler_type: x
```

## 4. moni/db/seed_config.py（3 处修改）

### 4.1 `_DEFAULT_SOURCES` 末尾追加

```python
{
    "id": "src-xwatch",
    "name": "xwatch",
    "display_name": "X 关键词监控",
    "source_type": "x",
    "url": "",
    "fetch_interval_min": 30,
    "notes": "按关键词发现 X 推文（DDG 发现 + syndication 抓全文，v1.0.11）。关键词在 fetch_config.queries。",
    "fetch_config": json.dumps({
        "queries": [
            "微信小程序 触达",
            "微信小程序 服务通知",
            "微信小程序 提醒",
        ],
        "max_results_per_query": 20,
        "request_delay": 2.5,
        "search_timeout": 20,
        "syndication_timeout": 20,
    }, ensure_ascii=False),
},
```

注意（已核实）：`seed_config.py` 的 `INSERT INTO sources`（约 L296-320）当前**不含** `fetch_config` 列。必须同步修改：
1. 列清单加 `fetch_config`，VALUES 加 `?`，参数用 `src.get("fetch_config")`；
2. `ON CONFLICT(id) DO UPDATE SET` 加 `fetch_config = excluded.fetch_config`；
3. 老源 dict 不带该 key → 保持 NULL（不破坏现状）。

改完验证幂等：连跑两次 `moni db seed-config`，`SELECT fetch_config FROM sources WHERE name='xwatch'` 值不变。

### 4.2 `_DEFAULT_SOURCE_TAGS` 追加

```python
("src-xwatch", "wechat"),
("src-xwatch", "miniprogram"),
```

### 4.3 黑名单

`_DEFAULT_FILTERS` **不新增** xwatch 条目（关键词搜索已定向；腾讯相关词不能进黑名单）。在 `_DEFAULT_FILTERS` 上方注释说明原因。

## 5. moni/fetch/sources/__init__.py（追加一行）

```python
from . import xwatch       # noqa: F401 — registers FetcherXWatch
```

## 6. moni/fetch/health.py（1 处修改）

`async_check` 调用前（`check_all_sequential` / 列表生成处）过滤空 `feed_url`：`if not cfg.get("feed_url"): continue`（或等价），避免对空 URL 发请求。不改 `async_check` 本身。

## 7. moni/fetch/xwatch_discovery.py（新建，无 DB 依赖）

```python
DEFAULT_TIMEOUT = 20
DDG_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
TWEET_URL_RE = re.compile(r"x\.com/([A-Za-z0-9_]+)/status/(\d+)")
```

### `search_tweet_urls(query: str, *, max_results: int = 20, timeout: int = DEFAULT_TIMEOUT) -> list[str]`

行为：
1. 构造搜索词 `f"site:x.com {query}"`，URL 用 `urllib.parse.urlencode({"q": ...})`
2. `httpx.get(DDG_URL, params=..., headers={"User-Agent": USER_AGENT}, timeout=timeout)`，`resp.raise_for_status()`
3. `_extract_tweet_urls(resp.text, max_results)`；若为空且页面含 `anomaly`/`Anomaly`（风控页），`time.sleep(5)` 重试一次；仍失败 → `logger.warning("[xwatch] ddg anomaly for %r", query)`，返回 `[]`（**不抛异常**）
4. 任何网络异常 → `logger.warning(...)`，返回 `[]`

### `_extract_tweet_urls(html: str, max_results: int = 20) -> list[str]`

行为：
1. `TWEET_URL_RE.findall(html)` → 组装 `https://x.com/{user}/status/{id}`
2. 保序去重；截断到 `max_results`
3. 不解析 DDG 的 `/uddg=` 重定向（页面里通常已含明文 x.com 链接；正则兜底即可）

## 8. moni/fetch/xwatch_syndication.py（新建，无 DB 依赖）

```python
SYNDICATION_URL = "https://cdn.syndication.twimg.com/tweet-result"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
STATUS_URL_RE = re.compile(r"x\.com/(?:[^/]+)/status/(\d+)")
```

### `tweet_id_from_url(url: str) -> str | None`

`STATUS_URL_RE.search(url)` → `group(1)`；无 → `None`。若输入是纯数字串，直接返回（兼容 `ingest 2083475663496044761`）。

### `fetch_tweet(tweet_id: str, *, timeout: int = 20) -> dict | None`

行为：
1. `httpx.get(SYNDICATION_URL, params={"id": tweet_id, "lang": "zh", "token": "a"}, headers={"User-Agent": USER_AGENT}, timeout=timeout)`
2. 200 → `resp.json()`；404（`__typename == "TweetWithheldError"` 或 JSON 含错误）→ 返回 `None`；其他状态/网络异常 → `logger.warning`，返回 `None`（**不抛异常**）
3. 顶层无 `text` 或 `id_str` → `None`

### `tweet_to_article(data: dict, *, source: str = "xwatch", fallback_url: str | None = None) -> dict | None`

规范化为 canonical dict（与 bestblogs parse 输出一致）：

```python
{
    "title": 首行（text.splitlines()[0]，截断 80 字符）,
    "url": f"https://x.com/{screen_name}/status/{id_str}",  # screen_name 取 data["user"]["screen_name"]；缺失用 fallback_url
    "source": source,
    "published_at": created_at 归一化 "YYYY-MM-DDTHH:MM:SS+00:00"（把 "Z" 替换为 "+00:00" 后 fromisoformat().isoformat()；解析失败置 None）,
    "content": data["text"],  # 全文，不 append 互动信息（保持 LLM 摘要干净）
    "publisher": f"@{screen_name}" if screen_name else None,
}
```

`data["user"]` 缺失或 `text` 空 → 返回 `None`。

## 9. moni/fetch/sources/xwatch.py（新建）

```python
_DEFAULTS = {
    "queries": ["微信小程序 触达", "微信小程序 服务通知", "微信小程序 提醒"],
    "max_results_per_query": 20,
    "request_delay": 2.5,
    "search_timeout": 20,
    "syndication_timeout": 20,
}

@register_fetcher
class FetcherXWatch(BaseFetcher):
    source_name: str = "xwatch"
```

### `_load_config() -> dict`

照抄 `FetcherBestBlogs._load_config` 的模式（`SELECT fetch_config FROM sources WHERE name='xwatch'`，合并 `_DEFAULTS`）。可 `from .bestblogs import FetcherBestBlogs` 复用其私有方法或复制实现——**优先复用**：`FetcherBestBlogs._load_config(self)` 通过实例方法调用即可（同包内复用，无需改 bestblogs 代码）。

### `fetch() -> list[dict]`

精确流程：
1. `cfg = self._load_config()`；`queries = cfg["queries"]`（兼容 list[str]；非法/空 → log warn，返回 `[]`）
2. `seen_urls: set[str] = set()`，`articles: list[dict] = []`
3. `progress_callback(total_feeds=len(queries), done_feeds=0, current_feed=None, articles_so_far=0)`（若设置了）
4. 对每个 query（index i）：
   - `urls = search_tweet_urls(query, max_results=cfg["max_results_per_query"], timeout=cfg["search_timeout"])`
   - 对每个 url：`tid = tweet_id_from_url(url)`；`tid in seen_ids` 跳过；`data = fetch_tweet(tid, timeout=cfg["syndication_timeout"])`；`raw = {"data": data, "url": url}`；`articles.append(raw)`；`seen_ids.add(tid)`
   - `i > 0 and cfg["request_delay"] > 0 → time.sleep(cfg["request_delay"])`
   - `progress_callback(total_feeds=len(queries), done_feeds=i+1, current_feed=query, articles_so_far=len(articles))`
5. `date_from/date_to` 过滤：复用 `from .bestblogs import _in_date_range`（对 raw 里已 parse 的 published_at 过滤，见下）
6. 返回 raw 列表；`logger.info("[xwatch] done — %d queries, %d raw articles", ...)`

raw 元素形态：`{"data": <syndication dict 或 None>, "url": <发现/输入的 URL>}`；`data is None` 的 raw 在 parse 返回 None（跳过）。`published_at` 供日期过滤：可在 fetch 内先调用一次 `tweet_to_article` 取 published_at，或简化——**MVP 允许跳过 date_from/date_to 的二次过滤**（fetch 命令的 `--from/--to` 对 xwatch 不生效，文档注明即可）；若实现，用 `tweet_to_article(data)["published_at"]`。

### `parse(raw) -> dict | None`

`raw["data"]` 为 None → `None`；否则 `tweet_to_article(raw["data"], source=self.source_name, fallback_url=raw.get("url"))`。

## 10. moni/cli/main.py（追加 `xwatch` 子命令）

```python
xwatch_parser = SUB.add_parser("xwatch", help="X 关键词监控工具")
xwatch_sub = xwatch_parser.add_subparsers(dest="xwatch_subcmd", required=True)
ingest_parser = xwatch_sub.add_parser("ingest", help="按 URL 或 tweet id 收录单条推文")
ingest_parser.add_argument("url_or_id", help="https://x.com/.../status/{id} 或裸 tweet id")
ingest_parser.add_argument("--dry-run", action="store_true")
```

`cmd_xwatch_ingest(url_or_id, dry_run=False)`：
1. `tid = tweet_id_from_url(url_or_id)`；None → `print("ERROR: 无法解析推文 id"); return 1`
2. `data = fetch_tweet(tid)`；None → `print("ERROR: syndication 抓取失败"); return 1`
3. `art = tweet_to_article(data, fallback_url=url_or_id)`；None → 同上 return 1
4. tag 门控：取 xwatch 绑定 tag 的关键词（复用 `moni.fetch.tag_filter._load_tag_keywords_for_source("xwatch")`），若 `title+content` 命中任一关键词 → `keyword_hits=[{"article_url": art["url"], "keyword": kw, "tag_name": tag, "filter_id": None, "hit_field": "title+content"}]`；全不命中 → `print("SKIP: 未命中 wechat/miniprogram 关键词"); return 0`（不入库）
5. dry-run → 打印文章 dict；否则 `ArticleRepository().dedupe_insert([art], keyword_hits)`，`print(f"inserted={n} url={art['url']}")`；`close_db()`；return 0

在 `main()` 的分发处加 `if args.subcmd == "xwatch": ...` 分支（参考现有 `db` 子命令写法）。

## 11. 边界与错误处理（必须遵守）

| 场景 | 行为 |
|---|---|
| DDG 风控（anomaly 页） | 每个 query 重试 1 次（sleep 5s），仍失败 log warning + 返回 []，继续下一个 query |
| DDG/网络超时 | log warning，返回 []，不抛异常 |
| syndication 404/敏感推文 | 返回 None，parse 跳过，log debug |
| syndication 429 | log warning，返回 None；整体不 sleep 重试（保底 request_delay 已限速） |
| 重复推文（跨 query 撞 URL） | `seen_ids` 去重，只处理一次 |
| 已入库 URL | `dedupe_insert` INSERT OR IGNORE，天然幂等 |
| queries 为空/类型非法 | fetch 返回 []，log warning |
| tag 未命中 | 自动进 tag_keyword_filter 的 basket（既有行为），fetch 日志 dropped_tag_filter 可见 |
| health 检查 | xwatch feed_url 为空 → skip |

## 12. 测试（详见 TEST-PLAN）

fixtures 放 `tests/fixtures/`：
- `ddg_results_sample.html`：真实 DDG 结果页片段（含 3 个 x.com/status 链接 + 1 个重复 + 1 个无关链接 + 1 个 x.com 非 status 链接）
- `ddg_anomaly.html`：含 "Anomaly" 的风控页片段
- `tweet_sample.json`：实测 syndication 响应（含 `text/user/created_at/favorite_count/in_reply_to_*`）
- `tweet_reply_sample.json`：含 `in_reply_to_screen_name` 的回复推文（用示例 2083475663496044761 的字段形态）

测试文件：`tests/test_xwatch_discovery.py`、`tests/test_xwatch_syndication.py`、`tests/test_xwatch_fetch.py`、`tests/test_xwatch_cli.py`。

## 13. 实现顺序（每步有验证命令）

1. 建 fixtures → 验证：文件存在且为合法 JSON/HTML
2. `xwatch_discovery.py` + 单测 → `python3 -m pytest tests/test_xwatch_discovery.py -q` 全绿
3. `xwatch_syndication.py` + 单测 → `python3 -m pytest tests/test_xwatch_syndication.py -q` 全绿
4. `xwatch.py` fetcher + `sources/__init__.py` import → 单测 fetch（mock 网络）→ `pytest tests/test_xwatch_fetch.py -q` 全绿
5. `config/sources.yaml` + `seed_config.py` + `health.py` → `set -a && source .env && set +a && /usr/local/bin/python3 -m moni db seed-config` 成功；`sqlite3 data/news.db "SELECT name, fetch_config FROM sources WHERE name='xwatch';"` 有行
6. CLI `xwatch ingest` + 单测 → `pytest tests/test_xwatch_cli.py -q` 全绿
7. 全量回归：`/usr/local/bin/python3 -m pytest tests/ -q` → 439 + 新增数 pass，2 skip，0 fail
8. 真实抓取（联网）：`/usr/local/bin/python3 -m moni fetch --source xwatch --dry-run`（不写库先看结果）；再正式跑一次，`sqlite3 data/news.db "SELECT source, count(*) FROM articles GROUP BY source;"` 确认 xwatch > 0
9. 手动收录：`/usr/local/bin/python3 -m moni xwatch ingest https://x.com/xiaoyangX026/status/2083475663496044761` → inserted=1；再跑一次 → inserted=0（去重）
10. 8901：`scripts/web-8901.sh restart`，curl 5 个菜单端点 200
11. 收尾文档 + PR（见 §14）

## 14. 收尾与发布（moni 标准流程）

1. 更新 `STATUS.md`（追加 x-keyword-watch 条目，不动用户已有修改）、`USER_GUIDE.md`（新源 + `xwatch ingest` 用法）、`AGENTS.md`（源数 7→8、测试数、schema 不变）、`vibe-coding-log.md`、`HANDOFF.md`
2. `git add` 相关文件 + commit（feature 分支）
3. `git push origin feature/v1.0.11-x-keyword-watch`（代理挂则切 HTTPS remote，owner `mariusiaowego-commits`，中段 5 字符 `o-w-e-g-o`）
4. `gh pr create --base main` → 用户 review → `gh pr merge --squash --delete-branch`
5. tag `v1.0.11`（显式 SHA）force push
6. **8900 重启由用户确认后执行**：`/usr/local/bin/python3 -c "..." &`（用绝对路径命令，不靠 web.sh）

## 15. Stop signals（执行中遇到就停下汇报，不要自行发挥）

- DDG 或 syndication 被大规模风控/403，免费路径实测打不通
- `seed-config` 因 fetch_config 列写入报错且无法在不改 repository 的前提下解决
- 需要登录态才能拿到目标内容
- 改动范围超出本规格（UI、付费 API、新表、多引擎）或需要改 base/repository 公共代码
