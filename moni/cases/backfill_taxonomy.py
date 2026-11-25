"""moni.cases.backfill_taxonomy — 企业/行业/场景/8类 规则回填 CLI (P1-1).

用法:
  python -m moni.cases.backfill_taxonomy --db /path/to/news.db           # 默认 dry-run
  python -m moni.cases.backfill_taxonomy --db /path/to/news.db --apply   # 写入 DB
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def normalize_alias_key(text: str | None) -> str:
    """规范化别名匹配键: NFKC + 去空白 + 小写."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text)).strip().lower()
    return re.sub(r"\s+", "", s)


def load_enterprise_alias_map(conn: sqlite3.Connection) -> dict[str, tuple[str, str, str]]:
    """从 enterprise_aliases JOIN enterprises 加载 {alias_norm: (enterprise_id, name_cn, industry_l1)}."""
    rows = conn.execute(
        """
        SELECT ea.alias_norm, e.enterprise_id, e.name_cn, e.industry_l1
        FROM enterprise_aliases ea
        JOIN enterprises e ON e.enterprise_id = ea.enterprise_id
        WHERE e.status = 'active'
        """
    ).fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def resolve_enterprise(
    raw_subject: str | None,
    alias_map: dict[str, tuple[str, str, str]],
) -> tuple[str | None, str | None, str]:
    """精确 + 最长别名前缀匹配企业; 未命中禁止模糊猜测, 返回 (None, None, '其他')."""
    key = normalize_alias_key(raw_subject)
    if not key:
        return None, None, "其他"

    if key in alias_map:
        return alias_map[key]

    best: tuple[int, tuple[str, str, str]] | None = None
    for alias_norm, target in alias_map.items():
        if len(alias_norm) < 2:
            continue
        if key.startswith(alias_norm):
            # 纯 ASCII 别名要求后一字符不是 ASCII 字母数字, 避免 'meta' 误匹配 'metaverse'
            if alias_norm.isascii() and len(key) > len(alias_norm):
                nxt = key[len(alias_norm)]
                if nxt.isascii() and nxt.isalnum():
                    continue
            if best is None or len(alias_norm) > best[0]:
                best = (len(alias_norm), target)

    if best is not None:
        return best[1]
    return None, None, "其他"


_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("procurement", ("中标", "采购", "招标", "预算金额", "入围", "比选")),
    ("compliance_risk", ("处罚", "被罚", "罚款", "违规", "失控", "漏洞", "泄露", "诉讼")),
    ("capital", ("融资", "收购", "并购", "要约", "投资", "估值", "IPO", "上市", "出售", "剥离")),
    ("org_strategy", ("裁员", "组织架构", "高管", "任命", "战略调整", "缩减", "关停", "停运", "退出")),
    ("partnership", ("合作", "联合", "携手", "签约", "战略协议", "接入", "联名")),
    ("digital_infra", ("核心系统", "分布式架构", "云平台", "算力", "数据中心", "芯片", "机房")),
    ("ai_adoption", ("AI", "大模型", "智能体", "人工智能", "Agent", "生成式", "智能客服", "智能风控", "智能审单", "智能投顾")),
]


def classify_category(
    *,
    summary: str | None = None,
    evidence_snippet: str | None = None,
    keywords: list[str] | None = None,
    case_type: str | None = None,
) -> str:
    """8 类规则分类 (确定性关键词优先序)."""
    text = " ".join(
        [
            summary or "",
            evidence_snippet or "",
            " ".join(keywords or []),
        ]
    )
    for code, kws in _CATEGORY_RULES:
        if any(k in text for k in kws):
            return code
    if (case_type or "").upper() == "PI":
        return "product_launch"
    return "product_launch"


_SCENARIO_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("ecny_stablecoin", ("数字人民币", "数币", "e-CNY", "稳定币", "USDC", "USDT", "货币桥", "mBridge")),
    ("cross_border_pay", ("跨境", "SWIFT", "汇款", "国际清算", "外汇", "Visa", "万事达", "Mastercard")),
    ("mobile_digital_pay", ("移动支付", "二维码", "刷脸", "NFC", "收单", "数字钱包", "即时支付", "支付业务", "微信支付")),
    ("risk_compliance", ("风控", "反欺诈", "反洗钱", "AML", "KYC", "安全", "合规", "处罚", "被罚", "审单")),
    ("wealth_advisory", ("投顾", "投研", "理财", "基金", "财富管理", "高净值")),
    ("credit_supply_chain", ("信贷", "贷款", "融资", "供应链金融", "普惠", "小微")),
    ("channel_service", ("手机银行", "信用卡App", "小程序", "网点", "智能客服", "远程银行", "App整合")),
    ("core_infra_cloud", ("核心系统", "云计算", "算力", "芯片", "数据中心", "分布式", "架构", "具身智能", "人形机器人")),
    ("llm_agent", ("大模型", "智能体", "Agent", "生成式AI", "LLM", "AI助手", "代码生成", "AI Coding", "AI")),
]


def classify_scenario(
    *,
    keywords: list[str] | None = None,
    summary: str | None = None,
    evidence_snippet: str | None = None,
    subject: str | None = None,
) -> str:
    """10 个受控业务场景规则分类."""
    text = " ".join(
        [
            " ".join(keywords or []),
            summary or "",
            evidence_snippet or "",
            subject or "",
        ]
    )
    for code, kws in _SCENARIO_RULES:
        if any(k in text for k in kws):
            return code
    return "other"


def run_backfill(db_path: Path, *, apply: bool = False) -> dict[str, Any]:
    """在指定 DB 上执行企业/行业/场景/8类规则回填."""
    if not apply:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        alias_map = load_enterprise_alias_map(conn)
        cases = conn.execute(
            """
            SELECT case_id, subject, case_type, summary, evidence_snippet
            FROM cases
            WHERE subject != '__processing__'
            ORDER BY case_id
            """
        ).fetchall()

        kw_rows = conn.execute("SELECT case_id, keyword FROM case_keywords").fetchall()
        kw_by_case: dict[str, list[str]] = {}
        for kr in kw_rows:
            kw_by_case.setdefault(kr["case_id"], []).append(kr["keyword"])

        matched = 0
        unmatched = 0
        unmatched_subjects: Counter[str] = Counter()
        by_enterprise: Counter[str] = Counter()
        by_industry: Counter[str] = Counter()
        by_scenario: Counter[str] = Counter()
        by_category: Counter[str] = Counter()

        updates: list[tuple[str | None, str, str]] = []
        ent_links: list[tuple[str, str, str]] = []
        scn_links: list[tuple[str, str]] = []

        for row in cases:
            cid = row["case_id"]
            subj = row["subject"]
            kws = kw_by_case.get(cid, [])

            ent_id, ent_name, industry = resolve_enterprise(subj, alias_map)
            if ent_id is not None:
                matched += 1
                by_enterprise[f"{ent_id}:{ent_name}"] += 1
                ent_links.append((cid, ent_id, subj))
            else:
                unmatched += 1
                unmatched_subjects[subj] += 1
            by_industry[industry] += 1

            cat = classify_category(
                summary=row["summary"],
                evidence_snippet=row["evidence_snippet"],
                keywords=kws,
                case_type=row["case_type"],
            )
            by_category[cat] += 1

            scn = classify_scenario(
                keywords=kws,
                summary=row["summary"],
                evidence_snippet=row["evidence_snippet"],
                subject=subj,
            )
            by_scenario[scn] += 1

            updates.append((ent_id, cat, cid))
            scn_links.append((cid, scn))

        if apply:
            now = datetime.now(timezone.utc).isoformat()
            with conn:
                conn.executemany(
                    "UPDATE cases SET subject_enterprise_id = ?, case_category = ? WHERE case_id = ?",
                    updates,
                )
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO case_enterprises
                        (case_id, enterprise_id, role, raw_name, source_type, created_at)
                    VALUES (?, ?, 'subject', ?, 'auto_rule', ?)
                    """,
                    [(cid, eid, raw, now) for cid, eid, raw in ent_links],
                )
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO case_scenario_links
                        (case_id, scenario_code, is_primary, source_type, created_at)
                    VALUES (?, ?, 1, 'auto_rule', ?)
                    """,
                    [(cid, scn, now) for cid, scn in scn_links],
                )

        return {
            "mode": "apply" if apply else "dry-run",
            "total": len(cases),
            "enterprise_matched": matched,
            "enterprise_unmatched": unmatched,
            "by_industry": dict(by_industry.most_common()),
            "by_category": dict(by_category.most_common()),
            "by_scenario": dict(by_scenario.most_common()),
            "top_enterprises": dict(by_enterprise.most_common(15)),
            "top_unmatched_subjects": dict(unmatched_subjects.most_common(15)),
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill case enterprise/industry/scenario/category")
    parser.add_argument("--db", required=True, help="SQLite DB path")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="仅统计不写库 (默认)")
    mode.add_argument("--apply", action="store_true", help="写入 DB")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2

    res = run_backfill(db_path, apply=bool(args.apply))
    print(f"[{res['mode']}] total={res['total']} enterprise_matched={res['enterprise_matched']} enterprise_unmatched={res['enterprise_unmatched']}")
    print("by_industry:", res["by_industry"])
    print("by_category:", res["by_category"])
    print("by_scenario:", res["by_scenario"])
    print("top_enterprises:", res["top_enterprises"])
    print("top_unmatched_subjects:", res["top_unmatched_subjects"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
