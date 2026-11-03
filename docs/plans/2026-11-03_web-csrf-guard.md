# PLAN — v1.0.66 Web CSRF / DNS-Rebinding Guard

- Sprint: 27 (`26110301`)
- Branch: `feature/v1.0.66-web-csrf-guard`
- Appetite: 1 day (small, security fix)

## 问题

8900/8901 只绑 `127.0.0.1`，零鉴权。浏览器就在本机，任意网页可以：

1. **CSRF**：无 body 或 `text/plain`/form 的 POST 不触发 CORS 预检，浏览器照发。
   `POST /api/service/stop`、`/api/fetch/all/run`、`/api/articles/{id}/summary` 等都会被执行
   （CORS 只拦"读响应"）。
2. **DNS Rebinding**：服务端不校验 `Host`，攻击域名重绑到 127.0.0.1 后在浏览器眼里是同源，
   全量 API 可读可写。

PoC（进程内，临时库）：跨域 `POST /api/service/stop` → 200 且 `Popen` 被调；
`Host: rebind.evil.example` 下 `GET /api/config/sources` → 200、`POST /api/config/tags` → 201。

## 拍板（dad 2026-11-03）

| Q | 选择 |
|---|---|
| Q1 stop/restart 按钮 | A 保留，只靠 CSRF 防护 |
| Q2 无 Origin/Sec-Fetch-Site 的写请求 | A 放行（curl/CLI/hermes） |
| Q3 局域网 | A 不开放，留 `MONI_ALLOWED_HOSTS` 扩展口 |
| Q4 拦截响应 | A 403 JSON `code=forbidden_origin` + warning 日志 |

## 方案

新增 `moni/web/security.py`：纯 ASGI 中间件 `LocalOriginGuard`，在 `create_app()` 最外层挂载。

1. **Host 白名单（所有方法）**：hostname ∈ {`127.0.0.1`, `localhost`, `::1`, `testserver`} ∪
   `MONI_ALLOWED_HOSTS`（逗号分隔），端口不限。不在名单 → 403。
2. **写方法来源校验**（POST/PUT/PATCH/DELETE 及 websocket）：
   - `Sec-Fetch-Site` 存在且不是 `same-origin`/`none` → 403
   - `Origin` 存在且（为 `null` 或 netloc ≠ `Host`）→ 403
   - 两者皆无 → 非浏览器客户端，放行
3. 判定逻辑抽成纯函数 `check_request(method, headers) -> str | None`，便于单测真值表。

## 非目标

- 不改前端 103 处 `fetch()`、不引入 token、不动 schema、不改 CORS 白名单。

## 验收

- 新测试 `tests/test_web_csrf_guard.py` 全绿；全量 pytest 不回退（基线 1387 pass + 2 skip）。
- 8901 上 SPA 正常增删改；跨域恶意页 / 伪造 Host 返回 403。
