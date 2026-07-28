"""moni.share.summary_gen — batch AI summary generation with DB writeback.

Called from default_worker.sh when the user selected "include AI summary"
(shareIncludeAbstract checkbox → include_abstract=true in manifest).

Flow:
1. Query articles that have raw_content but no ai_summary
2. Build a focused prompt requesting structured JSON output
3. Invoke hermes CLI for summary generation
4. Parse JSON response, write ai_summary + has_ai_summary=1 to DB
5. Return generated summaries as JSON dict {id: summary}

This is a one-time cost per article — subsequent shares reuse the DB value.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Prompt for focused summary generation.  Short, factual, no fluff.
SUMMARY_PROMPT = """你是 moni 新闻聚合系统的分析助手。请为以下每篇文章生成简洁的 AI 摘要。

要求：
- 每篇 3-5 句话
- 捕捉：核心论点、关键发现、为什么重要
- 保持事实性，不要"这篇文章值得关注"之类的填充语
- 中文文章用中文摘要，英文文章用英文摘要

请严格以 JSON 格式返回，不要添加任何其他文字：
```json
[{{"id": "文章ID", "summary": "摘要内容"}}]
```

文章数据：

{articles_text}"""


# Single-article prompt — more reliable than batch (LLM won't skip articles)
SINGLE_SUMMARY_PROMPT = """你是 moni 摘要助手。为下面文章写极短摘要。

硬要求：
- 最多 2 句中文，合计 ≤80 字
- 只写事实：谁、做了什么、关键数字/结论；不写「为什么重要」空评
- 禁止：值得关注、赋能、闭环、不是X而是Y、凸显了、深刻洞察
- 写得像 2022 年前人类短讯，不要 AI 腔

严格 JSON：
```json
{{"id": "{article_id}", "summary": "…"}}
```

文章标题: {title}
ID: {article_id}

文章内容:
{content}"""


def _needs_summaries(
    conn: sqlite3.Connection, article_ids: list[str]
) -> list[tuple[str, str, str]]:
    """Return articles that have raw_content but no ai_summary.

    Returns list of (id, title, raw_content) tuples.
    """
    if not article_ids:
        return []
    placeholders = ",".join("?" * len(article_ids))
    rows = conn.execute(
        f"SELECT id, title, raw_content FROM articles "
        f"WHERE id IN ({placeholders}) "
        f"AND (ai_summary IS NULL OR ai_summary = '') "
        f"AND (raw_content IS NOT NULL AND raw_content != '')",
        article_ids,
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _build_prompt(articles: list[tuple[str, str, str]]) -> str:
    """Build the summary-generation prompt from article tuples.

    For single articles, uses a focused prompt.  For multiple articles,
    still batches them but the caller should prefer single-article calls
    for reliability.
    """
    if len(articles) == 1:
        aid, title, content = articles[0]
        truncated = content[:3000] if len(content) > 3000 else content
        return SINGLE_SUMMARY_PROMPT.format(title=title, article_id=aid, content=truncated)

    parts = []
    for aid, title, content in articles:
        truncated = content[:3000] if len(content) > 3000 else content
        parts.append(f"### 文章: {title}\nID: {aid}\n\n{truncated}")
    articles_text = "\n\n---\n\n".join(parts)
    return SUMMARY_PROMPT.format(articles_text=articles_text)


def _invoke_hermes(
    prompt: str,
    timeout_sec: int = 120,
    extra_args: list[str] | None = None,
) -> tuple[str, int]:
    """Invoke hermes CLI and return (stdout, exit_code)."""
    argv = ["hermes", "-z", prompt, "--profile", os.environ.get("MONI_HERMES_PROFILE", "default")]
    if extra_args:
        argv.extend(extra_args)
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        stdout, _ = proc.communicate(timeout=timeout_sec)
        return stdout, proc.returncode
    except subprocess.TimeoutExpired:
        if proc:
            proc.kill()
            proc.wait()
        return "", 124
    except Exception as exc:
        return str(exc), 1


def _parse_summaries(output: str) -> list[dict]:
    """Extract JSON array from hermes output.

    Tries multiple strategies:
    1. Find ```json ... ``` code block
    2. Find [...] in the output
    3. Find {"id": ..., "summary": ...} pattern
    """
    # Strategy 1: ```json code block
    m = re.search(r"```json\s*\n(.*?)\n```", output, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    # Strategy 2: find [...] anywhere in output
    m = re.search(r"\[.*\]", output, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    # Strategy 3: find individual {"id": ..., "summary": ...} objects
    results = []
    for m in re.finditer(
        r'\{\s*"id"\s*:\s*"([^"]+)"\s*,\s*"summary"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}',
        output,
        re.DOTALL,
    ):
        results.append({"id": m.group(1), "summary": m.group(2)})
    return results


def _writeback(
    conn: sqlite3.Connection, summaries: list[dict]
) -> int:
    """Write summaries back to articles table.

    Returns count of articles updated.
    """
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for item in summaries:
        aid = item.get("id", "")
        summary = item.get("summary", "")
        if not aid or not summary:
            continue
        conn.execute(
            "UPDATE articles SET ai_summary=?, ai_summary_at=?, has_ai_summary=1 "
            "WHERE id=?",
            (summary, now, aid),
        )
        count += 1
    conn.commit()
    return count


def generate_and_writeback(
    db_path: str,
    article_ids: list[str],
    manifest_path: str | None = None,
) -> dict[str, str]:
    """Main entry: generate summaries for articles missing them, write to DB.

    Args:
        db_path: Path to SQLite database.
        article_ids: List of article IDs to check.
        manifest_path: Optional manifest JSON for provider/model overrides.

    Returns:
        Dict mapping article_id → summary text (only newly generated ones).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 1. Find articles needing summaries
    need = _needs_summaries(conn, article_ids)
    if not need:
        conn.close()
        return {}

    log.info("summary_gen: %d article(s) need AI summaries", len(need))

    # 2. Read provider/model overrides from manifest
    extra_args: list[str] = []
    if manifest_path:
        try:
            d = json.load(open(manifest_path))
            p = d.get("payload", {}) if isinstance(d.get("payload"), dict) else {}
            prov = p.get("provider") or ""
            model = p.get("model") or ""
            if prov:
                extra_args.extend(["--provider", prov])
            if model:
                extra_args.extend(["--model", model])
        except Exception:
            pass

    # 3. Process articles one at a time for reliability.
    # The LLM sometimes skips articles when batched — single-article
    # calls are more reliable and the cost is acceptable (1-2 articles
    # is the common case).
    all_summaries: dict[str, str] = {}
    for aid, title, content in need:
        prompt = _build_prompt([(aid, title, content)])
        output, exit_code = _invoke_hermes(prompt, timeout_sec=120, extra_args=extra_args)

        if exit_code != 0:
            log.warning(
                "summary_gen: hermes exited %d for article %s",
                exit_code, aid,
            )
            continue

        summaries = _parse_summaries(output)
        if not summaries:
            log.warning(
                "summary_gen: failed to parse summary for article %s: %s",
                aid, output[:200] if output else "(empty)",
            )
            continue

        # Write back immediately (incremental — don't lose partial results)
        count = _writeback(conn, summaries)
        for item in summaries:
            if item.get("id") and item.get("summary"):
                all_summaries[item["id"]] = item["summary"]
        log.info("summary_gen: wrote %d summary(ies) to DB", count)
    conn.close()

    # 6. Return {id: summary} dict for the caller to use
    return all_summaries


if __name__ == "__main__":
    # CLI: python3 moni/share/summary_gen.py <db_path> <article_ids_json> [manifest_path]
    if len(sys.argv) < 3:
        print("Usage: summary_gen.py <db_path> <article_ids_json> [manifest_path]")
        sys.exit(1)

    db = sys.argv[1]
    ids_str = sys.argv[2]
    mp = sys.argv[3] if len(sys.argv) > 3 else None

    # Handle both JSON ["a","b"] and Python repr ['a','b'] formats
    try:
        ids = json.loads(ids_str)
    except json.JSONDecodeError:
        try:
            import ast
            ids = ast.literal_eval(ids_str)
        except Exception:
            ids = []

    result = generate_and_writeback(db, ids, mp)
    print(json.dumps(result, ensure_ascii=False))
