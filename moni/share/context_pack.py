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
from functools import lru_cache
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

# AI 对话类上下文的第一段通常是用户自己写的「结论：…」，直接拿它当 TLDR。
# 上下文不进 LLM（红线），所以 TLDR 必须是抽取式，不做任何模型调用。
TLDR_PREFIXES = ("结论：", "结论:", "TLDR：", "TLDR:", "要点：", "摘要：")
MAX_TLDR_CHARS = 180


@lru_cache(maxsize=1)
def _markdown_renderer() -> Any:
    """mistune 实例；没装就返回 None（调用方退回纯文本）。"""
    try:
        import mistune
    except ImportError:  # pragma: no cover - 环境缺依赖时的降级路径
        return None
    return mistune.create_markdown(
        escape=True,  # 上下文是用户内容，原样 HTML 一律转义
        plugins=["table", "strikethrough"],
    )


def split_tldr(body: str) -> tuple[str, str]:
    """把上下文正文拆成 (TLDR, 详情)。

    AI 对话的第一段就是用户自己下的结论（``结论：…``），那就是现成的 TLDR；
    没有结论前缀时退化成第一句。TLDR 之后的所有内容都进详情，不能丢。
    不调用模型 —— 上下文不许进 LLM 输入。
    """
    text = (body or "").strip()
    if not text:
        return "", ""
    parts = text.split("\n\n", 1)
    first = parts[0].strip()
    rest = parts[1].strip() if len(parts) > 1 else ""
    head = first
    for prefix in TLDR_PREFIXES:
        if head.startswith(prefix):
            head = head[len(prefix) :].strip()
            break
    stop = re.search(r"[。！？!?]", head)
    if stop:
        sentence, leftover = head[: stop.end()].strip(), head[stop.end() :].strip()
    else:
        sentence, leftover = "", head
    if len(sentence) > MAX_TLDR_CHARS:
        overflow = sentence[MAX_TLDR_CHARS:].strip()
        sentence = sentence[:MAX_TLDR_CHARS].rstrip() + "…"
        leftover = f"{overflow} {leftover}".strip()
    if not sentence:
        if len(leftover) > MAX_TLDR_CHARS:
            overflow = leftover[MAX_TLDR_CHARS:].strip()
            sentence = leftover[:MAX_TLDR_CHARS].rstrip() + "…"
            leftover = overflow
        else:
            sentence, leftover = leftover, ""
    detail = f"{leftover}\n\n{rest}".strip() if rest else leftover
    return sentence, detail


def render_body_html(text: str) -> str:
    """Markdown → HTML。mistune 缺失时退回转义纯文本，不抛错。"""
    if not text:
        return ""
    renderer = _markdown_renderer()
    if renderer is None:  # pragma: no cover - 环境缺依赖时的降级路径
        return (
            '<div style="white-space:pre-wrap;overflow-wrap:anywhere;">'
            f"{html.escape(text)}</div>"
        )
    return renderer(text)


# --- 详情结构化渲染 -------------------------------------------------------
# AI 对话正文天然是「一、关键事实 / 二、案例与实验 / 三、无出处 / 四、未知」
# 这种分节。前两类是逐条编号的证据，铺成双列瀑布流好扫；后两类是整段说明，
# 保持整段排版。解析不出结构就退回普通 markdown，不猜。

_SECTION_LINE_RE = re.compile(r"^[ \t]*([一二三四五六七八九十]+)、[ \t]*(.*)$")
_NUMBERED_RE = re.compile(r"(?m)(?:^|(?<=[。！？；）)]))[ \t]*(\d{1,2})[.、][ \t]+")
# 标题行里紧贴正文的编号（``一、结论1. 这个案例…``）：前导不限标点，只要求
# 编号前不是数字，避免把年份「2024. 」里的「24.」当成条目。
_INLINE_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,2})[.、][ \t]+")
# 标题与正文的其它分隔：句读、冒号，或两个以上空格（``七、优先级建议  第一步：``）。
_HEADING_BREAK_RE = re.compile(r"(?:[。；：]|[ \t]{2,})")
_MAX_HEADING_CHARS = 12
_PROOF_RE = re.compile(r"证明[：:]")

_DETAIL_CSS = """
{S}.moni-ctx-h {
  margin: 20px 0 8px; font-size: 15px; font-weight: 700; color: #1a365d;
}
{S}.moni-ctx-h:first-child { margin-top: 0; }
{S}.moni-ctx-cols { columns: 280px 2; column-gap: 16px; margin: 0 0 4px; }
{S}.moni-ctx-item {
  break-inside: avoid; -webkit-column-break-inside: avoid;
  margin: 0 0 12px; padding: 10px 12px 11px;
  border: 1px solid #eee7dd; border-radius: 8px; background: #fffdf9;
}
{S}.moni-ctx-claim { font-size: 13.5px; line-height: 1.65; color: #292524; }
{S}.moni-ctx-proof { margin-top: 6px; font-size: 12.5px; line-height: 1.6; color: #78716c; }
{S}.moni-ctx-claim p, {S}.moni-ctx-proof p { margin: 0 0 0.45em; }
{S}.moni-ctx-claim p:last-child, {S}.moni-ctx-proof p:last-child { margin-bottom: 0; }
{S}.moni-ctx-prose { font-size: 14px; line-height: 1.8; color: #44403c; }
{S}.moni-ctx-prose p { margin: 0 0 0.6em; }
{S}.moni-ctx-prose ul { list-style: disc outside; margin: 0 0 0.6em; padding-left: 1.3em; }
{S}.moni-ctx-prose ol { list-style: decimal outside; margin: 0 0 0.6em; padding-left: 1.3em; }
{S}.moni-ctx-prose li { display: list-item; margin: 0 0 0.3em; }
{S}.moni-ctx-prose code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.92em; background: #f5f5f4; border-radius: 3px; padding: 0 3px;
}
@media (max-width: 720px) { {S}.moni-ctx-cols { columns: 1; } }
"""


def detail_css(scope: str = "") -> str:
    """分节渲染所需样式；scope 是选择器前缀（如 ``#moni-user-contexts``）。"""
    prefix = f"{scope} " if scope else ""
    return _DETAIL_CSS.replace("{S}", prefix)


def _split_sections(text: str) -> list[tuple[str, str]]:
    """按「一、xxx」切成 [(标题, 正文)]；没有标题的段落标题为空。"""
    sections: list[tuple[str, str]] = []
    heading = ""
    buf: list[str] = []
    for line in text.splitlines():
        match = _SECTION_LINE_RE.match(line)
        if not match:
            buf.append(line)
            continue
        if buf or heading:
            sections.append((heading, "\n".join(buf).strip()))
        buf = []
        rest = match.group(2).strip()
        label, body = _split_heading_line(rest)
        heading = label
        if body:
            buf.append(body)
    if buf:
        sections.append((heading, "\n".join(buf).strip()))
    return [(h, b) for h, b in sections if h or b]


def _split_heading_line(rest: str) -> tuple[str, str]:
    """标题行可能把第 1 条正文粘在标题后面，这里把它拆出来。

    生产里真实出现过 ``一、结论1. 这个案例…``、``六、两个反直觉的洞察1. …``
    和 ``七、优先级建议  第一步：…`` 三种写法；只在分隔符前的标题确实很短时
    才切，避免把正常的长标题腰斩。
    """
    inline = _INLINE_NUMBER_RE.search(rest)
    if inline and inline.start() > 0:
        return rest[: inline.start()].strip(), rest[inline.start() :]
    if inline:
        return "", rest
    sep = _HEADING_BREAK_RE.search(rest)
    if sep and 0 < sep.start() <= _MAX_HEADING_CHARS:
        return rest[: sep.start()].strip(), rest[sep.end() :].strip()
    return rest, ""


def _numbered_items(body: str) -> tuple[str, list[str]]:
    """拆出编号条目；返回 (前导文字, [条目])。

    编号必须连续（1→2→3…）才算一条，避免把正文里的「3.0.1」当成条目号；
    有些对话把编号写在上一句后面（``…先排除变量。14. 实验二``），所以句读
    之后的编号也算。
    """
    candidates = list(_NUMBERED_RE.finditer(body))
    if not candidates:
        return body, []
    # 从「正文开头」或「编号 1」起算，避免前导句里偶然出现的「。5.」把序列锁死。
    start = next(
        (i for i, m in enumerate(candidates) if not body[: m.start()].strip()), 0
    )
    if start == 0 and int(candidates[0].group(1)) != 1:
        start = next(
            (i for i, m in enumerate(candidates) if int(m.group(1)) == 1), 0
        )
    marks = [candidates[start]]
    expected = int(candidates[start].group(1)) + 1
    for match in candidates[start + 1 :]:
        number = int(match.group(1))
        if number != expected:
            continue
        marks.append(match)
        expected = number + 1
    starts_body = bool(marks) and not body[: marks[0].start()].strip()
    if not marks or (len(marks) < 2 and not starts_body):
        return body, []
    lead = body[: marks[0].start()].strip()
    items: list[str] = []
    for idx, match in enumerate(marks):
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(body)
        item = body[match.end() : end].strip()
        if item:
            items.append(item)
    return lead, items


def _render_numbered_items(items: list[str]) -> str:
    cards: list[str] = []
    for raw in items:
        proof_at = _PROOF_RE.search(raw)
        claim, proof = raw, ""
        if proof_at and proof_at.start() > 0:
            claim = raw[: proof_at.start()].strip()
            proof = raw[proof_at.start() :].strip()
        card = (
            '<div class="moni-ctx-item">'
            f'<div class="moni-ctx-claim">{render_body_html(claim)}</div>'
        )
        if proof:
            card += f'<div class="moni-ctx-proof">{render_body_html(proof)}</div>'
        cards.append(card + "</div>")
    return f'<div class="moni-ctx-cols">{"".join(cards)}</div>'


def render_detail_html(text: str) -> str:
    """上下文详情 → HTML：编号分节铺双列，其余整段。"""
    if not text:
        return ""
    sections = _split_sections(text)
    if not any(heading for heading, _ in sections):
        return render_body_html(text)
    parts: list[str] = []
    for heading, body in sections:
        if heading:
            parts.append(f'<h4 class="moni-ctx-h">{html.escape(heading)}</h4>')
        lead, items = _numbered_items(body)
        if items:
            if lead:
                parts.append(f'<div class="moni-ctx-prose">{render_body_html(lead)}</div>')
            parts.append(_render_numbered_items(items))
        elif body:
            parts.append(f'<div class="moni-ctx-prose">{render_body_html(body)}</div>')
    return "".join(parts)


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


def _title_covered_by_tldr(title: str, tldr: str) -> bool:
    """True when the title is just a truncated copy of the TLDR.

    AI 对话的标题由正文首句自动派生（截断到 40 字加省略号），正文首句同时
    又是 TLDR。两者都显示就成了同一句话出现两遍 —— 留 TLDR 一份即可。
    比较前先把「结论：」这类前缀从两边都剥掉，否则标题带前缀、TLDR 不带，
    就会判不出是同一句。
    """
    t, l = title.strip(), tldr.strip()
    if not t or not l:
        return False
    stem = t[:-1].strip() if t.endswith("…") else t
    for prefix in TLDR_PREFIXES:
        if stem.startswith(prefix):
            stem = stem[len(prefix) :].strip()
            break
    for prefix in TLDR_PREFIXES:
        if l.startswith(prefix):
            l = l[len(prefix) :].strip()
            break
    short, long_ = (stem, l) if len(stem) <= len(l) else (l, stem)
    if len(short) < 8:
        return False
    return long_.startswith(short)


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
    tldr_lines: list[str] = []
    for aid in article_ids:
        for item in contexts.get(str(aid)) or []:
            tldr, _ = split_tldr(str(item.get("body") or ""))
            if tldr:
                tldr_lines.append(f"- {tldr}")
            if len(tldr_lines) >= 3:
                break
        if len(tldr_lines) >= 3:
            break
    tldr_block = (
        "".join(
            [
                "> **TLDR**\n>\n",
                "\n".join(f"> {line}" for line in tldr_lines),
                "\n\n",
            ]
        )
        if tldr_lines
        else ""
    )
    return (
        f"## {heading}\n\n"
        f"{tldr_block}"
        "> 以下内容由用户在阅读时保存，未经事实核查，不代表本简报结论。\n\n"
        + "\n\n".join(blocks)
    )


def html_block(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    lis: list[str] = []
    for item in items:
        label = KIND_LABELS.get(str(item.get("kind") or ""), "上下文")
        raw_title = str(item.get("title") or "").strip()
        title = html.escape(raw_title)
        raw_body = str(item.get("body") or "").strip()
        if not raw_body:
            continue
        tldr, detail = split_tldr(raw_body)
        title_html = (
            f'<span class="ctx-title">{title}</span>'
            if title
            and not _title_is_body_prefix(raw_title, raw_body)
            and not _title_covered_by_tldr(raw_title, tldr)
            else ""
        )
        details_html = (
            '<details class="ctx-details">'
            '<summary class="ctx-summary">展开完整上下文</summary>'
            f'<div class="ctx-body">{render_detail_html(detail)}</div>'
            "</details>"
            if detail
            else ""
        )
        lis.append(
            '<li class="ctx-item">'
            f'<div class="ctx-meta"><span class="ctx-kind">{html.escape(label)}</span>{title_html}</div>'
            f'<div class="ctx-tldr">{html.escape(tldr)}</div>'
            f"{details_html}"
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

    Design: the kind badge leads every item, the owning article's title sits
    next to it (contexts from several articles share one list, so a card with
    no title cannot be traced back), and the block lives inside the content
    column (not as a separate 860px document after the footer).
    """
    contexts = load_contexts_db(db_path, article_ids)
    if not contexts:
        return ""
    article_titles = _article_titles(db_path, article_ids)
    items_html: list[str] = []
    count = 0
    used = 0
    for aid in article_ids:
        article_title = article_titles.get(str(aid), "")
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
            tldr, detail = split_tldr(body)
            truncated = len(detail) > budget
            text = detail[:budget]
            used += len(text)
            label = KIND_LABELS.get(str(item.get("kind") or ""), "上下文")
            title = str(item.get("title") or "").strip()
            title_html = ""
            if (
                title
                and not _title_is_body_prefix(title, body)
                and not _title_covered_by_tldr(title, tldr)
            ):
                title_html = (
                    '<div style="margin:0 0 4px;font-size:13px;'
                    f'color:#78716c;">{html.escape(title)}</div>'
                )
            details_html = (
                '<details style="margin:8px 0 0;">'
                '<summary style="cursor:pointer;font-size:12px;color:#78716c;">'
                "展开完整上下文</summary>"
                '<div style="margin:8px 0 0;font-size:14px;line-height:1.75;'
                f'color:#44403c;">{render_detail_html(text)}'
                f"{'<span>…</span>' if truncated else ''}</div>"
                "</details>"
                if text
                else ""
            )
            article_html = (
                '<span style="font-size:13px;font-weight:600;color:#44403c;">'
                f"{html.escape(article_title)}</span>"
                if article_title
                else ""
            )
            items_html.append(
                '<div style="border:1px solid #e2e8f0;border-radius:10px;'
                'padding:12px 14px;background:#ffffff;">'
                '<div style="display:flex;align-items:baseline;gap:8px;'
                'flex-wrap:wrap;margin:0 0 2px;">'
                f'<span style="display:inline-block;font-size:11px;font-weight:600;'
                'color:#3730a3;background:#e0e7ff;border-radius:4px;'
                f'padding:1px 6px;">{html.escape(label)}</span>'
                f"{article_html}</div>"
                f'{title_html}'
                '<div style="margin:6px 0 0;font-size:14px;line-height:1.7;'
                'font-weight:600;color:#1c1917;">'
                f"{html.escape(tldr)}</div>"
                f"{details_html}</div>"
            )
    if not items_html:
        return ""
    return (
        '<section id="moni-user-contexts" class="moni-user-contexts" '
        'style="margin:48px 0 0;padding-top:24px;border-top:1px solid #e2e8f0;'
        'font-family:system-ui,-apple-system,\'PingFang SC\',sans-serif;color:#292524;">'
        f"<style>{detail_css('#moni-user-contexts')}</style>"
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
