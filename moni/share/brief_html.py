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
    THIN_CONTENT_CHARS,
    compute_heuristic_quality,
    pick_source_summary,
    _format_date,
)

# Display copy when raw body is missing / too short (aligned with PDF brief prompt v1.0.15)
_THIN_SUMMARY_COPY = "仅标题/短帖入库，正文未抓取，无可展开摘要。"

_EMPTY_SUM_MARKERS = frozenset(
    {"", "素材不足", "（无摘要）", "无摘要", "（暂无可用摘要）", "—", "-"}
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


def _raw_len(row: sqlite3.Row) -> int:
    try:
        return len((row["raw_content"] or "").strip())
    except (KeyError, IndexError, TypeError):
        return 0


def _is_thin_article(row: sqlite3.Row) -> bool:
    return _raw_len(row) < THIN_CONTENT_CHARS


# Below this → treat as 正文未入库 for exclusion (stricter than BV thin=200)
_MIN_BODY_CHARS = 80


def _has_body(row: sqlite3.Row) -> bool:
    """True when we can embed a real article body (not empty title-only).

    Note: BV ``THIN_CONTENT_CHARS`` (200) is for scoring; exclusion only
    requires a minimal readable body (~80 chars).
    """
    try:
        if (row["brief_body_md"] or "").strip():
            return True
    except (KeyError, IndexError, TypeError):
        pass
    return _raw_len(row) >= _MIN_BODY_CHARS


def _field(row: sqlite3.Row | dict, key: str) -> str:
    try:
        v = row[key]
    except (KeyError, IndexError, TypeError):
        return ""
    return (v or "").strip() if v is not None else ""


def _usable_summary_raw(row: sqlite3.Row) -> tuple[str, str] | None:
    """Return (text, origin) if a real summary exists. origin: ai|bb|source.

    Prefer the *longest* among BB / AI / original / brief so HTML cards
    get more substance.  (brief_summary is often ≤80–100 chars by design.)
    """
    candidates: list[tuple[str, str]] = []
    for key, origin in (
        ("bb_summary", "bb"),
        ("ai_summary", "ai"),
        ("summary", "source"),
        ("bb_one_sentence", "bb"),
        ("brief_summary", "ai"),
    ):
        t = _field(row, key)
        if t and t not in _EMPTY_SUM_MARKERS:
            candidates.append((t, origin))
    if not candidates:
        # pick_source_summary as last resort
        full, origin = pick_source_summary(row)
        full = (full or "").strip()
        if full and full not in _EMPTY_SUM_MARKERS:
            o = (origin or "").lower()
            if "bb" in o:
                return full, "bb"
            if "ai" in o or "moni" in o:
                return full, "ai"
            return full, "source"
        return None
    # longest text wins; tie-break prefer non-brief sources already listed first
    candidates.sort(key=lambda x: len(x[0]), reverse=True)
    text, origin = candidates[0]
    # If longest is very short brief_summary but body exists, expand from body lead
    if len(text) < 120 and _has_body(row):
        lead = _body_lead_paragraph(row, max_chars=420)
        if lead and len(lead) > len(text) + 40:
            return lead, "body_lead"
    return text, origin


def _body_lead_paragraph(row: sqlite3.Row, max_chars: int = 420) -> str:
    """First usable paragraph(s) from body as extended 导读 when AI summary is short."""
    raw = _field(row, "brief_body_md") or _field(row, "raw_content")
    if not raw:
        return ""
    raw = _strip_frontmatter(raw)
    # drop markdown headings markers for lead
    lines = []
    for line in raw.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("---"):
            if lines:
                break
            continue
        s = re.sub(r"^[-*+]\s+", "", s)
        s = re.sub(r"^\d+[.)、]\s+", "", s)
        lines.append(s)
        blob = "".join(lines)
        if len(blob) >= max_chars:
            break
    text = "".join(lines) if lines else ""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        # cut at sentence boundary when possible
        cut = text[: max_chars + 1]
        for sep in ("。", "！", "？", ". ", "! ", "? "):
            i = cut.rfind(sep)
            if i >= max_chars // 2:
                return text[: i + len(sep)].strip()
        return text[:max_chars].rstrip() + "…"
    return text


def _summary_text(row: sqlite3.Row) -> str:
    """Prefer fullest available summary; never dump bare 素材不足 into the box."""
    hit = _usable_summary_raw(row)
    if hit:
        return hit[0]
    if _is_thin_article(row):
        return _THIN_SUMMARY_COPY
    return "（暂无可用摘要）"


def _summary_with_meta(row: sqlite3.Row) -> tuple[str, str, bool]:
    """(text, origin, is_ai) for rendering AI marks."""
    hit = _usable_summary_raw(row)
    if hit:
        text, origin = hit
        return text, origin, origin in ("ai", "bb")
    if _is_thin_article(row):
        return _THIN_SUMMARY_COPY, "thin", False
    return "（暂无可用摘要）", "none", False


def _bv_usable(row: sqlite3.Row) -> bool:
    """Business judgment successfully produced (not empty / 素材不足 stub)."""
    try:
        score = row["business_value_score"]
    except (KeyError, IndexError, TypeError):
        score = None
    try:
        raw_note = row["business_value_note"]
    except (KeyError, IndexError, TypeError):
        raw_note = None
    note = _bv_note_display(raw_note)
    if score is None and not note:
        return False
    if note and "素材不足" in note:
        return False
    if score is not None and int(score) <= 40 and (not note or "素材不足" in note):
        return False
    return True


def _fc_usable(row: sqlite3.Row) -> bool:
    """Fact-check produced real output (「无需核查」counts as success)."""
    items = _fc_items(row)
    if not items:
        return False
    if len(items) == 1 and items[0][0] == "none":
        t = items[0][1]
        if "无正文" in t or "失败" in t or not t.strip():
            return False
        return True  # 无需核查
    # has at least one claim with text
    return any((t or "").strip() and t not in ("（无说明）",) for _, t in items)


def _article_exclusion_reason(row: sqlite3.Row) -> str | None:
    """If article should be dropped from HTML brief, return short reason.

    Drop when 正文未入库, or 摘要 / 业务判断 / 事实核查 any pillar fails.
    """
    reasons: list[str] = []
    if not _has_body(row):
        reasons.append("正文未入库")
    if _usable_summary_raw(row) is None:
        reasons.append("无可用摘要")
    if not _bv_usable(row):
        reasons.append("业务判断不足")
    if not _fc_usable(row):
        reasons.append("事实核查不足")
    return "、".join(reasons) if reasons else None


def find_unready_articles(db_path: str, article_ids: list[str]) -> list[dict[str, Any]]:
    """Articles that would be excluded from a valuable brief (for API / footer)."""
    rows = _load_rows(db_path, article_ids)
    bad: list[dict[str, Any]] = []
    for r in rows:
        reason = _article_exclusion_reason(r)
        if not reason:
            continue
        missing: list[str] = []
        if not _has_body(r):
            missing.append("body")
        if _usable_summary_raw(r) is None:
            missing.append("summary")
        if not _bv_usable(r):
            missing.append("business_value")
        if not _fc_usable(r):
            missing.append("fact_check")
        bad.append(
            {
                "id": str(r["id"]),
                "title": _display_title(r),
                "missing": missing,
                "reason": reason,
                "hint": "需补全文抓取或补跑摘要/业务价值/事实核查后再纳入简报",
            }
        )
    return bad


def _title_looks_bad(title: str) -> bool:
    t = (title or "").strip()
    if not t or len(t) < 4:
        return True
    if re.search(r"https?://|t\.co/|bit\.ly/", t, re.I):
        return True
    if re.match(r"^(Apply|Link|URL|http|https|www\.)\b", t, re.I):
        return True
    # mostly punctuation / URL crumbs
    letters = re.sub(r"[\W_]+", "", t, flags=re.UNICODE)
    if len(letters) < 3:
        return True
    return False


def _derive_title_from_body(row: sqlite3.Row) -> str | None:
    """Heuristic title from body when stored title is a URL / Apply link."""
    raw = _field(row, "brief_body_md") or _field(row, "raw_content")
    if not raw or len(raw.strip()) < 20:
        return None
    raw = _strip_frontmatter(raw)
    for line in raw.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^#{1,3}\s+(.+)$", s)
        if m:
            cand = m.group(1).strip()
            if not _title_looks_bad(cand):
                return cand[:80]
        if s.startswith(">") or s.startswith("-") or s.startswith("*"):
            continue
        # first prose line
        cand = re.sub(r"\s+", " ", s)
        cand = re.sub(r"https?://\S+", "", cand).strip(" -—|:")
        if len(cand) >= 8 and not _title_looks_bad(cand):
            # first sentence-ish
            for sep in ("。", "！", "？", ". "):
                i = cand.find(sep)
                if 8 <= i <= 60:
                    return cand[: i + (0 if sep == ". " else 1)].strip()
            return cand[:60].rstrip("…") + ("…" if len(cand) > 60 else "")
    return None


def _display_title(row: sqlite3.Row) -> str:
    """Strip leading [publisher]; fix URL-like titles from body when possible."""
    t = (row["title"] or "").replace("\n", " ").strip()
    pub = (row["publisher"] or "").strip()
    t2 = re.sub(r"^\[[^\]]{1,40}\]\s*", "", t)
    t2 = re.sub(r"^【[^】]{1,40}】\s*", "", t2)
    if pub and t2.startswith(pub):
        t2 = t2[len(pub) :].lstrip(" ·:-—|")
    t2 = t2 or t
    if _title_looks_bad(t2):
        derived = _derive_title_from_body(row)
        if derived:
            return derived
    return t2


def _bv_note_display(note: str | None, *, for_focus: bool = False) -> str:
    """Shorten enrichment boilerplate for UI."""
    n = (note or "").strip()
    if not n:
        return ""
    if "素材不足" in n and ("正文过短" in n or "无法可靠" in n):
        return "素材不足（正文过短）" if for_focus else "素材不足 · 正文过短"
    if n == "素材不足":
        return n
    return n


def _bv_score_tier(score: int | None) -> str:
    """CSS modifier for BV badge by score band."""
    if score is None:
        return "bv-na"
    if score < 60:
        return "bv-lo"  # red — not actionable
    if score < 70:
        return "bv-60"
    if score < 80:
        return "bv-70"
    if score < 90:
        return "bv-80"
    return "bv-90"


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

    # Split ready vs excluded (正文未入库 / 摘要·判断·核查任一失败)
    included: list[sqlite3.Row] = []
    excluded: list[dict[str, Any]] = []
    for r in rows:
        reason = _article_exclusion_reason(r)
        if reason:
            excluded.append(
                {
                    "id": str(r["id"]),
                    "title": _display_title(r),
                    "reason": reason,
                }
            )
        else:
            included.append(r)

    focus_items: list[str] = []
    for r in included[:3]:
        t = _display_title(r)
        bv = r["business_value_score"]
        note = _bv_note_display(r["business_value_note"], for_focus=True)
        if not note or note.startswith("素材不足"):
            s = _summary_text(r)
            if s and s != _THIN_SUMMARY_COPY:
                note = s
            elif not note:
                note = "见正文"
        bv_s = str(bv) if bv is not None else "—"
        tip_bv = "业务价值（0–100）：对同业/自身业务可对标程度，由 moni AI 评估"
        focus_items.append(
            f"""<li class="focus-item">
  <div class="focus-title">
    <span class="focus-bv has-tip" data-tip="{_esc(tip_bv)}">业务价值 {_esc(bv_s)}</span>
    <strong>{_esc(t)}</strong>
  </div>
  <p class="focus-note">{_esc(note)}</p>
</li>"""
        )

    cards: list[str] = []
    for i, r in enumerate(included, 1):
        t = _display_title(r)
        laid = ""
        try:
            laid = (r["brief_body_md"] or "").strip()
        except (KeyError, IndexError, TypeError):
            laid = ""
        raw_body = (r["raw_content"] or "").strip()
        thin = _is_thin_article(r)
        body = laid or raw_body or "（无正文）"
        body_html = body_to_html(body)
        summ, sum_origin, sum_is_ai = _summary_with_meta(r)
        if sum_origin == "body_lead":
            sum_is_ai = False  # 正文节选
        summ_thin = sum_origin in ("thin", "none")
        bv = r["business_value_score"]
        bv_note = _bv_note_display(r["business_value_note"])
        bv_is_ai = bool(bv_note) and "素材不足" not in (bv_note or "")
        bv_score_ai = bv is not None
        q = _quality(r)
        pub_date = _format_date(r["published_at"]) or "日期不详"
        url = (r["url"] or "").strip()
        link_html = (
            f'<a class="ext" href="{_esc(url)}" target="_blank" rel="noopener">原文链接（可能无法打开）</a>'
            if url
            else ""
        )
        fc_items = _fc_items(r)
        fc_is_ai = not (
            len(fc_items) == 1 and fc_items[0][0] == "none" and "无需" in fc_items[0][1]
        )
        fc_lis = []
        _fc_lab = {"ok": "已核实", "warn": "待核实", "bad": "存疑"}
        for kind, fctext in fc_items:
            if kind == "none":
                fc_lis.append(
                    f"<li class='fc-line fc-stack'>"
                    f"<span class='fc-text fc-plain'>{_esc(fctext)}</span>"
                    f"</li>"
                )
            else:
                lab = _fc_lab.get(kind, "")
                fc_lis.append(
                    f"<li class='fc-line fc-stack'>"
                    f"<span class='badge {kind}'>"
                    f"{_badge_svg(kind)}<span class='badge-lab'>{_esc(lab)}</span>"
                    f"</span>"
                    f"<span class='fc-text'>{_esc(fctext)}</span>"
                    f"</li>"
                )

        idx = f"{i:02d}"
        bv_s = str(bv) if bv is not None else "—"
        bv_tier = _bv_score_tier(int(bv) if bv is not None else None)
        score_label = f"{bv_s}分" if bv is not None else "—"
        ai_mark = (
            '<span class="ai-mark has-tip" data-tip="由 moni / hermes 生成或压缩，非原文照搬">'
            "AI 生成</span>"
        )
        sum_origin_mark = ""
        if sum_origin == "body_lead":
            sum_origin_mark = (
                '<span class="ai-mark has-tip" data-tip="摘要字段过短时，从正文截取导读段落">'
                "正文节选</span>"
            )
        bv_sec_html = ""
        if bv is not None or bv_note:
            mark = ai_mark if (bv_is_ai or bv_score_ai) else ""
            note_txt = bv_note or "（无业务判断说明）"
            bv_sec_html = f"""
  <div class="sec">
    <h3 class="sec-label">业务判断 {mark}</h3>
    <ul class="fc">
      <li class="fc-line fc-stack">
        <span class="badge bv {_esc(bv_tier)}">{_esc(score_label)}</span>
        <span class="fc-text">{_esc(note_txt)}</span>
      </li>
    </ul>
  </div>"""
        sum_cls = "summary-block is-thin" if summ_thin else "summary-block"
        sum_ai = (ai_mark if sum_is_ai else "") + sum_origin_mark
        fc_ai = ai_mark if fc_is_ai else ""
        fold_open = "全文未入库" if thin and not laid else "查看全文"
        fold_close = "收起全文"
        card_cls = "card is-thin" if thin and not laid else "card"
        q_tip = (
            "质量/注意力分（0–100）：综合标题完整度、正文信息量等的启发式评分，"
            "用于粗略判断是否「值得一读」。非机构评级。"
        )
        bv_tip = (
            "业务价值（0–100）：对同业/自身业务的可对标、可落地程度。"
            "由 moni 结合正文做 AI 评估；正文过短时会标「素材不足」。"
        )

        cards.append(
            f"""
<article class="{card_cls}" data-id="{_esc(r['id'])}">
  <header class="card-head">
    <span class="card-idx" aria-hidden="true">{idx}</span>
    <h2>{_esc(t)}</h2>
  </header>
  <div class="meta-row">
    <span class="chip">{_esc(_source_line(r))}</span>
    <span class="chip">{_esc(pub_date)}</span>
    <span class="chip score has-tip" data-tip="{_esc(q_tip)}">质量分 {q}</span>
    <span class="chip score has-tip" data-tip="{_esc(bv_tip)}">业务价值 {bv_s}</span>
  </div>
  <div class="sec">
    <h3 class="sec-label">摘要 {sum_ai}</h3>
    <div class="{sum_cls}">{_esc(summ)}</div>
  </div>
  {bv_sec_html}
  <div class="sec">
    <h3 class="sec-label">事实核查 {fc_ai}</h3>
    <ul class="fc">{''.join(fc_lis)}</ul>
  </div>
  <div class="orig-wrap">
    <button type="button" class="fold-btn" aria-expanded="false"
            data-label-open="{_esc(fold_open)}" data-label-close="{_esc(fold_close)}">
      <span class="fold-chev" aria-hidden="true"></span>
      <span class="fold-txt">{_esc(fold_open)}</span>
    </button>
    <div class="orig-panel" aria-hidden="true">
      <div class="orig-inner">
        <div class="orig-scroll">
          <div class="orig-body">{body_html}</div>
        </div>
        <div class="orig-foot">{link_html}</div>
      </div>
    </div>
  </div>
</article>
"""
        )

    excluded_html = ""
    if excluded:
        items = "".join(
            f"<li><strong>{_esc(e['title'])}</strong>"
            f"<span class='ex-reason'> — {_esc(e['reason'])}</span></li>"
            for e in excluded
        )
        excluded_html = f"""
  <section class="excluded" aria-label="未纳入简报的文章">
    <h2>未纳入本次简报</h2>
    <p class="excluded-lead">以下文章因信息不足未展示（便于你补抓取/补摘要后再生成）：</p>
    <ol class="excluded-list">{items}</ol>
  </section>
"""

    n_show = len(included)
    n_drop = len(excluded)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{_esc(title)}</title>

<style>
  :root {{
    --bg: #f5f2eb;
    --card: #fffcf7;
    --text: #1c1917;
    --muted: #78716c;
    --border: #e7e0d4;
    --navy: #1a365d;
    --accent: #b45309;
    --accent-soft: #d97706;
    --tone-0: #faf7f2;
    --tone-1: #f3ebe0;
    --tone-2: #e8d9c4;
    --tone-3: #ddc9a8;
    --panel: #f0ebe3;
    --summary-bg: #fff4e6;
    --focus-bg: #f8f4ee;
    --focus-border: #e8d9c4;
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
  .wrap {{ max-width: 960px; margin: 0 auto; padding: 48px 36px 80px; }}
  h1 {{
    font-family: var(--serif);
    font-size: 2rem; margin: 0 0 12px; letter-spacing: -0.02em;
    font-weight: 700; line-height: 1.25;
  }}
  .sub {{
    color: var(--muted); margin: 0 0 36px; font-size: 0.95rem;
    font-family: var(--sans);
  }}
  .focus {{
    background: var(--focus-bg); border: 1px solid var(--focus-border);
    border-radius: 14px; padding: 28px 26px 24px; margin: 0 0 40px;
  }}
  .focus > h2 {{
    margin: 0 0 20px; font-size: 1.05rem; font-family: var(--sans);
    font-weight: 650; letter-spacing: 0.02em; color: var(--navy);
  }}
  .focus-list {{ list-style: none; margin: 0; padding: 0; counter-reset: fi; }}
  .focus-item {{
    counter-increment: fi;
    margin: 0 0 18px; padding: 16px 16px 16px 48px; position: relative;
    background: rgba(255,255,255,0.82); border-radius: 12px;
    border: 1px solid rgba(26,54,93,0.06);
  }}
  .focus-item:last-child {{ margin-bottom: 0; }}
  .focus-item::before {{
    content: counter(fi);
    position: absolute; left: 14px; top: 16px;
    width: 26px; height: 26px; border-radius: 999px;
    background: var(--navy); color: #fff;
    font-family: var(--sans); font-size: 12px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
  }}
  .focus-title {{
    display: flex; flex-wrap: wrap; align-items: baseline; gap: 10px;
    margin: 0 0 10px; line-height: 1.45;
  }}
  .focus-title strong {{ font-weight: 650; font-size: 1.02rem; }}
  .focus-bv {{
    font-family: var(--sans); font-size: 11px; font-weight: 700;
    color: var(--navy); background: var(--tone-1); border: 1px solid var(--tone-2);
    border-radius: 999px; padding: 3px 10px; cursor: help;
  }}
  .focus-note {{ margin: 0; color: #44403c; font-size: 0.98rem; line-height: 1.7; }}
  .card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 14px;
    padding: 28px 28px 24px; margin: 0 0 32px;
    box-shadow: 0 1px 0 rgba(28,25,23,0.03);
  }}
  .card.is-thin {{ opacity: 0.97; }}
  .card-head {{
    display: grid; grid-template-columns: auto 1fr; gap: 12px 16px;
    align-items: start; margin: 0 0 18px;
  }}
  .card-idx {{
    font-family: var(--sans); font-size: 1.6rem; font-weight: 700; line-height: 1.1;
    color: var(--accent-soft); letter-spacing: -0.02em; min-width: 1.6em;
  }}
  .card h2 {{
    font-size: 1.2rem; margin: 0; line-height: 1.45; font-weight: 700; color: var(--navy);
  }}
  .meta-row {{
    display: flex; flex-wrap: wrap; gap: 8px 10px; margin: 0 0 16px; font-family: var(--sans);
  }}
  .chip {{
    display: inline-flex; align-items: center; font-size: 0.8rem; color: var(--navy);
    background: var(--tone-1); border: 1px solid var(--tone-2);
    border-radius: 999px; padding: 5px 12px; max-width: 100%; line-height: 1.4;
  }}
  .chip.score {{
    font-weight: 650; cursor: help; background: var(--tone-0);
    border-color: var(--tone-2); color: var(--navy);
    position: relative;
  }}
  /* CSS tooltips (native title is too slow / easy to miss) */
  .has-tip {{
    position: relative;
    cursor: help;
  }}
  .has-tip::after {{
    content: attr(data-tip);
    position: absolute;
    left: 50%; bottom: calc(100% + 8px);
    transform: translateX(-50%) translateY(4px);
    min-width: 12em; max-width: 18em;
    padding: 8px 10px;
    border-radius: 8px;
    background: #1c1917;
    color: #fafaf9;
    font-size: 12px;
    font-weight: 500;
    font-family: var(--sans);
    line-height: 1.45;
    letter-spacing: 0;
    white-space: normal;
    text-align: left;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.12s ease, transform 0.12s ease;
    z-index: 40;
    box-shadow: 0 6px 20px rgba(0,0,0,0.18);
  }}
  .has-tip:hover::after,
  .has-tip:focus-visible::after {{
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }}
  .ai-mark {{
    display: inline-flex; align-items: center;
    font-family: var(--sans); font-size: 0.68rem; font-weight: 700; letter-spacing: 0.06em;
    color: var(--navy); background: var(--tone-1); border: 1px solid var(--tone-2);
    border-radius: 4px; padding: 2px 6px; vertical-align: middle; margin-left: 6px; cursor: help;
  }}
  .sec {{ margin: 24px 0 10px; }}
  .sec + .sec {{ margin-top: 30px; }}
  .sec-label {{
    font-size: 0.82rem; margin: 0 0 14px; color: var(--accent);
    font-weight: 650; font-family: var(--sans); letter-spacing: 0.04em;
    display: flex; align-items: center; flex-wrap: wrap; gap: 4px;
  }}
  .sec-label::before {{ content: "▎"; margin-right: 2px; color: var(--accent-soft); }}
  .summary-block {{
    margin: 0; padding: 16px 18px; background: var(--summary-bg);
    border: none; border-radius: 10px; color: #292524; font-size: 1.02rem; line-height: 1.75;
  }}
  .summary-block.is-thin {{ background: var(--tone-1); color: var(--muted); font-size: 0.95rem; }}
  .orig-body blockquote, blockquote {{
    margin: 0.7em 0; padding: 10px 14px; border-left: 3px solid var(--tone-2);
    background: var(--tone-0); border-radius: 0 8px 8px 0; color: #44403c;
  }}
  .fc {{ list-style: none; padding: 0; margin: 4px 0 0; }}
  .fc-line {{
    margin: 0 0 16px; display: flex; flex-wrap: nowrap; align-items: flex-start; gap: 10px;
  }}
  /* 标签单独一行，正文换行 */
  .fc-line.fc-stack {{
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }}
  .fc-line:last-child {{ margin-bottom: 0; }}
  .fc-plain {{ color: var(--muted); font-family: var(--sans); }}
  .badge {{
    display: inline-flex; align-items: center; gap: 4px; flex-shrink: 0;
    font-size: 12px; font-weight: 600; font-family: var(--sans);
    padding: 3px 9px 3px 5px; border-radius: 999px; white-space: nowrap; line-height: 1.4;
    border: 1px solid var(--tone-2); background: var(--tone-1); color: var(--navy);
  }}
  .badge.ok {{ background: #f0ebe3; color: #3f3a34; border-color: #ddd0bc; }}
  .badge.warn {{ background: #fff4e6; color: #9a3412; border-color: #f0d9b5; }}
  .badge.bad {{ background: #f5e6e0; color: #7c2d12; border-color: #e8c4b0; }}
  /* BV 分档着色：&lt;60 红，每 10 分一档 */
  .badge.bv {{
    font-variant-numeric: tabular-nums; min-width: 3.2em; justify-content: center;
    padding: 4px 10px;
  }}
  .badge.bv.bv-lo {{ background: #dc2626; color: #fff; border-color: #b91c1c; }}
  .badge.bv.bv-60 {{ background: #ea580c; color: #fff; border-color: #c2410c; }}
  .badge.bv.bv-70 {{ background: #d97706; color: #fff; border-color: #b45309; }}
  .badge.bv.bv-80 {{ background: #ca8a04; color: #fff; border-color: #a16207; }}
  .badge.bv.bv-90 {{ background: #16a34a; color: #fff; border-color: #15803d; }}
  .badge.bv.bv-na {{ background: var(--tone-1); color: var(--muted); border-color: var(--tone-2); }}
  .badge svg {{ flex-shrink: 0; display: block; }}
  .badge-lab {{ letter-spacing: 0.02em; }}
  .fc-text {{
    display: block; width: 100%;
    min-width: 0; line-height: 1.7; color: #1c1917;
  }}
  .excluded {{
    margin: 40px 0 0; padding: 22px 24px;
    background: var(--tone-0); border: 1px dashed var(--tone-2);
    border-radius: 12px; font-family: var(--sans);
  }}
  .excluded h2 {{
    margin: 0 0 10px; font-size: 1rem; color: var(--navy); font-weight: 650;
  }}
  .excluded-lead {{ margin: 0 0 12px; color: var(--muted); font-size: 0.9rem; }}
  .excluded-list {{ margin: 0; padding-left: 1.25em; color: #44403c; font-size: 0.92rem; }}
  .excluded-list li {{ margin: 0.45em 0; }}
  .ex-reason {{ color: var(--muted); font-weight: 400; }}
  .orig-wrap {{ margin-top: 28px; border-top: 1px dashed var(--border); padding-top: 22px; }}
  .fold-btn {{
    display: flex; align-items: center; justify-content: center; gap: 10px;
    width: 100%; min-height: 44px; padding: 10px 16px;
    border: 1px solid var(--tone-2); border-radius: 10px; cursor: pointer;
    font-family: var(--sans); font-size: 14px; font-weight: 600; letter-spacing: 0.03em;
    color: var(--navy); background: var(--tone-0);
    transition: background 0.15s ease, border-color 0.15s ease;
  }}
  .fold-btn:hover {{ background: var(--tone-1); border-color: var(--tone-3); }}
  .fold-btn:focus-visible {{ outline: 2px solid var(--accent-soft); outline-offset: 2px; }}
  .fold-chev {{
    width: 0; height: 0;
    border-left: 5px solid transparent; border-right: 5px solid transparent;
    border-top: 6px solid var(--muted); transition: transform 0.25s ease;
  }}
  .fold-btn[aria-expanded="true"] .fold-chev {{ transform: rotate(180deg); }}
  .orig-panel {{ overflow: hidden; max-height: 0; transition: max-height 0.32s ease; }}
  .orig-inner {{ padding-top: 16px; }}
  .orig-scroll {{
    max-height: 420px; overflow-y: auto; -webkit-overflow-scrolling: touch;
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 20px 22px; font-size: 1.02rem; color: #1c1917; line-height: 1.85;
  }}
  .orig-body {{ white-space: normal; word-break: break-word; font-family: var(--serif); }}
  .orig-body h2 {{ font-size: 1.12rem; margin: 1.2em 0 0.55em; color: var(--text); }}
  .orig-body h3 {{ font-size: 1.04rem; margin: 1.1em 0 0.5em; color: #44403c; }}
  .orig-body h4 {{ font-size: 0.98rem; margin: 1em 0 0.45em; color: #57534e; }}
  .orig-body p {{ margin: 0 0 0.95em; }}
  .orig-body p:last-child {{ margin-bottom: 0; }}
  .orig-body ul, .orig-body ol {{ margin: 0 0 0.95em; padding-left: 1.4em; }}
  .orig-body li {{ margin: 0.35em 0; }}
  .orig-body hr {{ border: 0; border-top: 1px solid #d6d3d1; margin: 1.2em 0; }}
  .orig-body code {{
    font-family: ui-monospace, Consolas, monospace; font-size: 0.88em;
    background: #e7e5e4; padding: 0.1em 0.35em; border-radius: 4px;
  }}
  .orig-body a {{ color: var(--accent); text-decoration: none; }}
  .orig-body a:hover {{ text-decoration: underline; }}
  .orig-body strong {{ font-weight: 700; color: #0c0a09; }}
  .orig-body .empty {{ color: var(--muted); }}
  .orig-foot {{ margin-top: 12px; font-size: 0.85rem; font-family: var(--sans); }}
  .orig-foot .ext {{ color: var(--muted); }}
  .foot {{
    text-align: center; color: var(--muted); font-size: 0.88rem;
    margin-top: 48px; font-family: var(--sans);
  }}
  @media print {{
    .orig-panel {{ max-height: none !important; overflow: visible !important; }}
    .orig-scroll {{ max-height: none; overflow: visible; }}
    .fold-btn {{ display: none; }}
    .wrap {{ max-width: none; }}
  }}
  @media (max-width: 720px) {{
    .wrap {{ padding: 28px 16px 56px; }}
    body {{ font-size: 16px; }}
    .card {{ padding: 22px 18px 20px; margin-bottom: 24px; }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    .orig-panel, .fold-chev {{ transition: none; }}
  }}
</style>

<script>
(function () {{
  function setOpen(btn, panel, open) {{
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    panel.setAttribute("aria-hidden", open ? "false" : "true");
    var txt = btn.querySelector(".fold-txt");
    if (txt) {{
      txt.textContent = open
        ? (btn.getAttribute("data-label-close") || "收起全文")
        : (btn.getAttribute("data-label-open") || "查看全文");
    }}
    if (open) {{
      panel.style.maxHeight = "none";
      var h = panel.scrollHeight;
      panel.style.maxHeight = "0px";
      void panel.offsetHeight;
      panel.style.maxHeight = h + "px";
      var done = function () {{
        if (btn.getAttribute("aria-expanded") === "true") {{
          panel.style.maxHeight = "none";
        }}
        panel.removeEventListener("transitionend", done);
      }};
      panel.addEventListener("transitionend", done);
    }} else {{
      panel.style.maxHeight = panel.scrollHeight + "px";
      void panel.offsetHeight;
      panel.style.maxHeight = "0px";
    }}
  }}
  document.addEventListener("click", function (e) {{
    var btn = e.target.closest(".fold-btn");
    if (!btn) return;
    var wrap = btn.closest(".orig-wrap");
    if (!wrap) return;
    var panel = wrap.querySelector(".orig-panel");
    if (!panel) return;
    var open = btn.getAttribute("aria-expanded") === "true";
    setOpen(btn, panel, !open);
  }});
}})();
</script>
</head>
<body>
<div class="wrap">
  <h1>{_esc(title)}</h1>
  <p class="sub">from moni by TQ · {n_show} 篇文章 · 按业务价值排序 · 全文已内嵌（离线可读）{
    f' · 另有 {n_drop} 篇因信息不足未纳入' if n_drop else ''
  }</p>

  <section class="focus">
    <h2>优先关注</h2>
    <ol class="focus-list">{''.join(focus_items) if focus_items else '<li class="focus-item"><p class="focus-note">暂无</p></li>'}</ol>
  </section>

  {''.join(cards)}

  {excluded_html}

  <p class="foot">Curated by moni — a TQ project.</p>
</div>

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
