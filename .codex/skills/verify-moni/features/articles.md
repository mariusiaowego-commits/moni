# 文章查阅

## Sub-features

- List with source/tag/keyword filters and pagination
- Row actions (摘要、核查、Pin、删除)
- Article detail with summary, fact-check conclusion, business value

## How to get to it (user POV)

Top nav → 文章查阅, or open `/articles` directly. The list loads on mount.

## Driving it with Playwright

```python
page.goto(f"{BASE}/articles", wait_until="domcontentloaded")
page.wait_for_timeout(2500)
rows = page.locator("table tbody tr").count()
assert rows > 0, "empty article table: check the DB snapshot (Launch step 2)"
```

Screenshot `articles_rows.png`. A run over a fresh worktree without the DB snapshot shows `rows == 0` — that is a setup failure, not a product failure.

## Gotchas

- `/api/articles?limit=` above the cap returns `[]` instead of clamping. Do not hand-check with a huge `limit`.
- Row action buttons live in `.row-actions`; adjacent buttons are `.btn + .btn`. Do not rely on nth-child ordering.
- Clicking a row action enqueues an LLM job (202). Follow it with `/api/llm/jobs/{id}` — see `../scripts/real_model_sample.py` — and only do that with authorization.
- `POST /api/articles/{id}/summary` needs `{"article_id": "<id>"}` in the body even though the id is already in the path. An empty body returns 422 and enqueues nothing, which looks like "summary silently did nothing".
