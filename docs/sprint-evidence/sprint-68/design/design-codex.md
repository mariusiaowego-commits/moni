# 方案 B（codex 版）：moni「对大模型的信息脱敏」

> 本文是设计方案，不是实现。所有「事实」都带 `path:line`；「判断」标明是设计决策。
> 参考实现：`/private/tmp/magpie-src/magpie`（HEAD `fc4406fb`）；官方演示视频 8 步流程已 OCR 校对。

## 0. 一句话结论

在 **moni 进程内**做一条「出站遮蔽 → 本地留本 → 入站还原」的管道，检测层不用 magpie 的通用 secret 规则，
而用 moni 自己才有的**垂类语义层**（内部 ID、目标清单、投研结论、作者身份），
再把「遮了多少 / 还原成功率 / 有没有泄漏」做成 moni 自己的面板——这是 magpie 结构上做不到的那部分。

## 1. 现状事实（已核）

### 1.1 moni 的 LLM 出站没有单一收口

| 出站点 | 覆盖的调用 | 证据 |
|---|---|---|
| `FailoverRunner._invoke` | 摘要、文内核查、tag、案例、事件、方向研究、方向打分、地理、关键词、连通测试 | `moni/llm/runner.py:518`、argv `:520` |
| `summary_gen._invoke_hermes` | 分享侧文内核查 / 业务价值 / 正文排版 / 短摘要 | `moni/share/summary_gen.py:114` |
| `worker_llm.run_hermes` | 非 HTML 简报等分享成品 | `moni/share/worker_llm.py:264` |
| reader | 阅读器对话（`hermes chat -q`，非 `-z`） | `moni/llm/reader.py:362` |
| `run_hermes_stream` | fact_check_plus 流式 | `moni/llm/stream.py:70` |
| bank bot | 银行同业抓取 | `scripts/bank_bot/run_bot.py:657` |

- 出站载荷是**单段纯文本 prompt**（`hermes -z {prompt}`），没有多轮 messages 结构。
- 链路：moni → hermes profile `moni` → provider `magpie` → `http://127.0.0.1:3425/v1`
  （`~/.hermes/profiles/moni/config.yaml` 的 `providers.magpie.base_url`）。
- 已有两种**单向**脱敏，方向都跟本方案相反：
  - `moni/llm/redaction.py:1` — LLM **输出**落库前脱敏成 `[REDACTED:type]`；
  - `moni/web/redact.py:1` — Web API **出站**脱敏，DB 仍存真值（`tests/test_source_url_redaction.py:204`）。

### 1.2 magpie 的脱敏机制（已核，代码级）

- 插入点：网关**出站前**遮蔽、**入站后**还原，同一层对称完成——`internal/gateway/redact.go:65`
  `redactedHits()`：先 `JSONHitsContext` 改写出站 body（`:78`），再 `m.Writer(w)` 包住回包写入器（`:103`）。
- 占位符：`{{KIND_xxxxxxxx}}`，id = `HMAC-SHA256(key, kind‖value)` → base32 取前 8 位小写
  （`internal/redact/redact.go:828` `tag()`、`:834` `placeholder()`）；同 kind 同值恒同码，key 落盘所以重启后仍稳定
  （`internal/redact/redact.go:808` `loadKey()`，key 文件 `~/.config/magpie/redact.key`，32 字节、0600）。
- 本本：**纯内存** `values = map[string]string{}`（`internal/redact/redact.go:766`），
  上限 200k、满时淘汰最久未用的一半（`:778` `maxValues`、`:859` `makeRoom()`）；**原值从不落盘**。
- 还原：`internal/redact/redact.go:1736` `restore()`——只还原本本里存在的码，**未知码原样保留**（`:1755`）；
  工具参数走 JSON 转义路径（`internal/redact/json.go:547` `asJSON`）。
- 检测：内置规则（`internal/redact/redact.go:170`）+ 用户词/规则（`internal/redact/rules.go:26`、`:121`）
  + 可选本地模型（`internal/redact/model.go:30`；AstrLink Guard ONNX 约 39MB `internal/pii/astrlink.go:33`）
  + 已脱敏值复认（`internal/redact/known.go:36`）。
- 可观测：`internal/maskrec/maskrec.go:71` `Record()` 记 kind/条数/耗时/还原处数，**不含原值**；
  落 `~/.config/magpie/masking/<date>.jsonl`（`:225`）；面板 API `internal/gui/masking.go:112`。
- 失败策略：脱敏侧 **fail-closed**（无 masker 就不发请求，`internal/gateway/redact.go:92`），
  还原侧未知码保留原样。

## 2. 插入点：三个候选与取舍

| 候选 | 做法 | 收益 | 代价 / 失败模式 |
|---|---|---|---|
| **A. moni 进程内逐出站点包裹**（选它） | 每个 hermes 调用点出站前 `shield_prompt()`、回包后 `unshield()` | 业务语义可控；可归因到具体 job；不改任何外部配置；能在 moni 自己的 UI 上演示 | 要碰 6 个出站点；漏一个就有明文外发 |
| B. 本地 sidecar 代理 | moni 起代理，hermes profile 指过去 | 一次改造覆盖全部出站 | 违背 `moni/llm/gateway.py:7`「只读：不写 hermes 配置、不写 magpie 配置」红线；多一跳延迟；多一个常驻进程 |
| C. 用 magpie 自己的 redact | 改 magpie 设置 | 零开发，引擎最成熟（含本地 NER） | 不是 moni 的 feature，评委看不到归属；**全局生效**会连 Codex/Hermes 一起遮；magpie 不知道 moni 的业务语义；无法做垂类分级 |

**选 A。** 决定性理由：magpie 的遮蔽是**通用 secret** 的（key/密码/邮箱/身份证），
而 moni 真正要拦的是**投研结论级敏感**（内部评分、目标清单、明日关注名单、作者身份）——
这类东西没有任何通用引擎能识别，只有 moni 自己知道。技术上也简单：出站载荷是纯文本 prompt，包一层不需要解析结构。
防护的等价性不成立这点不构成理由：A 在业务侧，B/C 在链路侧，是**纵深两层**而不是替代关系。

**收敛成本的诚实估计**：MVP 只接 `runner.py:518` 一处（覆盖摘要/文内核查/tag/案例/事件/方向研究/方向打分/地理/关键词/连通测试，
即绝大多数调用），M2 再补 share / reader / stream / bank bot。靠一条测试守住「不许出现裸 hermes 调用」。

## 3. 检测层：三级分级（这是 moni 的护城河）

| 级 | 内容 | 手段 | 占位符类型 |
|---|---|---|---|
| **L0 硬凭据**（必脱） | `sk-` 系 API key、UUID token、Bearer、`key=value`（`moni/llm/redaction.py:20`、`:28`、`:36`、`:40`、`:45` 六模式直接复用）+ URL query token / URL userinfo / 本地绝对路径 / 主机名 / 内网 IP | 正则 + 词典 | `SECRET` `TOKEN` `PATH` `HOST` `IP` |
| **L1 垂类内部信息**（默认脱，可白名单） | 文章内部 ID、目标清单机构（`config/bank_targets.json`）、方向研究 scope/out_of_scope、方向分与业务价值分、Pin 备注、作者/公众号身份、私有分享链接 | moni 自有数据源 + 用户自定义词表 | `INTERNAL_ID` `TARGET` `CONCLUSION` `AUTHOR` |
| **L2 语义等价替身**（M3，可选） | 把机构名换成**同行业但虚构**的名字，模型推理仍成立 | 替身词典 + 一致性映射 | `ORG_ALIAS` |

> L1 是 magpie 做不到的：magpie 只有通用规则 + 用户词表（`internal/redact/rules.go:26`），
> 它不知道「这条是 moni 的内部评级」。**差异化的一句话**：
> *magpie 遮的是「密钥」，moni 遮的是「不该外传的判断」。*

## 4. 占位符方案

- 语法 `{{TYPE_xxxxxxxx}}`，与 magpie 同构（好处：心智一致，将来可与 magpie 指纹互认）。
- id = `HMAC-SHA256(key, type‖value)` → base32 前 8 位小写；**同值恒同码**（模型看到的是一致的指代），
  **跨请求稳定**（同一篇文章在摘要与打分里是同一个码）。
- key：32 字节本地文件 `~/.config/moni/shield.key`（0600，缺失时随机生成）。
  **不违反「moni 不持有任何 API key」**：那条原则针对的是第三方模型凭证（`moni/llm/gateway.py:3`），
  这里存的是本地派生 HMAC 盐，不用于访问任何外部服务。
- 类型前缀只保留业务上有意义的：`SECRET` `TOKEN` `EMAIL` `PHONE` `PATH` `HOST` `IP`
  `INTERNAL_ID` `TARGET` `CONCLUSION` `AUTHOR` `ORG_ALIAS`。

## 5. 本本（值 ↔ 编码）

- **进程内存** `dict[str, str]`，LRU，上限 20000（比 magpie 的 200k 小一个量级，因为 moni 的值域小得多）。
- **原值不落盘**；落库的只有元数据（占位符、类型、hint 首字符、长度、命中数、来源 job）。
- 作用域：全局（跨请求），否则同一篇文章在两次调用里会是两个码，模型会以为说的是两回事。
- 额外选项（默认关）：`keep_map` 把映射落 SQLite `shield_map`（值可加密存）。
  默认关的理由：映射落库就等于多一份「值→码」资产，而原值本来就在 `articles` 表里，落盘不带来收益。

## 6. 还原

- 位置：每个出站点的**返回值**处，与出站对称。
- 未知码：**原样保留**并计 `unknown`（模型幻觉或改写），绝不猜值——同 magpie（`internal/redact/redact.go:1755`）。
- 变体容忍：大小写归一、缺一侧花括号、多余空格（对齐 magpie 的 `canonical()`/`braces()`，`internal/redact/redact.go:1780`、`:1793`）。
- JSON 上下文（reader 的 stream-json、`call_llm_json`）：走**值级**还原——先解析 JSON，再替换字符串值，
  不在裸文本上做正则替换，避免破坏结构。
- 写库前自检：还原后扫描残留 `{{TYPE_xxxxxxxx}}`，非零则重试一次并告警（防止占位符污染 `articles.summary`）。

## 7. 回退与失败语义

| 场景 | 行为 | 理由 |
|---|---|---|
| 脱敏器内部异常 | 原样出站 + 记 `error`，**不阻断** | 脱敏是保护层不是安全闸；分析系统不能因为脱敏器 bug 就停摆（与 magpie 的 fail-closed 相反，magpie 是密码管理器语义） |
| 严格模式 | 开启后异常直接失败请求（对齐 magpie `internal/gateway/redact.go:92`） | 给「宁可停不可发」的用户 |
| 还原未知码 | 保留占位符原文 + 计数 | 不猜、不泄露 |
| 模型拒答/超时 | 与脱敏无关，走原有重试 | — |

## 8. 可观测与可复算指标（决赛要展示的东西）

新增表 `shield_records`（migration `058`，`_CURRENT_VERSION` 57 → 58，`moni/db/migrate.py:21`）：

```
id, created_at, job, model, article_id,
masked_count, kinds_json, restored_count, unknown_count,
prompt_chars, masked_prompt_chars, took_ms, leak_scan_hits, error
```

指标（都能用 SQL 复算）：

- 覆盖率 = 有脱敏的请求数 / 全部 LLM 请求数
- 类型分布 = `kinds_json` 展开后按类型计数
- 还原成功率 = `restored_count / (restored_count + unknown_count)`
- **泄漏断言** = 对每次出站 payload 做一次「已知敏感串集合」子串扫描，`leak_scan_hits` 必须恒为 0
  （这是「敏感信息从未离开本机」的可复算证据）
- 性能开销 = `took_ms` 的 p50/p95

对照 magpie：magpie 只有「遮蔽条数 / 类型分布 / 按 agent 分流」，**没有还原成功率、没有泄漏断言**
（`internal/maskrec/summary.go:122` 的 `Summary` 字段里没有这两项）。这是 moni 可以领先的两格。

## 9. UI（落在 moni 自己界面，评委看得见）

- 位置：`moni/web/static/index.html` 配置页 → 「大模型设置」下新增「脱敏」分区
  （现结构：网关 `:3235`、用量统计 `:3455`，同级加第三块）。
- 面板四块（学 magpie 的 `internal/gui/assets/plugins/redact/client.js:19-32`，但换成 moni 的口径）：
  1. 统计卡：累计遮蔽条数 / 覆盖请求数 / 还原成功率 / 泄漏断言命中数（必须 0）
  2. 实时列表：按请求（job、模型、遮蔽条数、耗时、还原数、unknown 数）
  3. 四栏对照：原文 → 发给模型的内容 → 模型回包 → 本地还原后（magpie 的 maskSteps 同款）
  4. 脱敏实验室：贴一段真实文章 → 立即看脱敏结果（不发网络请求），可一键「真跑一次」看全链路
- 新增 API 前缀 `/api/shield/*`：`summary` `records` `record` `preview` `try` `rules`
  （新增端点，不改既有契约；路由挂在 `moni/web/routes/` 下，与 `llm.py:22` 的 `/api/llm` 平级）。

## 10. 里程碑（单工程师 2–3 周）

| 阶段 | 交付 | 验收标准 | 契约变更 |
|---|---|---|---|
| M0 骨架 | `moni/llm/shield/`（detect/placeholder/book/restore）+ 单测；接 `runner.py:518` | 单元测试覆盖：同值同码、未知码不还原、异常不阻断；`runner` 路径出站 payload 无敏感串 | 无 |
| M1 可用 | 配置项 + `shield_records` 表 + 记录写入 + 泄漏断言 | 跑 20 次真实 job，`leak_scan_hits` 全 0；还原成功率 100% | **schema 058**（需点头） |
| M2 演示级 | 脱敏面板 + 四栏对照 + 实验室 + 截图素材 | 浏览器实测面板数字与 SQL 复算一致 | 新增 API |
| M3 硬化 | 补齐 share / reader / stream / bank bot 四个出站点；加「不许有裸 hermes 调用」的仓库级断言 | `rg` 断言全绿；全量 pytest 绿 | 无 |

## 11. 风险（按严重度）

1. **遮蔽过度，模型推理失效**（把机构名全遮了，模型不知道是银行）→ 分级 + 白名单 + 类型前缀 + 实验室预览。
2. **还原不全，占位符污染落库** → 写库前残留扫描 + 重试 + 告警。
3. **出站点漏包**（6 处只接了 1 处）→ 仓库级 `rg` 断言 + 每次 release 检查。
4. **回包泄漏**（模型在回信里复述出敏感值）→ 回包也扫敏感串，命中则不落库并告警。
5. **分享路径把还原后的真值送出去** → 与 `moni/web/redact.py` 的出站脱敏协同，分享链路默认再脱一次。
6. **性能开销** → 规则 memo 缓存；目标 p95 < 10ms/请求，实测后再定。
7. **key 文件丢失** → 占位符变化，历史记录不可复现；提示用户备份（`moni/web` 设置区给一句提醒）。

## 12. 两周最小版（上台演示够用）

- **留**：L0 规则 + L1 里最容易做的两项（内部 ID + 目标清单机构名）、`runner._invoke` 一个接入点、
  出站遮蔽 + 回包还原、四栏对照页、泄漏断言计数。
- **砍**：L2 语义替身、share/reader/stream 三个出站点、映射落盘、严格模式。
- 演示动线：打开面板（显示本周遮蔽 N 处、泄漏 0）→ 现场跑一篇带敏感信息的文章 →
  展开四栏对照（模型只看到编码、回包已还原）→ 指着泄漏断言说「这一列永远是 0，且可复算」。

---

## 附：与 magpie 的对照表（可放汇报页）

| 维度 | magpie | moni 本方案 |
|---|---|---|
| 插入层 | 网关代理（全局，跨所有客户端） | moni 业务进程内（只管 moni 的 LLM 调用） |
| 检测语义 | 通用 secret（key/密码/邮箱/身份证）+ 本地 NER | 通用 secret（复用现有 6 模式）+ **投研垂类内部信息** |
| 本本 | 内存 200k，值不落盘 | 内存 20k，值不落盘（可选落盘，默认关） |
| 还原 | 同层对称，未知码保留 | 同上 + 写库前残留断言 |
| 指标 | 遮蔽条数 / 类型分布 / 按 agent 分流 | 上游三项 + **还原成功率** + **泄漏断言命中数** |
| 失败语义 | fail-closed（不发请求） | 默认 fail-open（分析不中断），可选严格模式 |
| 演示归属 | magpie 自己的 Insights 面板 | moni 自己的配置页，评委在 moni 里看到 |

