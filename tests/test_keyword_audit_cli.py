"""Tests for ``moni keyword-audit`` (v1.0.64).

The command exists so a keyword/rule change can be measured against real
stored data before anyone trusts it.  Two properties matter most:

* it must be strictly read-only (it reports on articles it would drop, so
  a write here would corrupt exactly the corpus it is auditing);
* a source-scoped run must use that source's *bound* tags and the
  bestblog title-only rule, otherwise the numbers are fiction.
"""

from __future__ import annotations

import json
from argparse import Namespace

import pytest

from moni.cli.keyword_audit import run_keyword_audit
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate


@pytest.fixture
def seeded(tmp_path):
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, created_at, updated_at) "
        "VALUES ('src-t', 'testsrc', '测试源', 'html', 'https://e.com/', 30, 1, "
        "'2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO tag_definitions "
        "(name, description, color, created_at, is_active, keywords) "
        "VALUES ('general-tech', '通用科技', '#6B7280', '2026-01-01', 1, ?)",
        (json.dumps(["MA", "CI/CD", "软件工程"], ensure_ascii=False),),
    )
    conn.execute(
        "INSERT INTO tag_definitions "
        "(name, description, color, created_at, is_active, keywords) "
        "VALUES ('banking', '银行', '#2563EB', '2026-01-01', 1, ?)",
        (json.dumps(["银行", "消费"], ensure_ascii=False),),
    )
    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) "
        "VALUES ('src-t', 'general-tech', '2026-01-01')"
    )
    for url, title, body in [
        ("https://e.com/1", "营销自动化 (MA) 落地", "正文。" * 300),
        ("https://e.com/2", "2024 北海道 Part 3", "晚餐 MASAJIN。" * 200),
        ("https://e.com/3", "五仁月饼", "顺便提一句消费。" + "正文。" * 400),
    ]:
        conn.execute(
            "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
            "VALUES (?, ?, ?, 'testsrc', '2026-10-25', ?)",
            (url[-1] * 16, title, url, body),
        )
    conn.commit()
    yield conn
    close_db()


def _args(**kw):
    base = {"source": None, "show_lost": 10}
    base.update(kw)
    return Namespace(**base)


def test_reports_a_real_delta(seeded, capsys):
    code = run_keyword_audit(_args())
    out = capsys.readouterr().out
    assert code == 0
    assert "旧规则(纯子串) 存活: 3" in out
    assert "v1.0.64 规则存活:   1" in out


def test_is_read_only(seeded):
    """The audit must not touch articles, basket, or hits."""
    before = {
        t: seeded.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("articles", "basket", "article_tags", "article_keyword_hits")
    }
    run_keyword_audit(_args())
    after = {
        t: seeded.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in before
    }
    assert before == after


def test_lists_the_articles_that_would_be_lost(seeded, capsys):
    run_keyword_audit(_args(show_lost=5))
    out = capsys.readouterr().out
    assert "北海道" in out
    assert "五仁月饼" in out
    assert "营销自动化" not in out.split("会因此不再命中")[-1]


def test_show_lost_zero_suppresses_the_list(seeded, capsys):
    run_keyword_audit(_args(show_lost=0))
    assert "会因此不再命中" not in capsys.readouterr().out


def test_source_scope_uses_only_bound_tags(seeded, capsys):
    """'消费' belongs to banking, which testsrc is not bound to."""
    run_keyword_audit(_args(source="testsrc"))
    out = capsys.readouterr().out
    assert "tag: 1" in out
    assert "'消费'" not in out


def test_unknown_source_exits_2(seeded, capsys):
    assert run_keyword_audit(_args(source="nope")) == 2
    assert "没有绑定任何带关键词的 tag" in capsys.readouterr().out


def test_reports_the_rule_profile(seeded, capsys):
    run_keyword_audit(_args())
    out = capsys.readouterr().out
    # MA + CI/CD are ASCII; 银行 + 消费 are 2-char Han; 软件工程 is substring.
    assert "ASCII 词边界 2" in out
    assert "≤2 字中文 2" in out
    assert "普通子串 1" in out


def test_bestblog_scope_reports_title_only(seeded, capsys):
    conn = get_db()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, created_at, updated_at) "
        "VALUES ('src-bb', 'bestblog-x', 'BB-X', 'rss', 'https://e.com/f', 60, 1, "
        "'2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) "
        "VALUES ('src-bb', 'general-tech', '2026-01-01')"
    )
    conn.commit()
    run_keyword_audit(_args(source="bestblog-x"))
    assert "只匹配标题" in capsys.readouterr().out


def test_legacy_baseline_also_respects_title_only(seeded, capsys):
    """The bug this test pins: scoring bestblog's baseline on title+content
    invents 'lost' articles that the old gate never actually kept."""
    conn = get_db()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, created_at, updated_at) "
        "VALUES ('src-bb', 'bestblog-x', 'BB-X', 'rss', 'https://e.com/f', 60, 1, "
        "'2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) "
        "VALUES ('src-bb', 'general-tech', '2026-01-01')"
    )
    # Body matches (软件工程), title does not.  Old gate = title-only = drop;
    # new gate = title-only = drop.  So the delta must be zero.
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
        "VALUES ('bb00000000000001', '444. 在编织的一针一线中', "
        "'https://e.com/bb/1', 'bestblog-x', '2026-10-25', ?)",
        ("关注公众号 软件工程 " * 50,),
    )
    conn.commit()

    run_keyword_audit(_args(source="bestblog-x", show_lost=0))
    out = capsys.readouterr().out
    assert "旧规则(纯子串) 存活: 0" in out
    assert "v1.0.64 规则存活:   0" in out
    assert "其中 0 篇是旧规则本会留下" in out


def test_all_source_mode_applies_title_only_per_row(seeded, capsys):
    """All-source mode must not treat every row as title+content.

    A bestblog-* row whose only match is in the body was never kept by the
    old gate either, so it must not show up as 'lost'.
    """
    conn = get_db()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, created_at, updated_at) "
        "VALUES ('src-bb', 'bestblog-x', 'BB-X', 'rss', 'https://e.com/f', 60, 1, "
        "'2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) "
        "VALUES ('src-bb', 'general-tech', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
        "VALUES ('bb00000000000002', '在编织的一针一线中', "
        "'https://e.com/bb/2', 'bestblog-x', '2026-10-25', ?)",
        ("关注公众号 软件工程 " * 50,),
    )
    conn.commit()

    run_keyword_audit(_args(show_lost=0))
    out = capsys.readouterr().out
    # The 3 testsrc rows are unaffected; the bestblog row contributes to
    # neither the legacy nor the new survivor count.
    assert "旧规则(纯子串) 存活: 3" in out
    assert "v1.0.64 规则存活:   1" in out
