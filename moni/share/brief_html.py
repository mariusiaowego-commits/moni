"""moni.share.brief_html — deterministic single-file HTML brief (v1.0.10).

Why not LLM-generated HTML for this path:
- End users cannot open original URLs → must embed full body text
- Collapse / max-height scroll / GSAP need stable DOM structure

Usage:
  python3 -m moni.share.brief_html --db data/news.db --article-ids '["a"]' \\
      --output data/artifacts/x.html --title '同业简报'
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

from moni.share.enrichment import (
    compute_heuristic_quality,
    pick_source_summary,
    _format_date,
)


def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def _strip_frontmatter(text: str) -> str:
    """Drop YAML frontmatter between leading --- fences (common in moni raw_content)."""
    t = text.lstrip("\ufeff")
    if not t.startswith("---"):
        return text
    # second --- ends frontmatter
    m = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", t, re.DOTALL)
    if m:
        return m.group(1).lstrip("\n")
    return text


def _inline_md(text: str) -> str:
    """Escape then apply a tiny inline markdown subset."""
    s = _esc(text)
    # links [label](url)
    s = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2" target="_blank" rel="noopener">\1</a>',
        s,
    )
    # bold **x** or __x__
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"__(.+?)__", r"<strong>\1</strong>", s)
    # italic *x* (avoid **)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", s)
    # inline code `x`
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def body_to_html(raw: str | None) -> str:
    """Render article body as simple HTML (markdown-ish, zero extra deps).

    Supports: YAML frontmatter strip, AT## headings, lists, hr, blockquotes,
    paragraphs, **bold**, *italic*, [links](url), `code`.
    Plain text (no markdown) still gets readable paragraphs.
    """
    if not raw or not str(raw).strip():
        return "<p class='empty'>（无正文）</p>"

    text = _strip_frontmatter(str(raw)).replace("\r\n", "\n").replace("\r", "\n")
    # Some sources glue headings mid-line: "…坚守。 ## 1. 标题"
    text = re.sub(r"([。！？；;])\s*(#{1,3}\s+)", r"\1\n\n\2", text)
    # Wall-of-text plain articles (no blank lines): break on sentence end
    # so reading isn't one giant <p>.
    if "\n\n" not in text and len(text) > 280:
        text = re.sub(r"([。！？])(?=[^\n]{12,})", r"\1\n\n", text)

    lines = text.split("\n")
    out: list[str] = []
    i = 0
    para: list[str] = []
    list_buf: list[str] = []
    list_ordered = False

    def flush_para() -> None:
        nonlocal para
        if not para:
            return
        chunk = " ".join(p.strip() for p in para if p.strip())
        if chunk:
            out.append(f"<p>{_inline_md(chunk)}</p>")
        para = []

    def flush_list() -> None:
        nonlocal list_buf, list_ordered
        if not list_buf:
            return
        tag = "ol" if list_ordered else "ul"
        items = "".join(f"<li>{_inline_md(x)}</li>" for x in list_buf)
        out.append(f"<{tag}>{items}</{tag}>")
        list_buf = []
        list_ordered = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # blank → break block
        if not stripped:
            flush_list()
            flush_para()
            i += 1
            continue

        # horizontal rule
        if re.match(r"^-{3,}$", stripped) or re.match(r"^\*{3,}$", stripped):
            flush_list()
            flush_para()
            out.append("<hr/>")
            i += 1
            continue

        # headings
        hm = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if hm:
            flush_list()
            flush_para()
            level = min(len(hm.group(1)) + 1, 4)  # # → h2 in body (h1 is page title)
            out.append(f"<h{level}>{_inline_md(hm.group(2).strip())}</h{level}>")
            i += 1
            continue

        # blockquote
        if stripped.startswith(">"):
            flush_list()
            flush_para()
            q_lines = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                q_lines.append(re.sub(r"^>\s?", "", lines[i].strip()))
                i += 1
            out.append(f"<blockquote>{_inline_md(' '.join(q_lines))}</blockquote>")
            continue

        # unordered list
        um = re.match(r"^[-*+]\s+(.+)$", stripped)
        if um:
            flush_para()
            if list_buf and list_ordered:
                flush_list()
            list_ordered = False
            list_buf.append(um.group(1).strip())
            i += 1
            continue

        # ordered list
        om = re.match(r"^(\d+)[.)、]\s+(.+)$", stripped)
        if om:
            flush_para()
            if list_buf and not list_ordered:
                flush_list()
            list_ordered = True
            list_buf.append(om.group(2).strip())
            i += 1
            continue

        # normal text line
        flush_list()
        para.append(stripped)
        i += 1

    flush_list()
    flush_para()
    return "\n".join(out) if out else f"<p>{_inline_md(text)}</p>"


def _load_rows(db_path: str, article_ids: list[str]) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if not article_ids:
        conn.close()
        return []
    ph = ",".join("?" * len(article_ids))
    order = " ".join(f"WHEN ? THEN {i}" for i in range(len(article_ids)))
    try:
        rows = list(
            conn.execute(
                f"""
                SELECT id, title, url, source, publisher, published_at, raw_content,
                       ai_summary, summary, bb_summary, bb_one_sentence, brief_summary,
                       brief_body_md,
                       fact_check_conclusion, fact_check_note,
                       business_value_score, business_value_note,
                       quality_score, bb_score
                FROM articles WHERE id IN ({ph})
                ORDER BY CASE id {order} END
                """,
                (*article_ids, *article_ids),
            ).fetchall()
        )
    except sqlite3.OperationalError:
        rows = list(
            conn.execute(
                f"""
                SELECT id, title, url, source, publisher, published_at, raw_content,
                       ai_summary, summary, bb_summary, NULL as bb_one_sentence,
                       NULL as brief_summary, NULL as brief_body_md,
                       fact_check_conclusion, fact_check_note,
                       business_value_score, business_value_note,
                       quality_score, bb_score
                FROM articles WHERE id IN ({ph})
                """,
                article_ids,
            ).fetchall()
        )
    conn.close()
    # BV desc for brief
    rows.sort(
        key=lambda r: (
            0 if r["business_value_score"] is not None else 1,
            -(r["business_value_score"] or 0),
        )
    )
    return rows


def _source_line(row: sqlite3.Row) -> str:
    pub = (row["publisher"] or "").strip()
    ch = (row["source"] or "").strip()
    if pub and ch:
        return f"{pub}（{ch}）"
    return pub or ch or "未知来源"


def _quality(row: sqlite3.Row) -> int:
    if row["bb_score"] is not None:
        try:
            return int(row["bb_score"])
        except (TypeError, ValueError):
            pass
    if row["quality_score"] is not None:
        try:
            return int(row["quality_score"])
        except (TypeError, ValueError):
            pass
    return compute_heuristic_quality(row)


def _summary_text(row: sqlite3.Row) -> str:
    bs = ""
    try:
        bs = (row["brief_summary"] or "").strip()
    except (KeyError, IndexError):
        bs = ""
    if bs:
        return bs
    full, _ = pick_source_summary(row)
    return full or "（无摘要）"


def _fc_items(row: sqlite3.Row) -> list[tuple[str, str]]:
    """Return list of (badge_kind, text) for fact-check lines.

    Badge is icon-only (status color); strip redundant status words from text.
    """
    note = (row["fact_check_note"] or "").strip()
    conc = (row["fact_check_conclusion"] or "").strip()
    raw = note or conc
    if not raw:
        return [("none", "无需核查")]
    raw = (
        raw.replace("[supported]", "【已核实】")
        .replace("[Supported]", "【已核实】")
        .replace("[unverified]", "【待核实】")
        .replace("[Unverified]", "【待核实】")
        .replace("[disputed]", "【存疑】")
        .replace("[Disputed]", "【存疑】")
    )
    parts = re.split(r"[;；]\s*", raw)
    out: list[tuple[str, str]] = []
    for p in parts[:3]:
        p = p.strip()
        if not p:
            continue
        if "【已核实】" in p or re.search(r"(?<![不待])已核实", p):
            kind = "ok"
        elif "【存疑】" in p or "存疑" in p:
            kind = "bad"
        elif "【待核实】" in p or "待核实" in p or "unverified" in p.lower():
            kind = "warn"
        elif "无需" in p:
            kind = "none"
        else:
            kind = "warn"
        # strip status labels + claimN prefixes — icon already conveys status
        p = re.sub(
            r"【已核实】|【待核实】|【存疑】|\[supported\]|\[unverified\]|\[disputed\]",
            "",
            p,
            flags=re.I,
        )
        p = re.sub(r"\b(supported|unverified|disputed)\b", "", p, flags=re.I)
        p = re.sub(r"^claim\s*\d+\s*[:：.\s-]*", "", p, flags=re.I)
        p = re.sub(r"^(已核实|待核实|存疑)\s*[:：.\s-]*", "", p)
        p = p.strip(" :：-—|")
        if not p and kind == "none":
            p = "无需核查"
        if p or kind == "none":
            out.append((kind, p or "（无说明）"))
    return out or [("none", "无需核查")]


def _badge_svg(kind: str) -> str:
    """Icon-only status badge (no text label — avoids 已核实 + 【已核实】 duplicate)."""
    if kind == "ok":
        return (
            '<svg viewBox="0 0 16 16" width="16" height="16" aria-label="已核实" role="img">'
            '<circle cx="8" cy="8" r="8" fill="#16a34a"/>'
            '<path d="M4.5 8.2l2.2 2.2 4.8-4.8" stroke="#fff" stroke-width="1.6" '
            'fill="none" stroke-linecap="round"/></svg>'
        )
    if kind == "bad":
        return (
            '<svg viewBox="0 0 16 16" width="16" height="16" aria-label="存疑" role="img">'
            '<circle cx="8" cy="8" r="8" fill="#dc2626"/>'
            '<path d="M8 4.2v5.2" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/>'
            '<circle cx="8" cy="12" r="1" fill="#fff"/></svg>'
        )
    if kind == "warn":
        return (
            '<svg viewBox="0 0 16 16" width="16" height="16" aria-label="待核实" role="img">'
            '<circle cx="8" cy="8" r="8" fill="#d97706"/>'
            '<path d="M8 4.2v5.2" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/>'
            '<circle cx="8" cy="12" r="1" fill="#fff"/></svg>'
        )
    return ""

def render_brief_html(
    db_path: str,
    article_ids: list[str],
    *,
    title: str | None = None,
    report_date: str | None = None,
) -> str:
    rows = _load_rows(db_path, article_ids)
    d = report_date or date.today().isoformat()
    title = title or f"同业简报 — {d}"
    n = len(rows)

    focus_items: list[str] = []
    for r in rows[:3]:
        t = (r["title"] or "").replace("\n", " ").strip()
        bv = r["business_value_score"]
        # Full note — no ellipsis truncation. Prefer BV note; else short summary.
        note = (r["business_value_note"] or "").strip()
        if not note:
            note = _summary_text(r).strip()
        if not note:
            note = "见正文"
        bv_s = str(bv) if bv is not None else "—"
        focus_items.append(
            f"""<li class="focus-item">
  <div class="focus-title"><span class="focus-bv">BV {_esc(bv_s)}</span>
    <strong>{_esc(t)}</strong></div>
  <p class="focus-note">{_esc(note)}</p>
</li>"""
        )

    cards: list[str] = []
    for i, r in enumerate(rows, 1):
        t = (r["title"] or "").replace("\n", " ").strip()
        # Prefer agent-laid-out markdown; fall back to raw + light MD render
        laid = ""
        try:
            laid = (r["brief_body_md"] or "").strip()
        except (KeyError, IndexError, TypeError):
            laid = ""
        body = laid or (r["raw_content"] or "").strip() or "（无正文）"
        body_html = body_to_html(body)
        summ = _summary_text(r)
        bv = r["business_value_score"]
        bv_note = (r["business_value_note"] or "").strip()
        q = _quality(r)
        pub_date = _format_date(r["published_at"]) or "日期不详"
        url = (r["url"] or "").strip()
        link_html = (
            f'<a class="ext" href="{_esc(url)}" target="_blank" rel="noopener">原文链接（可能无法打开）</a>'
            if url
            else ""
        )
        fc_lis = []
        _fc_lab = {"ok": "已核实", "warn": "待核实", "bad": "存疑"}
        for kind, text in _fc_items(r):
            if kind == "none":
                fc_lis.append(f"<li class='fc-line fc-plain'>{_esc(text)}</li>")
            else:
                # icon + 文字 同一容器，claim 紧随其后同一行（不换行断开）
                lab = _fc_lab.get(kind, "")
                fc_lis.append(
                    f"<li class='fc-line'>"
                    f"<span class='badge {kind}'>"
                    f"{_badge_svg(kind)}<span class='badge-lab'>{_esc(lab)}</span>"
                    f"</span>"
                    f"<span class='fc-text'>{_esc(text)}</span>"
                    f"</li>"
                )

        cards.append(
            f"""
<article class="card" data-id="{_esc(r['id'])}">
  <h2>{i}. {_esc(t)}</h2>
  <p class="meta">来源：{_esc(_source_line(r))} · 日期：{_esc(pub_date)}</p>
  <p class="meta">质量/注意力分：{q}/100 · 业务价值：{bv if bv is not None else '—'}/100
    {('— ' + _esc(bv_note)) if bv_note else ''}</p>
  <h3>摘要</h3>
  <blockquote>{_esc(summ)}</blockquote>
  <h3>事实核查</h3>
  <ul class="fc">{''.join(fc_lis)}</ul>
  <div class="orig-wrap">
    <button type="button" class="orig-toggle bd-root" aria-expanded="false">
      <span class="bd-pill" aria-hidden="true">
        <svg class="bd-chevs" width="14" height="16" viewBox="0 0 14 16">
          <g class="bd-dots" fill="#5c2410">
            <circle class="bd-dot" cx="2" cy="2" r="1" style="animation-delay:0s"/>
            <circle class="bd-dot" cx="5" cy="5" r="1" style="animation-delay:0.08s"/>
            <circle class="bd-dot" cx="8" cy="8" r="1" style="animation-delay:0.16s"/>
            <circle class="bd-dot" cx="5" cy="11" r="1" style="animation-delay:0.24s"/>
            <circle class="bd-dot" cx="2" cy="14" r="1" style="animation-delay:0.32s"/>
            <circle class="bd-dot" cx="6" cy="2" r="1" style="animation-delay:0.08s"/>
            <circle class="bd-dot" cx="9" cy="5" r="1" style="animation-delay:0.16s"/>
            <circle class="bd-dot" cx="12" cy="8" r="1" style="animation-delay:0.24s"/>
            <circle class="bd-dot" cx="9" cy="11" r="1" style="animation-delay:0.32s"/>
            <circle class="bd-dot" cx="6" cy="14" r="1" style="animation-delay:0.4s"/>
          </g>
        </svg>
      </span>
      <span class="bd-label" data-label="查看全文">查看全文</span>
    </button>
    <div class="orig-panel" aria-hidden="true">
      <div class="orig-scroll">
        <div class="orig-body">{body_html}</div>
      </div>
      <div class="orig-foot">{link_html}</div>
    </div>
  </div>
</article>
"""
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{_esc(title)}</title>
<!-- GSAP: light height tween only -->
<script src="https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js" defer></script>
<style>
  :root {{
    --bg: #f5f2eb;
    --card: #fffcf7;
    --text: #1c1917;
    --muted: #78716c;
    --border: #e7e0d4;
    --accent: #1d4ed8;
    --panel: #f0ebe3;
    --focus-bg: #eef4ff;
    --focus-border: #bfd0f5;
    --serif: "Iowan Old Style", "Palatino Linotype", Palatino, "Songti SC",
             "Noto Serif CJK SC", "Source Han Serif SC", "STSong", Georgia, serif;
    --sans: "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: var(--serif);
    line-height: 1.75; font-size: 17px;
    -webkit-font-smoothing: antialiased;
  }}
  .wrap {{
    max-width: 960px;          /* desktop reading width */
    margin: 0 auto;
    padding: 40px 32px 72px;
  }}
  h1 {{
    font-family: var(--serif);
    font-size: 2rem; margin: 0 0 8px; letter-spacing: -0.02em;
    font-weight: 700; line-height: 1.25;
  }}
  .sub {{
    color: var(--muted); margin: 0 0 28px; font-size: 0.95rem;
    font-family: var(--sans);
  }}

  /* —— 优先关注：完整文案，不截断 —— */
  .focus {{
    background: var(--focus-bg);
    border: 1px solid var(--focus-border);
    border-radius: 14px;
    padding: 20px 22px 18px;
    margin: 0 0 28px;
  }}
  .focus > h2 {{
    margin: 0 0 14px; font-size: 1.05rem; font-family: var(--sans);
    font-weight: 650; letter-spacing: 0.02em; color: #1e3a5f;
  }}
  .focus-list {{ list-style: none; margin: 0; padding: 0; counter-reset: fi; }}
  .focus-item {{
    counter-increment: fi;
    margin: 0 0 14px; padding: 12px 14px 12px 44px; position: relative;
    background: rgba(255,255,255,0.72); border-radius: 10px;
    border: 1px solid rgba(29,78,216,0.08);
  }}
  .focus-item:last-child {{ margin-bottom: 0; }}
  .focus-item::before {{
    content: counter(fi);
    position: absolute; left: 12px; top: 12px;
    width: 24px; height: 24px; border-radius: 999px;
    background: #1e3a5f; color: #fff;
    font-family: var(--sans); font-size: 12px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
  }}
  .focus-title {{
    display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px;
    margin: 0 0 6px; line-height: 1.45;
  }}
  .focus-title strong {{ font-weight: 650; font-size: 1.02rem; }}
  .focus-bv {{
    font-family: var(--sans); font-size: 11px; font-weight: 700;
    letter-spacing: 0.04em; color: #1d4ed8;
    background: #dbeafe; border-radius: 999px; padding: 2px 8px;
  }}
  .focus-note {{
    margin: 0; color: #44403c; font-size: 0.98rem; line-height: 1.65;
    /* no line-clamp, no ellipsis — full summary text */
  }}

  .card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 14px;
    padding: 22px 26px 18px; margin: 0 0 20px;
    box-shadow: 0 1px 0 rgba(28,25,23,0.03);
  }}
  .card h2 {{
    font-size: 1.22rem; margin: 0 0 10px; line-height: 1.4;
    font-weight: 700;
  }}
  .meta {{
    color: var(--muted); font-size: 0.88rem; margin: 3px 0;
    font-family: var(--sans);
  }}
  h3 {{
    font-size: 0.82rem; margin: 16px 0 8px; color: var(--muted);
    font-weight: 650; font-family: var(--sans); letter-spacing: 0.04em;
    text-transform: uppercase;
  }}
  blockquote {{
    margin: 0; padding: 12px 14px; border-left: 3px solid var(--accent);
    background: var(--panel); border-radius: 0 8px 8px 0; color: #292524;
    font-size: 1.02rem;
  }}
  .fc {{ list-style: none; padding: 0; margin: 0; }}
  .fc-line {{
    margin: 8px 0;
    display: flex;
    flex-wrap: nowrap;
    align-items: flex-start;
    gap: 8px;
  }}
  .fc-plain {{ color: var(--muted); font-family: var(--sans); }}
  .badge {{
    display: inline-flex;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
    font-size: 12px;
    font-weight: 600;
    font-family: var(--sans);
    padding: 2px 8px 2px 4px;
    border-radius: 999px;
    white-space: nowrap;
    line-height: 1.4;
  }}
  .badge.ok {{ background: #dcfce7; color: #166534; }}
  .badge.warn {{ background: #fef3c7; color: #92400e; }}
  .badge.bad {{ background: #fee2e2; color: #991b1b; }}
  .badge svg {{ flex-shrink: 0; display: block; }}
  .badge-lab {{ letter-spacing: 0.02em; }}
  .fc-text {{
    flex: 1 1 auto;
    min-width: 0;
    line-height: 1.55;
    color: #1c1917;
  }}

  /* —— 折叠按钮：全宽 + 珊瑚 pill + 点阵箭头（减速） —— */
  .orig-wrap {{
    margin-top: 18px;
    border-top: 1px dashed var(--border);
    padding-top: 14px;
    padding-left: 4px;
    padding-right: 4px;
  }}
  .bd-root {{
    --coral-from: #ffb088;
    --coral-to: #f56b42;
    --coral-deep: #9a3412;
    --shell-from: #f7efe6;
    --shell-to: #ebe0d4;
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    height: 48px;
    padding: 0 12px 0 4px;
    border: 1px solid #e0d2c4;
    border-radius: 12px;
    cursor: pointer;
    overflow: hidden;
    font-family: var(--sans);
    color: var(--coral-deep);
    background: linear-gradient(180deg, var(--shell-from) 0%, var(--shell-to) 100%);
    box-shadow:
      inset 0 1px 0 rgba(255,255,255,0.75),
      0 3px 10px rgba(154, 52, 18, 0.08);
    transition: transform 0.15s ease, box-shadow 0.2s ease;
  }}
  .bd-root:hover {{
    transform: translateY(-1px);
    box-shadow:
      inset 0 1px 0 rgba(255,255,255,0.85),
      0 6px 16px rgba(245, 107, 66, 0.18);
  }}
  .bd-root:active {{ transform: scale(0.985); }}
  .bd-root:focus-visible {{ outline: 2px solid #f56b42; outline-offset: 2px; }}
  .bd-pill {{
    position: absolute;
    top: 4px; left: 4px; bottom: 4px;
    width: 40px;
    display: flex; align-items: center; justify-content: center;
    border-radius: 8px;
    background: linear-gradient(180deg, var(--coral-from) 0%, var(--coral-to) 100%);
    box-shadow:
      inset 0 1px 0 rgba(255,255,255,0.45),
      inset 0 -2px 4px rgba(0,0,0,0.10),
      0 2px 4px rgba(245, 107, 66, 0.2);
    transition: width 0.26s cubic-bezier(0.22,1,0.36,1);
    z-index: 1;
  }}
  .bd-root:hover .bd-pill,
  .bd-root:focus-visible .bd-pill,
  .bd-root[aria-expanded="true"] .bd-pill {{
    width: calc(100% - 8px);
  }}
  .bd-label {{
    position: absolute;
    left: 0; right: 0;
    top: 50%;
    transform: translateY(-50%);
    z-index: 2;
    margin: 0;
    text-align: center;
    font-size: 15px;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: var(--coral-deep);
    pointer-events: none;
    opacity: 1;
  }}
  .bd-label .bd-ch {{
    display: inline-block;
    will-change: contents, opacity, text-shadow;
  }}
  /* 展开态：文案保持消失，直到再点折叠 */
  .bd-root[aria-expanded="true"] .bd-label {{
    opacity: 0 !important;
    visibility: hidden;
    pointer-events: none;
  }}
  @keyframes bd-dot-wave {{
    0%, 70%, 100% {{ opacity: 0.28; transform: scale(0.78); }}
    35% {{ opacity: 1; transform: scale(1.06); }}
  }}
  .bd-dot {{
    transform-box: fill-box;
    transform-origin: center;
    /* 更慢的呼吸节奏 */
    animation: bd-dot-wave 2.8s cubic-bezier(0.45,0.05,0.55,0.95) infinite;
  }}
  .bd-root:hover .bd-dot,
  .bd-root:focus-visible .bd-dot {{
    animation-duration: 1.6s;
  }}
  .bd-root[aria-expanded="true"] .bd-dot {{
    animation-duration: 2.2s;
  }}
  @media (prefers-reduced-motion: reduce) {{
    .bd-dot {{ animation: none; opacity: 1; }}
    .bd-pill {{ transition: none; }}
  }}
  .orig-panel {{
    height: 0; overflow: hidden; will-change: height;
  }}
  .orig-scroll {{
    margin-top: 12px;
    max-height: 420px;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 18px 20px;
    font-size: 1.02rem;
    color: #1c1917;
    line-height: 1.8;
  }}
  .orig-body {{ white-space: normal; word-break: break-word; font-family: var(--serif); }}
  .orig-body h2 {{ font-size: 1.12rem; margin: 1.15em 0 0.5em; color: var(--text); }}
  .orig-body h3 {{ font-size: 1.04rem; margin: 1.05em 0 0.45em; color: #44403c; }}
  .orig-body h4 {{ font-size: 0.98rem; margin: 0.95em 0 0.4em; color: #57534e; }}
  .orig-body p {{ margin: 0 0 0.85em; }}
  .orig-body p:last-child {{ margin-bottom: 0; }}
  .orig-body ul, .orig-body ol {{ margin: 0 0 0.85em; padding-left: 1.4em; }}
  .orig-body li {{ margin: 0.3em 0; }}
  .orig-body blockquote {{
    margin: 0.7em 0; padding: 10px 14px; border-left: 3px solid #a8a29e;
    background: #e7e5e4; border-radius: 0 6px 6px 0; color: #44403c;
  }}
  .orig-body hr {{ border: 0; border-top: 1px solid #d6d3d1; margin: 1.1em 0; }}
  .orig-body code {{
    font-family: ui-monospace, Consolas, monospace; font-size: 0.88em;
    background: #e7e5e4; padding: 0.1em 0.35em; border-radius: 4px;
  }}
  .orig-body a {{ color: var(--accent); text-decoration: none; }}
  .orig-body a:hover {{ text-decoration: underline; }}
  .orig-body strong {{ font-weight: 700; color: #0c0a09; }}
  .orig-body .empty {{ color: var(--muted); }}
  .orig-foot {{ margin-top: 10px; font-size: 0.85rem; font-family: var(--sans); }}
  .orig-foot .ext {{ color: var(--muted); }}

  .foot {{
    text-align: center; color: var(--muted); font-size: 0.88rem;
    margin-top: 36px; font-family: var(--sans);
  }}
  @media print {{
    .orig-panel {{ height: auto !important; overflow: visible !important; }}
    .orig-scroll {{ max-height: none; overflow: visible; }}
    .bd-root {{ display: none; }}
    .wrap {{ max-width: none; }}
  }}
  @media (max-width: 720px) {{
    .wrap {{ padding: 24px 16px 48px; }}
    body {{ font-size: 16px; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <h1>{_esc(title)}</h1>
  <p class="sub">from moni by TQ · {n} 篇文章 · 按业务价值排序 · 全文已内嵌（离线可读）</p>

  <section class="focus">
    <h2>优先关注</h2>
    <ol class="focus-list">{''.join(focus_items) if focus_items else '<li class="focus-item"><p class="focus-note">暂无</p></li>'}</ol>
  </section>

  {''.join(cards)}

  <p class="foot">Curated by moni — a TQ project.</p>
</div>
<script>
(function () {{
  var MATRIX = "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン0123456789";
  var ACCENT = "#f56b42";

  function panelInnerHeight(panel) {{
    var scroll = panel.querySelector('.orig-scroll');
    var foot = panel.querySelector('.orig-foot');
    var h = 0;
    if (scroll) h += scroll.offsetHeight;
    if (foot) h += foot.offsetHeight + 8;
    return h;
  }}

  function openPanel(btn, panel) {{
    btn.setAttribute('aria-expanded', 'true');
    panel.setAttribute('aria-hidden', 'false');
    var target = panelInnerHeight(panel);
    if (window.gsap) {{
      window.gsap.killTweensOf(panel);
      window.gsap.fromTo(panel,
        {{ height: 0 }},
        {{ height: target, duration: 0.28, ease: 'power2.out',
           onComplete: function () {{ panel.style.height = 'auto'; }} }}
      );
    }} else {{
      panel.style.height = target + 'px';
      panel.style.transition = 'height 0.28s ease';
      setTimeout(function () {{ panel.style.height = 'auto'; }}, 300);
    }}
    // 展开后文案渐隐并保持消失
    var label = btn.querySelector('.bd-label');
    if (label) hideLabelStay(label);
  }}

  function closePanel(btn, panel) {{
    btn.setAttribute('aria-expanded', 'false');
    panel.setAttribute('aria-hidden', 'true');
    var start = panel.scrollHeight;
    panel.style.height = start + 'px';
    void panel.offsetHeight;
    if (window.gsap) {{
      window.gsap.killTweensOf(panel);
      window.gsap.to(panel, {{ height: 0, duration: 0.22, ease: 'power2.in' }});
    }} else {{
      panel.style.transition = 'height 0.22s ease';
      panel.style.height = '0px';
    }}
    // 折叠后文案渐回
    var label = btn.querySelector('.bd-label');
    if (label) matrixFadeIn(label);
  }}

  /* —— label: matrix scramble → fade out on hover; fade back on leave —— */
  function clearLabelTimers(label) {{
    if (label._bdIntervals) {{
      label._bdIntervals.forEach(clearInterval);
      label._bdIntervals = [];
    }}
    if (label._bdTimeouts) {{
      label._bdTimeouts.forEach(clearTimeout);
      label._bdTimeouts = [];
    }}
    if (window.gsap) window.gsap.killTweensOf(label.querySelectorAll('.bd-ch'));
  }}

  function setPlainLabel(label, text, color) {{
    clearLabelTimers(label);
    label.style.visibility = 'visible';
    label.textContent = text;
    label.style.opacity = '1';
    if (color) label.style.color = color;
    else label.style.color = '';
  }}

  /** 渐隐并保持消失（展开态用） */
  function hideLabelStay(label) {{
    clearLabelTimers(label);
    var finalText = label.getAttribute('data-label') || '查看全文';
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    function done() {{
      label.style.opacity = '0';
      label.style.visibility = 'hidden';
      label.textContent = finalText;
    }}
    if (reduce || !window.gsap) {{
      if (window.gsap) {{
        window.gsap.to(label, {{ opacity: 0, duration: 0.28, ease: 'power2.out', onComplete: done }});
      }} else {{
        label.style.transition = 'opacity 0.28s ease';
        label.style.opacity = '0';
        setTimeout(done, 300);
      }}
      return;
    }}
    // 轻量 matrix 后渐隐，并保持消失
    matrixFadeOut(label, true);
  }}

  /**
   * @param stayHidden  true = 渐隐后保持隐藏（选中/展开）
   */
  function matrixFadeOut(label, stayHidden) {{
    var finalText = label.getAttribute('data-label') || '查看全文';
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce || !window.gsap) {{
      if (window.gsap) {{
        window.gsap.to(label, {{
          opacity: 0, duration: 0.28, ease: 'power2.out',
          onComplete: function () {{
            if (stayHidden) {{
              label.style.visibility = 'hidden';
              label.textContent = finalText;
            }}
          }}
        }});
      }} else {{
        label.style.transition = 'opacity 0.28s ease';
        label.style.opacity = '0';
        if (stayHidden) setTimeout(function () {{
          label.style.visibility = 'hidden';
          label.textContent = finalText;
        }}, 300);
      }}
      return;
    }}
    clearLabelTimers(label);
    label._bdIntervals = [];
    label._bdTimeouts = [];
    label.style.visibility = 'visible';
    label.innerHTML = '';
    label.style.opacity = '1';
    var chars = finalText.split('');
    var spans = [];
    chars.forEach(function (ch) {{
      var span = document.createElement('span');
      span.className = 'bd-ch';
      span.style.display = 'inline-block';
      span.style.color = ACCENT;
      span.style.textShadow = '0 0 8px ' + ACCENT;
      span.textContent = ch === ' ' ? '\\u00A0' : MATRIX[Math.floor(Math.random() * MATRIX.length)];
      label.appendChild(span);
      spans.push(span);
    }});
    var locked = 0;
    spans.forEach(function (span, i) {{
      if (chars[i] === ' ') {{
        span.textContent = '\\u00A0';
        locked++;
        return;
      }}
      var scramble = setInterval(function () {{
        if (!span._locked) {{
          span.textContent = MATRIX[Math.floor(Math.random() * MATRIX.length)];
        }}
      }}, 55);
      label._bdIntervals.push(scramble);
      var lockDelay = i * 45 + Math.random() * 100;
      var t = setTimeout(function () {{
        clearInterval(scramble);
        span._locked = true;
        span.textContent = chars[i];
        locked++;
        window.gsap.fromTo(span,
          {{ textShadow: '0 0 12px ' + ACCENT }},
          {{ duration: 0.32, textShadow: '0 0 0px transparent', ease: 'power2.out',
             onComplete: function () {{
               if (locked >= spans.length) {{
                 window.gsap.to(label, {{
                   opacity: 0, duration: 0.28, ease: 'power2.out',
                   onComplete: function () {{
                     if (stayHidden) {{
                       label.style.visibility = 'hidden';
                       label.textContent = finalText;
                     }}
                   }}
                 }});
               }}
             }} }}
        );
      }}, lockDelay);
      label._bdTimeouts.push(t);
    }});
  }}

  function matrixFadeIn(label) {{
    var finalText = label.getAttribute('data-label') || '查看全文';
    clearLabelTimers(label);
    label.style.visibility = 'visible';
    if (window.gsap) {{
      setPlainLabel(label, finalText, '');
      window.gsap.fromTo(label, {{ opacity: 0 }}, {{ opacity: 1, duration: 0.32, ease: 'power2.out' }});
    }} else {{
      setPlainLabel(label, finalText, '');
      label.style.transition = 'opacity 0.32s ease';
      label.style.opacity = '1';
    }}
  }}

  document.addEventListener('click', function (e) {{
    var btn = e.target.closest('.orig-toggle');
    if (!btn) return;
    var wrap = btn.closest('.orig-wrap');
    if (!wrap) return;
    var panel = wrap.querySelector('.orig-panel');
    if (!panel) return;
    var open = btn.getAttribute('aria-expanded') === 'true';
    if (open) closePanel(btn, panel);
    else openPanel(btn, panel);
  }});

  document.querySelectorAll('.bd-root').forEach(function (btn) {{
    var label = btn.querySelector('.bd-label');
    if (!label) return;
    btn.addEventListener('mouseenter', function () {{
      if (btn.getAttribute('aria-expanded') === 'true') return;
      matrixFadeOut(label, false);
    }});
    btn.addEventListener('mouseleave', function () {{
      // 展开态：文案保持消失，不恢复
      if (btn.getAttribute('aria-expanded') === 'true') return;
      matrixFadeIn(label);
    }});
  }});
}})();
</script>
</body>
</html>
"""


def write_brief_html(
    db_path: str,
    article_ids: list[str],
    output_path: str | Path,
    *,
    title: str | None = None,
) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    html_doc = render_brief_html(db_path, article_ids, title=title)
    out.write_text(html_doc, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render moni one-click brief HTML")
    p.add_argument("--db", required=True)
    p.add_argument("--article-ids", required=True, help="JSON list of ids")
    p.add_argument("--output", required=True)
    p.add_argument("--title", default=None)
    args = p.parse_args(argv)
    try:
        ids = json.loads(args.article_ids)
    except json.JSONDecodeError:
        import ast

        ids = ast.literal_eval(args.article_ids)
    path = write_brief_html(args.db, list(ids), args.output, title=args.title)
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
