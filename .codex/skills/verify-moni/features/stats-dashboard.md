# 量化看板

## Sub-features

- 抓取 / LLM / 效率 / 成果 four module cards
- LLM 用量 card: calls, input/output tokens, cost, per-model table
- 同步趋势 sparkline, fact-check method pie

## How to get to it (user POV)

Top nav → 量化看板, or open `/stats`. `statsOnEnter()` triggers `loadAll()`.

## Driving it with Playwright

```python
page.goto(f"{BASE}/stats", wait_until="domcontentloaded")
page.wait_for_timeout(6000)          # loadAll fans out 7 requests
card = page.locator("#stats-llm-usage")
rendered = card.locator(".stats-module-primary").inner_text().strip()
api = requests.get(f"{BASE}/api/stats/llm-usage?period=30d").json()
assert rendered.startswith(f"{api['totals']['calls']:,}") or rendered.startswith(str(api["totals"]["calls"])), (
    f"card says {rendered!r}, API says {api['totals']['calls']} calls"
)
```

Compare the rendered number against the API body, not against a hard-coded constant.

## Gotchas

- The card reads `statsLlmUsage` (from `stats.js`, `/api/stats/llm-usage`). `/config` → 大模型设置 → 用量统计 reads `llmUsage` (from `app.js`, `/api/llm/usage`). They must stay separate keys; when they collided, the card rendered 0 while the API reported 57 calls.
- `x-show="!llmUsage"` in the config tab intentionally uses `null` as "not loaded". Do not "fix" it to an object.
- Attach the console listener once. Nine `reading 'totals'` errors on this page were the symptom of the collision above; if they reappear, check the state names before touching the template.
