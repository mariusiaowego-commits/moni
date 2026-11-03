# moni — 同业信源自动抓取与本地新闻库

> 同业信源自动抓取 + 全文导入 + 多视角摘要 + 分享生成。v1.0.9.10 (2026-06-30) — 425 passed, 2 skipped。

## 项目路径

`/Users/mt16/dev/moni` (git remote → github.com/mariusiaowego-commits/moni)

## 首次启动

```bash
# 1. 装包 (editable install, 必须用 3.12 pip)
pip install -e .

# 2. 初始化数据库 (首次, 或 schema 升级后)
/usr/local/bin/python3 -m moni db init
# ⚠️ 必须用绝对路径 python3.12, 系统默认 python3 (3.14) 缺 yaml 依赖

# 3. 启动 web 服务 (prod: 8900)
/usr/local/bin/python3 -c "import uvicorn; from moni.web.server import create_app; uvicorn.run(create_app(), host='127.0.0.1', port=8900, log_level='error')"

# 4. 浏览器打开
open http://127.0.0.1:8900/
```

**dev 端口**: 8901 (主仓 checkout 改完用 `scripts/web-8901.sh restart` 验证)

## 抓取源 (7 active)

| 源 | 简介 | tag |
|------|------|------|
| `36kr_rss` | 36氪 AI/创投资讯 | ai-fintech |
| `mpaypass` | 移动支付网 (银行/支付/监管) | banking / payment / regulation |
| `qbitai` | 量子位 AI 资讯 | ai-fintech |
| `wechat_rss_a` / `wechat_rss_b` | 公众号聚合 RSS (waytomaster 需 token) | general-tech |
| `bestblogs` | OPML 聚合 (375 公众号) | banking / wechat |
| `bestblog-podcast` | 小宇宙播客 (60 feeds) | wechat |
| `bestblog-x` | x.com / Twitter (160 feeds) | wechat |
| `bestblog-yt` | YouTube 频道 (20 feeds) | wechat |
| `xbot_feed` | X-bot 精选 (私有仓库 `xbot-feed` 的 `feed/*.json`) | wechat / miniprogram / general-tech / ai-fintech |

## 关键命令

```bash
# 抓取
/usr/local/bin/python3 -m moni fetch                # 全源抓
/usr/local/bin/python3 -m moni fetch --source 36kr  # 单源抓

# DB
/usr/local/bin/python3 -m moni db init              # 初始化 + migrate
/usr/local/bin/python3 -m moni db stats             # 表格行数
/usr/local/bin/python3 -m moni db seed-config       # 重置 source 配置 (需先 source .env)
sqlite3 data/news.db "SELECT source, COUNT(*) FROM articles GROUP BY source"

# 测试
set -a && source .env && set +a                    # ⚠️ 必须先加载 env
/usr/local/bin/python3 -m pytest tests/ -q          # 425 passed, 2 skipped
```

## ⚠️ Env 加载 SOP (v1.0.7.6.2)

任何 moni 命令前**必须**先 `set -a && source .env && set +a`, 不只是 web service, CLI `seed-config` / `fetch` 都需要。

漏了会导致 wechat_rss token 加载失败 → `_env_token()` 返 `__WECHAT_RSS_A_TOKEN__` 占位符 URL 入库 → waytomaster 401 → 0 篇。

**诊断**: `sqlite3 data/news.db "SELECT name, substr(url, 1, 80) FROM sources WHERE name LIKE 'wechat%';"` — url 含 `__WECHAT_RSS_*_TOKEN__` 就是这个 bug。

## 部署信息

- **prod 端口**: 8900 (`127.0.0.1:8900`)
- **dev 端口**: 8901 (`scripts/web-8901.sh restart`)
- **服务进程**: uvicorn + Python 3.12 (绝对路径)
- **数据库**: 单文件 `data/news.db` (schema_version=19, 11 tables)
- **测试**: 425 passed, 2 skipped

## 目录结构

```
moni/
  db/         schema.sql + migrations/ + connection + seed_llm + seed_config
  fetch/      base + filters + scheduler + sources/ (7 fetchers) + chrome_daemon + browser_harness
  web/        FastAPI app + routes/ (articles/crud/cron/fetch/llm/share/config_*) + static/ (Alpine SPA)
  share/      manifest 队列 + 7 格式 (plain_text/markdown/html_landing/rich_text/info_graphic_png/report_png/pdf)
  cron/       APScheduler + jobs + run_history
  cli/main.py — moni db init / fetch / web 入口
config/       sources.yaml + tags.yaml + 36kr_cookies.json (gitignore)
data/         news.db (SQLite, gitignore)
docs/         plans/ + BROWSER_FETCHER_PATTERN.md + CRON.md + archive/
scripts/      web.sh + web-8901.sh + dev_8900.sh + backfill_bb_resource_id.sh
```

## 分享格式 (7 种)

| 格式 | 说明 |
|------|------|
| plain_text | 纯文本 |
| markdown | Markdown |
| html_landing | HTML 网页 (需 hermes agent) |
| rich_text | 富媒体 |
| info_graphic_png | 信息图 PNG |
| report_png | 报告 PNG |
| pdf | PDF 日报 |

## 文档

- `STATUS.md` — 当前状态 + 历史时间线
- `USER_GUIDE.md` — 用户使用指南
- `AGENTS.md` — 项目惯例 + 收尾 workflow
- `HANDOFF.md` — session 交接 (本地)
- `vibe-coding-log.md` — session 日志 (本地)
- `docs/plans/` — 历史 plan 文档 (双写到 Obsidian)
- `docs/CRON.md` — cron + worker 详细 manual testing
- `docs/BROWSER_FETCHER_PATTERN.md` — 36kr 反爬 + Chrome daemon 设计

## License

Internal project. Repo owner: mariusiaowego-commits
