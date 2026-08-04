# STATUS — moni

> 项目状态与功能清单。每个 Phase 完成时更新日期 + 阶段描述。

---

## 当前状态: **v1.0.12-dev — xwatch 三通道** (2026-08-04)

**分支**: `feature/v1.0.12-xwatch-health`（未合并）
**基线 tag**: v1.0.11 @ `e1478d2`
**数据库**: schema_version=22

### v1.0.12 — xwatch Following CDP + accounts + DDG

**目标**: DDG 不稳时仍能抓 X；Following 条数可配（含 300）。

**已交付:**
1. **Channel C Following** (`xwatch_following.py`)：纯 CDP 滚关注流（Profile 6/9222）；`following_max_posts`/`following_max_scrolls`
2. **Channel A accounts** + **Channel B DDG**（熔断+轮转）
3. CLI：`moni xwatch following --max-posts N [--ingest]`、`moni xwatch health`
4. health/UI ping；seed 含 following_*；依赖 `websocket-client`
5. Grok skill `x-watch-grok`

**验证**: 实机 CDP 56 帖；单元测试 xwatch 套件通过

**配置**: 默认 max_posts=100；可设 300+scrolls 80。seed-config 会重置 fetch_config。

---

### v1.0.11 (2026-08-02) — X 关键词监控

**目标**: 按关键词从 X (Twitter) 发现 + 收录推文，免 API key，可手动收录单条。

**已交付:**
1. **新源 `xwatch`** (sources 表 10→11)：DDG Lite 发现推文 URL → twitter syndication 抓全文 → 走既有 keyword filter + dedupe_insert 链路
2. **CLI `moni xwatch ingest <url|id>`**：手动收录单条推文，按 wechat/miniprogram tag 关键词门控；支持 `--dry-run`；重跑自动 dedupe
3. **`tag_definitions` 新增 `miniprogram` row**（TECH-SPEC §0.2 假设已存在，实际空缺；closes FK gap 让 xwatch source_tags 不报错）
4. **`moni/fetch/__init__.py` 加 `run_fetcher = run` 别名**：submodule shadow 修复——`moni.fetch.run` 是 CLI 模块，会覆盖同名 function 的 attribute 绑定，导致任何先于 routes 加载 cli_main 的场景下 routes 拿到 module 而非 function（潜在 latent bug，由本次 CLI 测试首次暴露）
5. **`moni/fetch/health.py` empty-feed_url 静默跳过**：之前对 `wechat_rss_a/b`/`xwatch`（feed_url 为空）返 "no URL configured" stub；现在直接跳过，更干净
6. **seed-config 适配**：INSERT INTO sources 新增 `fetch_config` 列写入；幂等
7. **DDG 端点 html/ → lite/**（v1.0.11.A）：html/ 在本 IP 几乎必返 anomaly 页；切 lite 体积更小、风控更宽松。lite shape `<a class="result-link" href="//duckduckgo.com/l/?uddg=...">` 解析：提 `uddg` query 参数 → URL-decode → 走原 TWEET_URL_RE。正则 fallback 兜底保留
8. **`request_delay` 默认 2.5s → 5s**：降低 DDG 风控触发概率
9. **4 fixtures + 4 测试文件 +43 单测**：discovery 10 + syndication 15 + fetch 13 + cli 6（含 1 new lite-shape uddg unwrap 测试）

**验证**:
- pytest 全量 **483 passed, 2 skipped, 0 failed**（baseline 439 → +44）
- `moni xwatch ingest https://x.com/xiaoyangX026/status/2083475663496044761` → inserted=1，raw_content 前 60 字 = "UX很流畅的感觉。但是我觉得最核心需求是要高触达，及时提醒还方便"（与 TEST-PLAN §5 #3 期望字符级一致）；再跑 inserted=0 (dedupe OK)
- tag 自动命中：`miniprogram,wechat`（keyword_hits 机制）
- DDG lite 端点从本 IP 偶尔返回 anomaly 页 → 走降级路径 (1 retry + 5s sleep → [] + log warn)，不抛异常、不换付费方案、不停止 feature（per spec §15 + 拍板 A）
- 8901 dev port `/api/articles?source=xwatch` 等 6 端点 HTTP 200

**已知 / 后续**:
- DDG 公开接口是 IP 敏感的免费服务；同一 IP 多次查询会触发 anomaly challenge。本 sprint 一次性 dry-run 抓 12 条，但稍后批量 fetch 触发 anomaly。当前降级路径保证 system 稳定 + 不抛异常，但**bulk fetch 入库波动较大**。manual `moni xwatch ingest` 始终可用（绕过 DDG，直接走 syndication）
- PR #60 已 squash merge + force push tag v1.0.11。8900 已用绝对路径命令重启，PID 53118

---

## 历史状态: v1.0.10.1 — waza-ui 布局精修 (2026-07-28)

**主分支 tag**: v1.0.10.1
**Web 服务**: `127.0.0.1:8900` (Python 3.12)
**数据库**: `data/news.db` — schema_version=22, 11 tables
**测试**: 见 PR / 全量 pytest

### v1.0.10.1 (2026-07-28) — waza-ui 布局精修

**目标**: 遵循 waza-ui skill "Group with Space, Not Lines" 原则，系统性优化全站布局呼吸感。

**已交付:**
1. **页面间距统一**：content padding 24/32/48 → 32/40/64px
2. **Panel Header**：margin-bottom 16 → 24px，action gap 8 → 12px
3. **Stats Row**：gap 12 → 16px，margin-bottom 24 → 32px，card padding 16/18 → 20/22px
4. **表格间距**：thead/tbody padding 10/14 → 14/18px，empty-row padding 24 → 32px
5. **折叠面板**：外层 share-collapsible content 20/20/4 → 24/24/12px，内层高级选项 16 → 20px
6. **Form Row**：margin-bottom 12 → 16px，label margin 4 → 6px
7. **分页栏**：margin-top 12 → 20px，padding 8 → 12px，gap 统一加宽
8. **Share Section**：margin-bottom 20 → 28px，h4 margin-bottom 8 → 12px
9. 优化按钮点击缩放：cubic-bezier(0.22, 1, 0.36, 1) 弹性曲线

**设计原则**: waza-ui 分层阴影 + 同心圆角化 + 中断式动效 = 视觉层级清晰，阅读呼吸感更强，符合 moni Sophon.at utilitarian aesthetic。

---

## 当前状态: **v1.0.10 — 一键简报** (2026-07-28)

**主分支 tag**: v1.0.10
**Web 服务**: `127.0.0.1:8900` (Python 3.12)
**数据库**: `data/news.db` — schema_version=22, 11 tables
**测试**: 见 PR / 全量 pytest

### v1.0.10 (2026-07-28) — 一键简报

**目标**: Pin N 篇 → 选格式 → 一点生成含摘要/事实核查/业务价值的简报，进度可见。

**已交付:**
1. `POST /api/share/brief` — 全部 pin + opinionated payload + auto `launch_worker`
2. migration 020–022 — `business_value_*` / `quality_score_*` / `brief_summary` / `brief_body_md`
3. `moni/share/enrichment.py` — 摘要/事实核查/业务价值落库；长摘要 AI 压缩
4. worker — hermes profile 默认 `default` (MiniMax)；brief 走 brief prompt + 富化后重建 content
5. **offline HTML** (`brief_html.py`) — 全文嵌入、中文事实标签+icon、业务价值排序、可折叠原文（浅灰 pill、**仅 click** 滑动）、GSAP 动效
6. 入库时本地 layout 写 `brief_body_md`（`layout-articles` CLI 可补历史）
7. 分享页主 CTA + sticky 任务进度；旧路径收入「自定义分享」

**默认格式**: HTML 网页（`html_landing`）offline 简报；默认不做 BestBlogs 增强。

**文档**: `docs/plans|prd|specs/2026-07-28_v1.0.10-one-click-brief*`

---

### v1.0.9.12 进展 (2026-07-03) — P0 紧急修复

**主分支 tag**: v1.0.9.12 (PR #56)

### v1.0.9.10 进展 (2026-06-30)

**信源 icon 精细化:**
1. **bestblog 子源 icon 拆分** — `bestblogs` / `bestblog-podcast` / `bestblog-x` / `bestblog-yt` 之前共用一张 bestblogs.png, 全部子源在 modal/表格里看起来一样。拆成 4 个独立 icon:
   - `bestblog-gzh.png` (公众号, bestblogs 主源)
   - `bestblog-x.png` (bestblog-x 子源)
   - `bestblog-yt.png` (bestblog-yt 子源)
   - `bestblogs.png` 保留给 bestblog-podcast
2. **mpaypass icon 升级** — 删除 `mpaypass_v2.png`, 新 `mpaypass.png` 黑底白图 (PRD 附录 B 风格, 与其他信源一致)
3. **wechat_rss_b 独立 icon** — `wechat_rss_b` 之前共用 `wechat_rss.png`, 现在有独立 `wechat_rss_b.png`
4. **icon 防白边** — 7 个信源 + moni 的 icon 容器背景从 `var(--card)` 改为 `#1a1a1a`, PNG 透明像素不再透出卡片底色
5. **feedInfoBar 居中修正** — `left:0; right:0` 改为 `left:50%; transform:translateX(-50%)`, 数字弹跳时不再被左偏
6. **moni 坐标微调** — `_moniLeft` 加 2px 右移, 与左侧信源链条视觉对称
7. **36kr 旧版吉祥物下线** — 删除 256x256 翠绿色卡通版, 统一用新版黑底白图

**新增资源**:
- `pin_a.png` / `pin_b.png` / `pin_c.png` / `pin_d.png` (4 个信源徽章/书签位图, 备用)
- `scripts/backfill_bb_resource_id.sh` (历史 343 篇 bestblogs 文章的 bb_resource_id 回填脚本, 跑完即清空遗留项)

**杂项清理**:
- `.gitignore` 加 21 条规则屏蔽 6/12-6/29 期间散落的临时废料 (demo html / report png / foshan-landing / 中文文件名 / 审计 md)
- 36kr_backup.png 删除 (新版替换后无需保留)

**测试**: 425 passed, 2 skipped | **8900**: 已 restart @ pid 32157, HTTP 200, 119KB 首页

**PR**: #52 squash merged → main @ 130de78, tag v1.0.9.10

---

### v1.0.9.12 进展 (2026-07-03)

**P0 紧急修复 (6 项):**
1. **Alpine console error** — 10 处 `fetchAllResult` 无保护访问 (`?.` 可选链), 生产页面零 error
2. **Share task 连败** — `moni` profile (xiaomi) API key 过期 → 默认切 `default` (MiniMax-M3), 重置 3 个 failed task 重试
3. **bb_resource_id 毒化修复** — fetcher 不再挑 `RAW_` 前缀 (存原始 GUID), `fetch_and_store()` 不再标记 fetched_at 留 NULL 等 backfill
4. **失败告警** — 新建 `moni/utils/notify.py` (Bark/Telegram/log), worker 失败路径自动通知
5. **launchd 托管** — 新建 `scripts/com.moni.cron.plist` + `install-launchd.sh` (当前未启用, 用户手动触发)
6. **依赖补全** — pyproject.toml 2→8 依赖 (补 fastapi/uvicorn/pydantic/httpx/bs4/psutil)

**代码变更**: 13 files, +302/−73 lines
**测试**: 428 passed, 2 skipped | **8900**: restarted @ v1.0.9.12
**PR**: #56 squash merged → main, tag v1.0.9.12

---

## 历史版本: v1.0.5 - v1.0.9.9 (438 行, 折叠看)

> 这一节是 v1.0.5 → v1.0.9.9 期间的所有版本阶段记录 (含 bestblog 系列、抓取管理 modal 动效、worker 优化、BB 增强一致性等)。
> v1.0.9.10 之后的 current 段见顶部。Git 完整 history 也有, 这里只留一份方便快速回看。

<details>
<summary>点击展开历史时间线 (v1.0.9.0 - v1.0.9.9)</summary>

## v1.0.9.9 — 粒子动效精调 (2026-06-29)

**粒子动效精调:**
- 速度 2x（duration 70-110 帧）
- 粒子密度 2.6x（整体翻倍）
- 取消透明度（全不透明）
- top3 大粒子 1.5x（而非 2x）
- 平滑贝塞尔曲线 + 5 色调色板（锌灰/雁灰/山黎豆红/隐红灰/芦穗灰）

**PR**: #51 squash merged, tag v1.0.9.9

---

**抓取管理 Modal 动效优化:**

1. **新 fetch progress modal**：替换 inline-style，使用 GSAP + Canvas 粒子系统
   - 左侧信源链条（flex-column，label 在 icon 上方）
   - 右侧 moni icon（96×96，带 label）
   - 粒子从当前 focus 源流向 moni
   - 单源完成时"吸入"动画（加速缩小 + 位移）
   - 批量模式 10 源链条依次切换（GSAP 过渡）

2. **Logo**：7 个信源 + moni 的黑底白图圆角矩形 app icon

3. **实时文章计数**：GSAP 数字弹跳动效，同时更新 top feedInfoBar + 底部 counter

4. **链条排序**：自定义顺序（wechatrss-A/B → bb公众号 → 量子位 → 移动支付网 → ...）

5. **测试**: 425 passed, 2 skipped

**PR**: #50 squash merged, tag v1.0.9.8

---

**BestBlogs 独立字段去重 (P2 任务)**:

1. **问题**: BB 字段共用一个 bb_fetched_at，获取任意字段会拉取全部 6 个字段。

2. **修复**: 
   - 新增 4 个独立 fetched_at 列
   - Migration 019 回填现有数据
   - fetch_and_store() 支持 fields 参数
   - 5 个新测试覆盖

3. **测试**: 425 passed, 2 skipped (15 BestBlogs API tests)

**PR**: #48 squash merged, tag v1.0.9.7

### v1.0.9.6 进展 (2026-06-26)

**Worker traceback 优化 (调试改进)**:

1. **问题**: Worker 错误只显示简单错误信息，没有 traceback，调试困难。

2. **修复**: 
   - BB_FETCH 错误现在显示完整 traceback
   - 文章内容处理错误现在显示完整 traceback
   - 更容易调试 share task 失败问题

3. **测试**: 420 passed, 2 skipped

**PR**: #47 squash merged, tag v1.0.9.6

### v1.0.9.5 进展 (2026-06-26)

**Worker 超时时间增加 (html_landing 格式超时修复)**:

1. **问题**: html_landing 格式文章多时超时（300s），SUMMARY_GEN 步骤需要 4+ 分钟生成 AI 摘要。

2. **修复**: 
   - 默认超时从 300s 增加到 600s（10 分钟）
   - 支持通过 `MONI_WORKER_TIMEOUT` 环境变量配置

3. **测试**: 420 passed, 2 skipped

**PR**: #46 squash merged, tag v1.0.9.5

### v1.0.9.4 进展 (2026-06-26)

**BestBlogs API 端点修复 (根因: 使用不存在的 /search 端点)**:

1. **根因**: `bestblogs_api.py` 调用 `/search?q=URL` 端点，但 BestBlogs API 文档中没有此端点。文章 URL 是 `mp.weixin.qq.com` 链接，无法匹配。343 篇 bestblogs 文章全部 BB 元数据为空。

2. **修复**: 
   - `bestblogs.py` — RSS 解析时提取 `<guid>RAW_xxxxx</guid>` 存入 `bb_resource_id`
   - `repository.py` — INSERT 时写入 `bb_resource_id`
   - `bestblogs_api.py` — 改用文档化端点 `GET /resources/{id}`，直接从 DB 读取 `bb_resource_id`
   - 删除 `search_by_url` 函数

3. **测试**: 10 个 BestBlogs API 测试全部通过

4. **遗留**: 343 篇历史文章 `bb_resource_id` 为 NULL（需网络稳定后运行 `scripts/backfill_bb_resource_id.py`）。新抓取文章自动从 RSS `<guid>` 获取。

**PR**: #45 squash merged, tag v1.0.9.4

### v1.0.9.3 进展 (2026-06-25)

**BestBlogs 增强一致性修复 (3 个 bug + 1 个 UI 缺陷)**:

1. **Bug #1 (P1, 修复)**: `source != 'bestblogs'` 字符串硬匹配 → 3 个 bestblog-* 子源 (bestblog-podcast / bestblog-x / bestblog-yt, 共 156 篇) 全部被静默排除, BB 增强从不生效。改为前缀匹配 (`=='bestblogs' or .startswith('bestblog-')`) 后这 156 篇可正常获取 BB 元数据。

2. **Bug #2 (P1, 修复)**: 3 个 bestblogs 文章在 v1.0.5.25 时代 (v1.0.9.2 修复之前) 因瞬时 API 失败被错误标记 `bb_fetched_at` 但 BB 字段全 NULL。**v1.0.9.2 fix 不会回溯老数据**。新增 `scripts/backfill_bb_poisoned_rows.py` 一键清掉毒化行 (默认 `--dry-run`, 需 `--commit` 确认)。SQL 仅匹配 `bb_fetched_at` 非空 + 5 个 bb_* 字段全 NULL, 极其特异, 不会误伤有效行。

3. **Bug #3 (P2, 修复)**: `bb_summary_source` 之前是 prompt 软约束, worker 模板总是同时渲染 AI + BB 摘要 (若两者都非 NULL)。worker 现在直接按 `moni_ai | bestblogs | both | none` 分支, 6 个新单测覆盖 (含 legacy manifest 兼容)。`scripts/default_worker.sh` 两个 inline Python block 都已改造, 都通过 `manifest_path` sys.argv[3] 读 manifest 里的 `bb_summary_source`。

4. **Bug #4 (P1, UI 修复)**: 分享 modal 加动态 hint: 选中的非 bestblogs* 源文章会被静默跳过, 不消耗 API 额度。颜色: 橙色 #fff7e6 背景 + 琥珀色 #f59e0b 左边框 (与主页面整体冷色风格区分, alert 性质)。

5. **防御加固**: `search_by_url` 加显式 `json.JSONDecodeError` catch (空 body / 非 JSON 200), 比 v1.0.9.2 的 `except Exception` 更精准, 日志更清楚。

**没修的 (v1.0.10+ 范畴)**:
- 4 字段独立去重 (P2): 4 个 BB 复选框仍共用一个 `bb_fetched_at`, 首次勾任一字段 = 拉全部 6 字段, **也写全部 6 字段**。增量勾选不重拉。需拆 4 列 + backfill。
- bestblogs 源识别: 仍是字符串匹配 (现在是前缀)。如果用户在 /config 重命名 "bestblogs" 源, BB 增强全失效。v1.0.10+ 加 `sources.bb_enabled BOOLEAN` 列。
- 4 字段在 modal 上也可独立显示/隐藏, 当前是统一开关。

**测试**: 300+ → 421 passed (+21 测试: 2 个 bestblog sub-source + 1 个 empty body + 6 个 worker 分支 + 1 个 backfill dry-run, 实际新增 7 个 + 之前 baseline 调整)。

**PR 准备**: feature branch 上 6 commits (Task 1/2/3/4/5/6), 待用户 review → squash merge → tag v1.0.9.3 → 手动启 8900 (frontend hint 改了需刷新)。

### v1.0.9.2 进展 (2026-06-24)

**BestBlogs transient vs permanent miss 修复**:
- 之前 `search_by_url` 返 `None` 涵盖两种情况: URL 真不在 bestblogs 索引 (永久) + SSL EOF/timeout/5xx/429 (瞬时)。两种都 `mark bb_fetched_at` → 瞬时错误被永久污染
- 三态返回: `str` (找到) / `""` (永久 miss, mark) / `None` (瞬时, **不** mark, 下次重试)
- 显式 catch `httpx.ConnectError` / `RemoteProtocolError` (SSL EOF 标准错误)
- 数据清理: SQL 把 3 篇被 SSL EOF 误标的 bestblogs 文章 `bb_fetched_at` 清 NULL, 让它们能重试
- 测试: `test_search_miss_marks_fetched` 拆成 permanent-miss + transient-error 两个 case

### v1.0.9.1 进展 (2026-06-24)

**Share worker PATH prepend (cron FileNotFoundError 修复)**:
- `scripts/default_worker.sh` + `moni/share/worker.py` 启动子进程前 `export PATH="$HOME/.local/bin:$PATH"`
- 根因: cron / launchd 子进程 PATH 精简 (/usr/bin:/bin),hermes CLI 装在 ~/.local/bin 找不到 → share task 1 秒死 + error_msg 永远空
- Defense-in-depth: 任何调用方式 (cron / launchd / web UI / 手动 shell) 都能找到 hermes
- 验证: `env -i PATH=/usr/bin:/bin` 模拟 cron 不再爆 FileNotFoundError;失败任务 `t5TRTdiMfE52cEOUtik-fA` 重跑成功,产物 PDF 15KB 1 页
- 详见 `concepts/moni-ai-architecture.md` (v1.0.5.26 那条踩坑)

**Pin icon 改版**:
- Pinned: SOLID silhouette (梯形头 + 粗针 + 底点), 17px 紫藤萝 — 来自 image gen (Fal gpt-image-2), 用户选候选 B
- Unpinned: outline (1.6px stroke, 17px) — 跟 v1.0.8 不变
- 撤掉 v1.0.8.1 的 5 层 3D enamel (用户 push "太丑了 不要背景")
- 撤掉 v1.0.8.2 的 40px 圆 + 26px svg (用户 push "缩小 1/3")
- 现状: 17px icon, 32px 圆 cell, 44px 列宽, 紫藤萝只 color swap (无背景/无阴影)

### v1.0.8.1 进展 (2026-06-23) — Pin UX 改版

**3 个修复**:
1. Pin 按钮位置 + 大小: 移到最左新增列, 32px → 40px → 17px (演化), GSAP 弹一下 (scale 0.85→1)
2. 单篇 togglePin 也走 5s 反悔: `_pendingPinToggles[articleId]` map, 双击撤销
3. bulk unpin 改即时 POST + 撤销=重新 pin: `_commitBulkUnpin` 立即发请求, `_repinBatch` 走 togglePin pipeline 撤销

**踩坑**:
- 用户先后两次推翻了 v1.0.8.1 的 3D enamel (太丑) + v1.0.8.2 的放大版 (太大), 最终 v1.0.8.3 用 image gen solid silhouette 17px
- v1.0.8.1 togglePin staging 模式 + bulk unpin 即时 POST = 行为统一, 撤销都走 _repinBatch/togglePin
- 沙箱拦 image gen (FAL_KEY 没配), 用户配 Nous Portal 后可用
- image gen 实际"圆头" prompt 跑了简化梯形, vision 描述为准

### v1.0.8 进展 (2026-06-23) — Pin Tab + 分享从 Pin 选择

**新功能**: Pin/Unpin 文章 + 文章查阅子 tab [全部] [Pin (N)] + 批量 unpin + 5s 倒计时撤销 + 分享 page chip 切换 [📋 全部] [📌 仅 Pin] (默认 Pin 模式) + Pin icon (nullframe 风格 SVG, 不用 emoji)
**后端**: migration 017 (article_pins + CASCADE) + 5 endpoints
**前端**: pinCol + subtab + toast + chip 切换
**测试**: 24 个新 test, 412/412 pass
**PR**: #40 squash merge → tag v1.0.8

### v1.0.7.6.2 进展 (2026-06-23)

**Bug 修复**:
- **wechat_rss_a/b 0 篇 (实际根因)** → `moni db seed-config` 上次跑时 env 变量没加载 (用户没 `set -a && source .env` 直接跑), `_env_token()` 返 `__WECHAT_RSS_A_TOKEN__` 占位符, 入库 `sources.url`, waytomaster 401 → 0 篇无错误提示
- **修复**: 实际抓取时 fetcher 检测占位符 URL (regex `__WECHAT_RSS_\w+TOKEN__`) loud error + [] 拒绝发起请求, 用户立即知道要 source .env 重跑 seed-config
- **诊断 SQL**: `SELECT name, substr(url, 1, 80) FROM sources WHERE name LIKE 'wechat%';` — 如果 url 包含 `__WECHAT_RSS_*_TOKEN__` 就是这个 bug

**安全告警** (待用户处理):
- **git 历史 token 泄漏**: commit 5b86616 (v1.0.5.6) 曾经在 sources.url 字段 hardcode 真实 token `eyJhbG...`, 7b5f52c 改为 _env_token 注入但没清 git history
- **影响范围**: 任何有 moni 仓库读权限的人 `git log -S "?token="` 可拿到 token
- **建议**: 用户在 waytomaster 控制台轮换 2 个 token, 旧 token 失效即不需清理 git history (轮换 vs git-filter-repo 二选一)

**测试**: +5 新增 (test_env_token_returns_placeholder_when_unset / returns_actual_value_when_set / a_falls_back_to_legacy_token / fetcher_refuses_placeholder_url / fetcher_refuses_empty_url)
**PR**: #39 squash merge → tag v1.0.7.6.2

**实际验证** (06-23 抓取):
- wechat_rss_a: 37 篇历史 + 6 fetched (0 inserted, dedupe 命中)
- wechat_rss_b: 41 → 59 (+18 inserted)
- 2026-06-23 发布的新文章已入库 ([未央科技weiyangx] 国务院报告)

**bestblog-x 状态**: 6:39-6:45 自动 done 5 篇入库, zombie sweeper (PR #36) 已自动清理, 无需代码修改

**操作 SOP** (写入 AGENTS.md): 任何 moni 命令前必须 `set -a && source .env && set +a` 让 env 变量自动 export

### v1.0.7.6.1 进展 (2026-06-23)

**Bug 修复**:
- **409 fallback 返错源 job_id** → list_recent(limit=1, status='running') 不按 source 过滤, 当 36kr_rss zombie 被 sweep 而 mpaypass/其他源仍有 fresh running row 时, 36kr 的 409 返的 running_job_id 可能是别源。前端拿错 job_id 轮询, modal 永远卡在「初始化中」。
- **修复**: list_recent 加 `source=` 参数 + 409 fallback 用 `source=resolved` 查同源 running job。Race 兜底: 查不到 running job_id 时(sweep 在两次 DB 调用之间发生)放过, 启动新 job。
- **手动清理**: 4 个 36kr_rss zombie running job (最早 1.5h 前) 手动 mark stale, 否则 36kr 继续被挡住

**测试**: +3 新增 (test_list_recent_filters_by_source / test_409_job_id_is_scoped_to_requested_source / test_409_falls_through_when_running_row_vanished)
**PR**: #38 squash merge → tag v1.0.7.6.1

### v1.0.7.6-hotfix 进展 (2026-06-23)

**Bug 修复**:
- **fetch_jobs 僵尸**: 8 个 36kr_rss running job 卡死 (daemon thread 异常退出但 mark_done 没成功), has_running() 永远返回 True 挡住新请求
- **修复**: sweep_zombies() 每次 /run 自动清理 2h+ 仍 running 的 job → status='stale'
- **前端**: status='stale' 映射「已中止(无响应)」红字, 轮询终止

### v1.0.7.6 进展 (2026-06-22)

**功能**:
- 异步 fetch 任务: POST /api/fetch/sources/{name}/run 立即返回 job_id, 后台线程跑
- 进度轮询: GET /api/fetch/jobs/{job_id} 每 5s 刷新
- 进度 modal: 实时显示 done_feeds/total_feeds, 当前 feed 名, 已抓文章数
- 错误处理: 10min hard timeout, 同源并发返 409, 失败/超时/僵尸状态红字提示
- 新表 fetch_jobs (migration 016): 存每次抓取实时状态

**测试**: 376 passed, 2 skipped (+17 新增)
**实测**: bestblog-yt (124 feeds) 174s 完成, 进度条实时更新

---

### v1.0.7.5 进展 (2026-06-22)

**Bug 修复**:
- **bestblog-yt 0 篇** → YouTube feed 用 Atom 1.0 格式，原代码只解析 RSS 2.0，导致 channel.find() 永远 None。修复：_fetch_feed 加 root tag 检测，Atom 走 _parse_atom。
- **bestblog-podcast 误命中 wechat tag** → tag_filter 只对 source=='bestblogs' 走 title-only，子类 fallback 到 title+content，导致 description 里的「关注公众号/小程序」误命中。修复：source.startswith('bestblog')。

**技术细节**:
- 重构 _extract_items 公共方法，RSS 和 Atom 共用 article 构造逻辑
- 修复 Python 3.12 Element truthiness deprecation 导致 find() or find() 链失败

**PR**: #33 squash merge → tag v1.0.7.5
**测试**: 359 passed, 2 skipped (351 → 359, +8 新增)
**实测**: bestblog-yt 5 feeds 抓 75 篇（修复前 0 篇）

---

### v1.0.7.4 进展 (2026-06-22)

**清理**:
- 删 `source_monitor.py` (598 行) — 早期 cron 脚本，已被 moni 包完整替代
- `moni/cron/scheduler.py` 清理 2 处 "migrated from source_monitor.py" 注释
- 移除 `/Users/mt16/dev/source-monitor/` 空壳目录 (根因: editable install 误指导致回归时全 API 404)

**PR**: #31 squash merge → tag v1.0.7.4
**测试**: 351 passed, 2 skipped (与 main 一致)
**影响**: 纯清理，无功能/行为变更

---

### v1.0.7.1 - v1.0.7.3 进展 (2026-06-18)

**新功能 — BestBlogs 系列信源扩展**:
- 新增 `bestblog-podcast` (60 个小宇宙播客 feeds)
- 新增 `bestblog-yt` (20 个 YouTube 频道 feeds)
- 新增 `bestblog-x` (160 个 x.com/Twitter feeds)
- `bestblogs` 合并 ALL OPML (291 个新英文技术博客)
- 多 OPML URL 逗号分隔支持，fetch 时合并去重
- 本地 OPML 文件支持

**技术细节**:
- FetcherBestBlogPodcast / FetcherBestBlogYt / FetcherBestBlogX 子类复用 FetcherBestBlogs
- bestblogs 排除 xgo.ing 域名的 feed (已移到 bestblog-x)
- 默认 tag: wechat, ai-fintech
- 黑名单: 入境游/文娱/明星/游戏/影视/电商/消费/美食/腾讯控股

---

### v1.0.6.9 进展 (2026-06-18)

**新功能 — 菜篮子 (Basket)**:
- 被过滤丢弃的文章不再消失，自动存入 basket 表
- 新增「菜篮子」UI tab，查看被丢弃文章 + rescue 回正式文章库
- 支持按来源/过滤原因/rescue 状态筛选
- rescue 时可选择 tag，自动关联到 article_tags
- 新增 4 个 API 端点: 列表/详情/rescue/删除

**技术细节**:
- 过滤器 (blacklist/whitelist/tag_filter) 丢弃时自动写 basket
- basket 表 schema 同 articles，额外加 dropped_reason/dropped_by/rescued_at
- INSERT OR IGNORE 防重复
- 新增 18 个测试覆盖 basket 功能

---

### v1.0.6.8 进展 (2026-06-17)

**功能**:
- 导入全文功能从仅 bestblogs/虎嗅 扩展到所有信源
- 移除前端 x-show 源过滤，mpaypass/36kr/qbitai 等均可导入 .md 全文
- 后端 import-fulltext 接口本身无源限制，改动仅在前端

---

### v1.0.6.7 进展 (2026-06-16)

**新功能**:
- 分享页文章选择新增 tag 分类下拉筛选
- 来源 + 分类 + 每页 三维筛选

---

### v1.0.6.5 进展 (2026-06-16)

**新功能**:
- 虎嗅 RSS 源 (第 7 个源): FetcherHuxiu, 全文抓取, 绑定 ai-fintech/miniprogram/wechat
- 单源「立即抓取」强制执行 (force=True)，不再被间隔挡住
- 虎嗅/BestBlogs 点「立即抓取」弹时间范围选择 modal

### v1.0.6.6 进展 (2026-06-16)

**Bug 修复**:
- 虎嗅 boilerplate 清洗: 清除 "本文来自微信公众号" / "原标题" 等来源声明
- CRUD 删除失败: 前端加 `?confirm=1` 参数
- 文章查阅分页: 新增每页条数选择 + 上下页翻页

---

### v1.0.6.4 进展 (2026-06-15)

**新功能**:
- Tag 过滤丢弃明细：抓取结果下方折叠面板，显示被丢弃文章标题（最多 20 条）
- 全部抓取时按源分组展示，单源抓取时直接展示
- tag_filter.py 返回 dropped_articles 列表

---

### v1.0.6.2 进展 (2026-06-15)

**新功能**:
- ⚡ 全部抓取按钮：抓取管理页顶部，一键跑所有源
- POST /api/fetch/all/run 端点：遍历所有源 + 详细日志写 cron_runs + fetch_log
- 周一补抓：自动检测周一 → date_from=周六
- cron 改工作日 9:10 AM（不跑常驻进程，手动触发为主）
- fetch_log 新增 8 列 (migration 014): cleaned/filtered/dropped_blacklist/dropped_whitelist/dropped_tag_filter/filters_applied/tags_used/deduped

**Bug 修复**:
- wechat_rss_b URL 截断修复（seed_config.py 存的是截断 token）
- wechat_rss token 从 .env 读取，不硬编码在代码里（安全修复）
- .env 拆成 WECHAT_RSS_A_TOKEN + WECHAT_RSS_B_TOKEN

**测试**: 322 passed, 2 skipped

---

### v1.0.6.0 进展 (2026-06-15)

**前端重构**:
- sidebar → topnav 顶栏导航 (Sophon.at 参考)
- 中国传统色 oklch 配色主题 (纯衣/玄色/天水碧/紫藤萝)
- 按钮全面重设计 (自包含样式 + hover/click 动效)
- 主题管理器: 🎨 切换/导入 globals.css/localStorage 持久化
- 🌙/☀️ 暗色模式一键切换

**服务控制**:
- topnav 状态指示灯 (绿点 + PID)
- ↻ 重启按钮 (确认 + 轮询等待 + Toast)
- GET/POST /api/service/status|restart|stop 端点

**测试**: 322 passed, 2 skipped

---

### v1.0.6.1 进展 (2026-06-15)

**新功能**:
- 抓取结果结构化解释: 「立即抓取」后 JSON 左侧新增文字说明
- 结果摘要: ✅ 新增 X 篇 / ⚠️ 全部重复 / ℹ️ 无新内容
- 流程分解: 远程拉取 → 解析 → 去重 → 过滤
- 双栏布局: 左侧结构化解释 + 右侧原始 JSON

**Bug 修复**:
- wechat_rss_b URL 截断修复 (JWT token 入库不完整)

**测试**: 322 passed, 2 skipped

---

### v1.0.5.25 进展 (2026-06-14)

**新功能**:
- fetch_config UI 编辑器：配置管理 → 抓取源 → 「抓取配置」按钮 → modal 编辑参数
- BestBlogs API 按需接入：分享时调 API 获取 score/summary/mainPoints/keyQuotes/tags
- 分享选项扩展：摘要来源（moni AI / BestBlogs / 两者）、BB 增强字段可选
- DB migration 013：articles 表加 bb_score/summary/one_sentence/main_points/key_quotes/tags/resource_id/fetched_at

**Bug 修复**:
- worker PNG/HTML 格式：检测 LLM 实际生成的文件并复制，不再保存文字描述
- PDF 末尾标签：跳过文档末尾的孤立标签段落
- SQL fallback：bb_* 列不存在时 fallback 到 NULL 查询

**测试**: 322 passed (新增 22 tests: fetch_config 10 + bestblogs_api 12)

---

### v1.0.5.24 进展 (2026-06-13)

**新功能**:
- BestBlogs 智能抓取：时间范围选择（自动/过去N天/自定义日期）
- 全文导入：文章详情页粘贴本地 .md 路径 → 更新 raw_content → AI 摘要/核查使用全文
- 复制原文链接：一键复制到剪贴板
- tag filter: bestblogs 只匹配标题（避免摘要 boilerplate 误命中）

**测试**: 300 passed (新增 19 tests)

---

### v1.0.5.22 进展 (2026-06-13)

**Bug 修复**:
- 黑名单模式记录 keyword_hits
- `scripts/backfill_keyword_hits.py` — 历史文章 keyword_hits 补录

---

### 历史版本

- v1.0.5.21: PDF 日报分享格式 + 版式配置化
- v1.0.5.20: tag 关联逻辑修正 + keyword_hits 完善
- v1.0.5.19: share bugfix + AI 摘要回写 + LLM 统一 + LLM 设置 UI
- v1.0.5.18: share 功能 v2 重构
- v1.0.5.17: UI worker 按钮 + 进度条
- v1.0.5.16: confirm-modal before queueing
- v1.0.5.15: 5 user fixes
- v1.0.5.14: display_name + 5 fixes
- v1.0.5.13: timezone-aware created_at
- v1.0.5.12: 6 UI bug fixes
- v1.0.5.11: CRUD create/edit modal + share card grid
- v1.0.5.10: 双端口 (8900/8901)
- v1.0.5.9: retire legacy wechat_rss
- v1.0.5.8: remove 36kr_newsflash
- v1.0.5.7: extract publisher from title prefix
- v1.0.5.6: wechat_rss multi-account
- v1.0.5.5: 36kr captcha bypass
- v1.0.5.4: tag-keyword 过滤
- v1.0.5.2: LLM 异步化 + 进度面板
- v1.0.5.1: 3 个 UX bug 修复
- v1.0.5: P0 + P1 全做完

</details>

---

## 分享格式一览 (7 种)

| 格式 | 说明 | Prompt 文件 |
|------|------|-------------|
| plain_text | 纯文本 | 无（直接输出） |
| markdown | Markdown | format-markdown.md |
| html_landing | HTML 网页 | format-html.md |
| rich_text | 富媒体文本 | format-rich-text.md |
| info_graphic_png | 信息图 PNG | format-info-graphic-png.md |
| report_png | 报告 PNG | format-report-png.md |
| pdf | PDF 日报 | format-pdf.md |

---

## 抓取源 (7 active)

| 源 | 间隔 | 类型 | 状态 |
|---|---|---|---|
| 36kr_rss | 5min | html | ✅ |
| mpaypass | 5min | html | ✅ |
| qbitai | 5min | rss | ✅ |
| wechat_rss_a | 5min | wechat_aggregator | ✅ |
| wechat_rss_b | 5min | wechat_aggregator | ✅ |
| bestblogs | 5min | rss (OPML 聚合, 375 号) | ✅ |
| huxiu | 5min | rss | ✅ |
