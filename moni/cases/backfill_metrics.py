"""moni.cases.backfill_metrics — 存量案例结构化指标规则回填 CLI (P0 收尾).

只读 cases.amount + cases.evidence_snippet + summary + articles.raw_content,
复用 moni.cases.normalize.parse_metric 重建 case_metrics, 并将 cases.amount
更新为带指标名的展示串。

证据约束: metric 的 evidence_quote 必须在 evidence_snippet 或正文窗口里逐字命中,
不命中则丢弃该 metric, 不猜测。

用法:
  python -m moni.cases.backfill_metrics --db /path/to/news.db           # 默认 dry-run
  python -m moni.cases.backfill_metrics --db /path/to/news.db --apply   # 写入 DB
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moni.cases.normalize import parse_metric

_METRIC_LABEL_CN: dict[str, str] = {
    "dau": "日活",
    "mau": "月活",
    "funding": "融资",
    "deal_value": "交易额",
    "investment": "投资",
    "contract_value": "合同/预算",
    "revenue": "营收",
    "valuation": "估值",
    "penalty": "罚款",
    "user_count": "用户",
    "txn_volume": "交易额",
    "txn_count": "交易笔数",
    "coverage": "覆盖",
    "headcount": "人员",
    "efficiency": "效率",
    "cost_saving": "降本",
}

_UNIT_SUFFIX_CN: dict[str, str] = {
    "CNY": "元",
    "USD": "美元",
    "EUR": "欧元",
    "KRW": "韩元",
    "JPY": "日元",
    "HKD": "港元",
    "GBP": "英镑",
    "INR": "卢比",
    "percent": "%",
    "x": "倍",
}


def format_display_amount(metric: dict[str, Any]) -> str:
    """将结构化 metric 格式化为带指标名的展示串 (绝不把外币/计数改成人民币)."""
    mtype = str(metric.get("metric_type") or "other")
    label = _METRIC_LABEL_CN.get(mtype, "")
    val_raw = str(metric.get("value_raw") or "").strip()

    # 若 value_raw 本身已带明确前缀 (如 "互动日活3000万"), 直接规范空白即可
    if label and label not in val_raw and mtype != "other":
        return f"{label} {val_raw}"
    return val_raw


def _extract_verbatim_quote(
    raw_amount: str,
    evidence_snippet: str | None,
    raw_content: str | None,
) -> tuple[str | None, str | None]:
    """从 evidence_snippet 或正文窗口中找与 raw_amount 匹配的逐字片段.

    Returns:
        (recovered_value_raw, evidence_quote) — 未命中返回 (None, None)
    """
    haystacks = [s for s in (evidence_snippet, (raw_content or "")[:4000]) if s]
    if not haystacks:
        return None, None

    raw_clean = raw_amount.strip()
    # 1. 原样逐字命中 (如 "1000 万户" / "数亿元" / "搜索量 +35%")
    for hay in haystacks:
        if raw_clean in hay:
            return raw_clean, raw_clean

    # 2. 提取数字, 在原文中找带真实数量级/币种/单位的片段
    num_m = re.search(r"(\d+(?:,\d{3})*(?:\.\d+)?)", raw_clean)
    if not num_m:
        # 模糊量词 (如 "数亿元" -> 找 "数亿")
        vague_m = re.search(r"(数[亿千万]+(?:美元|欧元|韩元|日元|元)?)", raw_clean)
        if vague_m:
            frag = vague_m.group(1)
            for hay in haystacks:
                if frag in hay:
                    return frag, frag
        return None, None

    num_token = num_m.group(1).replace(",", "")
    # 兼容 "266000.00" vs "266000"
    num_variants = [num_token]
    if "." in num_token and num_token.rstrip("0").endswith("."):
        num_variants.append(num_token.rstrip("0").rstrip("."))
    elif "." in num_token:
        num_variants.append(num_token.rstrip("0"))

    has_mag_or_cur = bool(re.search(r"(万亿|亿|千万|百万|万|千|元|美元|欧元|韩元|%|％)", raw_clean))

    for num_v in num_variants:
        num_pat = re.escape(num_v)
        # 必须带数量级或单位, 防止 "1 亿元" 误匹配 "一款智能体"
        unit_req = (
            r"(?:(?:万亿|亿|千万|百万|万|千)\s*"
            r"(?:美元|美金|USD|欧元|EUR|韩元|KRW|日元|JPY|港元|港币|HKD|英镑|GBP|卢比|INR|元|人民币|RMB|CNY|"
            r"万户|千家|万笔|万个|万用户|用户|客户|家|机构|网点|笔|个|户|人|%|％|倍)?|"
            r"(?:美元|美金|USD|欧元|EUR|韩元|KRW|日元|JPY|港元|港币|HKD|英镑|GBP|卢比|INR|元|人民币|RMB|CNY|"
            r"万户|千家|万笔|万个|万用户|用户|客户|家|机构|网点|笔|个|户|人|%|％|倍))"
            if has_mag_or_cur
            else r"(?:万亿|亿|千万|百万|万|千)?\s*[A-Za-z\u4e00-\u9fff%％]{0,4}"
        )
        pattern = re.compile(
            rf"((?:互动日活|日活|月活|DAU|MAU|预算金额|成交金额|交易额|股权价值|融资|投资|预算|估值|覆盖|裁员|超|约|近|超过|达)?\s*{num_pat}\s*{unit_req})",
            re.IGNORECASE,
        )
        for hay in haystacks:
            m = pattern.search(hay)
            if m:
                frag = m.group(1).strip("：:，,。.;； ")
                # 若 raw_clean 是 "1 亿元" 这类无单位的, 要求 frag 至少有数字+单位/量级
                if frag and re.search(r"(万亿|亿|千万|百万|万|千|元|美元|欧元|韩元|日元|港元|英镑|卢比|户|家|笔|个|人|%|％|倍|日活|月活|用户)", frag):
                    return frag, frag

    return None, None


def run_backfill_metrics(db_path: Path, *, apply: bool = False) -> dict[str, Any]:
    """回填存量案例 case_metrics 并更新 cases.amount 展示串."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) if not apply else sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT c.case_id, c.subject, c.amount, c.evidence_snippet, c.summary, a.raw_content
            FROM cases c
            LEFT JOIN articles a ON a.id = c.article_id
            WHERE c.subject != '__processing__'
              AND c.amount IS NOT NULL
              AND TRIM(c.amount) != ''
            ORDER BY c.case_id
            """
        ).fetchall()

        matched = 0
        dropped = 0
        unparseable = 0
        samples_before_after: list[dict[str, Any]] = []
        dropped_samples: list[dict[str, Any]] = []
        to_write: list[tuple[str, dict[str, Any], str]] = []

        for r in rows:
            cid = r["case_id"]
            old_amount = str(r["amount"]).strip()
            ev = r["evidence_snippet"] or ""
            raw_content = r["raw_content"] or ""
            context = f"{ev} {r['summary'] or ''}".strip()

            recovered_raw, quote = _extract_verbatim_quote(old_amount, ev, raw_content)
            if not quote or not recovered_raw:
                dropped += 1
                if len(dropped_samples) < 8:
                    dropped_samples.append({"case_id": cid, "subject": r["subject"], "old_amount": old_amount})
                continue

            metric = parse_metric(
                recovered_raw,
                context,
                evidence_quote=quote,
                source_type="rule_backfill",
            )
            if metric is None or metric.get("value_num") is None and metric.get("value_approx") is None:
                unparseable += 1
                continue

            display_amount = format_display_amount(metric)
            matched += 1
            to_write.append((cid, metric, display_amount))
            if len(samples_before_after) < 12 or any(k in old_amount or k in recovered_raw for k in ("3000", "2.5", "9.9", "6000")):
                samples_before_after.append(
                    {
                        "case_id": cid,
                        "subject": r["subject"],
                        "old_amount": old_amount,
                        "new_amount": display_amount,
                        "metric_type": metric["metric_type"],
                        "value_num": metric["value_num"],
                        "unit": metric["unit"],
                        "evidence_quote": quote,
                    }
                )

        if apply and to_write:
            now = datetime.now(timezone.utc).isoformat()
            with conn:
                for cid, m, display_amount in to_write:
                    conn.execute(
                        "DELETE FROM case_metrics WHERE case_id = ? AND source_type = 'rule_backfill'",
                        (cid,),
                    )
                    conn.execute(
                        """
                        INSERT INTO case_metrics
                            (case_id, metric_type, value_num, value_approx, unit,
                             period, period_type, value_raw, evidence_quote,
                             is_primary, source_type, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'rule_backfill', ?)
                        """,
                        (
                            cid,
                            m["metric_type"],
                            m["value_num"],
                            m["value_approx"],
                            m["unit"],
                            m["period"],
                            m["period_type"],
                            m["value_raw"],
                            m["evidence_quote"],
                            now,
                        ),
                    )
                    conn.execute(
                        "UPDATE cases SET amount = ? WHERE case_id = ?",
                        (display_amount, cid),
                    )

        return {
            "mode": "apply" if apply else "dry-run",
            "total_with_amount": len(rows),
            "matched": matched,
            "dropped": dropped,
            "unparseable": unparseable,
            "samples": samples_before_after,
            "dropped_samples": dropped_samples,
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill structured case_metrics from existing cases")
    parser.add_argument("--db", required=True, help="SQLite DB path")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="仅统计不写库 (默认)")
    mode.add_argument("--apply", action="store_true", help="写入 DB")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2

    res = run_backfill_metrics(db_path, apply=bool(args.apply))
    print(
        f"[{res['mode']}] total_with_amount={res['total_with_amount']} "
        f"matched={res['matched']} dropped={res['dropped']} unparseable={res['unparseable']}"
    )
    print("samples:")
    for s in res["samples"][:12]:
        print(
            f"  {s['case_id']} | {s['subject']} | {s['old_amount']} -> {s['new_amount']} "
            f"[{s['metric_type']} {s['value_num']} {s['unit']}] quote={s['evidence_quote']!r}"
        )
    print("dropped_samples:")
    for d in res["dropped_samples"][:5]:
        print(f"  {d['case_id']} | {d['subject']} | {d['old_amount']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
