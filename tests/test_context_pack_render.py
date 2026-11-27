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
    detail_css,
    html_block,
    render_body_html,
    render_detail_html,
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
    assert 'class="moni-ctx-item"' in html and "第一条" in html
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


_SECTIONED = """一、关键事实（作者的结论性主张）

1. 微信小程序基于 Webview 引擎。   证明：属于定性背景，无数据。

2. 有赞对「首帧」的定义是「切页后第一帧」。
   证明：有赞自定义口径，不是官方定义。

二、案例与实验（证据链逐条）3. 案例对比：H5 跳转 → 小程序跳转。
    证明：建立体验基线，只有录屏，无量化。

三、无出处 / 需标注的数字

- 200ms：异步注入的 setTimeout 延时，是模拟值。
- 1000ms：实验里的 sleep 阻塞时长，是人造模拟。

四、未知（文中没有）

- 首帧耗时的实际测量值。
- 灰度期间的问题率。"""


def test_render_detail_html_lays_numbered_sections_in_two_columns():
    html = render_detail_html(_SECTIONED)
    assert html.count('class="moni-ctx-cols"') == 2
    assert html.count('class="moni-ctx-item"') == 3
    assert html.count('class="moni-ctx-h"') == 4
    assert "关键事实（作者的结论性主张）" in html
    assert "案例与实验（证据链逐条）" in html
    assert "证明：属于定性背景，无数据。" in html
    assert 'class="moni-ctx-proof"' in html


def test_render_detail_html_keeps_prose_sections_whole_and_once():
    """无出处 / 未知 走整段，且回归过一处重复渲染（lead 与 body 各输出一次）。"""
    html = render_detail_html(_SECTIONED)
    assert html.count("200ms：异步注入的 setTimeout 延时") == 1
    assert html.count("首帧耗时的实际测量值") == 1
    assert "<ul>" in html
    prose = html.split('class="moni-ctx-prose"')
    assert len(prose) == 3  # 两个整段区块 + 一次 split 余量


def test_render_detail_html_falls_back_without_section_headings():
    html = render_detail_html("结论之外的补充：若换成三个月统计，结果不同。")
    assert "moni-ctx-cols" not in html
    assert "若换成三个月统计" in html


def test_render_detail_html_escapes_heading():
    html = render_detail_html("一、<script>alert(1)</script>\n\n1. 甲\n\n2. 乙")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_detail_html_splits_item_glued_to_heading():
    """生产真实写法：``一、结论1. 这个案例…`` —— 第 1 条不能并进标题。"""
    html = render_detail_html("一、结论1. 甲条。\n2. 乙条。\n\n六、两个反直觉的洞察1. 丙条。\n2. 丁条。")
    assert '<h4 class="moni-ctx-h">结论</h4>' in html
    assert '<h4 class="moni-ctx-h">两个反直觉的洞察</h4>' in html
    assert html.count('class="moni-ctx-item"') == 4
    assert "1. 甲条。" not in html


def test_render_detail_html_splits_heading_from_body_on_double_space():
    html = render_detail_html("七、优先级建议  第一步：权益台账。")
    assert '<h4 class="moni-ctx-h">优先级建议</h4>' in html
    assert "第一步：权益台账。" in html


def test_render_detail_html_keeps_long_heading_intact():
    heading = "四个可落地的玩法（按合规压力从低到高）"
    html = render_detail_html(f"四、{heading}\n\n1. 甲\n\n2. 乙")
    assert heading in html


def test_numbered_items_skip_stray_number_before_real_list():
    from moni.share.context_pack import _numbered_items

    lead, items = _numbered_items("前导一句话。5. 噪声\n\n1. 甲\n\n2. 乙")
    assert items == ["甲", "乙"]


def test_empty_section_heading_is_not_dropped():
    html = render_detail_html("一、A\n二、B\n\n1. 甲\n\n2. 乙")
    assert '<h4 class="moni-ctx-h">A</h4>' in html
    assert '<h4 class="moni-ctx-h">B</h4>' in html


def test_detail_css_is_scoped():
    assert "#box .moni-ctx-cols" in detail_css("#box")
    assert detail_css("").lstrip().startswith(".moni-ctx-h")


@pytest.mark.parametrize("kind", ["chat", "note", "quote", "summary", "fact_check"])
def test_tldr_works_for_every_kind(kind):
    body = "结论：跨类型都成立。\n\n细节一\n\n细节二"
    tldr, detail = split_tldr(body)
    assert tldr == "跨类型都成立。"
    assert "细节一" in detail
    assert kind in context_pack.KIND_LABELS
