# moni 使用指南

> 同业信源自动抓取与本地新闻库。v1.0.2 完成。
> 这份文档给**用户**（你自己）看 —— 怎么用 moni，不讲代码细节。

---

## 1. 启动

```bash
cd /Users/mt16/dev/source-monitor

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

## 2. 6 个菜单

| 菜单 | 作用 |
|---|---|
| **文章查阅** | 浏览所有抓回文章 (含 LLM 摘要/事实核查) |
| **CRUD** | 编辑/删除单篇文章 (两步删除 confirm) |
| **跑批日志** | cron 任务执行历史 |
| **抓取管理** | 5 源配置 + 手动触发抓取 + 健康检查 |
| **分享** | 11 种格式导出 (markdown / pdf_bound / ppt_slides 等) |
| **配置管理** | 业务源 / 业务 tag / 关键词过滤 CRUD (v1.0.2 新增) |

---

## 3. 配置管理 (核心)

### 3.1 抓取源 tab

5 个默认源 (DB 主用, YAML 备用):

| name | URL | 抓什么 | 抓取间隔 |
|---|---|---|---|
| 36kr_rss | https://36kr.com/feed | 36氪 科技/创投 | 15 min |
| mpaypass | https://www.mpaypass.com.cn/ | 移动支付网 银行/支付 | 30 min |
| qbitai | https://www.qbitai.com/ | 量子位 AI (尚未抓通) | 30 min |
| wechat_rss_a | https://wechatrss.waytomaster.com/api/rss/all?token=...账号A | 微信公众号聚合 A | 60 min |
| wechat_rss_b | https://wechatrss.waytomaster.com/api/rss/all?token=...账号B | 微信公众号聚合 B | 60 min |

**新建源**: 点"+ 新建 source" → 填 id / name / url / type / interval → 保存
**改 tags**: 行内 "改 tags" 按钮 → 多选弹窗
**停用源**: 不删, is_active 设为 false 即可 (行内"编辑")

### 3.2 业务 tag tab

5 个默认 tag (业务范围: 银行 × 支付 × 科技):

| name | 颜色 | 业务范围 |
|---|---|---|
| banking | 蓝 #2563EB | 银行 (央行罚款/上市/财报/并购) |
| payment | 绿 #16A34A | 支付 (PayPal/银联/二维码/跨境) |
| ai-fintech | 红 #DC2626 | AI × 金融 (智能投顾/风控/大模型落地) |
| regulation | 橙 #EA580C | 监管 (政策/法规/合规) |
| general-tech | 灰 #6B7280 | 通用科技 (其他) |

**新建 tag**: 点"+ 新建 tag" → 填 name (英数下划线) / description / color (hex) → 保存
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

## 4. 抓取

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
cd /Users/mt16/dev/source-monitor
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
| wechat_rss (legacy) | env var | - | 保留向后兼容 |
| wechat_rss_a | 嵌在 source.url | 2 | ✅ 启用 |
| wechat_rss_b | 嵌在 source.url | 2 | ✅ 启用 |
| wechat_rss_c / d / e | 嵌在 source.url | 0 | 占位, 待加 token |

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

### 4.3 抓取日志含义

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

## 5. LLM 摘要 / 事实核查

### 5.1 当前状态 (v1.0.1 修完)

- 端点: `POST /api/articles/{id}/summary` 和 `/api/articles/{id}/fact-check`
- 调 `moni.llm.call_llm` (subprocess 调 `hermes chat`)
- **默认 model**: minimax-cn / MiniMax-M3 (coder profile 自带)
- **写回**: `articles.summary` + `summary_at` + `fact_check_*` 字段

### 5.2 怎么用

1. 进"文章查阅"菜单
2. 找篇文章
3. **注**: v1.0.2-F 还没加 "摘要/核查" 按钮 UI (PRD §4.7 第 6 菜单计划)
4. **临时**: 用 curl 触发
   ```bash
   # 摘要
   curl -X POST http://127.0.0.1:8900/api/articles/<article_id>/summary \
     -H "Content-Type: application/json" \
     -d '{"article_id":"<article_id>"}'
   
   # 事实核查
   curl -X POST http://127.0.0.1:8900/api/articles/<article_id>/fact-check \
     -H "Content-Type: application/json" \
     -d '{"article_id":"<article_id>"}'
   ```

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

- **36kr_newsflash / qbitai** 没抓通 (首页问题, 待 v1.0.2-G 修)
- **wechat_rss** 没抓通 (`.env` token 是 13 字符占位符, 待你给完整 token)
- **36kr 30 篇老数据** 内容是 HTML 未清洗 (v1.0.2-E 待做)
- **mpaypass 10/66** 是 `/download/` 误抓 (v1.0.2-D 待过滤)
- **`/api/llm/token-log` 路径** 之前 404 (v1.0.1 已修)
- **share 按钮** 部分 (manifest queue 写盘) — T7 (0a71c88) 已实装 default profile worker + 5min crontab; 详见 `docs/CRON.md`

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
