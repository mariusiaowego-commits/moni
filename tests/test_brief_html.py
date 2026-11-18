"""Deterministic HTML brief: full body + collapsible original."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# 定位仓库根: **优先用本文件相对位置** ⇒ 从 worktree 单跑时命中 **worktree 代码**;
# 回退到 ~/dev/moni ⇒ 主 checkout 的既有用法**完全不变**。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not os.path.isdir(os.path.join(_REPO_ROOT, "moni")):
    _REPO_ROOT = os.path.expanduser("~/dev/moni")
sys.path.insert(0, _REPO_ROOT)

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.share.brief_html import render_brief_html, write_brief_html


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "bh.db"
    set_db_path(db_path)
    migrate()
    yield db_path
    close_db()
    import moni.db.connection as conn_mod

    conn_mod._CON = None
    conn_mod._DB_PATH = None


def test_html_contains_body_and_fold(fresh_db: Path, tmp_path: Path):  # tmp_path for write_brief_html
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    body = (
        "---\ntitle: x\n---\n"
        "这是完整正文段落一。\n\n"
        "## 小节标题\n\n"
        "这是段落二，**加粗**与[链接](https://example.com/a)。\n\n"
        "- 列表甲\n- 列表乙\n"
    )
    conn.execute(
        """
        INSERT INTO articles
        (id, title, url, source, publisher, published_at, fetched_at, raw_content,
         ai_summary, brief_summary, business_value_score, business_value_note,
         quality_score, fact_check_conclusion)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "h1",
            "测试标题足够长",
            "https://example.com/x",
            "bestblogs",
            "极客公园",
            now,
            now,
            body + ("补充段落使正文超过八十个字，便于通过简报就绪校验。" * 3),
            "长摘要不该优先，应展示完整的 AI 摘要内容，包含核心论点和关键数字说明。",
            "短摘要两句内。",
            80,
            "对我方有对标价值",
            70,
            "无需核查",
        ),
    )
    conn.commit()

    html_doc = render_brief_html(str(fresh_db), ["h1"], title="同业简报 — test")
    assert "查看全文" in html_doc
    assert "fold-btn" in html_doc
    assert "orig-scroll" in html_doc
    assert "max-height: 420px" in html_doc
    assert "var(--serif)" in html_doc or "serif" in html_doc
    assert "max-width: 960px" in html_doc
    assert "这是完整正文段落一" in html_doc
    assert "<h3>" in html_doc or "<h2>" in html_doc  # ## → h3 in body
    assert "<strong>加粗</strong>" in html_doc
    assert "<ul>" in html_doc and "列表甲" in html_doc
    # Prefer full AI summary over compressed brief_summary for HTML card
    assert "长摘要不该优先" in html_doc
    assert "极客公园" in html_doc
    # 业务判断 uses same .fc list layout as 事实核查
    assert "业务判断" in html_doc
    assert "bv-score" in html_doc
    assert "MATRIX" not in html_doc  # no exaggerated scramble
    assert "bd-root" not in html_doc
    assert 'aria-expanded="false"' in html_doc
    assert "from moni by TQ" in html_doc
    assert "Curated by moni" in html_doc
    # v1.0.15: soft summary wash + readable score chips + AI mark
    assert "summary-block" in html_doc
    assert "--summary-bg" in html_doc
    assert "card-idx" in html_doc
    assert "阅读 " in html_doc  # v1.0.96: brief chip renamed 阅读分 / BestBlogs
    assert "业务价值" in html_doc
    assert "bv-score" in html_doc  # score sits next to 业务判断 title
    assert "fc-inline" in html_doc  # 已核实 inline with claim text
    assert "ai-end" not in html_doc  # no trailing AI micro-tags
    # frontmatter stripped
    assert "title: x" not in html_doc

    out = tmp_path / "out.html"
    write_brief_html(str(fresh_db), ["h1"], out)
    assert out.is_file()
    assert out.stat().st_size > 500


def test_thin_article_goes_to_excluded_footer(fresh_db: Path):
    """Empty raw + no summary → not in cards; listed under 未纳入本次简报."""
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO articles
        (id, title, url, source, publisher, published_at, fetched_at, raw_content,
         business_value_score, business_value_note, quality_score)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "thin1",
            "[某号] 短讯标题",
            "https://e.com/t",
            "qbitai",
            "某号",
            now,
            now,
            "",  # empty body
            25,
            "素材不足（正文过短），无法可靠评估业务价值。",
            35,
        ),
    )
    conn.commit()
    html_doc = render_brief_html(str(fresh_db), ["thin1"])
    assert "未纳入本次简报" in html_doc
    assert "短讯标题" in html_doc
    assert "正文未入库" in html_doc
    # no article card for the only pin
    assert 'class="card' not in html_doc or "data-id=\"thin1\"" not in html_doc


def test_find_unready_articles(fresh_db: Path):
    from moni.share.brief_html import find_unready_articles

    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    body = "这是足够长的正文内容。" * 40
    conn.execute(
        """
        INSERT INTO articles
        (id, title, url, source, publisher, published_at, fetched_at, raw_content)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        ("bad1", "空文", "https://e.com/b", "s", "p", now, now, ""),
    )
    conn.execute(
        """
        INSERT INTO articles
        (id, title, url, source, publisher, published_at, fetched_at, raw_content,
         brief_summary, business_value_score, business_value_note, fact_check_conclusion)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "ok1", "有摘要", "https://e.com/o", "s", "p", now, now, body,
            "这是可用摘要，包含关键事实与数字说明，足够长。",
            75,
            "对支付侧产品有对标价值",
            "无需核查",
        ),
    )
    conn.commit()
    bad = find_unready_articles(str(fresh_db), ["bad1", "ok1"])
    assert len(bad) == 1
    assert bad[0]["id"] == "bad1"


def test_body_to_html_plain_paragraphs():
    from moni.share.brief_html import body_to_html

    h = body_to_html("第一段。\n\n第二段内容。")
    assert "<p>" in h
    assert "第一段" in h and "第二段" in h


def test_fc_items_strip_duplicate_status_from_claim_text():
    from moni.share.brief_html import _fc_items

    class R(dict):
        def __getitem__(self, k):
            return dict.get(self, k)

    row = R(
        fact_check_note="claim1 【已核实】: 监管文件已发布; claim2 【待核实】: 年报数据",
        fact_check_conclusion="",
    )
    items = _fc_items(row)
    assert items[0][0] == "ok"
    # claim text must not repeat status words (badge has icon+label)
    assert "已核实" not in items[0][1]
    assert "claim1" not in items[0][1].lower()
    assert "监管文件" in items[0][1]


def test_fc_badge_has_label_and_inline_claim(fresh_db: Path):
    """badge shows icon+文字; claim text below badge (fc-stack)."""
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    body = "正文段落足够长。" * 30
    conn.execute(
        """
        INSERT INTO articles
        (id, title, url, source, publisher, published_at, fetched_at, raw_content,
         ai_summary, fact_check_note, quality_score, business_value_score, business_value_note)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "fc1", "监管文件解读标题", "https://e.com/1", "s", "pub", now, now, body,
            "监管已发文，机构需在限期内完成整改与披露。",
            "【已核实】: 监管已发文",
            70,
            65,
            "合规动作可对标",
        ),
    )
    conn.commit()
    html_doc = render_brief_html(str(fresh_db), ["fc1"])
    assert "badge-lab" in html_doc
    assert "已核实" in html_doc
    assert "fc-stack" in html_doc
    assert "【已核实】" not in html_doc  # stripped from claim
    assert "监管已发文" in html_doc
    assert "65分" in html_doc
    assert "has-tip" in html_doc
    assert "fc-inline" in html_doc
