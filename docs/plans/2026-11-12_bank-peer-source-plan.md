# 银行同业信源梳理与纳入 moni 方案

> 日期：2026-11-12 · 类型：调研（research-first，未动业务代码）
> 基线：main @ v1.0.72 · schema v40 · 全文库 5039 篇 · 案例库 424 条
> 所有 URL 与计数均为 2026-11-12 实测（curl 直连，见各条「实测」列）

---

## 0. 结论先行

**「同业追踪」价值没体现，不是抓得少，是抓的压根不是同业。**

近 90 天 1223 篇 `banking` 文章里，只有 **36 篇（2.9%）** 正文出现具体银行名
（招行/工行/宁波/江苏/微众/网商）；案例库 424 条 **100% 来自 2 个源**
（mpaypass 319 + 36kr 105）；135 条银行案例横跨 104 家银行主体 —— 数量看着有，
但每家银行平均只有 1.3 条，且都来自支付/创投媒体，**不是银行的官方动作流**。

缺的是这 4 类源（现状一个都没有）：

1. **监管原文**（处罚、政策、窗口指导）—— 金融监管总局 / 央行
2. **行业自律与统计**（银行业协会规范、数据、月度动态）
3. **上市银行公告与财报**（巨潮 / 交易所）
4. **银行专属媒体与研究**（金融时报、第一财经金融频道、银行业协会「银行机构动态」）

**已实测可达、可立即接入的（9 类）**：央行 RSS、银行业协会 7 个栏目、金融时报、
第一财经 JSON API、巨潮公告 API、外汇局、支付清算协会、Fed RSS、ECB RSS。

**当前无法直接接入的（3 类，需换通道）**：金融监管总局（SPA + 静态 JSON 路径未定位）、
各行官网新闻（SPA）、微信公众号（waytomaster 上游已停更，RSSHub 公共实例被 Cloudflare 挡）。

---

## 1. 现状诊断（实测数据）

| 指标 | 实测值 | 出处 |
|---|---|---|
| 全文库总量 | 5039 篇 | `SELECT COUNT(*) FROM articles` |
| `banking` tag 文章 | 2645 篇 | `article_tags` |
| 近 90 天 `banking` 文章 | 1223 篇（bestblogs 739 / mpaypass 407 / huxiu 60 / cn-indie 12 / qbitai 5） | `articles` |
| 近 90 天含具体银行名的 `banking` 文章 | **36 篇 = 2.9%** | 同上 + `raw_content LIKE` |
| 案例库总量 | 424 条 | `cases` |
| 案例来源集中度 | **mpaypass 319 + 36kr 105 = 100%** | `cases JOIN articles` |
| 银行类案例 | 135 条 / 104 家不同主体 | `subject LIKE '%银行%'` |

**根因**：`config/sources.yaml` 13 个 active 源里，泛科技/支付/创投类占绝对多数，
没有任何一个源是「以银行为主角」的。`tag_definitions.banking` 的 300+ 关键词
（六大行/股份行/城商行/外资行/监管/宏观…）是**主题词**而非**主体词**，
所以闸门放进来的是「宏观泛金融」，不是「某家银行做了什么」。

---

## 2. 信源清单（分层，全部实测）

### 2.1 监管与官方（最高价值，同业行为的风向标）

| # | 信源 | 接入形态 | 实测 | 价值 |
|---|---|---|---|---|
| R1 | **中国人民银行 · 新闻 RSS** | RSS 2.0 | `GET https://www.pbc.gov.cn/goutongjiaoliu/113456/2986536/index.html` → 200，6 items，最新 2026-11-11 | 货币政策执行报告、行长会议、对外监管磋商。标题实测：「中国人民银行与印度尼西亚银行…」「2026年第三季度中国货币政策执行报告」 |
| R2 | **央行其他栏目页** | HTML | `https://www.pbc.gov.cn/rmyh/3963412/index.html` → 200，44KB，21 个日期型链接；`/jinrongxinxizhongxin/148526/index.html` → 89 链接 | 金融司/新闻稿原文，服务端渲染，可直接解析 |
| R3 | **国家金融监督管理总局** | SPA | 首页 200 但仅 237B；`ItemList.html?itemPId=914&itemId=915` → 200 / 4012B / **0 个日期链接** | 行政处罚、监管动态 —— 最高价值但当前抓不到，见 §3.1 |
| R4 | **国家外汇管理局** | HTML 列表+详情 | 列表 `https://www.safe.gov.cn/safe/whxw/index.html` → 200 / 22KB / 20 个日期链接；详情 `https://www.safe.gov.cn/safe/2026/1111/28084.html` → 200 / 17.6KB，**正文段落可解析**，含「索引号 000014453-2026-00846 / 发布日期 2026-11-11 / 来源 国家外汇管理局」+ 全文 | 外汇/跨境业务政策，同业跨境产品对标 |
| R5 | **中国外汇交易中心** | HTML | `https://www.chinamoney.com.cn/chinese/bkccpr/` → 200，24KB（中间价数据页） | LPR / Shibor / 存单报价，同业定价基准 |

### 2.2 行业协会与自律组织（**同业主场**）

| # | 信源 | 接入形态 | 实测 | 价值 |
|---|---|---|---|---|
| A1 | **中国银行业协会 · 银行机构动态** | HTML 列表+详情 | `https://www.china-cba.net/Index/lists/catid/35.html` → 200 / 23.7KB / 14 个日期；详情页 `/Index/show/catid/35/id/47262.html` → 200 / 27.8KB，**11 个正文段落**，含「发布时间：2026-10-22 / 来源：江西银行」 | **最贴合「同业追踪」**：逐家银行的科技金融、授信、产品动作。实测样例：「江西银行：优化科技金融授信审查 全力护航科创企业成长」 |
| A2 | CBA · 通知公告 | HTML | `catid=15.html` → 200 / 22KB / 28 个日期 | 行业自律规范实测：「关于发布《个人贷款业务明示综合融资成本自律规范》的公告」 |
| A3 | CBA · 政策动态 | HTML | `catid=36.html` → 200 / 19.4KB / 40 个日期 | 监管政策转述 |
| A4 | CBA · 新闻中心 | HTML | `catid=2.html` → 200 / 35KB / 16 个日期 | 监管口径讲话、协会发布报告 |
| A5 | CBA · 专题研究 / 相关形势研究 | HTML | `catid/30` / `catid/60` → 28 个日期 | 行业深度研究（对标参考价值） |
| A6 | CBA · 普法专栏 / 规范性文件 | HTML | `catid/100` / `catid/120` → 25 / 18 个日期 | 合规红线 |
| A7 | **中国支付清算协会** | HTML 列表+详情 | 列表 `https://www.pcac.org.cn/` → 200 / 107KB / 10 个日期；详情 `https://www.pcac.org.cn/eportal/ui?pageId=598378&articleKey=624353&columnId=595148` → 200 / 31.4KB，**标题可取**（「第十四届中国支付清算论坛召开 跨境二维码统一网关已于7月底上线试运行」），但正文段落只抓到版权行 → **正文选择器待调** | 支付清算行业标准（mpaypass 的上游补充） |

### 2.3 上市银行公告与财报（**同业动作的原始凭证**）

| # | 信源 | 接入形态 | 实测 | 价值 |
|---|---|---|---|---|
| L1 | **巨潮资讯网公告检索 API** | POST JSON + PDF | `POST http://www.cninfo.com.cn/new/hisAnnouncement/query`（`searchkey=银行`）→ 200 / 23.7KB，`totalAnnouncement=109617`；`searchkey=招商银行` → 200，`totalAnnouncement=2459`，返回 `secCode=600036` / `secName` / `announcementTitle` / `announcementTime` / `adjunctUrl`。PDF 实测可下载：`http://static.cninfo.com.cn/finalpage/2026-10-31/1225649440.PDF` → 200 / `application/pdf` / 106452 B | 42 家上市银行的**全部**公告：财报、高管变动、关联交易、股权变更。**标题/元数据白拿；正文是 PDF，而 moni 当前无 PDF 抽取依赖**（见 §4.1） |

### 2.4 财经媒体（银行专属频道）

| # | 信源 | 接入形态 | 实测 | 价值 |
|---|---|---|---|---|
| M1 | **金融时报**（人民日报社主管） | HTML 列表+详情 | 首页 200 / 67.8KB / **260 个日期型链接**；文章 URL 形如 `https://www.financialnews.com.cn/2026-11/12/content_460725.html` → 200 / 25.3KB，正文实测「本报讯 记者马梅若报道 11月11日，中国人民银行发布2026年第三季度中国货币政策执行报告。」 | 央媒级权威 + 银行条线深稿；日期在 URL 里，解析极易 |
| M2 | **第一财经 · 金融频道** | JSON API + HTML 详情 | `GET https://www.yicai.com/api/ajax/getlistbycid?cid=53&page=1&pagesize=20` → 200 / 28.9KB。字段实测齐全：`NewsTitle` / `NewsID` / `NewsNotes`(摘要) / `NewsSource` / `NewsLength` / `pubDate` / `url` / `EntityPublishDate`。详情 `https://www.yicai.com/news/103437144.html` → 200 / 25.3KB，**正文 27 段**可解析 | 结构化最干净，零解析成本。注意 `cid=53` 是泛金融频道（实测首条为保险新闻），需按 `EntityName`/关键词再筛银行 |
| M3 | 财联社电报 | JSON | `www.cls.cn/v3/depth/home/assembled/1000` → `{"errno":"10012","msg":"签名错误"}`；`nodeapi/telegraphList` → 404 | **需带签名**，抓取成本高，本轮不建议 |

### 2.5 境外监管（全球对标）

| # | 信源 | 接入形态 | 实测 | 价值 |
|---|---|---|---|---|
| O1 | **美联储** | RSS | `https://www.federalreserve.gov/feeds/press_all.xml` → 200 / 14.8KB / **20 items** | 利率决议、金融稳定报告 |
| O2 | **欧洲央行** | RSS | `https://www.ecb.europa.eu/rss/press.html` → 200 / `application/rss+xml` / **15 items** | 欧洲监管与数字化 |
| O3 | 港/新/澳央行 | RSS | 猜测路径全部 404 或连接失败（HKMA 404、MAS 超时、RBA 超时、BoE 404、BIS 404、FSB 404、IMF 403） | 需另找真实 feed 地址，**本轮未验证** |

### 2.6 微信公众号（**价值最高但通道已断**）

| 通道 | 实测 | 结论 |
|---|---|---|
| `wechatrss.waytomaster.com`（moni 现有 A/B 账号） | HTTP 200 / 476KB(A)、60 items(B)；但 **最新 pubDate = A 2026-07-22 / B 2026-07-29** | **上游停更**，与 `STATUS.md:1186` 记录一致。停用源无法靠重启恢复 |
| 已订阅公众号内容 | B 号订阅「金融科技研究」「未央科技weiyangx」 | 内容价值极高：「银行周报（第28期）｜工行科技条线组建"领航AI+"团队」「负债成本「平权时代」：什么样的银行在突围？」—— 正是 moni 缺的「谁在做什么」 |
| `rsshub.app` | 403（Cloudflare "Just a moment"） | 公共实例不可用 |
| `werss.app` | 200（16.6KB 落地页） | 付费服务，需账号 |
| `wechat2rss.xlab.app` / `rsshub.rssforever.com` / `api.allorigins.win` | 404 / 连接失败 | 不可用 |

---

## 3. 覆盖缺口（明确拿不到的部分）

### 3.1 金融监管总局（行政处罚）

- 站点为 Angular SPA，列表页 4012B、0 个日期链接，正文靠静态 JSON。
- 前端 `Script.js` 的取数规则已定位：
  `apiUrl_cdn + "/" + api.url.substr(1).replace("/","_") + ps + ".json"`，
  其中 `ps = "[" + 参数按 key 排序、value 经 encodeURIComponent、"," 连接 + "]"`，
  `apiUrl_cdn = originUrl + "/cn/static/data"`。
- 但按该规则拼出的 5 组 URL 全部 404 —— 说明**静态 JSON 是按具体 itemId 预生成的**，
  需要先从 `Nav.js` / `data_.json` 拿到正确的栏目 id 组合才能命中。
- **可行路径**：① 继续逆 `itemId`（低成本）；② 直接用 moni 已有的 `moni/fetch/chrome_daemon.py`
  无头 Chrome 渲染列表页（已有能力，成本中等）。

### 3.2 各银行官网新闻（SPA）

- 实测：招行 171KB / 0 日期链接；微众 15KB / 0；网商 6.7KB / 0；平安 103KB / 0；浦发 235KB / 0。
- 结论：**逐行官网抓取性价比极低**（20+ 站、每个都要 Chrome 渲染、且很多行新闻只在 App/公众号首发）。
  同业动态走 A1（协会「银行机构动态」）+ L1（巨潮公告）即可覆盖绝大部分。

### 3.3 微信公众号

- 见 §2.6。若要恢复，需付费通道（werss）或自建 RSSHub 实例（Docker，本机跑 `rsshub` + 微信 cookie）。
- **替代思路**：公众号的高价值内容在 A1/L1/M1 有同源转载（协会会转载银行业动态、
  金融时报会转载政策解读），先把可抓的做厚。

### 3.4 上交所公告接口

- `query.sse.com.cn/security/stock/queryCompanyBulletinNew.do?...keyWord=银行` 返回 **200 但
  `result:[]`、`total:0`** —— 接口连通、参数未调通。
- 结论：巨潮已覆盖沪深上市银行公告，本轮**不计入可用源**；若需要再单独调参。

---

## 4. 纳入 moni 的落地机制（每加一个源 6 步）

### 4.1 获取形态与依赖约束（开工前必须知道）

| 形态 | moni 现状 | 结论 |
|---|---|---|
| RSS 2.0 | `xml.etree.ElementTree` 已在用（`qbitai.py` / `kr_rss.py` / `huxiu.py`） | 零新增依赖，抄 `qbitai.py` 骨架即可 |
| HTML 列表+详情 | `httpx` + `beautifulsoup4` 已在 `pyproject.toml`；`mpaypass.py` 已用 BS4 | 零新增依赖 |
| JSON API | `httpx` 即可 | 零新增依赖 |
| **PDF 正文** | **无任何 PDF 库**（`pypdf`/`pdfplumber`/`fitz`/`pdfminer` 全无） | 巨潮公告正文需**新增依赖**，或先用标题/元数据入库 |
| 无头 Chrome | 已有 `moni/fetch/chrome_daemon.py` | 备选，用于 SPA 站（监管总局、各行官网） |

**两条解析注意点（实测踩到）**

1. 央行 RSS 的 `<link>` 在 CDATA 里且**不带 scheme**：`www.pbc.gov.cn/goutongjiaoliu/...`，
   入库前要补 `https://`；`description` 里是 HTML 片段，需剥标签。
2. 外汇局/协会详情页正文里混着导航与脚本模板段落（`<p>` 命中 4 段但只有 1 段是真正文），
   必须按容器选择器取，不能用「所有 `<p>` 长度 > N」的粗筛。

moni 的源契约（`moni/fetch/base.py:1-25`）：

- 1) `config/sources.yaml` → `source_name` / `type` / `feed_url` / `fetch_interval_min` / `enabled`
- 2) `moni/fetch/sources/<x>.py` → `class FetcherX(BaseFetcher)`：`source_name` + `fetch()` + `parse()`；`@register_fetcher` 自注册；RSS 用 ElementTree 解析，参考 `qbitai.py`
- 3) `moni/db/seed_config.py` → `_DEFAULT_SOURCES` 加一行（id/name/display_name/source_type/url/notes）
- 4) 同文件 `_DEFAULT_SOURCE_TAGS` → 绑 tag（tag 必须已在 `_DEFAULT_TAGS` 定义，否则 FK 崩）
- 5) 同文件 `_DEFAULT_FILTERS` → 可选黑名单（title+content）
- 6) 同文件 `_DEFAULT_TAGS` → 若需新 tag，在此处定义并写 keywords

验证命令：

    set -a && source .env && set +a
    /usr/local/bin/python3 -m moni fetch --source <x> --dry-run
    /usr/local/bin/python3 -m pytest tests/ -p no:cacheprovider -q

**关键词闸门要点（`moni/fetch/keyword_match.py`）**

- 匹配只能走 `moni/fetch/keyword_match.py`，严禁在别处手写 `kw in text`。
- 纯 ASCII 词按词边界匹配；≤2 汉字的词要求命中标题或在正文重复 ≥N 次。
- 规则收紧前必须先跑 `moni keyword-audit`（只读重放）。

**案例库落库链路**

- `CaseRepository.get_pending_articles()`（`moni/cases/repository.py:405-446`）
  取 `raw_content` 长度 ≥100 且尚无 cases 行的文章，**可按 `--source` 定向跑**。
- 新源一接通，跑一次 `moni cases extract --source <新源>` 即可直接进案例库与事件日历，
  不需要改案例抽取逻辑。

---

## 5. 分期建议

### P0（本周内，零新增依赖、全部已实测）

| 源 | 形态 | 建议绑定 tag | 备注 |
|---|---|---|---|
| `pbc_rss` | rss | `regulation` + `banking` | 直接抄 `qbitai.py` 解析骨架；`fetch_interval_min: 60` |
| `cba_banks` | html | `banking` | 采 A1 银行机构动态（catid=35）+ 详情页解析，正文里「来源：XX银行」可作 case 主体 |
| `cba_notices` | html | `regulation` + `banking` | 采 A2 通知公告（自律规范） |
| `financialnews` | html | `banking` + `regulation` | M1，URL 含日期，解析成本低 |
| `yicai_finance` | json | `banking` | M2，纯 JSON |

### P1（下周，需新写 API fetcher）

| 源 | 形态 | 说明 |
|---|---|---|
| `cninfo_bank` | json(POST) | L1；按行/按关键词定向拉公告标题+元数据（白拿）。PDF 正文需新增 PDF 依赖，见 §4.1 |
| `safe_news` | html | R4；外汇/跨境 |
| `pcac_news` | html | A7；标题可用，正文选择器待调 |
| `fed_press` / `ecb_press` | rss | O1/O2；英文源需 language tag 区分 |

### P2（需要额外决策）

| 源 | 阻塞点 | 决策点 |
|---|---|---|
| `nfra` | SPA + 静态 JSON 路径未命中 | 是逆 itemId 还是上 Chrome 渲染？成本 vs 价值 |
| 微信公众号 | 通道停更 | 买 werss / 自建 RSSHub / 放弃（用 A1+L1+M1 替代） |
| 各行官网 | SPA | 建议**放弃**，改用协会 + 巨潮 |
| 港/新/澳央行 | feed 地址未找到 | 需要 web 搜索定位真实地址 |

### 配套改动（每个新源都要做）

1. `tag_definitions` 里给 `banking` **补「主体词」而非「主题词」** 的关键词
   （如各行简称 + 「XX银行：」「XX银行 发布」这类动作句式），否则闸门继续放宏观稿进来。
2. 建议**新增独立 tag `peer-banks`（同业）**：关键词 = 各行名 + 动作词（获客/授信/财富/理财/投顾/科技投入/组织调整/处罚/中报/年报），
   专供案例抽取与「同业追踪」看板，避免和 `banking` 主题类混在一起。
3. 新增源后跑 `moni keyword-audit`，确认无过度误杀（注意 `CV` 类缩写边界坑）。

---

## 6. 风险与红线

- **不改现有源行为**：新增源为并行增量，不动 13 个现有源的 tag/关键词绑定。
- **新 tag 必须先在 `_DEFAULT_TAGS` 定义**（否则 FK 崩，空库启动失败）。
- **`ON CONFLICT` 不覆盖用户已配 keywords** —— 新 tag 的 keywords 只在首次 seed 写入。
- **公众号通道属外部账号操作**（订阅/退订/付费）需用户当轮点头，不自行执行。
- **巨潮/交易所 API 属公开检索接口**，调用需限速（建议 `fetch_interval_min >= 30`），
  避免被封；只用检索不下载 PDF 全文。
- 官方站点结构变化会导致解析静默失效 → 每个新源配 `moni fetch --health` 探针
  （`moni/fetch/health.py` 已有源健康检查框架）。

---

## 7. 复现本次实测的命令

    # RSS 有效性（央行 / Fed / ECB）
    curl -sS -A "Mozilla/5.0" "https://www.pbc.gov.cn/goutongjiaoliu/113456/2986536/index.html" \
      | grep -c '<item>'

    # HTML 列表页服务端渲染检查（银行业协会 / 金融时报）
    curl -sSL -A "Mozilla/5.0" "https://www.china-cba.net/Index/lists/catid/35.html" \
      | grep -o -E '20[0-9]{2}-[0-9]{2}-[0-9]{2}' | wc -l

    # 巨潮公告 API
    curl -sS -X POST "http://www.cninfo.com.cn/new/hisAnnouncement/query" \
      -H "Content-Type: application/x-www-form-urlencoded" \
      --data "pageNum=1&pageSize=5&column=szse&tabName=fulltext&searchkey=%E6%8B%9B%E5%95%86%E9%93%B6%E8%A1%8C"

    # moni 侧现状
    sqlite3 data/news.db "SELECT source, COUNT(*) FROM cases c JOIN articles a ON a.id=c.article_id GROUP BY 1;"

---

## 8. 待用户决策项

1. **P0 五个源是否直接开工？**（建议先做 `pbc_rss` + `cba_banks`，这两个验证「同业追踪」价值最快）
2. **是否新建 `peer-banks` tag？**（建议新建，否则案例主体仍会混在 banking 主题类里）
3. **公众号通道**：付费 werss / 自建 RSSHub / 放弃？
4. **金融监管总局**：逆 itemId（低成本、不确定）还是上 Chrome 渲染（成本高、稳）？
5. **是否需要境外源**（Fed/ECB）—— 对「全球同业对标」申报口径是否有价值？

