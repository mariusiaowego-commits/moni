# 事实核查白名单扩充 + 域名报表（v1.0.107）

## 问题

`config/fact_check_domains.toml` 是 v1.0.100 定下的快照，之后没再维护。用生产库实测
（`data/news.db` 副本，2026-11-19）：

```
证据域名 132 个 / 证据 236 条
  一手来源    28 个域名   40 条证据
  编辑制媒体  41 个域名  126 条证据
  不计入       5 个域名   10 条证据
  未收录      58 个域名   60 条证据   ← 25% 的证据被判「没有可计入的来源」
```

未收录里包括 `shengjingbank.com.cn`（盛京银行）、`psbc.com`（邮储）、
`unionpay.com`（银联）、`bloomberg.com`、`cnbc.com` 等明确可定级的主体。

更糟的是配置头注释写着「维护：每月跑 `moni fact-check-domains --report`」，
但 CLI 里**没有这个子命令**——维护回路是断的。

## 方案

1. **补 `moni fact-check-domains --report`**（只读）：按证据出现次数列出域名与
   当前分档，未收录的单独排队；`--limit 0` 看全部，`--json` 供脚本消费。
2. **按实测数据+明确依据扩充配置**，不批量「猜收录」：
   - `tier_a`：标准机构 `tc260.org.cn`；公告 wire `publicnow.com`
   - `tier_a_brand`：银行/支付机构官网（`psbc.com`、`hsbank.com.cn`、
     `shengjingbank.com.cn`、`unionpay.com`、`tenpayglobal.com`）、境外机构与厂商
     官网（`americanexpress.com`、`circle.com`、`isomorphiclabs.com`、`moomoo.com`、
     `westpac.com.au`、`stripe.com`/`stripe.events`/`stripetour.com`、`trendmicro.com`、
     `claude.com`、`googleblog.com`）、模型与研究院（`qwen.ai`、`hub.baai.ac.cn`）
   - `tier_b`：境外与国内编辑制媒体 26 个（`bloomberg.com`、`cnbc.com`、`cnn.com`、
     `fortune.com`、`inc.com`、`techcrunch.com`、`business-standard.com`、`finextra.com`、
     `theblock.co`、`idnfinancials.com`、`asiae.co.kr`、`chosun.com`、`yna.co.kr`、
     `9to5mac.com`、`cnyes.com`、`yahoo.com`、`ithome.com`、`mydrivers.com`、
     `chinadaily.com.cn`、`bjd.com.cn`、`eeo.com.cn`、`oeeee.com`、`10jqka.com.cn`、
     `ai-bot.cn`、`aiiaw.com`、`kodekloud.com`）
   - `tier_c`（不计入，仅报表可读）：招聘站、数据聚合、个人博客等 12 个
3. **修 eTLD+1**：`_MULTI_LABEL_SUFFIXES` 缺 `co.kr` 等，导致 `asiae.co.kr`
   与 `en.yna.co.kr` 都被算成 `co.kr`——两家不同媒体被当成同一来源，交叉验证虚高。

## 分级原则（写死的判断标准）

- **只收能确证归属的主体**：域名必须能对应到可识别的机构/编辑部，否则一律
  不收录（保持「未收录 = 不计入」）。
- **宁漏不虚**：未收录只是少算一条来源；错收一个 UGC/自媒体会把「无权威源」
  错判成「有权威源」，代价更大。
- **tier_c 只做可读性**：列进去的域名依然不计入判定。

## 验收

- `moni fact-check-domains --report` 在生产库副本上输出：未收录 0 个。
- 全量 `pytest` 通过；新增 7 条断言覆盖报表统计、limit、CLI 子命令、配置
  小写/无跨档重复、扩充域名分档、`co.kr` 注册域。
- 生产部署后同一命令在 8900 数据目录下复跑，未收录应为 0（新增证据若带来
  新域名会正常显示出来）。
