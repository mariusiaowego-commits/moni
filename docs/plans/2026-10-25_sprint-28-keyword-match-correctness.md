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

用 `moni keyword-audit` 对存量做只读重放。基线有两处口径必须对准真实
旧行为, 否则算出来的差异是假的:

1. **bestblog\* 系自 v1.0.5.24/7.5 起就是 title-only** —— 拿 title+content
   给它算基线会凭空造出「旧规则会留下的文章」。
2. **`bypass_keyword_filters` 的源完全不过闸门** —— `follow_builders`
   库里 38 篇行, 那些文章从来没有任何 matcher 看过。注意它现在
   `is_active=0`, 但文章是它在开启时入库的, 所以不能按活跃状态过滤。

重放必须按**每篇文章自己所属源的绑定 tag** 计算 (而不是把所有 tag 并起来
套给每篇), 否则会凭空多算出 190 篇「幸存者」。下面是按真实口径跑出来的
结果 (`moni keyword-audit`, 2026-10-25 生产库快照):

| 口径 | 篇数 |
|---|---:|
| 存量总量 | 4702 |
| 跳过 (bypass 源) | 38 |
| **受闸门约束** | **4664** |
| 其中无绑定 tag (pass-through) | 6 |
| 旧规则存活 | 4362 |
| 新规则存活 | 4210 |
| 新规则丢弃 | 454 |
| **其中「旧规则本会留下」** | **152** ← 误命中的真实体量 |

按源分布:

| 源 | 受影响 | 库内总数 |
|---|---:|---:|
| bestblog-x | 59 | 150 |
| bestblogs | 49 | 1736 |
| chinese_independent_blogs | 28 | 82 |
| mpaypass | 5 | 1427 |
| huxiu | 4 | 301 |
| qbitai | 3 | 82 |
| bestblog-yt | 2 | 61 |
| 36kr_rss | 2 | 451 |

> 受影响 = 「旧规则会留下、新规则会丢弃」的篇数, 按 title-only 口径对
> bestblog\* 系单独计算, 8 个源合计正好 152, 与总数一致。
>
> 独立博客源受影响比例最高 (28/82 = 34%): 它是纯个人博客, 正文长且话题
> 发散, 最容易出现「顺带提一句金融」的形态。

> bestblog-x 59/150 = 39% 更高, 但那是推文短文本里 `MA` / `CV` 这类
> ASCII 短词命中单词内部的典型形态 (x.com 上 Markdown、CVPR 别名词极多)。

单关键词层面最能说明问题的几个:

| 关键词 | 旧命中 | 新命中 | 说明 |
|---|---:|---:|---|
| `ark` | 20 | **0** | 全部是 Markdown / SarkGay 内部, 从来没有独立出现过 |
| `MA` | 24 | **0** | 全部在 MASAJIN / MAC / SMART 内部 |
| `CV` | 59 | 5 | 多数在 CVPR / 简历 "CV" 之外的文件名里 |
| `App` | 299 | 184 | 之前 `Apple` / `Application` 都算命中 |
| `金融` | 1212 | 1004 | 长文顺带提及被收掉 |
| `消费` | 603 | 420 | 同上 |
| `银行` | 1360 | 1185 | 同上 |
| `保险` | 179 | 102 | 读书笔记里提一次「保险」不再算 |
| `外汇` | 154 | 66 | 同上 |

> `ark` 归零值得单独说: 它和 `MiniMax` / `deepseek` / `gemini` 并列在
> model-providers 里, 但历史上从未真正命中过 ark 这个模型 —— 它此前
> 命中的 20 篇全是别的词的碎片。保留它无害 (现在它只在真出现
> "ark" 独立词时才命中), 但这一条说明**纯子串统计不能用来判断关键词
> 有没有价值**。

> 上表分两组, 对应两条新规则: `ark` / `MA` / `CV` / `App` 走**纯 ASCII
> 词边界** (`App` 少掉的 115 篇全是 `Apple` / `Application`); `金融` /
> `银行` / `消费` / `支付` / `保险` / `外汇` 都是 **2 字中文**, 走「标题命中
> 或正文 ≥2 次」门槛, 命中掉 10-30%。907 个关键词里 719 个仍走纯子串,
> 命中数不变 —— 收紧只作用在有证据的两种形态上。

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
| `moni/fetch/xwatch_gate.py` | **新增** xwatch 三条入口共用的闸门 (`gate_article` / `load_gate_keywords`) |
| `moni/cli/main.py` | `xwatch ingest` + `xwatch following --ingest` 都改走同一闸门 (否则批处理拦下的推文能从 CLI 塞进来) |
| `moni/cli/keyword_audit.py` | **新增** `moni keyword-audit` 只读重放命令 |
| `tests/test_keyword_match.py` | **新增** 33 个规则单测 |
| `tests/test_keyword_match_integration.py` | **新增** 16 个跨调用点集成测试 |
| `tests/test_keyword_audit_cli.py` | **新增** 16 个 CLI 测试 (含只读性断言) |
| `tests/test_tag_filter.py` | 修 1 个既有测试 —— 它此前靠短正文豁免过, 没有真的测到 content 匹配 |

### 4.1 为什么必须同时改 4 个调用点

tag 闸门、黑/白名单、xwatch `ingest`、xwatch `following --ingest` 是**四条
独立入口**。只改 tag 闸门会留下三个后门: 黑名单继续被 `MA` 误触发,
两条 CLI 继续能塞进批处理会拒绝的推文。第一轮 audit 就是在 `following
--ingest` 上抓到了这个漏洞 (它自己有一份 `kw in hay`)。

集成测试 `test_keyword_match_integration.py` 钉这条一致性: 同一篇推文
能否入库, 四条路径必须给出同一答案。

---

## 5. 验收标准

- [x] 全量 pytest 通过 (基线 1298 → 实测 **1377 passed / 2 skipped**)
- [x] 13 个变异全部被测试咬住 (纯子串回退×3 / title-only 丢失×2 / 词边界 / 中文门槛 / 短正文豁免边界 / 复数 / audit 基线 title-only×2 / audit bypass 排除×2 / audit 改回 tag 并集)
- [x] `moni keyword-audit` 只读 (articles/basket/tags/hits 计数不变)
- [x] 生产库重放: 4362 → 4210, 受闸门 4664, 丢 454, 其中 **152 篇**是旧规则本会留下
- [x] `moni keyword-audit` 跑完四张表计数不变 — articles 4702 / basket 36952 /
      article_tags 7581 / article_keyword_hits 123978, 重放前后完全一致
- [x] v1.0.63 修好的独立博客源没有回退 (82 篇仍全带 tag)
- [x] `bypass_keyword_filters` (follow_builders) 仍然短路
- [x] bestblog* 系仍然 title-only
- [x] audit 基线按**每篇所属源**的绑定 tag 计算 (并集口径虚增 190 篇)

---

## 6. 不做的事

- 不动任何**关键词内容** (不删 `MA` / `ark` / `支付`, 不加词) —— 那是数据决策,
  用户自己定; 本 sprint 只改匹配语义。
- 不回补 / 不重扫存量文章 (152 篇的 tag 归属不回改)。下次抓取自然生效。
- 不引入分词库 / jieba / 词典。
- 不改 `basket` 里已有的历史记录。

---

## 7. 后续可做 (不在本 sprint)

- `payment` tag 只有 `支付` 一个关键词, 命中面太宽 —— 需要用户补词。
- `general-tech` 的 `MA` 和 `CDP` 语义重叠 (营销自动化 vs 客户数据平台),
  按新规则 `MA` 归零, 可以考虑删掉。
- 存量 152 篇的 tag 归属是否要重算, 需要单独决策 (涉及 basket 语义)。
