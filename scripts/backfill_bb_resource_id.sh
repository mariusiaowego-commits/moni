#!/usr/bin/env bash
# Backfill bb_resource_id for bestblogs articles via API
set -euo pipefail
cd /Users/mt16/dev/moni

set -a && source .env && set +a

python3 -c "
import json, os, sqlite3, subprocess, time

DB = 'data/news.db'
BASE = 'https://api.bestblogs.dev/openapi/v2'
KEY = os.environ.get('BESTBLOGS_API_KEY', '')

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    'SELECT id, title FROM articles '
    'WHERE (source=\"bestblogs\" OR source LIKE \"bestblog-%\") '
    'AND bb_resource_id IS NULL'
).fetchall()
targets = {r['title']: r['id'] for r in rows}
print(f'Need to match {len(targets)} articles')

matched = {}
page = 1
errors = 0

while errors < 5:
    url = f'{BASE}/resources?limit=100&page={page}'
    header = 'X-API-KEY: *** .join(['X', 'API', 'KEY', ':', KEY])
    r = subprocess.run(
        ['curl', '-sL', url, '-H', header, '--max-time', '30'],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        errors += 1
        time.sleep(3)
        continue
    try:
        data = json.loads(r.stdout)
    except:
        errors += 1
        time.sleep(3)
        continue
    if not data.get('success'):
        errors += 1
        time.sleep(3)
        continue

    inner = data.get('data', {})
    if page == 1:
        print(f'Total in BestBlogs: {inner.get(\"totalCount\",0)} ({inner.get(\"pageCount\",0)} pages)')

    items = inner.get('dataList', [])
    if not items:
        break

    for item in items:
        rid = item.get('id', '')
        title = item.get('originalTitle') or item.get('title') or ''
        if title in targets and rid:
            matched[title] = rid

    if len(matched) >= len(targets) or page >= inner.get('pageCount', 0):
        break

    page += 1
    if page % 20 == 0:
        print(f'  page {page}, matched {len(matched)}/{len(targets)}')
    time.sleep(0.3)

print(f'Matched {len(matched)} by title')
for title, rid in matched.items():
    conn.execute('UPDATE articles SET bb_resource_id=? WHERE id=?', (rid, targets[title]))
conn.commit()

total = conn.execute('SELECT COUNT(*) FROM articles WHERE source LIKE \"bestblog%\"').fetchone()[0]
has = conn.execute('SELECT COUNT(*) FROM articles WHERE source LIKE \"bestblog%\" AND bb_resource_id IS NOT NULL').fetchone()[0]
conn.close()
print(f'Total: {total}, has_id: {has}, missing: {total - has}')
"
