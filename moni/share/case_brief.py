"""moni.share.case_brief — 案例库 HTML 简报渲染器 (P2-1 方案 A 融合).

纯 Python 字符串确定性渲染：不经过 hermes，不调用任何 LLM，不依赖 jinja2
(CI 只装 pyproject 依赖，案例库是唯一需要模板引擎的模块，故内联)。
读取 cases / articles / case_metrics / case_enterprises / enterprises /
case_scenario_links / case_scenarios / case_categories / case_insights
(case_insights 为空或不存在时优雅降级)。
"""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_GROUP_LABELS = {
    None: "按收录顺序",
    "enterprise": "按机构分组",
    "scenario": "按业务场景分组",
    "category": "按案例类型分组",
}


def _safe_json_obj(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def load_case_brief_items(
    db_path: str | Path,
    case_ids: list[str],
    *,
    include_insight: bool = True,
    include_evidence: bool = True,
) -> list[dict[str, Any]]:
    """从 SQLite 读取待渲染案例及关联实体/指标/场景/解读."""
    clean_ids = [str(cid).strip() for cid in case_ids if str(cid).strip()]
    if not clean_ids:
        return []

    conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        ph = ",".join("?" * len(clean_ids))
        has_ent = _table_exists(conn, "enterprises")
        has_cat = _table_exists(conn, "case_categories")
        has_scn = _table_exists(conn, "case_scenarios") and _table_exists(conn, "case_scenario_links")
        has_metrics = _table_exists(conn, "case_metrics")
        has_insights = include_insight and _table_exists(conn, "case_insights")

        ent_select = (
            "e.enterprise_id AS enterprise_id, e.name_cn AS enterprise_name, e.industry_l1 AS industry_l1,"
            if has_ent
            else "NULL AS enterprise_id, NULL AS enterprise_name, NULL AS industry_l1,"
        )
        ent_join = (
            "LEFT JOIN enterprises e ON e.enterprise_id = c.subject_enterprise_id"
            if has_ent
            else ""
        )

        cat_select = (
            "COALESCE(cat.name, COALESCE(c.case_category, c.case_type)) AS category_name, COALESCE(c.case_category, c.case_type) AS category_code,"
            if has_cat
            else "c.case_type AS category_name, c.case_type AS category_code,"
        )
        cat_join = (
            "LEFT JOIN case_categories cat ON cat.code = c.case_category"
            if has_cat
            else ""
        )

        scn_select = (
            "COALESCE(scn.code, 'other') AS scenario_code, COALESCE(scn.name, '其他业务场景') AS scenario_name,"
            if has_scn
            else "'other' AS scenario_code, '其他业务场景' AS scenario_name,"
        )
        scn_join = (
            "LEFT JOIN case_scenario_links scl ON scl.case_id = c.case_id AND scl.is_primary = 1 "
            "LEFT JOIN case_scenarios scn ON scn.code = scl.scenario_code"
            if has_scn
            else ""
        )

        rows = conn.execute(
            f"""
            SELECT c.case_id, c.article_id, c.case_date, c.subject, c.counterparty,
                   c.case_type, c.case_action, c.amount, c.reference_value,
                   c.reference_rationale, c.summary, c.evidence_snippet,
                   c.case_lifecycle, c.review_status,
                   {ent_select}
                   {cat_select}
                   {scn_select}
                   a.title AS article_title, a.source AS article_source,
                   a.url AS article_url, a.published_at AS article_published_at
            FROM cases c
            LEFT JOIN articles a ON a.id = c.article_id
            {ent_join}
            {cat_join}
            {scn_join}
            WHERE c.case_id IN ({ph})
              AND c.subject != '__processing__'
              AND c.case_lifecycle != 'archived'
              AND c.review_status != 'rejected'
            """,
            clean_ids,
        ).fetchall()

        by_id: dict[str, dict[str, Any]] = {r["case_id"]: dict(r) for r in rows}

        # 指标
        metrics_by_case: dict[str, list[dict[str, Any]]] = {}
        if include_evidence and has_metrics and by_id:
            mrows = conn.execute(
                f"""
                SELECT case_id, metric_type, value_num, unit, value_raw, is_primary
                FROM case_metrics
                WHERE case_id IN ({ph})
                ORDER BY is_primary DESC, id
                """,
                clean_ids,
            ).fetchall()
            for mr in mrows:
                metrics_by_case.setdefault(mr["case_id"], []).append(dict(mr))

        # 深度解读 (空表优雅降级为 None)
        insights_by_case: dict[str, dict[str, Any]] = {}
        if has_insights and by_id:
            irows = conn.execute(
                f"""
                SELECT case_id, body_json
                FROM case_insights
                WHERE case_id IN ({ph}) AND status = 'done'
                ORDER BY id DESC
                """,
                clean_ids,
            ).fetchall()
            for ir in irows:
                if ir["case_id"] not in insights_by_case:
                    parsed = _safe_json_obj(ir["body_json"])
                    if parsed:
                        insights_by_case[ir["case_id"]] = parsed

        # 按入参 case_ids 保序组装
        items: list[dict[str, Any]] = []
        for cid in clean_ids:
            item = by_id.get(cid)
            if not item:
                continue
            item["metrics"] = metrics_by_case.get(cid, [])
            item["insight"] = insights_by_case.get(cid)
            items.append(item)
        return items
    finally:
        conn.close()


def _group_items(
    items: list[dict[str, Any]],
    group_by: str | None,
) -> list[dict[str, Any]]:
    if group_by not in ("enterprise", "scenario", "category"):
        return [{"name": "全部案例", "cases": items}]

    buckets: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for item in items:
        if group_by == "enterprise":
            key = item.get("enterprise_name") or item.get("subject") or "其他机构"
        elif group_by == "scenario":
            key = item.get("scenario_name") or "其他业务场景"
        else:
            key = item.get("category_name") or item.get("case_type") or "未分类"
        buckets.setdefault(key, []).append(item)

    return [{"name": name, "cases": grp} for name, grp in buckets.items()]


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


_CSS = """
    :root {
      --bg: #f6f8fb;
      --card: #ffffff;
      --text: #162032;
      --muted: #5b667a;
      --border: #e1e7f0;
      --accent: #1d4ed8;
      --accent-soft: #eff6ff;
      --high: #b91c1c;
      --high-bg: #fef2f2;
      --med: #b45309;
      --med-bg: #fffbeb;
      --low: #475569;
      --low-bg: #f1f5f9;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 28px 18px 56px;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      line-height: 1.6;
    }
    .wrap { max-width: 960px; margin: 0 auto; }
    .hero {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 24px 26px;
      margin-bottom: 22px;
    }
    .hero h1 { margin: 0 0 8px; font-size: 24px; line-height: 1.3; }
    .hero-meta { color: var(--muted); font-size: 13px; display: flex; flex-wrap: wrap; gap: 14px; }
    .stats-bar {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 10px;
      margin-top: 16px;
    }
    .stat-box {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 10px 14px;
    }
    .stat-num { font-size: 20px; font-weight: 700; color: var(--accent); }
    .stat-lbl { font-size: 12px; color: var(--muted); }
    .group-title {
      font-size: 17px;
      font-weight: 700;
      margin: 24px 0 12px;
      padding-left: 10px;
      border-left: 4px solid var(--accent);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .group-count { font-size: 12px; font-weight: 500; color: var(--muted); }
    .case-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px 20px;
      margin-bottom: 14px;
    }
    .case-head {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: flex-start;
      gap: 10px;
      margin-bottom: 8px;
    }
    .case-subject { font-size: 17px; font-weight: 700; }
    .case-id { font-size: 12px; color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .badges { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
    .badge {
      display: inline-block;
      font-size: 12px;
      padding: 2px 9px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 500;
    }
    .badge-ref-high { background: var(--high-bg); color: var(--high); }
    .badge-ref-medium { background: var(--med-bg); color: var(--med); }
    .badge-ref-low { background: var(--low-bg); color: var(--low); }
    .badge-muted { background: var(--low-bg); color: var(--low); }
    .summary { margin: 0 0 10px; font-size: 15px; }
    .metrics-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
    .metric-chip {
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 4px 10px;
      font-size: 12px;
    }
    .metric-chip strong { color: var(--text); }
    .insight-box {
      background: #f8faff;
      border: 1px solid #dbeafe;
      border-radius: 10px;
      padding: 12px 14px;
      margin: 10px 0;
      font-size: 13px;
    }
    .insight-box h4 { margin: 0 0 6px; font-size: 13px; color: var(--accent); }
    .insight-box p { margin: 4px 0; }
    .insight-box ul { margin: 4px 0 4px 18px; padding: 0; }
    .evidence-box {
      background: #fafafa;
      border-left: 3px solid #cbd5e1;
      padding: 8px 12px;
      margin: 10px 0;
      font-size: 13px;
      color: var(--muted);
    }
    .case-foot {
      margin-top: 10px;
      font-size: 12px;
      color: var(--muted);
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      justify-content: space-between;
    }
    .case-foot a { color: var(--accent); text-decoration: none; }
    .case-foot a:hover { text-decoration: underline; }
    .footer-note {
      margin-top: 28px;
      text-align: center;
      font-size: 12px;
      color: var(--muted);
    }
"""


def _render_metrics(c: dict[str, Any]) -> str:
    metrics = c.get("metrics") or []
    if metrics:
        chips = "".join(
            f'<span class="metric-chip"><strong>{_esc(m.get("metric_type"))}</strong>: '
            f'{_esc(m.get("value_raw"))} ({_esc(m.get("unit"))})</span>'
            for m in metrics
        )
        return f'<div class="metrics-row">{chips}</div>'
    if c.get("amount"):
        return (
            '<div class="metrics-row"><span class="metric-chip">'
            f'<strong>规模/金额</strong>: {_esc(c["amount"])}</span></div>'
        )
    return ""


def _render_insight(insight: dict[str, Any]) -> str:
    parts: list[str] = ['<div class="insight-box"><h4>深度解读</h4>']
    if insight.get("one_liner"):
        parts.append(f'<p><strong>一句话结论：</strong>{_esc(insight["one_liner"])}</p>')
    if insight.get("why_it_matters"):
        parts.append(f'<p><strong>为什么值得看：</strong>{_esc(insight["why_it_matters"])}</p>')
    practices = insight.get("transferable_practices") or []
    if practices:
        items = "".join(
            "<li>{}{}</li>".format(
                _esc(p.get("practice")),
                f'（前提：{_esc(p["preconditions"])}）' if p.get("preconditions") else "",
            )
            for p in practices
            if isinstance(p, dict)
        )
        parts.append(f"<p><strong>可迁移做法：</strong></p><ul>{items}</ul>")
    risks = insight.get("risks_prerequisites") or []
    if risks:
        parts.append(f'<p><strong>风险/前提：</strong>{_esc("；".join(str(r) for r in risks))}</p>')
    parts.append("</div>")
    return "".join(parts)


def _render_case_card(
    c: dict[str, Any],
    *,
    include_insight: bool,
    include_evidence: bool,
) -> str:
    subject = c.get("enterprise_name") or c.get("subject") or ""
    counterparty = f' · 对手方：{_esc(c["counterparty"])}' if c.get("counterparty") else ""
    case_meta = _esc(c.get("case_id"))
    if c.get("case_date"):
        case_meta += f' · {_esc(c["case_date"])}'

    badges = [f'<span class="badge">{_esc(c.get("category_name"))}</span>']
    if c.get("case_action"):
        badges.append(f'<span class="badge badge-muted">{_esc(c["case_action"])}</span>')
    if c.get("industry_l1"):
        badges.append(f'<span class="badge badge-muted">{_esc(c["industry_l1"])}</span>')
    if c.get("scenario_name"):
        badges.append(f'<span class="badge badge-muted">{_esc(c["scenario_name"])}</span>')

    if c.get("article_url"):
        source = (
            f'<a href="{_esc(c["article_url"])}" target="_blank" rel="noopener noreferrer">'
            f'{_esc(c.get("article_title") or c.get("article_id"))}</a>'
        )
    else:
        source = _esc(c.get("article_title") or c.get("article_id"))
    if c.get("article_source"):
        source += f'（{_esc(c["article_source"])}）'

    chunks = [
        '<article class="case-card">',
        '<div class="case-head"><div>',
        f'<div class="case-subject">{_esc(subject)}{counterparty}</div>',
        f'<div class="case-id">{case_meta}</div>',
        "</div>",
        f'<span class="badge badge-ref-{_esc(c.get("reference_value"))}">'
        f'参考价值：{_esc(c.get("reference_value"))}</span>',
        "</div>",
        f'<div class="badges">{"".join(badges)}</div>',
    ]
    if c.get("summary"):
        chunks.append(f'<p class="summary">{_esc(c["summary"])}</p>')
    if include_evidence:
        chunks.append(_render_metrics(c))
    if include_insight and c.get("insight"):
        chunks.append(_render_insight(c["insight"]))
    if include_evidence and c.get("evidence_snippet"):
        chunks.append(
            f'<blockquote class="evidence-box"><strong>原文证据：</strong>'
            f'{_esc(c["evidence_snippet"])}</blockquote>'
        )
    foot = [f"<span>来源：{source}</span>"]
    if c.get("reference_rationale"):
        foot.append(f'<span>依据：{_esc(c["reference_rationale"])}</span>')
    chunks.append(f'<div class="case-foot">{"".join(foot)}</div>')
    chunks.append("</article>")
    return "".join(chunks)


def render_case_brief_html(
    db_path: str | Path,
    case_ids: list[str],
    *,
    title: str | None = None,
    include_insight: bool = True,
    include_evidence: bool = True,
    group_by: str | None = None,
) -> str:
    """渲染案例库 HTML 简报字符串 (0 LLM)."""
    items = load_case_brief_items(
        db_path,
        case_ids,
        include_insight=include_insight,
        include_evidence=include_evidence,
    )
    groups = _group_items(items, group_by)
    enterprises = {
        (it.get("enterprise_id") or it.get("subject"))
        for it in items
        if (it.get("enterprise_id") or it.get("subject"))
    }
    scenarios = {it.get("scenario_code") or "other" for it in items}
    articles = {it.get("article_id") for it in items if it.get("article_id")}

    resolved_title = (title or "").strip() or f"同业案例简报 — {datetime.now().strftime('%Y-%m-%d')}"
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    group_label = _GROUP_LABELS.get(group_by, _GROUP_LABELS[None])
    stats = [
        ("收录案例", len(items)),
        ("涉及机构", len(enterprises)),
        ("覆盖场景", len(scenarios)),
        ("来源文章", len(articles)),
    ]
    stats_html = "".join(
        f'<div class="stat-box"><div class="stat-num">{num}</div>'
        f'<div class="stat-lbl">{label}</div></div>'
        for label, num in stats
    )
    sections = []
    for group in groups:
        cards = "".join(
            _render_case_card(
                c,
                include_insight=include_insight,
                include_evidence=include_evidence,
            )
            for c in group["cases"]
        )
        sections.append(
            "<section>"
            f'<div class="group-title"><span>{_esc(group["name"])}</span>'
            f'<span class="group-count">{len(group["cases"])} 条案例</span></div>'
            f"{cards}</section>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{_esc(resolved_title)}</title>
  <style>{_CSS}</style>
</head>
<body>
  <div class="wrap">
    <header class="hero">
      <h1>{_esc(resolved_title)}</h1>
      <div class="hero-meta">
        <span>生成时间：{_esc(generated_at)}</span>
        <span>分组方式：{_esc(group_label)}</span>
        <span>渲染模式：结构化直出（0 LLM）</span>
      </div>
      <div class="stats-bar">{stats_html}</div>
    </header>
    {"".join(sections)}
    <footer class="footer-note">
      moni 同业案例库 2.0 · 自动生成简报
    </footer>
  </div>
</body>
</html>
"""


def write_case_brief_html(
    db_path: str | Path,
    case_ids: list[str],
    output_path: str | Path,
    *,
    title: str | None = None,
    include_insight: bool = True,
    include_evidence: bool = True,
    group_by: str | None = None,
) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    html_doc = render_case_brief_html(
        db_path,
        case_ids,
        title=title,
        include_insight=include_insight,
        include_evidence=include_evidence,
        group_by=group_by,
    )
    out.write_text(html_doc, encoding="utf-8")
    return out


def _parse_case_ids_arg(raw: str) -> list[str]:
    s = raw.strip()
    if s.startswith("["):
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return [str(x) for x in parsed if str(x).strip()]
    return [part.strip() for part in s.split(",") if part.strip()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render moni case brief HTML (no LLM)")
    p.add_argument("--db", required=True)
    p.add_argument("--case-ids", required=True, help="JSON list or comma-separated case_ids")
    p.add_argument("--output", required=True)
    p.add_argument("--title", default=None)
    p.add_argument("--group-by", choices=["enterprise", "scenario", "category"], default=None)
    p.add_argument("--no-insight", action="store_true")
    p.add_argument("--no-evidence", action="store_true")
    args = p.parse_args(argv)

    ids = _parse_case_ids_arg(args.case_ids)
    out = write_case_brief_html(
        args.db,
        ids,
        args.output,
        title=args.title,
        include_insight=not args.no_insight,
        include_evidence=not args.no_evidence,
        group_by=args.group_by,
    )
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
