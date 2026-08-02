# PRD: X 关键词监控 (v1.0.11 x-keyword-watch)

> 项目: moni · 创建: 2026-08-02 · 状态: 评审通过（拍板默认项） · 分支: `feature/v1.0.11-x-keyword-watch`
> 关联: PLAN `docs/plans/2026-08-02_x-keyword-watch.md` · TECH-SPEC `docs/specs/2026-08-02_x-keyword-watch.md` · TEST-PLAN `docs/test/2026-08-02_x-keyword-watch.md`

## 1. 背景与用户场景

用户在 X 上看到大量关于「微信小程序触达/服务通知能力」的讨论（帖子 + 回复），想系统性学习「世界怎么描述小程序触达能力」。

现状缺口（2026-08-02 实测）：
- bestblog-x 只抓 160 个固定账号的时间线 RSS（xgo.ing），**无关键词搜索、抓不到陌生账号的讨论**；
- 示例推文 `x.com/xiaoyangX026/status/2083475663496044761`（回复 @Philo2022，正文「UX很流畅的感觉。但是我觉得最核心需求是要高触达，及时提醒还方便，在这一点上国内APP没有能比得过微信小程序的」）不在 160 账号内 → 现链路抓不到；
- 已实测一条免费路径：DDG 搜索 `site:x.com + 关键词` 返回推文 URL；`cdn.syndication.twimg.com/tweet-result?id=…` 无需登录/key 返回推文完整 JSON。

## 2. 目标

让 moni 新增一个 `xwatch` 源：
1. 按用户配置的关键词自动发现 X 推文（含回复）并入库；
2. 支持手动粘贴单条推文 URL 收录（覆盖示例场景）；
3. 入库后自动走现有链路：tag 门控（wechat / miniprogram）→ LLM 摘要 → 业务价值 → brief，与其他源一致。

## 3. 用户故事

- 作为用户，我在 /config 或配置里写上关键词「微信小程序 触达」「微信小程序 服务通知」，moni 定时抓 X 上相关讨论入库；
- 我在群里看到一条有价值的推文链接，粘贴进 CLI `moni xwatch ingest <url>`，秒级入库并打上正确 tag；
- 我在文章列表能看到这些推文，带 wechat/miniprogram tag、LLM 摘要，可 pin、可进简报。

## 4. 验收（产品视角）

- [ ] `python3 -m moni fetch xwatch` 能新增 0+ 篇文章，sqlite 里 `source='xwatch'` 可见
- [ ] 入库文章带 `wechat` 或 `miniprogram` tag（命中 tag_keyword_filter 的 keyword_hits 机制）
- [ ] 粘贴示例 URL 入库，`raw_content` 含「UX很流畅的感觉…微信小程序的」全文
- [ ] 重复抓取不重复入库（UNIQUE(source,url)）
- [ ] 单关键词/单 URL 失败不影响其他；DDG 风控有重试与降级日志
- [ ] 全套 pytest 不回归（基线 439 pass + 2 skip，新增 ≥ 15 个用例）
- [ ] fetch_jobs 进度可见（8901 上 `/api/fetch/jobs` 或既有进度 UI）

## 5. 非目标（本期不做）

- 整楼评论/回复树爬取（需登录态或额外 API）
- xgo.ing OpenAPI 付费接入（留 backend 抽象）
- 关键词管理 UI（可 v1.0.12）
- 多搜索引擎（DDG 为主，架构留扩展）
- 推文互动数据（likes/retweets）入库

## 6. 成功指标

- 每次 `fetch xwatch` 有稳定新增（话题活跃期 >0 篇/query）
- 入库内容命中 tag 门控比例高（误入噪声被 wechat/miniprogram 关键词 gate 挡在 basket）
- 从「发现链接」到「入库可摘要」全自动，无需手工清理
