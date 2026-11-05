# sprint-30 / v1.0.66 — xbot_feed 真实运行记录（RUN.md）

- **日期**: 2026-11-05
- **代码**: `feat/sprint-30-xbot-feed-migration` @ `507c2b2`（基线 `e53956c`）
- **上游**: `mariusiaowego-commits/xbot-feed`（私有仓库），导出 `feed/YYYY-MM-DD.json`
- **涉及文件**: `2026-11-03.json`、`2026-11-04.json`、`2026-11-05.json`（各 1 份）

> **`RUN.md` 是 sprint-30 新起的命名** —— 仓库里此前没有任何 `RUN.md`
> （`find docs/sprint-evidence -name "RUN.md"` → 0 命中），
> `sprint-27/` 里只有 `AUDIT.md`。本 sprint 起约定：
> `RUN.md` = 真实运行记录（人读），`*.json` = 机器可读统计。

> **⚠️ 数字的适用范围**：下面用的是**测试关键词**（§3 列出）。
> **真实库的关键词不同 → 条数就不同。** 这里的**机制**是真的，
> **数字不是承诺**。任何人拿 71 当基线都是错的。

---

## 1. 环境

一次性隔离环境，**没碰 Mttt 的真实库**：

- `MONI_DATA_DIR=/tmp/xbot-evidence/data`（临时目录）
- `MONI_DB_PATH=/tmp/xbot-evidence/data/news.db`
- 上游用**本地 bare git 仓库**代替（`git init` + 提交 3 份 feed + `git clone --bare`），
  再预先 clone 到 `<data_dir>/xbot-feed` —— 因为 fetcher 的 `repo_url` 校验
  （v1.0.66 审计 B-6）只接受 `https://github.com/<owner>/<repo>`，
  **不能**指向本地路径。预置 clone 之后 `_sync_clone()` 只跑
  `git pull --ff-only`，解析到本地 `origin`，全程**无网络、无凭据**。
- 抓取逻辑一行没改（本次是文档回填 + 迁移）。

### 1.1 命令原文

```bash
# 1. 建库（fresh DB → migrate + seed）
MONI_DATA_DIR=/tmp/xbot-evidence/data \
MONI_DB_PATH=/tmp/xbot-evidence/data/news.db \
python -m moni db init

# 2. 写入测试关键词 + 预置 clone（见 §1）

# 3. 抓取
MONI_DATA_DIR=/tmp/xbot-evidence/data \
MONI_DB_PATH=/tmp/xbot-evidence/data/news.db \
python -m moni fetch --source xbot_feed

# 4. 再跑一次（幂等）
... python -m moni fetch --source xbot_feed
```

---

## 2. 三天的实测数字

### 2.1 逐日只读重放（`keyword_match.keyword_matches` + fetcher 的 `_truncate_title`）

| 文件 | 导出 | topic 分布 | 黑名单 | 关键词未命中 | **入库** | tag 分布 | 零 tag | 跨 topic 误打 |
|---|---:|---|---:|---|---:|---|---:|---:|
| `2026-11-03.json` | 203 | wechat 46 / xcircle 157 | 6（wechat 6） | 126（wechat 9 / xcircle 117） | **71**（wechat 31 / xcircle 40） | wechat 28 / miniprogram 21 / general-tech 37 / ai-fintech 7 | **0** | **0** |
| `2026-11-04.json` | 188 | wechat 83 / xcircle 105 | 1（wechat 1） | 72（wechat 21 / xcircle 51） | **115**（wechat 61 / xcircle 54） | wechat 46 / miniprogram 45 / general-tech 54 / ai-fintech 1 | **0** | **0** |
| `2026-11-05.json` | 188 | wechat 20 / xcircle 168 | 0 | 102（wechat 3 / xcircle 99） | **86**（wechat 17 / xcircle 69） | general-tech 68 / wechat 16 / miniprogram 5 / ai-fintech 2 | **0** | **0** |
| **三天合计** | **579** | — | **7** | **300** | **272** | — | **0** | **0** |

> **2026-11-03 那一行与 plan §1.2 的表逐项吻合**（203 / 46+157 / 6 / 126 / 71 /
> 93 tag 计数 / 0 零 tag / 0 跨 topic）—— 独立复算，数字可复现。

**"tag 分布"是多 tag 各计一次**：11-03 的 71 篇共挂 93 个 tag
（28 + 21 + 37 + 7）—— 说明有 22 篇挂了 2 个 tag。

**"跨 topic 误打"= 0** 的判据：wechat topic 的条目**不得**挂
`general-tech` / `ai-fintech`，xcircle topic 的条目**不得**挂
`wechat` / `miniprogram`。三天都是 0 —— 候选 tag 映射没有被越过。

### 2.2 端到端实跑（真 fetcher，真入库）

```
[xbot_feed]  fetched=579  parsed=270  inserted=270  parse_errors=0
Summary: 270 new article(s) inserted from 1 source(s), 0 skipped, 579 total fetched.
```

| 检查点 | 值 |
|---|---|
| 抓到的原始条目 | 579（三天合计） |
| **实际入库** | **270** |
| 解析错误 | 0 |
| **零 tag 文章** | **0**（AGENTS.md 铁律 3：入库必带 tag） |
| `article_tags` 分布 | wechat 89 / general-tech 158 / miniprogram 71 / ai-fintech 9（合计 327） |
| `article_keyword_hits` 分布 | 与 `article_tags` **完全一致**（89/158/71/9） |
| **rerun 入库** | **0** |

> **为什么实跑 270 而重放是 272？**
> 重放是逐文件独立统计，**跨文件的重复 tweet id 各算一次**；
> 实跑走的是 fetcher 的**全局去重**（tweet id 去重 + 与
> `bestblog-x` / `xwatch` 的跨源软去重），所以少了 2 条重复。
> **实跑的 270 才是真实入库数。**

### 2.3 rerun = 0（为什么）

```
[xbot_feed]  fetched=309  parsed=0  inserted=0  parse_errors=0
Summary: 0 new article(s) inserted from 1 source(s), 0 skipped, 309 total fetched.
```

- **已入库的 270 条**记进了状态文件 `.xbot_feed_state.json` 的 `imported_ids`
  → 第二轮不再拉。
- **被关键词挡掉的条目不写进 state**（`_round_ids` 只收真正入库的）→
  用户在 `/config` 改完关键词，下一轮它们**还能进来**。
- `fetched=309` 是第二轮读到的**文件里的原始条目数**（含 `latest.json`
  与当天文件的重复，靠 tweet id 去重），不是新增数。

---

## 3. 测试关键词（关键 —— 必须写进证据文件）

| tag | 关键词 |
|---|---|
| `wechat` | 微信 / WeChat / 公众号 / 视频号 / 微信支付 |
| `miniprogram` | 小程序 / mini program / miniprogram |
| `general-tech` | AI / 大模型 / LLM / 开源 / Agent / 模型 |
| `ai-fintech` | 量化 / 投资 / A股 / 基金 / 金融 |

**Mttt 真实库里的关键词不同 → 条数会不同。** 机制一样，数字不是承诺。

---

## 4. 闸门与状态的稳定性（三天数据）

三天分别代表三种 topic 分布（wechat 46 / 83 / 20），实跑结果：

| 机制 | 观察 |
|---|---|
| **handle 黑名单** | 只命中 wechat topic（7 条全部是 wechat），xcircle 0 —— 与上游的账号性质一致 |
| **topic → 候选 tag** | 三天的 tag 分布都**没有跨 topic** —— 候选映射稳定 |
| **零 tag 文章** | 三天都是 **0** —— 命中即带 tag，闸门在工作 |
| **状态文件** | rerun 入库 0 —— 已入库的不重复拉，未入库的保持可选 |

**"126 条被过滤"（11-03）的正确读法**：不是"命中率低"，是**闸门在工作** ——
那 126 条没命中**候选 tag 当前关键词**（xcircle 117 条占大头，因为
`general-tech` / `ai-fintech` 的测试关键词是 AI×金融向的）。
用户改完关键词它们下一轮还能进。

---

## 5. 机器可读统计

见同目录 `xbot-feed-import-stats.json`。

---

## 6. 相关测试与实测

| 项 | 结果 |
|---|---|
| 全量 pytest | **1486 passed / 2 skipped**（基线 1456 + 30） |
| 日志 | `docs/sprint-evidence/sprint-30/pytest-full.txt` |
| 真实库副本上的 039 迁移实测 | `docs/sprint-evidence/sprint-30/realdb-migrate.md` |

---

## 7. 复算方法

```python
# 逐条重放（只读，不入库）
from moni.fetch.keyword_match import keyword_matches          # 唯一匹配引擎
from moni.fetch.sources.xbot_feed import _truncate_title      # title 走这里
#   title  = _truncate_title(text, handle)
#   content = text + "\n\n[metrics]"
#   for tag in topic 的候选 tag:
#       for kw in 该 tag 当前关键词:
#           if keyword_matches(kw, title=title, body=content, field="title+content"):
#               hit_tags.append(tag); break
```

**必须走 `_truncate_title`**（截断到 80 字符首行）——
直接拿 `text` 当 title 匹配，`ai-fintech` 会得到 **9** 而不是 **7**。
这个坑 plan §1.2 的注释里已经记过一次。
