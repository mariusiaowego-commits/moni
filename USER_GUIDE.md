# moni 使用指南

> 同业信源自动抓取与本地新闻库。**v1.0.9.10 完成** (2026-06-30)。
> 这份文档给**用户**（你自己）看 —— 怎么用 moni，不讲代码细节。

---

## 1. 启动

```bash
cd /Users/mt16/dev/moni

# 1. 装包 (首次)
pip install -e .

# 2. 初始化 DB (首次, 或 schema 升级后)
python3 -m moni db init

# 3. 启动 web 服务
python3 -c "import uvicorn; from moni.web.server import create_app; uvicorn.run(create_app(), host='127.0.0.1', port=8900, log_level='error')"
# 或: bash scripts/web.sh start

# 4. 浏览器打开
open http://127.0.0.1:8900/
```

**停止 web**:
```bash
bash scripts/web.sh stop
# 或: pkill -f "uvicorn.run(create_app"
```

---

## 2. 服务控制 (v1.0.6.0 新增)

topnav 右侧显示服务状态：
- 🟢 绿点 + PID: 服务运行中
- ⚪ 灰点: 服务已停止
- ↻ 按钮: 确认后重启 (~3 秒恢复)
- 点击状态区域: 刷新状态信息

---

## 3. 7 个菜单

| 菜单 | 作用 |
|---|---|
| **文章查阅** | 浏览所有抓回文章 (含 LLM 摘要/事实核查, source/tag filter, 全文搜索, tag chip hover 详情) |
| **CRUD** | 编辑/删除单篇文章 (含新建 modal — v1.0.5.11), 两步删除 confirm |
| **跑批日志** | cron 任务执行历史 |
| **抓取管理** | 5 源配置 + 手动触发抓取 + 健康检查 |
| **分享** | 11 种格式导出 (card grid — v1.0.5.11, 1 卡 1 篇文章) |
| **LLM 任务** | 异步 LLM 摘要/事实核查进度面板 (v1.0.5.2, 状态徽章 + 自动轮询) |
| **配置管理** | 业务源 / 业务 tag (8 色 swatch 选色) / 关键词过滤 CRUD (v1.0.2 新增, v1.0.5.12 swatch palette 升级) |

---

## 4. 配置管理 (核心)

### 3.1 抓取源 tab

10 个默认源 (DB 主用, YAML 备用):

| name | URL | 抓什么 | 抓取间隔 |
|---|---|---|---|
| 36kr_rss | https://36kr.com/feed | 36氪 科技/创投 | 15 min |
| mpaypass | https://www.mpaypass.com.cn/ | 移动支付网 银行/支付 | 30 min |
| qbitai | https://www.qbitai.com/ | 量子位 AI | 30 min |
| wechat_rss_a | https://wechatrss.waytomaster.com/api/rss/all?token=...账号A | 微信公众号聚合 A | 60 min |
| wechat_rss_b | https://wechatrss.waytomaster.com/api/rss/all?token=...账号B | 微信公众号聚合 B | 60 min |
| huxiu | https://rss.huxiu.com/ | 虎嗅 科技商业 | 15 min |
| bestblogs | OPML 聚合 (公众号 + ALL 技术博客) | BestBlogs 公众号聚合 | 5 min |
| bestblog-podcast | OPML 聚合 | BestBlogs 播客 (60 feeds) | 5 min |
| bestblog-yt | OPML 聚合 | BestBlogs YouTube (20 feeds) | 5 min |
| bestblog-x | 本地 OPML | BestBlogs X/Twitter (160 feeds) | 5 min |

**新建源**: 点"+ 新建 source" → 填 id / name / url / type / interval → 保存
**改 tags**: 行内 "改 tags" 按钮 → 多选弹窗
**停用源**: 不删, is_active 设为 false 即可 (行内"编辑")
**抓取配置**: bestblogs/bestblog-* 源可点「抓取配置」编辑 max_feeds / max_articles_per_feed 等参数

### 3.2 业务 tag tab

5 个默认 tag (业务范围: 银行 × 支付 × 科技):

| name (PK, 不可改) | 显示名 (display_name, v1.0.5.14 起可改) | 颜色 | 业务范围 |
|---|---|---|---|
| banking | 银行 | 蓝 #2563EB | 银行 (央行罚款/上市/财报/并购) |
| payment | 支付 | 绿 #16A34A | 支付 (PayPal/银联/二维码/跨境) |
| ai-fintech | AI×金融 | 红 #DC2626 | AI × 金融 (智能投顾/风控/大模型落地) |
| regulation | 监管 | 橙 #EA580C | 监管 (政策/法规/合规) |
| general-tech | 通用科技 | 灰 #6B7280 | 通用科技 (其他) |

**v1.0.5.14 起**:`display_name` 字段是给 UI 展示用的中文名(可改),`name` 仍是 PK 英文标识(不可改,改了引用全断)。前端"业务 tag"和"抓取源"两处都按 `display_name || name` 兜底。

**新建 tag**: 点"+ 新建 tag" → 填 name (英数下划线) / display_name (可选, 中文展示名) / description / **color (8 色 swatch 单选, v1.0.5.12)** → 保存
**改名**: 选中后点编辑 → 改 display_name 即可(name 字段灰着不可改)
**删 tag**: 会级联清 source_tags + article_tags 引用

### 3.3 关键词过滤 tab

3 个默认 blacklist:

| 源 | mode | match_field | keywords |
|---|---|---|---|
| 36kr_rss | blacklist | title+content | 入境游/文娱/明星/游戏/影视/电商/消费/美食 |
| qbitai | blacklist | title+content | 机器人/自动驾驶/波士顿动力/Tesla |

**新建 filter**: 点"+ 新建 filter" → 选 source + mode (blacklist/whitelist) + 填 keywords (逗号分隔) → 保存
**改 source 下拉**: 顶部下拉框可只看某源的 filter

**filter 行为**:
- **blacklist**: title 或 content 命中任一 keyword → **drop**
- **whitelist**: title 或 content 必须命中至少 1 个 keyword → 否则 drop
- 多个 filter 按 (source, mode) 可同时生效
- filter 改后**新抓**立即生效，**老数据不重过滤**

---

## 5. 抓取

### 5.1 全部抓取 (v1.0.6.2 新增)

抓取管理页顶部 **⚡ 全部抓取** 按钮：
- 一键跑所有 6 个源
- 显示每个源的拉取/入库/去重/过滤明细
- 周一自动补抓周末数据
- 结果写入 cron_runs + fetch_log 日志表

### 5.2 Tag 过滤丢弃明细 (v1.0.6.4 新增)

抓取结果下方有「Tag 过滤丢弃明细 (N 条)」折叠面板：
- 点击展开 → 看到被过滤掉的文章标题列表
- 帮你判断 tag 关键词是否设置过严（好文章被误丢）
- 全部抓取时按源分组显示，单源抓取时直接显示

### 5.3 单源抓取

每行有「立即抓取」按钮，只跑该源。

### 4.1 手动抓取

```bash
# 全部启用源
python3 -m moni fetch

# 单源
python3 -m moni fetch --source 36kr_rss
python3 -m moni fetch --source mpaypass

# dry-run (不入库)
python3 -m moni fetch --dry-run

# 健康检查
python3 -m moni fetch --health
```

### 4.1.1 36kr captcha 维护 (v1.0.5.5+)

36kr.com 用字节跳动 TTGCaptcha 拦截所有非浏览器客户端 (httpx 返 1460 bytes captcha HTML)。v1.0.5.5 起 36kr fetcher 走真 Chrome (Profile 7, port 9223) 抓取。

**首次配置** (一次性, 30 秒):

```bash
# 1. 清残留 Chrome
pkill -f "remote-debugging-port=9223" 2>/dev/null; sleep 1

# 2. 启 headed Chrome 弹窗, 拖动 TTGCaptcha 滑块
cd /Users/mt16/dev/moni
MONI_36KR_HEADED=1 /usr/local/bin/python3 scripts/manual_captcha.py
# → Chrome 窗口弹出 → 36kr.com/feed 加载 → 拖滑块过 captcha
# → terminal 按 Enter → cookie 落盘 Profile 7

# 3. 验证能抓真文章
/usr/local/bin/python3 -m moni fetch --source 36kr_rss
# 应该看到 fetched=30 inserted=20+
```

**Cookie 失效后** (1-4 周一次, 看 36kr 风控严格度):

如果 `moni fetch --source 36kr_rss` 看到 `36kr returned a captcha challenge page` 警告, 重跑步骤 2 一次。

**注意事项**:
- **不要**在本机日常工作浏览器登 36kr 触发 ban (会污染 Profile 6 风控状态)
- Profile 7 是 moni 专用 cookie 仓, 与 x-watch 用的 Profile 6 隔离
- 如果 Profile 7 被 ban (持续要 captcha 怎么也过不了), 改 `MONI_CHROME_USER_DATA_DIR` env var 用 Profile 8

### 4.1.2 WeChatRSS 多账号 (v1.0.5.6+)

waytomaster 免费账户上限 2 公众号。多个 token 可追踪 4+ 公众号, 不付费。

**当前 5 源结构**:

| source_name | token 位置 | 公众号数 | 状态 |
|---|---|---|---|
| wechat_rss_a | 嵌在 source.url | 2 | ✅ 启用 |
| wechat_rss_b | 嵌在 source.url | 2 | ✅ 启用 |
| wechat_rss_c / d / e | 嵌在 source.url | 0 | 占位, 待加 token |

> **v1.0.5.9**: legacy `wechat_rss` 退役 (DB 不再 seed, env var 路径废). `moni fetch --source wechat_rss` CLI 仍能用 (走 WECHAT_RSS_TOKEN env var). 4 篇历史文章 `source='wechat_rss'` 保留.

**前端加新账号** (推荐, 不动代码):

```
配置管理 → 抓取源 → + 新建 source
  id:        src-wechat-rss-c
  name:      wechat_rss_c
  type:      wechat_aggregator
  url:       https://wechatrss.waytomaster.com/api/rss/all?token=完整 token
  interval:  60
  is_active: ✓
```

**跑一次验证**:

```bash
/usr/local/bin/python3 -m moni fetch --source wechat_rss_c
# 应该看到 fetched=2~20 inserted=2~20 (按账号关注公众号活跃度)
```

**注意**:
- token 必须用**完整 JWT 字符串** (不要简写 `.env` 那种前 4 后 4 形式, waytomaster 返 401 Invalid Token)
- DB `UNIQUE(source, url)` 约束, 不同账号 (a/b/c) 即使追踪同一公众号也**不会冲突**
- **v1.0.5.7**: publisher 字段已修 — 自动从 title 前缀 `[公众号名]` 抽出, DB 里能看到完整公众号名; 历史数据跑 `moni backfill-publisher` 一次回填

### 4.2 自动抓取 (cron)

moni 有**两套** cron 机制:

**A. 进程内 APScheduler** (web 启动时跑, 调 `config/schedule.yaml`):

```bash
# 启动 web 时自动跑 cron
bash scripts/web.sh start
# 或: python3 -m moni cron start
python3 -m moni cron status
python3 -m moni cron stop
```

**B. 5min crontab + default worker** (T7, 0a71c88, 扫 `~/.moni/queue/*.json`):

```bash
# 安装 5min crontab 条目 (拉 default profile agent 处理 share manifest 队列)
bash scripts/install_cron.sh install
bash scripts/install_cron.sh status
bash scripts/install_cron.sh uninstall
```

详细 manual testing 步骤见 [`docs/CRON.md`](docs/CRON.md).

`config/schedule.yaml` 定义 APScheduler 调度。

### 5.3 抓取结果解释 (v1.0.6.1 新增)

「立即抓取」后，结果区域双栏展示：

**左侧 — 结构化解释**:
- ✅ 新增 X 篇文章入库 / ⚠️ 全部重复 / ℹ️ 无新内容
- 抓取流程: 远程拉取 → 解析 → 去重 (跳过 N 条)
- 过滤情况: 黑名单/白名单/Tag 过滤丢弃数，或「全部通过」

**右侧 — 原始 JSON** (保留不变，调试用)

### 5.4 抓取日志含义

```
[36kr_rss]  fetched=30  parsed=30  cleaned=30  inserted=24  parse_errors=0
[36kr_rss]  keyword filter: 30 input, 24 kept, 6 dropped_blacklist
```

- `fetched` = 源返回的原始条数
- `parsed` = parse 成功的
- `cleaned` = HTML 清洗器处理的条数 (v1.0.3-html, 36kr 等源 raw_content 里的 `<h2>/<p>/<strong>` 标签被剥成纯文本)
- `inserted` = 入库条数 (去重后)
- `dropped_blacklist` = 黑名单 filter drop 掉的
- `parse_errors` = parse 失败 (单条 error 不阻塞)

### 4.4 清洗历史 HTML 文章 (v1.0.3-html)

36kr_rss 抓的历史 30 篇 raw_content 仍是 HTML 格式, 跑一次 `clean-articles` 即可一次性清洗:

```bash
python3 -m moni clean-articles
# clean-articles: scanned=10, updated=10, unchanged=0

python3 -m moni clean-articles  # 再跑一次 = 幂等
# clean-articles: scanned=0, updated=0, unchanged=0
```

清洗后 `articles.raw_content` 字段从 `<h2>...</h2><p>...</p>` 变成纯文本段落, LLM 摘要质量显著提升.

---

## 5. 分享菜单 (v1.0.5.11 起 · v1.0.10 一键简报)

### 5.0 一键简报 (v1.0.10，推荐主路径)

适合：已经 Pin 好一批同业文章，要快速出一份可决策简报。

1. 在「文章查阅」Pin 关心的文章（列表最左列）
2. 打开「分享」
3. 顶部 **一键简报** 区：选格式（默认 Markdown）→ 点 **「用当前 Pin 生成简报 (N)」**
4. 无需再勾选文章、无需再点「立即处理队列」
5. 卡片实时显示阶段（摘要 / 事实核查 / 业务价值 / 渲染）与进度；完成后点「打开产物」

**默认会做**：AI 摘要、事实核查、业务价值评判（0–100 + 说明），按业务价值排序，并生成「优先关注」rollup。  
**默认不做**：BestBlogs 增强（省额度）。需要 BB 时用下方「自定义分享」。

**注意**：处理 N 篇冷启动可能较久（首次摘要+核查+业务价值）；已富化过的文章会复用库内字段。

---

### 5.1 自定义分享：选文章 + 选格式 (老路径)

1. 进"分享"菜单
2. 上方"选择文章"模块: 右上"来源"下拉选源, "每页"下拉选 20/50/100/200, 翻页按钮
3. 文章卡片**勾选** (checkbox, 多选) — 至少 1 篇
4. 中间"选择格式"模块: 点 10 个 chip 之一 (plain_text / markdown / html_landing / pdf_bound / ppt_slides / ascii_art / ascii_video / architecture_svg / info_graphic_png / knowledge_comic)
5. html_landing / ppt_slides 两个格式要求填 "页面/演示标题" 字段

### 5.2 加入队列前 confirm 框 (v1.0.5.16)

点"加入队列"按钮不再立刻发请求, 而是先 fetch `/api/share/estimate` 拿 LLM 预估, 弹一个确认框让你看清楚再决定:

```
┌─ 确认加入分享队列 ─────────────────┐
│  格式: html_landing  文章数: 1  总字数: 748
│  ──────────────────────────────────
│  LLM 预估
│  模型:   MiniMax M3 (minimax-cn (默认))
│  预估 input:  299 tokens
│  预估 output: 538 tokens
│  预估耗时:   51 秒 (基于历史 1 次同格式任务均值)
│  完成后请到: /artifacts/<id>-html_landing.html
│  ──────────────────────────────────
│       [取消]    [确认加入队列]       │
└────────────────────────────────────┘
```

- 取消 / × / 点背景 / Esc → 关 modal, 不入队
- 确认加入队列 → 真 POST `/api/share` 入队, modal 关, 队列状态表新增一行 pending
- 估算接口挂了 (5xx / network) → modal 显错误 + 仍允许"盲点确认" (因为入队是独立代码路径)
- token / 耗时都是**粗估**: input 按总字数 / 2.5 (CJK mixed heuristic), 时长按 share_tasks 表最近 5 个同 format done 任务均值 (没历史 fallback 到静态默认 30-120s)

### 5.3 队列状态 → 完成 artifact

- 队列表每 30s 自动 polling, 状态从 `pending` → `running` → `done` / `failed`
- done 行"输出"列有可点击链接 (v1.0.5.15+ web URL, 不撞 macOS file:// 拦), 新 tab 打开 artifact
- artifact 实际存到 `data/artifacts/<task_id>-<format>.<ext>`, web URL 是 `/artifacts/<filename>`

### 5.4 立即处理队列按钮 + 实时进度 (v1.0.5.17)

加队列后, **不用等 cron 也不用敲命令** — UI 上点按钮一键激活 hermes chat 跑这批 task:

1. 进"分享"菜单
2. 滚到下面"队列状态"模块
3. **点 "▶ 立即处理队列"** 按钮 (有 pending/running 任务时高亮, 否则 disabled)
4. 弹 modal 显:
   - 状态: 已启动 / 已在跑 / 启动失败
   - PID (worker 子进程号)
   - 日志路径
   - 实时 worker.log (每 3 秒自动刷新, 看 hermes chat 输出流)
5. 关闭 modal 不影响 worker (它继续在后台跑, 关掉浏览器也跑)
6. 30s 后队列状态表自动 polling 看进度: pending → running (进度条动) → done

**进度条**: 队列状态表 running 行多一列"进度" — 显示当前阶段 (queued / fetching / building_prompt / invoking_llm / writing_artifact) + 百分比 0-100% + 蓝色进度条.

5 阶段对应 worker 实际在做什么:
- `queued` 0% — 任务在 queue 文件, worker 还没扫到
- `fetching` 20% — worker 正在从 articles 表读 raw_content
- `building_prompt` 40% — 拼 prompt 模板
- `invoking_llm` 60% — 调 hermes chat (5min timeout, 最长阶段)
- `writing_artifact` 95-100% — 写 output 文件到 `data/artifacts/`, status=done

worker 5 阶段拆详见 STATUS.md v1.0.5.17 章节.

### 5.5 内容选项 / 高级选项 / 报告 PNG 生图方式 (v1.0.5.18)

v1.0.5.18 重构后, 分享菜单在"选择格式"下方多 3 块可选项 (所有 6 个 format 通用):

#### 5.5.1 内容选项 (6 format 通用)

```
┌─ 内容选项 ──────────────────────────────┐
│ ☑ 包含 AI 摘要                            │
│ ☐ 包含事实核查 (较慢)                    │
└─────────────────────────────────────────┘
```

- **包含 AI 摘要** (默认勾上) — worker 调 hermes 给每篇文章生成 3-5 句摘要, 没摘要时尤其有用
- **包含事实核查** (默认不勾) — worker 调 hermes 核查每篇文章的事实性声明 (数字/引言/科学论断), **比较慢**但输出更可信


#### 5.5.1b BestBlogs 增强选项 (v1.0.5.25, 仅 bestblogs 源文章可用)

```
┌─ BestBlogs 增强 ─────────────────────────────────────┐
│ 摘要来源: [moni AI 摘要 ▾]                            │
│ ☐ 质量评分 (score)  ☐ 要点列表  ☐ 金句  ☐ 专业标签 │
└─────────────────────────────────────────────────────┘
```

- **摘要来源**: moni AI / BestBlogs / 两者都展示 / 不展示
  - moni AI: 用 LLM 生成摘要 (默认, 保留原有能力)
  - BestBlogs: 用 BestBlogs API 的专业摘要 (消耗 API 额度)
  - 两者: 同时展示, 分别标注来源
  - 不展示: artifact 不渲染任何摘要 (其他 BB 字段仍展示)
- **质量评分**: BestBlogs 0-100 综合质量分
- **要点列表**: 文章核心要点
- **金句**: 文章关键引述
- **专业标签**: BestBlogs 标签 (不同于 moni tag)

**额度**: Free 用户每天 50 次 API 调用。已拉取的文章不重复调用。
**生效条件**: 勾选任一增强选项 → 分享时自动调 API → 数据入库 → 后续分享复用。

**适用源 (v1.0.9.4)**:
- `bestblogs` (OPML 聚合, 183 篇)
- `bestblog-podcast` (BestBlogs 播客, 99 篇)
- `bestblog-x` (BestBlogs X.com, 43 篇)
- `bestblog-yt` (BestBlogs YouTube, 18 篇)

**v1.0.9.4 改进**:
- 新抓取文章自动从 RSS `<guid>` 获取 BestBlogs resourceId
- 分享时直接调用 `GET /resources/{id}` 获取元数据 (不再依赖不存在的 /search 端点)
- 历史文章 (343 篇) 需补填 resourceId (待网络稳定后运行 `scripts/backfill_bb_resource_id.py`)

**其他源 (36kr_rss / mpaypass / qbitai / wechat_rss_* / huxiu) 即使勾选 BB 增强, artifact 也不会有 BB 块**。分享 modal 现在会在选中混合源时显示橙色提示 (如 "已选 2 篇非 bestblogs 源文章, BB 增强仅对 bestblogs* 源生效")。

**故障排除**:
- artifact 没出现 BB 块 + modal 没提示: 该文章已被 v1.0.5.25 时代错误标记 `bb_fetched_at` (毒化行)。运行 `python3 scripts/backfill_bb_poisoned_rows.py --dry-run` 查看, `--commit` 清理后重分享即可。
- artifact 出现 BB 块但字段为空: API 临时挂了。worker 不会标记 `bb_fetched_at`, 下次分享自动重试。
#### 5.5.2 高级选项 (provider / model 覆盖)

```
⚙ 高级选项 (provider / model 覆盖)  ← 折叠, 默认收起
   ┌─ Provider: [留空=用默认] ─┐
   ┌─ Model:    [留空=用默认] ─┐
```

- 默认 Profile 的 provider/model 额度耗尽时, 用户可手动指定覆盖
- 例: `provider=google, model=gemini-2.5-pro` 切到 Google Gemini
- 不熟可**留空用默认**

#### 5.5.3 报告 PNG 生图方式 (仅 `report_png` format 显)

```
┌─ 生图方式 ───────────────────────────────┐  ← 仅选 report_png 时显
│ ▼ 信息图风格 (baoyu-infographic, 默认)  │
│   卡片风格 (ljg-card)                    │
│   排版风格 (kami)                        │
│   直出 (no skill)                       │
└─────────────────────────────────────────┘
```

- `baoyu-infographic` (默认) — 调 `baoyu-infographic` skill, 成熟的信息图风格
- `ljg-card` — 调 `ljg-card` skill, 卡片风格
- `kami` — 调 `kami` skill, 排版风格
- `direct` — 不调 skill, agent 自己拼 `image_generate` prompt, 最灵活

worker 跑 4 种方式分别走不同 skill, 详细见 `moni/share/prompts/format-report-png.md`.

---

## 6. 菜篮子 (v1.0.6.9 新增)

### 6.1 是什么

被过滤规则（黑名单/白名单/tag 过滤）丢弃的文章自动存入「菜篮子」，不再消失。

### 6.2 怎么用

1. 点顶部「菜篮子」tab
2. 看到所有被丢弃的文章，显示：标题、来源、过滤原因、命中关键词
3. 可按来源/过滤原因/rescue 状态筛选
4. 点「查看」打开详情弹窗：
   - 看原文内容预览
   - 点「打开原文 ↗」看原始链接
   - 选 tag 后点「确认 Rescue」→ 文章回到正式文章库
   - 不想要的直接点「删除」

### 6.3 Rescue 流程

Rescue = 把菜篮子里的文章「救回」到正式文章库：
- 选择要打的 tag（可多选）
- 点「确认 Rescue」
- 文章自动插入 articles 表 + 关联 article_tags
- 已 rescue 的文章会标记「已 rescue」，不能重复 rescue

---

## 5. LLM 摘要 / 事实核查

### 5.1 当前状态 (v1.0.1 修完)

- 端点: `POST /api/articles/{id}/summary` 和 `/api/articles/{id}/fact-check`
- 调 `moni.llm.call_llm` (subprocess 调 `hermes chat`)
- **默认 model**: minimax-cn / MiniMax-M3 (coder profile 自带)
- **写回**: `articles.summary` + `summary_at` + `fact_check_*` 字段

### 5.2 怎么用

1. 进"文章查阅"菜单
2. 找篇文章，点击打开详情面板
3. 点「生成摘要」或「事实核查」按钮
4. 如果文章只有标题/片段，可先用「📥 导入全文」导入 .md 文件补全文再生成

### 5.3 导入全文 (v1.0.5.24+)

所有信源的文章详情面板都有「📥 导入全文」折叠区:

1. 用 Obsidian Web Clipper 或浏览器复制原文
2. 保存为本地 .md 文件
3. 在详情面板展开「导入全文」，粘贴文件路径，点「导入」
4. 导入后旧摘要自动清除，需重新点「生成摘要」

**注意**: 导入会整体替换原有正文，不合并。

### 5.3 LLM 配置

LLM 用 3 张 DB 表管理 (v1.0.2-A):
- `llm_providers` (3 个 seed: minimax-cn / google / openrouter)
- `llm_models` (4 个 seed)
- `llm_settings` (7 个 active 配置)

**v1.0.2-F 还没建 web UI** (需 v1.1 加), 暂用 `sqlite3` 改:
```bash
sqlite3 data/news.db "UPDATE llm_settings SET value = '<model_id>' WHERE key = 'active_summary_model_id'"
```

---

## 6. .env 配置

`.env` 文件 (chmod 600, **不入库**):

```bash
# WeChatRSS 服务访问 token
WECHAT_RSS_TOKEN=<完整 JWT, ~150 字符>
```

申请: https://wechatrss.waytomaster.com/

`scripts/web.sh start` 会自动 source .env 把 token 传给 uvicorn。

---

## 7. 数据看

```bash
# 文章按源分布
sqlite3 data/news.db "SELECT source, COUNT(*) FROM articles GROUP BY source"

# 5 个源
sqlite3 data/news.db "SELECT name, source_type, url, fetch_interval_min FROM sources"

# 5 个 tag
sqlite3 data/news.db "SELECT name, color, description FROM tag_definitions"

# 3 个 filter
sqlite3 data/news.db "SELECT source_id, mode, match_field, keywords FROM source_keyword_filters"

# 10 张表行数
python3 -m moni db stats

# 导出全部
python3 -m moni db export json > backup-$(date +%F).json
```

---

## 8. 已知小问题 (不影响主流程)

> v1.0.5.13 状态: 之前列的 4 个老问题全部修复。

- ~~36kr 30 篇老数据 内容是 HTML 未清洗~~ — v1.0.3-html `moni clean-articles` 已修
- ~~mpaypass 10/66 是 `/download/` 误抓~~ — v1.0.5 regex 只匹 /news/ 已修
- ~~`/api/llm/token-log` 路径 之前 404~~ — v1.0.1 已修
- ~~share 按钮 manifest queue 写盘~~ — T7 (0a71c88) default profile worker + 5min crontab 已实装, 详见 `docs/CRON.md`
- ~~datetime.utcnow() deprecation warning (55/run)~~ — v1.0.5.13 改 `datetime.now(timezone.utc)` 已修, 0 warnings

**新发现 (不影响主流程, 待办)**: 2 个 pytest skip (`test_fetch_integration.py` 36kr URL P0-C 已知, +1 个待识别) — 改 mock 形式让 pytest 全绿 0 skip 是 P1 候选

---

## 9. 升级 / 迁移

每次 schema 改, 跑:
```bash
python3 -m moni db init
# 自动跑 pending migrations + 重新 seed
```

迁移文件: `moni/db/migrations/*.sql` (按编号顺序)

---

## 10. 出问题?

1. **web 启动报 "no module"** → `pip install -e .` 在**主 checkout** 跑 (不要在 wt/)
2. **端点 500** → `tail -f /tmp/moni-web.log` 看 stack
3. **filter 不生效** → `sqlite3 data/news.db "SELECT * FROM source_keyword_filters WHERE is_active=1"` 看 seed 对不对
4. **抓 0 条** → 看 fetch_interval_min 没到, 删 `data/.last_run.json` 强制重抓
5. **想重置 articles** → `sqlite3 data/news.db "DELETE FROM articles"` (destructive, 慎)

---

## 11. 怎么加新源 (DIY 流程)

1. **加 fetcher 代码**: `moni/fetch/sources/<name>.py`
   - 继承 `BaseFetcher`
   - `source_name = "<name>"`
   - `fetch()` → list of dict
   - `parse(raw)` → normalized dict
   - `@register_fetcher` 装饰
2. **加 config**: web UI "配置管理 > 抓取源" → "+ 新建 source"
3. **加 tag 关联**: web UI "配置管理 > 抓取源" 行内 "改 tags"
4. **加 filter** (可选): web UI "配置管理 > 关键词过滤"
5. **测抓取**: `python3 -m moni fetch --source <name>`

---

## 12. 贡献

- Issue / PR → github.com/mariusiaowego-commits/moni (私)
- 接手 agent 看 `HANDOFF.md`
- 本项目惯例 + 收尾 workflow → `AGENTS.md`
