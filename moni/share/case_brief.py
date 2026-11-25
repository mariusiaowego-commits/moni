"""moni.share.case_brief — 案例库 HTML 简报渲染器 (P2-1 方案 A 融合).

纯 Python + Jinja2 确定性渲染：不经过 hermes，不调用任何 LLM。
读取 cases / articles / case_metrics / case_enterprises / enterprises /
case_scenario_links / case_scenarios / case_categories / case_insights
(case_insights 为空或不存在时优雅降级)。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
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

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    tmpl = env.get_template("case_brief.j2")
    resolved_title = (title or "").strip() or f"同业案例简报 — {datetime.now().strftime('%Y-%m-%d')}"
    return tmpl.render(
        title=resolved_title,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        group_label=_GROUP_LABELS.get(group_by, _GROUP_LABELS[None]),
        total_cases=len(items),
        enterprise_count=len(enterprises),
        scenario_count=len(scenarios),
        article_count=len(articles),
        groups=groups,
        include_insight=include_insight,
        include_evidence=include_evidence,
    )


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
