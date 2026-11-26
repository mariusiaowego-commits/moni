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
        title_repeated = _title_is_body_prefix(title, body)
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


def _title_is_body_prefix(title: str, body: str) -> bool:
    """True when the stored title is just the head of the body.

    Titles are auto-derived from the first line and truncated to 40 chars
    with an ellipsis, so an exact ``title + '：'`` comparison missed them
    and produced ``- [AI 对话] 结论：…: 结论：…``.  Compare the untruncated
    prefix instead.
    """
    if not title:
        return False
    head = body.lstrip()
    stem = title[:-1].strip() if title.endswith("…") else title
    if not stem:
        return False
    if head.startswith(f"{stem}：") or head.startswith(f"{stem}:"):
        return True
    return len(stem) >= 16 and head.startswith(stem)


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
        title_html = (
            f'<span class="ctx-title">{title}</span>'
            if title and not _title_is_body_prefix(
                str(item.get("title") or "").strip(),
                str(item.get("body") or "").strip(),
            )
            else ""
        )
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
    """Self-contained section for LLM-generated HTML with no moni CSS.

    Design (approved 2026-11-26): the kind badge leads every item, article
    titles are not repeated, and the block lives inside the content column
    (not as a separate 860px document after the footer).
    """
    contexts = load_contexts_db(db_path, article_ids)
    if not contexts:
        return ""
    items_html: list[str] = []
    count = 0
    used = 0
    for aid in article_ids:
        items = contexts.get(str(aid)) or []
        for item in items:
            body = str(item.get("body") or "").strip()
            if not body:
                continue
            remaining = TOTAL_CHARS - used
            if remaining <= 0:
                break
            budget = min(PER_ARTICLE_CHARS, remaining)
            count += 1
            truncated = len(body) > budget
            text = body[:budget]
            used += len(text)
            label = KIND_LABELS.get(str(item.get("kind") or ""), "上下文")
            title = str(item.get("title") or "").strip()
            title_html = ""
            if title and not _title_is_body_prefix(title, body):
                title_html = (
                    '<div style="margin:0 0 4px;font-size:13px;'
                    f'color:#78716c;">{html.escape(title)}</div>'
                )
            items_html.append(
                '<div style="border:1px solid #e2e8f0;border-radius:10px;'
                'padding:12px 14px;background:#ffffff;">'
                f'<span style="display:inline-block;font-size:11px;font-weight:600;'
                'color:#3730a3;background:#e0e7ff;border-radius:4px;'
                f'padding:1px 6px;">{html.escape(label)}</span>'
                f'{title_html}'
                '<div style="margin:6px 0 0;font-size:14px;line-height:1.75;'
                'color:#44403c;white-space:pre-wrap;overflow-wrap:anywhere;">'
                f"{html.escape(text)}{'…' if truncated else ''}"
                "</div></div>"
            )
    if not items_html:
        return ""
    return (
        '<section id="moni-user-contexts" class="moni-user-contexts" '
        'style="margin:48px 0 0;padding-top:24px;border-top:1px solid #e2e8f0;'
        'font-family:system-ui,-apple-system,\'PingFang SC\',sans-serif;color:#292524;">'
        '<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;">'
        f'<h2 style="margin:0;font-size:1.15rem;color:#1a365d;">{html.escape(heading)}</h2>'
        f'<span style="font-size:12px;color:#78716c;">{count} 条 · 用户整理，未经事实核查</span>'
        "</div>"
        '<div style="display:grid;gap:10px;margin-top:14px;">'
        f'{"".join(items_html)}'
        "</div>"
        '<p style="margin:10px 0 0;font-size:12px;color:#a8a29e;">'
        "这些内容由用户在阅读时保存，不代表本简报结论。</p>"
        "</section>"
    )


def anchor_banner(count: int) -> str:
    """In-content entry point so the contexts are discoverable on screen one."""
    if count <= 0:
        return ""
    return (
        '<a class="moni-ctx-anchor" href="#moni-user-contexts" '
        'style="display:block;margin:16px 0 0;'
        "padding:10px 12px;border:1px solid #e2e8f0;border-radius:8px;"
        "background:#f8fafc;color:#334155;font-size:13px;text-decoration:none;"
        'font-family:system-ui,-apple-system,\'PingFang SC\',sans-serif;">'
        f"本文含 {count} 条用户整理上下文 · 查看文末 ↓</a>"
    )


def _inject_after_header(html_text: str, anchor: str) -> str:
    if not anchor:
        return html_text
    match = re.search(r"</header\s*>", html_text, flags=re.IGNORECASE)
    if match:
        idx = match.end()
        return html_text[:idx] + anchor + html_text[idx:]
    match = re.search(r"<body\b[^>]*>", html_text, flags=re.IGNORECASE)
    if match:
        idx = match.end()
        return (
            html_text[:idx]
            + '<div class="moni-ctx-anchor-wrap" '
            'style="max-width:720px;margin:0 auto;padding:0 24px;">'
            + anchor
            + "</div>"
            + html_text[idx:]
        )
    return anchor + html_text


def inject_html_section(html_text: str, section: str, *, anchor: str = "") -> str:
    """Insert the context section before the page footer (inside .wrap).

    Before v1.0.141 the section was appended after ``</div></body>`` — i.e.
    after the footer, outside the 720px column, so users never saw it.
    Re-injection is idempotent: a previous ``moni-user-contexts`` section is
    dropped first, so retrying a task cannot stack two appendices.
    """
    if not section:
        return html_text
    # Drop a previous run's anchor and section so a resumed task cannot stack
    # two entry banners or two appendices.
    html_text = re.sub(
        r'<div[^>]*class="moni-ctx-anchor-wrap"[^>]*>.*?</div>\s*',
        "",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    html_text = re.sub(
        r'<a[^>]*class="moni-ctx-anchor"[^>]*>.*?</a>\s*',
        "",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    html_text = re.sub(
        r'<section[^>]*class="[^"]*moni-user-contexts[^"]*"[^>]*>.*?</section>\s*',
        "",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    html_text = _inject_after_header(html_text, anchor)
    footer = None
    for footer in re.finditer(r"<footer\b", html_text, flags=re.IGNORECASE):
        pass
    if footer is not None:
        idx = footer.start()
        return html_text[:idx] + section + "\n" + html_text[idx:]
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
        contexts = load_contexts_db(args.db, ids)
        n_contexts = sum(
            1
            for items in contexts.values()
            for item in items
            if str(item.get("body") or "").strip()
        )
        section = standalone_html_section(args.db, ids)
        if section:
            path = args.inject_html
            with open(path, encoding="utf-8") as f:
                doc = f.read()
            with open(path, "w", encoding="utf-8") as f:
                f.write(inject_html_section(doc, section, anchor=anchor_banner(n_contexts)))
        return 0
    print(markdown_section(args.db, ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
