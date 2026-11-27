"""分享简报里用户上下文的渲染：TLDR 抽取 + markdown 渲染。

失败模式先行：
1. 没有「结论：」前缀的上下文 → TLDR 退化成第一句，剩余内容不丢。
2. 结论超过上限 → 截断加省略号，不能把整段吞掉。
3. mistune 缺失 → 退回转义纯文本，不能抛错（渲染失败不能拖垮整个简报）。
4. 上下文里带 HTML → 必须转义，不能注入标签。
5. 单段正文（无「\n\n」）→ 第一句进 TLDR，剩下的进详情，不能重复整段。
6. 空正文 → 不产出卡片。
"""
from __future__ import annotations

import sqlite3

import pytest

from moni.share import context_pack
from moni.share.context_pack import (
    html_block,
    render_body_html,
    split_tldr,
    standalone_html_section,
)


def _db(tmp_path, rows):
    path = tmp_path / "news.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE article_contexts (id INTEGER PRIMARY KEY, article_id TEXT,"
        " kind TEXT, title TEXT, body TEXT, source_question TEXT,"
        " quoted_text TEXT, sort_order INTEGER DEFAULT 0, created_at TEXT DEFAULT '')"
    )
    conn.execute("CREATE TABLE articles (id TEXT PRIMARY KEY, title TEXT)")
    for article_id, kind, title, body in rows:
        conn.execute(
            "INSERT INTO articles (id, title) VALUES (?, ?)",
            (article_id, f"标题 {article_id}"),
        )
        conn.execute(
            "INSERT INTO article_contexts (article_id, kind, title, body)"
            " VALUES (?, ?, ?, ?)",
            (article_id, kind, title, body),
        )
    conn.commit()
    conn.close()
    return str(path)


def test_split_tldr_takes_leading_conclusion():
    body = "结论：这篇文章的证据链是三段对照实验。\n\n一、关键事实\n\n1. 第一条"
    tldr, detail = split_tldr(body)
    assert tldr == "这篇文章的证据链是三段对照实验。"
    assert detail.startswith("一、关键事实")


def test_split_tldr_falls_back_to_first_sentence():
    body = "第一句话在这里。第二句话也算详情。"
    tldr, detail = split_tldr(body)
    assert tldr == "第一句话在这里。"
    assert "第二句话" in detail


def test_split_tldr_keeps_remainder_of_single_paragraph():
    body = "结论很直接。补充说明在后面。"
    tldr, detail = split_tldr(body)
    assert tldr == "结论很直接。"
    assert "补充说明在后面。" in detail


def test_split_tldr_keeps_remainder_of_long_single_paragraph():
    """单段长正文（结论 + 依据全在一段）不能只留 TLDR 丢细节。"""
    body = "结论：" + "甲" * 40 + "。" + "依据：1. " + "乙" * 60 + " 2. 丙。"
    tldr, detail = split_tldr(body)
    assert tldr.endswith("。")
    assert "依据" in detail
    assert "丙" in detail


def test_split_tldr_keeps_detail_of_prefixed_paragraph():
    body = "结论：证据链是三段实验。依据：1. 甲。2. 乙。"
    tldr, detail = split_tldr(body)
    assert tldr == "证据链是三段实验。"
    assert "依据" in detail and "乙" in detail


def test_split_tldr_truncates_long_conclusion():
    body = "结论：" + "长" * 500
    tldr, _ = split_tldr(body)
    assert len(tldr) <= context_pack.MAX_TLDR_CHARS + 1
    assert tldr.endswith("…")


def test_split_tldr_empty_body():
    assert split_tldr("") == ("", "")
    assert split_tldr("   ") == ("", "")


def test_render_body_html_renders_markdown():
    out = render_body_html("一、关键事实\n\n1. 第一条\n\n2. 第二条")
    assert "<ol>" in out and "<li>" in out


def test_render_body_html_escapes_raw_html():
    out = render_body_html("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_render_body_html_degrades_without_mistune(monkeypatch):
    monkeypatch.setattr(context_pack, "_markdown_renderer", lambda: None)
    out = render_body_html("带 <b> 标签的正文")
    assert "<b>" not in out
    assert "&lt;b&gt;" in out


def test_html_block_shows_tldr_and_collapses_detail():
    body = "结论：这是一句话结论。\n\n一、关键事实\n\n1. 第一条"
    html = html_block([{"kind": "chat", "title": "", "body": body}])
    assert "这是一句话结论。" in html
    assert "<details" in html and "展开完整上下文" in html
    assert "<ol>" in html
    assert html.count("这是一句话结论。") == 1


def test_html_block_skips_empty_body():
    assert html_block([{"kind": "chat", "title": "", "body": "  "}]) == ""


def test_html_block_does_not_repeat_title_when_it_is_the_tldr():
    body = "结论：证据链是三段对照实验，但没有任何性能量化指标。"
    title = "结论：证据链是三段对照实验，但没有任何性能量化指标…"
    html = html_block([{"kind": "chat", "title": title, "body": body}])
    assert html.count("证据链是三段对照实验") == 1
    assert "ctx-title" not in html


def test_html_block_keeps_short_title_that_is_not_the_tldr():
    """短标题（如「判断」）不能被 TLDR 去重规则误吞。"""
    html = html_block(
        [{"kind": "chat", "title": "<判断>", "body": "<script>x</script>"}]
    )
    assert "&lt;判断&gt;" in html
    assert "&lt;script&gt;" in html


def test_standalone_section_renders_tldr_and_details(tmp_path):
    db = _db(
        tmp_path,
        [("a1", "chat", "结论：测试结论。", "结论：测试结论。\n\n一、事实\n\n1. 甲")],
    )
    section = standalone_html_section(db, ["a1"])
    assert "测试结论。" in section
    assert "<details" in section
    assert "moni-user-contexts" in section
    assert "AI 对话" in section


def test_standalone_section_empty_without_contexts(tmp_path):
    db = _db(tmp_path, [])
    assert standalone_html_section(db, ["a1"]) == ""


@pytest.mark.parametrize("kind", ["chat", "note", "quote", "summary", "fact_check"])
def test_tldr_works_for_every_kind(kind):
    body = "结论：跨类型都成立。\n\n细节一\n\n细节二"
    tldr, detail = split_tldr(body)
    assert tldr == "跨类型都成立。"
    assert "细节一" in detail
    assert kind in context_pack.KIND_LABELS
