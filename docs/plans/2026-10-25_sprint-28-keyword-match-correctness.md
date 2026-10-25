# sprint-28 TECH-SPEC: 关键词匹配正确性 (v1.0.64)

- **Sprint ID**: 26102501
- **目标版本**: v1.0.64
- **阶段**: sprint-28
- **日期**: 2026-10-25
- **目标仓库**: `mariusiaowego-commits/moni`
- **状态**: 实现中
- **前序**: v1.0.63 (PR #164/#165) 把独立博客源改绑现有业务 tag 时, 暴露了匹配层的老问题

---

## 1. 触发 (v1.0.63 收尾时实测)

v1.0.63 修好「新源走不带关键词的 pass-through tag」之后, 82 篇独立博客文章
全部带上了 tag。但抽查发现其中有明显不相关的文章也过了闸门:

| 文章 | 命中的 tag | 实际命中的词 | 真实原因 |
|---|---|---|---|
| 2024 北海道 Part 3 | general-tech | `MA` | 晚餐店名 **MASAJIN** |
| Clang Attributes 黑魔法小记 | model-providers | `ark` | 代码里的 **SarkGay** |
| Jekyll in 2021 | model-providers | `ark` | 正文里的 **Markdown** |
| 《Getting Things Done》读书笔记 | banking / payment | `不良` / `支付` | 正文一处顺带提及 |
| 五仁月饼 | banking | `消费` | 正文一处顺带提及 |
| 我的时间管理道与术（三） | payment | `支付` | 正文一处顺带提及 |

两个根因, 都是「关键词用 `in` 做纯子串匹配」:

1. **ASCII 短词没有词边界** — `MA` / `ark` / `CV` 会命中任意更长的英文单词内部。
2. **中文短词没有配信度要求** — `消费` / `银行` / `支付` 是真词, 但一篇
   5000 字无关长文里顺带出现一次, 不代表文章主题是它。

这不是新源独有的问题: 同一套 tag 关键词服务全部 12 个源。

---

## 2. 影响面实测 (2026-10-25 生产库快照, 4702 篇)

用 `moni keyword-audit` 对存量做只读重放。基线必须按**各源真实旧行为**
计算 —— bestblog* 系自 v1.0.5.24/7.5 起就是 title-only, 拿 title+content
给它算基线会凭空造出「旧规则会留下的文章」:

| 规则 | 存活 | 丢弃 |
|---|---:|---:|
| 旧规则 (纯子串, 按源套 title-only) | 4547 | 155 |
| 新规则 (本 sprint) | 4396 | 306 |

其中 **151 篇是「旧规则本会留下、新规则丢掉」** — 这就是误命中的体量。

按源分布:

| 源 | 受影响 | 库内总数 |
|---|---:|---:|
| bestblog-x | 59 | 150 |
| bestblogs | 48 | 1736 |
| chinese_independent_blogs | 28 | 82 |
| 36kr_rss | 4 | 451 |
| huxiu | 4 | 301 |
| qbitai | 3 | 82 |
| mpaypass | 2 | 1427 |
| bestblog-yt | 2 | 61 |
| follow_builders | 1 | 38 |

> 独立博客源受影响比例最高 (28/82 = 34%): 它是纯个人博客, 正文长且话题
> 发散, 最容易出现「顺带提一句金融」的形态。

单关键词层面最能说明问题的几个:

| 关键词 | 旧命中 | 新命中 | 说明 |
|---|---:|---:|---|
| `ark` | 130 | **0** | 全部是 Markdown / DarkScrim / SarkGay, 从来没有独立出现过 |
| `MA` | 79 | **0** | 全部在 MASAJIN / MAC / MAX_BOOKS / SMART 内部 |
| `CV` | 64 | 5 | 多数在 CVPR / CV 文件里 |
| `金融` | 1279 | 1052 | 长文顺带提及被收掉 |
| `消费` | 636 | 444 | 同上 |
| `保险` | 194 | 105 | 读书笔记里提一次「保险」不再算 |
| `外汇` | 159 | 70 | 同上 |

> `ark` 归零值得单独说: 它和 `MiniMax` / `deepseek` / `gemini` 并列在
> model-providers 里, 但历史上从未真正命中过 ark 这个模型 —— 它此前
> 命中的 130 篇全是别的词的碎片。保留它无害 (现在它只在真出现
> "ark" 独立词时才命中), 但这一条说明**纯子串统计不能用来判断关键词
> 有没有价值**。

---

## 3. 规则设计

新增 `moni/fetch/keyword_match.py`, 三种关键词走三种规则:

| 关键词形态 | 匹配规则 | 理由 |
|---|---|---|
| 纯 ASCII | 两端词边界 (`[A-Za-z0-9_]` 之外), 容许 `s`/`es` 复数 | 中文无空格, 拉丁词必须有边界; 但 `AI Agent` 必须还能命中 "AI Agents" |
| 纯中文且 ≤2 字 | 命中标题, 或正文里出现 ≥2 次, **或正文本身 ≤200 字** | 一次顺带提及不算主题; 但 60 字行情快讯物理上没地方重复 |
| 其余 (≥3 字中文 / 混合词) | 纯子串, 不变 | 已经足够具体, 改动只会掉召回 |

### 3.1 为什么保留子串语义

中文没有词边界, `股票市场` 必须还能被 `股票` 命中。真正的分词需要词典,
引入 N 个新依赖和一大类新误判 (分词错误). 收紧的只限于**有明确证据的
两种形态**。

### 3.2 为什么容忍复数

实测 `AI Agent` 若不容忍复数会丢 41 篇 (都是 "AI Agents")。
容忍复数后 `MA` 依然不会命中 MASAJIN —— `MASAJIN` 的 M-A 后面是 S,
不是词边界也不是复数结尾。

### 3.3 为什么短正文豁免

不加豁免时 `36kr_rss` 的 117 字行情快讯会因为「腾讯」只出现一次被丢,
`huxiu` 的 33 字短评同理。这类短讯的每一次提及都是它的全部内容。
实测加 200 字豁免后恢复 22 篇, 都是这类短讯。

### 3.4 刻意不改的地方

| 位置 | 为什么不改 |
|---|---|
| `moni/cases/normalize.py` | 闭集分类 (5 类枚举), 短词子串就是设计意图 |
| `moni/events/normalize.py` | 同上, 唯一致力于「哪类最像」 |
| `moni/wechat_clip.py` | 人工标题 drop-list, 不是内容闸门 |
| 大小写 | `APP` / `App` / `MiniMax` / `minimax` 都是刻意分开的条目, 折叠会改变 tag 命中 |

---

## 4. 改动文件

| 文件 | 改动 |
|---|---|
| `moni/fetch/keyword_match.py` | **新增** 规则引擎 (纯函数, 无 DB 依赖) |
| `moni/fetch/tag_filter.py` | tag 闸门改用 `keyword_matches`; `hit_field` 现在如实反映 title-only 源 |
| `moni/fetch/filters.py` | 黑/白名单 + keyword_hits 记录改用同一引擎; 删掉重复的 `_match_field` |
| `moni/cli/main.py` | `xwatch ingest` 手动路径对齐批处理闸门 (否则批处理拦下的推文能从 CLI 塞进来) |
| `moni/cli/keyword_audit.py` | **新增** `moni keyword-audit` 只读重放命令 |
| `tests/test_keyword_match.py` | **新增** 44 个规则单测 |
| `tests/test_keyword_match_integration.py` | **新增** 14 个跨调用点集成测试 |
| `tests/test_keyword_audit_cli.py` | **新增** 8 个 CLI 测试 (含只读性断言) |
| `tests/test_tag_filter.py` | 修 1 个既有测试 —— 它此前靠短正文豁免过, 没有真的测到 content 匹配 |

### 4.1 为什么必须同时改 3 个调用点

tag 闸门、黑/白名单、xwatch CLI 是**三条独立的入口**。只改 tag 闸门会
留下两个后门: 黑名单继续被 `MA` 误触发, CLI 继续能塞进批处理会拒绝的推文。
集成测试 `test_keyword_match_integration.py` 就是钉这条一致性的。

---

## 5. 验收标准

- [ ] 全量 pytest 通过 (基线 1298 → 预期 1369)
- [ ] 9 个变异全部被测试咬住 (纯子串回退×2 / title-only 丢失×2 / 词边界 / 中文门槛 / 短正文豁免边界 / 复数 / audit 基线 title-only)
- [ ] `moni keyword-audit` 只读 (articles/basket/tags/hits 计数不变)
- [ ] 生产库重放: 幸存篇数与文档记录一致 (4547 → 4396, 丢 306, 其中 151 篇是旧规则本会留下)
- [ ] v1.0.63 修好的独立博客源没有回退 (82 篇仍全带 tag)
- [ ] `bypass_keyword_filters` (follow_builders) 仍然短路
- [ ] bestblog* 系仍然 title-only

---

## 6. 不做的事

- 不动任何**关键词内容** (不删 `MA` / `ark` / `支付`, 不加词) —— 那是数据决策,
  用户自己定; 本 sprint 只改匹配语义。
- 不回补 / 不重扫存量文章 (194 篇的 tag 归属不回改)。下次抓取自然生效。
- 不引入分词库 / jieba / 词典。
- 不改 `basket` 里已有的历史记录。

---

## 7. 后续可做 (不在本 sprint)

- `payment` tag 只有 `支付` 一个关键词, 命中面太宽 —— 需要用户补词。
- `general-tech` 的 `MA` 和 `CDP` 语义重叠 (营销自动化 vs 客户数据平台),
  按新规则 `MA` 归零, 可以考虑删掉。
- 存量 194 篇的 tag 归属是否要重算, 需要单独决策 (涉及 basket 语义)。
