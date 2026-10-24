# moni/web/static/vendor — 自托管第三方库

v1.0.47 起，Alpine.js 与 GSAP 从 CDN 改为本地自托管。

## 为什么

原来两个库都从 `cdn.jsdelivr.net` 加载（`index.html` 的 preload +
defer script 两处）。内网隔离、外网 DNS 被污染、或者 CDN 故障时，
Alpine 根本加载不出来 —— `x-cloak` 让整页持续空白，SPA 全瘫。
STATUS.md 早就记过这个离线空白页问题，这次是根因修复，不是绕过。

项目里已有同款先例：Inter 字体在 v1.0.44 就已经改成 `@font-face`
自托管（见 `../fonts/`）。

## 文件清单

| 文件 | 版本 | 许可 | 大小 |
|---|---|---|---|
| `alpine-3.14.1.min.js` | Alpine.js 3.14.1 | MIT | 44,659 B |
| `gsap-3.12.5.min.js` | GSAP 3.12.5 | GreenSock Standard "No-Charge" | 72,214 B |

许可全文：
- `LICENSE-ALPINE.txt`（MIT，Caleb Porzio 及贡献者）
- `LICENSE-GSAP.txt`（GreenSock 标准许可 + 合规说明）

## 来源与校验

| 文件 | 来源 URL | SHA-256 |
|---|---|---|
| `alpine-3.14.1.min.js` | `https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js` | `358d9afbb1ab5befa2f48061a30776e5bcd7707f410a606ba985f98bc3b1c034` |
| `gsap-3.12.5.min.js` | `https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js` | `28033e449a31ebcc396e5be8b13b63152bf03094288fb5867034321927bce087` |

两份文件的 SHA-256 同时被
`tests/test_frontend_safe_href_and_vendor.py::test_vendor_asset_hash_is_pinned`
钉死 —— 任何未经评审的二次修改 / 重新压缩都会让测试变红。

## 版本钉死策略

- **文件名带显式语义版本**：`alpine-3.14.1.min.js` / `gsap-3.12.5.min.js`。
  严禁 `alpine.min.js` 这类无版本裸名，也不做软链接 —— 升级必须是
  "新文件 + 改 index.html + 改测试哈希"三步可见动作，不能静默漂移。
- **测试断言即锁**：哈希写死在测试常量里，改文件不改测试 = 测试红。
- **SHA-512 / SRI 备查**：
  - Alpine `sha512-base64` = `ytM6hP1K9BkRTjUQZpxZKFjJ2TvE4QXaK7phVymsm7NimaI5H09TWWW6f2JMbonLp4ftYU6xfwQGoe3C8jta9A==`
  - GSAP `sha512-base64` = `7eHRwcbYkK4d9g/6tD/mhkf++eoTHwpNM9woBxtPUBWm67zeAfFC+HrdoE2GanKeocly/VxeLvIqwvCdk7qScg==`

## 升级流程

1. 从上游下载目标版本的 `cdn.min.js` / `gsap.min.js`
2. 用新版本号命名放进本目录
3. 更新 `index.html` 的 4 处引用（2 个 preload + 2 个 script）
4. 更新 `tests/test_frontend_safe_href_and_vendor.py` 的 `EXPECTED_SHA256`
5. 更新本 README 的版本 / 哈希两列
6. 跑 `pytest tests/test_frontend_safe_href_and_vendor.py tests/test_frontend_render_hygiene.py`

## 离线验证

`scripts/evidence/sprint24_offline_probe.mjs` 会用 CDP 阻断外网，
然后断言 `window.Alpine` / `window.gsap` 仍然加载成功、`x-cloak`
清零。自托管之后这个探针才第一次真正测到"离线可用"。
