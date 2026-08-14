# Sprint 12 — PNG 生图流程重新设计 (v1.0.31)

## Goal

PNG 生图流程 (info_graphic_png / report_png) 从"半成品靠运气"重构成**生产可用**: 不依赖用户默认 provider, 不因模型账号没钱而失败但 UI 显示 done, PNG 真图能可靠产出。**同时**修关联问题 (Q3 URL 仍用 /articles, Q4 看板单色, Q5 call_llm module kwarg)。

## Blocking Questions (Q1-Q4 拍照 + Q1 PNG 拍板 4 个)

- **Q1-重修 A. moni worker 强制显式 --provider/--model (不依赖 ~/.hermes/config.yaml)**
  - 每次调 hermes 都传 `--provider X -m Y` (从 DB `llm_models` + `llm_providers` 读)
  - 默认用 LLM settings 里的 `active_summary_model_id`
  - **效果**: dad 切默认 provider 不影响 moni 生图
  - **推荐 A** ✓

- **Q1-重修 B. moni worker 加 LLM 失败检测 (看 stdout 关键字)**
  - hermes 返回 0 但 stdout 含 "API call failed" / "HTTP 4xx" / "rate limit" / "insufficient credits" → status=failed
  - 错误消息落 DB (share_tasks.error)
  - **推荐 B** ✓

- **Q1-重修 C. 配置 hermes fallback chain (hermes fallback add minimax-cn MiniMax-M3)**
  - 父 agent 给 dad 直接配好 fallback chain (monimax-cn → google gemini-2.5-flash-lite)
  - 即使 moni 改 worker, 默认 nos 也兜底到 minimax-cn
  - **推荐 C** ✓

- **Q5 call_llm() 'module' kwarg bug**
  - 修前端 (config_llm 页面) 测试按钮调 call_llm() 时不传 `module`
  - 或修后端 `moni/llm/jobs.py` `def call_llm(...)` 接受 `**kwargs`
  - **推荐: 修前端** (后端是公共 API, 加 kwargs 可能掩盖其他 bug)

## 拍板合并 (本次 sprint 范围)

**核心** (sprint-12 main):
1. **A + B + C** (PNG 生图流程重新设计, 3 个 fix)
2. **D call_llm kwarg** (Q5, 跟 LLM 配置相关, 顺手)

**挂起** (等 PNG sprint 修完再激活):
- Q3 量化看板 URL 改 /stats 独立 (大改, 跟 SPA 架构相关, 不属于生图 sprint)
- Q4 量化看板单色提示 (数据真实, 加提示文字属 UI 改进, 跟生图无关)

## Assumptions

1. **数据**: llm_models + llm_providers 已有 5+1 个 provider/model seed 数据, schema 不用改
2. **失败**: hermes API 错误 = stdout 含 "API call failed" / "HTTP 4xx" (观测样本 1 个, 需要更多样本但这是合理启发)
3. **超时**: 现有 600s 不变
4. **边界**: 用户没 override provider → 用 LLM settings `active_summary_model_id`
5. **环境**: hermes CLI 0.5+ (已有), Python 3.12, fallback chain 用 hermes fallback add 命令配置
6. **测试**: 单测用 mock subprocess.Popen, 模拟 hermes 返回不同 stdout
7. **范围**: moni worker bash + tests + 一次性 setup script 配 hermes fallback
8. **降级**: 配 fallback 后如果全部失败 → status=failed (优雅), 不会再"假 done"

## Plan (5 个 deliverable)

1. **moni worker bash**: `scripts/default_worker.sh` 改 hermes 调用
   - 从 DB `llm_settings.active_summary_model_id` + `llm_models` + `llm_providers` 读 provider/model
   - 拼 `--provider <p> -m <m>` 显式传
   - 加 LLM 失败检测: stdout 含 "API call failed"/"HTTP 4xx"/"HTTP 5xx" → return 1 (跟 timeout 一样走 fail)
2. **setup script**: `scripts/setup-hermes-fallback.sh`
   - `hermes fallback add minimax-cn MiniMax-M3` (父 agent 跑, dad 确认)
   - `hermes fallback list` 验证
3. **tests**: `tests/test_worker_llm_failure.py` 新文件
   - mock subprocess, 测 stdout 含 "API call failed" → status=failed
   - 测 stdout 含 "HTTP 404" → status=failed
   - 测正常输出 → status=done
4. **D 修 call_llm**: `moni/web/static/js/config-llm.js` 找测试按钮 → 去 `module` kwarg
5. **Verify**: 跑全量 pytest + restart 8900 + 真触发 info_graphic_png (应该产出真 PNG)

**Alternative considered and rejected**:
- 改 ~/.hermes/config.yaml 的 provider default — rejected, dad 改的话会反复触发, 不如 moni 自己用 DB 显式传
- 改 frontend config UI 让用户选 — rejected, 用户不该每次生图选模型
- 监听 stderr (而非 stdout) — rejected, hermes 错误走 stdout (已验证)
