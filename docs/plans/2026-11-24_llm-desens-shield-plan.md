# moni「对大模型的信息脱敏」实施规格（合并版 v1.0）

- 日期：2026-11-24
- 分支：`feat/desens-llm-shield`（基线 `main` @ `247cfc0` / v1.0.122，SQLite `_CURRENT_VERSION = 57`）
- 状态：进行中
- 设计来源：agy 方案 A（`/private/tmp/herdr-dispatch/tasks/20261124-112654-moni-desens-design-agy/result.md`，615 行）
  + codex 方案 B（`/private/tmp/moni-desens-design-codex.md`）的 2 项增量
- 参考实现：magpie `fc4406fb`（`/private/tmp/magpie-src/magpie`）、官方演示录屏 8 步

---

## 0. 一句话与三个支撑点

**在 moni 进程内、hermes 子进程启动前的最后一跳收口全部 5 条出站通道，做「出站遮蔽 → 本机本本 → 入站还原」双向管道**，
让外部大模型只做盲态推理，moni 的凭据、同业投研意图与内部判断不出本机。

1. **垂类深度**：通用 secret 之外，覆盖 moni 才有的内部判断——同业机构/项目**全简称同义归并**（`招商银行`/`招行`/`CMB` 恒定同码）、
   关注名单、内部方向研究标尺；并用 Luhn / 模 11 校验位 + 金融量词白名单保证 `5.138 亿元`、`1.7 万网点` 零误伤。
2. **任务感知**：按 `task_type` 切换策略——联网核查**不遮公开主体**（否则公网搜不到）、PNG 生图改用**中文泛称**
   （否则编码被烤进像素）、结构化 JSON 走**词法感知还原**（否则含引号的原值炸 `json.loads()`）。
3. **可证伪**：每次调用写审计账本（零原值），产出**还原成功率**、**出站泄漏断言**、**写库残留断言**三项 magpie 没有的指标，
   配「原文 → 本本 → 模型收发 → 本地还原」四栏演练台，数字可现场用 SQL 复算。

---

## 1. 现状事实（已核，附 `path:line`）

### 1.1 moni 的 LLM 出站有 5 条独立 hermes 通道

| # | 位置 | 覆盖 | argv |
|---|---|---|---|
| ① | `moni/llm/runner.py:518` `_invoke` | 摘要 / 文内核查 / tag / 案例 / 事件 / 方向研究 / 方向打分 / 地理 / 关键词 / 连通测试 | `["hermes","-z","{prompt}",…]`（`:520`） |
| ② | `moni/llm/stream.py:51` `run_hermes_stream` | `fact_check_plus`（联网核查） | `hermes chat -q … --format stream-json -t web`（`:70`） |
| ③ | `moni/llm/reader.py:350` `run_reader_stream` | 阅读器实时问答 | `hermes chat -q … --oneshot -Q --format stream-json`（`:362`） |
| ④ | `moni/share/worker_llm.py:213` `run_hermes` | 分享产物（非 HTML 简报等） | `hermes -z {prompt} --profile …` |
| ⑤ | `moni/share/summary_gen.py:108` `_invoke_hermes` | 简报摘要 / 快速核查 / 通用十维分 / 正文排版 / 紧凑摘要 | `hermes -z {prompt}` |

- `scripts/bank_bot/run_bot.py` 只吃 `config/bank_targets.json` 的公开栏目，不读私有新闻库正文 → **不纳入**。
- 载荷形态：**单段纯文本 prompt**，无多轮 messages 结构；`llm_model_mode=profile` 时不传 `--provider/-m`
  （`moni/llm/client.py:162`、`moni/llm/runner.py:598`），跟随 `~/.hermes/profiles/moni/config.yaml`
  → provider `magpie` → `http://127.0.0.1:3425/v1`。切 `explicit` 模式则**直连公网、绕过 magpie**（这是必须自建防线的原因之一）。

### 1.2 已有脱敏全是反向单向

- `moni/llm/redaction.py:42` `redact_text()`：LLM **输出**落库前替换成 `[REDACTED:type]`；其中 `UUID_PATTERN`（`:37`）无条件替换所有 UUID。
- `moni/web/redact.py:30` `_TOKEN_PARAM_NAMES` + `:66` `sanitize_source_url()`：Web API **出站**抹 URL 里的 token，DB 仍存真值
  （`tests/test_source_url_redaction.py:204` 明确断言）。
- **发往大模型前：空白**。

### 1.3 语料量化（`sqlite3 "file:data/news.db?mode=ro"` 只读复算）

| 指标 | 值 | 口径 |
|---|---|---|
| 文章总数 | 6,352 | `articles` 总行数 |
| 有实质正文 | 3,100 | `length(raw_content)>200` |
| 正文含邮箱样式串 | 475（占有正文 15.3%） | `raw_content GLOB '*@*.*'` |
| 正文含 11 位数字串 | 257 | 11×`[0-9]` GLOB |
| URL 带 query | 3,236（占 50.9%） | `url LIKE '%?%'` |
| 署名来源数 | 765 | `count(DISTINCT publisher)` |
| `llm_jobs` 有 token 计量 | 85 行 / 累计 5,010,513 input tokens | 均值 ≈ 5.9 万 token/次 |

### 1.4 magpie 机制（供对齐，非复用代码）

- 插入点：网关出站改写 body + 同层包回包写入器，**对称**：`internal/gateway/redact.go:65`（`:78` 改写、`:103` 包 `m.Writer(w)`）。
- 占位符：`HMAC-SHA256(key, kind‖value)` → base32 前 8 位小写 = `{{KIND_xxxxxxxx}}`：`internal/redact/redact.go:828` `tag()`、`:834` `placeholder()`；
  正则 `internal/redact/redact.go:331`。key 落 `~/.config/magpie/redact.key`（32B/0600，`:808` `loadKey()`）。
- 本本：**纯内存** `values` map（`internal/redact/redact.go:766`），上限 200k、淘汰最久未用一半（`:778`、`:859`），**原值永不落盘**。
- 还原：`internal/redact/redact.go:1736` `restore()`；**未知码原样保留**（`:1755`）；JSON 上下文走 `jsonEscape`（`internal/redact/json.go:547`）。
- 二次脱敏免疫：`apply()` 会跳过已落在占位符内的 span（`internal/redact/redact.go:1627-1630`）——
  **故 moni 生成的 `{{KIND_xxxxxxxx}}` 与 magpie 网关天然正交叠加**。
- 缺口：`internal/maskrec/maskrec.go:216` 落盘时清空 `Back`（还原计数）→ **magpie 算不出还原成功率**；脱敏侧 fail-closed（`internal/gateway/redact.go:92`）。

### 1.5 对设计稿一处事实错误的纠正（以代码为准）

设计稿称「moni 提示词大量用 `str.format()`，会吃掉双花括号」，并引用 `moni/llm/prompts/summary.md:15-18`。**实测不成立**：

- `moni/llm/prompts/__init__.py:4` 明确写「实现用 regex 而非 `str.format()`，避免模板里 ```json { ... }``` 块的冲突」，
  渲染器是 `_PLACEHOLDER_RE.sub`（`:14`、`:26`）；`prompts/` 下所有模板走这条路径。
- `str.format()` 也不会改写**被替换进去的值**，只处理模板本身 → 「值里的 `{{...}}` 被剥成 `{...}`」机制不存在。
- 真正用 `str.format()` 的只有分享侧 5 处：`moni/share/summary_gen.py:98`、`:105`、
  `moni/share/enrichment.py:833`、`:1106`、`:1266`；`scripts/default_worker.sh:1085-1109` 是纯 shell 字符串拼接。

**插入点结论不变，但理由改为**：① 5 条通道各有自己的 argv 拼装点，模板渲染后、进程启动前是**唯一统一收口点**；
② 掩码后的载荷与真正发出的 argv 逐字节一致，可断言；③ 不必在 5 个模板体系里重复插桩。

---

## 2. 插入点决策

**选：在 moni 进程内、模板渲染完成后、`hermes` 子进程启动前拦截。**

| 候选 | 结论 | 理由 |
|---|---|---|
| **A. moni 进程内统一门面**（选它） | ✅ | 5 条通道 100% 覆盖；拿得到 `task_type`/`article_id`/是否联网/是否生图；指标直接落 `news.db`；不改任何外部配置 |
| B. 只改 `call_llm()` | ❌ | 覆盖约 55%，漏掉 ②③④⑤ 四条；决赛现场点阅读器或一键简报即穿帮 |
| C. 依赖 magpie 网关 | ❌ | `explicit` 模式直连公网绕过网关；magpie 只见聊天 JSON、不知业务意图；日志不在 moni、无法归因到 job；还原计数不落盘 |
| D. 本地 sidecar 代理 | ❌ | 新增常驻进程与端口，违反 `moni/llm/gateway.py:7`「只读：不写 hermes 配置、不写 magpie 配置」 |

**收口原则**：5 处薄切面，**保持既有函数签名向后兼容**（新增能力走可选参数或内部包裹）。

---

## 3. 检测层（纯标准库，零新依赖）

### 3.1 四路检测器（优先级从高到低）

1. `dict`——用户配置的投研专有词与**同义别名组**（`招商银行` ≡ `招行` ≡ `CMB`，归并到同一 canonical 值 → 同一码）。
   词边界对齐 `moni/fetch/keyword_match.py:106-113` 的 `[A-Za-z0-9_]` 规则，防 `CMB` 误伤 `CMake`；中文按最长前缀优先。
2. `custom`——用户自定义前缀/正则：≤32 条、正则 ≤300 字符、禁匹配空串（对齐 `internal/redact/rules.go:26-35`），编译期校验防 ReDoS。
3. `rule`——内置高确定性规则：
   - URL 凭据参数：复用 `moni/web/redact.py:30` `_TOKEN_PARAM_NAMES` + `:55` `_JWT_VALUE_RE`（19 个参数名）→ `URL_TOKEN`。
   - 密钥类：`sk-`（移植 `notASlug` 排除 `sk-hynix-third-quarter` 这类新闻 slug，`internal/redact/redact.go:525-540`）、
     `gh[pousr]_`、`github_pat_`、`AIza`、`AKIA`/`ASIA`、`Bearer`、键值赋值、中文口令句式（`密码/口令/密钥/私钥/授权码` + `为/是/[:：=]`）、
     PEM 私钥块。
   - PII（**数学自校验，防误伤金融数字**）：
     - 身份证 18 位 + **GB 11643 模 11 校验位**（移植 `internal/redact/redact.go:730-737`）；
     - 银行卡 15–19 位 + **Luhn 模 10**（`:739-760`）+ 金融语境放行（紧邻 `.`、JSON 裸数字、后接 `亿元/万元/元/美元/bp/%/股/户/笔`）；
     - 手机号 `1[3-9]\d{9}` + 非毫秒时间戳边界；邮箱排除示例域与 `@2x.png`/`@AGENTS.md`。
   - **不误伤内部 ID**：移植 `internal/redact/ids.go:33-78` `noIDs` + `credentialField`——UUID 与 16–32 位 hex
     （`articles.id`）只在紧邻凭据字段名时才脱敏，且**绝不允许手机号/卡号正则把长 hex 截成半截**。
4. `known`——跨轮记忆：已脱敏且长度 ≥8 的原值进入会话级 known 集合，后续脱离上下文再次出现仍按同一码脱敏
   （对齐 `internal/redact/known.go:29-74` 的 4-gram 位图思路）。

### 3.2 三级分级与默认开关

| 级 | KIND | 默认 | 说明 |
|---|---|---|---|
| **L1 凭据**（必脱） | `PRIVATE_KEY` `API_KEY` `URL_TOKEN` `JWT` `BEARER` `PASSWORD` `SECRET` | 开 | 外发即失守；`PRIVATE_KEY` 可配 `block` 阻断 |
| **L2 投研 + 金融隐私**（建议脱） | `PHONE` `ID_CARD` `BANK_CARD` `EMAIL` `ORG` `SELF_ORG` `PROJECT` `WATCHLIST` `SENSITIVE_VIEW` | 开 | 泄的是行内投研意图 |
| **L3 基础设施**（可选） | `PATH_USER` `INTERNAL_HOST` `PUBLIC_IP` `PUBLISHER` | `PATH_USER` 开，其余关 | `/Users/<name>` 保形替身；署名默认不遮 |

### 3.3 本地 NER 模型：M0–M2 **不引入**

- 理由：`internal/redact/model.go:274-300` 与 `internal/masking/masking.go:184-204` 记录了通用 NER 在结构化语料的高误报
  与 ~6 KB/s 吞吐；moni 的敏感对象都具有强结构特征或属于用户有限集合，确定性引擎准确率 >99%、耗时 <2ms。
- M3 若启用：`QuantumNous/astrlink-guard`（Apache-2.0，38.96 MB int8 ONNX，`internal/pii/astrlink.go:33-37`），
  macOS 走 CoreML，1.5s 超时熔断并回退规则引擎；不进默认依赖。

---

## 4. 占位符规范（**冻结契约**）

- 语法：`{{<KIND>_<8位小写RFC4648-Base32>}}`
- 正则（**必须与 magpie 字节级一致**）：`\{\{[A-Z][A-Z0-9_]{0,23}_[a-z2-7]{8}\}\}`
  （对齐 `internal/redact/redact.go:331`；Base32 字符集为 `a-z` 与 `2-7`，不含 `0,1,8,9`）
- 生成：

```python
def make_placeholder(kind: str, canonical_value: str, salt: bytes) -> str:
    digest = hmac.new(salt, f"{kind}\x00{canonical_value}".encode(), hashlib.sha256).digest()
    return "{{" + kind + "_" + base64.b32encode(digest).decode()[:8].lower() + "}}"
```

- **同值恒同码**（同 `kind` + 同 `canonical_value` → 同码，跨请求、跨轮次、跨重启）；别名组归并到同一 `canonical_value`。
- salt：`~/.moni/desensitize.salt`，32 字节 `secrets.token_bytes(32)`，目录 0700 / 文件 0600。
  **这不是 API key**（不用于访问任何外部服务），与「moni 不持有任何 API key」红线自洽。
- **形状保真**：URL 只替换参数值（保留协议/域名/参数名）；`/Users/<name>` → `/Users/u_<8位Base32>`；
  PNG 生图用中文泛称（`某股份制银行`），**杜绝 `{{...}}` 进像素**。

---

## 5. 映射本本（**冻结契约**）

- **原值只驻内存**：`InMemoryVault`（`threading.RLock` + LRU，上限 50,000，TTL 24h），
  **永不进 `news.db`、永不写日志**（对齐 `internal/redact/redact.go:766` 与 `internal/maskrec/maskrec.go:5-10`）。
- SQLite 只存**零原值**审计元数据（占位符、kind、首字符提示、长度、命中数、还原数）。
- 作用域：单请求 `RequestScopeVault` + 进程级 `InMemoryVault` 双层。
- 跨进程例外（仅 `html_landing` 需要）：`~/.moni/run/desens-<task_id>.bin`，HMAC 派生密钥 + 完整性校验，
  绑定单个 `share_task_id`，TTL 30 分钟，`trap EXIT` 立即 unlink + 启动清扫孤儿。
- UI 提供「一键清空内存本本」与「轮换 salt」（轮换即清空 vault）。

---

## 6. 还原（**冻结契约**）

### 6.1 五级容错（中文模型增强）

1. 精确匹配 `{{KIND_xxxxxxxx}}`
2. 空白/大小写折叠：`{{ org_r4k7m2qa }}`、`{{ORG_R4K7M2QA}}`（对齐 `internal/redact/redact.go:1780` `canonical()`）
3. 残缺括号吞并：`{ORG_x}`、`{{ORG_x}`、`{ORG_x}}`、裸 `ORG_x`（前后非 `[A-Za-z0-9_]`）（对齐 `:1793` `braces()`）
4. **中文全角折叠**：`｛｛ORG_x｝｝`、`【ORG_x】`、全角下划线 `＿`
5. **本请求活跃集 8 位后缀回查**：模型把 `ORG` 汉化成 `机构` 时，按 8 位 Base32 后缀在本次请求 vault 内唯一命中才还原

### 6.2 分输出形态处理

- **整包自然语言**：`restore_text(reply)`，写库前执行。
- **流式（阅读器 / fact_check_plus）**：`StreamRestorer` 维护尾部缓冲，末尾疑似占位符前缀（`{`/`{{`/`｛`/`【` 或活跃 `KIND_` 前缀，<38B）不立即吐出，
  还原后再推给前端 → **打字机动画全程不闪现半截 `{{ORG_` 再跳变**；流结束 `flush()`。
- **结构化 JSON**（cases / events / fact_check_plus / 方向研究与打分）：**词法感知还原**——
  扫描时跟踪匹配位点是否在 JSON 双引号字符串字面量内；在字面量内 → `json.dumps(val, ensure_ascii=False)[1:-1]` 转义后填入；
  模型漏写引号成裸值 → 自动补完整 JSON 字符串。**不做裸文本正则替换**。
- **HTML 产物**：`html.escape(val, quote=True)` 后写入，防破 DOM / 本地 XSS。

### 6.3 合法性校验与幻觉处置

- 还原前必须查本本。查不到时：
  - `KIND == "EXAMPLE"`（如 `{{EXAMPLE_00000000}}`，全 `0` 非法 Base32，永不会被还原）：
    与其它非法码同样替换为可读泛称并计入 `invalid_code_count`，**豁免的是「不计入幻觉」而不是「保留字面量」**
    —— 唯一硬不变量是**任何 `{{...}}` 都不允许落进 `articles.summary` / `llm_jobs.result` / `reader_messages.content`**；
  - 其余「形如占位符但本本没有」的编码分两类，**都不允许落进 `articles.summary` / `llm_jobs.result` / `reader_messages.content`**：
    1. **非法 Base32 字符集**（含 `0/1/8/9` 的手误码，如 `{{ORG_r4k7m2q1}}`）→ 替换为可读泛称，
       计入 `invalid_code_count`，**不计入 `hallucinated_count`**（它不是幻觉，是转写损坏）；
    2. **合法 Base32 但本本没有** → **模型幻觉**：`hallucinated_count += 1`；
    自然语言输出替换为可读泛称（`ORG/SELF_ORG` → `【某相关机构】`、`PROJECT` → `【某业务项目】`、`PERSON` → `【某相关人员】`、其他 → `【脱敏项】`）；
    **严格 JSON/代码输出保留字面量**（防止破坏下游枚举/非空校验）。
- **绝不盲删整句、绝不抛异常**。

---

## 7. 增量指标（magpie 没有的三项，本 feature 的核心可证伪点）

### 7.1 出站泄漏断言（codex 增量 ①）

每次出站后，对**真正发出的 payload** 做一次「已知敏感串集合」子串扫描，
命中数记入 `leak_scan_hits`，**必须恒为 0**。这是「敏感信息从未离开本机」的**运行时可复算**证据。

- 扫描集合来源：本请求被脱敏掉的全部原值 + 配置的自定义敏感词表；
- 扫描时机：子进程启动**之前**（对 argv 里的 prompt 串），
- 任何一次命中 → 记 `error='leak_scan_hit'`、阻断本次调用（fail-closed，仅此一项）、并写审计行。

### 7.2 写库前残留断言（codex 增量 ②）

还原完成后、写库前，扫描残留 `{{KIND_xxxxxxxx}}`：

- 命中 → `unrestored_count` 递增、**重试一次还原**（多级容错重跑），仍残留则落可读泛称 + 写告警；
- **不允许占位符污染 `articles.summary` / `llm_jobs.result` / `reader_messages.content`**。

### 7.3 指标口径（可复算）

| 指标 | 口径 |
|---|---|
| 过检调用数 | `count(checked=1)` |
| 累计替换处数 | `sum(masked_total)` |
| **去重敏感实体数** | `COUNT(DISTINCT placeholder)`（**不得把每行 `masked_distinct` 相加**，否则同一机构在 10 次调用里被虚增成 10 个） |
| 精确还原成功率 | `sum(restored_total) / sum(restored_total + unrestored_count)`；分母为 0 记 100%（无回显残留） |
| 端到端零残留率 | `count(masked_total>0 AND unrestored_count=0) / count(masked_total>0)` |
| **出站泄漏命中数** | `sum(leak_scan_hits)`，**期望恒为 0** |
| Token 净变化 | `sum(estimate_tokens(before) - estimate_tokens(after))`，口径沿用 `moni/llm/runner.py:38`，界面标「公式估算」（沿用 `moni/stats/savings.py:143` 的防质疑惯例） |
| 延迟开销 | `(mask_latency_us + restore_latency_us)/1000` 的 P50 / P95，目标 **P95 ≤ 2.5 ms、P50 ≈ 1.7 ms**（6,000 字密集研报实测 1.8–2.1 ms；原 2.0 ms 口径在繁忙主机上会抖动，故放宽到 2.5 ms 并保留实测分布） |

---

## 8. 数据契约（migration `058`，**待授权**）

遵循 `moni/AGENTS.md` §2 迁移纪律：纯 additive、`INSERT OR IGNORE` 不覆盖用户配置、`_CURRENT_VERSION` 由 57 → 58 并全仓 `rg "_CURRENT_VERSION"` 复核
（测试用 `MAX(schema_version) == _CURRENT_VERSION` 动态断言，`tests/test_db.py:98`；无字面量 `== 57` 断言）。

```sql
-- moni/db/migrations/058_llm_desensitize.sql
INSERT OR IGNORE INTO app_settings (key, value, updated_at) VALUES
    ('desensitize.enabled', '1', NULL),
    ('desensitize.level_l1', '1', NULL),
    ('desensitize.level_l2', '1', NULL),
    ('desensitize.level_l3', '0', NULL),
    ('desensitize.strict_fail_closed', '0', NULL);

CREATE TABLE IF NOT EXISTS desensitize_rules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_type     TEXT NOT NULL CHECK (rule_type IN ('word','regex','allowlist')),
    kind          TEXT NOT NULL DEFAULT 'ORG',
    pattern       TEXT NOT NULL,
    aliases_json  TEXT NOT NULL DEFAULT '[]',
    pseudonym     TEXT,
    action        TEXT NOT NULL DEFAULT 'mask' CHECK (action IN ('mask','warn','block')),
    match_case    INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    notes         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_desens_rules_active ON desensitize_rules(is_active, rule_type);

CREATE TABLE IF NOT EXISTS desensitize_events (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id                TEXT NOT NULL UNIQUE,
    job_id                 TEXT,
    share_task_id          TEXT,
    article_id             TEXT,
    call_site              TEXT NOT NULL,   -- runner|stream|reader|worker_llm|summary_gen
    task_type              TEXT NOT NULL,
    provider               TEXT,
    model                  TEXT,
    policy_mode            TEXT NOT NULL DEFAULT 'standard',
    checked                INTEGER NOT NULL DEFAULT 1,
    blocked                INTEGER NOT NULL DEFAULT 0,
    masked_total           INTEGER NOT NULL DEFAULT 0,
    masked_distinct        INTEGER NOT NULL DEFAULT 0,
    restored_total         INTEGER NOT NULL DEFAULT 0,
    fuzzy_restored_total   INTEGER NOT NULL DEFAULT 0,
    unrestored_count       INTEGER NOT NULL DEFAULT 0,
    hallucinated_count     INTEGER NOT NULL DEFAULT 0,
    leak_scan_hits         INTEGER NOT NULL DEFAULT 0,   -- 期望恒为 0（codex 增量①）
    residual_placeholders  INTEGER NOT NULL DEFAULT 0,   -- 写库前残留断言（codex 增量②）
    fallback_used          TEXT,
    prompt_chars_before    INTEGER NOT NULL DEFAULT 0,
    prompt_chars_after     INTEGER NOT NULL DEFAULT 0,
    tokens_delta_est       INTEGER NOT NULL DEFAULT 0,
    mask_latency_us        INTEGER NOT NULL DEFAULT 0,
    restore_latency_us     INTEGER NOT NULL DEFAULT 0,
    kinds_json             TEXT NOT NULL DEFAULT '{}',
    by_json                TEXT NOT NULL DEFAULT '{}',
    items_json             TEXT NOT NULL DEFAULT '[]',   -- 无原值
    created_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_desens_events_created  ON desensitize_events(created_at);
CREATE INDEX IF NOT EXISTS idx_desens_events_task     ON desensitize_events(task_type, created_at);
CREATE INDEX IF NOT EXISTS idx_desens_events_job      ON desensitize_events(job_id) WHERE job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_desens_events_article  ON desensitize_events(article_id) WHERE article_id IS NOT NULL;
```

**硬约束**：`desensitize_events` **无任何原值列**（只有 `head_hint` 首字符提示与长度）。测试须用哨兵密钥断言 DB 字节流零出现。

---

## 9. 任务感知策略矩阵

| `task_type` | 策略 | 特殊规则 |
|---|---|---|
| `summary` / `fact_check` / `case_extract` / `event_extract` / `direction_research` / `direction_score` / `reader_chat` | `standard` | 标准编码 → 还原 |
| `fact_check_plus`（联网核查） | `web_search_safe` | 脱凭据与内部保密词，**保留公开机构名**——否则公网搜到 0 命中 |
| `info_graphic_png` / `report_png` | `raster_generalize` | `ORG`/`PROJECT` 换**中文泛称**，杜绝编码进像素 |
| 分享成品（`plain_text`/`markdown`/`rich_text`/`pdf`/`html_landing`） | `standard` | `html_landing` 额外走文件级还原钩子 |

---

## 10. 回退矩阵

| 故障 | 行为 | `fallback_used` |
|---|---|---|
| 单条自定义正则编译失败 | 跳过该条 + 告警，其余继续 | — |
| 规则表读库失败 | 回退进程内 `last_known_good_rules` | — |
| 脱敏引擎整体异常 | 走**零依赖极简擦除器**（仅 `sk-`/`Bearer`/URL `token=`）后放行；**不因脱敏让分析任务失败** | `mask_error_scrub_pass` |
| 严格模式（`strict_fail_closed=1`） | 脱敏异常直接失败请求（对齐 magpie `internal/gateway/redact.go:92`） | — |
| 还原异常 | 返回模型原始回复，任务正常置 `done` | `restore_error_raw` |
| 还原后仍有残留 | 写可读泛称 + 告警徽章，**不触发任务失败重试**（避免双倍 Token） | `restore_error_raw` |
| 模型见到占位符拒答 | 出站注入 `[系统脱敏说明]`；仍拒答则 L2/L3 实体换拟真中文假名透明重试 1 次（**L1 凭据绝不裸发**） | `pseudonym_retry` |
| **出站泄漏断言命中** | **阻断本次调用**（唯一 fail-closed 项）+ 写审计行 | `leak_scan_hit` |

---

## 11. UI 契约

- 位置：`/config` → 「大模型设置」（`moni/web/static/index.html:3226-3232` 的 `.segmented-control`）下新增第 3 个子分段「信息脱敏」。
- 第一区：4 张概览卡（防线状态 / 累计拦截处数与去重实体 / **还原成功率** / **泄漏断言命中数（必须 0）**）+ 总开关。
- 第二区：**四栏演练台**——
  ① 本地原文（高亮命中的 KIND） ② 本机内存小本本（`{{ORG_x}} ↔ 招商银行`，徽章「仅驻本机内存 · 未落库」）
  ③ 供应商收发实况（实际发出去的脱敏 prompt + 模型回包） ④ 本地还原终稿（附本次实测脱敏/还原耗时与还原率）。
  内置 3 个剧本：含 RSS token 与员工邮箱 / 含本行简称归并与立项代号 / 模型故意漏括号与全角改写。
- 第三区：分级开关 + 投研词库 CRUD（列：KIND / 主词·正则 / 同义别名 / 泛称 / 动作 / 状态 / 操作，操作用 `.row-actions`）。
- `/stats` 新增第 8 块「隐私脱敏防线」；样本 < 10 显示「数据积累中」（`moni/stats/repository.py` `N_DEFAULT_THRESHOLD`）。
- 业务流徽章：`masked_total>0` 的任务行与阅读器气泡显示「已脱敏 N 项」，`floating.js` 浮层看类型明细。
- 窄屏 390×844 折 2×2 网格；亮暗双主题对比度 ≥4.5:1；`?v=` 版本号按 `index.html:32`/`:5156`/`:5170` 现有惯例递增。

---

## 12. 里程碑与验收（**M0 为本轮开发范围**）

| 阶段 | 交付 | 可判定验收 | 契约变更 |
|---|---|---|---|
| **M0 内核骨架**（本轮） | 新建 `moni/llm/desensitize/`：`salt.py` `vault.py` `rules.py` `restorer.py` `engine.py` `policy.py` `__init__.py`；`tests/test_desensitize_core.py` | ① 单测 ≥45 用例全绿（实交付 72 例）；② 同值/别名恒同码；③ 身份证模 11、银行卡 Luhn、金融金额零误伤；④ 含 `"` 与 `\n` 的原值还原后 `json.loads()` 通过；⑤ 流式 delta 任意切分零裂缝；⑥ 占位符正则与 magpie **写入侧**一致（kind 截断 24，比其读取侧更严格）；⑦ 6,000 字中文研报 `mask+restore` **P95 ≤ 2.5 ms**（实测 1.8–2.1 ms，P50 ≈ 1.7 ms）；⑧ **不新增任何第三方依赖**；⑨ 全量 pytest 不回归 | **无**（`_CURRENT_VERSION` 保持 57） |
| M1 全链路可用 | migration 058 + 5 条通道收口 + 任务策略 + `moni desensitize-audit` 只读回放 CLI | 5 条通道均产生审计行；DB 字节流零原值；真实库回放金融数字/百分比/日期/`article_id` 误判 = 0 | **schema 57→58（需授权）** |
| M2 演示级 | 四栏演练台 + `/stats` 第 8 块 + 徽章 + 离线 showcase | 8901 真浏览器 1280×720 与 390×844 实测无裁切/无溢出/对比度达标；面板数字与复算 SQL 逐位一致；断网 `file://` 可交互 | 新增 API（纯增量） |
| M3 硬化 | 拒答假名重试 + 跨进程临时槽 + 90 天归档 + 可选本地 NER | 拒答桩触发 1 次重试成功且 L1 不裸发；并发无锁竞争 | 可选依赖（不进默认） |

**两周 MVP 裁剪**（若排期压到 10 个工作日）：留 M0 + 3 条主通道（runner/reader/summary_gen）+ 四栏演练台 + 泄漏断言；
砍 本地 NER、拒答重试状态机、跨进程文件槽、90 天归档、4/5 号通道。

---

## 13. 风险（按严重度）

| 级别 | 风险 | 触发 | 影响 | 缓解 |
|---|---|---|---|---|
| P0 | **结构化 JSON 还原破坏语法** | 原值含 `"` `\` `\n` 被模型放进 JSON 字符串字段 | `json.loads()` 报 `JSONDecodeError`，案例/事件/联网核查整批失败 | 强制 JSON 词法感知还原（§6.2），字面量内转义、裸值自动补引号、异常回退原串 |
| P0 | **真实原值落 SQLite 或演示包** | 实现图省事把 `placeholder→raw_value` 写库或打进 showcase | 违反「凭据不落库」红线；拷库即泄密 | 表无原值列；哨兵密钥断言 DB 字节流零出现；showcase 剧本只用虚构凭据 |
| P1 | **金融数字被误判** | 11 位金额、小数、16 位 hex ID 被手机/卡号规则截断 | 摘要失真、按 id 回写匹配不到行（`moni/share/summary_gen.py:191`） | 模 11 / Luhn 校验位；`noIDs`+`noNumbers` 守卫；上线前 `desensitize-audit` 全库回放 |
| P1 | **联网核查搜不到 / 生图烤进编码** | `fact_check_plus` 遮机构名；PNG 收到 `{{...}}` | 核查全判待核实；海报成废片 | 策略矩阵硬隔离（§9） |
| P1 | **流式跨帧切碎 / 中文全角改写** | delta 切在占位符中间；模型写 `｛｛ORG_x｝｝` 或汉化前缀 | 阅读器闪编码、最终残留乱码 | `StreamRestorer` 尾缓冲 + 五级容错 + 残留降级泛称 |
| P2 | **模型拒答** | 小模型把 `{{...}}` 当模板变量 | 返回无效拒答 | 注入 `[系统脱敏说明]`；L2/L3 换假名重试 1 次 |
| P2 | **SQLite 写锁 + 记账失败** | 高频流式并发写审计表 | `database is locked` | 单条 INSERT + 立即 commit（<0.5ms），记账失败只打日志不阻断主流程 |
| P2 | **端口/环境串扰** | 验证时误碰生产 8900 | 生产事故 | 只用 8901；禁止 `launchctl`、禁止 kill 进程 |

---

## 14. 冻结接口（M0 交付物必须遵守，开发与架构分工的边界）

```python
# moni/llm/desensitize/__init__.py 对外只暴露这些
from .salt import load_salt, salt_path
from .vault import InMemoryVault, RequestScopeVault
from .engine import mask_text, mask_prompt, MaskResult
from .restorer import restore_text, restore_json_aware, restore_html_safe, StreamRestorer
from .policy import TaskPolicy, PolicyMode, resolve_policy
from .rules import Rule, UserRules, builtin_rules
```

- `mask_text(text, *, kind_hint=None, vault=None, policy=None) -> MaskResult`
- `MaskResult` 至少含：`text`、`masked_total`、`items`（无原值）、`vault`。
- `restore_text(text, vault, *, escaped=False) -> str`
- `restore_json_aware(text, vault) -> str`
- `StreamRestorer(vault).feed(delta) -> str`（可立即吐出的已还原片段）+ `.flush() -> str`
- 占位符生成、salt 读写、vault 存取**只在上述模块内实现**，M0 不得新增跨模块私有工具函数。

---

## 15. 附录：magpie 源码索引（供实现对照）

| 主题 | 位置 |
|---|---|
| 占位符生成 | `internal/redact/redact.go:828` `tag()`、`:834` `placeholder()` |
| 本体内存表 | `internal/redact/redact.go:766`、`:778` `maxValues`、`:859` `makeRoom()` |
| 还原 | `internal/redact/redact.go:1736` `restore()`、`:1780` `canonical()`、`:1793` `braces()` |
| 二次脱敏免疫 | `internal/redact/redact.go:1627-1630` `apply()` 跳过占位符内 span |
| 网关对称插入点 | `internal/gateway/redact.go:65` `redactedHits()`、`:78`、`:103` |
| 指纹（跨机去重） | `internal/masking/fingerprint.go:24` `Fingerprint()` |
| 审计记录 | `internal/maskrec/maskrec.go:71` `Record()`、`:225` 落盘、`:216` 清空 `Back` |
| 还原计数缺口 | `internal/maskrec/summary.go:122` `Summary()`（无还原成功率字段） |
| 用户规则上限 | `internal/redact/rules.go:26-35`、`:121` `customRules()` |
| 不误伤 ID | `internal/redact/ids.go:33-78` `noIDs()` |
| 形状替身 | `internal/redact/shape.go:88` `shapeOf()`、`:370` `standInOf()`、`:398` `ipStandIn()` |
| JSON 感知还原 | `internal/redact/json.go:547` `asJSON()` |
| 占位符提示注入 | `internal/gateway/placeholder_note.go:24` |
| 本地模型（可选） | `internal/pii/astrlink.go:33`、`:25`、`:50`；`internal/pii/guard/guard.go:74` `MinScore` |

