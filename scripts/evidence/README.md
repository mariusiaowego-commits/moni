#!/usr/bin/env node
/* scripts/evidence/README — sprint-24 evidence probes
 *
 * These are NOT pytest tests. `pytest tests/` never collects them and CI
 * does not run them. They exist to produce reviewable artifacts for one
 * round of verification; each needs a dev server on a free port and a
 * local Google Chrome, so they cannot run unattended.
 *
 * Kept out of tests/ on purpose (warden L-3): a .mjs file sitting in tests/
 * reads as "this is covered by the test suite", which is false.
 *
 * Usage (from the repo root, with the server already listening):
 *
 *   # 1. which family actually rendered each weight  (needs server)
 *   node scripts/evidence/sprint24_font_probe.mjs \
 *        http://127.0.0.1:8902 docs/sprint-24-evidence/font-platform-probe.json
 *
 *   # 2. is the variable wght axis really applied  (needs server)
 *   node scripts/evidence/sprint24_font_axis_probe.mjs \
 *        http://127.0.0.1:8902 docs/sprint-24-evidence/font-axis-probe.json
 *
 *   # 3. offline first-paint timing + screenshots   (needs server)
 *   node scripts/evidence/sprint24_offline_probe.mjs \
 *        http://127.0.0.1:8902 docs/sprint-24-evidence
 *
 * Each exits non-zero if its own check fails, so they can be chained in CI
 * later if a headless environment is ever wired up.
 *
 * Port hygiene: the probes take the base URL as argv[2] and bind Chrome to
 * fixed debug ports (9333/9334/9335/9336). Check those are free before
 * running, and never point a probe at another sprint's server.
 */

## sprint24_flash_timing_probe.mjs

Measures how long the pin colour flash is actually visible at runtime, on all
three paths (GSAP present / GSAP absent / prefers-reduced-motion: reduce).
Confirms the 700ms pin (dad, 2026-10-10) holds in a real browser, not just in
the source constants.

```bash
node scripts/evidence/sprint24_flash_timing_probe.mjs \
     http://127.0.0.1:8904 docs/sprint-24-evidence/flash-timing-probe.json
```

Needs a dev server and uses Chrome debug port 9336.

## sprint-25 探针 (3 个)

这三个是 sprint-25 (v1.0.45) 的验收探针。**都需要一个 dev server 在跑**,
并且**都需要本机 Google Chrome** —— 它们开 headless Chrome 连 CDP。

⚠️ **它们默认 base 都写死 `http://127.0.0.1:8906`**。用之前先确认该端口上
跑的确实是本分支代码 (prod 8900 跑的是 main, 不能用来验本分支)。

| 脚本 | 用途 | 端口参数 | 退出码 |
|---|---|---|---|
| `s25_fps_probe.mjs <base> <秒数> [out.json] [debug-port]` | 量抓取弹窗粒子场的每帧工作量 / FPS / 超预算帧占比 | 第 4 个参数, 默认 **9342** | 0 (恒 0, 看 JSON) |
| `s25_particle_cap_probe.mjs <base> [debug-port]` | burst storm 压测 MAX_PARTICLES 上限是否真的生效 | 第 2 个参数, 默认 **9343** | 0 = CAP HOLDS / 1 = CAP BREACHED / 2 = 探针失败 |
| `s25_bgroup_verify.mjs <base> [debug-port]` | 真机验 B 组: 页面启动、View Transitions 路径、reduce 门控、焦点环、bulk-bar、行 hover token | 第 2 个参数, 默认 **9344** | 0 = ALL B-GROUP CHECKS PASS |

起 dev server (不要用 prod 8900):

```bash
cd /Users/mt16/dev/moni-worktrees/26101201-motion
./scripts/s25-env.sh /usr/local/bin/python3 -c "
import uvicorn
from moni.web.server import create_app
uvicorn.run(create_app(), host='127.0.0.1', port=8906, log_level='error')
" &
```

跑完记得关掉自己起的 server。**不要碰 8900 prod, 也不要在 worktree 里用
`scripts/web.sh stop`** —— worktree 的 `data/.web.pid` 可能写着 prod 的 PID。

两个口径提醒:

- **FPS 在 headless 里测不出回归**。rAF 被 vsync 锁死, 只要每帧活儿塞得进
  16.7ms 就恒报 60。真正的判据是 `s25_fps_probe.mjs` 打的 `workPerFrameMs.p95`
  (每帧工作量), 不是 FPS。
- **`longFrameRatioPct ≈ 50%` 是 headless + `--disable-gpu` 的 vsync 调度产物**,
  不是应用卡顿率。帧时间恒为 16.7ms 的整数倍, 而 workPerFrameMs.mean 只有 ~2.3ms
  (预算 13.8%)。**不要据此去做 arc 调用 / OffscreenCanvas 优化。**
