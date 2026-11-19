"""fact-check-domains 报表 + 白名单配置完整性。

覆盖的失败模式：
1. 报表漏统计未收录域名 → 月度维护回路失效，配置永远停在旧快照。
2. 同一域名同时出现在两个档位 → classify 结果取决于判断顺序（隐式优先级）。
3. 配置大小写不一致 → 匹配静默失效。
4. 注册域多段后缀缺失（co.kr）→ 两家不同媒体被当成同一来源，交叉验证虚高。
5. CLI 子命令被删/改名 → 配置头注释里的维护入口消失。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from moni.cli.main import PARSER
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.llm import domain_report
from moni.llm.domains import classify, registrable_domain

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "fact_check_domains.toml"
TIER_KEYS = ("tier_a", "tier_a_brand", "tier_b", "tier_c")


@pytest.fixture()
def conn(tmp_path):
    set_db_path(tmp_path / "news.db")
    migrate()
    connection = get_db()
    yield connection
    close_db()


def _seed(conn, domain: str, times: int = 1) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO articles (id, title, url, source, fetched_at) "
        "VALUES ('a1', 't', 'https://example.test/a', 'src', '2026-11-19')"
    )
    for i in range(times):
        conn.execute(
            "INSERT INTO fact_check_evidence "
            "(id, article_id, query, url, domain, snippet, fetched_at, model, provider,"
            " conclusion, evidence_seq, created_at) "
            "VALUES (?, 'a1', 'q', ?, ?, 's', '2026-11-19', 'm', 'p', '【已核实】', ?, '2026-11-19')",
            (f"e-{domain}-{i}", f"https://{domain}/{i}", domain, i),
        )
    conn.commit()


def test_report_counts_and_surfaces_unlisted(conn):
    _seed(conn, "pbc.gov.cn", 3)
    _seed(conn, "unknown-blog.example", 2)
    _seed(conn, "zhuanlan.zhihu.com", 1)

    rows = domain_report.collect()
    by_domain = {row["domain"]: row for row in rows}

    assert by_domain["pbc.gov.cn"]["count"] == 3
    assert by_domain["pbc.gov.cn"]["tier"] == "A"
    assert by_domain["unknown-blog.example"]["tier"] is None
    assert by_domain["zhuanlan.zhihu.com"]["tier"] == "C"

    text = domain_report.render_text(rows, limit=0)
    assert "未收录 Top 1" in text
    assert "unknown-blog.example" in text


def test_report_limit_trims_only_the_unlisted_tail(conn):
    for name in ("a1.example", "a2.example", "a3.example"):
        _seed(conn, name, 1)

    text = domain_report.render_text(domain_report.collect(), limit=2)
    assert "未收录 Top 2" in text
    assert "其余 1 个未显示" in text


def test_report_summary_does_not_count_unlisted_as_usable(conn):
    _seed(conn, "pbc.gov.cn", 1)
    _seed(conn, "mystery.example", 4)

    summary = domain_report.summarize(domain_report.collect())
    assert summary["total_evidence"] == 5
    assert summary["tiers"]["一手来源"]["evidence"] == 1
    assert summary["tiers"]["未收录"]["evidence"] == 4


def test_cli_subcommand_exists():
    args = PARSER.parse_args(["fact-check-domains", "--report", "--limit", "5"])
    assert args.subcmd == "fact-check-domains"
    assert args.limit == 5
    assert args.as_json is False


def test_config_is_lowercase_and_has_no_cross_tier_duplicates():
    raw = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    seen: dict[str, str] = {}
    for key in TIER_KEYS:
        for entry in raw.get(key, []):
            assert entry == entry.lower(), f"{key} 含大写: {entry}"
            assert entry and entry.strip() == entry, f"{key} 含空值/空白: {entry!r}"
            assert entry not in seen, f"{entry} 同时出现在 {seen[entry]} 与 {key}"
            seen[entry] = key

    for pattern in raw.get("tier_a_regex", []):
        re.compile(pattern)


def test_expanded_domains_are_classified():
    assert classify("shengjingbank.com.cn") == "A"
    assert classify("www.psbc.com") == "A"
    assert classify("cn.unionpay.com") == "A"
    assert classify("westpac.com.au") == "A"
    assert classify("platform.claude.com") == "A"
    assert classify("bloomberg.com") == "B"
    assert classify("finance.yahoo.com") == "B"
    assert classify("cn.ithome.com") == "B"
    assert classify("zhiye.com") == "C"
    assert classify("www.tradingview.com") == "C"


def test_registrable_handles_korean_two_label_suffixes():
    assert registrable_domain("asiae.co.kr") == "asiae.co.kr"
    assert registrable_domain("en.yna.co.kr") == "yna.co.kr"
