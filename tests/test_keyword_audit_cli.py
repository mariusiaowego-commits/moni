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
    """Numbers must match the real gate, which unions only the bound tags.

    ``testsrc`` is bound to ``general-tech`` only, so the ``banking``
    keyword ``消费`` in the third article is out of scope: the real gate
    would never look at it.  Counting it (3 survivors) would be the
    union-everywhere bug this fixture exists to prevent.
    """
    code = run_keyword_audit(_args())
    out = capsys.readouterr().out
    assert code == 0
    assert "旧规则(纯子串) 存活: 2" in out
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
    # 五仁月饼 only hits `消费`, which belongs to banking — a tag testsrc is
    # not bound to, so the real gate never looks at it.  It must NOT be
    # reported as lost.
    assert "五仁月饼" not in out
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
    # Scope is per-source: only general-tech is bound to testsrc.
    # MA + CI/CD are ASCII; 软件工程 is a plain substring.
    assert "ASCII 词边界 2" in out
    assert "≤2 字中文 0" in out
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
    # testsrc still scores 2/1; the bestblog row's only match is body-only
    # and bestblog is title-only, so it contributes to neither count.
    assert "旧规则(纯子串) 存活: 2" in out
    assert "v1.0.64 规则存活:   1" in out


def test_bypass_sources_are_excluded_from_the_delta(seeded, capsys):
    """follow_builders-style bypass never runs a gate — scoring it invents
    a delta for articles no matcher ever saw."""
    conn = get_db()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, created_at, updated_at, fetch_config) "
        "VALUES ('src-fb', 'follow_builders', 'FB', 'json', 'https://e.com/fb', "
        "360, 1, '2026-01-01', '2026-01-01', ?)",
        (json.dumps({"bypass_keyword_filters": True}),),
    )
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
        "VALUES ('fb00000000000001', 'Nothing matches here', "
        "'https://e.com/fb/1', 'follow_builders', '2026-10-25', '无关正文')"
    )
    conn.commit()

    run_keyword_audit(_args(show_lost=5))
    out = capsys.readouterr().out
    assert "跳过 1 篇 bypass 源" in out
    assert "受闸门约束:        3 篇" in out
    assert "Nothing matches here" not in out


def test_bypass_exclusion_ignores_is_active(seeded, capsys):
    """An inactive bypass source still holds articles that skipped the gate.

    follow_builders is inactive in production today; its 38 stored rows
    were ingested while it was on, so they must stay out of the delta.
    """
    conn = get_db()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, created_at, updated_at, fetch_config) "
        "VALUES ('src-fb', 'follow_builders', 'FB', 'json', 'https://e.com/fb', "
        "360, 0, '2026-01-01', '2026-01-01', ?)",
        (json.dumps({"bypass_keyword_filters": True}),),
    )
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
        "VALUES ('fb00000000000003', 'Old builders feed', "
        "'https://e.com/fb/3', 'follow_builders', '2026-10-25', '无关正文')"
    )
    conn.commit()

    run_keyword_audit(_args(show_lost=5))
    out = capsys.readouterr().out
    assert "跳过 1 篇 bypass 源" in out
    assert "Old builders feed" not in out


# ── per-source tag bindings ─────────────────────────────────────────────────


@pytest.fixture
def two_sources(tmp_path):
    """Two sources, deliberately bound to *different* tags.

    src-a → banking (keywords 银行/消费)
    src-b → general-tech (keywords MA/软件工程)

    An all-source replay that unions every tag everywhere reports 2
    survivors for article 2, because `银行` is in article 2's body.  The
    real gate for src-b never looks at `银行`.  That gap is the bug this
    fixture pins.
    """
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    for sid, name in (("src-a", "srca"), ("src-b", "srcb")):
        conn.execute(
            "INSERT INTO sources (id, name, display_name, source_type, url, "
            "fetch_interval_min, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, 'html', 'https://e.com/', 30, 1, "
            "'2026-01-01', '2026-01-01')",
            (sid, name, name),
        )
    conn.execute(
        "INSERT INTO tag_definitions "
        "(name, description, color, created_at, is_active, keywords) "
        "VALUES ('banking', '银行', '#2563EB', '2026-01-01', 1, ?)",
        (json.dumps(["银行", "消费"], ensure_ascii=False),),
    )
    conn.execute(
        "INSERT INTO tag_definitions "
        "(name, description, color, created_at, is_active, keywords) "
        "VALUES ('general-tech', '通用科技', '#6B7280', '2026-01-01', 1, ?)",
        (json.dumps(["MA", "软件工程"], ensure_ascii=False),),
    )
    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) "
        "VALUES ('src-a', 'banking', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) "
        "VALUES ('src-b', 'general-tech', '2026-01-01')"
    )
    # Article 1: src-a, hits its own tag in the title.
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
        "VALUES ('a000000000000001', '银行发布新规', 'https://e.com/a1', "
        "'srca', '2026-10-25', ?)",
        ("正文。" * 300,),
    )
    # Article 2: src-b, body mentions 银行 (src-a's tag) only.  The src-b
    # gate must not see it.
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
        "VALUES ('b000000000000002', '一次重构记录', 'https://e.com/b2', "
        "'srcb', '2026-10-25', ?)",
        ("顺便说一句银行。" + "正文。" * 400,),
    )
    # Article 3: src-b, hits its own tag in the title.
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
        "VALUES ('b000000000000003', '营销自动化 (MA) 落地', 'https://e.com/b3', "
        "'srcb', '2026-10-25', ?)",
        ("正文。" * 300,),
    )
    conn.commit()
    yield conn
    close_db()


def test_unbound_tags_do_not_contribute_to_the_all_source_delta(two_sources, capsys):
    """Only each article's own bound tags are in scope.

    Article 2's body says 银行, which belongs to banking — bound to src-a,
    not to article 2's source src-b.  Counting it (3 survivors) is exactly
    the union-everywhere bug.
    """
    run_keyword_audit(_args(show_lost=10))
    out = capsys.readouterr().out
    # src-a keeps 1 (银行 in title); src-b keeps 1 (MA in title).
    # Article 2 keeps nothing under either rule.
    assert "旧规则(纯子串) 存活: 2" in out
    assert "v1.0.64 规则存活:   2" in out
    assert "一次重构记录" not in out


def test_scoped_run_ignores_other_sources_bindings(two_sources, capsys):
    run_keyword_audit(_args(source="srcb", show_lost=10))
    out = capsys.readouterr().out
    # srcb has 2 articles; only the MA title hits.
    assert "旧规则(纯子串) 存活: 1" in out
    assert "银行发布新规" not in out


def test_source_without_bound_tags_is_pass_through(two_sources, capsys):
    conn = get_db()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, created_at, updated_at) "
        "VALUES ('src-c', 'srcc', 'srcc', 'html', 'https://e.com/c', 30, 1, "
        "'2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
        "VALUES ('c000000000000004', '完全不匹配', 'https://e.com/c4', "
        "'srcc', '2026-10-25', '无关正文')"
    )
    conn.commit()

    run_keyword_audit(_args(show_lost=0))
    out = capsys.readouterr().out
    assert "其中无绑定 tag (pass-through): 1 篇" in out
    # 2 hits + 1 pass-through (srcc has no bound tags).
    assert "旧规则(纯子串) 存活: 3" in out
    assert "v1.0.64 规则存活:   3" in out


def test_scoped_run_on_a_bypassed_source_reports_zero_gated(seeded, capsys):
    conn = get_db()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, created_at, updated_at, fetch_config) "
        "VALUES ('src-fb', 'follow_builders', 'FB', 'json', 'https://e.com/fb', "
        "360, 1, '2026-01-01', '2026-01-01', ?)",
        (json.dumps({"bypass_keyword_filters": True}),),
    )
    conn.execute(
        "INSERT INTO source_tags (source_id, tag_name, created_at) "
        "VALUES ('src-fb', 'general-tech', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
        "VALUES ('fb00000000000002', 'AI builders roundup', "
        "'https://e.com/fb/2', 'follow_builders', '2026-10-25', '无关正文')"
    )
    conn.commit()

    run_keyword_audit(_args(source="follow_builders", show_lost=0))
    out = capsys.readouterr().out
    assert "受闸门约束:        0 篇" in out
    assert "新规则丢弃:        0" in out
