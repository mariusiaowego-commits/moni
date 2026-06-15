# Browser Fetcher Pattern — 从 x-watch 学到的浏览器调度套路

> 给 moni coder 看的参考文档。讲清楚 x-watch 是怎么调度浏览器去抓 X.com 的，
> 36kr / 任何反爬严格的源能不能套这套，以及套的时候踩什么坑。

---

## TL;DR

**x-watch 不走 agent 工具栈，自己起 Chrome daemon。** 三个关键点：

1. **headless Chrome + Profile 6 + `--remote-debugging-port=9222`** —— 自己 `subprocess.Popen` 拉起，不靠 hermes `browser_navigate`
2. **`BU_CDP_URL=http://127.0.0.1:9222` 配 `browser-harness -c ...`** —— hermes 自带的 CDP 客户端 CLI
3. **Profile 6 里持久化登录态 cookie** —— 这是 X 不弹 captcha 的根本原因

套到 moni 抓 36kr 同样可行，但要注意 36kr 的反爬是字节跳动 TTGCaptcha 滑块（aid=331981），X 是登录后放行，两者机制不一样。详见后文。

---

## x-watch 浏览器调度的完整链路

### 1. Chrome daemon 生命周期（自管自）

`~/.hermes/scripts/x-watch.py` 里有这套基础设施：

```python
CHROME_DEBUG_PORT = 9222
CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_USER_DATA_DIR = os.path.expanduser(
    "~/Library/Application Support/Google/Chrome/Profile 6"
)

def ensure_chrome():
    if chrome_is_running():           # 9222 端口有响应 → 在跑
        return
    # 否则杀残留 → 起新的 headless Chrome → 轮询端口就绪
    subprocess.Popen([CHROME_BINARY,
        "--remote-debugging-port=9222",
        "--user-data-dir=" + CHROME_USER_DATA_DIR,
        "--no-first-run", "--no-default-browser-check",
        "--headless", "--window-size=1280,800",
        "--disable-extensions", "--disable-gpu",
        "--remote-allow-origins=*"],
        stdout=open("/tmp/chrome-profile6-stderr.log", "a"),
        stderr=subprocess.STDOUT)
    for _ in range(10):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{CHROME_DEBUG_PORT}/json/version", timeout=1)
            break
        except: continue

def stop_chrome():
    subprocess.run(["pkill", "-f", "Chrome.*Profile 6.*headless"], capture_output=True)
```

**关键点：**
- `atexit.register(stop_chrome)` 在脚本退出时自动杀 Chrome，避免僵尸
- Profile 6 是个独立的 user-data-dir（不是默认 Chrome profile），cookie 持久化在这里
- **必须用完整 binary path**，`chrome --remote-debugging-port` 在 macOS 会复用已开窗口不开 port
- `--headless` 让 Chrome 在后台跑没窗口
- `--remote-allow-origins=*` 是 Chrome 111+ 必需，否则 CDP 协议会被拒

### 2. CDP 调用（走 browser-harness）

`browser-harness` 是 hermes 自带的 CDP 客户端 CLI（路径在 PATH 里）。两个用法：

```bash
# 单条命令
browser-harness -c 'cdp("Page.navigate", url="https://x.com/home?following=true")'

# 跑一段 Python（含 js / cdp / switch_tab / list_tabs / new_tab / page_info）
browser-harness -c 'import time; tid = new_tab("https://x.com/..."); time.sleep(6); print(tid)'
```

环境变量 `BU_CDP_URL=http://127.0.0.1:9222` 必须设，否则 browser-harness 不知道连哪个 Chrome。

### 3. 在 Python 脚本里调 browser-harness

x-watch 把整段交互封装成一个 `subprocess.run(['browser-harness', '-c', script])`：

```python
env = {**os.environ, "BU_CDP_URL": f"http://127.0.0.1:{CHROME_DEBUG_PORT}"}
r = subprocess.run(
    ["browser-harness", "-c", "import time; tid = new_tab(URL); time.sleep(6); print(tid)"],
    capture_output=True, text=True, env=env, timeout=60
)
tab_id = r.stdout.strip()
```

### 4. JS 提取（X 虚拟化 DOM 兼容）

**X.com 用虚拟化列表，scroll 后旧帖从 DOM 移除。** 所以 x-watch 边滚边取：

```python
for i in range(n_scrolls):
    subprocess.run(["browser-harness", "-c", "js('window.scrollBy(0, 2000)')"], ...)
    time.sleep(2)
    r = subprocess.run(["browser-harness", "-c", f"js({EXTRACT_JS!r}); print(...)"], ...)
    # 累积去重
```

EXTRACT_JS 是单行 JS，从 `article` 元素里抽 `account / time / text / url / images`。完整代码见 `~/.hermes/scripts/x-watch.py:66`。

**session 隔离注意：** 每次 `browser-harness -c "..."` 是独立 CLI invocation，`switch_tab` 效果不跨调用保持。必须在**同一 session** 内完成所有滚动+提取操作 —— 写一个 Python 文件 `exec(open(path).read())` 注入。

---

## 套到 moni 抓 36kr 的可行性分析

### 36kr 的反爬机制（实测 2026-06-07）

裸 `httpx.get` 抓 `https://36kr.com/newsflashes`：

```
status: 200
len:   1460
body:  <html><head>...</head>
        <script src="https://lf-cdn-tos.bytescm.com/obj/static/
                     sec_sdk_build/3.3.4/captcha/index.js"></script>
        <script>
        const options = {
          commonOptions: { aid: 331981, iid: '0', did: '0' },
          captchaOptions: {
            showMode: 'mask',
            host: 'https://netsec-img-req-cn.zijieapi.com',
            fp: 'MTE0LjkyLjE5MS4zNXwzMTM5MzY4',
            ...
          }
        }
        window.TTGCaptcha.init(options)
        ...
```

**字节跳动安全 SDK，TTGCaptcha，slide 滑块验证。** aid=331981 是字节内部业务 ID，host `netsec-img-req-cn.zijieapi.com` 是验证图片/轨迹上传地址。**任何无 cookie 的服务器端请求 100% 弹**。

`https://36kr.com/feed`（RSS）也是同样 captcha 页面 —— 现在 36kr 的 CDN 层（`netsec-img-req-cn.zijieapi.com` 之前应该是 nginx + WAF）会先检查请求指纹，命中就整页替换。

### 三种应对策略

#### 策略 A：复用 x-watch 模式起 Chrome daemon

**可行，但有坑。**

新 fetcher 大致长这样（伪代码）：

```python
class Fetcher36krBrowser(BaseFetcher):
    source_name = "36kr_browser"  # 新源，不覆盖现有 36kr_newsflash

    def fetch(self):
        # 1. ensure_chrome(Profile 7)  ← 必须用新 Profile 隔离 x-watch
        # 2. new_tab("https://36kr.com/newsflashes")
        # 3. sleep 8 等 JS 跑 + 首次 captcha（如果有）
        # 4. if 检测到 captcha: return [] （跟现状一样 graceful degrade）
        # 5. else: 边滚边取 soup.find_all('div', class_='newsflash-item')
```

**坑：**
- **Profile 6 现在是 X 专用 cookie 仓**。x-watch 杀 Chrome 时 moni 抓取会断 → **必须 Profile 7 隔离**
- **第一次跑 36kr 还是要过滑块**。要 headed 模式弹窗让你手动滑一次，cookie 落盘后切 headless 复用
- **被 ban 是按 fp（设备指纹）+ IP 算的**。Profile 7 整个废了得换 Profile 8
- **moni 跟 x-watch 抢 Chrome 端口**。如果 moni 起 daemon 时 x-watch daemon 也在跑 9222，端口冲突 → moni 失败。moni 得用 9223 或先检测 x-watch 是否在跑

#### 策略 B：第三方反 captcha 服务

**最省心。** TTGCaptcha 有商业化服务支持（2Captcha / YesCaptcha / Anti-Captcha 等都有 TTGCaptcha 适配器）。

```python
# 伪代码
def solve_ttg_captcha(page_url: str) -> str:
    """调第三方服务解滑块，返回验证 token cookie"""
    task_id = create_task(page_url, captcha_type="ttg_slide")
    for _ in range(60):  # 等最多 60s
        result = poll_task(task_id)
        if result.ready:
            return result.cookie_token
        time.sleep(1)
    return None

class Fetcher36krSolved(BaseFetcher):
    def fetch(self):
        token = self._get_or_refresh_token()  # 缓存 1h
        cookies = {"ttg_token": token}
        resp = httpx.get("https://36kr.com/newsflashes",
                         cookies=cookies, headers=DEFAULT_HEADERS, timeout=15)
        # ... 正常解析
```

**成本：** 0.001-0.01 USD/次。36kr 5 分钟一次 = 每天 288 次 = 几分钱/天。
**优点：** 纯服务器端，不需要 Chrome daemon，不需要手动过 captcha。
**缺点：** 依赖第三方服务可用性 + 字节更新 SDK 适配器可能滞后。

#### 策略 C：放弃 36kr，封源

**最实际。** moni 现在 5 个源（mpaypass / qbitai / wechat_rss / 36kr_rss / 36kr_newsflash），其中 4 个正常抓，36kr 两个源都 0 items。fetcher 已经在 graceful degrade：

> **v1.0.5.8 outcome**: 36kr_rss 走 captcha-bypass 真 Chrome 路径修好 (v1.0.5.5, 35 articles)。
> 36kr_newsflash 因为 React 客户端渲染 + captcha 限制, 抓取没修通; 用户决定**彻底删除**
> (不再作为源), 36kr_rss 已经覆盖 36kr 全部内容。 见 STATUS.md v1.0.5.8 段。

```python
# kr_newsflash.py:84-91
if "TTGCaptcha" in resp.text or "captcha" in resp.text.lower() and len(resp.text) < 5000:
    logger.warning("[36kr_newsflash] 36kr returned a captcha challenge page ...")
    return []
```

但每次跑都打 warning 日志 + 2 个 pytest fail + cron 输出噪音。**直接 `enabled: false` 两个 36kr 源 + 在 STATUS.md 标注「36kr captcha blocked, pending 反爬策略决定」**。

---

## 给 coder 的建议（如果 moni 真要支持 36kr）

**第一步：封源 + 标 STATUS。** 立即见效，省 cron 噪音，省 pytest fail。

**第二步：看 moni 的真实使用场景。** 36kr 对你 moni 来说是"补全"信源还是"核心"信源？如果是补全（4 个源够用），永远别折腾 36kr。如果是核心（比如给客户做行业日报必须 36kr），才值得花精力。

**第三步：如果非抓不可，按优先级试：**
1. 策略 B（第三方 captcha）最稳，但要先确认字节 SDK 第三方能解（实测再下结论，别信广告）
2. 策略 A（Chrome daemon）工程量大，且 ban 风险高，长期维护成本不划算
3. 兜底：直接换源，比如机器之心、虎嗅、亿邦动力等替代品，反爬弱

---

## 如果 moni 决定走策略 A（复用 x-watch 模式）

实施 checklist：

1. **新建 Profile 7**：
   ```bash
   mkdir -p "$HOME/Library/Application Support/Google/Chrome/Profile 7"
   ```
   首次 headed 模式跑一次 36kr，手动过滑块，cookie 落盘。

2. **新 fetcher 放 `moni/fetch/sources/kr_browser.py`**，用 Profile 7 + 9223 端口：
   ```python
   CHROME_PORT_36KR = 9223
   PROFILE_DIR_36KR = "~/Library/Application Support/Google/Chrome/Profile 7"
   ```

3. **确保 x-watch daemon 在跑时 moni 不抢端口**：
   ```python
   def ensure_chrome_36kr():
       if port_in_use(CHROME_PORT_36KR): return
       if port_in_use(9222):  # x-watch 在跑
           logger.warning("x-watch Chrome on 9222, moni 36kr uses 9223")
       # 起 9223 daemon
   ```

4. **moni 的 config/sources.yaml 加新源**：
   ```yaml
   - source_name: 36kr_browser
     type: html
     feed_url: https://36kr.com/newsflashes
     fetch_interval_min: 30
     enabled: false  # 首次需 headed 手动过 captcha 才 enable
     crawler_type: browser
   ```

5. **测试：先把现有 36kr_rss / 36kr_newsflash 标 disabled**，等新 fetcher 验证 OK 再切换。

---

## 关键参考

- x-watch 主脚本：`~/.hermes/scripts/x-watch.py`（245 行，含完整 Chrome 生命周期 + EXTRACT_JS + 虚拟化 DOM 兼容）
- x-watch SKILL.md：`~/.hermes/skills/x-watch/SKILL.md`
- 36kr captcha 检测代码：`/Users/mt16/dev/moni/moni/fetch/sources/kr_newsflash.py:84-91`
- moni fetcher base：`/Users/mt16/dev/moni/moni/fetch/base.py`（268 行，`run()` 串起 fetch → parse → cleaner → filter → dedupe）
- moni 36kr RSS fetcher：`/Users/mt16/dev/moni/moni/fetch/sources/kr_rss.py`
- moni 36kr newsflash fetcher：`/Users/mt16/dev/moni/moni/fetch/sources/kr_newsflash.py`

---

**作者注：** 这份文档是 agora profile 的 me（MiniMax-M3）根据用户 2026-06-07 询问
"x-watch 怎么调浏览器" + moni 36kr captcha 卡点 整理的。moni 改造决策权交回 coder。
