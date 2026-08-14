# Sprint 13 — Q4 LLM 用量统计 (v1.0.32, 从 0 到真实数据)

## 1. 数据来源: hermes CLI --usage-file(已实测确认)

hermes CLI 支持 `--usage-file PATH` 标志(仅 `-z` one-shot 模式有效),跑完写一个 JSON usage report,包含 `input_tokens` / `output_tokens` / `cache_read_tokens` / `cache_write_tokens` / `reasoning_tokens` / `total_tokens` / `api_calls` / `estimated_cost_usd` / `model` / `provider` / `failed` / `session_id` 等字段,即使运行失败也会写文件(pipelines 总能记账)。已实测成功调用输出 `input_tokens=32740 / output_tokens=26 / estimated_cost_usd=0.0 / api_calls=1`;失败时字段为 null 且 `failed=true`。`hermes chat -q` 子命令**不接受** `--usage-file`,非静默模式的 token/cost 摘要也只出现在 stdout,stderr 无 token 统计 — 因此用量统计统一走 `-z` one-shot + `--usage-file` 路径,stderr 关键字解析作为兜底(命中 `tokens`/`input`/`output` 的行才解析)。

## 2. Schema: migration 029 给 llm_jobs 加用量列

新 migration `029_llm_usage_counters.sql` 给 `llm_jobs` 加三列:`input_tokens INTEGER`、`output_tokens INTEGER`、`cost_usd REAL`(可空,旧行不迁移,统计时 COALESCE 为 0)。`_CURRENT_VERSION` 从 28 → 29。`llm_jobs` 已有 `provider` / `model` / `status` / `started_at` / `finished_at`,按 model 聚合 + 时间窗过滤的条件都齐了,不需要额外索引(数据量在每日几十行量级)。

## 3. 写路径: worker_llm.py run_hermes 返回 dict + jobs.py 写回

`moni/share/worker_llm.py` 的 `run_hermes()` 加 `usage_file` 参数,argv 拼 `--usage-file <path>`,跑完读 JSON 并解析出 `input_tokens` / `output_tokens` / `cost_usd`(失败或字段 null 归 0),返回结构从 tuple 改为 dict:`{stdout, exit_code, input_tokens, output_tokens, cost_usd, usage}`(保留字段兼容,旧调用处解包适配)。`moni/llm/client.py` 的 `call_llm()` 同样加 `usage_file` 可选参数,把 `hermes chat -q` 换成 `hermes -z`(语义等价、支持 `--usage-file`);`moni/llm/jobs.py` 的 `_run_job` 传临时 usage 文件路径,LLM 成功后把三列写回 `llm_jobs`(成功和失败路径都写,失败时值可能全 0)。worker 侧(`default_worker.sh` → `worker_llm.py` CLI)也加 `--usage-file` 参数,生图任务同样记账。

## 4. 看板 API: GET /api/stats/llm-usage

`moni/web/routes/stats.py` 加 `GET /api/stats/llm-usage?period=7d|30d|90d`,SQL 聚合 `llm_jobs WHERE started_at >= <period_start>`,返回 `{period, period_start, totals: {calls, input_tokens, output_tokens, cost_usd}, by_model: [{model, calls, input_tokens, output_tokens, cost_usd}], module: 'llm_usage'}`。聚合逻辑放 `moni/stats/repository.py` 新增 `llm_usage_layer_stats()`,与现有 `llm_layer_stats` 风格一致;period 非法值 fallback 30d。

## 5. 前端: 看板加「LLM 用量」模块

`moni/web/static/js/stats.js` 加 `llmUsage` state + `loadLlmUsage()`(fetch `/api/stats/llm-usage?period=`),`loadAll()` 并行加载;`moni/web/static/index.html` 在事实核查 method 分布模块旁(第 7 块)加「LLM 用量」卡片:顶部 `totals` 三数字(调用次数 / 输入 tokens / 输出 tokens / 花费 $),下方 `by_model` 表格(model / calls / input / output / cost),空数据显示「数据积累中」。测试 `tests/test_llm_usage_counters.py` 覆盖 migration 列、usage JSON 解析、stats API 聚合、前端模块标记。
