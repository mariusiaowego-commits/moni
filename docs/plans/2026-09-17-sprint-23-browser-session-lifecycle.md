# Plan: Chrome / BrowserSession 生命周期 (2026-09-17) — sprint-23 草案

- **sprint**: sprint-23 (id `26091701`)
- **slug**: `browser-session-lifecycle`
- **版本**: **v1.0.43**（Q1=A decided 2026-09-17）
- **状态**: **待启动 / Approved for planning** — Q1/Q2/Q3/Q7 decided；Q4/Q5/Q6/Q8 open
- **触发**: `LH260917-MONI-REVIEW` 只读 code review (mini-audit by MiniMax-M3)，A1-A7 + B1-B8 实证 7 条 daemon / 焦点 / 配置问题
- **范围**: 本文件**只是计划**；实现日另开（sprint-23 implement session），今天不写任何 `.py` / `.plist` / `.yaml` 业务逻辑
- **配套**: 本仓镜像 `docs/plans/2026-09-17-sprint-23-browser-session-lifecycle.md` ↔ Obsidian `sprints/sprint-23-browser-session-lifecycle/`

---

## 1. Goal

把 moni 的浏览器调度收敛到**单一 owner + 短生命周期 + 默认不可见**，从根上消除"Chrome daemon 泄漏"、"抢焦点"、"两套 manager"三件套。具体交付：

1. **唯一 `BrowserSession`**：吸收 `chrome_daemon.py` (Profile 7 / 9223) + `xwatch_following.py` (Profile 6 / 9222) 的重复代码；提供 `BrowserSession.start() → use → stop()` context-manager 接口；模块导入一次，所有 fetcher 共享。
2. **默认 `--headless=new`**：解决"chrome_daemon 默认 `--headless` (legacy) 仍弹 dock 图标"问题；headed 仅在 captcha bypass 时打开，且带 mutex + 限时 + 必须 `--disable-features=Translate`。
3. **独立 user-data-dir + 独立 debug port**：`Profile 7` 留 36kr，`Profile 6` 留 X，未来新增源继续按 `Profile N` 严格隔离；`CHROME_USER_DATA_DIR` 不再写死为用户家目录（生产 env-only override）。
4. **短生命周期 + 引用计数 + TTL**：每次 fetch 都在 `try/finally` 内 stop；daemon 空闲 > 5 分钟 → 杀；并发 fetch 用 `flock` 串行化（避免多 Chrome 实例撞端口）。
5. **launchd `KeepAlive=false`** + cron 进程每次 fetch 完退出 → Chrome daemon 必随父进程死亡。
6. **禁 OS 焦点抢占**：代码内不出现 `NSWorkspace` / `osascript activate` / `frontmost`；config/CLI 无 `MONI_*_HEADED=1` 默认（必须显式 `--headed` 一次性）。
7. **36kr_rss（Q2 decided）**：自 v1.0.5.8 起长期 0 产出，但**必须保留并修好，禁止 disable / 砍源**。优先 **httpx + persisted cookies**（或现有 cookie 通路）；captcha 仅短命 headed + mutex，纳入 `BrowserSession`，不当长期 headed。实现落在 Phase 1/2 的抓取路径改造，不在「关源」上解决。

---

## 2. Blocking Questions / Decisions

### Decisions（已拍板 · 2026-09-17）

| # | 问题 | 结论 | 拍板 |
|---|---|---|---|
| **Q1** | 本 sprint 是否升 v1.0.43？ | **A — 升 v1.0.43**（本 sprint 实现完成即 tag） | dad 2026-09-17 — **decided** |
| **Q2** | 36kr_rss 最终归宿？ | **保留并修好，禁止 disable / 砍源。** 优先 **httpx + persisted cookies**（或现有 cookie 通路）；captcha 仅 **短命 headed + mutex**，纳入 `BrowserSession`，不当长期 headed | dad 2026-09-17 — **decided** |
| **Q3** | Phase 0/1/2 一次交付 vs 拆 sub-ship？ | **B — 先合 P0**（生命周期止血 PR）；P1/P2 后续 PR | dad 2026-09-17 — **decided** |
| **Q7** | 是否同意 KeepAlive=false？ | **A — KeepAlive=false**；APScheduler/cron 走单次 exec + 退出 | dad 2026-09-17 — **decided** |

### 仍 open

| # | 问题 | 选项 | 备注 |
|---|---|---|---|
| Q4 | **`scripts/manual_captcha.py` 是否需要 headed 互斥？** | A) 是 — captcha 期间 lock file；B) 否 — 独占 9223 + retry | 与 Q2 一致倾向 A；实现 P0 不阻塞 |
| Q5 | **是否迁移到 Playwright？** | A) 否 — 维持 `BrowserSession`；B) 是 — 后期切换 | **非 36kr 默认路径**（Q2 优先 httpx+cookies） |
| Q6 | **`config/36kr_cookies.json` 是否入 `.gitignore`？** | A) ignore；B) 否；C) secret 模式 | Q2 走 cookies 后更敏感；可随 P0 `.gitignore` 一并答 |
| Q8 | **统一 module name?** | A) `moni.fetch.browser.session`；B) `browser_session`；C) `session` | P1 开干前答即可 |

> 注：**Q1/Q2/Q3/Q7 decided**（2026-09-17）。实现日可按 **Q3=B 先做 P0** 开工；P1/P2 另开 PR。Q4/Q5/Q6/Q8 仍 open。

---

## 3. Assumptions

1. **单一 owner**：moni 当前所有 Chrome 启动点收敛到 2 处（`moni/fetch/chrome_daemon.py:142` + `moni/fetch/xwatch_following.py:201`），加上 `scripts/manual_captcha.py:111` 一处用户手动入口；将来只剩 `BrowserSession.start()` 一个。
2. **数据**：`Profile 7` cookie 来自 `scripts/manual_captcha.py` 一次性登录（`config/36kr_cookies.json` 仅作备份）；`Profile 6` cookie 来自 hermes x-watch 手动登录；切换路径不会动 cookie。
3. **Frequency**：`config/sources.yaml` 6 个源 `fetch_interval_min: 5` 当前是 dead config（schedule.yaml 只 9:10 跑一次）；本 sprint **不修 dead config**，仅记录为技术债（Phase 2 B1 二选一）。
4. **Boundaries**：完全不动 `moni/llm/*`、`moni/db/*`、`moni/web/static/js/*`；仅限 `moni/fetch/browser/*`（新）+ `moni/fetch/{chrome_daemon,browser_harness,xwatch_following}.py`（改）+ `scripts/com.moni.cron.plist`（改 KeepAlive）+ `scripts/manual_captcha.py`（改 mutex）+ `.gitignore`（加 cookies）。
5. **State**：零 DDL、零 migration、零 schema 变更；sprint-22 已用 034/035，本 sprint 无新 migration。
6. **Environment**：`MONI_CHROME_*` env 全部 `os.environ.get()` 模块级读取改为函数级读取（Phase 2 B2）→ 测试可 monkeypatch；生产 .env 注入走 launchd plist `EnvironmentVariables`。
7. **Concurrency**：`moni fetch` 单进程串行调用 fetcher（已 verify）；并发只发生在 (a) 用户手跑 + cron 同一秒 (低概率)，(b) `moni xwatch following` 与 cron 同秒。后者 Q4 mutex 解决，前者 flock 解决。
8. **Headless quality**：macOS Chrome `--headless=new` 比 legacy `--headless` 不弹 dock 图标（Chrome 109+ 实测）；前提 `CHROME_BINARY` 指向系统 Chrome `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`（已是）。
9. **No regression / 36kr**：Q2 要求**继续能抓到内容**；当前 0 产出不能当「允许继续 0」的借口——实现日验收应以「能抓到文章」为准（允许分阶段：先 cookies 通路绿，再稳 captcha）。
10. **Scope lock**：计划日**只写文档**。实现日按 **Q3=B 先合 P0**；P1/P2 另 PR。任何业务文件变更在实现会话进行。

---

## 4. Plan

### Phase 0 — 止血（最小 PR，约 30 min 编码）

**目标**：消除已实测的 daemon 泄漏 + 误杀风险，单 PR 可 ship。

1. **加 `finally` 收尾** — `moni/fetch/browser_harness.py:104-165`
   - `fetch_body_innertext()` 包 `try/finally: stop_chrome()`；
   - 引入"idle TTL"：`_last_used_at` + `_IDLE_TTL_S = 300`（5 min）；`stop_chrome()` 检查 idle 后再杀；
   - 子 fetcher 改为 `with BrowserSession(headed=False) as session:` context manager（Phase 0 inline 实现）。

2. **改 launchd plist `KeepAlive`** — `scripts/com.moni.cron.plist:31-32`
   - `<key>KeepAlive</key><false/>`（cron 任务跑完即退出，子 daemon 随父进程死亡）；
   - `RunAtLoad=true` 保留（开机自启 fetch）；
   - 配套 cron scheduler 改成"单任务跑完即退"模式（不是常驻 APScheduler）。

3. **加 flock 串行** — 新 `moni/fetch/browser/lock.py`（Phase 0 inline）
   - `fcntl.flock(LOCKFILE, LOCK_EX)` 包住 `ensure_chrome + fetch + stop`；
   - LOCKFILE 路径 `$MONI_DATA_DIR/.chrome.lock`，未设置时落 `data/.chrome.lock`；
   - 阻塞超时 30s（避免僵尸卡死）。

4. **改 `stop_chrome()` 不再用 `pkill -f`** — `moni/fetch/chrome_daemon.py:188-191`
   - 改抄 `xwatch_following.stop_x_chrome` (L137-168) 模式：`lsof -iTCP:9223 -sTCP:LISTEN -t` → SIGTERM → 2s → SIGKILL；
   - 防御误杀日常 Chrome。

5. **`.gitignore` 加 `config/*_cookies.json`** — `.gitignore`
   - 防 `git add -A` 误把真实 cookie 入仓（B6 风险）。

**Phase 0 不动**：BrowserSession 抽象、headless=new、36kr 抓取路径改造（Q2）、Playwright。

### Phase 1 — 抽象合并（约 2-3h）

**目标**：把两套 manager 收编成 `BrowserSession` 单类；headless 默认改 `--headless=new`。

1. **新建 `moni/fetch/browser/session.py`**（独立子包 `browser/`）
   ```python
   class BrowserSession:
       def __init__(
           self,
           profile_dir: Path,           # user-data-dir
           debug_port: int,             # --remote-debugging-port
           headed: bool = False,        # 默认 headless=new
           idle_ttl_s: int = 300,       # 5 分钟 idle 杀
       ): ...
       def __enter__(self) -> "BrowserSession": ...
       def __exit__(self, *exc) -> None: ...   # finally stop
       def navigate(self, url: str, settle_s: float = 3.0) -> str: ...
       def eval_js(self, expr: str) -> Any: ...
       def new_tab(self, url: str) -> str: ...
       def ensure(self) -> bool: ...
       def stop(self) -> None: ...
   ```

2. **`chrome_daemon.py` → 薄包装**：内部只剩 `monochrome_browser_session(profile="moni-36kr")` 工厂；保留模块级常量仅作 back-compat import shim。

3. **`xwatch_following.py` → 切到 BrowserSession**：复用 `ensure=True, headed=False` (Phase 1 默认改 headed=False；X 跟 36kr 一样走 headless=new 测试)；保留 `stop_x_chrome()` 但内部实现改为 `BrowserSession.stop()`。

4. **`scripts/manual_captcha.py` 加互斥**：captcha 期间写 `$MONI_DATA_DIR/.chrome.captcha.lock`，fetcher 启动前检查；manual 跑完自动 rm lock。

5. **`--headless=new` 全局默认**：`BrowserSession.__init__` 默认传 `--headless=new`，不再 `--headless`。

6. **常量函数级读取**：把 `chrome_daemon.py:56-74` 的 module-level 常量改为 `monochrome_browser_session(profile=...)` 函数内 `os.environ.get()`，支持测试 monkeypatch（Phase 2 配套）。

7. **新增 `moni/fetch/browser/registry.py`**：维护 profile/port 分配表（Profile 6 / 9222 X；Profile 7 / 9223 36kr；Profile 8 / 9224 future），避免新增源撞端口。

### Phase 2 — 配置 / 可选 Playwright（约 1-2h + 可选 1d）

**目标**：处理死配置 + 可选 Playwright 路径。

1. **dead config 二选一**（B1 决策）：
   - 路线 A：scheduler 真 5 分钟跑一次（APScheduler 加 interval 触发器）；
   - 路线 B：sources.yaml 全改成 1440（一天一次）+ CLI 手跑覆盖；
   - 路线 A 改动小但可能放大 LLM 配额压力；路线 B 改动大但贴近 schedule.yaml 现实。

2. **常量函数级读取 + launchd .env 注入**（B4 / B5）：
   - `com.moni.cron.plist` 加 `<key>EnvironmentVariables</key>` dict 注入 `.env` 内容（手工维护，不要 source .env 脚本）；
   - 验：`launchctl getenv WECHAT_RSS_A_TOKEN` 能拿到。

3. **可选 Playwright 路径**（Q5）：
   - 引入 `playwright>=1.40` 依赖（pyproject.toml）；
   - 新 `moni/fetch/browser/playwright_session.py`，wrapper `BrowserSession`；
   - 36kr_rss 切 Playwright 测试对比 headless Chrome daemon 抓取成功率；
   - 切换成功 → 砍 chrome_daemon / browser_harness 整条路径；
   - 切换失败 → 保留 chrome_daemon 路径，Playwright 仅作 A/B 对照。

4. **36kr_rss 修好路径**（Q2 decided）：落地 httpx+cookies（或现有 cookie 通路）+ captcha 短命 headed；**禁止** disable/砍源。

### 实现顺序

Phase 0 → 全量 pytest (确保无回归) → Phase 1 → 单测 → 全量 pytest → Phase 2 → 文档。

### 验证

```bash
# Phase 0
/usr/local/bin/python3 -m pytest tests/ -q -p no:cacheprovider
ps -ax | grep "Profile 7" | grep -v grep        # → 空（fetch 完即杀）
launchctl list com.moni.cron                     # → KeepAlive=false

# Phase 1
/usr/local/bin/python3 -m pytest tests/test_browser_session.py -v -p no:cacheprovider
monochrome CLI 模式启动 → 跑一次 fetch → 检查 chrome 进程已杀
monochrome headed (manual_captcha) → 检查 flock 生效

# Phase 2 (可选)
playwright install chromium
monochrome playwright → 对比 chrome_daemon 抓取 36kr 成功率
```

### 回滚

- Phase 0：5 个改动互相独立，每个 revert 单 commit；plist `KeepAlive` 改回 `true`。
- Phase 1：保留 `chrome_daemon.py` / `browser_harness.py` 作 back-compat shim；新增 `browser/session.py` 不引用即可回滚。
- Phase 2：Playwright 加 `requirements.txt` 不强；切换失败直接 revert 不动现有路径。

---

## 5. 验收 checklist

### Phase 0 完成判据
- [ ] `monochrome fetch --source 36kr_rss` 跑完，`ps -ax | grep "Profile 7"` 立即为空（验证 daemon 收尾）
- [ ] `monochrome cron start` + 等 10 分钟，`KeepAlive=false` 下进程退出；`ps -ax | grep "Profile 7"` 空
- [ ] `stop_chrome()` 改 lsof 路径后，杀进程不误伤日常 Chrome（实测 9223 + 9222 两个 fake listener）
- [ ] `flock` 验证：两个 `monochrome fetch --source 36kr_rss` 并发 → 第二个阻塞 → 第一个完后第二个启动
- [ ] `.gitignore` 含 `config/*_cookies.json`；`git check-ignore config/36kr_cookies.json` 返 `config/36kr_cookies.json`
- [ ] 全量 pytest 889+ pass (与 sprint-22 baseline 一致；本 sprint 无新增 migration)
- [ ] 手动 captcha 路径仍 work（`scripts/manual_captcha.py` 跑一次能完成 captcha + 落 cookie）

### Phase 1 完成判据
- [ ] `BrowserSession` 单元测试覆盖 enter/exit/idle_ttl/concurrency
- [ ] `chrome_daemon.py` 与 `xwatch_following.py` 内部 import `BrowserSession`；旧 API 仍 back-compat
- [ ] `--headless=new` 默认；headed 仅 manual_captcha 路径用
- [ ] manual_captcha 期间 flock 生效，fetcher 自动 stop / retry
- [ ] profile/port registry 文档化（README + 代码注释）

### Phase 2 完成判据
- [ ] dead config 决策落地（路线 A 或 B）+ STATUS.md 一行指针
- [ ] launchd plist 注入 `.env`；`launchctl getenv WECHAT_RSS_A_TOKEN` 拿到值
- [ ] 36kr_rss 继续产出内容（httpx+cookies 优先；captcha 短命 headed）；STATUS 标注 Q2 decided
- [ ] （若走 Playwright）切换成功率 > 现状，否则保留现状

---

## 6. Out of scope — 今天不实现

本 sprint **只交付计划文档**，以下项目**全部留到实现日另开会话**：

1. **任何 `.py` 文件业务代码变更**（今天零 diff）
2. **`scripts/com.moni.cron.plist` 改动**（Phase 0 才动）
3. **`config/sources.yaml` / `config/schedule.yaml` 改动**（Phase 2 dead config 才动）
4. **`.gitignore` 改动**（Phase 0 才动）
5. **`scripts/manual_captcha.py` mutex 改动**（Phase 1 才动）
6. **新建 `moni/fetch/browser/` 子包**（Phase 1 才动）
7. **Playwright 依赖**（Phase 2 可选）
8. **disable / 砍 36kr_rss 源**（**已否决** — Q2 decided 保留并修）
9. **production 8900 重启 / kill**（任何时候不动）
10. **PR 提交**（实现日另开 PR）

---

## 7. References

### 代码定位（只读引用，不动）

| 文件 | 行 / 符号 | 角色 |
|---|---|---|
| `moni/fetch/chrome_daemon.py` | L102 `ensure_chrome()` / L182 `stop_chrome()` / L56-74 常量 | Profile 7 / 9223 manager |
| `moni/fetch/browser_harness.py` | L89 `fetch_body_innertext()` / L104 `ensure_chrome()` 调用点 | subprocess wrapper |
| `moni/fetch/xwatch_following.py` | L137 `stop_x_chrome()` / L171 `ensure_x_chrome()` / L283 `scrape_following()` / L437-449 finally | Profile 6 / 9222 manager（范本） |
| `moni/fetch/sources/kr_rss.py` | L27 import / L41 `fetch_body_innertext()` / L89 fallback 路径 | 36kr_rss fetcher |
| `scripts/manual_captcha.py` | L42 `MONI_36KR_HEADED=1` / L111 `ensure_chrome()` / L134 `stop_chrome()` | headed manual 入口 |
| `scripts/com.moni.cron.plist` | L31-32 `KeepAlive=true` / `RunAtLoad=true` | launchd job |
| `config/sources.yaml` | L9-39 6 源 `fetch_interval_min: 5` | sources 配置 |
| `config/schedule.yaml` | L18-29 5 jobs (9:10 等) | cron 调度 |
| `docs/BROWSER_FETCHER_PATTERN.md` | 全篇 277 行 | 范式 + 36kr captcha 分析 |

### 来源 review

- `LH260917-MONI-REVIEW`（2026-09-17）MiniMax-M3 只读 audit：
  - A1-A7 (Chrome/focus 7 条)
  - B1-B11 (其它 11 条)
- 父 sprint：sprint-22 (v1.0.42, PR #141 + PR #142) — 安全原语 + 租约 + LLM runner
- AGENTS.md「sprint-22 ship (8 条新坑)」+「v1.0.42 ship」段

### 双写位置

- 代码库：`/Users/mt16/dev/moni/docs/plans/2026-09-17-sprint-23-browser-session-lifecycle.md`
- Obsidian：`/Users/mt16/Library/Mobile Documents/iCloud~md~obsidian/Documents/tqob/05-Coding/project-moni/sprints/sprint-23-browser-session-lifecycle/`
  - `sprint-23-browser-session-lifecycle-2026-09-17.md`
  - `plan-browser-session-lifecycle-2026-09-17.md`

---

## 8. 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| KeepAlive=false 漏掉某些需要常驻的 job | 中 | Phase 0 改 plist 前跑一周观察 cron_runs 表覆盖情况 |
| `--headless=new` 抓取成功率与 `--headless` (legacy) 不同 | 中 | Phase 1 实现后必须 A/B 对比 36kr_rss 真实抓取；FAIL 即回滚 legacy |
| BrowserSession 抽象引入新故障点 | 中 | Phase 1 保留 `chrome_daemon.py` 作 back-compat shim；新模块故障可立即切换回旧路径 |
| flock 阻塞导致 cron 任务超时 | 低 | 阻塞超时 30s + cron timeout 60s 兜底；超时即 SIGTERM |
| Playwright 依赖膨胀 | 低 | Phase 2 可选；切换成功才加 requirements.txt；切换失败不动 |
| KeepAlive=false 后 cron 漏跑 | 中 | Q7=A 已定；实现日验证单次 exec + RunAtLoad；漏跑则补 schedule 告警 |
| 36kr_rss 修好后仍 0 产出 | 中 | Q2 已定保留；实现日验证 httpx+cookies；captcha 仅短命 headed + mutex |
