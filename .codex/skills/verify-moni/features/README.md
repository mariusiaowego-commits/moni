# moni verification feature map

One file per user-facing surface worth proving before a release. Start here, pick the surfaces your change touched, and follow each file's `Driving it with Playwright` section. Every journey ends in an observable state; a failure is reported, never softened.

| Feature | Route | File | Proves |
|---|---|---|---|
| 文章查阅 | `/articles` | [articles.md](articles.md) | Table renders real rows, detail opens, no console errors |
| 量化看板 | `/stats` | [stats-dashboard.md](stats-dashboard.md) | LLM usage card matches `/api/stats/llm-usage`, trend modules render |
| 脱敏演练台 | `/desens-lab.html` | [desens-lab.md](desens-lab.md) | Four-column preview masks locally, switch defaults off |

Not yet mapped (drive manually if a change touches them): `/fetch`, `/calendar`, `/cases`, `/crud`, `/cleaning`, `/llmJobs`, `/share`, `/basket`, `/config`.

Shared preconditions live in `../SKILL.md` (Launch / Doctor). A journey that needs data the snapshot lacks reports `SKIPPED` with the reason.
