"""Shared loader/renderer for article-level user contexts.

One source of truth for the share pipeline:
* Markdown/PDF/plain-text/rich-text get a deterministic appendix.
* Offline HTML gets a per-article block inside each card.

Contexts are snapshots.  Deleting a reader conversation must not remove them.
"""
from __future__ import annotations

import argparse
import ast
import html
import json
import re
import sqlite3
import sys
from typing import Any

KIND_LABELS = {
    "chat": "AI 对话",
    "note": "手记",
    "quote": "原文摘录",
    "summary": "摘要",
    "fact_check": "核查",
}

PER_ARTICLE_CHARS = 2400
TOTAL_CHARS = 12000


def load_contexts(
    conn: sqlite3.Connection, article_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    if not article_ids:
        return {}
    placeholders = ",".join("?" * len(article_ids))
    try:
        rows = conn.execute(
            "SELECT article_id, kind, title, body, source_question, quoted_text "
            "FROM article_contexts "
            f"WHERE article_id IN ({placeholders}) "
            "ORDER BY article_id, sort_order, created_at",
            article_ids,
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["article_id"]), []).append(dict(row))
    return grouped


def article_budget(context_article_count: int) -> int:
    return min(
        PER_ARTICLE_CHARS,
        max(600, TOTAL_CHARS // max(context_article_count, 1)),
    )


def load_contexts_db(
    db_path: str, article_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    if not article_ids:
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return load_contexts(conn, article_ids)
    finally:
        conn.close()


def format_lines(items: list[dict[str, Any]], *, budget: int) -> list[str]:
    if not items or budget <= 0:
        return []
    lines: list[str] = []
    used = 0
    for item in items:
        body = (item.get("body") or "").strip()
        if not body:
            continue
        label = KIND_LABELS.get(str(item.get("kind") or ""), "上下文")
        title = (item.get("title") or "").strip()
        prefix = f"- [{label}]"
        body_head = body.lstrip()
        title_repeated = bool(title) and (
            body_head.startswith(f"{title}：") or body_head.startswith(f"{title}:")
        )
        if title and not title_repeated:
            prefix += f" {title}:"
        line = f"{prefix} {body}"
        remaining = budget - used
        if remaining <= 0:
            break
        if len(line) > remaining:
            line = line[: max(remaining - 1, 0)] + "…"
        lines.append(line)
        used += len(line)
    return lines


def markdown_section(
    db_path: str,
    article_ids: list[str],
    *,
    heading: str = "用户上下文（用户整理，非核查结论）",
) -> str:
    contexts = load_contexts_db(db_path, article_ids)
    if not contexts:
        return ""
    titles = _article_titles(db_path, article_ids)

    budget = article_budget(len(contexts))
    blocks: list[str] = []
    shown = 0
    for aid in article_ids:
        items = contexts.get(str(aid)) or []
        if not items:
            continue
        lines = format_lines(items, budget=budget)
        if not lines:
            continue
        shown += 1
        blocks.append(
            f"### 文章 {shown}: {titles.get(str(aid), '')}\n" + "\n".join(lines)
        )
    if not blocks:
        return ""
    return (
        f"## {heading}\n\n"
        "> 以下内容由用户在阅读时保存，未经事实核查，不代表本简报结论。\n\n"
        + "\n\n".join(blocks)
    )


def html_block(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    lis: list[str] = []
    for item in items:
        label = KIND_LABELS.get(str(item.get("kind") or ""), "上下文")
        title = html.escape(str(item.get("title") or "").strip())
        body = html.escape(str(item.get("body") or "").strip())
        if not body:
            continue
        title_html = f'<span class="ctx-title">{title}</span>' if title else ""
        lis.append(
            '<li class="ctx-item">'
            f'<div class="ctx-meta"><span class="ctx-kind">{html.escape(label)}</span>{title_html}</div>'
            f'<div class="ctx-body">{body}</div>'
            "</li>"
        )
    if not lis:
        return ""
    return (
        '<div class="sec sec-contexts">'
        '<h3 class="sec-label">用户上下文</h3>'
        f'<ul class="ctx-list">{"".join(lis)}</ul>'
        '<p class="ctx-note">用户整理，未经事实核查，不代表本简报结论。</p>'
        "</div>"
    )


def _article_titles(db_path: str, article_ids: list[str]) -> dict[str, str]:
    if not article_ids:
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" * len(article_ids))
        return {
            str(row["id"]): str(row["title"] or "")
            for row in conn.execute(
                f"SELECT id, title FROM articles WHERE id IN ({placeholders})",
                article_ids,
            ).fetchall()
        }
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


def standalone_html_section(
    db_path: str,
    article_ids: list[str],
    *,
    heading: str = "用户上下文",
) -> str:
    """Self-contained section for LLM-generated HTML with no moni CSS."""
    contexts = load_contexts_db(db_path, article_ids)
    if not contexts:
        return ""
    titles = _article_titles(db_path, article_ids)
    budget = article_budget(len(contexts))
    blocks: list[str] = []
    shown = 0
    for aid in article_ids:
        items = contexts.get(str(aid)) or []
        if not items:
            continue
        lines = format_lines(items, budget=budget)
        if not lines:
            continue
        shown += 1
        body = html.escape("\n".join(lines))
        title = html.escape(titles.get(str(aid), ""))
        blocks.append(
            '<article style="margin:0 0 24px;">'
            f'<h3 style="margin:0 0 8px;font-size:1rem;">文章 {shown}: {title}</h3>'
            '<pre style="margin:0;padding:12px 14px;background:#faf7f2;'
            'border-radius:8px;white-space:pre-wrap;overflow-wrap:anywhere;'
            f'font:inherit;line-height:1.7;">{body}</pre>'
            "</article>"
        )
    if not blocks:
        return ""
    return (
        '<section class="moni-user-contexts" '
        'style="max-width:860px;margin:56px auto 0;padding:0 24px;'
        'font-family:system-ui,-apple-system,\'PingFang SC\',sans-serif;color:#292524;">'
        f'<h2 style="margin:0 0 8px;font-size:1.2rem;">{html.escape(heading)}</h2>'
        '<p style="margin:0 0 20px;font-size:0.85rem;color:#78716c;">'
        "用户整理，未经事实核查，不代表本简报结论。</p>"
        f'{"".join(blocks)}'
        "</section>"
    )


def inject_html_section(html_text: str, section: str) -> str:
    if not section:
        return html_text
    match = None
    for match in re.finditer(r"</body\s*>", html_text, flags=re.IGNORECASE):
        pass
    if match is None:
        return html_text + "\n" + section + "\n"
    idx = match.start()
    return html_text[:idx] + section + "\n" + html_text[idx:]


def _parse_ids(raw: str) -> list[str]:
    try:
        ids = json.loads(raw)
    except json.JSONDecodeError:
        ids = ast.literal_eval(raw)
    if not isinstance(ids, list):
        raise ValueError("article-ids must be a JSON array")
    return [str(x) for x in ids if x]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="render article user contexts")
    parser.add_argument("--db", required=True)
    parser.add_argument("--article-ids", required=True)
    parser.add_argument("--format", choices=["md"], default="md")
    parser.add_argument(
        "--inject-html",
        default=None,
        help="Insert a standalone section before </body> of this HTML file",
    )
    args = parser.parse_args(argv)
    try:
        ids = _parse_ids(args.article_ids)
    except (ValueError, SyntaxError) as exc:
        print(f"bad --article-ids: {exc}", file=sys.stderr)
        return 2
    if args.inject_html:
        section = standalone_html_section(args.db, ids)
        if section:
            path = args.inject_html
            with open(path, encoding="utf-8") as f:
                doc = f.read()
            with open(path, "w", encoding="utf-8") as f:
                f.write(inject_html_section(doc, section))
        return 0
    print(markdown_section(args.db, ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
