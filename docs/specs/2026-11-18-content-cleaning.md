# 数据清洗 V1 设计方案（已拍板 · 已实现）

分支：`feat/content-cleaning`（base `c4d5906` = origin/main v1.0.99+5）
日期：2026-11-18
状态：**dad 已拍板「甲（清洗中心）+ 丙（信源契约）」合并一个 sprint，代码与测试已完成**

> 实现期与本文档的差异见文末 §10「实现落地记录」。
> sprint 过程文档：Obsidian `tqob/05-Coding/project-moni/sprints/sprint-56-content-cleaning-2026-11-18/`。

## 0. 结论

moni 现在的「清洗」只有一件事：把 HTML 标签剥成纯文本。实测 5,330 篇正文里：

- **491 篇（9.2%）带可确定性清除的杂质**（公众号尾巴 286、实体 82、零宽 84、乱码 55、frontmatter 34）；
- **666 篇正文为空仍照常入库**，摘要/简报链路没有拦；
- **32 篇会被现有 cleaner 二次清空**：`extract_text()` 不幂等——第一次已把 HTML 洗成纯文本，
  但正文里还留着 `<ENV_ID>` / `< img` 这类尖括号；再跑一次时找不到块级标签，直接返回 `""`。
  今天跑 `clean-articles` 就是一次数据破坏。

真正的「错误页/垃圾页」严格口径是 **0 篇**，跨源重复约 **1%**。
所以 V1 不做内容好坏打分（已有 BV 十维），不做自动丢弃，也不引 LLM：

1. **修 P0**：cleaner 无块级标签时兜底取文本，不再清空正文；
2. **统一清洗**：frontmatter / HTML 残留 / 实体 / 零宽 / 乱码 / 公众号尾巴，一个纯函数收口；
3. **内容状态**：`ok / short / empty` 落库，让「一半正文不可用」可查、可筛、可统计；
4. **存储升级**：同一篇再抓到更完整正文时覆盖旧的空/短正文，不让 `INSERT OR IGNORE` 永久丢弃。

## 1. 现状：内容进来后经过什么

主链路 `BaseFetcher._ingest`（`moni/fetch/base.py:201`）：

```
fetch() → parse() → HTML cleaner → 关键词黑/白名单 → tag 关键词门 → dedupe_insert
                        ↑ 只有 extract_text()          ↑ 丢弃进 basket
```

- `moni/fetch/cleaner.py:139` `extract_text()`：BS4 剥标签、去 script/nav/footer、压空白、
  去连续重复段落。**只处理 HTML；无块级标签时返回空串（P0）。**
- `moni/fetch/sources/bestblogs.py:35`、`moni/fetch/sources/huxiu.py:77`：各有一份公众号尾巴正则，
  同一种脏写两遍。
- `moni/fetch/filters.py` / `tag_filter.py`：主题相关性与准入，**不是内容质量**。
- `moni/db/repository.py:22` `dedupe_insert`：`INSERT OR IGNORE ON (source,url)`，
  同一 URL 更完整的正文不会更新。
- 入库后 `repository.py:106` 立刻 `apply_local_body_layout_for_ids(new_ids)`，
  **每篇新文都会生成 `brief_body_md`**（这一点后面会推翻一个错误设计）。
- `moni/scoring/heuristic.py:61`：Pin 质量分读 `raw_content` 的长度/数字/分段，
  杂质会被当正文计分。
- 下游：`render_summary` 把 `raw_content` 全量塞 prompt；`share/enrichment.py:820` 截 3,000 字。

## 2. 实测问题清单（生产库只读，2026-11-18）

数据源 `/Users/mt16/dev/moni/data/news.db`：5,330 篇，17 个 source。

| # | 问题 | 实测 | 证据 |
| --- | --- | --- | --- |
| **P0** | **cleaner 不幂等：二次清洗把正文清成空** | **32 / 34 篇**含 `<` 的正文会被写空 | `cleaner.py:150`；`clean_all_html` 按 `LIKE '%<%'` 选中它们 |
| P1 | 正文为空仍入库 | **666 篇**（bestblogs 451、huxiu 133、bestblog-yt 60、qbitai 14） | `raw_content` 空 |
| P2 | 正文 < 200 字 | **2,010 篇**（bestblogs 1,326、快讯源 208、xbot 180、qbitai 66） | `length(raw_content)<200` |
| P3 | 可确定性清洗的杂质 | **491 篇（9.2%）**，移除 38,073 字 | 本地纯函数跑全库 |
| P4 | HTML/Markdown 残留 | 33 篇含伪标签（`< img id="wx_img" ...>`） | cleaner 只认合法块级标签 |
| P5 | 同 URL 无法升级正文 | 机制缺失 | `repository.py:69` |
| P6 | 错误页/验证页 | **0 篇**（严格口径；宽松口径 12 篇全误报） | 见 §7 |
| P7 | 跨源重复 | 56 篇标题跨源重复 / 47 篇正文前 200 字重复 = **1%** | 见 §7 |
| P8 | 超长正文进 prompt | 16 篇 > 20,000 字，最长 133,479 字 | `render_summary` 无截断 |
| P9 | 发布方 100% 为空 | huxiu 301、快讯源 474、qbitai 82，共 **857 篇** | `publisher IS NULL` |

优先级：**P0 > P1/P2 > P3/P5 > P8/P9 > P4/P6/P7**。

## 3. 候选能力与取舍（含 agy 评审后的裁剪）

| 候选 | 价值 | 成本 | 风险 | 结论 |
| --- | --- | --- | --- | --- |
| A. 修 P0 + 统一清洗（纯函数） | 9.2% 正文变干净；不再破坏 32 篇 | 低 | 低（幂等、dry-run） | **V1** |
| B. 内容状态落库 + 体检报告 | 空/短正文可见、可查、可按源归因 | 低 | 低 | **V1** |
| C. 再抓取时升级正文 | 修复 P5；源补全文后自动恢复 | 低 | 中（覆盖策略要保守） | **V1** |
| D. 空/短正文进 basket | 列表更干净 | 低 | 高：666 篇是真实条目 | 不做，交你判（D1） |
| E. 全文抓取补齐（trafilatura/browser） | 直接解决一半正文不可用 | 中高 | 中 | **V2** |
| F. 跨源去重 / 同题合并 | 少 1% 噪音 | 中 | 中 | **V2** |
| G. LLM 初筛 0-100 价值门 | 挡低价值内容 | 中 | 高（成本 + 口径） | **不做**：与 BV 十维重复 |
| H. prompt 正文预算（P8） | 避免 133k 字进模型 | 低 | 低 | V1.5，另开 |
| I. 发布方归一（P9） | 857 篇补发布方，Pin 分更准 | 低 | 低 | 属元数据修复，待你点头（D2） |
| J. `fetch_log` 新计数列 | 抓取弹窗可见 | 低 | **中：派生数据，4 个模块联动** | **砍掉**（agy，同意） |
| K. `content_reasons` 复杂查询 | 细粒度诊断 | 中 | 低 | **只存不算**，不写 `json_each` 过滤 |

## 4. V1 设计

### 4.1 模块与调用点

新增 `moni/fetch/content.py`，纯函数，不碰 DB：

```python
CLEANER_ID = "clean_v1"

@dataclass(frozen=True)
class Cleaned:
    text: str
    flags: tuple[str, ...]      # frontmatter/entities/zero_width/replacement/boilerplate/html

@dataclass(frozen=True)
class ContentVerdict:
    state: str                  # "ok" | "short" | "empty"
    chars: int
    reasons: tuple[str, ...]

def normalize(raw: str, *, title: str = "") -> Cleaned
def assess(cleaned: Cleaned) -> ContentVerdict
def clean_article(art: dict) -> ContentVerdict   # 原地写入 text + content_* 字段
```

调用点四处，全部走同一个 `clean_article()`：

1. `BaseFetcher._ingest`：parse 之后、**关键词过滤之前**；
2. `ArticleRepository.dedupe_insert`：兜底（CLI / 未来调用方）；
3. `moni/web/routes/crud.py:73`：手工新建；
4. `moni/web/routes/basket.py:133`：basket rescue；
5. `moni/web/routes/articles.py:567`：手工导入全文。

> agy 核对：3、4、5 都是**直接写 SQL**，不走 `dedupe_insert`；只改 repository 会漏。

### 4.2 清洗规则（固定顺序，全部幂等）

1. **YAML frontmatter**：正文以 `---` 开头且 1,000 字内闭合才剥；正文中间的 `---` 不动。
2. **HTML 实体先解**：`html.unescape`。防 `&lt;p&gt;hello&lt;/p&gt;` 被漏掉
   （agy 发现的顺序坑；解完再判断是否含 `<`）。
3. **HTML 剥离**：`extract_text()`；修 P0 —— **无块级标签时兜底 `soup.get_text()`**，
   保证 `extract_text(extract_text(x)) == extract_text(x)`（幂等）。
4. **伪标签兜底**：只匹配 `<` + 字母/斜杠开头的残留（`<\s*[A-Za-z/][^<>\n]{0,200}>`），
   `< 100 亿` 这类数学表达式不动。
5. **零宽/BOM**：删 `U+200B/200C/200D/FEFF/2060`。
6. **乱码**：删 `U+FFFD`，不猜原文。
7. **公众号尾巴**：整行匹配才删；模式合并 bestblogs 现有清单 + huxiu 两条。
   `bestblogs._clean_description` 保留为薄包装，既有测试不变。
8. **空白**：行内多空格压一个，≥3 连续换行压成 2。

不删标题、列表、代码块、数字、标点。

### 4.3 状态判定

| state | 条件 | 含义 |
| --- | --- | --- |
| `empty` | 清洗后去空白/纯标点为空 | 无正文 |
| `short` | `0 < chars < 200` | 短稿（可能合法快讯，也可能抓取失败） |
| `ok` | `chars >= 200` | 正常 |

阈值 200 与 `scoring/heuristic.py:23` 的 `THIN_CHARS` 一致。
不做 `partial`（付费墙/截断）与 `non_article`：实测误报高、当前 0 命中，写了就是死代码。
`state` 只描述事实，**不代表该丢**。

### 4.4 数据模型（migration 049，additive）

```sql
ALTER TABLE articles ADD COLUMN content_state TEXT;          -- ok|short|empty
ALTER TABLE articles ADD COLUMN content_chars INTEGER;
ALTER TABLE articles ADD COLUMN content_reasons TEXT;        -- JSON 数组（只存不算）
ALTER TABLE articles ADD COLUMN content_cleaner TEXT;        -- 'clean_v1'
ALTER TABLE articles ADD COLUMN content_cleaned_at TEXT;
CREATE INDEX IF NOT EXISTS idx_articles_content_state ON articles(content_state);
```

**不新增 `fetch_log` 列**（agy：按 `source + fetched_at` 从 `articles` 聚合即可，少改 4 个模块）。

历史行不回填：由 `moni clean-articles --apply` 一次性补，靠 `content_cleaner` 判版本。

### 4.5 再抓取时升级正文

命中已存在 `(source,url)` 时，满足任一条件才升级：

- 旧 `content_chars == 0` 且新正文非空；
- 新正文比旧正文**清洗后**多 ≥200 字。

**不用 `brief_body_md IS NULL` 做互斥条件**：agy 核对 `repository.py:106` 会在每篇新文入库时
自动生成 `brief_body_md`，用它判断会让 2,010 篇短稿永远升不了级。
人工导入全文走 `articles.py:567`，其正文通常更长，不会被覆盖。

升级时同步：

- `raw_content`（清洗后）、`content_*`；
- 清 `summary / summary_at / brief_summary / brief_summary_at / brief_body_md`；
- commit 后 `apply_local_body_layout_for_ids([aid])` 重建排版；
- 若在 Pin 中，`recompute_quality_if_pinned`。

`dedupe_insert` 的 `inserted` 语义不变，升级数单独 `logger.info` 记录。

### 4.6 下游护栏

- **异步摘要** `moni/llm/jobs.py:326`：`content_state == 'empty'` 或正文空白时，
  直接 `_mark_failed(job_id, "no_body")`，不调模型。
  与同步接口 `moni/web/routes/llm.py:52` 的既有行为对齐（agy 核对：同步已拦、异步没拦）。
- **简报/分享**：不整份失败。空正文的文章跳过正文摘要，保留标题 + 原文链接；
  事实核查写「无正文，无需核查」（沿用 `share/enrichment.py:821` 的现有温和处理）。

### 4.7 可观测

- `moni clean-articles`：默认 **dry-run**，`--apply` 才写；
  输出 `scanned / changed / empty / short / chars_removed`；
  只 UPDATE 实际变化的行（agy：避免 4,839 篇无谓写触发 FTS5 触发器）。
  保留 `clean_all_html()` 签名，内部委托新函数（现有测试 `test_cleaner.py` 不变）。
- 新增只读 `moni content-audit`：按 source 输出 `总数 / ok / short / empty` 与占比；
  直接查 `articles`，不依赖抓取日志。
- 前端：V1 暂不加筛选 chip（D5），避免无谓标签。

## 5. 验收

单测（新增 `tests/test_content_normalize.py`、`tests/test_content_assess.py`、`tests/test_content_upgrade.py`）：

1. 每种杂质一条 + 一条「不误删」（正文中间 `---`、行内「点击关注」、`营收 < 100 亿`）；
2. **P0 回归**：`extract_text` 幂等；纯文本含 `<ENV_ID>` / `< img ...>` 时保留正文；
   32 篇真实样本抽 5 篇断言二次清洗后非空；
3. **双重转义**：`&lt;p&gt;hello&lt;/p&gt;` 清洗后不残留 `<p>`；
4. 幂等：`normalize(normalize(x)) == normalize(x)`；
5. 状态边界：0 / 1 / 199 / 200 字；纯标点 → `empty`；
6. 升级：`empty→非空` 覆盖；`+199` 字不覆盖；`+200` 字覆盖；
   **旧文已有 `brief_body_md` 时仍能升级**，且清空旧 `summary`、重建 `brief_body_md`；
7. 直写路径一致性：`POST /api/articles`、basket rescue、导入全文都写 `content_*`；
8. 异步摘要空正文：状态 `failed`、`error='no_body'`、未调用 `call_llm`；
9. `clean-articles --apply` 幂等，二次 `changed=0`。

生产口径（只读预演）：

- 全库：`changed=491 / empty=666 / short=2010 / chars_removed≈38,073`；
- `LIKE '%<%'` 的 34 篇：修复前空 32 篇 → 修复后 0 篇；
- 抽样 20 篇改动前后对照，人工确认无正文丢失；
- 现有 4 篇 Pin 短稿，清洗后质量分逐条列出（预期只降不升）。

## 6. agy 评审采纳记录

| agy 发现 | 我的核实 | 采纳 |
| --- | --- | --- |
| `brief_body_md IS NULL` 会让升级全失效 | `repository.py:106` 入库即生成 `brief_body_md` | **采纳**，改为字数比较 |
| `crud.py` / `basket.py` / `articles.py` 不走 `dedupe_insert` | 三处都是直接 SQL | **采纳**，加进调用点 |
| `&lt;p&gt;` 双重转义顺序坑 | `extract_text("&lt;p&gt;…")` = `""` | **采纳**，先 unescape |
| 升级后要清旧摘要 | `articles.py:567` 已有先例 | **采纳** |
| 砍掉 `fetch_log` 三列 | 派生数据可从 `articles` 聚合 | **采纳** |
| 不做可插拔 pipeline / partial / 中位数阈值 | 5k 量级，规则固定 | **采纳** |
| 加 effective_ratio / 句子完整度指标 | 会扩大状态机与口径 | **不采纳**：纯标点已归 `empty`，先不做 |
| 引入 trafilatura / ftfy / simhash | 对「已有纯文本」无收益 | **采纳**：V1 不引；V2 全文补齐再评估 |

## 7. 明确不做（附证据）

- **错误页/验证页分类**：严格口径 0 命中；宽松口径 12 篇全误报
  （「服务异常」「安全验证」出现在正常正文）。
- **跨源去重**：1%，且「同题不同角度」与「转载」界线模糊。
- **LLM 质量门**：与 `bv_rubric_v1` 重复；BestBlogs Dify 初筛是价值判断，不是清洗。
- **自动丢弃空/短正文**：666 篇空正文里有真实新闻（如「燧原科技IPO过会」）。
- **URL 规范化**：agy 提醒 `id = sha256(source+url+title)`，V1 动 URL 会造重复行；留给 V2。

## 8. 与进行中分支的冲突

`feature/sprint-54-xbot-content-quality`（worktree `moni-worktrees/sprint-54-...`，未提交）
改了 `moni/fetch/filters.py`、`moni/fetch/tag_filter.py`、`moni/fetch/sources/xbot_feed.py`、
`moni/web/routes/__init__.py`、`STATUS.md`。

本 sprint **不碰以上文件**，新增模块 + `base.py` + `repository.py` + migration 049 + CLI +
`crud.py` / `basket.py` / `articles.py` / `llm/jobs.py` + 前端（如 D5 选是）。
冲突面：`base.py`（低）、`STATUS.md`（收尾时按最新 main 重写）。

## 9. 拍板结果（2026-11-18）

| # | 决策项 | dad 的决定 |
| --- | --- | --- |
| D1 | 空/短正文：只打状态还是进 basket | **只打状态**：空正文是合规常态（只能抓标题），后续人工导入正文；绝不自动丢弃、绝不自动补抓 |
| D2 | 发布方归一（857 篇） | 不进本 sprint，单独一条 PR |
| D3 | 摘要遇到空正文 | **直接失败**（`no_body`）；简报只跳过正文，不整份失败 |
| D4 | 历史全量清洗 | 页面上先预演、再执行；可重复执行 |
| D5 | 前端 | **要做**：一页「数据清洗」，可预演、可执行、可导出报告、可对外汇报 |
| D6 | 生产库 4 行测试数据（源 `s` / `testsrc`） | **同意清理**，但要软归档到菜篮子可回捞 |

---

## 10. 实现落地记录

### 10.1 与 §4 原设计的差异（都有生产数据支撑）

| 原设计 | 落地做法 | 原因（实测） |
| --- | --- | --- |
| frontmatter 按 1,000 字内闭合剥离 | 追加「块内每行必须是 `key:`」+ 超 1,000 字或长文里占一半 → 判 `frontmatter_embedded`，**不动正文** | bestblogs 把整篇 2,464 字文章塞进 `title: "…"`，正则一路吃到正文里的 `---`，一次删掉 1,333 字 |
| `html.unescape` 单次 | 循环解到稳定（≤5 轮） | 单轮时 `&amp;lt;b&amp;gt;` 只解一层，留下的那层正好被下一次清洗删掉——就是「二次清洗清空正文」的另一副面孔 |
| 无块级标签才兜底 `soup.get_text()` | 改为**门控**：块级标签 ≥8 且标记字符占比 ≥5% 才调 `extract_text()`；否则就地剥标签（块级标签→换行） | 34 篇含 `<` 的正文只有 1 篇是真 HTML。8,110 字的技术文夹一行 `<h1>404 Not Found</h1>` 被抽成 13 字；5,617 字被抽成 3,554 字 |
| 伪标签正则 `<\s*[A-Za-z/][^<>\n]{0,200}>` | 只匹配真实 HTML 标签名 | 放宽版会吃掉 `i < customerNum; i++` 与 `<bean id="…">`，Java/Spring 文章被删 473 字代码段 |
| 公众号尾巴整行匹配 | 三层：行首页脚整行删 / 短行含版权声明的整行删 / 行尾 CTA 仅在尾巴 ≤30 字且无句读时截断 | `商务合作` 命中「东方枢纽国际**商务合作**区」、`扫描二维码` 命中「乘客在车站扫描二维码后，…”、`版权归` 命中「版权**归属**、内容合规性」 |
| `content_reasons` 只存不算 | 存 + 概览按 flag 计数（JSON 精确匹配 `"flag"`） | 页面要能一眼看出「清了什么」，这是对外汇报的核心 |
| 前端暂不加 | 新增 `/cleaning` 页 + 导航 | dad D5 |

### 10.2 新增的护栏

- **清空长正文即跳过**：清洗后为空、且原正文 ≥200 字 → 不写，计 `emptied`（本次 0）。
- **正文变了就作废派生文本**：`summary / ai_summary / brief_summary / brief_body_md` 清空并重建排版；
  事实核查结论**不清**（基于证据，重跑昂贵）。
- **异步摘要 `no_body`**：正文为空直接失败，不烧 token。
- **样例窗口**：预演对照取首次差异附近 160 字，否则页脚类改动在头部根本看不出来。

### 10.3 最终实测（生产库最新快照，5,335 篇）

| 步骤 | scanned | changed | backfilled | emptied | empty | short | chars_removed | 测试行归档 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 预演 | 5,335 | 880 | 4,455 | 0 | 667 | 2,016 | 47,551 | 0 |
| 执行 | 5,335 | 880 | 4,455 | 0 | 667 | 2,016 | 47,551 | 4 |
| 再执行 | 5,331 | 0 | 0 | 0 | 665 | 2,014 | 0 | 0 |

杂质命中：公众号尾巴 494、HTML 实体 82、零宽 84、乱码 55、frontmatter 7、
frontmatter 内嵌正文 1、HTML 残留 8。

> 与 §0 的 5,330 / 491 / 666 差异说明：生产库持续抓取（本次快照比 §0 多 5 篇 xbot）；
> 清洗规则收紧后命中口径也变了（例如 `\u00a0` 计入空白压缩、纯标点归 `empty`）。

### 10.4 落地文件

新增：`moni/fetch/content.py`、`moni/cleaning/{__init__,service}.py`、`moni/web/routes/cleaning.py`、
`moni/db/migrations/049_content_cleaning.sql`。

修改：`moni/fetch/base.py`、`moni/fetch/cleaner.py`、`moni/fetch/sources/{bestblogs,huxiu}.py`、
`moni/db/repository.py`、`moni/db/migrate.py`、`moni/llm/jobs.py`、`moni/cli/main.py`、
`moni/web/routes/{__init__,crud,basket,articles}.py`、`moni/web/static/{index.html,css/main.css,js/app.js}`。

测试：`tests/test_content_normalize.py`、`test_content_assess.py`、`test_content_upgrade.py`、
`test_cleaning_service.py`、`test_cleaning_api.py`。

### 10.5 独立 audit 修复记录（PR #238）

独立 read-only audit 结论：无 P0，4 条 P1 必修。全部修复并有回归：

| finding | 修复 | 回归 |
| --- | --- | --- |
| 只补状态的行也清空 `summary/ai_summary/brief_*`（生产副本 37/125/48 → 0） | UPDATE 拆成「内容列」与「派生文本列」两组，只有 `new != old` 才清派生文本 | `test_backfill_keeps_derived_text` |
| 「会清空长正文」护栏只在清洗服务里，导入全文/手工编辑/菜篮子 rescue/批量 HTML 清洗裸写 | 新增 `clean_for_write(art, old_text)`，五条写路径统一调用 | `TestWriteGuard` + `test_import_fulltext_guard_rejects_empty_result` |
| 「编辑/责编/校对」按前缀误删正文（真实案例：`3978a92ddfe933d3` 的「编辑完规则后…」） | 从 `_FOOTER_START` 移除这三个词，改为短署名形态正则 | `test_editor_sentence_survives` / `test_proofread_sentence_survives` |
| `a<b 且 b>c` 被当 HTML 标签闭合吃掉 | 标签体只允许属性形态，不接受比较表达式正文 | `test_cjk_comparison_prose_survives` |

顺手修复的 P2：Markdown 代码围栏原样保留；`Q:/A:` 不当 YAML frontmatter；
测试数据只按 `source IN ('testsrc','s')` 白名单归档；`no_body` 只拦 `summary/brief`，
不再影响 `fact_check/fact_check_plus`；前端 `loadCleaning` 校验 HTTP 状态、`states` 缺失防崩；
migration 048 只在 `content_contract IS NULL` 时写入；emptied 行也打 `content_cleaner`，
避免「待清洗」永不收敛；UPDATE 带 `raw_content` 旧值条件（lost-update 防护）。

生产库最新副本复演（修复后，5,330 行）：

| 步骤 | changed | backfilled | emptied | 摘要保留 |
| --- | ---: | ---: | ---: | --- |
| apply1 | 875 | 4,455 | 0 | `summary/ai/brief` 只按正文变化清 |
| apply2 | 5 | 0 | 0 | 不再变化 |
| apply3 | 0 | 0 | 0 | 幂等 |

> `changed=875/872` 与 §10.3 的 `880` 差异来自 audit 修复后规则更保守：
> 署名形态、属性形态标签、代码围栏和 `Q:/A:` 防护少删了少量文本；apply2 的 5 篇是
> 旧 cleaner 输出中残留的多余空白，第三次为 0。
