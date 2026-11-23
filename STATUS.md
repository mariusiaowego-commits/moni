# STATUS — moni

> 项目状态与功能清单。每个 Phase 完成时更新日期 + 阶段描述。

---
## 已发布: **v1.0.114 — 银行同业抓取可见性** (2026-11-23 · PR #272 → `12ffca3`、PR #273 → `a9ac7b5` · tag `v1.0.114` · Sprint 26112067)

dad 看到银行同业连续两次「异常」：一次 `部分完成` 只有一句 `feed 不完整: 1 个目标失败 (icbc-news)`，
另一次 `完成` 但 `0/0 feed · 拉取 0 · 入库 0`。排查后两件事都不是源站故障，是可见性问题。

**根因**
- ICBC 栏目页无日期，`config/bank_targets.json` 里 `static: false`，每次必须走 agent 渲染；本次 agent 偶发失败，2 分钟后重跑即 `errors: []`。
- 「0 篇」是去重：当天 25 条与 11-20 完全重合，25/25 已在 `imported_ids`；银行官网自 11-20 00:00 起没有新稿。
- 真问题：失败原因没落库；界面把「抓了但重复」显示成「没抓」。

**① 失败原因透传 + 人话**
`failed_targets_detail` 新增 target/label/reason/error；warning 改为
`2 个来源未抓到：工行（访问超时）、中行（页面无法读取）`。原因映射：访问超时 / 网络连接失败 /
页面无法读取 / 未抓到内容；`补抓失败` 同套说法。

**② 区分「没抓」与「抓了但重复」**
`result` 新增 `raw_items`、`skipped_existing`。状态卡片 0 新增时显示「无新增」，明细多「检查 / 已在库」；
结果块显示「同步正常：检查 25 篇均已在库，无新增」，有新增时显示「同步完成：新增 M 篇，D 篇已在库」
（仅 bank_feed，不影响其他源）。文案由 agy 产出（Herdr `w52:pG`）。

**③ 口径修正**
首版 `skipped_existing=167`（把 `max_days` 内 5 个历史 feed 的重复都算进来），#273 收紧为只统计当天。

**验证**：`test_bank_feed.py` 55 passed；五文件联合 174 passed；两笔 CI 均 success；
合并后部署 PID 48133、`/api/fetch/sources` 200；真实 feed 实测
`{'raw_items': 25, 'skipped_existing': 25, 'inserted': 0}`。

**未做**：ICBC 的 agent 自动重试 / 静态解析回退（P3，另开 PR）。

## 已发布: **v1.0.113 — 核查等待态收口 + 时效门禁逐条降级** (2026-11-20 · PR #270 → `c0074f1` · tag `v1.0.113` · Sprint 26112066)

dad 报的 4 条反馈：点了联网核查还提示“没跑过”；停留很久后进度条消失回到“未核查”；
重进有结果却没有任何“终值还是要等”的提示；任务 `39bd90ff` 6 条证据全【待核实】、结论只有三个字。

**① 空态提示**
`index.html` 的提示条件补 `!readerJobBusy('fact_check')`，并按「联网核查 / 事实核查」区分文案。

**② 终态回读**
done 先回读详情、成功才清槽位；回读期间显示「已生成，正在刷新…」，失败保留
「结果已生成，点此刷新」（按钮只重发回读，不重新入队）。切篇后回调直接 `return`，
不再误清另一篇的槽位（审计 P2）。

**③ 重进恢复**
`resumeArticleJobs` 改单次 `GET /api/llm/jobs?article_id=X&limit=10`，按槽位取最新一条，
仅恢复 `pending/running/failed/stale`；done 不重复画状态条。

**④ 时效门禁（Gate 3）**
只把超 90 天的【已核实】证据逐条降级；仍有新鲜【已核实】时整篇保留【已核实】，
`timeliness=90d_partial`，summary 前缀「部分参考证据超 90 天」。无新鲜证据才整篇降级（旧行为）。
`GET /api/llm/jobs/{id}` 的 `fact_check` 载荷补 `summary` / `timeliness`。

**验证**
受影响 7 个文件 `117 passed`；全量 `2411 passed, 2 skipped`；`test_frontend_render_hygiene` `36 passed`；
CI `pytest · py3.12` success；agy 复审 APPROVE。生产重跑文章 `64e56ae7`：
`conclusion=【已核实】(已过期 30-90 天)`、`timeliness=90d_partial`、`demoted_from=null`、
`evidence` 已核实 4 条（3–50 天）/ 待核实 2 条（含 156 天那条）、弹窗带人话 summary。

**部署**：`v1.0.113` = `c0074f1`，8900 PID **75940**；备份
`data/backups/news.db.pre-v1.0.113-redeploy-*`（`integrity_check` ok）。
一条 CI 教训：`app.js` 二次改动 / `reader.js` 改动都要 bump `?v=`，否则
`test_versioned_assets_busted_after_their_last_commit` 直接红。

---
## 已发布: **v1.0.112 — 浏览器抓取卫生（文档外迁 / 脱敏 / Profile 7 迁移 / daemon 锁）** (2026-11-20 · PR #267 → `44eb3ea`，同版本含 PR #268 → `ffc8b57` · tag `v1.0.112` · Sprint 26112065)

公开仓暴露治理的第一刀，外加把 36kr 用的 Chrome profile 从用户真实 Chrome 目录里拔出来。

**① 抓取文档出库**
`CHROME-PROFILES.md`（118 行）与 `docs/BROWSER_FETCHER_PATTERN.md`（277 行）从公开仓删除，
操作手册改存私有 wiki（`projects/project-moni/browser-fetch-runbook.md`，hermes-base-wiki `6829f7b`）。
`README.md`、`docs/archive/README.md`、`docs/plans/2026-09-17-sprint-23-browser-session-lifecycle.md`
的引用同步改掉。实测 `rg "BROWSER_FETCHER_PATTERN|CHROME-PROFILES"` → 0 命中。

**② 邮箱脱敏**
5 个 tracked 文件里的 4 个真实 Gmail 换成 `user1..6@example.com` 合成账号，断言同步改。
实测按 4 个真实 Gmail 的本地部分逐文件扫描 → 0 命中（不在文档里复述具体字符串）。
历史 commit 里的邮箱不动（要 `filter-repo`，非本 sprint 范围）。

**③ Profile 7 迁移**
`scripts/migrate-chrome-profiles.py --apply`（不带 `--include-xwatch`）：
`Chrome/Profile 7`（165M / 3 条 36kr cookie）→ `~/.moni/chrome/daemon`；legacy 目录已消失，
`profile-map.json` 已生成，`chmod 700` 完成，备份 84M 在 `~/Documents/moni-chrome-profile-backup-20261120/`。
用户 `Profile 6` 的 334 条 cookie 未变。试抓 `fetched=30 parsed=30 inserted=3 parse_errors=0`，
**无需重过验证码**；验证后 `stop_chrome()` 释放 9223。

**④ manual_captcha 去硬编码**
不再把 `MONI_CHROME_USER_DATA_DIR` 钉到 legacy `Chrome/Profile 7`，交给 `profile_paths` 解析，
避免迁移后被 warmup 脚本重新拉回旧目录。

**⑤ daemon 锁泄漏修复**
`ensure_chrome()` spawn 成功即 `release_daemon_lock()`（原来持锁到进程退出，Chrome 崩一次之后
同进程所有 respawn 都撞 `Another instance holds daemon.lock`）。新增
`test_ensure_chrome_releases_lock_after_success` 守住这个行为。

**验证**：定向 48 passed（guard + migration 两文件）；全量 **2402 passed / 2 failed / 2 skipped**
（`test_migration_039` 因沙箱不能写主仓 `.git/worktrees` 失败、CI 绿；`test_worker_lock` 为 flake，
单独复跑 28 passed）。CI `pytest · py3.12` success；合并用 `sha=8fc8342` 守护，
合并后 `git diff --quiet origin/main 8fc8342` 通过。

**部署**：`v1.0.112` tag = `ffc8b57`（15:07:50），8900 PID **41055** 启动 15:08:29 晚于 release 提交；
`/`、`/api/fetch/sources`、`/api/articles` 均 200；9223 无监听（无孤儿 Chrome）。

**同版本另含** PR #268 联网核查 JSON 解析修复（本会话交付，细节见该 PR）。

## 已发布: **v1.0.111 — 阅读器任务状态机 + 真进度** (2026-11-20 · PR #265 → `e71773c` · tag `v1.0.111` · Sprint 26112063)

dad 报的原始问题：点「重新联网核查」后只有一个「核查中」，退出弹窗再进来变回「待核查」，
可大模型任务其实还在跑；停在弹窗里也没有任何进度，只能退出去刷新。

**① 等待态从 `articleDetail` 剥离成槽位状态**
`summary` / `fact_check` 两槽（快核与联网核查共用后者），文案全部由纯函数 `job_view.js` 产出。
根因是把「⏳ 占位」写进正文字段，失败/超时后撤不掉，只能刷新页面。

**② 契约层固定 7 个 phase**
`moni/llm/phases.py`（含用于判死的 `stale`）+ `response_model` 挂到 4 个路由，`/openapi.json` 当闸门。

**③ 后端补进度数据**
`GET /stats/duration` 回 p50/p90（只统计 `status='done' AND duration_ms>0`，n<3 回 None）；
pending 回 `queue_position` / `queue_total`；`?status=` 白名单补 `stale`。

**④ 前端状态条**
排队中·第 X/Y 位 / 已运行 M 分 S 秒 / 失败原因 + 重试 / stale「服务中断或超时中止」；
queued/running 时旧结论隐藏；关弹窗 `stopReaderJobs()` 收定时器与秒表；按钮等待态只用 `:disabled`。

**验证**：测试闸门 41 passed（契约 / 时长统计 / 队列位次 / node 跑 `job_view.js` / AST 不变量 / 前端接线）；
全量 `2384 passed, 2 skipped, 1 failed`（唯一失败仍是 `test_migration_039` 沙箱不能建 worktree，非沙箱重跑 33 passed）。
界面在 8901 + 生产 DB 副本实测 pending / running / failed 三态，秒表真实走动；
接口实测 `queue_position=1`、`p50_sec=66.4 / p90_sec=161.7`（n=45）。

**未做**：流式第二层（`progress` 字段已声明、恒为 null）。

## 已发布: **v1.0.110 — 信源管理 UI 返修** (2026-11-20 · PR #260 → `9877a00` · tag `v1.0.110` · Sprint 26112001)

dad 在 v1.0.109 上线后继续提了 5 条，加两轮独立审计，本版一次修完：

**① 眼睛双态**
隐藏行渲染闭眼 SVG + 按钮底色，恢复后回睁眼。实测：未隐藏行可见 1 个睁眼 SVG、
底色 transparent；已隐藏行可见 1 个闭眼 SVG、底色 `oklch(0.928 0.034 84.6)`、title「恢复显示」。

**② 配置-信源表列宽**
colgroup 重排到 1198px（操作列 189 / 状态列 122 / 标签列 223），5 个图标按钮与
「启用 + 已隐藏」双徽章都放得下；表头「同步间隔」简化成「间隔」不再折行。
实测 1300/1440：`scrollWidth == clientWidth == 1198`，横滚消失；1100 视口退化成横滚，
滚到底最右按钮完整可点。

**③ 同步中计时对齐**
`.pixel-label` / `.pixel-elapsed` 统一 `line-height: 28px`，文字中线 119 / 119，差 0。

**④ 执行状态明细被裁**
根因是 `main.css` 还留着旧版 `24px + 1fr` 双列（配一条已删除的竖线 rail），步骤被压进
24px 后全部退化成省略号。删掉旧规则、改 300px 自适应分栏：容器 24 → 1112px，截断 6 → 0，
`.fetch-ops-body` 不再有内层滚动。

**⑤ 重开已结束的同步**
从执行状态点「查看详情」不再看到「同步中...」+ 满高空舞台：弹窗直接呈终态
（完成条带 / 状态词），批量「新增 N 篇」从各源明细汇总（12+5=17），失败态只放错误横幅，
已停止态标题写「已停止」而不是「同步失败」。

**⑥ 遗留清理**
cron 刷新按钮改 icon-only（去掉底色与描边）、全站 modal 去毛玻璃（`backdrop-filter: none`）、
删 `.is-closing` 与 `.btn-refresh` 死样式、弹窗宽 620 → 720px。

**测试**：`pytest tests/` → **2342 passed, 2 skipped, 1 failed**（`test_migration_039` 沙箱内
无法建 worktree，提权单跑 **1 passed**）；前端子集 **564 passed**；`test_frontend_render_hygiene.py`
**36 passed**（资源 `?v=` 统一 `20261120011`）。独立只读审计 **APPROVE**（无 P0/P1），
其抓出的「批量重开新增 0 篇」与 3 条 P2 已修。
rebase 到 `845fa89`（含 #261 滚动锁 / #262 心跳续租）后复跑：
`pytest -k "frontend or fetch or source or motion or microinteraction or render or modal or llm_jobs or scroll"`
→ **580 passed, 2 skipped**；`test_frontend_modal_scroll_lock.py` + `test_llm_jobs_heartbeat.py` → **10 passed**。

**合并方式**：GitHub 不可达期间先做过一次纯本地 squash（`0d0a521`）；恢复后改走 PR ——
分支 rebase 到 `845fa89` → **PR #260** squash 合并 → **`9877a00`**。本地 main 用
`git rebase --onto origin/main 0d0a521 main` 丢掉重复 squash、只重放 STATUS 提交（`61dc5f3`）；
`v1.0.110` 重指 `9877a00` 后推送，远端 `refs/tags/v1.0.110` = `9416887` → `9877a00`，
旧本地 squash 留在 `refs/backup/v1.0.110-local` 备查。

**部署**：8900 重启 PID **67861**（`launchctl kickstart -k gui/501/com.moni.web`），
`version v1.0.110 / commit 61dc5f3 / commits_ahead 1 / schema 52 / worktree_dirty=false`；
备份 `data/backups/news.db.pre-v1.0.110-redeploy-20261120-111123`（`integrity_check` ok / schema 52 / 5660 篇）；
schema 无变化，未跑 migration。
**线上冒烟**：`/` 200（300421B）；静态版本 `?v=20261120011`；线上 `modal.js` 含 `_syncScrollLock`（7 处），
`app.js` 无 `modal-locked` 残留（#261 锁收敛生效）；`/api/config/sources` 18 条全部带 `is_hidden`，其中 2 条已隐藏。

## 已发布: **v1.0.109 — 信源管理改版** (2026-11-19 · PR #253 → `1993082` · tag `v1.0.109` · Sprint 26111901)

dad 在生产 `/fetch` 页实测提了 7 条（弹窗多重滚动条 + 滚轮穿透 + 完成态占位过大、
「抓取」措辞、同步中关弹窗找不回进度、信源隐藏未做、执行状态区 slop、刷新按钮底色描边、
三个信源 logo 与品牌无关）。本分支一次处理完：

**① 文案：抓取 → 同步**
`index.html` 45 处 + `app.js` 43 处，动词改「同步」，标题「抓取管理」→「信源管理」；
`USER_GUIDE.md` 同步改名。代码注释与 API 字段名（`fetch_*`）不动。

**② 信源隐藏（新增 `sources.is_hidden`）**
migration `052_source_hidden.sql`（纯 additive，默认 0 = 全部可见）；
`GET/POST/PUT /api/config/sources` 透传；「配置管理 → 信源」行内眼睛按钮切换，隐藏行标「已隐藏」。
**隐藏 = 不显示，停用 = 不跑**，两者独立（单测专门锁这条）。

**③ 同步进度弹窗**
页面滚动锁（`body.modal-locked` + `overscroll-behavior: contain`）→ 滚轮不再穿透背后页面；
弹窗内只剩一个滚动容器（`index.html` 内 `overflow-y:auto` 计数 1 → 0）；
完成时粒子舞台 420px → 72px，完成卡压成一行；标题按状态走（全部已同步 / 同步失败）。
hermes（profile `pixel`）+ unslop 技能改版。

**④ 执行状态区**
任务列表改紧凑行 + 1px 分隔线，去掉彩色卡片 / 徽章 / 解释性文案；行内「进度 / 停止 / 清除」；
批量结果默认收起。agy（herdr `w52:pG`）改版。

**⑤ 后台同步**
关掉进度弹窗不再丢任务：顶部芯片 + 任务行「进度」都能把弹窗叫回来；
终态任务**不再 6 秒自动消失**，留在执行状态区等用户「清除」（上限 12 条）。

**⑥ logo**
新增 `bank_feed`（银行柱廊）、`chinese_independent_blogs`（书 + 信号弧）、`follow_builders`（X + 星芒）
三张品牌图标；15 张底板由纯黑 `#000000` 统一提亮为 `#2E2A24`，四角统一 radius 46 透明遮罩。
生成规范与提示词见 `docs/source-icon-style.md`。

**⑦ 复核修正（独立审计 P1）**
隐藏源不参与链路绘制 → 画面节点数与「13/13」对不上。链路 filter 改回与后端
`Scheduler.batch_sources()` 同口径（隐藏照跑），并加单测锁死。
`USER_GUIDE.md` / `docs/OPERATION_GUIDE.md` 的「抓取」一并改为「同步」。

**测试**：合并最新 main 后全量 `pytest tests/` → **2341 passed, 2 skipped, 2 failed**
（`test_migration_039` 沙箱内无法建 worktree，提权单跑 **1 passed**；`test_showcase_*` 缺 PIL，与改动无关）；
定向 `frontend/source_hidden/migration` → **271 passed**。浏览器实测见 sprint verify。

**部署**：8900 重启 PID **61574**，`version v1.0.109 / commit 1993082 / schema 52 / worktree_dirty=false`；
备份 `data/backups/news.db.pre-v1.0.109-20261119-204943`；迁移显式执行（服务启动不跑 migration）
`Applying migration 052_source_hidden.sql (v51 → v52)`。
**线上冒烟**：`/fetch` 200；`/api/config/sources` 18 条全部带 `is_hidden`（hidden=0）；
静态资源 `app.js?v=20261119012`、`main.css?v=20261119011`、新图标 `bank_feed.png` 200；页面「抓取」计数 0。

## 已发布: **v1.0.108 — 已核查文章提供重跑入口** (2026-11-19 · PR #256 → `a907492` · tag `v1.0.108`)

**问题**：底部操作条的核查按钮用 `x-show="!articleDetail?.has_fact_check"` / `!has_web_fact_check` 控制，**一旦核查过就永久消失**。v1.0.107 扩充白名单后，历史上跑过的文章没有任何入口重跑，用户只能看着旧结论。

**修法**：
- 联网核查按钮**常驻**，文案在「联网核查 / 重新联网核查」之间切换。
- 快速核查仅在**没有联网结果**时出现（`!has_web_fact_check`），文案「事实核查 / 重新核查」。
  原因：快速核查写 `fact_check_method='llm_inference'` 并覆盖 `fact_check_conclusion`，但不会把
  `has_web_fact_check` 置 0 → 会出现「徽标显示联网核查、结论却是单模型推断」。
- 两个按钮加 `title` 说明旧结论 / 旧证据链会被替换。
- 审计 P2：快速核查端点补 `_has_active_job` 409 防重复入队（与联网核查共用闸门）。

**审计**（agy）：主改动 APPROVE WITH NITS（唯一 P2 已修）→ 增量审计 `88910fe` APPROVE，无 findings。

**验证**：全量 **2335 passed, 2 skipped, 1 failed**（唯一失败 `test_worker_lock` 时序用例，单跑 2 passed，load 22.5）；定向 `test_api + test_fact_check_plus + test_frontend_article_job_watch` **178 passed**。

**生产真机验收**（8900）：文章 `4a111c743cb775e3` 点「重新联网核查」→ `⏳ (job 67bfab67)`，**不刷新** 1m45s 后自动更新为新的可读结论 + `核查时间 20:11` + `时效在 30 天内`（旧徽章「时效降级 · 超 90 天旧证」消失）。

**部署**：8900 PID **50414**，`v1.0.108 / a907492`；备份 `news.db.pre-v1.0.108-*`。
后续 `#257`（`8da2829`）删除误提交的审计产物 `result.md`，无代码变更。

## 已发布: **v1.0.107 — 事实核查白名单扩充 + 域名维护报表** (2026-11-19 · PR #254 → `46a2ad6` · tag `v1.0.107`)

**问题**：`config/fact_check_domains.toml` 停在 v1.0.100 快照。生产库实测 132 个证据域名里有 **58 个未收录**（60 条证据，占 25%），包括 `shengjingbank.com.cn`、`psbc.com`、`unionpay.com`、`bloomberg.com`、`cnbc.com`。配置头注释承诺的维护入口 `moni fact-check-domains --report` **在 CLI 里根本不存在**。

**修法**：
- 新增只读报表 `moni fact-check-domains --report`（`--limit` / `--json`），按证据出现次数列出域名与分档，未收录单独排队。
- 按实测数据 + 明确依据扩档：`tier_a` 2 个（`tc260.org.cn`、`publicnow.com`）、`tier_a_brand` 17 个、`tier_b` 25 个、`tier_c` 14 个。未收录域名仍一律按「不计入」处理。
- 修 eTLD+1 多段后缀：补 `co.kr` / `or.kr` / `co.jp` / `co.in` / `com.br` 等。修复前 `asiae.co.kr` 与 `en.yna.co.kr` 都被切成 `co.kr`，两家媒体被当成同一个注册域。

**审计**（agy 独立只读，APPROVE WITH NITS）：
- P2 `moomoo.com` 是零售券商资讯频道，不算一手 → `tier_a_brand` 降 `tier_b`
- P3 `ai-bot.cn`（导航汇总）、`kodekloud.com`（培训文档）不具独立采编 → 降 `tier_c`
- 三条已修并补断言锁死分档

**验证**：
```
一手来源   28 域 / 40 条 → 47 域 / 60 条
编辑制媒体 41 域 / 126 条 → 66 域 / 152 条
不计入      5 域 / 10 条 → 19 域 / 24 条
未收录     58 域 / 60 条 →  0 域 / 0 条
```
- 全量 `pytest` → **2334 passed, 2 skipped**，0 failed
- `-k 'fact_check or domain or gate3'` → **107 passed**
- 新增 `tests/test_fact_check_domains_report.py` 7 条断言

**部署**：8900 重启 PID **21543**，`version v1.0.107 / commit 46a2ad6 / schema 51 / worktree_dirty=false`；备份 `data/backups/news.db.pre-v1.0.107-20261119-175941`；生产实跑报表 → **未收录 0 / 全部域名已收录**。

## 已发布: **v1.0.106 — 摘要/核查任务完成后页面自动同步** (2026-11-19 · PR #250 → `b9d8b15` · tag `v1.0.106`)

**问题**：点「联网核查」后面板一直停在 `⏳ 联网核查中... (job xxx)`，任务完成后也不变，用户只能自己刷新或重进页面。

**根因**：老 `_pollFactCheckPlusJob` 只轮询 30 次 × 2s = **60 秒**，而线上同类任务实测 **2.2 / 2.7 / 4.4 分钟**；`generateSummary` / `generateFactCheck` 两个入口压根不轮询，只放 `⏳` 占位符。

**修法**：三个入口统一 `watchArticleJob`——3s → 6s → 15s 退避、预算 30 分钟；`done` / `failed` / `cancelled` / `404` / 超时**所有出口**都回读文章详情；打开文章时 `resumeArticleJobs` 按 `/api/llm/jobs?article_id=&status=pending|running` 自动续上未完成任务；占位符收敛到 `_articleJobPlaceholder` 单一来源。agy 审计 P2（404 空转）/ P3（续跑并发写 `articleDetail`）同 PR 修掉。

**验证**：定向 `test_frontend_article_job_watch + render_hygiene + reader_tabs` → **55 passed**；全量 → **2326 passed, 2 skipped, 1 failed**（唯一失败 `test_worker_lock::test_sigterm_kills_blocking_child_that_ignores_term`，单跑 3 次全绿，机器 load 12–14 时时序抖动，与本改动无关）；GitHub CI `pytest · py3.12` **success**。

**生产验收**（8901 隔离端口，两次真实 LLM 任务）：任务 1m14s，**不刷新页面**自动从 `⏳` 变成 `【待核实】` + 核查时间 + `证据链 4 条`；续跟路径 21s 自动更新。

**部署**：8900 重启 PID **81875**，`version v1.0.106 / commit b9d8b15 / schema 51 / worktree_dirty=false`；静态资源 `app.js?v=20261119007`、`reader.js?v=20261119007`；备份 `data/backups/news.db.pre-v1.0.106-20261119-162648`。

## 已发布: **v1.0.104 — 联网核查 Gate 3 时效误判修复** (2026-11-19 · PR #246 → `c0363e3` · tag `v1.0.104`)

**生产事故**：文章 `4a111c743cb775e3`（央行公示辽宁第四批金融科技创新应用）联网核查后 UI 只显示「【存疑】」两个字。

**根因**：6 条证据里 4 条核心证据 13/13/13/29 天，2 条历史背景证据 787/651 天。原 Gate 3 取全批 `max(days_old)=787` → 触发 `90d_demote`，把 `【待核实】` 改成 `【存疑】`（旧 ≠ 矛盾），并清空 `summary`（导致分享预检反复判缺 summary）。

**修法**（agy 只读评审 **APPROVE WITH EDITS**）：时效聚合改代码侧判定——有【已核实】证据取 `max`，否则取 `min(days_old)`（最新一条）；91+ 只把【已核实】降【待核实】，【待核实】/【存疑】不动；**不再清空 summary**；Gate 2 只在开头加【门禁降级：权威源不足】；how 文案改报最新/最早来源距今；UI 加时效徽章（`30d_warn` / `90d_demote`）。

**验证**：`pytest tests/ -q` → **2316 passed, 2 skipped**；`-k 'fact_check or frontend'` → **213 passed**；新增 6 条语义契约 + 生产场景回归（4 近期 + 2 历史背景）；UI 实测旧数据 `timeliness=90d_demote` 显示「时效降级 · 含超 90 天旧证」。

**已知边界**：历史已落库的 `fact_check_conclusion/summary` 不回填，需重跑联网核查生效。

## 已完成: **全库正文清洗落地 + 闸门打开** (2026-11-19 · Sprint 58 收尾 · tag `v1.0.105`)

dad 授权后在生产库执行了两件此前挂起的事。

**① `clean-articles --apply`（872 篇正文重写）**

```text
dry-run: scanned=5323 changed=872 backfilled=4451 emptied=0 chars_removed=54670
apply  : scanned=5323 changed=872 backfilled=4451 emptied=0 chars_removed=54670
run id : 7f739a070b334867（耗时 0.21s）
```

只重写 `articles.raw_content` 一列（外加 `content_state/chars/reasons/cleaner/cleaned_at`）。
删的是：文末授权声明 / 免责声明 / 投稿邮箱 / 编辑署名行、行内 CTA 尾巴、
HTML 标签与实体、零宽字符、`U+FFFD` 乱码、YAML frontmatter。**不删正文段落。**

**清洗前后对照（全库计数）**

| 检查项 | 清洗前 | 清洗后 |
|---|---|---|
| 文末授权声明残留 | 200 | **0** |
| 零宽字符 | 16 | **0** |
| `U+FFFD` 乱码 | 55 | **0** |
| YAML frontmatter | 34 | **1**（YAML 头无闭合标记，按安全策略跳过） |

**改写幅度核对**：872 篇中 848 篇变短、24 篇等长原地替换（去零宽/空格），
**0 篇正文增长**；变空的只有 3 篇，原本就是 1 字 / 40 字的纯样板文字。
可见文本变化的文章，其 `summary`/`ai_summary`/`brief_body_md` 已置空待重生成。
备份：`/private/tmp/moni-pre-clean-20261119-145347.db`（119MB，保留待 dad 确认后再删）。

**② 打开内容闸门**

```text
PUT /api/moderation/labels/gate {"enabled":true} → {"gate_enabled": true}
summary.gate_enabled = True
```

实测拦截：`spam_service` 3 篇（`3e40069e` 本人诚信经营长期合作 / `dd68ae11` 二十年商家做的是一个信用 /
`fceab32a` 视频号点赞刷量）已从简报与摘要流程中剔除。
`engagement_bait` / `low_value_rant` **不在拦截范围**（只标注，不改流程）。

**测试**：`test_moderation_labels/service/labels_api/brief_html` 39 passed；
`-k "clean or content"` 156 passed。

---
## 已完成: **闸门改成运行时配置** (2026-11-19 · Sprint 58 补丁 · PR #247 → `c5f1bc8`)

把「分级标注闸门」从代码常量 `GATE_ENABLED` 改成**数据库配置**，以后开关不再发版、不再走 PR。

**改法**：migration `051_app_settings.sql`（`app_settings` 键值表，纯 additive，默认 `0`=关）；
`moni/moderation/service.py::gate_enabled()/set_gate_enabled()` 每请求实时读库，缺表/缺键一律按关闭；
写接口 `PUT /api/moderation/labels/gate`（挂在 labels 路由，`/api/moderation` 仍只读）；
`moni/share/brief_html.py` 主查询与 fallback 共用同一段过滤条件，无旁路；
`/cleaning` 页新增开关按钮。

**已部署（2026-11-19 06:12 UTC）**：迁移 `v50 → v51`；`com.moni.web` 重启后 `/health` 200；
实测 `PUT {"enabled":true}` → `summary.gate_enabled=true`，`PUT {"enabled":false}` → `false`，
全程不重启、不发版；当前生产为**默认关闭**。

**用法**：页面 `/cleaning` 点开关，或
`curl -X PUT http://127.0.0.1:8900/api/moderation/labels/gate -H 'Content-Type: application/json' -d '{"enabled":true}'`。

---
## 已完成: **内容分级标注（S 类 / 广告 / 涨粉 / 情绪）** (2026-11-19 · Sprint 58 · id `26111901` · PR #244 → `be12f0c`)

把「黄赌毒 / 纯广告 / 涨粉 / 情绪发泄」从入库关键词一刀切升级为**全语料标注层**：
只判定、只标注、有限拦截，**永不自动删正文**。设计文档
`docs/2026-11-19-content-moderation-labels.md`（两轮 agy 审计记录见 §18）。

**已落地**：migration `050_content_moderation_labels.sql`（`content_labels` / `label_runs`，纯 additive）；
`moni/fetch/moderation.py::evaluate_labels()`（80 字窗 + 三要素 + 六类路由）；
`moni/moderation/service.py`（preview / apply / relabel / samples / review / gate）；
CLI `moni label-articles`；API `/api/moderation/labels/*`（独立 router，`/api/moderation` 仍只读）；
`/cleaning` 内嵌分级区块（S 类默认打码 + 单条揭示）；`/api/articles` 角标与 `label_category` 筛选；
入库 / 导入全文 / 编辑正文三处重标；摘要与简报闸门（默认关闭，PR #247 后改由 `app_settings` 配置）。

**实测（生产库副本 4,663 篇非空正文）**：`spam_service=3`（全为 xbot 刷量广告）、
`engagement_bait=2`、`low_value_rant=4`，其余三类为 0；扫描 0.44s；第二次 apply `new_rows=0`。
直接复用 #237 的 `evaluate_article()` 会误杀 42+14 篇，故两入口共享词表但不合并。

**已部署（2026-11-19）**：迁移 `v49 → v50`；`com.moni.web` 重启后 `/health` 200；
4 行测试数据（`a0/a1` 源 `s`、`thin-a/thin-b` 源 `testsrc`）归档进菜篮子
（`dropped_reason='test_source'`）后删除，`articles` 5,327 → 5,323；
生产预演 `scanned=5323 labeled=9`（分布同上）。sprint 留档见
`sprints/sprint-58-content-moderation-labels-2026-11-19/`（含 `verify-`）。

**已执行（2026-11-19 收尾）**：`clean-articles --apply` 全库重写 872 篇、闸门打开，
详见本文档首节。

**不做**：自动删除 / 移菜篮子 / 封作者；图像识别；模型二审（只留设计位）。

---
## 已完成: **xbot_feed 劣质内容治理（主题 ≠ 准入）** (2026-11-19 · Sprint 54 · id `26111703` · PR #237 已合并 `10f3957`)

把「xbot_feed 抓到的主题相关内容」与「该不该进库」**拆成两层**：只读文本规则判劣质 ⇒
**只产候选清单**给 dad 审，**不自动处置**。另把 `bypass` 的两个闸门拆成独立开关（**零行为变化**）。

**改法**（**28 个文件**，**已合并**）：决策纯函数 `moni/fetch/moderation.py`（**纯函数、零 DB 依赖**）；
保护资产只读护栏 `moni/fetch/protected_assets.py`（10 类，**零新表**）；
`bypass` 拆分到新键 **`bypass_tag_or_gate`**（旧 deprecated alias `bypass_tag_filter` 的归一/pop **一行未动**，
`tests/test_fetch_config.py` **一行未改**）；只读预演器 `scripts/xbot_quality_candidates.py`；
脱敏产物生成器 `scripts/xbot_quality_artifacts.py`；离线验证器 `scripts/xbot_quality_validate.py`；
只读端点 `moni/web/routes/moderation.py`（`GET /api/moderation/xbot/candidates`）；
只读 FTS 探针 `scripts/verify_fts_index_criteria.py`。
**本 sprint 不新增任何 migration**（**不抢迁移号**）；合并当时把最新 `origin/main`
（`e5975a3`）merge 进本分支 ⇒ **继承 main 的 `_CURRENT_VERSION` 47**
（含 `046_fact_check_summary.sql` / `047_fact_check_gate.sql`），本分支**未新建、未重编号**任何迁移。
⚠️ **口径**：上面 **47** 是**合并当时**的快照；**当前** repo main 与生产运行版本是**更晚的快照**
（生产 schema 已到 **51**）—— 两者**不要**混成同一个「当前」。

**不做**：任何**生产处置**（隔离 / 屏蔽作者 / 归档 execute / rescue —— 属阶段二）、改生产配置
（`bypass_keyword_filters` 仍 true，本 sprint 的新键 `bypass_tag_or_gate` **从未启用**）、
启黑名单、改词表阈值、前端 UI 入口
（I8 **只是 API**，界面**没有**真实用户入口 ⇒ **不能称「阶段二执行已开发」**）。
⚠️ **后续事件（不是本 sprint 的动作）**：内容闸门由 **#249 / `v1.0.105`** 打开
（`PUT /api/moderation/labels/gate {"enabled":true}`，见**本文档首节**「全库正文清洗落地 + 闸门打开」）。
那是**后续 sprint 的发布动作**，**不冒认**为本 sprint 的交付；本 sprint 自己的
`bypass_tag_or_gate` **至今未启用**。

✅ **schema 落差已处理**（合并当时口径）：合并前本地是 **45**、`origin/main` 已是 **46/47**
（`046_fact_check_summary.sql` / `047_fact_check_gate.sql`）⇒ 本分支**未新建、未重编号**迁移，
已通过 merge 干净继承 main 的 **`_CURRENT_VERSION` = 47** ⇒ **不会带着旧版本号**合并。

**测试**（**2026-11-19 14:28** p7 warden 对**已合并树** head `271a35b` 的**独立实跑**；
该 head 与 squash merge `10f3957` **树等价**（`8d917c4387c5b515e655763c0d809618ea5ec17f`））：
全量 **2277 passed / 2 skipped**（399.59s，一次跑绿）；5 个既有文件**原位**单跑 **61 passed**
（改用相对定位 ⇒ 消除「原位跑假绿」，**绿灯现可直接引用**）；本 sprint 新增 7 个测试文件 **123 passed**。
变异负控**打中**（注入 `moni/db/migrate.py` MUTANT ⇒ 原位单跑红）⇒ 证明原位跑真在测 worktree 代码。
⚠️ 本段数字**均为 2026-11-19 14:28 那次实跑**，**不是**本次文档收尾重跑（文档收尾**本地测试 NOT_RUN**）。

**审计**：p7 warden 多轮 —— 初审 P0=3/P1=6 → 复核 P0=0/P1=1 → 第三轮 P0=0/P1=1 → 第四轮 PASS → 终审 PASS（P0=0/P1=0）
⇒ 4 条 P2 全消项；13:44 又派**最终 diff 复核**（**26 个文件**全纳审，含 14 个 untracked）；
**2026-11-19 14:2x 对已合并树再审一次**（审 **28 个文件**）：`GATE: PASS / P0=0 / P1=0 / P2=3`
（3 条 P2 全是本段这类**叙述过时**，不影响代码、配置、迁移与生产行为）。

**⚠️ 已知限制（随包读）**：① 候选是 **agent 草标，不是 dad 人工真值** ⇒ **没有真值就没有精确率/召回率/误杀率**；
② 上游 feed **零媒体字段** ⇒ **媒体类劣质内容是硬盲区**，本方案不覆盖，也不得声称「已清干净」；
③ basket 恢复**有损**（派生数据 / FTS 索引 / 原 `fetched_at` 不恢复）；④ FTS 探针**只报告不自愈**
（external-content 表无 trigger ⇒ 会漂移，rebuild 需 dad 单独 GO）；⑤ 规则阈值全是**暂定值**，未经标注集标定。

**状态**：**阶段一已完成并已合并**。PR **#237** 已 squash 合入 `main` ——
**squash merge commit `10f39571e33a2a39244950d0a9e313a6abbe0440`**（单亲 `9b07c9f`），
合并时间 **2026-11-19 12:39:54 CST**；PR head `271a35b` 与该 merge commit **树等价**
（`8d917c4387c5b515e655763c0d809618ea5ec17f`）⇒ 合并未丢内容。
⚠️ **squash 后的 ancestry 用 `10f3957` 验证**（`git merge-base --is-ancestor 10f3957 <main>`），
**不要**把 PR head `271a35b` 说成 main 的祖先。

**版本证据**：含本 sprint 代码的**首个正式 tag 是 `v1.0.104`**（tag 对象 `6dd9301`，peeled → `c0363e3`，
由 **#246** 发布，但**已包含** #237）；其后 **`v1.0.105`**（peeled → `26a3502`）与
**`v1.0.106`**（peeled → `b9d8b15`）**同样包含** #237。
⇒ 按事实记：**本 sprint 不是任一 tag 的专属发布**，只是**随**这些 tag 进入正式发布。

产物 `artifacts/xbot-quality-2026-11-18/`（脱敏候选 41 / 标注模板 41 / 不确定案例 35）**已 gitignore，不入库**
⇒ dad 要看请**只读查看**，**填 `annotation-template.json` 的 `dad_verdict`** ⇒ 填完才有真值与指标。

**待 dad GO**（**本 sprint 范围内未做**）：阶段二（自动拦截 / 屏蔽作者 / 归档 execute）、
G2 配置、G3 批量处置 —— 各自需**单独 GO**；另**阶段一已关闭**，但上述 P0 限制
（缺 dad 人工真值 ⇒ 无准确率）**不因阶段一关闭而解除**。


## 未发布: **数据清洗 clean_v1 + 前端清洗页** (2026-11-18 · 分支 `feat/content-cleaning` · PR #238)

**做了什么**：内容进来后的劣质内容清理从「只剥 HTML 标签」扩成一套确定性清洗，
并给出可预演、可执行、可导出报告的界面。

**规则**（`moni/fetch/content.py`，纯函数、幂等）：YAML frontmatter → HTML 实体（循环解到稳定）
→ HTML（仅当块级标签 ≥8 且标记占比 ≥5% 才走块级抽取，否则就地剥标签）→ 伪标签（只认真实 HTML 标签名）
→ 零宽字符 → U+FFFD → 公众号尾巴（行首页脚整行删 / 短行版权声明整行删 / 行尾 CTA 截断）
→ 空白压缩（含 `\u00a0`，Markdown 代码围栏内容原样保留）。

**修掉的真 bug**（全部在生产库上复现过）：

- `extract_text()` 不幂等，二次清洗清空 32 篇纯文本正文；
- 正文夹一行 `<h1>404 Not Found</h1>`，8,110 字正文被抽成 13 字；
- 页脚/CTA 正则误删真句：「国际**商务合作**区」「乘客在车站**扫描二维码**后」「版权**归属**」；
- bestblogs 把整篇正文塞进 `title: "…"`，frontmatter 剥离吃掉 1,333 字；
- 空正文仍会进大模型（异步摘要无护栏）。

**新增**：migration 049（`articles.content_state/chars/reasons/cleaner/cleaned_at`、
`sources.content_contract`、`cleaning_runs`）；`moni/cleaning/service.py`；
`/api/cleaning/*`；前端「数据清洗」页；`moni clean-articles`（默认 dry-run）+ `moni content-audit`；
四条入库路径（抓取 / 手工新建 / 菜篮子 rescue / 手工导入全文）统一清洗。

**生产口径**（库副本，5,335 篇）：正文改写 **880** 篇、只补状态 4,455 篇、
净减 **47,551** 字、会清空长正文而跳过 0 篇、归档测试数据 4 条、二次执行 `changed=0`。
杂质命中：公众号尾巴 494、实体 82、零宽 84、乱码 55、frontmatter 7（另有 1 篇内嵌正文未自动处理）、HTML 残留 8。

**独立 audit 后修复**（2026-11-18，4 条 P1 全修并各有回归）：
只补状态的行不再误清 `summary/ai_summary/brief_*`；五条写路径统一「会清空长正文就拒绝写入」护栏；
「编辑完规则后…」等正文句不再被当署名行删除；`a<b 且 b>c` 不再被当 HTML 标签吃掉。
顺手修复：代码围栏原样保留、`Q:/A:` 不当 YAML、测试数据只按 `testsrc/s` 白名单归档、
`no_body` 只拦摘要/简报不再拦事实核查、前端错误 JSON 与 `states` 缺失防崩、
migration 049 只在 `content_contract IS NULL` 时写入。

**第二轮 audit 后修复**（2026-11-19，无 P0，2 条 P1 全修并各有回归）：
菜篮子 rescue 遇到「清洗后为空」现在返回 400 且不写 `rescued_at`（之前静默写入空正文还报成功）；
Markdown 代码围栏改为整块（含 ` ``` `）保护，二次清洗不再删围栏内 HTML、`normalize` 幂等。
顺手修：正文未变的 `PUT` 不再清空 `summary/ai_summary/brief_*`、`Q:/A:/Note:` 只在全是散文时判为正文、
批量清洗纯补状态行不再重建 layout、`clean_all_html` 增加 `skipped` 计数、
导入全文守卫测试不再被路径白名单短路、migration 049 注释改正。
全量 `2124 passed / 2 skipped`；生产副本 5,330 行三次 apply = `875/0/0`，`emptied=0`，围栏与摘要均未丢。

**审计 P2 并入**（2026-11-19，两条有实际损耗的修进本 PR）：
frontmatter 现在允许 YAML 缩进续行，生产 34 篇以 `---` 开头的正文由「只剥 7 篇」变为「剥 33 篇」
（剩 1 篇是整篇正文塞进 `title:` 的 `frontmatter_embedded`，按设计保留）；
纯空白 diff 不再清 `summary/ai_summary/brief_*`、也不再重建 layout，另记 `whitespace_only` 子计数。
生产副本 5,330 行：`changed=875 / emptied=0 / chars_removed=54,683 / whitespace_only=195`，
二次三次 `changed=0`；摘要保留 `13/84/21`。全量 `2127 passed / 2 skipped`。

增量复审再补两条：`---` 之间全是缩进行（代码块/缩进段落）不再被当 frontmatter 删掉（要求至少一个顶层键）；
`clean-articles` 预演也输出 `whitespace_only`（之前只有执行才写）。

**与 #242 冲突处理**（2026-11-19，rebase 到 `a6a8b92`）：`index.html` 里 `main.css` / `app.js` 的 `?v=`
取本分支更高值（`20261119002` / `20261119003`），并修正 #242 新测试把版本号截成前 8 位的问题
（`\d{8}` → `\d{8,}`，否则同一天连续改两次永远误报）。

**测试**：`pytest tests/ --deselect <2 条环境失败>` → **2108 passed, 2 skipped**。
`test_showcase…` 因当前虚拟环境缺 `PIL` 失败（与 PR 无关）；`test_039…` 需主仓 `.git` 建临时 worktree，
沙箱内失败原因与既有记录一致，audit 单跑 33 passed。

**文档**：`docs/specs/2026-11-18-content-cleaning.md`（方案+落地记录）、
`docs/specs/2026-11-18-content-cleaning-options.md`（甲/乙/丙对比）、
Obsidian `sprint-56-content-cleaning-2026-11-18/` 六件套。

**未做**：自动丢弃空/短正文（合规常态）、LLM 质量打分（与 BV 十维重复）、
跨源去重（约 1%）、全文抓取补齐（V2）、发布方归一（857 篇，另开）。


---

## 已发布: **v1.0.102 / v1.0.103 — 阅读器页签化 + 流式修复 + 静态资源缓存号** (2026-11-19 · PR #240/#241/#242 · tag `v1.0.102` / `v1.0.103` · commit `c4c9357` / `a312971` / `a6a8b92`)

**症状**（生产 + 用户反馈 6 条）：

1. 摘要 / 事实核查内容是纯文本，markdown 不渲染；
2. 摘要 / 核查块挂在正文末尾，要滚到底才看得到；
3. 人话结论 `fact_check_summary` 前端从未渲染（0 处引用）；
4. AI 对话流式输出一直在闪；
5. 放大按钮只是弹窗内放大，不是全屏；
6. 窄屏时对话框盖在正文上。

**改动**：

- 阅读器改「页签 + 内容面板」：`.reader-split` 变两行网格，行 1 = `正文 | 摘要 | 事实核查`。宽屏右侧 AI 常驻、「对话」页签隐藏；窄屏 4 页签整屏 `opacity/translateX` 切换，`.ai-pane` 不再 `position:absolute`。
- `readerRenderMarkdownPlain`（marked + DOMPurify，不加引用 chip）渲染摘要与核查结论；补渲染 `fact_check_summary`；长报告结论直出 + 「展开逐条核查」折叠。
- 流式：settled/tail 增量渲染，流式期关掉 `.ai-structured-list > li` 入场动画；done / failed / cancelled 三态都整段定稿。
- 全屏：`.is-expanded` 改 `position:fixed; inset:0`，Esc 先退全屏再关弹窗。
- 报错人话：`_llmErrorText` 把熔断 / 会话异常 / 超时 / 解析失败映射成人话，原文进 toast「查看原因」。
- 迟到结果守卫：新增 `isCurrentReaderArticle(id)`，`readerRefreshDetail` 与 `loadArticleEvidence` 共用，换篇 / 关阅读器都不再串数据。
- 页签样式：去掉全宽分隔线与下划线（不再和卡片上边框凑双线），选中态实心荔肉白底 + 600 字重，hover 35% 混合底。
- 全局焦点环只挂 `:focus-visible`、颜色 `--accent` → `--muted-fg`：鼠标点击不再留紫框，键盘 Tab 仍有环。
- v1.0.103：`main.css / toast.js / app.js / reader.js` 的 `?v=` 统一 bump——此前没 bump，服务端换了文件但浏览器整版用旧缓存。

**审计**（grok，只读、跨仓指向 worktree）：#240 `APPROVE WITH NITS`（P1 窄屏切不回内容页签、P2 迟到结果覆盖）→ 修复 → 复审通过；#241 `APPROVE WITH NITS`（P2 取消/失败不定稿）→ 修复 → 增量复审 `APPROVE`；#242 `APPROVE WITH NITS`（版本号断言同日漏判）→ 改为按提交比对 + 变异验证。三次 `collect` 均 `introduced: []`。

**测试**：全量 **2063 passed + 2 skipped + 1 failed**；failed 为 `test_worker_lock::test_sigterm_kills_blocking_child_that_ignores_term` 并发 flake，单跑 **28 passed**。前端子集 **116 passed**。竞态实证：拦延迟 A 的证据响应后立刻开 B —— 守卫关闭 `evidence_rows=11`（错，A 的条数）、开启 `=3`（B 的真实条数）。

**发布**：squash `c4c9357`（#240）→ `a312971`（#241；#240 squash 后产生 add/add 冲突，`git rebase --onto origin/main be09aac` 重放 7 个提交，`--force-with-lease` 只推 PR 分支）→ `a6a8b92`（#242）。新 tag `v1.0.102` / `v1.0.103`（未覆盖旧 tag）。备份 `news.db.pre-v1.0.102-20261119-094352`、`news.db.pre-v1.0.103-20261119-100943`。8900 重启两次，最终 PID **24850**（2026-11-19 10:09:44），schema 仍 **48**（本 sprint 无迁移）。

**生产实测**：

- `index.html` 返回 `main.css / toast.js / app.js / reader.js` 四个 `?v=20261119001` 系版本号，带版本号的 `main.css` 返回 200；
- 无缓存浏览器上下文打开阅读器：4 个页签、选中底 `oklch(0.928 0.034 84.6)`、`::after=none`、页签条 `border-bottom: 0px`；
- `/` 与 `/api/articles?limit=5` 均 200。

**已知边界**：

- 真实联网核查端到端没验成：本机 hermes 熔断器 OPEN（`breaker OPEN`），该项记 **failed**，不是 passed；
- 历史长 markdown 核查结论不回填数据，靠前端折叠处理；
- `test_versioned_assets_busted_after_their_last_commit` 在浅克隆 / 非 git checkout 下 skip；
- 上表「生产实测」的采集时点是 `a6a8b92`（schema 48、PID 24850）。收尾期间 `origin/main` 前进到
  `ef806ec`（#238 数据清洗，migration 049），并由另一路部署到 8900（现 `version=v1.0.103 /
  commit=ef806ec / schema 49 / PID 37003`）；#238 尚未打 tag。本 sprint 的页面结论不受影响，
  但推进到该提交后应再点一遍。

## 已发布: **v1.0.101 — 事实核查提示词改人话结论 + 详情页两档展示** (2026-11-18 · PR #236 · tag `v1.0.101` · commit `70bc017`)

**症状**：分享图里的核查行仍是「温度 [0,2]、top_p [0,1]…均与文档一致」；详情页接口
`GET /api/articles/{id}` 的返回 key 里没有任何 `fact_check_*`，20 篇有结论的文章前端一直空白。

**改动**：

- `fact_check.md`：1-3 条最值得核的主张；依据类型四档（文内矛盾 / 公开常识 / 模型记忆 /
  无依据）；无证据只能「无法验证」，不许判「错误」；全文 ≤600 字。
- `fact_check_plus.md`：`conclusion` 只出标签；新增 `thesis`（≤40 字）与
  `evidence.source_date`；`summary` 2-3 句 ≤150 汉字，回答"能不能引用 / 依据多硬 / 注意什么"。
- `jobs.py` + migration 048：落库 `fact_check_thesis`；摘要上限 120→150；【无需核查】档也写 thesis。
- `enrichment.py`：离线档禁【已核实】；勾「深度事实核查」时补齐联网核查（最多等 180 秒）。
- web：`Article` 增加 9 个 fact_check 字段；结论框标「联网核查 · 有出处」/「快速核查 ·
  未联网，模型推断」，显示 `fact_check_how` 与 `thesis`；证据项显示 verdict / claim / 来源日期。
- `share/estimate`：新增 `articles_missing_summary` 与 `articles_missing_web_fact_check` 预检。

**审计**：grok 只读三轮 —— `APPROVE WITH NITS`（5 条 P2）→ 修完 → `APPROVE WITH NITS`
（3 条 P3）→ 修完 → 终审 **`APPROVE`**。合并前确认 `base = main@e5975a3`（最新 main）、
GitHub `mergeable_state=clean`。

**测试**：定向 185 passed；全量 **2039 passed + 2 skipped + 2 环境性 failed**（共 2043 项）。
`test_migration_039` 在沙箱内因
`git worktree add` 无权限失败，沙箱外 **33 passed**；`test_worker_lock` 满载偶发一次，
单跑 **1 passed**。

**发布**：squash `70bc017` · tag `v1.0.101` · 备份
`data/backups/news.db.pre-v1.0.101-20261118-163420` · `python3 -m moni.db.migrate`
→ schema **48** · 重启 8900（PID **54877**）→ `/api/service/info` 报
`v1.0.101 / 70bc017 / schema 48 / 5330 articles / worktree_dirty=false`。

**生产实测**：

- 详情接口：`/api/articles/7ab82a579114a88d` 返回 9 个 fact 字段；`fact_checked_at`
  可被 Pydantic 解析，脏值回退 `null`。
- 证据接口：`/api/fact-check-plus/evidence/7ab82a579114a88d` 返回 `claim/verdict/source_date/
  days_old/timeliness`。
- UI：首页实际返回「联网核查 · 有出处」「快速核查 · 未联网，模型推断」「生成前提示」三段文案。
- 预检：`report_png` + `include_fact_check=true` 返回 `articles_missing_summary=0 /
  articles_missing_web_fact_check=0`。
- 真实联网核查：`POST /api/articles/13525e80d88e7816/fact-check-plus` 两次均 `failed`，
  原因是 hermes `session_replay_gap` → 熔断器打开（`reset in 583s`），不是本 PR 代码路径；
  该项标 **failed**，待熔断恢复后重跑。

**已知边界**：

- 历史行不回填：旧 `fact_check_summary` 仍 ≤120 字，旧 evidence 的 `days_old` 仍是 0
  （展示为 fresh），只有新跑的核查才用新规则。
- 其它分支迁移号冲突：`feat/content-cleaning` 也占用 `048`；PR #225 的
  `046_bv_direction.sql` 与 main 的 `046_fact_check_summary.sql` 撞号。两者都需 rebase 后
  改为 049/050 并同步 `_CURRENT_VERSION`。

---


---

## 已发布: **分享事实核查改人话结论 + 核查器 summary** (2026-11-18 · PR #232/#233 · commit `f1c26da` · 未打 tag)

**症状**：PNG 报告的事实核查行变成「温度 [0,2]、top_p [0,1]、频率惩罚 [-2,2] …均与官方文档一致」。

**根因（实测）**：

- `moni/share/enrichment.py` 把 `fact_check_note` 的 6 条 `query/url/snippet/rationale` 原样拼进正文，
  单篇 7327 字节里约 5.9KB 是核查 JSON（`7ab82a579114a88d`）。
- 该文 `fact_check_conclusion` 只有「【待核实】」5 个字；枚举句来自 rationale，不是结论字段。
- `format-report-png.md` 只定义颜色、`content-structure.md` 要求逐条 1-2 句理由；
  `writing-style-compact.md` 只在 brief 模式加载，普通 PNG 无风格约束。
- `fact_check_evidence.conclusion` 逐条都写总体标签（`moni/llm/jobs.py`），没有逐条判定；
  核查器输出结构也没有一句人话 summary。

**改动**：

- migration 046：`articles.fact_check_summary` + `fact_check_evidence.claim/verdict`（additive）。
- `moni/llm/prompts/fact_check_plus.md`：新增 `summary`（≤60 字人话）与逐条 `claim/verdict`。
- `moni/llm/jobs.py`：解析/落库 summary 与逐条判定；缺 verdict 回退总体标签；90 天降级时 summary 加过期提示。
- `moni/share/enrichment.py`：有 summary 只注入「结论 + 核查要点」，历史行回退到
  `claim/verdict/domain` 紧凑列表；丢掉 query/snippet/url；`include_fact_check=false` 不再注入。
- `format-report-png.md`：一句 ≤60 汉字、禁抄数字/区间/参数名/域名/原文、禁 3 项以上列举；
  明确 override content-structure §3。
- `content-structure.md`：逐条理由只给长文格式，单图格式一句话。
- `writing-style-compact.md`：禁止把核查素材（参数区间、字段名、「逐项一致」）搬进结论。
- `scripts/default_worker.sh`：单图格式也加载风格提示；新增 `MONI_PROMPT_DUMP` 便于审计组装结果。

**审计**：独立只读审计两轮（CHANGES REQUESTED → APPROVE WITH NITS）：已修好示例与规则自相矛盾、
Gate 2 降级后 summary 与逐条判定不同步、brief 脚注不受 flag 控制、异常兜底过宽；
以及英文 verdict 绕过降级。新增 6 条回归用例。剩余 P3（非 JSON 历史 note 回灌）留后续。

材料层 summary 上限 120 字（按标点回退），60 字只约束成品那一行；避免把句子切在词中间。

**测试**：`pytest tests/` → **2022 passed, 2 skipped**；唯一失败 `test_039_upgrades_a_db_built_by_old_code`
是沙箱无法在主仓库 `.git` 建临时 worktree，沙箱外单跑 **1 passed**。

**发布**：squash `b434943`（#232）+ `f1c26da`（#233） · 生产 `git pull --ff-only` ·
`python3 -m moni.db.migrate` → schema **46** · `com.moni.web` 重启（PID 41774）·
`/api/service/info` 报 `commit=f1c26da / schema_version=46 / worktree_dirty=false`。

**生产实测**：

- 注入块：`7ab82a579114a88d` 从 7327 字节（约 5.9KB 核查 JSON）降到 **404 字节**，
  只剩「结论 + 核查要点」，`"snippet"` / `"query"` / URL 均为 0 命中。
- 重跑联网核查：`fact_check_summary` 落库 66 字；逐条 verdict 分开为
  `已核实 / 待核实 / 存疑`（11 条）。
- PNG E2E：任务 `1-fGf5pB9WBuGNBuyuOD8Q` → `data/artifacts/1-fGf5pB9WBuGNBuyuOD8Q-report_png-0.png`，
  事实核查行 **33 字**：「文中引用的事实都查得到，主体是个人管理观点，引用时宜标为观点。」
  无数字区间、无参数名、无分号、无冒号后枚举。

**已知边界（未修，P3）**：`fact_check_conclusion` 若带「≤20 字说明」后缀（如
`【已核实】仅指可查事实部分`），Gate 2 的白名单检查用精确等值比较会跳过。属既有逻辑，待单独处理。

## 已发布: **v1.0.99 — Pin 质量分改 1 位小数** (2026-11-18 · PR #229 · tag `v1.0.99` · commit `a6e8e93`)

实现记录 `docs/plans/2026-11-18_quality-scale-5.md` §6，设计稿 §5 已同步。

- **口径**：`pin_heuristic_v3` 取整 `round(..., 1)`，Pin 列与 tooltip 明细 `toFixed(1)`。
  公式每项都是 0.1 的整数倍（长度 1.4/1.0/0.6/0.2/−1.1、数字结构 0/0.2/0.4/0.6、
  发布方 0.3、时间 0.2、底分 2.5），第二位小数恒为 0，故收掉。
- **不重算**：取值集合不变（`1.4–5.0`），存量行无需 refresh。
- **缓存**：`index.html` 的 `js/app.js?v=` 推进到 `20261118001`（上次 bump 停在 #219）。
- **审计**：独立只读审计 **PASS WITH NITS**（3 P3，均为文案/注释）：验收用例 `3.10/5.00/4.80`、
  css 注释、plan 前文旧口径，已在本 PR 修完。
- **测试**：`test_scoring_heuristic` / `test_scoring_integration` / `test_scoring_store` /
  `test_pins` 共 **51 passed**；前端守卫 4 文件 **84 passed**；CI `pytest · py3.12` pass。
- **发布**：squash merge `a6e8e93` · annotated tag `v1.0.99` · 生产 ff 到 `a6e8e93` ·
  `com.moni.web` 重启（PID 97496）· `/api/service/info` 报
  `version=v1.0.99 / commit=a6e8e93 / schema=45 / worktree_dirty=false`。
- **生产冒烟**：`/static/index.html` 返回 `app.js?v=20261118001`，JS 含 `toFixed(1)`；
  36 篇 Pin 取值 `1.6–5.0` 全为 1 位小数；浏览器实测第 1 页 20 条、列值
  `4.6 / 5.0 / 3.6 / 4.0 / 3.8`、分页「第 1 页 · 共 36 篇」。

## 已发布: **v1.0.98 — 分享 PNG prompt 限额与产物回收** (2026-11-18 · PR #226 · tag `v1.0.98` · commit `3b15660`)

另一条线发布。观察证据：tag `v1.0.98` → `3b15660`；生产运行窗口内 `/api/service/info` 报
`version=v1.0.98 / commit=3b15660 / schema=45 / worktree_dirty=false`（PID 89688）。实现细节见 PR #226。

## 已发布: **v1.0.97 — 质量分 5 分制 + Pin 列头 + 分页修复** (2026-11-18 · PR #227 · tag `v1.0.97` · commit `57af0d3`)

实现记录 `docs/plans/2026-11-18_quality-scale-5.md`，设计稿 §5 已同步。

- **5 分制**：`pin_heuristic_v3`，底分 2.50 + 长度 ≤1.40 + 数字结构 ≤0.60 + 发布方 0.30 +
  发布时间 0.20，四项全满 5.00，仍不设调整分（当时两位小数，v1.0.99 起改 1 位）。
  公式名升版，存量 `v2` 行加载/refresh 时重算。
- **列头**：「质量」→「质量分」，Pin tab 内显示。
- **分页**：修掉 Pin 子标签复用「全部文章」分页状态的问题；现在 Pin 用自己的
  `pinnedPage / pinnedPageSize / pinnedHasMore`，文案「第 N 页 · 共 M 篇」。
- **审计**：独立只读审计 **PASS WITH NITS**（4 P3）。已修 3 项：`enrichment._ensure_quality`
  不再把 0–100 写进 Pin 列（改为只对 Pin 集合内行调 `pin_heuristic_v3`）、删除死导入
  `compute_heuristic_quality`、Pin 子标签隐藏顶部「共 5330 篇」；migration 020 历史注释保留不改。
- **测试**：全量 **1975 passed / 2 skipped**（`test_brief.py` / `test_brief_html.py` 写死生产路径，
  单独跑 19 passed；混跑会串库），2 项既有环境性失败；CI `pytest · py3.12` pass。
- **发布**：squash merge `57af0d3` · annotated tag `v1.0.97` · 生产 `/Users/mt16/dev/moni`
  ff 到 `57af0d3` · `com.moni.web` 重启（PID 85688）· `/api/service/info` 报
  `version=v1.0.97 / commit=57af0d3 / schema=45 / worktree_dirty=false`（本次无迁移）。
- **生产冒烟**：`/static/index.html` 含「质量分」；36 篇 Pin 全部 `pin_heuristic_v3 / ready`，
  分值 **1.60–5.00**、全部两位小数、12 篇满分；`page=1` 20 条、`page=2` 16 条；
  refresh 返回 `updated=36 / unchanged=0 / skipped=0`。

## 已发布: **v1.0.96 — Pin 质量列 + 业务价值 harness** (2026-11-17 · PR #223 · tag `v1.0.96` · commit `5316f05`)

设计稿 `docs/specs/2026-11-16-pin-quality-bv-harness.md`（v4 已拍板 B/C/C/B），实现记录
`docs/plans/2026-11-17_pin-quality-bv-harness.md`。新包 `moni/scoring/`，两件事：

- **Pin 质量列**：`pin_heuristic_v2` 本地算 0–90（底分 45 + 长度 + 数字结构 + 发布方 + 发布时间），
  不调模型；Pin 成功时算，Pin 页发现公式过期对该 id 发一次 `POST /api/articles/pins/quality-refresh`，
  正文写回时同流程重算。前端「质量」列右对齐等宽数字，悬停才出公式 tooltip。
- **通用业务价值**：10 维各 0–10，每维必须引用正文原句（子串校验 ≥12 字），程序按权重加总，
  模型不给总分；检查不过重跑 1 次，仍不过标「待复核」排在 ready 之后。简报卡显示
  「通用 n」+ 最多 3 个维名标签（不带数字），决策窗口过期现场标注不改分。

**数据**：migration **045**（`articles` 加 10 列，只加不回填）；`_CURRENT_VERSION 44 → 45`。
**V1 不做**：方向分/方向研究（V2 附录）、历史分回填、`GET /api/articles` 暴露质量字段。

**测试**：`test_scoring_heuristic` 9 + `test_scoring_bv` 19 + `test_scoring_integration` 14 + `test_scoring_store` 2 全绿；
`test_pins` / `test_brief_html` / `test_brief` 全绿；全量 **1992 passed / 2 skipped**，
2 项环境性失败（Pillow 未安装、沙箱禁止建 git worktree），与本改动无关。
**浏览器实测**：Pin 质量列 `— / 30 / 87`（旧行经 refresh 自动补算），tooltip 默认 `display:none`、
仅 `:hover` 显示；简报页 `通用 72` + 标签 + `窗口已过（评分时 52 天）`、`通用 90 · 待复核`、
未纳入区 `正文过短（不足 200 字）`。

**审计**：独立只读审计 round-1 判 FAIL（4 P1 + 8 P2）→ 全部修正并复跑（预算按墙钟扣减、
CRUD/CLI 写路径补重算、长文 4 窗取证、5 次调用硬上限、引文重编号、legacy 同档、
日期空格、rubric 缓存键、检查员看引文、chip 文案、渲染 hash 校验）；round-2 判 **PASS WITH NITS**
（4 P1 确认修好；再修 `_windows` 2999/3000 字空窗边界 + 引文截 300 字）；round-3 判
**PASS WITH NITS**、无 P0/P1（再补 >4 窗边界用例与打分 prompt 引文上限）。

**发布**：squash merge `5316f05` · annotated tag `v1.0.96` · 生产 `/Users/mt16/dev/moni` 已 ff 到 `5316f05` ·
`com.moni.web` 重启（PID 25885）· `schema_version 44 → 45`（执行前备份
`data/backups/news.db.pre-045-20261117-1500`，integrity ok / 版本 44 / 124,383,232 bytes）·
`/api/service/info` 报 `version=v1.0.96 / commit=5316f05 / schema=45 / worktree_dirty=false`。

**生产冒烟**：`GET /api/articles/pinned` 36 篇均带 `quality` dict（历史行 formula 为空）；
对 `7ab82a579114a88d` 调一次 refresh → `updated`，落库 `quality_score=83 / ready / pin_heuristic_v2`，
再调一次 → `unchanged`；`GET /api/articles` 无 `quality_*`；生产库只读渲染简报正常
（`按通用业务价值排序`、`通用`/`阅读` chip、页脚免责句）；首页 200，`app.js` 含 Pin 质量函数、
`main.css` 含 `quality-col-head`。

**遗留**：方向分/方向研究按 B 方案留到 Sprint 53（复用 `score_general_bv(rubric=...)`）。

---

## 已发布: **v1.0.94 — 孤儿行清理与顶部 admin 浮层** (2026-11-17 · PR #218 / #219 / #220 · tag `v1.0.94`)

两件事：

- **孤儿行清理（migration 044）**：`article_tags` 305 行、`article_keyword_hits` 344 行、`llm_jobs` 3 行
  指向已删除文章。根因是 2026-06-16~18 那批删除时连接没开 `PRAGMA foreign_keys`，
  `ON DELETE CASCADE` 未生效。连接层现在每次建连都开外键，新删文章会级联清子表，不再新增；
  044 只清存量，`NOT IN` 判定，可重复执行。
- **顶部 admin 浮层**：右上角 `PID xxxxx` 胶囊悬停展开九行信息 —— 运行状态 / 端口 / PID / 已运行、
  版本 / 分支 / 工作区脏否 / Schema、数据库文件名 + 大小 / 文章数 / 队列 / 模型、最近抓取。
  新增只读 `GET /api/service/info`，git 子进程 3 秒超时降级为 `null`，整包缓存 30 秒
  （`?force=true` 绕过），只返回数据库**文件名**不暴露绝对路径。

**测试**: `tests/test_service_info.py` 6 passed（字段齐全、不泄露路径、git 缺失降级、
干净工作区为 `false`、缓存与 force、模板结构）；migration 相关 229 passed。

**发布**: `e5b807f` + `a7382f7` + `d69f66d` · tag `v1.0.94` · 8900 已重启（PID 3762，root 200）·
`main.css` / `app.js` 均为 `20261116006`。
生产实测：迁移后三类孤儿全部 0，`article_tags` 8396 → 8091，文章数不变 5262，schema 43 → 44；
浮层 320×356，1440 宽右边界 1268、1280 宽 1093，移开鼠标收起，`worktree_dirty=false`。

## 已发布: **v1.0.95 — xbot_feed 上游 git 临时网络失败的退避重试** (2026-11-16 · Sprint 51 · PR #221 · tag `v1.0.95` · commit `60a2a06`)

上游私有仓库经本机代理访问时**隧道建立后 TLS 握手被重置**（`LibreSSL SSL_connect: SSL_ERROR_SYSCALL`），
`_sync_clone` 对 `git pull --ff-only` 单次尝试无重试 ⇒ 一次抖动整轮失败。
生产最后成功 pull **2026-11-12 10:57:54**，此后 xbot_feed 新文章 0 入库（表内最新 2026-11-11，共 197 篇）。

**改法**：仅 `moni/fetch/sources/xbot_feed.py` —— 临时网络/TLS 失败按 **3 次尝试、2s→4s 退避、总时限 90s** 重试；
每次单次超时取 `min(git_timeout=120, 剩余预算)`（否则 3×120+6=366s 会突破总时限并吃掉 job 级 600s 预算的 61%）。
判据以 **stderr 文本**为准、rc 只判成功：git 的 rc 在网络类失败下不携带语义（TLS/仓库不存在/认证失败都是 128）。
永久错误（非快进 / 认证 / 仓库不存在 / 权限 / host key / 本地冲突）**不重试**，未知错误也**不重试**（保守默认）。

**不做**：改代理节点或代理配置（不在本次授权）、改 UI、改其它源、改作者屏蔽逻辑、改 `git pull --ff-only` 语义、改 max_days 去重。
`git pull --ff-only`、max_days 去重、作者屏蔽三项**逐字未变**（有回归护栏断言）。

**测试**：专项 67 passed（4 个新文件）；负控 **22/22**（NC-1..NC-25）；全量 **1950 passed / 2 skipped / 0 failed**（串行跑）。
**审计**：warden round-1 判 FAIL（P0=1 / P1=2 / P2=3）→ 6 项全修；round-2 判 PASS（P0=0 / P1=0 / P2=4）→ 4 项 P2 全修；
round-3 定向复审判 **PASS**（P0=0 / P1=0 / P2=1，残留项为元护栏子串绕过，不阻塞）。含一条**数据丢失级 P0**：
新增的 clone 清理用 rmtree 删整个 clone_root，`clone_dir` 指向数据目录时会删光 `news.db`；
已改为按「是不是我们造的」判定，且只删 `.git`（6 场景探针全部通过）。
**状态**：**未发布**。代码 + 测试已就绪，PR **#221** 已开（base `0aef205`；实现 commit `befd901`，
head 见 PR 页面 —— head SHA 会随每次 push 前进，故此处只记稳定的 base 与实现 commit）；
**待 dad merge / tag / 部署**；本条**不随已发布的 v1.0.93 / v1.0.94 发布**，随 v1.0.95 发布。
**base**：分支已 merge 到 `origin/main @ 0aef205`（PR #222，仅文档）。

---

## 已发布: **v1.0.93 — 标题列截断与回答结构化** (2026-11-16 · PR #216 · tag `v1.0.93`)

v1.0.92 上线后暴露两个问题：标题按固有宽度撑开、压到「命中标签」列上；文内 AI 的 5 条 bullet 被
hermes 挤成一行、渲染成一整段。本版两处都收掉。

- 标题列改 flex 链路补 `min-width: 0`，ellipsis 真正生效；悬停浮层不变，仍给全文。
- 编号回答在序号前切行生成真实 `<ol>`，标成 `.ai-structured-list`：accent 圆形序号徽标 +
  分条卡片 + 入场动画（并入既有 `prefers-reduced-motion` 合并块）。引用 chip 保留，点击仍高亮原句。

**测试**: `pytest tests/test_reader_ai.py tests/test_pins.py` 43 passed；前端动效守卫测试通过。

**发布**: squash `e104fbb` · tag `v1.0.93` · 8900 已重启（PID 9636，root 200）·
`main.css?v=20261116005`、`reader.js?v=20261116005`。
生产实测：标题宽 304 / 单元格 362，`overflow-x=hidden`，标签 chip 未被覆盖；
结构化 5 条、徽标 17px accent 圆、首条无字面序号；点条目内引用 `scrollTop` 0 → 1071 且高亮正常。

---

## 已发布: **v1.0.92 — 文章列表直达与引文高亮** (2026-11-16 · PR #214 · tag `v1.0.92`)

点文内 AI 的依据时，左侧仍滚到原文，同时把引用的那一句高亮，保持到下次点击。
列表补三处：聊过 AI 的文章在操作列出现对话图标；标题可点，等同「查看」；长标题悬停看全文。
操作列从 100px 加到 132px，图标不会再把「删除」挤出格子。

**测试**: `pytest tests/test_reader_ai.py tests/test_pins.py` 41 passed（新增 Pin 列表标记断言）。

**发布**: squash `cc80b4b` · tag `v1.0.92` · 8900 已重启（PID 78052，root 200）。
生产实测：引文点击 `scrollTop` 0 → 994，`mark.reader-flash` 文本与引文一致、同时只 1 个高亮；
标题为 `BUTTON`，对话图标可见，删除按钮右缘 1307 < 格右缘 1319；悬停浮层显示完整标题。

---

## 已发布: **v1.0.91 — 文章查阅收尾（标签计票 / 胶囊行 / 纯图正文）** (2026-11-16 · PR #212 · tag `v1.0.91`)

PR #207 把文章查阅、筛选与文内 AI 合进 `main` 后，生产验收又发现三处小问题，本版一并收掉：

- `GET /api/articles/facets` 直接数 `article_tags`，没 join `articles`。生产库里有 305 条已删文章残留的标签行，导致 chip 显示 2701、点开只有 2695。改成 `JOIN articles` 后两者一致。
- 提示词胶囊行整体横向滚动，最右的 ⋯ 会被裁掉。改成 `⋯` 与滚动容器并排，固定在行尾。
- 纯图片公众号文章剔图后正文为空，阅读区一大片空白。正文可读文字不足 80 字时提示「这篇原文以图片为主，正文没有可读文字。」

**测试**: `pytest tests/test_reader_ai.py` 15 passed（新增 3 条：孤儿标签计数、胶囊行结构、纯图提示）。

**发布**: squash `a928a23` · tag `v1.0.91` · 8900 已重启（PID 45987，root 200，schema 43）。
生产实测 facets：banking 2695 / ai-fintech 2196 / wechat 1177（修前 2701 / 2413 / 1252）；
纯图文章《派评》出现「这篇原文以图片为主」提示；1280 宽下 ⋯ 右边界 1203，不再被裁。

---

## 分支待清理（2026-11-16 核对：远端仅剩 `main` 与下表第一条）

| 分支 | 位置 | 状态 | 依据 |
|---|---|---|---|
| `feat/bank-peer-source-plan` | 本地 + 远端 | PR 已关闭，可删 | PR #185 CLOSED，内容已被 v1.0.89 覆盖 |
| `feat/articles-chat-highlights` | 仅本地 | 已合并，可删 | PR #214 squash `cc80b4b`，tag `v1.0.92` |
| `feat/articles-title-ellipsis-structure-answer` | 仅本地（worktree `.codex/worktrees/4ffa/moni`） | 已合并，可删；先挪走 worktree | PR #216 squash `e104fbb`，tag `v1.0.93` |
| `feat/contest-finals-2611` | 本地（worktree `.codex/worktrees/2f0d/moni`） | **勿删，未合并** | 无 PR |
| `feat/showcase-demo-2611` | 仅本地 | **勿删，未合并** | 无 PR |
| `feat/xbot-git-retry-v1.0.95` | 远端 + 本地（worktree `dev/moni-worktrees/xbot-git-retry-v1.0.93`） | **勿动，本 sprint 在跑** | Sprint 51 / id 26111606 · PR #221 已开，待 dad merge |

上表已删掉四条早已不存在的分支：`feature/v1.0.88-bank-feed-resilience`、`docs/v1.0.89-relabel`、
`feat/xbot-author-block-v1.0.75`、`feat/articles-reading-ai`。
删分支前按惯例先 `gh pr view <n>` 确认 MERGED / CLOSED，再逐条报给 dad；不批量 `git branch -D`。

---

## 已发布: **文章查阅阅读 / 筛选 / AI 对话** (2026-11-16 · Sprint 47 · PR #207 · 随 v1.0.91 发布)

分支 `feat/articles-reading-ai`。开发起点是 v1.0.87（`d7e6680`），未包含当时还在别的分支上的提交。2026-11-16 已把 `origin/main` 后到的 5 个提交合进来：#206 v1.0.88、#208/#209 v1.0.89、#210/#211 v1.0.90。v1.0.88/89/90 已被占用，本功能尚未打 tag、未重启 8900。

文章查阅：正文按 Markdown 渲染，剔除图片；标签芯片 + 信源多选；全部列表可批量硬删除（上限 200）。详情右侧是一次性 `hermes chat --oneshot` 对话，不新建 profile、不累积 hermes session。2 万字以内整篇送入；更长的文章分段读完再合并。每次提问写入 `llm_jobs.task_type=reader_chat`。关闭详情后任务继续，完成后列表出现「AI 有新结果」。

`moni` profile 走 hermes gateway，`--run-budget` 会被拒，超时由本地看门狗杀进程组。AI 面板按 AICSS 手写：orb 等待态、3 字/12ms 逐字上屏、思考折叠、引文 chip、12px 圆角表格。详情弹窗左右卡片对齐，动作按钮在左栏底部吸条。xbot「屏蔽作者」按钮留在作者芯片上。

Schema：042（作者屏蔽，已在 main）+ 043（reader AI）。`_CURRENT_VERSION = 43`。`migrate()` 会补跑 41 之后缺席的迁移，避免并行版本互相跳过。

## 已发布: **v1.0.90 — 60 天时效窗口覆盖 agent / 媒体条目** (2026-11-16 · PR #210 · tag `v1.0.90`)

**触发**: v1.0.89 上线后在生产 dry-run，当天 feed 40 条里有工行 agent 交回的
2026-01-27 旧闻 —— 60 天窗口此前只作用在 `static: true` 的官网列表。

**改动**

- `item_is_recent()` + 合并后统一过滤：按每个目标的 `max_age_days`
  （默认 60，0 = 关闭）约束 **agent 与媒体**条目；没有日期的条目不参与判断
  （无从比较，保留）；被丢弃条数打到 stderr。
- 静态解析阶段仍先过滤（省详情页请求），合并后二次过滤保证三类条目同一口径。
- 测试：`item_is_recent` 边界（窗口内/外、0 关闭、无日期）、agent 旧稿被丢、
  媒体旧稿被丢。
- 文档：`USER_GUIDE` §11.1、`config/bank_targets.json` 注释改为
  「对全部条目生效」。窗口内一条都没有时 bot 以 2 退出、不写 feed。
- 媒体旧稿测试核对剩下的是新稿 URL，不只断言条数。
- 独立审计 **APPROVE WITH NITS**（无 P1）；断言强度与文档口径已在本 PR 修掉。

**发布**: squash `c9d55b8` · tag `v1.0.90` · schema 仍 42 · 8900 已重启（root 200）。
bot 实跑 `/private/tmp/bank-feed-v1090`：**29 条 / 0 error**，丢掉 11 条窗口外旧稿；
最旧 2026-09-24，早于 cutoff（2026-09-17）的 0 条。工行 agent 12 条里留下 5 条。

---

## 已发布: **v1.0.89 — bank_feed 可靠性（重试 / 部分成功 / 官网确定性解析）** (2026-11-16 · Sprint 48 · PR #208 · tag `v1.0.89`)

**合并**: squash `1cd6b92`（叠在 `fa651fb` / v1.0.88 #206 之上）· **测试**: 合并后全量 1853 passed / 2 skipped
（沙箱内 039 worktree 1 例失败，沙箱外 passed）· **部署**: 8900 已重启，schema 42

**触发**: 11-16 生产点「抓取 bank_feed」显示「失败 · 已抓 0 篇 · 0/0」，
底部 `feed 不完整: 1 个目标失败 (psbc-news)`；库里其实已入库 19 篇。

**根因**: `psbc-news` 的 agent 调用报 `session_replay_gap`（hermes 网关事件流
瞬断，11-13 同一错误落在 `boc-news`）；目标级失败被当成整轮 failed；
`articles_so_far` 没有上报；30 分钟冷却让「再点一次」直接读旧 feed。

**改动**

- **P1 重试**：`call_agent_with_retry()` 只对瞬时错误（`session_replay_gap` /
  `timed out` / 连接断）重试 1 次，非瞬时错误立即抛；每目标独立调用，
  重试天然只覆盖失败目标。
- **P2 部分成功**：`mark_partial()` + `fetch_jobs.status='partial'`；
  `bank_feed.run()` 有数据时把目标缺口写 `warning`（0 数据仍 `error`，fail-closed）；
  `bank_feed` 上报 `progress_callback`（total/done/articles）；批次聚合区分
  `partial_sources`；CLI 打 `WARN` / `PARTIAL`；手动点击（`base.force`）绕开
  `retry_after_min` 冷却。前端单任务终态接受 `partial`，弹窗显示
  「部分完成 · N 篇 · 缺口」，警告条用 `banner-warning`。
- **P3 确定性解析**：`parse_static_list()` / `collect_official_static()` 解析
  boc / abchina / ccb / psbc 四个官网（列表锚点 + URL 日期 + 详情页正文），
  ccb 老站点定向放行 legacy TLS；静态失败自动回退 agent 并在 feed `fallbacks`
  留痕；工行保持 agent（JS 渲染）。
- **审计修复（独立 audit BLOCK → 第 2 轮）**：
  - P1：静态解析失败回退 agent、agent 又交回 0 条时，把该目标写进 `errors`
    （此前只写 `fallbacks`，`_feed_is_fresh()` 看不到缺口，手动点击也不补跑）。
  - 详情页取不到正文（WAF / 改版 / 编码）→ `TargetError` 回退 agent；
    详情页解码改走 `_decode_html`（UTF-8 / GB18030）。
  - 官网列表加时效窗口 `max_age_days`（默认 60 天，0 = 关闭），剔除
    2022/2025 旧稿；窗口内无新闻 = 合法 0 条，不算解析失败。
  - `_report_progress` 不再吞 `TimeoutError`；`base.run()` 让它穿透到 web 端
    timeout 收尾。
  - `get_batch()` 的 partial 只进 `results`（带 `warning`），不再重复塞
    `errors`；批量面板新增 `.fa-row-warn` + `.fa-warn`。
  - 批量写 `cron_runs.success=0`（有 partial 也算没抓全）。
  - `moni.fetch.run()` 把 CLI 的 `force` 落到 `fetcher.force`。
- 第 2 轮独立审计 **APPROVE WITH NITS**；nits（P1 三条分支、`max_age_days=0`、
  批量 partial 的 `cron_runs.success` 都没有测试）已于同一 PR 补齐用例。

**实测**

- bot `--no-agent` 全量：**26 条 / 0 error / 17–24s**（此前 agent 段约 12 分钟）；
  boc 12 / abc 1 / ccb 7 / psbc 3 / 一财 3；正文最短 529 字；最旧条目
  2026-09-24（全部落在 60 天窗口内）。相比加窗口前的 49 条，剔除的是
  ccb 2 条 2022-09-07、abc 11 条 2025 年旧稿。
- 工行 agent 单跑：**6 条 / 136s**；首次调用 `session_replay_gap`，
  重试 1 次后成功（P1 重试在真实链路上命中）。
- 隔离环境 8902 端到端：故意塞一个坏目标 → job **`partial`**，
  `articles_so_far=12`、`inserted=12`、warning 列出 `dead-rss`。
- 浏览器（Playwright）实测弹窗：状态「部分完成」、顶部/底部均「12 篇」、
  警告条 class `fr-error banner-warning`、完成摘要
  `12 篇 · 用时 9s · feed 不完整: 1 个目标失败 (dead-rss)`。
- 批量面板 DOM（隔离 8902 + 注入 partial 结果）：2 行、source 唯一，
  `bank_feed` 行 class `fa-row-details fa-row-warn`，`.fa-warn` 文本
  `feed 不完整: 1 个目标失败 (psbc-news)`，computed color
  `oklch(0.72 0.14 75)`。
- 全量测试 **1773 passed / 2 skipped**；沙箱内
  `test_migration_039_xbot_feed` 因 `git worktree` 无写 `.git` 权限失败，
  沙箱外单跑 **1 passed**。

---

## 补丁: **v1.0.87 — 生图方式说明 + 提炼卡文案** (2026-11-13 · Sprint 46 · PR #204 · tag `v1.0.87`)

**合并**: squash（base = `main@9f4e586` / v1.0.86）· **测试**: 前端红线 92 passed

生图方式四种各配一句说明并随选中项联动（`sharePngApproachOptions` + `pngApproachDesc()`）。内容提炼卡标题改为「提炼内容摘要」/「深度事实核查」，色块标签接在标题同行；灰色说明精简且不含 BestBlogs 文案。审计 nits 同 PR 修掉：摘要来源不支持时给出禁用原因、标签 `nowrap`。

---

## 补丁: **v1.0.86 — 分享页页签样式与文案收口** (2026-11-13 · Sprint 46 · PR #203 · tag `v1.0.86`)

**合并**: squash（base = `main@b595b6b` / v1.0.85）· **测试**: 前端红线 92 passed；全量沿用分支 1742 passed / 2 skipped

去掉「一键分享」的「默认」徽标；`.share-tab` 选中态改为与配置管理 `.subtab` 同款紫底药丸（无下划线、鼠标点击无 focus 框，键盘 focus-visible 保留）。文案 `PDF 日报`→`报告 PDF`、`富媒体文本`→`富文本`（一键卡片 + 自定义分段控件同步）。内容提炼卡片显式 `color: var(--foreground)`（原 `<button>` 默认白字导致浅色主题标题隐形），标题/徽标/说明统一左对齐。

## 已发布: **v1.0.85 — 分享页三页签** (2026-11-13 · Sprint 46 · PR #202 · tag `v1.0.85`)

**合并**: squash（base = `main@adc53b7` / v1.0.84）· **测试**: 前端红线 89 passed；全量沿用分支 1742 passed / 2 skipped

分享页改为三个并列页签：一键分享（默认）/ 自定义分享 / 分享队列。一键分享 7 种格式卡片（含 `report_png`）。自定义分享去掉 320px 内层滚动，Pin 每页 20 篇、全部文章默认 24 篇（可选 12/24/48/96）。「立即处理队列」只保留一个 SVG 三角。队列脉冲 `.queue-statusbar .dot.running` 纳入 `prefers-reduced-motion`。

## 补丁: **v1.0.84 — 改 tags / tag 色 / tooltip / 队列表头** (2026-11-13 · PR #201 · tag `v1.0.84`)

**合并**: `adc53b7`。改 tags 弹窗打开前先加载 tag；`.tag-chip` 用 tag 色做淡底；`--surface-modal` 柔化弹窗底；tooltip 真实节点挂到 `#moni-float`；队列表头 `nowrap`。

## 已发布: **v1.0.78 — Sprint 44 补丁 + 前端缓存修复** (2026-11-13 · Sprint 43–44 · PR #191 · tag `v1.0.78`)

**合并**: `d376079`（squash，base = `main@f0e7321`）· **测试**: 1739 passed / 2 skipped · **服务**: 8900 重启后 200

Sprint 43（agy · unslop 只读复检，37,388 字符报告）确认 dad 点名的两处 P0：
顶栏图标在 light 下不可见（`.theme-toggle` 34px 白卡 vs 邻居 28px ghost；moon `fill="none"` + stroke 1.6
→ 屏显 1.07px；palette 圆点 `r=0.5` → 0.33px）；`/config` L1 在右上（x=875）、L2 在左下（x=120），
点「大模型设置」横跨 1015px。

Sprint 44 落地：顶栏三图标统一 18px / stroke 1.8，palette 圆点 `r=1.2`，moon path 加
`fill: oklch(from var(--foreground) l c h / 0.12)`，`.theme-toggle` 并入 `.btn.btn-ghost.btn-sm`（删白卡与阴影）；
配置页 L1 抽离到标题下单独一行，L2 改 `.subtab-bar` / `.subtab`，两者左边缘同为 x=120。
缓存修复：`serve_root` / `spa_fallback` 返回 `index.html` 时带 `Cache-Control: no-cache`，
`main.css` / `app.js` 的 `?v=` `20261113001` → `20261113002`（此前后端改了但浏览器拿旧 HTML，界面看不到变化）。

生产实测（8900，1440×900 light）：顶栏三按钮 `h=28px` / `background=rgba(0,0,0,0)` / `svg width=18` /
`stroke-width=1.8`；L1 `x=[120,195,279,380,481]`、L2 `x=[120,182]`；`GET /` 返回
`cache-control: no-cache` 且引用 `?v=20261113002`。

## 已发布: **v1.0.77 — Web UI 规范化 Phase 1–4** (2026-11-13 · Sprint 39–42 · PR #186 · tag `v1.0.77`)

**合并**: `2f3e22b`（squash，base = `main@53aee12`，已含 v1.0.76 bank_feed）
· **测试**: 1739 passed / 2 skipped · **服务**: 8900 重启后 200
· **口径**: PC 专用（移动端不做）；`/fetch` Canvas 粒子动效保留

UI 体检（agy · unslop skill，62 条 findings：P0×8/P1×29/P2×25）后分 4 个 sprint 落地。
Sprint 39 只动 CSS 层：新增 `--space-*` / `--radius-*` / `--control-h-*` / `--z-*` / `--chart-*`
等语义 token，组件规则裸 hex 从 29 处降到 0，`.btn-sm` 归到 `4px 10px / h28`，
导航项归到 `6px 14px / h36` 并去掉彩色 glow。
验收：`pytest` 1630 passed / 2 skipped；10 视图亮暗 20 张截图无回归。
**口径**：移动端不做（PC 专用）；`/fetch` 粒子动效保留。
后续：Phase 2 按钮与图标、Phase 3 去重与弹窗、Phase 4 PC 视口与对比度。

Phase 2（Sprint 40）已完成：`.link-btn`/`.btn-link` 合并为同一套度量（4px 8px / 28px / 13px），
行内危险操作降为静默文字态（hover 才出 `--destructive`），`/config` 操作列改 4 个 28px icon-only 按钮
（sliders / tag / pencil / trash，均带 `aria-label`）；界面 emoji 与字符图标 **25 → 0**，全部换内联 SVG
（16px / stroke 1.6 / currentColor）。`/config` 表格 30px 横向溢出用 `.url-cell max-width: 240px` 收口。
验收：pytest 1630 passed / 2 skipped；glyph 扫描 0 命中。

Phase 3（Sprint 41）已完成：pinned 子 tab 下隐藏重复的 Pin 切换列（行内改「取消 Pin」），
`fresh`+`new` 双徽标合并为单个「新」，删掉大模型任务页第二个「刷新」；
文章详情弹窗合并重复的「事实核查」，全弹窗仅 1 个 Primary，操作条改为底部 sticky；
`z-index` 收敛到 `--z-modal(500)` / `--z-toast(1000)`。
验收：DOM 断言 pinned 态 pin 列 0 可见、每行徽标 ≤1、弹窗 Primary=1、actions `position: sticky`、z=500；pytest 1630 passed / 2 skipped。

Phase 4（Sprint 42）已完成：1280/1440/1920 × 10 视图 `scrollWidth === clientWidth` 30/30；
可见文本字号从 23 种收敛到 **7 档**（12/13/14/16/18/24/28px，全部走 `--font-*`）；
全局 `:focus-visible` 2px accent 焦点环；标签 chip 对比度亮/暗最小 11.97:1。
pytest 1630 passed / 2 skipped。移动端按 dad 决定不做。

## 已发布: **v1.0.76 — 银行同业 agent 管道** (2026-11-12 · PR #187 · tag `v1.0.76`)

**合并**: `b19df86`（squash，base = `main@7375e84`）· **审计**: 两轮独立只读审计
（BLOCK → 修复 → APPROVE WITH NITS，nits 已收口）· **CI**: 6b73c17 run 43223991792 success
**生产**: schema 41 已迁移（18 源 / `peer-banks` 19 词 / 绑定 1）· 8900 重启后 200 ·
`bank_feed` 已在抓取源列表，等首次手动抓取。

汇报里宣称的「同业追踪」此前没有数据支撑：近 90 天 `banking` 1223 篇里只有
36 篇（2.9%）提到具体银行名，案例库 424 条 100% 来自 mpaypass + 36kr。
本版加的是一个**以银行为主角**的源，而不是又一批宏观金融稿。

### 做了什么

- **`bank_feed` 源**（`moni/fetch/sources/bank_feed.py`）：只做导入，读
  `data/bank_feed/*.json`。当天 feed 缺失时**就地调 agent 补跑**
  （`scripts/bank_bot/run_bot.py`），失败抛 `RuntimeError` 让 job 记 `failed`。
- **`peer-banks` tag**（同业）+ migration 041（schema 40 → 41）：只放五家行名，
  不放动作词（OR 闸门没法表达 AND；放动作词等于把闸门拆了）。
- **agent 两段式**：媒体 RSS/JSON 用 Python 确定性解析；官网栏目 + 公众号交
  `hermes --profile moni`（= magpie 路由组，与 moni 其他 LLM 调用同一条链路）。
- **11 个默认目标**（`config/bank_targets.json`）：5 个官网新闻栏目 + 5 个媒体
  RSS + 1 个媒体 JSON（第一财经）。加信源改 `fetch_config.targets`，零代码。
- **不新建调度**：`config/sources.yaml` 里 `batch_enabled: false` —— 不进工作日
  9:10 的 `fetch_all_sources`，也不进 web「一键抓取」；只在抓取管理里对
  「银行同业」单点「立即抓取」（或 `moni fetch --source bank_feed`）时跑。

### 8903 实测（隔离 DB，2026-11-12）

| 项 | 结果 |
|---|---|
| `POST /api/fetch/sources/bank_feed/run` | 200 → job done（fetched 4 / inserted 1；幂等复跑 inserted 0）|
| bot 产出 | 61 条 / 0 error；`{icbc:14, boc:12, ccb:12, abc:12, psbc:10, bocom:1}` |
| 入库 | `articles` 63 条，`article_tags` 63 条 `peer-banks`（0 篇零 tag）|
| tag 命中 | `建设银行/中国建设银行`、`工行/农行`、`工商银行/农业银行`（真实关键词） |
| `GET /api/articles?tag=peer-banks` | 63 条 |
| `moni extract cases --source bank_feed --limit 5` | 5 篇 → 6 个 case（subject 如「中国农业银行安徽省分行」） |
| `moni keyword-audit --source bank_feed` | 63/63 存活，0 丢弃 |
| 正文完整性 | 63 篇 0 空；61 篇 ≥100 字；官方 58 篇 174–661 字 |
| 媒体正文 | 导语 <200 字时抓详情页：一财 44/0 字 → 1475/1719 字，媒体 0 条 <100 字 |
| 失败路径 | bot 脚本缺失 → job `failed`（error 带原因）；`moni fetch --source bank_feed` exit 1 |
| `pytest tests/` | 1739 passed / 2 skipped（1 例仅沙箱失败：039 worktree） |

耗时：agent 段逐页抓 5 个官网约 12 分钟（单目标上限 240s，总预算 1200s）。

### 为什么 agent 不用「一次调完 5 个官网」

第一版把 5 个官网塞进一次 `hermes -z`，实测 600s 没跑完 → 5 个官网**全丢**，
只剩媒体 RSS 的 4 条。改成一页一次调用后同样的输入拿到 58 条官网条目（+3 条媒体，共 61）。

## 已发布: **v1.0.88 — X-bot 精选「屏蔽作者」** (2026-11-12 · PR #206 · tag `v1.0.88` · commit `fa651fb`)

在 X-bot 精选（`xbot_feed`）的文章详情弹窗里，按 `@handle` 一键屏蔽作者。
被屏蔽的作者以后不再入库；旧文章**物理删除**（不可恢复，屏蔽前确认框显示
「将删除 N 篇已有文章, 不可恢复」）。名单管理放在 X-bot 抓取配置区，与
`fetch_config` 完全解耦（独立表 `source_author_blocks` + migration 042）。

**dad 的四条决定**（15:04 GO IMPL + 16:02 认可 warden 4 项口径）：
存储用独立表 / 旧文物理删除 / 只对 `xbot_feed` 生效且 `author_blocked`
单独计数 / 名单管理放配置区。

**同步上游 main（2026-11-16）**：合入 upstream 21 个 commit（至 `d7e6680`），
迁移号 041 → **042**（上游已占 041 = bank_feed），`_CURRENT_VERSION` 41 → **42**，
发布版本 v1.0.75 → **v1.0.88**（实时 tags 最新 v1.0.87；v1.0.75 从未发布过）。
上游 v1.0.77 已用主题 token `var(--fg-strong)` 修好同一个深色对比度问题 →
本 sprint 丢弃自带的 `.dark` 覆盖，采用上游体系，P2 断言改写为「断言解算后对比度」。

**UI 复审**（agy，w52:p8）：r1 判 FAIL（1 个 P0 首页首点屏蔽报错 + 1 个 P1
焦点丢失 + 1 个 P2 深色对比度）→ 三项全修 → r2 判 PASS → **r3 判 PASS**
（合入上游后定向复审，P0/P1/P2 各 0，4 项回归无回退；深色 15.75:1 / 浅色 16.54:1 实测）。
详见 `docs/plans/2026-11-12_xbot-author-block.md`。

**warden 审计**：round 1 判 FAIL（P0=0 / P1=1 / P2=3）—— P1 是 node harness
硬编码本地绝对路径导致 **CI 红灯**（本机绿、CI 必挂的假绿）；3 项 P2 是
确认框文案断言强度不足 / 函数名与断言值矛盾 / handle 归一化两处各写一份。
**4 项全部已修**（commit `a69f3e1`），CI run `43799900478` 最终 **success**。
round 2 复审判 **PASS**（P0=0 / P1=0），仅余 1 项 P2 残留：确认框文案断言
的区域含 `//` 注释，注释里的「不可恢复」会满足断言 → 已改为只认
`const msg = ...;` 这一句的**模板字符串**（5 个变异全部咬住，含此前测不出的
「去掉不可恢复措辞」与「handle 写死」）。

**验证**：sprint-38 专项 79 passed；全量 **1823 passed / 2 skipped / 0 failed**
（本地与 CI 条数一致）。**已合并** squash `fa651fb`（PR #206）→ tag **`v1.0.88`**
→ 部署 8900，`_CURRENT_VERSION` 41 → 42（migration 042 已应用到生产库）。

---

## 已发布: **v1.0.74 — 分享产物 worker 修复** (2026-11-12 · PR #183 / #184 · tag `v1.0.74` · commit `7375e84`)

v1.0.42 起 `worker_llm.py` 把 prompt 从 `-z` 后面挪到 argv 末尾，hermes argparse
把 `--usage-file` 当成 `-z` 的值后 exit 2；worker 只记录 stdout，导致
`share_tasks.error` 为空、任务「无原因失败」。修复：argv 用 `{prompt}` 占位符由
`HardenedSubprocess` 原地替换，非 0 退出且 stdout 为空时回填 stderr。
生产库副本重跑 `wnk8S6glnjrqYfpqRUomsg` 已产出 24,661 字节 html_landing（status=done）。

PNG 格式另有一条独立问题：`PdnKZBj_ylX6XIjqy6Zc9A`（info_graphic_png）跑满
600s 被判 timeout，但 agent 在 13:31 已写出 1080×1920 PNG，13:33 才收尾。
`info_graphic_png` / `report_png` 超时改为默认 1200s（`MONI_WORKER_TIMEOUT_PNG` 可覆盖），
其他格式仍用 `MONI_WORKER_TIMEOUT`（默认 600s）。
超时不再直接判失败：`moni/share/timeout_recovery.py` 会在 `infographic/`、
`data/briefings/`、artifacts、hermes profile reports/scratch 里找「本次运行之后」
新出现且通过 PNG（签名+IHDR+IEND）或 HTML 校验的产物，复制成标准输出名并标 done。

## 当前状态: **v1.0.72 — 大模型设置改为网关视角** (2026-11-12 · **SHIPPED**)

**主分支**: main（合并提交 `2cd0138`）· **Tag**: `v1.0.72` · **PR**: #181（已 squash 合并）
· **schema**: 40（无新迁移）· **测试**: 1607 passed / 2 skipped（1 例仅沙箱失败：test_migration_039）
· **服务**: 8900 已重启（PID 51952）

### v1.0.72 部署验证

- `GET /api/llm/gateway` → `mode=profile`、`profile=moni`、`provider=magpie`、
  `default_model=group/tq-s-select`、`base_url=http://127.0.0.1:3425/v1`、
  `reachable=true`、`model_count=58`、`groups=[group/tq-s-select, auto/deepseek-v4.1-flash]`。
- 生产 UI 实测（Playwright @ 8900）：`tabs=[网关, 用量统计]`；四张卡片
  `网关可达 / 58 模型 / 2 路由组 / moni·magpie / group/tq-s-select`；
  `details.open=false`，展开后 headings=`[Provider 映射, Model 映射, 默认模型（explicit 模式）]`。
- 真实调用（`POST /api/llm/providers/seed-provider-ark/test`）：
  `ok=true`、`mode=profile`、`tested=profile`、`provider=magpie`、
  `model=group/tq-s-select`、`duration_ms=13699`。

### v1.0.72 交付要点

v1.0.70 之后模型来源已经交给 magpie，但「大模型设置」页还是 hermes 时代的形态：
Provider 绑定 / Model 选择 两张表 + `active_*_model_id` 下拉，看着能配，实际在默认
（跟随 profile）模式下改什么都不影响推理；provider 连通测试更是**测的不是它写着的那个
provider**（参数被忽略）。本页改成网关视角。

- **`GET /api/llm/gateway`**（只读）：当前模式 + hermes `moni` profile 的 provider /
  默认模型 / 网关地址 + 网关 `/v1/models` 可用模型清单（标出路由组）。网关不可达时仍返回
  200，把原因带给页面。
- **子页重排**：`网关` / `用量统计`。网关页放「模型来源」开关与实际生效模型；
  Provider / Model 绑定收进默认折叠的「高级：explicit 兜底（模型映射）」，并写明
  **只在指定模型模式下生效**。
- **连通测试语义修正**：profile 模式下后端改测 profile 当前模型，响应带 `tested=profile`，
  界面与确认弹窗都写明「实际走 profile 当前模型」。
- **任务列表**：profile 模式下「模型」列显示「跟随 profile」，不再空白。
- **seed 备注更新**：老 provider 行标注「仅 explicit 模式使用」，新增 `magpie` provider 行。

## 当前状态: **v1.0.71 — X-bot scope 展示 + per-topic 门槛可配** (2026-11-12 · **SHIPPED**)

**主分支**: main（合并提交 `e8d63de`）· **Tag**: `v1.0.71` · **PR**: #180（已 squash 合并）
· **schema**: 40（**沿用 main 的 migration 040，本版无新迁移**）· **测试**: 1598 passed / 2 skipped
· **服务**: 8900 已重启（PID 24463，`/health` → `{"status":"ok"}`）

### v1.0.71 交付要点

X-bot 精选（`xbot_feed`）这条信源"它到底抓什么范围、按什么标准筛"此前在 moni 工作台里
**完全不可见** —— 28 个固定推特账号、wechat 关键词正则、上游自己的门槛，全在另一台机器的
私有仓库里。本版把这两件事显式化，并且只让"门槛"这一半可配。

- **范围展示 + 可编辑副本**：`GET /api/config/sources/{id}/scope` 返回 scope（xcircle
  账号名单 / wechat 正则 / 每日查询词）、thresholds、来源标注。`scope_source` 三态：
  `upstream`（上游 feed JSON 顶层导出 scope）> `moni_stored`（moni 侧存着用户编辑过的副本）
  > `moni_fallback`（兜底快照 2026-11-11）。
- **⚠️ scope 是 moni 侧副本，不参与入库闸门**：编辑它**不会改变 X-bot 的搜索范围**，
  也不会让 moni 多收或少收任何条目（上游已经把范围筛过一遍，再按手抄名单过滤 = 静默丢条目）。
  UI 里写明这一条。
- **per-topic 门槛（第二道闸门）**：wechat / xcircle 各自的 `min_followers` / `min_likes`，
  用**类型化输入框**编辑（不是一坨 raw JSON）。默认全 0 = 不设限 → **行为零变化**。
  判定按 topic × 原因码计数（`threshold_dropped` / `threshold_dropped_by` /
  `threshold_missing_by`），结果贯通到抓取结果面板与 job 结果 JSON。
- **缺字段按放行处理并计数**：`author_followers` / `metrics.like` 缺失（含类型不对）
  不丢弃，只计数 —— 门槛是"收紧"闸门，不是"内容匹配"，不能误杀。
- **被丢弃的条目不进 `.xbot_feed_state.json`** → 之后把门槛调松，这些条目还能重新导入。
- **上游自 2026-11-12 起自带 wechat 门槛**（5000 粉 / 5 赞）：moni 的门槛是**串联**的
  第二道，**只能更严，捞不回上游已丢的条目**（实测：wechat 237 条里同时满足两者的只有
  55 条 = 23%）。
- **schema 不变（40）、无新表、不改 migration 039**：门槛与 scope 的默认值放**独立常量**，
  `DEFAULT_FETCH_CONFIG` / 039 SQL 字面量 / 既有护栏测试**零改动**。
  （schema 40 来自 main 的 migration 040 —— `llm_model_mode` 默认 profile，**本版没有新迁移**。）

### ⚠️ 已知边界：`moni db init` / `moni db seed-config` 会覆盖整行 `fetch_config`

`moni/db/seed_config.py` 的 sources upsert 用
`fetch_config = COALESCE(excluded.fetch_config, sources.fetch_config)`，而
`excluded.fetch_config` **恒非 null** → seed **一定覆盖**整行 `fetch_config`，
**包含本版新增的 `thresholds` 与 `scope`**。

- 本版**不加** seed 保护（`seed_config.py` **零改动**，全局 seed 语义不动）。
- **升级请用 `python3 -m moni.db.migrate`**（只跑迁移）—— migrate-only 路径**不碰**
  `sources` 行，dad 配的门槛与 scope 在升级后原样保留。
- ⚠️ **不要用 `moni db init` 升级** —— 它 = migrate + seed，会把你改过的门槛与 scope 抹掉。

### 上线执行记录 (2026-11-12)

- **合并**：PR #180 squash 合并 → main `e8d63de`（父提交 `04c9a42`）；分支
  `feat/xbot-scope-thresholds-v1.0.71` **保留**（未删）。tag `v1.0.71` 打在 `e8d63de` 上
  （lightweight，与 v1.0.69 / v1.0.70 同体例）。
- **CI**：GitHub Actions `pytest · py3.12` 在 PR head `121c625` 上 **success**
  （run 43140327255）。
- **部署**：`launchctl kickstart -k gui/501/com.moni.web` → 8900 PID 由 `37097` 变为
  **`24463`**（launchd `com.moni.web` 已托管）。
- **健康检查**（只打 8900，未跑真实抓取）：
  - `GET /` → **200**；`GET /health` → `{"status":"ok"}`；
  - `GET /api/config/sources/src-xbot-feed/scope` → **200**，`scope_source="moni_fallback"`、
    `upstream_scope_available=false`、28 个 xcircle 账号、wechat 正则正常；
  - **门槛默认全关**：prod 的 `thresholds` = wechat/xcircle 各 `min_followers=0` / `min_likes=0`
    （不设限 = 行为零变化）；
  - **prod 的 xbot `fetch_config` 完好**：8 个原有键都在（`blacklisted_handles` /
    `topic_tags` / `repo_url` / `clone_dir` / `max_days` / `git_timeout` /
    `bypass_keyword_filters` / `skip_existing_urls`），**未被 seed 或迁移改动**；
  - **新代码确实在跑**：8900 吐出的 `index.html` 含 `fetch-config-xbot` 区块、
    `main.css` 含 `.fetch-config-xbot .fetch-config-scope-input` 规则、
    `app.js` 含 `moni_stored` 三态分支；
  - **prod 日志无 traceback**（`/private/tmp/moni-8900-v1.0.52.log`）。
- **静态资源 cache-bust 未动**：`index.html` 里的 `?v=`（`main.css` / `app.js` = `20261013002`）
  **本版没改** —— v1.0.70 同样改了静态文件而没 bump，本版沿用同一口径，未改 `index.html`。
  实测 8900 直接吐的是**新** HTML / CSS / JS（见上），无旧资源残留。
- **回滚**：本版无新表、无新迁移、无 DDL → 回滚只需把 main 退到 `04c9a42`
  并 `launchctl kickstart` 重启 8900；prod 的 `sources` 行未被本版改动，无需数据回滚。

## 历史状态: **v1.0.70 — 跟随 moni profile 的 magpie 路由组** (2026-11-11 · **SHIPPED**)

**主分支**: main（合并提交 `3eb0b98`）· **Tag**: `v1.0.70` · **PR**: #178（已 squash 合并）
· **schema**: 40（migration 040）· **测试**: 1524 passed / 2 skipped
· **服务**: 8900 已迁移并重启（PID 37097，`/health` → `{"status":"ok"}`）

### v1.0.70 交付要点

moni 此前把 `HERMES_PROFILE` 钉在 `coder`（`moni/llm/runner.py:155`），且每次调用都带
`--provider/-m`。模型路由要改就得改代码/DB，和 dad 在 magpie 里按场景切路由组的用法冲突。

- **profile 固定为 `moni`**：`runner.py` / `share/worker_llm.py` / `share/summary_gen.py` 默认值
  从 `coder` / `default` 统一改成 `moni`（`coder` 是 2026-08-24 PR #120 因 ark 429 的临时补丁）。
- **`llm_settings.llm_model_mode`**（默认 `profile`）：`profile` 不传 `--provider/-m`，跟随
  `moni` profile 在 magpie 里选的路由组；`explicit` 保留旧行为（按 `active_*_model_id` 调用）。
- **migration 040**：给存量库补 `llm_model_mode='profile'`（`INSERT OR IGNORE`，不覆盖用户已设值）。
- **HTTP API**：`PUT /api/llm/settings` 接受 `llm_model_mode`（`profile|explicit`）。
- **设置页**：新增「模型来源」下拉（跟随 profile / 指定模型）；profile 模式下方两个模型
  下拉置灰，避免「改了没生效」。
- **入队前置校验**：`/summary`、`/fact-check` 在 profile 模式下不再要求
  `active_*_model_id` 行存在（`articles.py` `_resolve_job_model`），空库也能入队。

**实测**：

- `HERMES_PROFILE=moni hermes -z ...` 与 `hermes -z ... --profile moni` 均返回 200，
  magpie 记账 `agent=hermes` / `req=group/tq-s-select` → `inception/mercury-3`。
- 清掉 `~/.hermes/profiles/moni/config.yaml` 顶层残留 `model.base_url` 后重跑仍落
  `tq-s-select`（`PING_CFG_OK`）。
- 生产 E2E（合并后）：`POST /api/articles/c75ef59726eebbe0/summary` → job
  `166139a92f5d46ceb610cebb16652061`，20s `done`，job 行 `provider=profile` / `model=''`，
  24774 in / 1775 out；magpie 同时段记录 `req=group/tq-s-select` → `inception/mercury-3`，200。

**回滚**：`llm_model_mode=explicit` 即退回逐模型绑定的旧行为。

## 历史状态: **v1.0.69 — BestBlogs 信源取舍配置** (2026-11-06 · **SHIPPED**)

**主分支**: main @ `0dbe7e2` · **Tag**: `v1.0.69` · **PR**: #176（已 squash 合并）
· **schema**: 39（无新迁移）· **测试**: 1518 passed / 2 skipped
· **服务**: 8900 已重启（PID 75502，`/health` → `{"status":"ok"}`）

### v1.0.69 交付要点

dad 对 BestBlogs 的 506 个 feed 做了首次取舍：**删 28、加 5**。此前没有"按 feed 取舍"的入口，
只能用 `max_feeds=375` 从 OPML 头部硬截断，想加号就得改代码或直接改 DB 行。

- **`config/feed_overrides.json`**（按 source 名分块）：`exclude_feeds` 按 feed URL 精确匹配删除；
  `extra_feeds` **插到最前面**，保证在 `max_feeds` 截断前一定跑到（5 个播客原本排 502–506 位，永远轮不到）。
- **`moni/fetch/sources/bestblogs.py`**：`_load_feed_overrides` 对缺文件 / 坏 JSON / 块值非 list /
  url 非字符串一律降级为空（不让可选配置拖垮抓取）；`_apply_feed_overrides` 按 URL 去重与排除，
  **配置了但 OPML 里已不存在的删除项会 warning 点名**，不静默失效。
- 只作用于同名 source 块；`bestblog-x` 走独立 fetch，不读此文件。

**已知边界**：每轮仍受 `max_articles_total=500` 限制（实际只覆盖 ~95 个 feed），
28 条删除项位于合并序号 107–370，**不改变单轮覆盖率**；要让更后面的号跑到需调该上限。

## 历史状态: **v1.0.68 — xbot_feed 回填与迁移 039** (2026-11-06 · **SHIPPED**)

**主分支**: main @ `b1517ad` · **PR**: #175（已 squash 合并）· **schema**: 39（migration 039）

**分支**: `feat/sprint-30-xbot-feed-migration` · **测试**: 1508 passed / 2 skipped

### v1.0.68 交付要点

v1.0.66 加了 `xbot_feed` 源，但**只进了 `seed_config.py` + `config/sources.yaml`** ——
没进任何存量数据库。存量库升级后要么看不到这个源，要么它被全局黑名单**静默误杀**
（`_is_source_bypassed()` 只读 DB，源行缺失时它返回 False；抓取不报错，只是少文章）。
本版用 **migration 039** 把升级从 seed-config 解耦。

| 项 | 决定 | 理由 |
|---|---|---|
| 新源落库 | **migration 039 幂等插入** `src-xbot-feed` + 4 个 tag 绑定 | 升级不再依赖 seed-config |
| 覆盖策略 | 只 INSERT，**不 UPDATE 任何已有行** | 用户改过的字段（含停用状态）一个字都不动 |
| 关键词 | 不碰 `tag_definitions.keywords` | 用户的关键词是用户的数据 |
| **升级命令** | **`python3 -m moni.db.migrate`**（migrate-only） | `moni db init` = migrate + seed，会重置用户改过的字段（plan §4.3.1） |
| 用户删掉的源 | **恢复**（Mttt Q-O-2，2026-11-05） | `db init` 每次都跑 seed()，seed 会加回来；停用请用 `is_active=0` |
| 同名源冲突 | 当成"已满足"，不报错 | 用户用自己的 id 重建了同名源时，裸 INSERT 会让整个迁移失败 |
| 文档 | PR #172 审计回填 + 真实运行证据进 `docs/` | 审计报告原本只在工作区 |

**真实库副本实测**（3 个场景，见 `docs/sprint-evidence/sprint-30/realdb-migrate.md`）：
源原样 → 0 变动；源被删 → 恢复 + 4 条绑定；用户改过 → `is_active=0` /
`display_name` / 间隔 / notes **逐字段保留**，只有取消的那条 tag 勾选被补回
（"绑定只增不删"，与 seed 一致）。第二次跑 **no-op**。

### 已知遗留

- `fetch_config` 的默认值现在有**四处**副本（fetcher / seed / web / migration SQL），
  测试 `test_039_fetch_config_matches_default` 是唯一的护栏
- N-7: `moni/fetch/sources/follow_builders.py:152` 的 `url LIKE '%status/{id}%'` 前缀误命中（同 B2），不在本 sprint
- `moni db init` 仍然是 migrate + seed（行为未改）—— 升级一律用 `python3 -m moni.db.migrate`（plan Q-O-10）

---

## 历史状态: **v1.0.67 — 抓取超时修复（心跳容错 / 分批落盘 / 排队可见）** (2026-11-06 · **SHIPPED**)

**主分支**: main @ `0dda941` · **Tag**: `v1.0.67` · **PR**: #173（已 squash 合并）
· **schema**: 38（无新迁移）· **测试**: 1475 passed / 2 skipped
· **服务**: 8900 已重启（新 PID 70041，`/health` → `{"status":"ok"}`）

### v1.0.67 交付要点

- **心跳容错**：租约心跳线程单次 SQLite 异常不再永久退出（重试 + 停止信号）。
- **批量 pending 队列**：`/api/fetch/all/run` 先建全部 pending 行再起 worker，前端可见「排队中」；批次中断时未执行行落 `failed`，不再永久 `running`。
- **BestBlogs 分批落盘**：每 10 个 feed flush 一次，进程被杀不再丢掉整轮（原 475 篇全在内存）。
- **文案中文化**：`lease heartbeat expired` 等租约术语改为中文可读提示。
- **cron_runs 真实性**：批次进程内异常不再记 `success=1`；SIGTERM 场景仍由 sweep 兜底。

---

## 历史状态: **v1.0.66 — xbot_feed 源（X-bot 精选桥接）** (2026-11-03 · **SHIPPED**)

**主分支**: main @ `e53956c` · **PR**: #172（squash merge）· **schema**: 38（无新迁移）
**测试**: 1456 passed / 2 skipped · **审计**: 9 blocking + 6 should-fix 全修完，nit 不阻塞
（审计回填见 `docs/plans/2026-11-05_sprint-30-xbot-feed-backfill-and-migration-AUDIT.md`）

### v1.0.66 交付要点

新增第 14 个抓取源 **`xbot_feed`**（DB id `src-xbot-feed`，显示名「X-bot 精选」）——
把 X-bot 每天挑好的内容（微信话题 + 推特圈）导入 moni。

| 项 | 决定 | 理由 |
|---|---|---|
| 取数 | 数据目录下 `git clone` / `git pull --ff-only` 私有仓库 `mariusiaowego-commits/xbot-feed`，读 `feed/*.json` | moni **不存任何 key**：私有仓库访问权来自机器已有的 git 凭据 |
| 源名 | `xbot_feed`（不是 `x_wechat`） | 源不限微信（两个 topic 都导），且要跟 `xwatch`（X 关键词监控，自己抓）区分 |
| topic → **候选** tag | `wechat` → `wechat` + `miniprogram`；`xcircle` → `general-tech` + `ai-fintech`（可在 `fetch_config.topic_tags` 改） | 不新建 tag：关键词为空的 tag 会让闸门透传（migration 037 的教训） |
| 关键词闸门 | tag 闸门在 **fetcher 内**执行；`bypass_keyword_filters: true` 只短路全局/源级黑白名单 | 精选内容整体入库，但 tag 仍由关键词决定 |
| 频率 | `fetch_interval_min: 60` | 抓取本身很轻（一次 git pull + 读 JSON） |
| 去重 | tweet id 去重（状态文件 `.xbot_feed_state.json`）+ 与 `bestblog-x`/`xwatch` 的跨源软去重（**精确 id 匹配**） | 沿用 `follow_builders` 的 `normalize_tweet_url` + 全局 EXISTS 检查 |
| 未知 topic | **不入库**，记 warning | 上游加新 topic 时宁可少收也不要错打 tag |

**关键实现细节（v1.0.66 审计 B1 重做）**：tag 不再按 topic **硬打**，而是
「**topic → 候选 tag → 该 tag 当前关键词的真实命中**」：

1. `fetch_config.topic_tags`（默认 `{"wechat": ["wechat","miniprogram"],
   "xcircle": ["general-tech","ai-fintech"]}`）给出每个 topic 的**候选** tag，
   可在 web 端改；未配置的 topic 若有**同名 active tag** 就用它，否则跳过 + warning。
2. fetcher 从 `tag_definitions.keywords` 读该 tag **当前**的关键词，只用
   `moni/fetch/keyword_match.py` 的 `keyword_matches` 匹配标题 + 正文
   （AGENTS.md 铁律 1：关键词匹配只能走这一个引擎）。
3. **只挂命中的 tag**，命中关键词写进 `article_keyword_hits`；
   **零命中 → 该条不入库**（不产生零 tag 的文章，AGENTS.md 铁律 3）。
4. **被关键词挡掉的条目不写进已导入 state** —— 用户在 `/config` 改完关键词，
   下一轮它们还能进来。

`bypass_keyword_filters` 打开时，`moni/fetch/tag_filter.py` 的 bypass 分支会
给每一篇文章合成全部绑定 tag 的命中（`hit_field == "bypass"`）；那批是假的，
`dedupe_insert()` 丢掉它，只透传 fetcher 自己算出来的真实命中。

**内置 handle 黑名单**（可在 `fetch_config.blacklisted_handles` 改）：`jkghzone` /
`sensenxnyz`（代购代付灰产）、`Eason3110188520`（签到打卡接单）、`duizhanggege`
（小程序副业营销号）、`HunchoJ01`（关键词误命中的噪音号）。判据来自 2026-11-03 首日
feed 实测（46 条 wechat + 157 条 xcircle）。

**git 失败必须报错**：clone/pull 失败时记 `error`，绝不静默返回 0 篇 —— 静默 0 篇跟
"今天真的没内容"看起来一模一样，但含义完全相反。

**git 输入校验 + 输出脱敏（v1.0.66 审计 B-6/B-7）**：`repo_url` 只接受
`https://github.com/<owner>/<repo>[.git]`（拒绝以 `-` 开头的 git 选项与非
github 协议）；`clone_dir` 解析后必须落在 moni 数据目录内；git 的 stderr/stdout
进日志前脱敏（URL 里的 `user:token@` → `***`），凭据不落盘。

**首次在 Mac 上跑之前**：确认那台机器的 git 能访问私有仓库 `xbot-feed`
（SSH agent 或 credential helper 里已有凭据）。

---

## 历史状态: **v1.0.65 — 词边界收紧后的召回补偿** (2026-10-25 · **SHIPPED**)

**主分支**: main @ `dcd4178` · **Tag**: `v1.0.65` · **PR**: #168
**schema**: 38（migration 038）· **测试**: 1387 passed / 2 skipped
**审计**: 1 轮独立只读审计，**APPROVE WITH NITS**，无 P0/P1（P2-1/P2-2 已在本 PR 内补掉）

### v1.0.65 交付要点

v1.0.64 的词边界收紧是对的（`MA` 命中 MASAJIN、`ark` 命中 Markdown 确实是误命中），
但**把「152 篇旧规则独有存活」当成了「152 篇误命中」** —— 拆开看等式不成立：

| 关键词 | 篇数 | 判定 |
|---|---|---|
| `App` | 70 | 真误命中（`Application Security` 之类）|
| `CV` | 41 | **真命中被误伤**（CVPR / ECCV 论文标题）|
| `ark` | 11 | 真误命中（词内碎片）|
| `MA` | 7 | 真误命中（MASAJIN 之类）|

- 处置：**给 `ai-fintech` 补 `CVPR` / `ECCV` 两个关键词**（migration 038），不动规则、不删 `CV`。
- 只读重放实测：存活 4210 → **4236（+26，CVPR 17 / ECCV 9）**，
  旧规则独有存活 152 → **126**，**新增噪音 0**。
- 刻意不加（抽样验证）：`Application`（救回的是 "Web Application Security" /
  "Applications are now open for Fellowships"，与 APP=手机应用无关）、
  `NTIRE`（只出现在 "CVPR NTIRE" 里，已被 CVPR 覆盖）、`CVE`（漏洞编号，非计算机视觉）。
- **迁移保序是硬约束**：关键词是用户在 /config 手工维护的**有序**数据。第一版用 `UNION`
  导致 SQLite 按字母重排，已改成 `json_insert(keywords,'$[#]',...)` + `json_each` 查重
  （幂等），`test_038_preserves_the_existing_order` 钉死这条。

### 上线执行记录 (2026-10-25)

| 步骤 | 结果 |
|---|---|
| 审计 | 独立只读审计 `01a170db`：APPROVE WITH NITS；两条指定变异（改回 UNION / 去掉幂等查重）均被测试捕获 |
| 合并 | PR #168 squash 合并 → `dcd4178`；远端分支已删除 |
| Tag | `v1.0.65` → `dcd4178`（与 HEAD 一致，非覆盖）|
| main 全量测试 | **1387 passed / 2 skipped**（基线 1379，新增 8：038 迁移 8 条测试）|
| 8900 重启 | launchd `kickstart -k`（PID 74099 → 22620）；8 个端点全部 200 |
| 生产库 | schema 38，`ai-fintech` 关键词 79 → 81（CVPR/ECCV 追加在末尾，顺序保持）|

### 已知遗留（不在本版范围）

- **存量 126 篇**仍不回补 tag（涉及 basket 语义），需单独决策。
- 生产库先跑迁移、代码后合并（半小时窗口）。回滚安全：`migrate()` 在
  `applied(38) >= _CURRENT_VERSION` 时早退，不会降级；闸门逻辑不依赖 schema 版本。
- `payment` tag 只有 `支付` 一个词、`general-tech` 的 `MA` 已归零 —— 仍是用户数据决策。

---

## 历史状态: **v1.0.64 — 关键词匹配正确性** (2026-10-25 · SHIPPED)

**主分支**: main @ `2ac5f73` · **Tag**: `v1.0.64` · **PR**: #166
**schema**: 37（无迁移）· **测试**: 1379 passed / 2 skipped
**审计**: 4 轮独立审计，最终 **APPROVE**，无 P0/P1 遗留

### v1.0.64 交付要点

修 v1.0.63「已知遗留」里那条短关键词误命中 —— 根因是关键词用 `in` 做纯子串匹配，
两个形态各有一条新规则（`moni/fetch/keyword_match.py`）：

| 关键词形态 | 规则 |
|---|---|
| 纯 ASCII | 两端词边界，容许 `s`/`es` 复数（`AI Agent` 仍命中 "AI Agents"）|
| 纯中文 ≤2 字 | 标题命中，或正文 ≥2 次，或正文本身 ≤200 字符 |
| 其余（≥3 字中文/混合）| 纯子串不变（907 个关键词里 719 个走这条）|

- **四条入口同时改**（只改一处等于没改）：tag 闸门 `tag_filter.py`、黑白名单 `filters.py`、
  xwatch 三条路径共用的新增 `xwatch_gate.py`（`ingest` / `following --ingest` / 批处理）。
- 新增 `moni keyword-audit` 只读重放命令：改匹配语义前先算清影响面，别再靠猜。
- 生产库实测（4702 篇）：受闸门 4664 / 旧规则存活 4362 → 新 4210 / 丢 454 /
  **其中 152 篇是「旧规则独有存活」，不等于 152 篇误命中** —— 里面既有真误命中
  （`ark` / `MA` 的词内碎片），也有被收紧规则连带丢掉的真命中（`CVPR` / `ECCV` / `CVE`
  不再命中 `CV`，`Applications` 不再命中 `App`）。单关键词变化：
  `ark` 20→0、`MA` 24→0、`CV` 59→5、`App` 299→184。
- **刻意不改关键词内容**（不删 `MA`/`ark`、不加词）—— 那是数据决策；
  **不回补存量** —— 下次抓取自然生效。

### 上线执行记录 (2026-10-25)

| 步骤 | 结果 |
|---|---|
| 合并 | PR #166 squash 合并，`2ac5f73`；远端分支已自动删除 |
| Tag | `v1.0.64` → `2ac5f73`（与 HEAD 一致，非覆盖）|
| main 全量测试 | **1379 passed / 2 skipped**（基线 1298，新增 81）|
| 8900 重启 | launchd 托起重启（PID 变更）；`/api/articles`、`/api/config/sources`、`/api/config/tags`、`/api/fetch/sources`、`/api/basket`、`/api/stats/overview`、`/api/stats/cases-dashboard`、`/api/stats/llm` 全部 200 |
| 只读性验证 | `keyword-audit` 重放前后 articles 4702 / basket 36952 / article_tags 7581 / article_keyword_hits 123978 完全一致 |

### 已知遗留（不在本版范围）

- **关键词内容**仍需治理：`payment` tag 只有 `支付` 一个词，命中面太宽；
  `general-tech` 的 `MA` 按新规则已归零，可以考虑删掉。都属用户数据决策。
- **收紧规则会连带丢真命中**：`CV` 失去 `CVPR` / `ECCV` / `CVE` 类标题，`App` 失去
  `Application(s)`。这是词边界规则的已知代价；要找回应补关键词（如 `CVPR`），而不是放宽规则。
- **存量 152 篇**的 tag 归属不回改（涉及 basket 语义），需单独决策。
- `describe_gate()` 目前全仓无直接测试（新加的防御分支未覆盖），非阻塞。

---

## 历史状态: **v1.0.63 — 独立博客源改用现有业务标签** (2026-10-25 · SHIPPED)

**主分支**: main @ `3f7c4e8` · **Tag**: `v1.0.63` · **PR**: #164
**schema**: 37 · **测试**: 1298 passed / 2 skipped
**审计**: 两轮独立审计，均 APPROVE WITH NITS，无 P0/P1 阻塞

### v1.0.63 交付要点

- 删除 v1.0.60 引入的一次性 tag `independent-blog`（无关键词 → 标签闸门形同虚设）。
- 新源改绑现有 10 个业务 tag：`APP` / `ai-fintech` / `banking` / `general-tech` /
  `miniprogram` / `model-providers` / `payment` / `regulation` / `wechat` / `workbuddy`。
- 新增 migration 037：清 `independent-blog` 的 tag_definitions / source_tags /
  article_tags 关联，补齐 10 条 source_tags 绑定，写 `schema_cleanup_log`。
- `seed_config` 补齐 `APP` / `model-providers` / `workbuddy` 三个 tag 定义
  （原先只在 live DB 用 /config 建过，空库 seed 会 FK 失败）；**不覆盖**用户已配的关键词。
- 新增 `scripts/purge_cn_indie_unmatched.py`：一次性清理历史 105 篇，
  命中保留并补打 tag + `article_keyword_hits`，未命中直接删除。
- 测试：全量 **1298 passed / 2 skipped**（含新增 purge 脚本 6 个测试、037 迁移 9 个测试）。

### 上线执行记录 (2026-10-25)

| 步骤 | 结果 |
|---|---|
| 迁移 037 | schema 36 → 37；legacy tag 定义/source_tags/article_tags 全部清零 |
| seed-config | 10 tag / 16 source / 50 source-tag；**用户配置的 keywords 零变化**（逐 tag diff 验证） |
| 存量清理 | 105 篇 → 保留 39 / 删除 66；全部 39 篇带 tag；其他 12 个源计数零变化（4725 → 4659） |
| 8900 重启 | launchd 重启成功；`/api/articles`、`/api/config/sources`、`/api/config/tags`、`/api/fetch/sources`、`/api/stats/cases-dashboard` 均 200 |
| 真实抓取验证 | 抓 114 / 入库 43 / **标签闸门拦下 70**；入库后 82 篇 100% 带 tag（修复前是 105/105 无 tag） |
| 备份 | `/private/tmp/news.db.pre-037-002102`（迁移前完整库） |

> 已知遗留「短关键词误命中」已由 **v1.0.64**（PR #166）修掉。

## 历史状态: **v1.0.61 — 36kr 历史 source id 归一化** (2026-10-24 · **SHIPPED**)

**主分支**: main @ `246301f` · **Tag**: `v1.0.61`
**上一版本**: v1.0.60 (PR #160) — 中文独立博客信源
**热修 PR**: #161 — 修复 live DB 历史 id 漂移导致 `seed-config` 唯一约束失败

### v1.0.61 交付要点

- 新增 migration 036：把旧 id `src-36kr_rss` 归一化为 `src-36kr-rss`，保留 `source_tags`、`source_keyword_filters.id` 与 `article_keyword_hits.filter_id`。
- canonical / legacy 并存时合并子数据，不静默丢弃。
- `_run_sql_file()` 失败后显式 rollback，避免连接污染。
- 全量测试 **1226 passed / 2 skipped**；独立审计两轮，最终 **APPROVE**。
- 生产库已升至 schema 36；36kr 451 篇文章不变；新源 `chinese_independent_blogs` 已上线。

## 历史状态: **v1.0.60 — 中文独立博客信源** (2026-10-24 · **SHIPPED**)

**主分支**: main @ `ff84b27` · **Tag**: `v1.0.60` · **PR**: #160
**新增信源**: `chinese_independent_blogs`「中文独立博客」
**上游**: `github.com/geekplux/chinese-independent-blogs` (MIT)，`feed.opml`

### 可行性实测 (2026-10-24)

| 指标 | 结果 |
|---|---:|
| OPML feeds | 695 |
| 可抓取 feed | 306 |
| 可解析文章 | 13,107 |
| 有正文/摘要 | 290 feeds |
| 中位响应 | 1.15 s |

### 交付要点

- 独立聚合源，不并入 `bestblogs`；复用 OPML + RSS/Atom 解析，独立限流与轮转。
- 默认每轮 80 feeds / 每 feed 3 篇 / 总上限 240 篇，`fetch_interval_min=180`。
- 单 feed 失败不影响整轮；v1.0.62 起仅在扫描满 10 个且网络失败率超过 70% 时提前结束。
- ~~新 tag `independent-blog`（无关键词），避免被现有 `wechat` / `general-tech` 窄门控误杀。~~
  **v1.0.63 已移除**：无关键词 tag 会让闸门 pass-through 且文章全部无 tag，改为复用现有业务 tag。
- 安全边界：仅 http(s) feed、5 MB 响应上限、本地 OPML 限 `config/`、编辑器路径越界拒绝。
- 测试：定向 90 passed（含 follow-builders 99 passed）；全量 **1280 passed / 2 skipped / 0 failed**。
- 真实 smoke（v1.0.62）：12 feeds 扫描 12 个，ok 4 / 网络失败 7 / HTTP 错误 1，抓取 8 篇，未触发 70% 熔断。

### 历史状态: v1.0.53 — 2026-10-24 审计整改批次 + Chrome 沙箱护栏

**主分支**: main @ `8ce4f15` (PR #153 squash-merged)  
**Tag 链**: `v1.0.53` (`8ce4f15`) → `v1.0.52` (`7d56649`) → v1.0.45 → v1.0.44 → ... (均未 force push)  
**注意**: `v1.0.52` 的 tag 打在 #150 的 docs commit (`7d56649`) 上（因 #150 后于 #152 合并，此为当时实际合并状态与真实历史，照实记录）。  
**测试基线**: 本地全量测试通过 (1149 tests: 1147 passed / 2 skipped / 0 failed)；GitHub Actions CI 状态**红**（修复进行中：`feature/v1.0.55-ci-green`，见下文诚实披露）。  
**服务状态**: 生产端口 8900 当前由 macOS launchd 用户代理 `com.moni.web` 托管（KeepAlive 保持运行，PID 会随重启变化；重启使用 `launchctl kickstart -k gui/$(id -u)/com.moni.web`，**`scripts/web.sh status` 暂不认识 launchd 进程** —— 这是本轮新增的部署事实）。

### 本轮交付清单 (v1.0.47 ~ v1.0.53)

| 版本 | PR | squash commit | 一句话 |
|---|---|---|---|
| v1.0.47 | #146 | `66cc665` | 修 `predict_tags` 定时任务 3 处致命缺陷（SQL 查不存在的列 → 进程内崩，**从没跑成功过**） |
| v1.0.48 | #147 | `013647f` | 前端 safeHref URL scheme 白名单（拦 `javascript:` 等）+ Alpine/GSAP 自托管（离线不白屏） |
| v1.0.49 | #148 | `a49b035` | 分享队列 worker 锁改原子获取 + 如实上报启动结果 |
| v1.0.50 | #149 | `0c84875` | 加 GitHub Actions CI + 修掉它首次跑出的 fetch job 测试隔离问题 |
| v1.0.51 | #151 | `8c884b6`(merge) / `b942100` | 删掉 `PUT /api/fetch/sources` 扇出地雷 + 封死测试改生产配置 |
| v1.0.52 | #152 | `fa0efdc`(merge) / `5aa1461` | 测试数据隔离：生产 `data/` 目录全路径隔离 + 会话级护栏 |
| — | #150 | `7d56649` | 归档 2026-09 架构审计包 + 10-23 复核结论 + sprint-23 计划 |
| v1.0.53 | #153 | `8ce4f15` | 沙箱内改走 playwright headless-shell、headed xwatch fail-fast、指数退避 |

### ⚠️ 本轮最重要的三项事实与教训 (必须诚实面对)

1. **CI 一直是红的，而且是在红 CI 下合的 PR**：
   - GitHub Actions 从 #149 首次引入起，**每次 CI run 均为 failure**。#151 / #152 / #150 / #153 全部是在红 CI 状态下合并进 main 的。
   - 最近一次 main 分支 run（ID: `40059182154`）结果为 **7 failed / 1142 passed / 2 skipped**。
   - 7 个失败包含三组根因：
     1. `moni/share/worker.py::launch_worker()` 把 Python 解释器路径硬编码为 `/usr/local/bin/python3`（GitHub runner 虚拟环境中不存在该路径）。
     2. `tests/test_36kr_browser.py` 中的 3 个新测试依赖**本机** Playwright 缓存环境，在无预装缓存的 runner 上失败。
     3. `tests/test_frontend_safe_href_and_vendor.py` 运行时直接 import PIL，但 `pyproject.toml` 的 dev 依赖中遗漏了 Pillow。
   - **修复进行中**：已建立分支 `feature/v1.0.55-ci-green`（v1.0.55），必须彻底解决上述环境依赖，CI 跑绿才算整改完毕。
   - **流程漏洞警示**：GitHub 仓库此前**没有配置 required check（必选状态检查）**，导致 CI 虽然全红但 PR 仍能直接合并，门禁形同虚设。必须配置 branch protection 才能形成真实防御。

2. **Chrome 守护护栏（v1.0.54，`feature/v1.0.54-chrome-guard`）**：
   - **事故起因**：2026-10-24 09:11–09:35 期间，macOS 系统生成了 **25 份** `Google Chrome-*.ips` 崩溃日志（SIGABRT / parent=Python / coalition=com.openai.codex）。moni 在 Codex 沙箱里反复拉起**系统 Google Chrome**，每次被 launchservicesd 拒绝当场崩溃，用户感知为浏览器被反复拉起、疯狂抢占前台焦点并不断弹崩溃窗。
   - **两大致命根因**：
     - (a) 沙箱检测代码读取 `MONI_RUNNING_IN_SANDBOX` 环境变量，但**全仓库没有任何地方设置过它**，属于纯死代码；
     - (b) `resolve_chrome_binary()` 在找不到 headless-shell 时**静默回落到系统 Chrome**。
     - 附带问题：`stop_chrome()` 原使用 `pkill -f` 容易误杀用户打开的其他 Chrome 进程；退避计数只保存在进程内存中，进程崩溃重启后计数重置，导致退避失效。
   - **治理方案**：新增 `moni/fetch/chrome_guard.py` 实现真实沙箱环境检测（检测 `CODEX_SANDBOX=seatbelt` / `CODEX_CI=1` 等信号）、沙箱内绝不 spawn 系统 Chrome、彻底删除静默回落系统 Chrome、杀进程前严格校验 cmdline、加入跨进程单实例锁 + 每小时最多 spawn 3 次硬上限、退避状态落盘持久化、xwatch headed 在沙箱内直接 fail-fast。

3. **Chrome profile 目录归属混乱（v1.0.56，`feature/v1.0.56-chrome-profile-migration`）**：
   - **排查发现**：磁盘上的 `Chrome/Profile 6` 实际上是**用户真实的「hermes」账号 profile**（1.6G，包含 Bookmarks、History 以及 338 个 Cookies）。
   - **污染后果**：moni 的 xwatch 任务此前将 `Profile 6` 直接作为 `--user-data-dir` 启动，导致 Chrome 在其内部又新建了一层 `Profile 6/Default/`（存有 moni 的 274 个 cookie，含 x.com 的 `auth_token`/`ct0`）。导致 `Profile 6/Default/Preferences` 的 `exit_type = Crashed`，用户日常打开 Chrome 经常遭遇「恢复页面」弹窗。
   - 此外，`Chrome/Profile 7` 整个是 moni 专用的 36kr daemon profile（153M）。
   - **治理方案**：
     - 将 moni 的自动化数据彻底搬迁到专属目录 `~/.moni/chrome/{xwatch,daemon}`，与用户个人浏览器数据彻底解耦；
     - 新增 `chrome-profiles.py` 工具脚本，输出「Chrome UI 名字 ↔ 磁盘目录」对照表（解决用户分不清 profile 的痛点）；
     - **目前默认值改动与迁移脚本就绪，真正目录搬迁操作待 dad 拍板后执行。**
   - **v1.0.59 安全结论**：xwatch 搬迁实测后已完整回滚。headless-shell 访问 x.com 会清掉登录 cookie（274 → 5）；恢复后 274 条与 `auth_token`/`ct0` 完整。`--apply` 现默认只搬 daemon，xwatch 需显式 `--include-xwatch` 且禁止沙箱执行；xwatch 拒绝任何 headless-shell。真实 xwatch 搬迁仍待用户在沙箱外以 headed Chrome 验证后再决定。

---

## 历史: **v1.0.47 ~ v1.0.51 — 2026-10-23 审计整改批次** (2026-10-24 · **SHIPPED**)

**来源**: 2026-10-23 架构审计 (`docs/sprint-audit-2026/`) + 5 路独立 re-audit  
**合并顺序**: #146 → #147 → #148 → #149 → #151 → #152 → #150 → #153 (版本号递增, 架构文档归档与沙箱护栏收尾)  
**范围**: 8 个 PR 各自独立收敛; 0 DDL / 0 migration / 0 schema 变更  

| PR | 版本 | 分支 | 修什么 | 用户可见影响 | 最终结论 |
|---|---|---|---|---|---|
| #146 | v1.0.47 | `feature/v1.0.47-predict-tags-modernize` | 打标定时任务 `predict_tags` 的 3 处致命缺陷 (查了不存在的列 → 进程内直接崩, 从没成功跑过) | 打标任务终于能产出标签 | **SHIPPED** (squash @ `66cc665`) |
| #147 | v1.0.48 | `feature/v1.0.48-safe-href-offline-assets` | 抓取内容里的恶意链接 (`javascript:`) 拦掉 + Alpine/GSAP 本地化, 断网也能开页面 | 链接点不动 + 离线不白屏 | **SHIPPED** (squash @ `013647f`) |
| #148 | v1.0.49 | `feature/v1.0.49-worker-atomic-lock` | 分享队列 worker 锁改原子获取 (原来两个 worker 同时起来都以为拿到活儿) | 分享任务不再重复跑 | **SHIPPED** (squash @ `a49b035`) |
| #149 | v1.0.50 | `feature/v1.0.50-ci-workflow` | GitHub Actions 自动测试流水线; 顺带修掉它首次跑就抓出的路径硬编码问题 | 合并后 CI 自动跑测试 | **SHIPPED** (squash @ `0c84875`) |
| #151 | v1.0.51 | `feature/v1.0.51-remove-fetch-sources-fanout` | 删掉 `PUT /api/fetch/sources` —— 它把请求体应用到**所有**源并重写整个 `config/sources.yaml` | 少一个危险接口 | **SHIPPED** (merge @ `8c884b6` / `b942100`) |
| #152 | v1.0.52 | `feature/v1.0.52-test-data-isolation` | 测试数据隔离：生产 `data/` 目录全路径隔离 + 会话级护栏 | 测试不再污染生产数据库与配置 | **SHIPPED** (merge @ `fa0efdc` / `5aa1461`) |
| #150 | — | `docs/2026-10-23-audit-and-sprint-plan` | 审计报告归档 + 文档纠错 (17 处修正) + sprint-23 计划 | 仅文档 | **SHIPPED** (commit @ `7d56649`, tag `v1.0.52`) |
| #153 | v1.0.53 | `feature/v1.0.53-sandbox-headless-shell` | 沙箱内改走 playwright headless-shell、headed xwatch fail-fast、指数退避 | 沙箱内抓取不崩、不抢用户浏览器 | **SHIPPED** (squash @ `8ce4f15`, tag `v1.0.53`) |

---

## 历史: **v1.0.46 — 测试基线可信化** (2026-10-23 · **SHIPPED**)

**主分支**: main @ `0f8c056` (PR #145 squash-merged)
**Tag 链**: v1.0.46 **未打 tag** → v1.0.45 → v1.0.44 → v1.0.42 → ...
**状态**: 已合并到 main; **tag 待打**
**范围**: 只改测试, 0 DDL / 0 migration / 0 产品代码改动

> **v1.0.46 (2026-10-23) 测试基线可信化** — 起因: 2026-10-23 的架构审计发现 `pytest tests/` 长期有 8 个"已知失败"(STATUS v1.0.42~45 都记着), 逐个查下来**全部是跨模块状态污染, 不是产品回归**。建可信基线才有意义让后续 CI 当门禁。
>
> **只改 3 个测试文件** (`tests/test_db_leases.py` / `tests/test_prompts.py` / `tests/test_share_manifest.py`), 3 个根因:
>
> - **`tests/test_share_manifest.py`**: 全局 `monkeypatch.setattr(Path, "write_text", boom)` —— pytest 自己的 `tmp_path` 清理也走 `write_text`, 补丁活过本用例, 泄漏到后续模块 (含 `test_api.py` 的 share 用例)。改成只替换 manifest 队列路径的窄桩。
> - **`tests/test_db_leases.py`**: 模块 import 时 `os.environ.setdefault("MONI_DB_PATH", ...)` 把 `/tmp/moni-test-leases.db` 泄漏给后续所有测试模块 (monkeypatch 只还原自己改过的值)。改成 fixture 内 `monkeypatch.setenv`, 自动还原。
> - **`tests/test_prompts.py`**: 过期 mock —— 它 patch `moni.llm.client.subprocess.run`, 但 v1.0.42 已把子进程执行挪到 `moni/llm/runner.py`, 补丁打空、断言失去意义。改为 patch `moni.llm.runner.HardenedSubprocess.run` 并经 `_build_argv()` 断言 argv。
> - **结果**: `1045 passed / 2 skipped / 0 failed` (修前 `8 failed`)。

---

## 历史: **v1.0.45 — 动画与微交互 (sprint-25)** (2026-10-13 · **SHIPPED**)

**主分支**: main @ `b09e599` (PR #144 squash-merged @ `b09e599`; 源分支 head `74ade9a`)
**Tag 链**: v1.0.45 → v1.0.44 → v1.0.42 → v1.0.41 → v1.0.40 → ...
**状态**: 已合并到 main, **tag v1.0.45 已打**
**范围**: 纯前端 (0 DDL / 0 migration / 0 后端 Python 改动); **本 sprint 未引入 WebGPU 运行时**
**pyproject.toml**: 不改 — 沿 moni 惯例 (自 v1.0.0 起固定 `0.1.0`, 50+ tag 均未动过)
**审查**: warden 四轮复审 (定标 + R1/R2/R3 + 最终 GATE) = 可合并, P0=0; moni-agy 只读审查 = 无阻塞项

> **v1.0.45 (2026-10-13) 动画与微交互 (sprint-25)** — 来源: PLAN §2.1 (dad 拍板), 做 A1-a + B 组 P0/P1 + 删死资产。15 文件, +1810 / −13。
>
> - **A1-a 抓取弹窗粒子场封顶 200 (AC-1)**: `app.js` 新增 `const MAX_PARTICLES = 200`, 在 `_spawnBurst` **出生点**检查并淘汰最旧粒子 —— burst 每 200ms 触发、每粒子活 70~110 帧, 原先数组随弹窗打开时长无上界增长。取 200 而非 180: 改前实测活粒子在 89~173 波动, 180 会削掉观测峰值 (属观感改变), 200 高于所有观测峰值、正常使用下不可见。
> - **B 组微交互 P0/P1 共 15 条**: 2a 无障碍 `:focus-visible` 焦点环 (B01/B04, **reduce 下刻意保留** —— 焦点环是功能不是装饰); 2b 性能卫生 删 `.btn` 常驻 `will-change` (B03) + 7 条 hover 抬升包进 `@media (hover: hover)` (B19, 触屏不再粘滞); 2c 主题一致 新增 `--row-hover` token 替换 3 条硬编码 hex (B07); 2d 反馈对称 抓取按钮 `min-width` / `tabular-nums` / toast 与 modal 补退场 (B12/B14/B16/B17); 2e 结构转场 批量栏进入动画 (B09) + 11 个 `x-show` section 走**原生 View Transitions API** (B10, 零依赖零字节, 不支持时静默退化为瞬时切换, **reduce 判定放在 JS** —— 浏览器先做 DOM 快照再询问 CSS, 媒体查询拦不住那份开销)。
> - **reduce-motion 覆盖率 15/15 → 16/16 = 100%**: 新增动画全部并入 sprint-24 那个末尾汇总块, 未新开块。
> - **删 4 张未引用 pin PNG, 净减 2.83 MiB**: 是 WebGPU 运行时 gzip 体积 (≈690 KB) 的 **4.2 倍**。判定经过一次自我纠正 —— 第一轮用 `pin_[abcd]` 粗搜得「28 处引用」差点误判, 逐条看全是**子串误报** (`pin_anim.js` 文件名 / `pin_count` 字段名); 改用精确搜后 0 处运行时引用。**未删 `moni_v2.png` (407 KB)** —— 它被 `static/demo.html` / `demo-v2.html` 引用, 删了 demo 页会裂图。
> - **门禁结果 (warden 定的口径)**: **A** 每帧工作量 before p95 median 2.7ms / after 2.7ms, **比值 1.000** (门限 ≤1.30) PASS (3 次 × 20 秒取中位数, dpr=1, `--disable-gpu`); **B** 出生点封顶 + cap probe burst storm `peakLiveParticles=200 exceededBy=0 → CAP HOLDS` PASS; **C-1** `longFrameRatioPct` 差值 **−0.125pp** (门限 ≤+5.0pp) PASS; **C-2** `maxFrameMs` 50→50 比值 1.000 / `p95FrameMs` 33.4→33.4 比值 1.000 均 PASS。
> - **⚠️ C-3 解释性声明 (必读, 防误判)**: `longFrameRatioPct ≈ 50%` 是 **headless Chrome + `--disable-gpu` 的 vsync 调度产物, 不是应用卡顿率** —— 帧时间恒为 16.7ms 的整数倍 (33.4=2×, 50.0=3×), 而 `workPerFrameMs.mean` 仅 2.30ms (帧预算 16.7ms 的 13.8%)。**不要据此去减少每粒子 arc 调用次数或上 OffscreenCanvas, 那是无效优化。** 同理, 早期用 10 秒窗口量到的 avgFps 60 / avgLive 75 也是失真的 —— 粒子数是时间的函数, 20 秒窗口才暴露真实稳态 (avgFps 45 / avgLive 118)。
> - **⚠️ 作废声明**: 本 PR 首个 commit `f3d8ca2` 的 message 里写了「超预算帧占比 (限 ≤0.5%)」与「成因是粒子数与帧率互相追; 要真正压到 0.5% 需要减少每粒子 arc 调用次数 / OffscreenCanvas」。**该绝对阈值口径已作废** (改为 C-1/C-2 相对口径), **该归因不成立** (见上 C-3)。以 squash commit `b09e599` 为准。
> - **验证**: 真机 (headless Chrome + CDP, `s25_bgroup_verify.mjs`) = **ALL B-GROUP CHECKS PASS** —— navigate 正常态 `startViewTransition` 调用 1 次, reduce 态调用 **0 次** (JS 门控生效)。pytest 相关三套 **60 passed**; 全量 **8 failed / 1037 passed / 2 skipped**, D-13 失败集集合级对账 baseline 8 / head 8, sha256 `560a54c1356092322d83279f5033e400` 两侧逐字节相同, NEW NONE / DISAPPEARED NONE。变异探针 14 条 / 13 bite。
> - **本 sprint 未做**: WebGPU 运行时 (A1-b) 与灯塔光束 hero (A2) —— 按 dad 拍板留待后续, 需另行拍板。

---

## 历史: **v1.0.44 — 前端渲染稳健性 + Pin 交互一致性 (sprint-24)** (2026-10-10 · **SHIPPED**)

**主分支**: main @ `33a529d` (PR #143 squash-merged @ `33a529d`; 源分支 head `72b19cb`)
**Tag 链**: v1.0.44 → v1.0.42 → v1.0.41 → v1.0.40 → ...
**状态**: 已合并到 main, **tag v1.0.44 已打**
**范围**: 纯前端与静态契约 (0 DDL / 0 migration / 0 后端改动)
**pyproject.toml**: 不改 — moni 自 v1.0.0 起该文件固定 `version = "0.1.0"`, 50+ 个 tag 均未改过, 发版由 Git Tag + STATUS.md + 前端 `?v=` 查询串驱动
**审查**: moni-agy 只读审查 = MERGE (无阻塞项); warden 三轮 delta 审查 P0=0 / P1=0

> **v1.0.44 (2026-10-10) 前端渲染稳健性与 Pin 交互一致性 (sprint-24)** — 来源 `tqob/05-Coding/project-moni/research/webgpu-ui-design-research-2026-10-10.md` §6 附带发现 D/E/F 三项 (dad 点名):
> - **GSAP 异步时序与降级 (AC-1/2)**: `index.html` GSAP 脚本加 `defer` 与 `preload` 提示 (位置保持在所有消费脚本之前, 未混 `async`); `app.js` 彻底消灭裸 `gsap.` 调用, 统一通过 `window.gsap` 访问, 并在 CDN 缺失/弱网时直落视觉终态 (`transform` / `opacity` / `textContent`), 根除慢网打开抓取进度 modal 时的 ReferenceError 崩溃隐患 (10 处动画调用点)。
> - **自托管 Inter 变量字体 (AC-3)**: 移除 Google Fonts 3 个外部阻塞外链 (2 条 preconnect + 1 条 fonts.googleapis.com), 引入本地 `inter-latin-400.woff2` (48KB) 与 `inter-latin-ext-400.woff2` (85KB) + SIL OFL `inter-LICENSE.txt`; 解码确认两文件均含 `fvar` / `gvar` / `avar` / `STAT` / `HVAR`, 为真实可变字体 (wght 100–900), `@font-face` 声明 `font-weight: 400 700` 覆盖 400/500/600/700 全字重, 无伪加粗; 启用 `font-display: swap` 消除断网及外网不通时的首屏字体阻塞。CJK 不在子集内, 回落 system-ui 的既有行为保持不变。
> - **prefers-reduced-motion 覆盖率 100% (AC-4/5)**: `main.css` 原先分散的 3 个媒体查询收束为文件末尾统一块, 覆盖全站 **15/15** 条 `animation` 规则 (缺口 8 → 0, 47% → 100%), 对带延时规则显式归零 (`animation-delay: 0ms` / `--d: 0ms`), 避开 `* { animation: none !important }` 一刀切, 保留必要的 `.toast` 语义过渡; `pin_anim.js` 增加 `prefersReducedMotion()` 感知, 减弱动效下跳过 GSAP scale pop。
> - **Pin 闪色时钟统一 700ms (AC-6, P1-2)**: `pin_anim.js` 单一常量 `FLASH_MS = 700` (添加 class 时立即启动 setTimeout, 移除 `onComplete` 嵌套二次计时器); `main.css` `.pin-btn--just-pinned` / `.pin-btn--just-unpinned` 动画时长对齐 `0.7s`; **正常 / 无 GSAP / 减弱动效三条路径闪色反馈时长完全一致 (真浏览器实测中位数 716 / 717 / 716 ms)**; `classList.add(flashClass)` 移至 `window.gsap` 检查之前, 无 GSAP 时 CSS 颜色闪烁依然触发。
> - **⚠️ Alpine CDN 离线边界 (L-2 必记项)**: 断网或 CDN 离线时, 首屏外层骨架及顶栏导航可在 **105ms** 内可读渲染 (远优于 3000ms 预算), 但 **Alpine.js 3.14.1 仍依赖 `cdn.jsdelivr.net`, 断网时 Alpine 未能加载, 导致正文区域 39 个带 `x-cloak` 属性的节点保持初始隐藏, 正文区呈现大面积空白**。**此属本 sprint 明确划定的未承诺范围 (非回归缺陷)** — 本 sprint 只承诺 GSAP 与字体两个外链不阻塞, 不承诺 Alpine 离线可用; 后续若需完全离线可用需单独立项自托管 Alpine。**不要把断网时的空白正文当成本版本的回归来查。**
> - **测试基线**: 新增 `tests/test_frontend_render_hygiene.py` **35/35 通过**; SPA 三件套 (`test_spa_fallback.py` + `test_llm_usage_counters.py` + 新测试) **81/81 通过**; share 回归 **38/38 通过**; 全量 **8 failed / 1012 passed / 2 skipped**, 失败集与 baseline **逐条 diff 完全相同** (既有 flaky: test_api share / test_fetch_integration / test_prompts / test_share_manifest, 与本 diff 无关)。变异探针 13 条 (MP-1~MP-15) 全部证明断言真咬, 复原后 sha256 与变异前逐字相同。
> - **运行时证据** (留在本地, 不入库, 见 `docs/sprint-24-evidence/`): 字体字重实测 (400/500/600/700 advance width 四个互异且严格递增 = wght 轴生效)、CJK 回落 PingFang SC、断网首屏 105ms + 截图、pin 闪色三路径计时。

---

## 历史: **v1.0.42 — sprint-22 双 PR 双 ship** (2026-09-10 · **SHIPPED**)

**主分支**: main @ `cbd0b6a` (PR #141 squash @ `b02d53c` + PR #142 squash @ `cbd0b6a`)
**Tag 链**: v1.0.42 → v1.0.41 → v1.0.40 → ...
**Prod**: 8900 pid 93202 (新代码已生效; schema 已迁移到 v35)
**DB**: schema_version = 35 (migrations 034_leases + 035_llm_runner_config 全部应用; integrity_check=ok)

> **v1.0.42 (2026-09-10) 统一租约运行时 + LLM 调用层完全体 (sprint-22)** — 来源 `docs/sprint-audit-2026/` 21 工单之 P0-3 / P0-4 / P1-5 / P1-6 / P1-7(合并执行)。M3(本次)+ DeepSeek(parallel)双 agent 同步 ship,叶子先行:
> - **PR #141 (叶子: web 边界防御, 0 migration)** — `fix(web): v1.0.42 SPA 路径穿越遏制 + 源凭据出栈脱敏 + modal 转义`: P0-2 `resolve_static_path()` 替掉 `Path` `/` 拼接吞 base bug (实测修前可读 `.env` / `hosts`), P1-10 `redact.py` 出栈脱敏 token, P2-15 modal innerHTML 转义。PR #141 squash @ `b02d53c`.
> - **PR #142 (核心: DB 租约 + LLM runner, 升库到 v35)** — `feat(core): v1.0.42 统一租约运行时 + LLM 调用层完全体`:
>   - **S2-1 + P1-7 统一租约**: 新建 `moni/db/leases.py` (acquire/heartbeat/finish/sweep_expired 四原语, fence 单调递增防迟到写者覆盖, SQLite 端 strftime 跨进程钟差免疫, 表白名单防注入) + `moni/db/migrations/034_leases.sql` (fetch_jobs + llm_jobs 加 lease_owner / lease_heartbeat_at / fence 三列, 并发 SQL UPDATE 一次事务, F6 同业案例库表透明保留) + `moni/fetch/jobs.py` mark_* 全部 fence 化, `moni/llm/jobs.py` _run_job 接受 handle + worker_loop 加 sweep_expired, `moni/web/routes/fetch.py` 启动 fence-bump + 30s daemon heartbeat, 删 `STALE_THRESHOLD_SECONDS` / `get_stale_threshold()` 整套 v1.0.35.17 补丁(墙钟判死 → 心跳判死) + `tests/test_jobs_stale_threshold.py` 删.
>   - **P1-5 + P1-6 + P0-4 LLM 调用层完全体**: 新建 `moni/llm/runner.py` (`HardenedSubprocess` 进程组 `killpg` SIGTERM→SIGKILL, 200KB argv 预检 `PromptTooLargeError` 而非 OSError E2BIG, stdin 协议可插拔; `CircuitBreaker` 内存态 CLOSED/OPEN/HALF_OPEN, 每 provider 独立计数; `FailoverRunner` 链式 `provider→备→失败` fallback) + `moni/db/migrations/035_llm_runner_config.sql` (4 个 llm_settings key: `fallback_chain` / `breaker_failure_threshold=3` / `breaker_reset_seconds=60` / `use_stdin_transport=1`) + `moni/llm/client.py` 收敛到 `runner.call(CallRequest)`, `estimate_tokens` 内联到 runner 避免循环 import, `ChatClient` 保留 DeprecationWarning(向后兼容) + `moni/share/worker_llm.py` `run_hermes` 走 `HardenedSubprocess`(`_build_argv` 内联 prompt 后剥掉, runner 自 append).
>   - **测试净增**: `tests/test_db_leases.py` 17 用例 + `tests/test_llm_runner.py` 17 用例 (含真子进程复现 — 进程组击杀零孤儿) + bump 既有 `test_migration_030_ghost_cleanup` / `test_llm_usage_counters` / `test_fetch_jobs` schema_version 33→35.
>   - **回归**: 9 套测试套件 171/171 全部通过 (`tests/test_source_url_redaction.py` + `test_spa_fallback.py` + `test_db_leases.py` + `test_llm_runner.py` + `test_llm_usage_counters.py` + `test_migration_030_ghost_cleanup.py` + `test_fetch_jobs.py` + `test_worker_llm_failure.py`).
> - **踩坑**: (1) Migration 033→34→35 bump 必须同时更新 `tests/test_migration_030_ghost_cleanup.py` L114 / L248 + `tests/test_llm_usage_counters.py` L123 hard-coded `_CURRENT_VERSION == 33`; (2) `_run_fetch_job` 不能在 daemon thread 重新 `acquire`,应读 INSERT 时 mint 的 `lease_owner`/`fence` 然后 `finish` 一次性 fence-bump; (3) worker_llm `_invoke` 测试桩的 `SubprocessResult.input_tokens==0` 不能被 usage_file 重读覆盖,加 guard; (4) `gh pr merge` GraphQL EOF 时改用 REST `PUT /repos/.../pulls/.../merge`; (5) `worktree remove` cwd 在已删目录时报 "Unable to read current working directory" 但 worktree 实际已删; (6) SSL_ERROR_SYSCALL 偶发时 git push / curl 重试 30-45s 后通.

**架构决策**: 9 月 9 日 dad 拍板合并执行 3 个核心工单 (P0-3 + P1-7 + P0-4 + P1-5 + P1-6), 共 1.5h 落地 PR-A + PR-B 两路并行 (M3 主跑, DeepSeek 副跑 web 防御 PR #141). PR-A 先 ship (24h 观察) 与 PR-B 同时 ship 的方案放弃, 改双 PR 并发同步 ship, 已由 dad 9-10 14:30 拍板. **教训**: 熔断器阈值默认 3/60s 是保守数字, 9-13 production 观察期后若仍有大量 false-positive, 应降为 5/120s.

> **v1.0.42 (2026-09-10) 安全原语 (sprint-22)** — 来源 `docs/sprint-audit-2026/` 21 工单之 P0-2 / P1-10 / P2-15:
> - **P0-2 SPA 路径穿越遏制**: `moni/web/server.py` 新增 `resolve_static_path()`，替掉 `STATIC_DIR / full_path`（`Path` 的 `/` 在右操作数为绝对路径时会**丢弃** base → 任意文件读）。拒绝 NUL/反斜杠/`..`/绝对输入 + 软链越界，非法与不存在统一返 None。
>   - **实测**: 修复前 `curl --path-as-is //etc/hosts` = 200/213B **真文件**，还有 `//Users/mt16/dev/moni/.env` = 200/494B **真 .env (含微信 token)**；修复后两者均 200/216024B SPA index.html。正例 `/js/app.js` 197311B / `/css/main.css` 123695B 不受影响。
> - **P1-10 源凭据出栈脱敏**: 新增 `moni/web/redact.py`; `GET /api/config/sources` 从原样返回 `…?token=eyJ…` 改为 `…?token=…`。**仅出栈** — 库内 URL 一字未动（它同时是抓取输入，打码会让聚合服务 401）；PUT 收到打码值按"未修改"处理。参数名集合与 showcase 生成器 `_TOKEN_QS` 对齐。
> - **P2-15 modal 转义**: `modal.js` confirm() 的 title / 按钮标签补齐 `_escapeHtml`（原只有 message 转义；前/后 node 功能验证 HEAD=FAIL / 改后=PASS×7）。
> - **测试**: 新增 `tests/test_spa_fallback.py` 31 passed + `tests/test_source_url_redaction.py` 34 passed。全量 **943 passed / 2 skipped / 11 failed**（`MONI_DB_PATH=/tmp/moni-test-ds.db`）；干净迁移库 **947 / 7**。**这 11(或 7) 个 failure 与本 diff 无关**：`git stash` 掉本 PR 三文件后同批测试照样 fail，且同 diff 换库状态 failure 数从 7 → 11，多出的 4 个全是 `UNIQUE constraint failed: share_tasks.id` ⇒ pre-existing 测试隔离缺口（P1-D），顺延下一 sprint。
> - **踩坑**: `MONI_DB_PATH` 指向 prod 副本会让依赖空库假设的测试互相污染（干净迁移库更稳）；声明 "pre-existing 失败" 必须给**回退法证据**，不能空口。

---

## 历史: **v1.0.41 — follow_builders 新源接入 (sprint-21)** (2026-09-02 SHIPPED)

**主分支**: main @ `023c1e3` (PR #140 squash-merged, tag v1.0.41)
**Tag 链**: v1.0.41 → v1.0.40 → v1.0.39 → v1.0.37.7 → ...
**PR 链路**: #140 (follow_builders 新源 + test_global_filters 隔离修复)
**Prod**: 8900 pid 6346 (新代码已生效; follow_builders 仍默关 is_active=0)

> **v1.0.41 (2026-09-02) follow_builders 新源接入 (sprint-21)** — 试验源路径:
> - **新源 follow_builders**: GET `feed-x.json` → 扁平化 tweets → URL 归一化 (x.com/twitter.com/mobile.twitter.com + 剥 tracking params) → 跨源软去重 (全局 EXISTS) → bypass keyword/tag filter → 显式合成 ai-fintech tag
> - **试验源策略**: `sources.yaml enabled: false` + DB `is_active=0`，用户 /config 手动开
> - **三处注册**: `config/sources.yaml` + `seed_config.py` + `sources/__init__.py`
> - **测试**: `tests/test_follow_builders_fetch.py` 9 个 test + `tests/fixtures/feed_x_sample.json`; `tests/test_global_filters.py` 隔离修复 (tmp-DB, _seed_source 强制 INSERT)
> - **验证**: 全量 pytest **889 passed + 2 skipped, 0 failed**; 手动 POST /run force=true 实测 38 篇入库; prod 8900 已重启 pid 6346 源仍默关
> - **踩坑**: test_global_filters 原 fixture 打真库 (prod 36kr_rss/qbitai 被改 example.com) → 修 tmp_path 隔离; 手动抓取 force=true 不看 is_active; git LibreSSL EOF; seed_config 被拦改 sqlite INSERT
> - **遗留**: seed 是临时 sqlite INSERT 未走官方 seed_config.py; follow_builders 仍默关

---

## 历史: **v1.0.40 — config 交互升级 (sprint-20 完整交付)** (2026-09-01 SHIPPED)

**主分支**: main @ `d7e6fa3` (PR #138 + #139 squash-merged, tag v1.0.40 `d7e6fa3` force push)
**Tag 链**: v1.0.40 → v1.0.39 → v1.0.37.7 → ...
**PR 链路**: #138 (3.3 + 4.1 纯前端) + #139 (3.4 全栈管线)
**Prod**: 8900 pid 47612 (新代码已生效; migration 033 落库; `/api/config/global-filters` 200)

> **v1.0.40 (2026-09-01) config 交互升级 (sprint-20)** — 3 需求一次性解决:
> - **3.3 关键词输入改 Chip/Tag CRUD** (Filter + Tag 两处复用 `keywordChipEditor` Alpine.data 组件, 增/删/批量粘贴拆分/去重, 用 moni 设计 token), 后端 keywords 接口未动。
> - **4.1 业务 tag name 提交确认状态机**: 新建 tag 加「✓ 确定标识 / ✏️ 修改」按钮 + `nameConfirmed` 状态机; audit S1「改 name 自动回退 confirmed」修掉漏洞; 编辑已有 tag 永久锁 + 🔒 徽章。
> - **3.4 全局关键词 (跨信源)**: migration 033 新表 `global_keyword_filters` (方案 A 独立表, 零破坏 source_keyword_filters), 5 层管线优先级铁律 (全局黑→源黑→全局白→源白→tag门控, 黑并集/白交集/match_field 各自独立), fetch_log `ALTER TABLE` 加 3 列全局计数列 (dropped_global_blacklist / dropped_global_whitelist / global_filters_applied), 端到端从 filters.py → base.py → cron 链路完整落库 (audit 抓出的 blocker), 全局面板在 /config + 全局白名单弹 ⚠️ 警告。
> - **验证**: agy Playwright + 真实 Chrome 9/9 通过 (PR1) + 全量 pytest **880 passed + 2 skipped, 0 failed** (PR2 后) + moni-audit 二审 (PR1: ✅ 可 merge; PR2 复审: ✅ 可 merge, 0 严重)。
> - **风险**: 全局白名单误配过窄会误杀全源 (后端空词 400 + 前端 Warning + 默认推荐黑名单三重护)。
> - **数据现状**: 测试 880 pass + 2 skip; schema_version 32 → 33 (033_global_keyword_filters.sql + ALTER fetch_log 3 列); data/news.db global_keyword_filters 表已建。

---

## 历史: **v1.0.39 — config 黑名单关键词输入锁死 + 业务 tag name 锁死修复** (2026-09-01 SHIPPED)

**主分支**: main @ `516b322` (PR #137 squash-merged, tag v1.0.39 `516b322` push)
**Tag 链**: v1.0.39 → v1.0.37.7 → ...
**PR 链路**: #137 (v1.0.39 config 前端 bugfix)

> **v1.0.39 (2026-09-01) config 前端 3 bug + 1 加固** (纯前端, moni/web/static/)
> - **黑名单 filter 关键词半角逗号输不进去**: 根因 `:value=join(', ')` + `@input=split(',')` 双绑不对称, :value 每次重算覆盖、光标跳走。修复: 改 `x-model` 绑独立 `keywordsText` 字符串字段, 用户打字自由 (app.js openConfigModal filter 分支初始化 + 新 filter 默认 '').
> - **逗号后的空格消除**: `join(', ')` 是渲染副作用, DB 数据层一直干净 list. 改 `join(',')` 无空格 + 保存时 trim 兜底.
> - **全角逗号兼容加固**: 保存时 `.replace(/，/g, ',')` 转半角再 split.
> - **业务 tag name 输一个字符就锁死**: 根因 `:disabled=!!configModalTarget.name` + x-model 每敲一键写回 name → 首字符后 !name 变 true 禁用. 修复: 改 `:disabled=!!configModalTargetOriginal`, 新建全程可改, 编辑已有才锁定.
> - 验证: agy 用 Playwright + 真实 Chrome 对 127.0.0.1:8901 端到端实测 4/4 通过 (半角/全角逗号分隔+落库, 空格消除, tag name 新建可输/编辑锁定).
> - **数据现状**: 同 v1.0.37.6 (案例 319 / 同业 446 / 高价值 116; 关键词 537; 测试 864 passed + 2 skip). 本次纯 UI 改动无 schema/数据变更, 无新增测试 (UI 任务, dad 约定 UI 不跑 pytest only 生产代码改动才跑).

---

## 历史: **v1.0.37.6 — sprint-19 案例地理维度 + 反选面板大区域重构** (2026-08-27 SHIPPED)

**主分支**: main @ `85ed1f9` (PR #135 + #136 合并 + 4 hotfix, 6 commits 链路)
**Tag 链 (本次)**: v1.0.37.6 @ `85ed1f9` (当前) ← v1.0.37.5 / .4 / .3 / .2 / .1 / v1.0.37
**PR 链路**: #134 (v1.0.36 sprint-18 关键词) → #135 (v1.0.37 sprint-19 地理后端) → #136 (v1.0.37.2 反选面板)

> **v1.0.37.6 (2026-08-27) sprint-19 + 4 hotfix** (dad 8-27 拍板: 案例国家/地区维度 + 反选面板展开太小)
> - **PR #135** v1.0.37: 案例地理维度 (国家+全球大区) 后端基础设施
>   - Migration 032 `case_geography` 表 (ISO 3166-1 alpha-2 + 中文名 + 5 大区 + is_primary 跨境 1:N)
>   - `GEO_ALIAS_MAP` (50+ 别名) + `SUBJECT_GEO_DEFAULTS` (70+ 银行主体 0-token 推断)
>   - `normalize_geo()` 入参兼容 str/list/dict/None
>   - pipeline 透传 country → 同事务写 case_geography (auto_llm/auto_rule)
>   - CLI `moni extract geo` (规则优先 + LLM 补 + dry-run 零写 + overwrite 保护 manual_user)
>   - prompt 加 country 字段 + few-shot 3 例 (境内/海外/全球)
>   - agy 二审 **Approve** (2 LOW 非阻断)
> - **PR #136** v1.0.37.2: 反选面板大区域重构 + 双维精确反选引擎
>   - 排除标签 Popover 520px 3 列 Chip grid + 双 Tab (业务关键词/国家与区域) + 搜索框
>   - localStorage v2 `{keywords, countries, regions}` + v1 自动迁移
>   - 精确 Set 反选: 关键词 + country_code + region 任一命中剔除
>   - 徽章: "已排除 N 关键词 · M 国家 · K 区域 · 过滤 X 条" (0 维省略)
>   - 后端: 榜单行带 keywords + 主地理字段 + geo_distribution 聚合
>   - agy 二审 **Approve** (2 LOW: 搜索 debounce + 焦点还原)
> - **hotfix v1.0.37.1**: thread-local 连接 — 修 8900 并发 sqlite3.InterfaceError
>   - 根因: singleton `_CON` 跨线程共享 + `check_same_thread=False`, FastAPI threadpool 两线程交错 execute 同一连接 → "bad parameter or other API misuse"
>   - 修: get_db() 返回 calling thread 专属连接 (threading.local), 每线程懒建; close_db() 只 retire 当前线程连接
>   - 验证: 8 线程 × 200 并发写 = 1600 行 0 错
> - **hotfix v1.0.37.3**: 反选文本 fallback 修无 geo 漏网 + 榜单分页
>   - P0 bug: dad 反馈排除巴西后仍有巴西资讯 — 102/319 case 无 case_geography, 反选只匹配 country_code 导致漏网
>   - 修: `_rowHitsExcluded` 加别名文本 fallback (BR→巴西/Brazil/Pix), 无 geo 时扫 subject/counterparty/summary
>   - 需求: 榜单分页 20/50 每页 + 翻页控件, 筛选后行数减少但每页容量不变
> - **hotfix v1.0.37.4**: 反选面板加大 (520→720px 宽, 360→520px 高, 3→4 列 grid) — dad 反馈"展开太小看得累"
> - **hotfix v1.0.37.5**: thread-local 路径感知 + 修测试悬挂锁 — 864 测试全绿
>   - 双根因 (agy 诊断): (1) test_api.py:417 缺 `conn.commit()` → 悬挂写锁 → anyio worker thread 等5s 超时; (2) thread-local 跨 pytest test 复用 worker thread 时, 旧 conn 指向已换掉的 tmp db 文件 → `no such table`
>   - 修: connection.py get_db() 加 db_path 感知自愈 (cached_path != curr_path 自动 drop + 重建); test_api.py 补 `conn.commit()` 立即释放锁
> - **hotfix v1.0.37.6** (grok 独立诊断加固): close_db 真 close + 删 30s 误导注释
>   - close_db 之前只把 conn 推到 `_RETIRED` 不 `.close()`, fd/锁泄漏 — 修: 显式 rollback + `.close()`
>   - 注释里 "测试 30s timeout override via conftest.py" 是空头支票 (conftest.py 不存在) — 删 + 改正说明指向 v1.0.37.5 commit
> - **数据现状**: 案例 319 / 同业 446 / 高价值 116; 关键词 537 个; 地理 330 行覆盖 302 case (94.7%), APAC 224 / NAM 58 / EMEA 29 / GLOBAL 14 / LATAM 5; 测试 **864 passed + 2 skipped**
> - **双 agent 交叉验证**: agy (v1.0.37.5) + grok (v1.0.37.6) 独立诊断, 核心共识 (commit + path-aware), grok 额外补 2 个技术债 (close_db + 误导注释)

## 当前状态: **v1.0.36 — sprint-18 案例关键词 LLM 打标** (2026-08-27 SHIPPED, PR #134)

> **v1.0.36 (2026-08-27) sprint-18 P0**: 关键词 LLM 打标 + `moni cases verify-all` CLI
> - CLI `moni extract keywords` (--source/--limit/--dry-run/--overwrite-auto, --source-filter 分流)
> - 规则阶段: `normalize_keywords()` (KEY_KEYWORD_MAP 别名库) + subject/counterparty 主体库兜底
> - LLM 阶段: prompt 增强 (Few-shot 3 例) + 429 重试 (time.sleep(5) 单次)
> - `--overwrite-auto` 范围安全 (只删 auto_rule/auto_llm, 保护 manual_user) + dry-run 零写
> - sprint-17 hotfix 落地 (spy call_llm e2e 测试钉死 prompt 类复发)
> - **数据现状**: 关键词 537 个入库 (auto_llm 536 + manual_user 1), 试点 mpaypass 500 篇提取 224 case; 测试 814 passed + 2 skip
> - agy + grok 双审计 **Approve**

## 当前状态: **v1.0.35.19 — 竞赛成果数据支撑** (2026-08-26 SHIPPED)

**主分支**: main @ `5e66984` (squash of PR #133; 批量提取CLI + 成果看板 + 案例库闭环 + 关键词反选)
**Tag 链 (本次)**: **v1.0.35.19 @ `5e66984`** (当前)

> **v1.0.35.19 (2026-08-26)** 竞赛申报成果数据支撑 (领导要求: 追踪多少同业/多少参考价值案例)。
> - P0 管道: `resolve_active_model()` (llm_settings 回退链, minimax-cn 恢复可用 dad 指定优先; ark 5h 配额易 429) + cases 占位机制 (mark_processing 防重复烧 LLM) + events/cron 去 minimax 硬编码
> - P1 CLI: `moni extract cases|events` (dry-run/source/limit/resume/skipped/failed) — 试点 mpaypass 100 篇 29.3min 提取 44 案例
> - P2 API: `/api/stats/cases-dashboard` (period=all 累计) + `/api/stats/data-funnel` + 案例 confirm/reject/PATCH/DELETE(软删 archived) + keywords CRUD
> - P2 前端 (grok): /stats 2 分区 (累计成效全景/周期运行监控) + 数据漏斗 4 数字 + 高价值榜单 (摘要/置信度/来源) + 排除标签反选 v1 + 案例编辑/删除/标签
> - schema v31 (031_case_keywords.sql) + 修复: case_id 脏字符/alias substring 越界/migration 断言
> - 数据现状: 案例 95 / 同业 123 / 高价值 40 / 漏斗 3583→666→95→89; 测试 798 passed + 2 skip
> - sprint-18 待开: 案例关键词 LLM 打标 + tag 双维度 + 反选升级 (plan: docs/plans/2026-08-26-sprint-18-case-keywords-plan.md)

**Demo 历史目录** (`docs/moni-web-showcase-history/`, dad 8-25 约定):
- `v1.0.35.2-final-2026-08-21.html` — 微信附件 baseline (md5 `926312d`), 参赛版
- `v1.0.35.17.1-baseline-2026-08-26.html` — 合规化前 baseline (md5 `ff38066`), 8-26 早晨
- `v1.0.35.18-compliance-preverify-2026-08-26.html` — 合规化版本 (md5 `dcf382be`), dad 验证通过
- `v1.0.35.2-git-069b4cc-2026-08-19.html` — git 官方 v1.0.35.2-hotfix @ `069b4cc` (md5 `dd49bae`)
- `v1.0.35.16.4-before-btn-fix-2026-08-25.html` — v1.0.35.16.4 当前 demo (md5 `926312d`)
- `README.md` — 命名约定 + 流程

**测试**: 未改生产代码, 无需跑 pytest
**生产**: 8900 PID 71613 running (v1.0.35.17 代码, v1.0.35.18 demo 改动是 demo-only)

---

## 当前状态: **v1.0.35.17 — bestblogs 超时假警报修复** (2026-08-25 SHIPPED)

**主分支**: main @ `de9e922` (squash of PR #130; 超时按源配置化)
**Tag 链 (本次)**: **v1.0.35.17 @ `de9e922`** (当前)

> **v1.0.35.17 (2026-08-25)** fix issue #129: bestblogs 整轮跑完 1875 篇(883s>600s)被全局 JOB_TIMEOUT=600 事后打成 `timeout after 10min` = **假警报非漏抓**。超时按源配置化: `_job_timeout(source)` 读 `sources.fetch_config.timeout_sec`(缺省 600); `STALE_THRESHOLD` → 动态 `get_stale_threshold()=max(全局 timeout)+300`(≥900); `mark_timeout` 文案带已抓计数; bestblogs `timeout_sec=1800` + 4 个 bestblog* `fetch_interval_min` 5→120(防叠跑)。全量 **780 passed + 2 skip**。8900 PID 14325→71613。

---

## 当前状态: **v1.0.35.16.5 — sprint-16.7 demo 修复 + 历史版本管理建立** (2026-08-25 SHIPPED)

**主分支**: main @ `29442a5` (squash of PR #128; demo 修复 + history目录建立)
**Tag 链 (本次 sprint-16.7)**: v1.0.35.16 @ `0c425cc` · v1.0.35.16.1 @ `ccb59b7` · v1.0.35.16.2 @ `797183d` · v1.0.35.16.3 @ `1bc4892` (回退 demo) · v1.0.35.16.4 @ `a4a81b0` (微信 baseline) · **v1.0.35.16.5 @ `29442a5`** (当前)

> **sprint-16.7 (2026-08-24 → 2026-08-25)** 五次 ship:
> 1. v1.0.35.16 (PR #123, `0c425cc`) — demo 重建反映 v1.0.35.13/14/15 生产 UI
> 2. v1.0.35.16.1 (PR #124, `ccb59b7`) — demo fix: 生成器剥 oklch 时 `.dark` 覆盖 `:root` 浅色变量
> 3. v1.0.35.16.2 (PR #125, `797183d`) — demo fix: html_landing `</script>` 提前终止 JSON script 致简报平铺
> 4. v1.0.35.16.3 (PR #126, `1bc4892`) — 回退 demo HTML 到 v1.0.35.2-hotfix (修 ship 出去的 demo 跑飞问题)
> 5. v1.0.35.16.4 (PR #127, `a4a81b0`) — 切到 dad 微信附件 demo (md5 `926312d`) 作为最终版 baseline
> 6. v1.0.35.16.5 (PR #128, `29442a5`) — 修复 panel-soft 内 "打开产物" 按钮文字看不清 (CSS `.panel-soft * { color: var(--fg) }` 通配覆盖 `.btn-primary` 字色)

**Demo 历史目录** (`docs/moni-web-showcase-history/`, dad 8-25 约定):
- `v1.0.35.2-final-2026-08-21.html` — 微信附件 baseline (md5 `926312d`), 参赛版
- `v1.0.35.2-git-069b4cc-2026-08-19.html` — git 官方 v1.0.35.2-hotfix @ `069b4cc` (md5 `dd49bae`)
- `v1.0.35.16.4-before-btn-fix-2026-08-25.html` — v1.0.35.16.4 当前 demo (md5 `926312d`)
- `README.md` — 命名约定 + 流程

**测试**: 772 pytest pass / 2 skip (本次未改生产代码)
**生产**: 8900 PID 14325 running (v1.0.35.15 代码, v1.0.35.16.5 demo fix 是 demo-only)
**测试**: **770 passed / 2 skipped / 0 failed** (基线 766 → v1.0.35.13 +2, v1.0.35.14 +4)

### v1.0.35.13 — provider 切 ark/ark-code-latest + HERMES_PROFILE=coder (PR #120)

**背景**: 联网核查 (fact_check_plus) 端到端一直跑不通。MiniMax-M3 429 Token Plan 配额耗尽; 切 ark 后 8900 服务进程又报 `Unknown provider 'ark'`。

**修法**:
1. `moni/db/seed_llm.py`: 默认 provider/model 从 minimax-cn/MiniMax-M3 → **ark/ark-code-latest** (summary/tag/fact_check 三用途全切, minimax 回退)
2. `moni/llm/client.py`: subprocess env `setdefault("HERMES_PROFILE", "coder")` —— 8900 进程无 HERMES_PROFILE → fallback default profile 认不出 ark (ark 定义在 coder profile config, base_url ark volces **/v3**)

**验证**: 8900 PID 88391, 文章 df2257ff32d22bc3 (AI办公垃圾赛道) → done, 27.46s, 1 尝试

### v1.0.35.14 — 联网核查结果友好渲染 + 引号解析兜底 (PR #121)

**背景**: dad 生产验收 v1.0.35.13 后暴露: (1) /llmJobs modal 甩 LLM 原始 JSON 用户看不懂 (2) LLM rationale 偶发塞未转义 ASCII 引号 → evidence parse failed。

**修法**:
- 后端 `llm_jobs.py`: GET /api/llm/jobs/{id} 对 fact_check_plus done 附加结构化 `fact_check` (conclusion + evidence rows)
- 后端 `jobs.py`: `_parse_fact_check_plus_evidence` 加保守兜底 —— 中文夹住的 ASCII 引号转全角再试 (解 evidence parse failed)
- 模板 `fact_check_plus.md`: 强约束禁字符串值内半角引号 (治本)
- 前端 modal 渲染「核查结论」badge + 证据卡片 (可点 URL) + 可折叠原始 JSON

**验证**: 8900 PID 97142, 数字人民币文章 (2c0efb0264d49cf8) → done, 79.7s, 11 evidence 全入库

### v1.0.35.15 — /llmJobs 列表 UX (PR #122)

**背景**: dad 生产验收后补 2 个 UX: 点文章标题想当前页看文章详情; 任务 id 列表没展示。
**修法** (纯静态 index.html + main.css, 刷新生效): 标题点击改 `showArticleDetail({id: job.article_id})`; 加「任务 ID」列 (前 8 位等宽 + 悬浮看全 id)。
**8900**: PID 14325 running, 5 端点 200

## 当前状态: **v1.0.35.9 — sprint-16 P1 fact_check_plus 占位符 $name → {name} 修复** (2026-08-23 SHIPPED)

**主分支**: main @ `15a5256` (squash of PR #116)
**PR #116**: **MERGED (squash, branch deleted)** @ `15a5256` (2 files +63/-4)
**Tag**: **v1.0.35.9 @ `15a5256`** (force push OK, 无 drift)
**测试**: **757 passed / 2 skipped / 0 failed** (基线 756 → +1 新单测 `test_render_fact_check_plus_empty_article_falls_back` + 99c133c 的 `test_render_fact_check_plus_fills_placeholders`)
**Web 服务**: `127.0.0.1:8900` PID 9661 (uvicorn, absolute python3.12 path, dotenv loader in /tmp/moni-8900-launcher.py 绕 shell builtin)
**端到端验证 (live 8900)**:
- 5 端点 200 OK: `/`, `/api/articles`, `/api/config/sources`, `/api/llm/jobs`, `/api/share/tasks`
- POST `/api/articles/{id}/fact-check-plus` 跑 Westpac + Amp 文章 `d1073c471aa9a9b1`:
  - `status=done` (修前 `failed` "unfilled placeholders")
  - duration 51.2s, input 19481 tokens (修前 0 token, LLM 拒答)
  - `conclusion=【已核实】`, 5 条 evidence 落库 (westpac.com.au 官方 / Finextra / westpac.com.au 公司概况 / Wikipedia Big Four / Asian Banking & Finance)
- 另一篇播客文章 (第一财经 Vol.585): `status=failed` 但 error 是 "no evidence with snippet" (跟修前 "unfilled placeholders" 不同, 渲染层已通, 是下游 evidence parse 闸跟【无需核查】结论交互的已知边界, 跟本次 fix 无关)

## 当前状态: **v1.0.35.10 — sprint-16.5 lint placeholder + spy call_llm e2e** (2026-08-24 SHIPPED)

**主分支**: main @ `1fa7c42` (squash of PR #117)
**PR #117**: **MERGED (squash, branch deleted)** @ `1fa7c42` (3 files +387/-0)
**Tag**: **v1.0.35.10 @ `1fa7c42`** (push OK, 无 drift)
**测试**: **762 passed / 2 skipped / 0 failed** (基线 757 → +5: 3 lint test + 2 spy test)
**Web 服务**: `127.0.0.1:8900` PID 34329 (uvicorn, absolute python3.12 path, dotenv loader in /tmp/moni-8900-launcher.py)
**端到端验证 (live 8900)**:
- 5 端点 200 OK: `/`, `/api/articles`, `/api/config/sources`, `/api/llm/jobs`, `/api/share/tasks`
- **lint 守卫 live**: `python scripts/lint_prompt_placeholders.py` exit 0 (4 模板 clean)
- POST `/api/articles/{id}/fact-check-plus` 跑 36kr 文章 `3908eb246312d9a3` (博泰车联 8397 座舱):
  - `status=done` (v1.0.35.9 后继续 ship,渲染层修复已稳)
  - duration 9.7s, input 12969 tokens
  - `conclusion=【待核实】` (仅 财联社 + 至顶网 2 自媒体,无 gov/交易所命中,符合 4 档规则)
  - 2 条 evidence 落库 (cls.cn / zhiding.cn)

## 当前状态: **v1.0.35.11 — sprint-16.6 【无需核查】空 evidence 误报修复** (2026-08-24 SHIPPED)

**主分支**: main @ `dd3ef00` (squash of PR #118)
**PR #118**: **MERGED (squash, branch deleted)** @ `dd3ef00` (2 files +211/-1)
**Tag**: **v1.0.35.11 @ `dd3ef00`** (push OK, 无 drift)
**测试**: **765 passed / 2 skipped / 0 failed** (基线 762 → +3: 1 happy + 2 防御)
**Web 服务**: `127.0.0.1:8900` PID 48790 (uvicorn, absolute python3.12 path, dotenv loader)
**端到端验证 (live 8900)**:
- 5 端点 200 OK: `/`, `/api/articles`, `/api/config/sources`, `/api/llm/jobs`, `/api/share/tasks`
- POST `/api/articles/df2257ff32d22bc3/fact-check-plus` 跑 dad 报错文章 "AI办公这个'垃圾赛道',大厂到底在抢什么?" (bestblog-podcast 人民公园说AI):
  - `status=done` (修前 `failed` "no evidence with snippet")
  - duration 9.78s, input 13280 tokens, error=None
  - DB 入库确认: `fact_check_conclusion=【无需核查】`, `has_fact_check=1`, `has_web_fact_check=1`, `fact_check_method=web_search`, `fact_check_note='[]'`, `fact_check_timeliness=NULL`

## 当前状态: **v1.0.35.12 — sprint-16.6 hotfix 扩展空 evidence 豁免到【待核实】/【存疑】** (2026-08-24 SHIPPED)

**主分支**: main @ `a479714` (squash of PR #119)
**PR #119**: **MERGED (squash, branch deleted)** @ `a479714` (2 files +66/-8)
**Tag**: **v1.0.35.12 @ `a479714`** (push OK, 无 drift)
**测试**: **766 passed / 2 skipped / 0 failed** (基线 765 → +1 新测试 `test_doubtful_empty_evidence_succeeds`, 改 `test_pending_..._still_fails` → `_succeeds`)
**Web 服务**: `127.0.0.1:8900` PID 70928 (uvicorn, absolute python3.12 path, dotenv loader)
**端到端验证 (live 8900)**:
- 5 端点 200 OK: `/`, `/api/articles`, `/api/config/sources`, `/api/llm/jobs`, `/api/share/tasks`
- POST `/api/articles/83318acaac00bc8a/fact-check-plus` 跑 bestblog-x Twitter 小众文章 "Apply or refer the best person you know":
  - `status=done` (v1.0.35.12 沿用 v1.0.35.11 hotfix 路径,【无需核查】+[] → done)
  - duration 6.42s, input 13259 tokens, error=None
  - DB 入库: `fact_check_conclusion=【无需核查】`, `has_web_fact_check=1`, `fact_check_method=web_search`, `fact_check_note='[]'`

### v1.0.35.12 核心改动 (sprint-16.6 hotfix)

**背景**: 父 agent 扫 DB llm_jobs 发现 v1.0.35.11 ship 后仍残留 6 个【待核实】+[] fail, 跟【无需核查】同性质. 模板 L41 明确允许 3 档 evidence=[] (【待核实】/【存疑】/【无需核查】), v1.0.35.11 只豁免了【无需核查】, 漏了 2 档.

**修法 (agy 方案 A)**: `jobs.py:649` 白名单 → 黑名单
- 修前: `if conclusion.startswith("【无需核查】"):` 豁免
- 修后: `if not conclusion.startswith("【已核实】"):` 豁免 (只有【已核实】+[] 仍 fail, 是 LLM 错误场景, 应被闸拒绝)
- 3 档 (【待核实】/【存疑】/【无需核查】) evidence=[] → 直接 done 入库, 跳过 evidence 闸
- 防御测试: `test_verified_empty_evidence_still_fails` (【已核实】) 仍 fail; `test_pending_empty_evidence_succeeds` (改名前是 still_fails) + 新增 `test_doubtful_empty_evidence_succeeds` (【存疑】)

**Risk**: Low — ~36 行改动 (5 行生产代码 + 31 行测试), 逻辑白名单 → 黑名单. rollback = revert 1 commit.

**Refs**: agy herdr 报告 `/Users/mt16/dev/moni-ref-fact-check/SPRINT-16.6-FOLLOWUP-REPORT.md` (179 行, moni-agy pane `w4:p1V` Claude Opus 4.6 调研 ~2 min)

### v1.0.35.11 核心改动 (sprint-16.6)

**背景**: dad 2026-08-24 live 验报 bug — 纯观点文 "AI办公这个'垃圾赛道'..." 联网核查报 "DB writeback failed: ... no evidence with snippet". LLM 判定 + 返回全对 (【无需核查】 + 空 evidence 符合模板), 但 `_persist_fact_check_plus` 闸把它当失败处理. **设计与实现矛盾**: 模板 `fact_check_plus.md:42` 规定【无需核查】evidence 必须为空, 但 `jobs.py:640-641` 不区分 conclusion.

**修法 (agy 方案 A)**:
- `_persist_fact_check_plus` 提前 `conclusion` 到 Gate 1 之前 (agy 第一刀: 0 regression)
- `if not valid:` 加条件 `if conclusion.startswith("【无需核查】"):` → 调新增 `_persist_no_check_needed()` 直接 done 入库
- 新增 `_persist_no_check_needed` (~70 行): UPDATE articles + DELETE old evidence, 3 层 migration fallback
- 其他 3 档 (【已核实】/【待核实】/【存疑】) 行为不变 (防御测试钉死)

**Risk**: Low — ~20 行生产代码 + ~125 行测试, 仅在 `conclusion.startswith("【无需核查】") AND valid==[]` 时走新路径.

**Refs**: agy herdr 报告 `/Users/mt16/dev/moni-ref-fact-check/SPRINT-16.6-REFERENCE-REPORT.md` (353 行, moni-agy pane `w4:p1V` Claude Opus 4.6 调研 ~3 min)

### v1.0.35.10 核心改动 (sprint-16.5 加固)

**背景**: v1.0.35.9 修了 fact_check_plus.md 占位符 bug, 但只是治标. 本 PR 加固, 防止 "$name 雷再埋" + 钉死 sprint-14 类复发 (mock 太厚盖掉真实 LLM 输入).

**3 文件 ship**:
1. `scripts/lint_prompt_placeholders.py` (新, 95 行) — 扫 4 个 regex-rendered .md 模板, 拒 `$[a-zA-Z_]+` 占位符 (代码块内允许). 不扫 case_extraction.txt / event_extraction.txt (不同渲染器)
2. `tests/test_lint_prompts.py` (新, 99 行, 3 tests) — pytest 集成 (不引 pre-commit 依赖, moni 无 `.pre-commit-config.yaml`)
3. `tests/test_fact_check_plus.py` (+192 行, 2 spy tests) — captured dict spy 模式, 断言 prompt 到 LLM 时不含 `$xxx` + 含真实 article 字段 + `extra_args=["-t", "web"]`; raw_content fallback 路径钉死

**范围决策 (agy herdr §3)**: 原 8 项盲区砍到 3 项 (本 PR 全部 ship), 砍 4 项理由:
- `{json_key}` 边界 (L772 现有测试间接覆盖)
- `published_at` 异常值 (DB schema 保证不会触发)
- `_PLACEHOLDER_RE` regex pin (5 sprint 不动, 低价值)
- `extra_args` 单独 spy (合并到 spy 测试)

**Risk**: Low — 零生产代码改动, rollback = 删 3 文件 + revert 1 commit.

**Refs**: agy herdr 报告 `/Users/mt16/dev/moni-ref-fact-check/SPRINT-16.5-REFERENCE-REPORT.md` (198 行, moni-agy pane `w4:p1V` Claude Opus 4.6 调研 ~4 min)

### v1.0.35.9 核心改动 (sprint-16 P1)

**根因**: moni/llm/prompts/fact_check_plus.md L45-50 用 `$title/$url/$published_at/$content` (dollar 前缀), 但 `moni/llm/prompts/__init__.py::_render()` 正则 `_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")` 只认 `{name}`。`$name` 形式完全不被识别 → 模板原样发到 LLM → LLM 拒答 "article template was sent with unfilled placeholders" → `llm_jobs.status=failed`。

**暴露过程**: 这 bug 自 v1.0.22 (fact_check_plus 上线) ship 起一直未通, 被 sprint-07~13 hermes flag (LLM 拒答自动 retry) 遮蔽, v1.0.34 删 flag 后事实核查 job 才持续以 failed 落库, v1.0.34.2 修了一部分但漏这个, v1.0.35 / v1.0.35.1~8 都没动, 拖到 sprint-16 P1 才修。

**修法**: 模板 4 行 `$name → {name}`, 跟其他 3 模板 (fact_check.md / predict_tag.md / summary.md) 风格统一。

**端到端验证** (tmp DB + enqueue + _run_job + mocked call_llm):
- prompt 渲染输出不再含 `$title` 等字面
- `llm_jobs.status=done` (修前 `failed`)
- `llm_jobs.error=None` (修前含 "unfilled placeholders")
- `fact_check_evidence` 落库

**测试**: `tests/test_fact_check_plus.py` 新增 `test_render_fact_check_plus_fills_placeholders`, 断言渲染后不含 `$title`/`$url`/`$published_at`/`$content` 字面, 含已填内容。

### v1.0.35.4 核心改动 (2 PR)

**主分支**: **main** @ `244bd9f` (PR #113 + #114 都 merged)
**PR #113**: **merged** (build script 同步 oklch→hex, 231 ins / 4 del, `244bd9f`)
**PR #114**: **merged** (test fix, 父 agent 自主, 2 ins / 2 del, `a799f36`)
**Tag**: v1.0.35.3 @ `069b4cc` (维持, demo HTML 不重 ship, 跑 8900 不需 restart)
**测试**: **755 passed / 2 skipped / 0 failed** (链修前 1 fail stale, merge 后 OK)
**Web 服务**: `127.0.0.1:8900` PID 24133 (v1.0.35.1 起的, demo 文件改动不需 restart)
**Web 服务**: `127.0.0.1:8900` PID 24133 (v1.0.35.1 起的, demo 文件改动不需 restart)

### v1.0.35.4 核心改动 (2 PR)

**PR #113 (build script, 231 ins / 4 del)**:
- v1.0.35.3 ship 时 demo HTML 手快绕过了 oklch 颜色转换 (因 Windows Chrome 105 不支持 oklch), 现在正式 patch 进 build script
- 新增 `_oklch_to_srgb()` / `_oklch_from_var_to_rgba()` / `_strip_oklch()`, 用 CSS Color 4 oklch(L C h [/ alpha]) → sRGB hex
- `main()` 集成: assemble 前 strip CSS, assemble 后再 strip 整 HTML (主 JS theme dict)
- 验证: 重 build `oklch stripped (CSS): 174 → 0 (var_map size=32)`, `oklch in assembled HTML (JS): 81 → 0`

**PR #114 (test fix, 2 ins / 2 del)**:
- v1.0.34.2 ship 加 migration 030 (ghost sources cleanup), schema 29 → 30, 但 `test_llm_usage_counters.py::test_migration_029_adds_usage_columns` 2 个 assert 仍 hard-code 29
- 修法: 升 30 + 注释 v1.0.34.2 source
- 验证: 755 passed / 2 skipped / 0 failed (链修前 1 fail)

### 约束遵守
- demo HTML 不重 ship (3.85MB v1.0.35.3 dad 实测满意版本维持)
- `docs/moni-showcase-artifacts/` 5 HTML 保留 disk 不动 (git rm sandbox 拦截, sprint-16 处理)
- `~/Documents/moni-archive-20260819/` 4 目录保持现状 (HANDOFF 原定)

### 后续 (out of scope, 记录给后续 sprint)
- sprint-14 P1 仍是 `moni/llm/prompts/__init__.py::render_fact_check_plus()` 模板变量未替换 bug
- v1.0.34.2 防御 UX (`is_fetchable` 字段 + 灰 badge) 暂挂
- `docs/moni-showcase-artifacts/` 5 个外部 HTML 暂保留 (sandbox 拦截, 后续 sprint-16 review)
- `~/Documents/moni-archive-20260819/` 4 归档目录保留 (HANDOFF "不删, 等 review")

---

## 当前状态: **v1.0.35.3 — demo 单文件物料内嵌 (v1.0.35.2 hotfix, dad 要求单文件交付)** (2026-08-19 PM)

**主分支**: **main** @ `069b4cc` (hotfix ship, code HEAD)
**Web 服务**: `127.0.0.1:8900` PID 9646 (v1.0.35.1 起的, demo 文件改动不需 restart)
**Tag**: v1.0.35.3 @ `069b4cc` (新 tag, 不用 --force)
**测试**: **738 passed / 2 skipped / 0 failed** (无回归)
**文件大小**: demo 3.50MB → 3.85MB (+350KB, base64 webp 240KB + modal HTML + 4 占位)

### v1.0.35.3 核心改动 (v1.0.35.2 hotfix)

**dad 反馈**: 分享演示时只能有 1 个 HTML 文件 (单文件交付). v1.0.35.2 的 `docs/moni-showcase-artifacts/` 5 个独立 HTML 不符合.

**修复**:
- 5 个外部 HTML 展示页删除, 改为 5 个 inline modal (`x-show` 切换)
- 仅 1 个真物料 (`info_graphic` 2.4MB 原图 → webp 240KB → base64 320KB) 内嵌进 demo HTML
- 其他 4 种 (`html_landing` / `pdf` / `report_png` / `markdown`) 用 moni 主题占位 + 文字说明, 标 "演示占位"
- `markdown` 是真文本内嵌 (9KB, 完整源文) 展示原文 (等宽字体 + 等高滚动块)
- 新增 `showcaseArtifact` / `closeShowcaseArtifact` JS 方法 + `showcaseArtifactOpen` state
- 文件大小: 3.50MB → 3.85MB (+350KB), Chrome 105 `file://` 打开 1-2 秒可接受

### 约束遵守

- 不删 `docs/moni-showcase-artifacts/` (留给 sprint-16 review 是否保留为开发参考)
- 不动业务代码
- 不动 working tree `scripts/build_showcase_html.py` (跟 demo HTML 分开 commit)

### 后续 (out of scope, 记录给后续 sprint)

- sprint-14 P1 仍是 `moni/llm/prompts/__init__.py::render_fact_check_plus()` 模板变量未替换 bug
- v1.0.34.2 防御 UX (`is_fetchable` 字段 + 灰 badge) 暂挂
- `scripts/build_showcase_html.py` 待 sprint-16 更新 (v1.0.35.3 的 inline modal 模式同步进 builder)
- `~/Documents/moni-archive-20260819/` 4 个残留归档待 sprint-16 review
- `docs/moni-showcase-artifacts/` 5 个外部 HTML 待 sprint-16 review (保留/删除)

---

## 当前状态: **v1.0.35.2 — demo refresh (sprint-15, UI 搬运 + artifacts 整肃 + 演示物料)** (2026-08-19 PM)

**主分支**: **main** @ `22b846a` (sprint-15 ship, code HEAD)
**Web 服务**: `127.0.0.1:8900` PID 9646 (上次 v1.0.35.1 起的, demo 文件改动**不需 restart 8900**, demo HTML 是 moni-web-showcase.html 静态文件)
**Tag**: v1.0.35.2 @ `22b846a` (已 push, tag=main 无漂移)
**测试**: **738 passed / 2 skipped / 0 failed** (无回归, 跟 v1.0.35.1 持平)

### v1.0.35.2 核心改动 (sprint-15, 3 路并行, herdr pane 分工)

**A 路 (artifacts-curator pane, commit 7d8bbab)**:
- 根目录残留目录 `output/` `share_output/` `infographic/` `infographics/` mv 到 `~/Documents/moni-archive-20260819/` (不删, 归档)
- 写 `docs/ARTIFACTS-STRUCTURE.md` 说明生产真目录是 `data/artifacts/` (代码佐证: `moni/share/artifacts.py:25` + `moni/share/worker.py:36`)

**B 路 (主线 blue-owl, commit 22b846a) — demo HTML 同步 v1.0.35.1 prod UI**:
- 分类列名"分类" → "命中标签"
- tag chip 紧凑样式 (字号 0.78→0.72rem, padding 2px 10px→1px 6px, 圆角 4→3px, 间距 6px→4px)
- 全局兄弟选择器 `.btn + .btn { margin-left: 8px }` + `.modal-footer` override
- `.link` class 暗色模式友好
- `.row-actions` wrapper + ghost-style `.btn-danger` (行内 destructive 不显沉重)
- articles 操作列加删除按钮 (demo 化: `showDeleteDemo` 弹"演示提示" modal, 不真删)
- CRUD 标题模糊搜索框 (300ms debounce, 后端 `/api/articles?keyword=` LIKE 已支持)

**C 路 (demo-builder pane -> 主线接管, 因 coder state.db 2.8GB 写满中断, commit 22b846a)**:
- `docs/moni-showcase-artifacts/` 5 个独立 HTML 展示页: `info_graphic` / `html_landing` / `pdf` / `report_png` / `markdown`
- 每个展示页内嵌真物料 (`data/artifacts/` 下的真产物, 5 个最大最完整: 2.4MB 信息图 / 158KB HTML / 326KB PDF / 481KB 报告 PNG / 21KB Markdown)
- demo HTML 分享页面加"演示物料 5 种格式"按钮组 (target=_blank, 不影响原"打开产物"演示流程)

### 约束遵守

- 不删任何东西 (artifacts 整肃用 mv, 不用 rm -rf)
- 不动 working tree `scripts/build_showcase_html.py` (跟 demo HTML 分开 commit, 等 dad 后续决定 build 脚本要不要更新)
- 不碰 moni 业务代码
- herdr pane 派 2 个 agent (artifacts-curator + demo-builder) 因 coder profile state.db 写满 (`/Users/mt16/.hermes/profiles/coder/state.db` 2.8GB) 多次中断, demo-builder 转主线手动完成

### 后续 (out of scope, 记录给后续 sprint)

- sprint-14 P1 仍是 `moni/llm/prompts/__init__.py::render_fact_check_plus()` 模板变量未替换 bug
- v1.0.34.2 防御 UX (`is_fetchable` 字段 + 灰 badge) 暂挂
- demo build 脚本 `scripts/build_showcase_html.py` 待 sprint-16 更新 (把 sprint-15 的 manual patch 同步进 builder)
- `~/Documents/moni-archive-20260819/` 残留归档待 sprint-16 review 是否真可删

---

## 当前状态: **v1.0.35.1 — production UI ship (PR #112 + PR #113 hotfixes)** (2026-08-19)

**主分支**: **main** @ `7080e6a` (v1.0.35.1 ship, code HEAD)
**Web 服务**: `127.0.0.1:8900` PID 24133 (2026-08-19 PM restart, 6 端点 200 OK, tag v1.0.35.1 = code HEAD `7080e6a`)

### v1.0.35.1 核心改动 (sprint-14 P2 + P3 + P4 + P5, dad 6 轮反馈收敛)

**Q1-Q6 production UI 修复** (demo HTML 实测发现同款 bug, 带回 prod)
- Q1 tbody hover `#F9F2E4` / `#241D17` (含 `.dark` + `:root.dark` 双保险)
- Q2 `.tag-chip` 文字色 白天 `#1A1A1A` / 黑夜 `#F2E6CE`
- Q3 `.tag-chip-wrap` / `.tag-chip` margin
- Q4 `.link` class (暗色模式友好) + llmJobs article 加 class
- Q5 分类列固定 140px + td.tags-cell 垂直对齐
- Q6 `.title-cell` 单行截断 (原2 行被 dad 否决, 改回 ellipsis)

**waza-ui 间距审计** (sprint-14 P3)
- 全局 `.btn + .btn { margin-left: 8px }` (46 处按钮自动获得安全距离)
- `.modal-footer` override 避免 16px 叠加
- `.row-actions` wrapper 4 处 + `.row-actions .btn-danger` ghost 样式 (destructive 不显沉重)
- 全局裸 `<a>` 加 `class="link"` 修复暗色刺眼 (share 输出列 + CRUD url + fetch 信源 url)
- fetch progress icon 容器背景 `var(--card)` (跟随主题, 不再硬编码 `#1a1a1a`)

**分类列重新设计** (sprint-14 P4)
- 列名 "分类" → "命中标签"
- 所有 chip 都展示 (取消 slice 前 3 限制)
- chip 字号 `0.78rem` → `0.72rem`, padding `2px 10px` → `1px 6px`, 圆角 `4px` → `3px`
- chip 间距 `6px` → `4px` (缩但保留), 列宽 `160px` → `220px` max

**新功能** (sprint-14 P5)
- Feature 1: articles + pinned tab 操作列加删除按钮 (二次确认 modal 复用), `confirmDelete` 同步清 `pinnedArticles[]`
- Feature 3: CRUD 列表加 title 模糊搜索框 + 300ms debounce + `loadCrudArticles` 传 `keyword`

**顺手 bug fix**
- articles 原文时间 fallback (`published_at || fetched_at || created_at`)
- LLM provider button 重复 `class=` attribute bug
- title-cell hover tooltip (molevibe 风格浮层)

### 效果

- 5 端点 200 OK (含 /share, /crud, /fetch, /articles)
- `pytest tests/` **738 passed, 2 skipped** (deselect 17 flaky 网络/边界用例, 非 UI 回归)
- prod CSS 已生效 (15 处 `v1.0.35.1` 标记)
- 列名 "命中标签" 在 HTML 已生效

### 后续 (out of scope, 记录给后续 sprint)

- sprint-14 P1 仍是 `moni/llm/prompts/__init__.py::render_fact_check_plus()` 模板变量未替换 bug (v1.0.34 浮出)
- v1.0.34.2 防御 UX (`is_fetchable` 字段 + 灰 badge) 暂挂
- demo HTML (`docs/moni-web-showcase.html`) 不在 main, 独立产物 (`~/Documents/moni-share/` 副本)

---

## 当前状态: **v1.0.35 — production UI 修复 (hover / tag chip / link / 标题截断)** (2026-08-19)

**主分支**: **main** @ `ab18556` (PR #112 squash-merged, tag v1.0.35 `ab18556` push, tag=main 无漂移)
**Web 服务**: `127.0.0.1:8900` 已 restart 载入 v1.0.35 代码 (2026-08-19 AM, HTTP 200, 5 端点 OK)

### v1.0.35 核心改动

**Sprint-14 P2 — prod UI bug 修复 (demo HTML 实测发现同款 bug, 带回 prod)**

- **Q1 tbody hover 色**: `oklch(from var(--muted) l c h / 0.5)` 灰蒙 → `#F9F2E4` 白天 / `#241D17` 黑夜 (含 `.dark` + `:root.dark` 双保险). demo 截图发现 prod 也有.
- **Q2 tag chip 文字色**: 默认 `oklch` 太淡 → `#1A1A1A` 白天深色 / `#F2E6CE` 黑夜荔肉白. 保留各色 tag inline `--tag-bg/--tag-fg` 能力 (prod 不走 demo 的"统一背景色").
- **Q3 tag chip 间距**: `.tag-chip-wrap` / `.tag-chip` margin `2px 6px` (修关键词过滤子菜单 chip 间距, prod 同款).
- **Q4 `.link` class**: 修裸 `<a>` 默认蓝色刺眼. 新增 `.link` CSS (紫 ` #4a3a8c` 白天 / ` #D1B3FF` 黑夜) + llmJobs 列表 article title 加 `class="link"`.
- **Q5 分类列固定宽度 + 垂直对齐**: `<th style="width: 140px; min-width: 140px;">分类</th>` + articles 列表 td class="tags-cell" 垂直居中 (含 chip wrap 同步).
- **Q6 title-cell 2 行截断**: `.title-cell` CSS (max-width 480px + -webkit-line-clamp 2) + `truncateTitle(text, max = 80)` 默认值 + 3 处调用 sites 改 80 (articles 列表 / CRUD 列表 / 卡片). **只改 CRUD 列表用 `.title-cell`, articles 列表不加 class 因 cell 里有 fresh/new badge**.

### 不带回 prod (demo-only, dad 明确)

- 抓取管理源 tag chip 改统一背景色 (Q8) — prod 各色 tag 保留
- 粒子动效 `count * 0.8` + `z-index: 3` (Q10) — 粒子是 demo 离线性能优化, dad 明确不要改 prod
- `tagLookup` map + 抓取管理源 tagLookup 渲染 (Q7/Q9) — demo-only 概念

### 顺手 bug fix

- articles 列表原文时间 fallback: `formatTime(a.published_at || a.fetched_at || a.created_at)` — 原文时间字段空时不再显示 "-"

### 效果

- 5 端点 200 OK (`/articles` `/api/config/sources` `/fetch` `/stats` `/`)
- `pytest tests/` **748 passed, 2 skipped** (无回归, 比 v1.0.34.2 baseline +15 = truncateTitle/title-cell 新 test coverage)
- prod 视觉: hover 不灰蒙, tag chip 文字清晰, llmJobs 列表 article 链接不再是默认蓝色, CRUD 列表标题列 2 行截断不再溢出

### 后续 (out of scope, 记录给后续 sprint)

- sprint-14 P1 仍是 `moni/llm/prompts/__init__.py::render_fact_check_plus()` 模板变量未替换 bug (v1.0.34 浮出)
- v1.0.34.2 防御 UX (`is_fetchable` 字段 + 灰 badge) 暂挂
- demo HTML (`docs/moni-web-showcase.html`) 不在 main 合入, 独立产物 (`~/Documents/moni-share/` 副本)

---

## 当前状态: **v1.0.34.2 — /fetch 页 4 行 ghost sources 清理** (2026-08-17 PM)

**主分支**: **main** @ `6fc33a1` (PR #110 squash-merged, tag v1.0.34.1 `6fc33a1` push, tag=main 无漂移)
**Web 服务**: `127.0.0.1:8900` 已 restart 载入 v1.0.34.2 代码 (2026-08-17 PM, HTTP 200, `/api/config/sources` 返 11 行无 ghost)

### v1.0.34.2 核心改动

- **A `moni/db/migrations/030_drop_mp_ghost_sources.sql`**: 删除 4 行 2026-08-13 测试残留的 ghost sources (id: `src-mp_3be50e90` / `src-mp_3be50e90-b` / `src-mp_4ea4c40a` / `src-mp_4ea4c40a-b`). 现象: `/fetch` 页同时显示 2 个 36氪 + 2 个 支付 (重影). 根因: 8-13 06:36/06:42 在 `/config` 批量创建测试行, hex 哈希后缀命名 (`mp_3be50e90` 等), url=example.com 占位, 从未清理. `/fetch` 调 `/api/config/sources` 直接读 DB 不过滤 YAML, 所以跟真行同屏. schema_version 29 → 30.
- **B `tests/test_migration_030_ghost_cleanup.py`**: 7 个新 case (migration version 同步 / 文件加载 / ghost 删 / 真源保护 / 重复运行 idempotent / 全 fresh DB migrate / FK CASCADE 自动清 source_tags)

### 效果

- `/api/config/sources` 返 15 → **11** (4 行 ghost 删); `/fetch` 页面"立即抓取"按钮 ghost 行 404 → 全 11 行按钮可达真源
- 真源全部保留 (`36kr_rss` / `mpaypass` / `huxiu` / `qbitai` / `bestblogs` / `bestblog-podcast` / `bestblog-x` / `bestblog-yt` / `wechat_rss_a` / `wechat_rss_b` / `xwatch`)
- FK CASCADE 自动带走 `source_tags` (如果有的话; prod 是 0 行 — schema 004 已配 CASCADE)

### 验证

- 7 个新 case **passed** (7/7)
- 18 个 schema/CRUD test **passed** (18/18)
- 4 端点 200 OK: `/api/config/sources` (11 rows) / `/api/fetch/sources` (11 rows) / `/api/articles` / `/api/cases/stats`
- 总测试 (跨 sprint baseline): 733+ passed / 2 skip

### 后续 (out of scope, 记录给后续 sprint)

- 防御 UX: 给 `/api/config/sources` GET 加 `is_fetchable` 字段, `/fetch` 页未挂载行显示灰 badge + 禁用按钮 — 防 YAML ↔ DB 漂移再次裸奔
- 架构: `/fetch` 页改读 `/api/fetch/sources` (YAML), `/config` 仍读 DB — 概念分层更干净
- 现阶段 dad 拍板选 A (最小改动), 后续如有 ghost row 重现再做

---

## 当前状态: **v1.0.34.1 — bestblogs UA+retry 修 SSL EOF + 全量 375 feeds + 财经号补全** (2026-08-17 PM)

**主分支**: **main** @ `6fc33a1` (PR #110 squash-merged, tag v1.0.34.1 `6fc33a1` push, tag=main 无漂移)
**Web 服务**: `127.0.0.1:8900` 已 restart 载入 v1.0.34.1 代码（2026-08-17 PM, HTTP 200）

### v1.0.34.1 核心改动

- **A `moni/fetch/sources/bestblogs.py`**: `_fetch_feed` 加浏览器 UA + 3 次重试退避 (修复 wechat2rss.bestblogs.dev 对 httpx 默认 UA 的 SSL EOF 断连, 全量 375-feeds sweep 0 giving up)
- **B DB 配置 (非 git)**: bestblogs `max_articles_total` 500→1900 (全量 375 feeds 不截断); bestblogs 绑 `banking`/`regulation` tag; banking 关键词 129→204 (+75 泛金融: 央行/利率/股市/IPO/证券/基金/汇率/保险/财报/港股 等)
- **C DB 配置**: wechat_rss_a/b 暂停 is_active=0 (上游 waytomaster 停更, 保留记录)

### 效果

- 抓取: fetched=1875 (375 feeds 全量跑通), 修复后轮次 inserted=140 (修复前最高 58)
- wechat2rss OPML 375 公众号覆盖 **149 → 212 (57%)**; 核心财经号 (财新/钛媒体/雪球/华尔街见闻/财联社/21世纪/格隆汇/虎嗅/人民日报) 全部进库
- 剩余 163 号: 153 泛民生/文化/体育号 (按 tag 门合理拦截) + 10 标题不含金融词的财经号 (靠后续轮次自然入库)

### 验证

- `tests/test_fact_check_plus.py`: **28/28 passed** (直接覆盖 jobs.py:296-297 改动)
- 19 个相关 test 文件: **487 passed / 2 skipped / 0 failed**
- 4 个 fetcher test: **87 passed**
- 端到端真跑 (8900 PID 72830 修复版): `POST /articles/{id}/summary` → rc=0; `POST /articles/{id}/fact-check-plus` → hermes 接受 cmd + LLM 返回 parseable JSON
- 5 端点全 200: `/api/articles`、`/api/config/sources`、`/api/cases/stats`、`/api/llm/usage`、`/api/cron/runs`

### 关联

- PR #109: https://github.com/mariusiaowego-commits/moni/pull/109
- 踩坑沉淀: HANDOFF.md §sprint-13.5

### ⚠️ 已浮出的 sprint-14 candidate (v1.0.34 不修)

- **prompt 渲染层 bug**: `fact_check_plus` 端到端跑出现 "article template was sent with unfilled placeholders ($title / $url / $published_at)" 错误, hermes 调用链路通了但 prompt 模板变量未替换。需查 `moni/llm/prompts/__init__.py` 的 `render_fact_check_plus()` 实现。  
  这条 bug 在 v1.0.32 之前就被 hermes flag 拒收遮蔽, **现在才浮出水面** = P1 "联网核查 ≥3 PASS" 闸仍未满足的根因。

---

## 历史: **v1.0.33 — method 饼图渲染修复 + v1.0.32 worker 漏改 pickup (sprint-13 收尾)** (2026-08-15)

**主分支**: **main** @ `51650cf` (PR #108 squash-merged, tag v1.0.33 `41d2236` push)
**数据库**: schema_version=29（v1.0.32 LLM usage counters）

- v1.0.33-A: method 饼图 Alpine SVG `:d` attribute reactivity quirk 修复 (`<g x-html="methodPieSvg()">`)
- v1.0.33-B: v1.0.32 squash-merge 漏 3 文件 pickup (`default_worker.sh` + 2 测试)
- PR: https://github.com/mariusiaowego-commits/moni/pull/108

### v1.0.32 — LLM 用量统计 (sprint-13, 5 个 fix) (2026-08-15)

- `moni/db/migrations/029_*.sql`: `llm_jobs` 加 `input_tokens / output_tokens / cost_usd` 列 + 索引
- `moni/llm/client.py`: `call_llm()` 加 `--usage-file` 支持 (hermes `-z` one-shot 写 JSON)
- `moni/llm/jobs.py`: token/cost 写回 `llm_jobs`
- `moni/stats/repository.py`: LLM usage aggregation by day/provider/model
- `moni/web/routes/`: `/api/llm/usage?period=7d/30d/90d` 端点 + 看板新模块
- `scripts/setup-hermes-fallback.sh`: YAML 编辑绕交互 picker, 配 hermes fallback chain
- 测试: +13 用量统计测试, 704 passed / 2 skipped / 0 failed
- PR: https://github.com/mariusiaowego-commits/moni/pull/107

---

## 历史: **v1.0.31 — PNG 生图重新设计 (sprint-12)** (2026-08-14)

**主分支**: **main** @ `1ad7bd5` (PR #106 merge, tag v1.0.31 `b324514` push)
**Web 服务**: `127.0.0.1:8900` PID 7323 已 restart 跟 main（2026-08-14）

### v1.0.31 核心改动

- **A1 显式传 provider/model**: `moni/share/worker_llm.py` 新模块, 从 DB `llm_settings.active_summary_model_id` 读, 拼 `--provider X -m Y` 显式传, 不依赖 `~/.hermes/config.yaml` 默认
- **A2 LLM 失败检测**: `detect_llm_failure()` 6 关键字 (API call failed / HTTP 4 / HTTP 5 / rate limit / insufficient credits / requires available credits), 子进程退出 0 但 stdout 命中 → 返回退出码 1
- **A3 远程 fal.media URL 下载**: `scripts/default_worker.sh` PNG 分支, curl 下载 + PNG magic `89504e470d0a1a0a` 校验, 失败报 FAIL 不再存 `.out` 假 done
- **C hermes fallback**: `scripts/setup-hermes-fallback.sh` (YAML 编辑绕交互 picker, 幂等)
- **D call_llm kwarg**: `moni/web/routes/llm.py` 去 `module=` kwarg, 5 provider 测连通都通
- **测试**: `tests/test_worker_llm_failure.py` 26 测试新加, **730 passed / 2 skipped / 0 failed**

### 关联

- PLAN: `moni/docs/plans/2026-08-14-sprint-12-png-redesign.md`
- closeout: `tqob/05-Coding/project-moni/sprints/sprint-12-png-redesign/`
- 修复 skill reference: `share-task-info-graphic-blank-artifact-triage.md`

---

## 历史: **v1.0.30 — 简报 worker 同步化 + PNG 生图流程修复 (sprint-11)** (2026-08-14)

**主分支 tag**: **v1.0.30** @ `817bb5f` (PR #104 squash-merged, 已 push)
**数据库**: schema_version=28（v1.0.28 fact_check 状态机 028）
**Obsidian**: `tqob/…/project-moni/sprints/sprint-10-fetch-frontend-ux/` status=已完成 · verify 3-1-1
**并行 sprint-09**: evidence 落库脱敏（另一 agent / worktree `moni-evidence-redaction`）

### v1.0.29 — SQLite 线程安全修复 (2026-08-13, PR #98)

- 修复 Python 3.12 间歇 SIGSEGV (5 次/天, `_pysqlite_query_execute` use-after-free 0x8)
- 根因: `close_db()` 立即 close / GC 释放全局单例连接, 与后台 worker (llm_jobs/fetch) execute 竞争
- 修法: connection.py — `close_db()` 改 retire 延迟关闭 (atexit) + `_CREATE_LOCK` + `_RETIRED` maxlen=8 防 fd 耗尽
- 验证: 全量 pytest 701 passed 0 segfault (之前 2-3 次必崩); 8900/8901 已重启生效
- PR: #98 https://github.com/mariusiaowego-commits/moni/pull/98

### v1.0.28 — sprint-10 抓取管理前端 (2026-08-13)

- 关 progress modal 后 Drive 像素 chip 持续显示，点击 reopen；任务行「进度」入口
- 多任务并行 TaskRows + poll 与 modal 解耦
- 抓取结果（摘要/JSON/分源）默认折叠
- 品牌：瞭望 + MONI + Koboyo lighthouse-beam logo/favicon
- 顶部操作钮与表内「立即抓取/连通性」统一 btn-primary / btn-secondary
- PR: #92 https://github.com/mariusiaowego-commits/moni/pull/92 · #94 https://github.com/mariusiaowego-commits/moni/pull/94

**并行支线（未 ship）**: evidence 落库脱敏 @ worktree `moni-evidence-redaction`

---

## 历史: **v1.0.25 — 看板 fact_check method 分布 (sprint-08 收尾)** (2026-08-12)

**主分支 tag**: **v1.0.25** @ 59bc328 (PR #88 squash-merged, **tag 已修复** — 2026-08-13 删旧推新, 远端 = 59bc3282)
**Web 服务**: `127.0.0.1:8900` PID 67958 (sprint-07 重启后, sprint-08 3 ship docs 改动, 不需重启)
**数据库**: schema_version=26 (migration 025_fact_check_evidence + 026_brief_evidence_rendered)
**测试**: 685 passed + 2 skip (655 baseline + v1.0.22 14 + v1.0.23 4 + v1.0.24 7 + v1.0.25 5 = 685)

### v1.0.23 / v1.0.24 / v1.0.25 — sprint-08 3 sub-ships

**v1.0.23 — B5 失败 raw_output 落库** (P0 诊断盲区修复):
- `moni/llm/jobs.py` 新增 `_truncate_utf8` + `_log_failure` (UTF-8 边界保护, 截断 2KB + `... [truncated N B total]` 提示)
- `_persist_fact_check_plus` 3 个解析失败子分支 (`evidence parse failed` / `evidence is not a list` / `no evidence with snippet`) 各落 `RAW OUTPUT: <truncated> | PARSE: <reason>`
- 触发 sprint-07 4 个 fact_check_plus jobs failed 诊断盲区, 修 LLM 回复格式时一眼能看 LLM 实际输出
- 4 新测试 (`test_fact_check_plus_failure_log_*`)

**v1.0.24 — 简报 web 模式** (银行业合规刚需):
- 简报生成时检查 `has_web_fact_check`, 缺则 enqueue fact_check_plus (异步, 不阻塞)
- 模板 (`moni/share/prompts/format-brief-{markdown,html}.md`) 加 "事实核查证据" 段 + 顶部 rollup 计数
- 失败降级不阻塞: 该篇 evidence 标"未联网核查, 证据解析失败" + 简报仍生成
- migration 026: `articles.evidence_rendered_at` (审计) + `has_web_fact_check` (v1.0.22 隐债补)
- 7 新测试 (`test_brief_web_mode.py`)

**v1.0.25 — 看板 fact_check method 分布** (申报书量化数据源):
- `moni/stats/repository.py` 新增 `fact_check_method_layer_stats(period)` SQL 聚合 (7d/30d/90d/all)
- 新端点 `GET /api/stats/fact-check-method?period=...`
- 量化看板第 6 块 SVG 饼图 (3 色: llm_inference=蓝 / web_search=绿 / human=灰)
- 5 新测试 (`test_stats_method.py`)
- 0 篇 web_search 现状下饼图只有蓝色, 待 sprint-08 v1.0.24 简报 web 模式增长后丰富

**已记录 P1** (sprint-09 已完成, 见下方 v1.0.26-28 段):
- ✅ evidence 落库脱敏 (v1.0.28 已做)
- 简报 worker 改用 LLM 检索 (v1.0.24 简报 web 模式同步性升级, P1 留)
- evidence 按 URL 去重 (跨文章同 URL 合并, P1 留)
- ✅ evidence 时效校验 (v1.0.26 已做)
- method × 来源交叉分析 (P1 留)
- 按 method 分的 token 成本估算 (P1 留)
- ✅ 状态机 (v1.0.27 已做)

### v1.0.27 — 事实核查+ 状态机 (sprint-09, 2026-08-13, PR #93)

**业务**:
- 联网核查+ 结论是 LLM 自动生成, 业务人员需能"确认/驳回"留痕 — v1.0.27 加 5 档状态机 (draft → verified → published → deprecated → archived), 单向流转, 复核人 + 备注落库审计
- `articles` 新加 4 字段 (migration 028): `fact_check_state` / `reviewed_by` / `reviewed_at` / `review_note`
- 新端点 `PATCH /api/fact-check-plus/{id}/review` (state + reason + reviewer, 跨档跳转 400)
- 看板 method 分布端点扩 by_state 维度
- 6 新测试 (`test_fact_check_state.py`), 全量 696 passed
- 合规审计: 每条核查结论有"谁最终确认"记录 → 申报书 §4.5 人机协同 + 留痕审计

### v1.0.26 — evidence 时效校验 (sprint-09, 2026-08-13, PR #91)

**业务**:
- 证据链落库后是抓取时刻的检索结果, 时间久了失效 — v1.0.26 加时效校验 (闸 3 timeliness)
- `fact_check_evidence` 新加 3 字段 (migration 027): `timeliness` / `age_days` / `timeliness_flag`
- 规则: < 30 天正常 / 30-90 天标「已过期 30-90 天」/ > 90 天结论强制降级【存疑】
- 端点 GET evidence 返 timeliness + age_days 字段
- 5 新测试, 全量 690 passed
- 银行业合规刚需: 防止引用过时政策/数据

### v1.0.22 — 事实核查+ (sprint-07 历史)

**业务**:
- 联网核查+ v1.0.22 升级: 从 v1.0.10 基础档 (LLM 常识推断 method=llm_inference) 扩展为**联网核查+** (实时检索 + 证据链落库 + 双闸防幻觉)
- `fact_check_evidence` 独立表 (migration 025, 14 列 + 3 索引 + FK CASCADE), 每条结论可按文章 / URL / 检索关键词反查
- 双闸防幻觉: 闸 1 证据片段非空 + URL 合法, 闸 2 权威域白名单 25 域 (pbc.gov.cn / cbirc.gov.cn / sse.com.cn / xinhuanet.com / 主流财经媒体) + 3 正则 (.gov.cn / .sse.com.cn / .szse.cn) 命中才允许【已核实】, 否则强制降级为【待核实】
- 3 端点: `POST /api/articles/{id}/fact-check-plus` (202 + job_id + poll_url) / `POST /api/fact-check-plus/batch` (≤20 篇, 409 防呆) / `GET /api/fact-check-plus/evidence/{article_id}` (按 evidence_seq 升序)
- method 收敛: 'llm' → 'llm_inference' 9 行迁移 (sprint 05 遗留 + sprint 07 配套), `llm_jobs.task_type` 加 `'fact_check_plus'` 白名单
- 修隐债: jobs.py 写库补 `has_fact_check=1` (基础档 v1.0.10 之前漏); 删死 route `/api/llm/fact-check`

**前端**:
- 文章详情区 "事实核查" + "联网核查" 双按钮组 (`.fact-check-plus-actions` flex 布局)
- 证据链折叠 details 视图 (`.evidence-list` ol + `.evidence-item` li, 含 query / url / snippet / domain / model / fetched_at)
- cache-bust bump: main.css + app.js `?v=20260812003` (v1.0.16 教训, 日期+序号格式)
- B3 poll loop: `_pollFactCheckPlusJob` 30×2s, done → 调 `loadArticleEvidence` + 刷新 articleDetail

**测试 / 验证**:
- 14/14 sprint-07 测试 PASSED (test_fact_check_plus.py)
- 全量 pytest 669 passed (baseline 655 + sprint-07 14)
- 浏览器手测 V1-V8 全绿 (B1 CSS 类名 / B2 Alpine state / B3 poll loop / B4 按钮 handler 4 bug 修复后 PASS)
- 端到端 curl 6 项 PASS (cache-bust / DOM / 404 / 405 / 202 / 200)

**已记录 P1**:
- B5: 后端 `_parse_fact_check_plus_evidence` 失败时不持久化 raw_output (诊断盲区), 留 sprint08
- 简报 enrichment `fact_check_mode: web` 模式 + 人工覆写 draft→verified 状态机 + Gemini grounding 备用

**业务**:
- 5 模块量化看板 (抓取 / LLM / 抽取 / 使用 / 效率), 顶部三卡 (基线 / 当前 / 目标 × 1.2)
- 节省分钟数公式 (T₀ yaml 配置 30min/篇保守 + T₁ llm_jobs.duration_ms 实测)
- 趋势图 (抓取 + LLM 双 SVG inline path) + 节省瀑布图 (按 task_type 高度比)
- 防夸大口径: display_status='数据积累中' (N<阈值); efficiency.display_label='公式估算' + t0_source='config/savings_baseline.yaml'
- `moni/stats/repository.py` SQL 聚合 7 张表 (articles / cron_runs / llm_jobs / share_tasks / article_pins / events / cases) — COALESCE(extracted_at, created_at) 防 v0.x pipeline 缺字段
- `moni/stats/savings.py` 节省公式 (T₀ yaml + T₁ 实测)
- `moni/web/routes/stats.py` 8 端点 (overview / fetch / llm / usage / savings / timeseries × 2)
- `config/savings_baseline.yaml` T₀ 配置 (默认 30min/篇 + 反夸大三铁律注释)

**前端**:
- `moni/web/static/js/stats.js` — Alpine module, Promise.all 并行 fetch 4 端点, SVG trend path, waterfall
- `moni/web/static/css/main.css` — `.stats-*` 全套 (+174 行) + 三断点响应式
- `moni/web/static/index.html` — 顶栏 "量化看板" 入口 + section (158 行)
- `moni/web/static/js/app.js` — `...statsModule()` + `statsOnEnter()` 注入

**MOA 4 视角拍板** (A+A+A+A+A+(A+补丁)):
- Q1 模块: A 全 5 模块 (DA 反方采纳 Eng 数据稀疏论据)
- Q2 节省: A 公式估算 (T₀ yaml 不入 DB, 历史口径污染可控)
- Q3 T₀: A 保守 30min/篇 (Brynjolfsson 新手 +34%)
- Q4 布局: A 横排 + 三卡 + 瀑布 (申报截图强制 1440px 视窗)
- Q5 端点: A 4 独立端点 (模块级 ETag 可省 ~60% 流量)
- Q6 稀疏: **A+补丁** (DA 关键盲点: 默认 N<10 会同时砍 events/cases 等效 Q1=B; 修正: events/cases N<5 阈值)

**修复 3 个真实 bug** (跟 brief v1.0.18 → v1.0.19 同步):
- `cron_runs` 字段 = triggered_at / finished_at (非 started_at) — brief 假设修复
- `llm_jobs.status` 枚举 = done (非 success)
- `share_tasks.status` 枚举 = done (非 completed)
- 瀑布图柱子初始显示 4px (parent 高度缺失 → bar-fill height:% 解析失败) — 加 `.stats-waterfall-bar { height: 100% }`

**浏览器验证** (8900 prod):
- 8 端点全 200 OK
- 顶部三卡 (基线 13 / 当前 13 / 目标 16)
- 5 模块横排: 抓取 (92.3% / 861 新文章) / LLM (100% / 29.3s 平均) / 抽取 (稀疏徽章) / 使用 / 效率 (295 分钟节省)
- 趋势图 SVG 紫线 + 瀑布图柱子 (77px / 72px, 高度比 100% / 66%)

**Obsidian**: sprint 06 W1 (`sprints/sprint-06-w1-quant-dashboard/`) 含 brief v1.0.19 (15100 字符) / research w1-reference-2026-08-08.md (6 leaf agents 调研)

---

## 当前状态: **v1.0.17 — F6 同业案例库** (2026-08-08)

**主分支 tag**: **v1.0.17** @ 5ac49e4 (PR #79 squash-merged)
**Web 服务**: `127.0.0.1:8900` 手动重启 (绝对路径 `/usr/local/bin/python3`,不用 web.sh); source=port, PID 60389
**数据库**: schema_version=24 (新增 cases 表 + case_tags + cases_fts, 业务表 11→12, 含 fts 19 张)
**测试**: `tests/test_cases.py` 62 passed (+ 既有 560 = 622 total, 2 skip 已知)

### v1.0.17 — F6 同业案例库 (sprint 05)

**业务 (sprint 05)**:
- 新增 `cases` 表 (case_id / article_id / subject / case_type / case_action / case_date / counterparty / amount / reference_value / summary / evidence_snippet / extraction_confidence / extraction_status / case_lifecycle / review_status + normalize_version + extracted_at + updated_at)
- 8 索引 + 5 CHECK 约束 + UNIQUE(article_id, subject, case_type, case_action) 防重
- 新增 `case_tags` 多对多表 (跟 article_tags 同模式)
- 新增 `cases_fts` FTS5 虚拟表 (trigram tokenizer, 中文友好) + 3 trigger (INSERT/UPDATE/DELETE)
- `moni/cases/` 三层事务管道: normalize.py (100+ 金融机构别名) → repository.py (CRUD + 状态机) → pipeline.py (extract→insert→state-transition)
- case_id 人脑可读 `MN-<2字母分类>-<YYMM>-<4位序号>` 例 `MN-AI-2608-0042`
- `moni/llm/prompts/case_extraction.txt` 3-shot few-shot + JSON schema
- `/api/cases` 7 端点: list / get / by-article / retry / pending / stats / search (FTS5)

**前端 (跟 sprint 04 UI polish 一致风格)**:
- `moni/web/static/js/cases.js` — 同业案例库 Alpine 组件 (列表 + 详情 modal + 搜索 + 筛选)
- `moni/web/static/css/main.css` — `.case-*` 全套 (210+ 行) + `@media (max-width: 1100px)` + 600 双断点
- `moni/web/static/index.html` — 顶栏 "案例库" 入口 + cases section + case_id monospace
- `moni/web/static/js/app.js` — casesModule 注入 + 3 处 init 分支加 cases

**修复 4 个 bug** (sprint 05 过程中踩到):
- `_CURRENT_VERSION` 23 → 24 + INSERT OR IGNORE schema_version (单 PK 不能 UPDATE 全部)
- `_run_sql_file` 用 `executescript()` 兜底 trigger BEGIN/END 解析 (naive `split(";")` 切错)
- `_generate_case_id` YYMM 截错 `[:6]` → `[:7]` (YYYY-MM-DD[:7] = YYYY-MM)
- `cases_fts` trigger 改用业务主键 (case_id) 匹配而非 FTS5 'delete' command (fts rowid 跟 cases rowid 是独立 namespace, 跨表对齐脆弱)

**Obsidian**: sprint 05 (`sprints/sprint-05-f6-cases-library/`) 含 plan / sprint doc / verify-2026-08-08.md (3-1-1) / research (MOA 6 leaf agents) / decision-log 7 条 PDR

### v1.0.16 — F5 行业事件日历 + UI polish

**业务 (sprint 03)**:
- 新增 `events` 表 (event_id / article_id / event_date / subject / event_type / summary / review_status / extraction_status)
- 6 索引 + 5 CHECK 约束 + UNIQUE(article_id, event_date, subject, event_type)
- `moni/events/` 三层事务管道: normalize.py (主体别名 TOP20 银行 + 日期/类型归一化) → repository.py (CRUD + 统计) → pipeline.py (extract→insert→state-transition)
- `moni/llm/prompts/event_extraction.txt` 事件提取 prompt
- `/api/events` 5 端点: list / get / by-article / stats / pending
- 4 时段调度: 7:00 / 8:30 / 12:00 / 18:00 (`config/schedule.yaml` + `cron/jobs.py`)

**前端 (sprint 03 + sprint 04 polish)**:
- `moni/web/static/js/calendar.js` — 月历 Alpine 组件 (网格 / 类型筛选 / popover / 待确认)
- `moni/web/static/css/main.css` — `.cal-*` 全套类 + `@media (max-width: 1100px)` + 600 双断点
- `moni/web/static/index.html` — 顶栏入口 + 日历 section + popover
- **better-ui + better-layout 整改** (sprint 04):
  - "今天" 改 btn-primary 主按钮
  - 右栏顺序倒过来: 待确认 → 统计 (重要在前)
  - nav margin-bottom 24px / weekdays→grid 4px (节奏 4/8/12/16/24)
  - popover max-width min(320, calc(100vw-32)) 边缘格子安全
  - @media 断点 900→1100 (1100-900 区间不崩)
  - 类型色从满饱和 hex 改 OKLCH 软色 + bg/fg chip (与 moni 中日古风调色板统一)
  - 修 popover inline `display: none` 与 x-show 冲突 BUG

**Obsidian**: sprint 03 (`sprints/sprint-03-f5-events-calendar/`) + sprint 04 (`sprints/sprint-04-f5-events-calendar-polish/`) 含 plan / sprint doc / verify (3-1-1) / decision-log 3 条 PDR

### v1.0.15 — PDF / HTML 简报版式与就绪规则

**PDF** (`generate_pdf.py` + `pdf-style.yaml`):
- 摘要/引用：淡橙底色块，去掉粗描边框
- 「优先关注」不再占文章编号 01

**HTML brief** (`brief_html.py`):
- 卡片间距、简单折叠（无 matrix 动效）
- 质量分/业务价值 CSS tooltip；业务判断分数标题同行方框分档色
- 已核实/待核实与正文同行段首
- 信息不足文章撤下并在文末列表说明
- 怪标题（URL/Apply）从正文推导

**API**: 全部 Pin 不可用时 400 `articles_not_ready`；部分可用仍生成

---

## 历史: **v1.0.14 — brief 防呆 + 队列 pause/resume/cancel + LOG_FILE 修复** (2026-08-06)

**主分支 tag**: **v1.0.14** @ c238f6a (PR #74 squash-merged)
**Web 服务**: `127.0.0.1:8900` 经 `web.sh` 重启; source=pidfile
**数据库**: schema_version=22 (无 schema 变更)
**测试**: `tests/test_brief.py` 12 passed (+ 既有 service status 10)

### v1.0.14 — 一键简报连点防呆 + 任务控制 + html 简报失败修复

**问题**:
1. 分享页「用当前 Pin 生成简报」连点 3 次 → 入队 3 份相同任务
2. 队列无暂停/丢弃，误点后只能干等 LLM
3. `html_landing` 简报 enrich 成功后一律 `brief_html render failed`

**修复** (PR #74):
- **防呆**: `POST /api/share/brief` 同 pin 集 pending/running → 409 `duplicate_active_brief`；`force=true` 二次确认后放行
- **前端**: briefSubmitting + 15s cooldown toast + moniModal 确认；队列表/当前简报卡 暂停·继续·丢弃
- **队列 API**: `POST /api/share/tasks/{id}/pause|resume|cancel`；状态 `paused`/`cancelled`
- **Worker**: 阶段边界 abort；`done` 不覆盖用户 cancel
- **LOG_FILE**: `default_worker.sh` 定义 `ARTIFACTS_DIR`/`LOG_FILE`（`set -u` 下未定义导致假失败）

**验收**:
- 二次 brief 无 force → 409；pause/cancel API OK；产物 HTML 200
- 8900 硬刷新 /share 视觉通过

---

## 历史: **v1.0.13.3 — svc-status port fallback** (2026-08-06)

**主分支 tag**: **v1.0.13.3** @ c3da809 (PR #72 squash-merged)
**Web 服务**: `127.0.0.1:8900` 经 `web.sh` 重启 (PID 见 status API); source=pidfile
**数据库**: schema_version=22 (无 schema 变更)
**测试**: `tests/test_service_status.py` 10 passed

### v1.0.13.3 — 顶栏服务状态假「已停止」修复

**根因**: `/api/service/status` 只信 `data/.web.pid`；裸 uvicorn / agent 起服不写 pidfile → UI 显示「已停止」。

**修复** (PR #72):
- `resolve_service_pid()`: pidfile 优先；失效扫 `:8900 LISTEN`（优先 lsof，psutil 次之 + AccessDenied 兜底）
- port 发现后 heal 写回 pidfile，保证 ↻ / `web.sh stop` 杀对进程
- status JSON 增加 `source`: pidfile | port | none
- restart/stop 调用 web.sh 前先 heal

**验收**:
- status: `running:true, source:pidfile`
- 删 pidfile 再 curl → `source:port` + heal，无 500
- 5 菜单端点 200

---

## 历史: **v1.0.13.2 — audit F16 head meta + inline SVG favicon** (2026-08-06)

**主分支 tag**: **v1.0.13.2** @ 5687a34 (PR #70 squash-merged)
**Web 服务**: `127.0.0.1:8900` 已重启 (3.12); `8901` 可选 dev
**数据库**: schema_version=22 (无 schema 变更)
**测试**: 本版仅 `index.html` head 静态变更（无 Python/JS 逻辑）

### v1.0.13.2 — Sprint 补合 F16 + git 仓库自检清枝

**背景**: git 仓库自检发现 Sprint 02 Block C 的 F16（head meta）在旧 feature 枝上未合入 main；旧枝 base 落后，直接 merge 会回退 Q3/a11y/particle。

**代码 (PR #70)**:
- cherry-pick `b91748c` → 干净枝 `feature/v1.0.13.2-head-meta` from current main
- `index.html` head +7/−1:
  - `<meta name="description">`
  - `<meta name="color-scheme" content="light dark">`
  - `<meta name="theme-color">` light + dark
  - `<title>` → `moni — 同业信源本地新闻库`
  - inline SVG favicon（data URL，避免 `/favicon.ico` 404）

**仓库卫生**:
- 本地删 5 条 stale feature 枝
- 远程 prune 9 条已合/取代 feature+docs 枝
- tag 纠偏：曾误打到 docs commit，已 force → `5687a34`
- `.git` ~7.5MB（gc 后）

**验收**:
- main @ `5687a34` 与 tag `v1.0.13.2` 一致
- head 含 description / color-scheme / theme-color / favicon
- Q3 `onArticlesFilterChange` 未回退

---

## 历史: **v1.0.13.1 — fetch modal particle stop + xwatch icon + moni-icon v3.1** (2026-08-05)

**主分支 tag**: **v1.0.13.1** @ 93ae824 (PR #68 squash-merged)
**Web 服务**: `127.0.0.1:8900` 已重启 (PID 69873, 3.12 venv); `8901` 不再需要
**数据库**: schema_version=22 (无 schema 变更)
**测试**: **504 passed, 2 skipped in 59.29s** (基线无回归)

### v1.0.13.1 — Sprint 03 fetch modal particles + source icons

针对 dad QA 的 3 个 frontend 问题修复:

**1. 抓取 modal 粒子动画完成态不停止 (修复)**
- Bug: modal 显示"完成"后背景粒子还在持续动画
- 根因: `_spawnBurst` 内嵌套 `setTimeout` 延迟 burst + `_initParticleStage` 内 `setTimeout(initStageWithSources, 100)` 重试 + `cancelAnimationFrame` 前未先置 `_particleRunning = false` (循环体内会再次申请 RAF)
- 修复 (app.js +162/-21):
  - `_particleRunning` 闸门 (RAF 循环体内 `if (this._particleRunning)` 守卫)
  - `_particleBurstTimers` Set 追踪 `_spawnBurst` 内嵌套 `setTimeout`
  - `_particleInitTimer` 追踪 `initStageWithSources` 的 100ms 重试
  - 统一收尾函数 `_finishFetchAnimation(killGsapRows)` (默认 false, 保留 done 路径的 `_suckIntoMoni` GSAP tween)
  - `closeFetchProgress()` 走 `_finishFetchAnimation(true)` + 显式 `gsap.killTweensOf(moniIcon)`
  - `initStageWithSources` retry 内加 `if (!this.fetchProgressModal) return` gate
- 覆盖路径: 单源 done / 失败 / 超时 / 已中止 + 批量 done / partial / failed + 关闭 modal

**2. xwatch (X 关键词监控) icon (新增)**
- Bug: 5 处 iconMap 都没有 `xwatch` 条目, 全部 fallback 到 36kr.png
- 修复: 256×256 RGBA 60KB 新图 (cobalt blue `#1E40AF` 字母 X + 金色放大镜 + 黑色圆角底 + 金色 bevel + 3D 高光, 与 series 风格对齐)
- 接入 (app.js):
  - `_activeSourcesForModal()` iconMap + labelMap + colorMap + sortOrder (插在 bestblog-x 与 bestblog-yt 之间)
  - `getSourceIcon()`
- colorMap `[30, 64, 175]` cobalt blue 与图标描边同色

**3. moni-icon v3.1 (升级)**
- Bug: v1.0.9.10 moni_v2.png 是黑底纯白扁平漏斗, 与 source icon 系列风格 (黑底圆角 + 金色 bevel + 高饱和纯色 + 拟人化) 不一致
- v1.0.13 第一版 (紫藤萝 solid silhouette, 仿 v1.0.8.3 Pin icon 17px 风格) 被 dad QA reject: "太丑了, 风格不一致"
- v1.0.13.1 = 候选 B (3 层金色 concentric ring 由大到小 + 底部 teal `#14B8A6` 宝石终点), 256×256 RGBA 44KB, 走 series emblem 风格
- `index.html` L471 引用 `moni_v2.png` → `moni_v3.png`
- `moni_v2.png` (406KB) 与 `_rejected-moniv3-wisteria-silhouette.png` 都保留作回退保险

**4. .gitignore 新增**
- `scripts/.pending_*.py` — icon 迭代期间临时脚本不入库
- `docs/moni-icon-redesign-*` — 候选目录不入库

**决策档案**: `docs/moni-icon-redesign-2026-08-05/DECISIONS.md` (含 6 line-art 候选废弃原因 + A/B/C 三选 + image-gen swap 真相)

**验收**:
- pytest: **504 passed, 2 skipped in 59.29s** (基线无回归)
- node --check app.js: PASS
- 8901 dev 5 端点 200 OK
- 8900 prod 已 restart (PID 69873), 5 端点 200 OK
- xwatch 实跑 fetch: 99 抓 / 1 入库 / 137.1s (DDG anomaly + SSL EOF 已知坑, 不阻塞; `tag_definitions` miniprogram row 自动命中)
- 视觉 (dad QA): xwatch 链显示新 cobalt 图, moni-icon 是 3 金环+teal 宝石

---

## 历史: **v1.0.13 — Dialog Certainty (frontend audit-fix)** (2026-08-05)

**主分支 tag**: **v1.0.13** @ 4 PRs squash-merge 后
**Web 服务**: `127.0.0.1:8900` 重启 (3.12 venv); `8901` 验证
**数据库**: schema_version=22 (无 schema 变更)

### v1.0.13 — Sprint 02 Dialog Certainty (frontend audit-fix)

针对 `docs/frontend-arch-a11y-audit-2026-08-05.md` (16 项) 的 9 项修复; 7 项留 Sprint 03+ 路线.
(注: Sprint 02 final PR #67 squash 合并, 把 4 个独立 PR 的 commit squash 成 1 个 v1.0.13 commit)

**Sprint 02 范围 (Q1=A Q2=A Q3=A Q4=A):**

PRs (全部 OPEN → squash merge 成 v1.0.13):
- **#64** `feature/v1.0.13-dialog-certainty-modal-accessibility` — `feat(ui): dialog certainty + modal accessibility (audit F4/F5/F13/F14)`
  - `moni/web/static/js/toast.js` (新) — 33 native `alert()` → `moniToast.{success,error,info,undo}`, 4 档 + sticky + 复制错误
  - `moni/web/static/js/modal.js` (新) — 9 native `confirm()` → `moniModal.confirm()` (role=alertdialog); `moniModal.open()` 自动注入 `role="dialog"` / `aria-modal="true"` / `aria-labelledby`; Tab focus trap; body scroll lock
  - `index.html` — 12 modal 加 `aria-label="关闭"` 6 处 + `@keydown.escape.window` 6 处; 加载 toast/modal 在 app.js 之前
  - `main.css` — 单 toast 改 stack, 加 action button / sticky / 错误红条
  - `app.js` — 新增 `_notify(kind, msg)` helper; 8 destructive 方法改 `async` + `await moniModal.confirm(...)`
- **#65** `feature/v1.0.13-audit-fix-timers-stubs-clean` — `chore(ui): audit fix F2/F3/F6 (stubs + share-poll teardown + busy-wait cap)`
  - 删 5 stub JS 文件 (`articles.js` / `cron.js` / `fetch.js` / `share.js` / `crud.js` — crud.js 一旦加载会静默改 DELETE 行为)
  - `_stopSharePoll()` — `navigate()` / `onPopState()` 离开 `/share` 时显式清理 5s `setInterval`
  - busy-wait cap — 两处 `setTimeout(initStageWithSources, 100)` 加 50 次重试上限 + 错误提示
- **#66** `feature/v1.0.13-audit-fix-headmeta` — `feat(ui): audit fix F16 (head meta + inline SVG favicon)`
  - `<meta name="description">` / `color-scheme` / `theme-color` (light + dark) / inline SVG favicon data: URL
  - `<title>` 改为 "moni — 同业信源本地新闻库"
  - **未做 (F1 app.js 拆块)**: 留 Sprint 03+, 跟 F7 三处 dispatch 树一起做
- **#67** `feature/v1.0.13-dialog-certainty` — Sprint 02 final (Q3 polish + Sprint 02 final commit)
  - `index.html` — 文章 filter `<select>` 加 `class="filter-select"`, `@change` 改 `onArticlesFilterChange()` (避免后页筛选假空态)
  - `main.css` — `.filter-select { height: 35px; box-sizing: border-box; }` (Q3 高度统一)
  - `main.css` (v1.0.13.1) — `.badge` / `.status-badge` 加 `white-space: nowrap` (dad QA 修复: basket `tag 过滤` / config `启用` 不再换行)
  - 5 paths HTML + 3 API 在 3.12 venv 8901 dev 全部 200

**验收**:
- pytest: **504 passed, 2 skipped in 59.74s** (基线无回归)
- node --check toast.js / modal.js / app.js 全 0 错误
- 8901 dev (3.12): 5 paths HTML 200; 3 API 200; filterSelectHeight=35px == searchInputHeight=35px
- 浏览器: `moniToast` / `moniModal` 全局可用, `moniModal.confirm` 渲染 role=alertdialog, 13 modal 自动 ARIA
- 视觉 (dad QA): filter 高度统一, basket/config pill 单行不换行

**Sprint 03+ 候选** (本 sprint 不做):
- F1: app.js 拆 basket / share / llm 模块 (跟 F7 一起)
- F7: navigate / onPopState / init 三处 dispatch 树合并
- F8: pin icon SVG 每次重渲 perf
- F9: source icon 4 处重复 (iconMap / labelMap / colorMap / PNG)
- F10: oklch Relative-Color Syntax 30+ 处 @supports 包装
- F11: 单移动断点 → 多档
- F12: design token SSoT (CSS :root + .dark + theme.js DEFAULT_THEME)
- F15: polling .then() 链堆叠
- Sprint 03 候选: 方向 A 扫读速度 / 方向 C 键盘+导航骨架 / Audit F1-F7
- 已知: pytest 3.14 偶发 segfault (与本 sprint 改动无关, 是 moni 自身 fixture 兼容)

---

## 历史: **v1.0.12.2 — 配置 UI 中文说明** (2026-08-04)

**主分支 tag**: **v1.0.12.2** @ `3925854` (PR #63; 基线 v1.0.12 / .1)
**Web 服务**: `127.0.0.1:8900` 已重启
**数据库**: schema_version=22

### v1.0.12.2 — 配置字段中文说明

- 抓取配置 modal：中文名 + 英文 key + 用法说明；bool/列表/JSON 编辑
- 抓取源 / tag / 过滤 / LLM 表头与表单补中文
- 无 fetch_config 的源（如 36kr_rss）弹窗为空属正常

### v1.0.12.1 — Following 抓完关闭 Profile 6（Dock）

抓取后默认 `stop_x_chrome`（只杀 9222）；CLI `moni xwatch chrome-stop`。

---

### v1.0.12 — xwatch Following CDP + accounts + DDG

**目标**: DDG 不稳时仍能抓 X；Following 条数可配（含 300）。

**已交付:**
1. **Channel C Following** (`xwatch_following.py`)：纯 CDP 滚关注流（Profile 6/9222）；`following_max_posts`/`following_max_scrolls`
2. **Channel A accounts** + **Channel B DDG**（熔断+轮转）
3. CLI：`moni xwatch following --max-posts N [--ingest]`、`moni xwatch health`
4. health/UI ping；seed 含 following_*；依赖 `websocket-client`
5. Grok skill `x-watch-grok`

**验证**: 实机 CDP 56 帖；pytest 504 pass；8900 重启后 health/articles/sources 200

**使用**: Profile 6 登录后 `moni xwatch following --max-posts 100`；可设 300。

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


## v1.0.35.6 (2026-08-20)

**Demo HTML 修复 + 演示物料 UX 优化**

- configModal 区域闭合修复 (1 `</template>` + 3 `</div>`) — info_graphic / html_landing modal 不再被 HTML5 parser 吞掉
- demo mock `/api/share/brief` 返 `share_id` envelope 匹配 prod contract
- modal 宽高按产物尺寸 (modalContent 95vh, modalBody 80vh)
- 7 share formats (跟 prod 一致) + `openArtifact()` gate: html_landing / info_graphic_png 真演示, 其他 5 种弹提示
- 生成产物 6 步骤可见进度 (~7s, fetch → generate → render → factcheck → finalize → done)
- 暗色模式 status badge 改用 `var(--warning-muted)` / `var(--muted)` 取代硬编码浅色
- format chip 标签带 "✓ 演示就绪" / "未演示" 高亮

PR #115 merged (0d87a66), tag v1.0.35.6


## v1.0.35.7 (2026-08-21)

**一键简报 select 默认 HTML 网页**

- `shareFormats` 数组重排 — demo-ready (html_landing, info_graphic_png) 排前 2 位
- 修复 `<select x-model="briefFormat">` 初次渲染显示 "纯文本" 跳变到 "HTML 网页" 的视觉不一致
- `briefFormat` 默认值 (L12314) 一直就是 'html_landing', 这次只调顺序, 不改默认值

commit `e593c73`, share copy 同步到 `~/Documents/moni-share/moni-web-showcase-v1.0.35.6.html` (sha256[:16]=`019426b1c9a18812`)


## v1.0.35.8 (2026-08-21)

**Demo HTML "打开产物" 按钮全黑修复 + 性能优化**

- **按钮全黑根因**: v1.0.35.6 加的 `.panel-soft * { color: var(--foreground) }` 光暗通用, light mode `--foreground`=玄色(#1A1A1A)跟 button 背景同色 → 隐形. 且 dark class 加在 `body` 上, 不是 `:root`, 导致 round-2 scope 修复失效.
- **grok 修复** (herdr pane w4:p1V): `.panel-soft .btn-primary { color: var(--background); }` — specificity (0,2,0) 压过原通配, mode-agnostic, 只影响 打开产物 button.
- **性能**: srcdoc/img 抽到 `<script type="application/json">` + Alpine 按需注入 (deferred), 单文件保留, modal 打开更快.
- **llmUsage 默认值** 改 `{ totals:{}, by_model:[] }` 避免 API 失败时 null 报错.
- commit 链: `6394c6f`(--fg typo) → `cea9e39`(defer) → `ea1a292`(body.dark scope, 失败) → `3353508`(grok fix, 成功). tag `v1.0.35.8`.

share copy: `~/Documents/moni-share/moni-web-showcase.html` (sha256[:16]=`472f6586588366e1`)
## 已发布: **v1.0.78 — Sprint 44 补丁 + 前端缓存修复** (2026-11-13 · Sprint 43–44 · PR #191 · tag `v1.0.78`)

**合并**: `d376079`（squash，base = `main@a1c1566`）· **测试**: 1739 passed / 2 skipped · **服务**: 8900 重启后 200

1. 顶栏三图标 light 下不可见：`.theme-toggle` 34px 白卡 + moon `fill="none"` + stroke 1.6（屏显 1.07px）、
   palette 圆点 `r=0.5`（0.33px）→ 统一 `.btn.btn-ghost.btn-sm`（28px/透明/6px 圆角）、图标 18px、
   stroke 1.8、palette `r=1.2`、moon path 12% 前景填充。
2. `/config` 一级页签在右上（x=875）、二级在左下（x=120）：L1 抽到标题下单独一行，L2 改 `.subtab-bar`，
   两者左边缘统一 x=120。
3. 「后端改了但界面没变」：`index.html` 无 `Cache-Control` 被浏览器启发式缓存 → `serve_root` /
   `spa_fallback` 返回 `index.html` 时带 `no-cache`，`?v=` 提到 `20261113002`。

实测：三按钮 `h=28px` / 背景透明 / `svg width=18` / `stroke-width=1.8`；L1 `x=[120,195,279,380,481]`、
L2 `x=[120,182]`。

## 已发布: **v1.0.79 — LocalOriginGuard 防 CSRF / DNS-rebinding** (2026-11-13 · PR #171 · tag `v1.0.79`)

**合并**: `4f1acef`（squash，base = `main@a1c1566`）· **测试**: 1743 passed / 2 skipped · **服务**: 8900 生效

中间件 `moni/web/security.py`（纯 ASGI）：Host 白名单（loopback / `testserver` / `MONI_ALLOWED_HOSTS`），
非 GET/HEAD/OPTIONS 校验 `Sec-Fetch-Site`（须 `same-origin/none`）与 Origin（须匹配 Host），违规 403
`forbidden_origin`；无 Origin/Sec-Fetch 的 curl/CLI/hermes 直接放行，零前端改动、零 schema 改动。
背景：该 PR 创建于 2026-11-03（原 v1.0.66 计划，Sprint 27），搁置 10 天后于本轮合并。

实现（8900 实测）：伪造 `Host: evil.com`、伪造 Origin、`Sec-Fetch-Site: cross-site` 均 403；正常 GET、
同源 POST、静态资源 200。合并前独立核验：本地试合并树全量 pytest 1743 passed / 2 skipped。

## 已发布: **v1.0.80 — Sprint 45 标签叠加与按钮散落整改** (2026-11-13 · PR #194 · tag `v1.0.80`)

**合并**: `a84a945`（squash，base = `main@0d61aca`）· **测试**: 1743 passed / 2 skipped · **服务**: 8900 重启后 200

dad 点名「各种标签叠加和按钮乱放」。agy 出 design reference（`/private/tmp/ui-audit/s45/ref/index.html`）后落地：
`.tags-cell` 的 `font-size:0` + `max-width:calc(100% - 4px)` 让每个 chip 独占一行，`chinese_independent_blogs`
一行被撑到 **337px**；现改横排 + 单行 ≤2（抓取源）/ ≤3（关键词）+ `+N` 悬停浮层。
表格改 `table-layout: fixed` + `<colgroup>` 定宽，消除「状态」截成 `启…`、文章时间截成 `2025-03-21 0…`。

列语义化：`所属源→作用范围`、`模式→类型`、`匹配字段→匹配范围`、`关键词→命中关键词`，新增「规则说明」列
展示数据库**已有但从未展示**的 `notes` 字段（不动 schema；真正的 `name` 字段需 migration 042，dad 决定暂不做）。
弹窗表单标签同步；抓取配置弹窗补齐 `feed_url` / `*_xpath` / `thresholds` 等此前无 label 的字段。

压层：删 `/config` 8 条孤立「刷新」工具条（`switchConfigTab()` 切页签已自动加载），「新建 xxx」并入标题行，
`网关/用量统计` 降级为内容区左对齐分段控件。`/articles` 的 `formatTime` 去秒 → `YYYY-MM-DD HH:mm`。

8902 实测：抓取源 18 行全 **44px**、状态列 93px/显示名 174px/规则说明 376px/文章时间 160px 全部 0 截断；
`/config` chrome **2 层**、内容起始 y=**186px**（改前 298px）；分段控件 `x=120` 与卡片左对齐。

## 补丁: **v1.0.81 / v1.0.82 — 上线后列宽收口** (2026-11-13 · PR #196 / #197)

8900 上线实测补两处残留截断（8902 复测时未覆盖）：

- `v1.0.81`（PR #196）：`/articles` 操作列 100→120px（`查看 删除` 需 108px，实际 103px）；
  `/config` 抓取源 类型列 110→150px（`wechat_aggregator` 需 142px，实际 116px）
- `v1.0.82`（PR #197）：加宽类型列后操作列被挤到 143px、18/18 行溢出 →
  源标识 190→170、URL 155→150、操作 140→170，总宽仍在容器内

复核（8900，1440×900，按列统计 `scrollWidth > clientWidth`）：显示名 / 类型 / 状态 / 业务标签 /
操作 / 作用范围 / 规则说明 / 命中关键词 / 文章时间 全部 **0 截断**；仅 URL 列（设计省略）与
`chinese_independent_blogs` 这类超长源标识按省略号显示。

## 补丁: **v1.0.83 — STATUS 结构修复 + 显示名 3px 收口** (2026-11-13 · PR #199 · tag `v1.0.83`)

**合并**: `2ab5765`（squash，base = `main@8b3f318`）· **测试**: 1743 passed / 2 skipped · **服务**: 8900 重启后 200

1. `STATUS.md` 前几次合并的冲突残留：v1.0.78 只剩标题（正文被吞）、v1.0.79 与 v1.0.80 标题各重复一次、
   文件末尾留一个截断标题 → 恢复 v1.0.78 正文，按 78→79→80→81/82 排序。
2. 抓取源「显示名」列 cw=166px，`Follow Builders (精选 X)` 需 169px 被截 3px → colgroup 显示名
   165→168px、URL 150→147px（该列本就按省略号显示），总宽不变。

复核（8900 重启后实测）：18 行仍全 44px；显示名 **169px / 0 截断**；类型 151 / 状态 88 / 业务标签 221 /
操作 171 均 0 截断；URL 148 按设计省略。源标识列与 `/articles` 来源列仍对 `chinese_independent_blogs`
省略（dad 决定保持现状）。
