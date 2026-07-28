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
                       NULL as brief_summary,
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
    """Return list of (badge_kind, text) for fact-check lines."""
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
    for p in parts[:2]:
        p = p.strip()
        if not p:
            continue
        if "【已核实】" in p or "已核实" in p:
            kind = "ok"
        elif "【存疑】" in p or "存疑" in p:
            kind = "bad"
        elif "【待核实】" in p or "待核实" in p:
            kind = "warn"
        elif "无需" in p:
            kind = "none"
        else:
            kind = "warn"
        out.append((kind, p))
    return out or [("none", "无需核查")]


def _badge_svg(kind: str) -> str:
    if kind == "ok":
        return (
            '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">'
            '<circle cx="8" cy="8" r="8" fill="#16a34a"/>'
            '<path d="M4.5 8.2l2.2 2.2 4.8-4.8" stroke="#fff" stroke-width="1.6" '
            'fill="none" stroke-linecap="round"/></svg>'
        )
    if kind == "bad":
        return (
            '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">'
            '<circle cx="8" cy="8" r="8" fill="#dc2626"/>'
            '<path d="M8 4.2v5.2" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/>'
            '<circle cx="8" cy="12" r="1" fill="#fff"/></svg>'
        )
    if kind == "warn":
        return (
            '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">'
            '<circle cx="8" cy="8" r="8" fill="#d97706"/>'
            '<path d="M8 4.2v5.2" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/>'
            '<circle cx="8" cy="12" r="1" fill="#fff"/></svg>'
        )
    return ""


def _badge_label(kind: str) -> str:
    return {"ok": "已核实", "warn": "待核实", "bad": "存疑", "none": ""}.get(kind, "")


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
        note = (r["business_value_note"] or "").strip()
        if len(note) > 40:
            note = note[:37] + "…"
        if not note:
            note = "见正文"
        bv_s = str(bv) if bv is not None else "—"
        focus_items.append(
            f"<li><strong>{_esc(t)}</strong>（业务价值 {_esc(bv_s)}）— {_esc(note)}</li>"
        )

    cards: list[str] = []
    for i, r in enumerate(rows, 1):
        t = (r["title"] or "").replace("\n", " ").strip()
        body = (r["raw_content"] or "").strip() or "（无正文）"
        # plain text body for offline reading
        body_html = _esc(body).replace("\n", "<br>\n")
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
        for kind, text in _fc_items(r):
            if kind == "none":
                fc_lis.append(f"<li class='fc-plain'>{_esc(text)}</li>")
            else:
                fc_lis.append(
                    f"<li><span class='badge {kind}'>{_badge_svg(kind)} {_esc(_badge_label(kind))}</span> "
                    f"<span>{_esc(text)}</span></li>"
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
    <button type="button" class="orig-toggle" aria-expanded="false">
      <span class="chev" aria-hidden="true">▸</span> 查看全文（离线可读）
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
<!-- GSAP: light height tween only; no plugins, no scroll-trigger -->
<script src="https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js" defer></script>
<style>
  :root {{
    --bg: #f8fafc; --card: #ffffff; --text: #0f172a; --muted: #64748b;
    --border: #e2e8f0; --accent: #2563eb; --panel: #f1f5f9;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: "Segoe UI", "Microsoft YaHei", "PingFang SC", system-ui, sans-serif;
    line-height: 1.65; font-size: 15px;
  }}
  .wrap {{ max-width: 720px; margin: 0 auto; padding: 28px 18px 56px; }}
  h1 {{ font-size: 1.65rem; margin: 0 0 6px; letter-spacing: -0.02em; }}
  .sub {{ color: var(--muted); margin: 0 0 22px; font-size: 0.92rem; }}
  .focus {{
    background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 12px;
    padding: 14px 16px 14px 32px; margin: 0 0 22px;
  }}
  .focus li {{ margin: 6px 0; }}
  .card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 18px 20px 16px; margin: 0 0 16px;
  }}
  .card h2 {{ font-size: 1.08rem; margin: 0 0 8px; line-height: 1.4; }}
  .meta {{ color: var(--muted); font-size: 0.88rem; margin: 3px 0; }}
  h3 {{ font-size: 0.85rem; margin: 14px 0 6px; color: var(--muted); font-weight: 600; }}
  blockquote {{
    margin: 0; padding: 10px 12px; border-left: 3px solid var(--accent);
    background: var(--panel); border-radius: 0 8px 8px 0; color: #334155;
  }}
  .fc {{ list-style: none; padding: 0; margin: 0; }}
  .fc li {{
    margin: 8px 0; display: flex; gap: 8px; align-items: flex-start; flex-wrap: wrap;
  }}
  .fc-plain {{ color: var(--muted); }}
  .badge {{
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 999px;
    white-space: nowrap;
  }}
  .badge.ok {{ background: #dcfce7; color: #166534; }}
  .badge.warn {{ background: #fef3c7; color: #92400e; }}
  .badge.bad {{ background: #fee2e2; color: #991b1b; }}
  .badge svg {{ flex-shrink: 0; }}

  /* 原文折叠：默认关；展开后内部固定高度滚动 */
  .orig-wrap {{ margin-top: 14px; border-top: 1px dashed var(--border); padding-top: 10px; }}
  .orig-toggle {{
    display: inline-flex; align-items: center; gap: 6px;
    border: 1px solid var(--border); background: #fff; color: var(--text);
    border-radius: 8px; padding: 6px 12px; font-size: 0.88rem; cursor: pointer;
    font-family: inherit;
  }}
  .orig-toggle:hover {{ border-color: #94a3b8; }}
  .orig-toggle .chev {{
    display: inline-block; width: 1em; transition: transform 0.2s ease;
    color: var(--muted);
  }}
  .orig-toggle[aria-expanded="true"] .chev {{ transform: rotate(90deg); }}
  .orig-panel {{
    height: 0; overflow: hidden; will-change: height;
  }}
  .orig-scroll {{
    margin-top: 10px;
    max-height: 280px;           /* 固定可视高度，不无限增高 */
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 14px;
    font-size: 0.9rem;
    color: #1e293b;
  }}
  .orig-body {{ white-space: normal; word-break: break-word; }}
  .orig-foot {{ margin-top: 8px; font-size: 0.82rem; }}
  .orig-foot .ext {{ color: var(--muted); }}

  .foot {{
    text-align: center; color: var(--muted); font-size: 0.85rem; margin-top: 28px;
  }}
  @media print {{
    .orig-panel {{ height: auto !important; overflow: visible !important; }}
    .orig-scroll {{ max-height: none; overflow: visible; }}
    .orig-toggle {{ display: none; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <h1>{_esc(title)}</h1>
  <p class="sub">from moni by TQ · {n} 篇文章 · 按业务价值排序 · 全文已内嵌（离线可读）</p>

  <section class="focus">
    <h2 style="margin:0 0 8px;font-size:1rem;">优先关注</h2>
    <ol>{''.join(focus_items) if focus_items else '<li>暂无</li>'}</ol>
  </section>

  {''.join(cards)}

  <p class="foot">Curated by moni — a TQ project.</p>
</div>
<script>
(function () {{
  // Lightweight GSAP height tween. Prefer gsap if loaded; CSS fallback otherwise.
  function panelInnerHeight(panel) {{
    // Measure content height of the scroll box + foot (capped by max-height CSS).
    var scroll = panel.querySelector('.orig-scroll');
    var foot = panel.querySelector('.orig-foot');
    var h = 0;
    if (scroll) {{
      // offsetHeight respects max-height when content overflows
      h += scroll.offsetHeight;
    }}
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
  }}

  function closePanel(btn, panel) {{
    btn.setAttribute('aria-expanded', 'false');
    panel.setAttribute('aria-hidden', 'true');
    var start = panel.scrollHeight;
    panel.style.height = start + 'px';
    // force reflow
    void panel.offsetHeight;
    if (window.gsap) {{
      window.gsap.killTweensOf(panel);
      window.gsap.to(panel, {{ height: 0, duration: 0.22, ease: 'power2.in' }});
    }} else {{
      panel.style.transition = 'height 0.22s ease';
      panel.style.height = '0px';
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
