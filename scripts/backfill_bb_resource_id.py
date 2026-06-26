#!/usr/bin/env python3
"""Backfill bb_resource_id for bestblogs articles via BestBlogs API."""
import json, os, sqlite3, subprocess, sys, time

DB_PATH = "data/news.db"
BASE_URL = "https://api.bestblogs.dev/openapi/v2"

def curl_get(url, api_key):
    hdr = "X-API-KEY: " + api_key
    r = subprocess.run(["curl", "-sL", url, "-H", hdr, "--max-time", "30"], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except:
        return None

def main():
    api_key = os.environ.get("BESTBLOGS_API_KEY", "")
    if not api_key:
        print("No BESTBLOGS_API_KEY set"); sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, title FROM articles WHERE (source='bestblogs' OR source LIKE 'bestblog-%') AND bb_resource_id IS NULL").fetchall()
    targets = {r["title"]: r["id"] for r in rows}
    print(f"Need to match {len(targets)} articles")
    if not targets:
        print("Nothing to backfill"); conn.close(); return

    matched, page, total_pages, errors = {}, 1, None, 0
    while errors < 5:
        url = f"{BASE_URL}/resources?limit=100&page={page}"
        data = curl_get(url, api_key)
        if not data or not data.get("success"):
            errors += 1; print(f"  API error page {page}: {data}"); time.sleep(3); continue
        errors = 0
        inner = data.get("data", {})
        if total_pages is None:
            total_pages = inner.get("pageCount", 0)
            print(f"Total in BestBlogs: {inner.get('totalCount',0)} ({total_pages} pages)")
        items = inner.get("dataList", [])
        if not items: break
        for item in items:
            rid = item.get("id", "")
            title = item.get("originalTitle") or item.get("title") or ""
            if title in targets and rid:
                matched[title] = rid
        if len(matched) >= len(targets) or (total_pages and page >= total_pages): break
        page += 1
        if page % 20 == 0: print(f"  page {page}/{total_pages}, matched {len(matched)}/{len(targets)}")
        time.sleep(0.3)

    print(f"\nMatched {len(matched)} articles by title")
    for title, rid in matched.items():
        conn.execute("UPDATE articles SET bb_resource_id=? WHERE id=?", (rid, targets[title]))
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM articles WHERE source LIKE 'bestblog%'").fetchone()[0]
    has = conn.execute("SELECT COUNT(*) FROM articles WHERE source LIKE 'bestblog%' AND bb_resource_id IS NOT NULL").fetchone()[0]
    conn.close()
    print(f"\nTotal: {total}, has_id: {has}, missing: {total - has}")

if __name__ == "__main__":
    main()
