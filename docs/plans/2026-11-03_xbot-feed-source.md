---
id: 26110301
type: sprint
version: v1.0.66
start_date: 2026-11-03
end_date: 2026-11-03
status: 已实现（PR #172，未合并）
priority: 中
summary: 新增 xbot_feed 源 —— 把 X-bot 每天挑好的内容（微信话题 + 推特圈）经私有 git 仓库导入 moni
tags: [sprint, project-moni, fetcher, xbot-feed]
---

# xbot_feed 源（X-bot 精选桥接）

> **上游方案**：`/workspace/moni-plan/moni-wechat-source-plan.md`（原名 `x_wechat`，
> 最终改名 `xbot_feed`，见该文档开头的「最终决定」）
>
> **X-bot 侧工作流**：`/workspace/moni-plan/wechat-workflow.md` 第 14 节

---

## 1. 目标与边界

把 X-bot 每天挑好的内容导入 moni，作为第 14 个抓取源。

| 项 | 值 |
|---|---|
| 源名 (`sources.name`) | `xbot_feed` |
| DB id (`sources.id`) | `src-xbot-feed` |
| 显示名 (`display_name`) | 「X-bot 精选」 |
| fetcher 类 | `FetcherXbotFeed`（`moni/fetch/sources/xbot_feed.py`） |
| `fetch_interval_min` | 60 |
| schema | 38（无新迁移） |

**不在范围内**：不碰 X API（不花 API 额度）、不新增 articles 列、不合并本 PR
（合入和发版要 Mttt 确认）、不动 `AGENTS.md`（本地 gitignore 文件）。

---

## 2. 命名：为什么不叫 `x_wechat`

1. **源不限微信**。上游 feed 有两个 topic：`wechat`（微信/小程序）和 `xcircle`
   （推特圈固定账号），两个都导。源名带 `wechat` 会误导。
2. **避免与 `xwatch` 语义打架**。moni 已有 `xwatch`（X 关键词监控，自己抓），
   `xbot_feed` 是"导入 X-bot 已经挑好的内容"，两者来源不同。
3. **对得上上游**。上游仓库就叫 `xbot-feed`。

---

## 3. 数据链路

```
X-bot 抓取（/workspace/x-digest/wtoday*.txt + atoday*.txt）
   ↓
xbot-feed 导出器（独立仓库 /workspace/xbot-feed）
   ↓  git commit → 私有仓库 mariusiaowego-commits/xbot-feed
   ↓  feed/YYYY-MM-DD.json + feed/latest.json
moni FetcherXbotFeed
   ↓  <MONI_DATA_DIR>/xbot-feed/ 下的本地 clone
   ↓  git clone（没有）/ git pull --ff-only（有）
   ↓  读 feed/*.json → 按 tweet id 去重 → topic→tag → handle 黑名单
articles 表（source='xbot_feed'）
```

**moni 不持有任何 key**。私有仓库的访问权来自**机器上已有的 git 凭据**
（SSH agent / credential helper）。这跟 `moni/llm/client.py` 的薄壳原则一致：
moni 消费上游的产物，不直接调用需要鉴权的第三方 API。

---

## 4. topic → tag

| topic | 内容 | tag | 理由 |
|---|---|---|---|
| `wechat` | 微信/小程序话题 | `wechat` + `miniprogram` | 就是微信生态话题，小程序是核心议题 |
| `xcircle` | 推特圈（28 个固定中文账号） | `general-tech` + `ai-fintech` | 中文技术/投资圈的一般讨论；其中"AI × 金融/投资"归 `ai-fintech` |

**不新建 tag**。理由两条：

- 新建一个**关键词为空**的 tag 会让 `apply_tag_keyword_filter()` 变成透传，
  且每篇文章会带一个语义空泛的标签 —— migration 037 踩过这个坑。
- 现有 4 个 tag 的关键词并集正好构成入库闸门。

---

## 5. 关键实现：bypass 下按 topic 精确打 tag

这是本 PR 最需要解释的一段设计。

`bypass_keyword_filters: true`（同 `follow_builders`）打开后，
`moni/fetch/tag_filter.py` 的 bypass 分支会给**每一篇**文章合成**全部**绑定
tag 的命中：

```python
# moni/fetch/tag_filter.py:183-202
if _is_source_bypassed(source):
    bound_tags = _load_bound_tag_names_for_source(source)
    for art in articles:
        for tag_name in bound_tags:
            synthetic_hits.append({... "tag_name": tag_name,
                                    "hit_field": "bypass"})
```

源绑了 4 个 tag，所以**每篇**文章都会被打上 `wechat` + `miniprogram` +
`general-tech` + `ai-fintech` —— 推特圈的文章也会被标成"微信生态"。标签串台，
前端按 tag 筛会乱。

`FetcherXbotFeed` 因此覆写了 `dedupe_insert()`：

```python
# 丢掉 bypass 的全量合成
real_hits = [h for h in keyword_hits if h.get("hit_field") != "bypass"]
# 换成按每篇自己 topic 算出来的
topic_hits = [{"article_url": art["url"],
               "keyword": f"(topic:{art['xbot_topic']})",
               "tag_name": tag, "filter_id": None,
               "hit_field": "topic"} for ...]
super().dedupe_insert(articles, real_hits + topic_hits)
```

文章依然**保证带 tag**（AGENTS.md 铁律 3：任何 pass-through 路径都必须显式
合成 hit，否则入库后零 tag、前端筛不到），但带的 tag 是对的。

---

## 6. 去重（三层）

1. **物理约束**：`articles` 表的 `UNIQUE(source, url)` 不动。
2. **tweet id 去重**（源内）：导入状态记在 `<MONI_DATA_DIR>/.xbot_feed_state.json`。
   状态**在入库成功之后**才落盘 —— git 失败或入库失败都不会把条目标记成
   "已导入"，否则这批内容就永久丢了。被闸门挡掉的条目不在 id 列表里，所以
   闸门配置变了以后它们还有机会入库。
3. **跨源软去重**：同一条推文可能已经被 `bestblog-x` / `xwatch` 存过
   （URL 变体：`twitter.com` vs `x.com`、带 `?s=20`）。沿用 `follow_builders`
   的 `normalize_tweet_url()` + 全局 EXISTS 检查
   （`WHERE url = ? OR url LIKE '%status/<id>%'`）。

---

## 7. 其他决定

| 事项 | 决定 | 理由 |
|---|---|---|
| 未知 topic | **不入库**，记 warning | 上游加新 topic 时宁可少收也不要错打 tag |
| handle 黑名单 | 内置 5 个（刷量/营销/灰产号），可在 `fetch_config` 改 | 这是**按作者**的窄兜底，不是内容闸门（内容闸门统一走 `keyword_match` 引擎） |
| git 失败 | 记 `error`，**绝不静默返回 0 篇** | 静默 0 篇跟"今天真的没内容"看起来一模一样，但含义完全相反 |
| 黑名单账号 | `jkghzone` / `sensenxnyz`（代购代付灰产）、`Eason3110188520`（签到打卡接单）、`duizhanggege`（小程序副业营销号）、`HunchoJ01`（关键词误命中的噪音号） | 判据来自 2026-11-03 首日 feed 实测（46 条 wechat + 157 条 xcircle） |
| 频率 | 60 分钟 | 抓取本身很轻（一次 git pull + 读 JSON） |

---

## 8. 改动清单

| 操作 | 路径 | 要点 |
|---|---|---|
| CREATE | `moni/fetch/sources/xbot_feed.py` | fetcher 本体 |
| CREATE | `moni/web/static/icons/sources/xbot_feed.png` | 源图标 |
| CREATE | `scripts/make_xbot_feed_icon.py` | 图标生成脚本 |
| CREATE | `tests/test_xbot_feed_fetch.py` | 27 条离线测试 |
| CREATE | `tests/fixtures/xbot_feed/` | 2026-11-03 首日真实产出的样本 |
| MODIFY | `moni/fetch/sources/__init__.py` | 注册 fetcher |
| MODIFY | `config/sources.yaml` | 加条目（interval 60，enabled true） |
| MODIFY | `moni/db/seed_config.py` | source + 4 个 tag 绑定 + `bypass_keyword_filters: true` |
| MODIFY | `moni/web/routes/config_sources.py` | `_FETCH_CONFIG_DEFAULTS` 加默认值 |
| MODIFY | `moni/web/static/js/app.js` | icon / label / color / sortOrder |
| MODIFY | `moni/obsidian_push.py` | `WECHAT_SOURCES` + 来源标签 |
| MODIFY | `README.md` / `STATUS.md` / `USER_GUIDE.md` | 源表与状态同步 |

---

## 9. 测试

```bash
uv venv --python 3.12 .venv && uv sync --extra dev
.venv/bin/python -m pytest tests/ -p no:cacheprovider -q
```

**结果：1414 passed / 2 skipped**（基线 1387 + 新增 27，无退步）。

新测试全程离线：git 操作全在 `tmp_path` 的本地 bare repo 上跑，不联网，
不硬编码 `/Users` 路径。覆盖解析、topic→tag、id 去重、重复导入幂等、
跨源软去重、黑名单、git 失败报错、clone vs pull 分支、bypass 按 topic
打 tag，以及 bypass 不泄漏到别的源的回归。

---

## 10. Mac 上首次运行

**这台机器的 git 必须能访问私有仓库 `mariusiaowego-commits/xbot-feed`。**

```bash
git ls-remote https://github.com/mariusiaowego-commits/xbot-feed.git
```

拉不到时 fetcher 记 `error`（`git clone/pull failed — cannot read upstream feed`），
**不会静默返回 0 篇** —— 所以第一次跑看到 error，先查 git 凭据，不是 moni 的 bug。

clone 落在 `<MONI_DATA_DIR>/xbot-feed/`，导入状态在
`<MONI_DATA_DIR>/.xbot_feed_state.json`。换位置改 `fetch_config.clone_dir`。
