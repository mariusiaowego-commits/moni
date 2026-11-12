# 银行同业信源（资讯/新闻向）梳理与纳入 moni 方案

> 日期：2026-11-12 · 类型：调研（research-first，未动业务代码）
> 基线：main @ v1.0.72 · schema v40 · 全文库 5039 篇 · 案例库 424 条
> 全部 URL 与计数为 2026-11-12 curl 实测

---

## 0. 结论先行

需求口径：**① 银行相关「资讯/新闻」（不是公告）② 公众号 ③ 重点中/农/工/建/邮储 ④ 公开稳定 RSS**。

四个问题的实测答案：

1. **五大行官方 RSS 不存在**。工行首页 521KB / 农行 203KB / 建行 2.2KB / 中行 77KB / 邮储 368B，
   **0 个 rss 链接**，新闻列表全是 SPA。五大行内容只能从「媒体 + 公众号」来。
2. **公开稳定 RSS 有，且有一个银行垂直高密度的**：
   **未央网** `https://www.weiyangx.com/feed` — 30 条/页，**银行命中 7/30 = 23%**，
   且**与 moni 已订阅的公众号「未央科技weiyangx」同源**（等于白捡一个公众号的稳定 RSS），
   并支持 `?page=N` 回溯。**东方财富** `rss.eastmoney.com/rss_partener.xml` — 98 条/次，
   实测命中「财政部注资银行保险业新进展 工行、农行、中国人保定增获上交所审核通过」。
3. **公众号没有可用的第三方公开 RSS**。实测 16 个通道：waytomaster 上游停更、rsshub.app 403、
   唯一可用镜像 20 items 后立刻 429（45s 复查仍 429）、其余镜像全超时/403、
   搜狗微信只给标题不给条目。**公众号唯一稳定解 = 自建 RSSHub**（Docker 已装，colima daemon 未启动）。
4. **五大行筛选不用改 schema**：moni 现有 `banking` tag 关键词已含
   中行/BOC/中国银行、工行/ICBC/工商银行、农行/ABC/农业银行、建行/CCB/建设银行、邮储/PSBC/邮储银行，
   「稳定 RSS + 现有闸门」即可自动筛出五大行。

---

## 1. 现状诊断（实测数据）

| 指标 | 实测值 |
|---|---|
| 全文库总量 | 5039 篇 |
| `banking` tag 文章 | 2645 篇 |
| 近 90 天 `banking` 文章 | 1223 篇（bestblogs 739 / mpaypass 407 / huxiu 60 / cn-indie 12 / qbitai 5） |
| 近 90 天含具体银行名的 `banking` 文章 | **36 篇 = 2.9%** |
| 案例库总量 / 来源集中度 | 424 条；**mpaypass 319 + 36kr 105 = 100%** |
| 银行类案例 | 135 条 / 104 家主体（每家 1.3 条） |

**根因**：13 个 active 源没有一个「以银行为主角」；`banking` 的 300+ 关键词是**主题词**
（六大行/监管/宏观），闸门放进来的全是宏观泛金融，不是「某家银行做了什么」。

---

## 2. 公开稳定 RSS（实测 HTTP 200 + items>0），按银行命中率排序

| 源 | RSS 地址 | 条数/次 | 银行命中 | 备注 |
|---|---|---|---|---|
| **未央网**（互联网金融垂直） | `https://www.weiyangx.com/feed` | 30 | **7/30 = 23%** | 实测命中「金融结构变迁下商业银行的角色重塑」「韩国主要银行因黑客事件大幅上调网络安全预算」等；**支持 `?page=N` 回溯**；与公众号「未央科技weiyangx」同源 |
| **东方财富** | `https://rss.eastmoney.com/rss_partener.xml` | 98 | 2/98 | 实测命中「财政部注资银行保险业新进展 工行、农行、中国人保定增获上交所审核通过」；**量最大**，30 分钟轮询 ≈ 日增 4700 条；不支持翻页 |
| **界面新闻** | `https://a.jiemian.com/index.php?m=article&a=rss` | 30 | 1/30 | 综合财经；`cid=` / `page=` 参数实测**无效**（返回同一批） |
| **FT中文网** | `https://www.ftchinese.com/rss/feed` | 20 | 1/20 | 深度报道，境外视角 |
| **第一财经·金融** | `https://www.yicai.com/api/ajax/getlistbycid?cid=53` | 20 | 待筛 | 纯 JSON，字段齐：`NewsTitle/NewsID/NewsNotes/pubDate/url` |
| **雷锋网** | `https://www.leiphone.com/feed` | 21 | 0 | 金融科技垂类 |
| **InfoQ** | `https://www.infoq.cn/feed` | 20 | 0 | 企业级技术 |
| **开源中国** | `https://www.oschina.net/news/rss` | 51 | 0 | 金融开源/银行科技 |
| **钛媒体** | `https://www.tmtpost.com/feed` | 17 | 0 | `?tag=金融` 实测无效 |
| **爱范儿** | `https://www.ifanr.com/feed` | 20 | 0 | |
| **Solidot** | `https://www.solidot.org/index.rss` | 20 | 0 | |
| **雪球热门话题** | `https://xueqiu.com/hots/topic/rss` | 20 | — | 标题在 atom ns，需 `entry/title` 解析 |
| **BBC 中文** | `https://feeds.bbci.co.uk/zhongwen/simp/rss.xml` | 42 | — | |
| **纽约时报中文** | `https://cn.nytimes.com/rss/` | 20 | — | 中资银行出海视角 |
| **美联储** | `https://www.federalreserve.gov/feeds/press_all.xml` | 20 | — | 监管 |
| **欧洲央行** | `https://www.ecb.europa.eu/rss/press.html` | 15 | — | 监管 |

**moni 已有**（不重复造）：36氪、虎嗅、量子位、移动支付网、BestBlogs（375 公众号聚合，
内含「中国金融四十人论坛」「香帅的金融江湖」等金融号，**不含五大行官方号**）、中文独立博客。

---

## 3. 公众号：实测结论与唯一可行路径

### 3.1 第三方公开通道全部不可用（16 个实测）

| 通道 | 实测 | 结论 |
|---|---|---|
| `wechatrss.waytomaster.com`（moni 现用 A/B 号） | 服务本身活着（200 / 476KB）；但内容最新 pubDate = **A 2026-07-22 / B 2026-07-29** | **上游整体停更**（非账号问题），与 `STATUS.md:1186` 记录一致 |
| `rsshub.app` | 403 Cloudflare | 不可用 |
| `rsshub.ktachibana.party`（唯一可用的镜像） | 首测 200/20 items；随后 `/cls/telegraph`、`/cls/depth/1032` 均 **429**，45s 后复查仍 429 | 公共实例限流，**不能作生产依赖** |
| `rsshub.rssforever.com` / `feeded.xyz` / `iamtsi` / `doubanio` / `ohmygpt` / `19940731` / `api.allorigins.win` | 全部 000 超时或 403 | 不可用 |
| `wechat2rss.xyz` / `wxrss.top` / `wxrss.cn` / `wechat2rss.xlab.app` | 404 / 超时 | 不可用 |
| `feeddd.org` | 200 但根路径无条目 | 不可用 |
| **搜狗微信** | 五大行公众号名都能搜到（title 正确），但结果页 10800B 只给标题**不给条目**；`type=2` 文章搜索 0 条 | 需验证码/cookie，**不可靠** |
| `werss.app` | 200 落地页 | 付费服务，需账号 |

### 3.2 唯一稳定解：自建 RSSHub

- 本机已有 `/opt/homebrew/bin/docker`，但 **colima daemon 未运行**
  （`docker info` → `dial unix /Users/mt16/.colima/default/docker.sock: no such file`）。
- 落地步骤：① 启 colima → ② `docker run -d -p 1200:1200 diygod/rsshub`
  → ③ 用户扫码提供 `WECHAT_COOKIE` → ④ 路由 `/wechat/mp/:biz`（biz = 五大行公众号 id）。
- 产出 = 五大行官方公众号一手资讯，这是任何公开 RSS 都拿不到的部分。

---

## 4. 纳入 moni 的落地机制

### 4.1 获取形态与依赖约束

| 形态 | moni 现状 | 结论 |
|---|---|---|
| RSS 2.0 | `xml.etree.ElementTree` 已在用（`qbitai.py` / `kr_rss.py` / `huxiu.py`） | 零新增依赖，抄 `qbitai.py` 骨架 |
| HTML 列表+详情 | `httpx` + `beautifulsoup4` 已在 `pyproject.toml` | 零新增依赖 |
| JSON API | `httpx` 即可 | 零新增依赖 |
| SPA / 反爬站 | 已有 `moni/fetch/chrome_daemon.py` | 备选 |

### 4.2 每加一个源 6 步

1. `config/sources.yaml` → `source_name` / `type` / `feed_url` / `fetch_interval_min` / `enabled`
2. `moni/fetch/sources/<x>.py` → `class FetcherX(BaseFetcher)`：`source_name` + `fetch()` + `parse()`，`@register_fetcher` 自注册
3. `moni/db/seed_config.py` → `_DEFAULT_SOURCES` 加一行
4. 同文件 `_DEFAULT_SOURCE_TAGS` → 绑 tag（tag 必须已在 `_DEFAULT_TAGS` 定义，否则 FK 崩）
5. 同文件 `_DEFAULT_FILTERS` → 可选黑名单（title+content）
6. 同文件 `_DEFAULT_TAGS` → 若需新 tag，在此定义并写 keywords

验证：

    set -a && source .env && set +a
    /usr/local/bin/python3 -m moni fetch --source <x> --dry-run
    /usr/local/bin/python3 -m pytest tests/ -p no:cacheprovider -q

**关键词闸门**（`moni/fetch/keyword_match.py`）：匹配只能走该模块；纯 ASCII 按词边界，
≤2 汉字要求命中标题或正文重复 ≥N 次；规则收紧前先跑 `moni keyword-audit`。

**案例库链路**：`CaseRepository.get_pending_articles()`（`moni/cases/repository.py:405-446`）
取 `raw_content` ≥100 字且无 cases 行的文章，可按 `--source` 定向跑 ——
新源接通后跑 `moni cases extract --source <新源>` 即进案例库，无需改抽取逻辑。

---

## 5. 三个可选方案（需用户选）

### 方案 A — 稳定 RSS 批量接入（今天可上，零依赖、零外部账号）

接入 §2 的 16 个源，用**新增 tag `peer-banks`**（五大行名 + 动作词）过滤，
把「泛财经」与「同业动作」分开。

- 预期：轮询增量日增数千条；五大行命中量按实测密度约 **日几十条**（东财 2/98 + 未央 7/30 + 界面 1/30 + FT 1/20 叠加）。
- 注意：界面/东财 RSS **不支持翻页**，历史回溯只能靠未央网 `?page=N`。

### 方案 B — 公众号（需用户配合，约 30 分钟）

启 colima → 自建 RSSHub → 用户扫码给 cookie → 接五大行 5 个官方公众号
+「金融科技研究」「财金吃瓜党」「未央科技」（后三个正是 waytomaster 停更前订阅的高价值号）。

### 方案 C — A + B 并行（推荐）

A 立刻上线，B 在后台做（colima 起服务 + 扫码 + 路由调试）。

---

## 6. 风险与红线

- **不改现有源行为**：新增源为并行增量，不动 13 个现有源的 tag/关键词绑定。
- **新 tag 必须先在 `_DEFAULT_TAGS` 定义**（否则空库 FK 崩）。
- **`ON CONFLICT` 不覆盖用户已配 keywords**。
- **公众号账号操作**（扫码登录、订阅/退订、付费）需用户当轮点头，不自行执行。
- **RSS 轮询限速**：媒体源建议 `fetch_interval_min >= 15`，避免被封。
- **自建 RSSHub 依赖微信 cookie**，会过期，需有维护预案；cookie 属凭证，不得入库。

---

## 7. 复现命令

    # RSS 有效性 + 条数
    curl -sS -A "Mozilla/5.0" "https://www.weiyangx.com/feed" | grep -c '<item>'

    # 未央网回溯（page 参数有效）
    curl -sS -A "Mozilla/5.0" "https://www.weiyangx.com/feed?page=2" | grep -c '<item>'

    # 东方财富（量最大的中文财经 RSS）
    curl -sS -A "Mozilla/5.0" "https://rss.eastmoney.com/rss_partener.xml" | grep -c '<item>'

    # 五大行官网无 RSS 的证据
    curl -sSL -A "Mozilla/5.0" "https://www.icbc.com.cn/" | grep -io 'href="[^"]*rss[^"]*"' | wc -l

    # moni 侧现状
    sqlite3 data/news.db "SELECT source, COUNT(*) FROM cases c JOIN articles a ON a.id=c.article_id GROUP BY 1;"

---

## 8. 待用户决策

1. **方案 A / B / C 选哪个？**（建议 C）
2. **`peer-banks` tag 是否新建**（建议新建，关键词 = 五大行名 + 动作词）
3. **公众号路线**：自建 RSSHub（需你扫码）还是先只做 A？
4. **RSS 源取舍**：16 个全接，还是只接命中率高的 4 个（未央网 / 东财 / 界面 / FT中文）？
