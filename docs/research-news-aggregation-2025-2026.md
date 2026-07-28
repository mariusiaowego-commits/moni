# 2025-2026 个人/小团队新闻聚合与信源监控产品研究

> 面向 moni（FastAPI + SQLite + Alpine.js 本地自用聚合系统）的对标研究，2026-07 完成。

## 一、标杆产品对比

### 1. Readwise Reader —— "深度阅读 + 稍后读" 路线
定位不是 feed 扫描器，而是"读进去"的工具：RSS、Newsletter（专属收件邮箱）、Web clipper、PDF、EPUB、YouTube（带字幕对齐播放）、Twitter thread 全部收进统一收件箱。差异化在**高亮/批注体系**（高亮自动同步 Obsidian/Notion）、Ghostreader（GPT 就地摘要/解释/提问）、以及"Inbox → Later → Archive" 的三态 triage 工作流。它证明了：聚合器的护城河可以是"阅读后资产化"而非抓取量。

### 2. Feedly（Leo AI）—— "AI 优先级引擎" 路线
Leo 的核心不是摘要而是**队列塑形**：按主题/实体优先级排序、near-duplicate 去重、静音不关心的话题、Must-Read 置顶。付费墙内还有面向团队的 Threat/Market Intelligence 场景。启示：LLM 最大价值在"决定你先看什么、不看什么"，而非每篇都给摘要。

### 3. Inoreader —— "规则自动化 + 泛信源" 路线
免费档即支持 150 feeds + 20 网页监控 + 20 Newsletter 邮箱 + Bluesky/Telegram/Facebook 社媒流；**Rules/Filters 引擎**（关键词命中 → 自动打标/归档/推 Slack）是研究员和记者的首选；还能**监控没有 RSS 的网页**（页面 diff 转为条目）。Intelligence 档提供摘要、AI 标签、播客/视频转写。它是"信源丰富度"维度上最全面的商业产品。

### 4. Artifact（2024 年初关停）—— 反面教材
Instagram 创始人做的 AI 个性化新闻流，算法很强但缺少用户自有信源（不能订阅自己的 RSS/Newsletter），纯算法推荐在个人工具市场证伪：**用户要的是"我的信源 + AI 辅助"，不是"AI 的信源"**。

### 5. Folo/Follow（RSSNext，开源）—— "RSSHub 原生 + 内容形态分型" 路线
2024-2025 增长最快的开源阅读器（GitHub 30k+ star）。三个关键设计：① **原生集成 RSSHub**，Twitter/Telegram/Instagram/GitHub/播客一键订阅；② **按内容形态分视图**——Articles/Social/Images/Videos/Audio/Notifications 各有专属 UI（社媒是卡片流、图片是瀑布流、播客有播放器），而非全部塞进文章列表；③ AI 时间线摘要 + 晨间邮件简报 + 自动打标，直接回应"1000+ 未读焦虑"。对 moni 参考价值最高。

### 6. Meridian（iliane5/meridian，开源，2.4k star）—— "个人情报简报" 路线
Cloudflare Workers + Vue + Python ML 服务的自托管系统：抓数百个源 → embedding（含 embedding_text 字段入库）→ **UMAP 降维 + HDBSCAN 聚类成 story** → LLM 对每个 story 做多源交叉分析 → 生成每日个性化 brief。它是"presidential daily brief for individuals"理念的最完整开源实现，pipeline 设计（抓取/嵌入/聚类/合成四段解耦）值得直接借鉴。

### 7. 其他自托管生态
- **FreshRSS / Miniflux**：轻量自托管标杆，Miniflux 的"单二进制 + 集成 readability 全文抓取 + Fever/Google Reader API 兼容"是工程精致度范本。
- **morss / FiveFilters Full-Text RSS**：把摘要型 feed 转全文 feed 的专用代理，作为管道组件嵌入。
- **RSS-Bridge**：RSSHub 之外最大的开源桥接器（PHP，200+ bridge），适合补 RSSHub 没有的站点。
- **Karakeep（原 Hoarder）**：自托管 bookmark + AI 自动打标 + 全文存档，代表"收藏即存档"趋势。

## 二、2025-2026 内容获取渠道趋势

1. **RSSHub 路由生态是中文信源的事实标准**：5000+ 公共实例、数千条路由覆盖微博/知乎/B站/播客/GitHub 等。但趋势是**需要凭证的路由增多**（Twitter 需 auth token、部分站点需 cookie），自托管实例 + 配置私有凭证成为标配玩法；公众号仍无官方 feed，主流做法是 Wechat2RSS / WeRSS / 瓦斯阅读类聚合中转（moni 已在用此路线）。
2. **Newsletter 是一等信源**：Readwise Reader、Inoreader、Feedly 均提供专属收件邮箱（xxx@reader.app），把 Substack/Ghost 邮件解析成条目。自托管可用 Kill the Newsletter! 或 IMAP 拉取 + HTML 清洗。
3. **全文抓取标准化**：Python 侧 **trafilatura** 是 2024-2025 学术评测中正文抽取准确率最高的库（优于 newspaper3k/goose），Mozilla readability.js 是 JS 侧标准；Miniflux/morss 都内置类似能力。反爬升级下，Playwright 无头渲染 + 抓取 API（Firecrawl/Jina Reader）作为 fallback 成为常见双层架构。
4. **播客转写平民化**：faster-whisper 本地转写成本趋近于零，Inoreader 已把"播客/视频转写"做进产品；转写 → LLM 章节化摘要是 2025 播客类产品的标准链路（Snipd 是标杆）。
5. **X API 收费后的替代方案**：免费 API 基本不可用（$100+/月起）。实际路线：① RSSHub twitter 路由 + 自备 auth token（TOS 风险自担）；② Nitter 公共实例（大量失效，自托管 + 账号池尚可）；③ **转向 Bluesky（AT Protocol 开放 API 免费）与 Mastodon** 作为增量社媒信源——Inoreader 已原生支持 Bluesky，这是 2025 明确趋势。
6. **Web Clipper / 稍后读回流**：Pocket 于 2025 年 7 月关停，用户涌向 Readwise Reader、Karakeep、Wallabag——"浏览器一键存入聚合器"成为个人系统的必备入口。

## 三、LLM 在新闻聚合中的应用模式

1. **语义去重**：embedding（如 bge-m3 / text-embedding-3-small）+ 余弦阈值合并同事件多源报道，Feedly Leo 的 near-duplicate 折叠是产品化范本；SQLite 场景可用 sqlite-vec 存向量。
2. **Story clustering（事件聚类）**：Meridian 的 UMAP + HDBSCAN 管线是开源参考实现——单篇文章不再是展示单元，"事件卡片（N 个来源报道了同一事件）"才是。这是聚合器从"读文章"升级为"看事态"的关键跃迁。
3. **个性化排序**：用行为信号（打开/Pin/停留）训练轻量偏好，或直接让 LLM 按用户兴趣 profile 给条目打分（Folo/Feedly 模式）；比推荐算法更务实的是"可解释的优先级 + 一键静音话题"。
4. **每日简报生成**：Folo 晨间邮件、Meridian daily brief、Readless 的"1 digest not 12"都收敛到同一形态：**定时 → 聚类 → 跨源综合（而非逐篇摘要拼接）→ 简报含'为什么重要'一句话**。跨源综合是与逐篇摘要拉开体验差距的关键。
5. **辅助模式**：AI 自动打标（Karakeep/Inoreader）、就地问答（Readwise Ghostreader）、转写后摘要（Snipd）。

## 四、值得 moni 借鉴的功能清单（12 条）

1. **自托管 RSSHub 实例接入**——一次接入即获得数千条路由（微博/B站/GitHub releases 等），是提升信源丰富度 ROI 最高的一步（Folo 已验证）。
2. **Newsletter 收件邮箱**——IMAP 拉取或 Kill the Newsletter! 中转，把 Substack/邮件专栏变成信源（Readwise/Inoreader 标配）。
3. **无 RSS 网页监控**——对指定 URL 定时抓取 + 内容 diff 生成条目，覆盖公司 blog/政策页等长尾信源（Inoreader 独有卖点）。
4. **trafilatura 全文抓取层**——摘要型 feed 自动补全正文，摘要和标签质量直接受益（Miniflux/morss 模式）。
5. **事件聚类卡片**——embedding + HDBSCAN 把同事件多源报道折叠为一张卡片，展示"N 源报道"，从根本上降噪（Meridian/Feedly Leo）。
6. **跨源综合每日简报**——在已有 LLM 摘要基础上升级为"聚类后跨源合成 + 为什么重要"，定时生成推送（Meridian/Folo 模式），与现有 7 种分享格式天然契合。
7. **按内容形态分视图**——播客/Twitter/YouTube 用专属卡片 UI 而非统一文章列表，精致度提升最直观（Folo 的核心体验创新）。
8. **规则引擎**——"关键词/来源/标签命中 → 自动 Pin/静音/打标/通知"，把重复性 triage 自动化（Inoreader Rules）。
9. **话题静音与优先级**——一键 mute 不关心的话题实体，LLM 按兴趣 profile 排序收件箱（Feedly Leo 的核心价值）。
10. **播客转写 + 章节摘要**——faster-whisper 本地转写，让播客内容可搜索、可引用（Inoreader/Snipd）。
11. **Bluesky 信源接入**——AT Protocol API 免费开放，是 X API 收费后成本最低的社媒增量信源（Inoreader 已支持）。
12. **浏览器 Bookmarklet/Clipper 手动入库**——任意网页一键存入并走同一套摘要/标签管线，补齐"主动收藏"入口（Pocket 关停后的明确需求，Readwise/Karakeep 模式）。

## 五、一句话总结

2025-2026 的共识形态是：**"泛信源接入（RSSHub/Newsletter/网页监控/社媒）× LLM 事态化（聚类去重 + 跨源简报）× 阅读资产化（收藏/标注/分享）"**。moni 已具备第三层的独特优势（7 种分享格式），最大增量空间在第一层（信源丰富度：RSSHub 自托管、Newsletter、网页监控）和第二层的升级（从逐篇摘要到事件聚类 + 跨源简报）。
