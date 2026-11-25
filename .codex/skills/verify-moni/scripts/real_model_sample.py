"""One-shot real-model sampling of moni's three LLM channels.

Spends model quota and writes llm_jobs / share rows. Run only with explicit
authorization for the turn. Order matters: the brief needs an article that is
already body+summary+business_value+fact_check ready, so pin one first.

Usage:
    MONI_BASE=http://127.0.0.1:8901 /usr/local/bin/python3 \
        .codex/skills/verify-moni/scripts/real_model_sample.py

Optional env: MONI_SAMPLE_ARTICLE_SUMMARY, MONI_SAMPLE_ARTICLE_FACTCHECK.
"""

import json
import os
import sqlite3
import time
import urllib.error
import urllib.request

BASE = os.environ.get("MONI_BASE", "http://127.0.0.1:8901").rstrip("/")
DB = os.environ.get("MONI_DB", "data/news.db")
SUMMARY_ARTICLE = os.environ.get("MONI_SAMPLE_ARTICLE_SUMMARY", "aca0b6a22ce3c236")
FACTCHECK_ARTICLE = os.environ.get("MONI_SAMPLE_ARTICLE_FACTCHECK", "6d573ec7d6cd7b8f")

out = {"started": time.strftime("%Y-%m-%dT%H:%M:%S")}


def call(method, path, payload=None, timeout=60):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            return resp.status, (json.loads(body) if body.strip() else None)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:600]


def poll_job(job_id, budget=420):
    started = time.time()
    while time.time() - started < budget:
        code, body = call("GET", f"/api/llm/jobs/{job_id}")
        if code == 200 and body.get("status") in ("done", "failed", "cancelled", "stale"):
            body["wall_s"] = round(time.time() - started, 1)
            return body
        time.sleep(3)
    return {"status": "timeout", "wall_s": round(time.time() - started, 1)}


def article_state(article_id):
    conn = sqlite3.connect(DB)
    row = conn.execute(
        "SELECT length(coalesce(summary,'')), length(coalesce(fact_check_conclusion,'')),"
        " fact_check_method FROM articles WHERE id=?",
        (article_id,),
    ).fetchone()
    evidence = conn.execute(
        "SELECT count(*) FROM fact_check_evidence WHERE article_id=?", (article_id,)
    ).fetchone()[0]
    return {"summary_len": row[0], "fc_len": row[1], "fc_method": row[2], "evidence_rows": evidence}


# ① 摘要 —— 通道 ① (runner.py)
# ArticleSummaryRequest 要求 body 里带 article_id，空 body 会 422（不会入队）。
code, body = call(
    "POST", f"/api/articles/{SUMMARY_ARTICLE}/summary", {"article_id": SUMMARY_ARTICLE}
)
out["summary_enqueue"] = {"http": code, "body": body}
if code in (200, 202) and isinstance(body, dict) and body.get("job_id"):
    out["summary_job"] = poll_job(body["job_id"])
    out["summary_article"] = article_state(SUMMARY_ARTICLE)

# ② 联网事实核查 —— 通道 ② (stream.py + web_search_safe)
code, body = call("POST", f"/api/articles/{FACTCHECK_ARTICLE}/fact-check-plus", {})
out["fact_check_plus_enqueue"] = {"http": code, "body": body}
if code in (200, 202) and isinstance(body, dict) and body.get("job_id"):
    out["fact_check_plus_job"] = poll_job(body["job_id"])
    out["fact_check_plus_article"] = article_state(FACTCHECK_ARTICLE)

# ③ 分享简报 —— 通道 ④/⑤ (独立子进程 + ContextVar)
code, body = call("POST", "/api/share/brief", {"force": True})
out["brief_enqueue"] = {"http": code, "body": body}
if code in (200, 201) and isinstance(body, dict) and body.get("share_id"):
    share_id = body["share_id"]
    started = time.time()
    while time.time() - started < 420:
        code, task = call("GET", f"/api/share/tasks/{share_id}")
        if code == 200 and task.get("status") in ("done", "failed", "cancelled"):
            task["wall_s"] = round(time.time() - started, 1)
            out["brief_task"] = task
            break
        time.sleep(5)
    else:
        out["brief_task"] = {"status": "timeout"}

print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
