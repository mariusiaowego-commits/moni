#!/usr/bin/env bash
# Backfill bb_resource_id for bestblogs articles via BestBlogs API /resources list
set -euo pipefail
cd "$(dirname "$0")/.."

set -a && source .env && set +a

python3 << 'PYEOF'
import json, os, sqlite3, subprocess, time, sys

DB = "data/news.db"
BASE = "https://api.bestblogs.dev/openapi/v2"
KEY = os.environ.get("BESTBLOGS_API_KEY", "")

if not KEY:
    print("FATAL: BESTBLOGS_API_KEY not set in .env")
    sys.exit(1)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# Get articles that need bb_resource_id
rows = conn.execute(
    "SELECT id, url, title, source FROM articles "
    "WHERE (source='bestblogs' OR source LIKE 'bestblog-%') "
    "AND (bb_resource_id IS NULL OR bb_resource_id = '')"
).fetchall()
print(f"Need to backfill {len(rows)} articles")

# Build lookup: URL -> article_id, title -> article_id
by_url = {r["url"]: r["id"] for r in rows}
by_title = {r["title"]: r["id"] for r in rows}
found = 0
page = 1
errors = 0

while errors < 5:
    url = f"{BASE}/resources?limit=100&page={page}"
    r = subprocess.run(
        ["curl", "-sL", url, "-H", "X-API-KEY: " + KEY, "--max-time", "30"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        errors += 1
        print(f"  curl error (attempt {errors}/5): {r.stderr.strip()}")
        time.sleep(3)
        continue

    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        errors += 1
        print(f"  JSON parse error (attempt {errors}/5)")
        print(f"  HTTP {r.returncode}, body[:500]: {r.stdout[:500]}")
        time.sleep(3)
        continue

    # API returns either {"data": [...]} dict or [...] array
    if isinstance(data, str):
        print(f"  API returned string, not JSON object: {data[:300]}")
        errors += 1
        time.sleep(3)
        continue
    items = data if isinstance(data, list) else data.get("data", [])
    if not items:
        print(f"  page {page}: no more items, stopping")
        break

    matched_on_page = 0
    for item in items:
        rid = item.get("resourceId", "")
        item_url = item.get("url", "")
        item_title = item.get("title", "")
        # Try match by URL first, then by title
        aid = by_url.get(item_url) or by_title.get(item_title)
        if aid:
            conn.execute("UPDATE articles SET bb_resource_id=? WHERE id=?", (rid, aid))
            found += 1
            matched_on_page += 1
            # Remove from lookup to speed up subsequent pages
            by_url.pop(item_url, None)
            by_title.pop(item_title, None)

    print(f"  page {page}: {len(items)} items, {matched_on_page} matched (total: {found})")
    page += 1
    time.sleep(0.5)  # rate limit courtesy

conn.commit()
conn.close()
print(f"\nDone. Backfilled {found} bb_resource_id(s). {len(by_url)} articles still unmatched.")
PYEOF
