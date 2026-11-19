"""``moni fact-check-domains`` — 把证据里的域名对照白名单分档报表。

为什么需要这个
-------------
``config/fact_check_domains.toml`` 决定哪些域名算独立来源。新出现的地方媒体
每天都在往证据链里灌，未收录的域名会被静默当成「不计入」，而配置里没有任何
信号告诉你漏了谁。这个报表按真实出现次数列出未收录域名，让配置维护从猜
变成按数据定级。

只读：不写库、不改配置。
"""

from __future__ import annotations

import json
from typing import Any

from moni.db.connection import get_db
from moni.llm import domains as dom

_TIER_LABEL: dict[str | None, str] = {
    "A": "一手来源",
    "B": "编辑制媒体",
    "C": "不计入",
    None: "未收录",
}


def collect() -> list[dict[str, Any]]:
    """每条证据域名一行：出现次数、注册域、当前分档。"""
    conn = get_db()
    cfg = dom.load_config()
    rows = conn.execute(
        """
        SELECT domain, COUNT(*) AS n
        FROM fact_check_evidence
        GROUP BY domain
        ORDER BY n DESC, domain
        """
    ).fetchall()
    return [
        {
            "domain": row[0],
            "registrable": dom.registrable_domain(row[0]),
            "count": row[1],
            "tier": dom.classify(row[0], cfg),
        }
        for row in rows
    ]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """按档位汇总：域名数与证据条数，外加未收录的可计入缺口。"""
    buckets: dict[str | None, list[dict[str, Any]]] = {
        "A": [], "B": [], "C": [], None: [],
    }
    for row in rows:
        buckets[row["tier"]].append(row)
    return {
        "total_domains": len(rows),
        "total_evidence": sum(row["count"] for row in rows),
        "tiers": {
            label: {
                "domains": len(buckets[tier]),
                "evidence": sum(row["count"] for row in buckets[tier]),
                "registrable_domains": len({row["registrable"] for row in buckets[tier]}),
            }
            for tier, label in _TIER_LABEL.items()
        },
    }


def render_text(rows: list[dict[str, Any]], limit: int = 20) -> str:
    summary = summarize(rows)
    lines = [
        f"证据域名 {summary['total_domains']} 个 / 证据 {summary['total_evidence']} 条",
    ]
    for tier, label in _TIER_LABEL.items():
        stat = summary["tiers"][label]
        lines.append(
            f"  {label:6s} 域名 {stat['domains']:3d}  证据 {stat['evidence']:4d}"
            f"  注册域 {stat['registrable_domains']:3d}"
        )

    unlisted = [row for row in rows if row["tier"] is None]
    if not unlisted:
        lines.append("全部域名已收录。")
        return "\n".join(lines)

    shown = unlisted if limit <= 0 else unlisted[:limit]
    lines.append(f"\n未收录 Top {len(shown)}（共 {len(unlisted)} 个，按证据条数排序）：")
    for row in shown:
        lines.append(f"  {row['count']:3d}  {row['domain']:45s} reg={row['registrable']}")
    if len(shown) < len(unlisted):
        lines.append(f"  … 其余 {len(unlisted) - len(shown)} 个未显示（--limit 0 看全部）")
    return "\n".join(lines)


def run_report(*, limit: int = 20, as_json: bool = False) -> int:
    rows = collect()
    if as_json:
        print(json.dumps(
            {"summary": summarize(rows), "domains": rows},
            ensure_ascii=False, indent=2,
        ))
    else:
        print(render_text(rows, limit=limit))
    return 0
