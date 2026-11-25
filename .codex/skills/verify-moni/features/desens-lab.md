# 脱敏演练台

## Sub-features

- 四栏对照: 原文 / 遮蔽后 / 模型回包还原 / 状态
- `GET /api/desensitize/stats` 审计账本（遮蔽处数、策略、耗时）
- 总开关（L1/L2/L3 三层）默认关

## How to get to it (user POV)

Open `/desens-lab.html` in the browser (standalone page, not a SPA route). Load the built-in sample, then 预览 to mask locally.

## Driving it with Playwright

```python
page.goto(f"{BASE}/desens-lab.html", wait_until="domcontentloaded")
page.click("#sample")
page.click("#preview")               # POST /api/desensitize/preview — local only, no network
page.wait_for_function("document.getElementById('status').textContent.includes('遮蔽')", timeout=20000)
c1, c2, c3 = (page.inner_text(s) for s in ("#c1", "#c2", "#c3"))
assert page.locator(".cols .col").count() == 4
assert "13800138000" in c1 and "13800138000" not in c2 and "{{" in c2
assert c3.strip() == "—"             # 未跑真实模型时第三栏为空
assert "总开关 关" in page.inner_text("#switchState")
```

`#preview` is the safe path: `POST /api/desensitize/preview` masks locally and never calls a model. `#run` calls `/api/desensitize/run`, which does hit the model and writes audit rows — authorization required.

## Gotchas

- The lab is `/desens-lab.html`, not `/static/desens-lab.html` (both may 200; prefer the former).
- 总开关默认关 is a product decision. If a run shows 开, check `/api/desensitize/stats` and `llm_settings` before calling it a regression.
- The audit ledger excludes lab-originated calls from production counts (`call_site=desens_lab`). Do not compare lab rows against production totals.
