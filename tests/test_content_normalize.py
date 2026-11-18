"""moni.fetch.content.normalize — deterministic body cleaning.

Failure modes pinned here were all observed on the production corpus
(5,330 rows), not invented:

* the extractor eating a whole article because one inline ``<h1>`` appeared;
* a boilerplate regex truncating real policy text (``国际商务合作区``);
* a CTA regex truncating real prose (``乘客在车站扫描二维码后，…``);
* frontmatter stripped from a YAML dump that contained the whole body;
* double-escaped entities only half-decoded, so the *second* run "cleans"
  what the first left behind.
"""

from __future__ import annotations

import pytest

from moni.fetch.content import clean_boilerplate, normalize


class TestFrontmatter:
    def test_small_header_removed(self):
        raw = "---\ntitle: x\nauthor: y\n---\n正文第一段"
        cleaned = normalize(raw)
        assert cleaned.text == "正文第一段"
        assert "frontmatter" in cleaned.flags

    def test_body_dumped_into_title_field_is_left_alone(self):
        """bestblogs wrote one article's body into ``title: "…"`` with ``\\n``."""
        body = "最近墨问 Bot 基本上一天一个版本的发布。" * 40
        raw = f'---\ntitle: "{body}"\nsource: "https://mp.weixin.qq.com/s?x=1"\n---\n后记'
        cleaned = normalize(raw)
        assert len(cleaned.text) >= len(raw) * 0.9
        assert "frontmatter_embedded" in cleaned.flags

    def test_markdown_horizontal_rule_is_not_frontmatter(self):
        raw = "---\n\n第一段\n\n---\n\n第二段"
        cleaned = normalize(raw)
        assert cleaned.text == raw
        assert cleaned.flags == ()


class TestEntities:
    def test_named_entities_decoded(self):
        cleaned = normalize("&ldquo;买被看见&rdquo;&mdash;&mdash;一井两面")
        assert cleaned.text == "“买被看见”——一井两面"
        assert "entities" in cleaned.flags

    def test_double_escaped_is_fully_decoded(self):
        """``&amp;lt;b&amp;gt;`` must land on ``<b>`` in one pass."""
        cleaned = normalize("&amp;lt;b&amp;gt;加粗&amp;lt;/b&amp;gt;")
        assert cleaned.text == "加粗"
        assert "entities" in cleaned.flags

    def test_nbsp_becomes_space(self):
        assert normalize("甲&nbsp;乙").text == "甲 乙"


class TestHtml:
    def test_real_html_blob_is_extracted(self):
        html = "".join(f"<p>第{i}段正文内容，讲一点事情。</p>" for i in range(12))
        cleaned = normalize(html)
        assert "<p>" not in cleaned.text
        assert "第0段" in cleaned.text
        assert "第11段" in cleaned.text
        assert "html" in cleaned.flags

    def test_block_tags_become_line_breaks_not_glue(self):
        raw = "<div>第一段</div><div>第二段</div>" + "<p>填充段落内容够长。</p>" * 8
        cleaned = normalize(raw)
        assert "第一段第二段" not in cleaned.text
        assert "第一段\n" in cleaned.text

    def test_inline_tag_in_prose_does_not_eat_the_article(self):
        """npm 404 事故：8,110 字正文只剩 13 字。"""
        prose = "想想项目创建之后，多久没给 npm 依赖升级了？\n\n" * 20
        raw = prose + "npm ERR! audit The server said: <h1>404 Not Found</h1>\n\n" + prose
        cleaned = normalize(raw)
        assert "想想项目创建之后" in cleaned.text
        assert len(cleaned.text) > len(raw) * 0.9

    def test_code_comparisons_survive(self):
        raw = "for (int i = 0; i < customerNum; i++) { if (productNum > 0) {} }"
        assert normalize(raw).text == raw

    def test_unknown_xml_sample_survives(self):
        raw = '<bean id="pool" class="org.springframework.Pool"><property name="core" value="2"/></bean>'
        assert normalize(raw).text == raw

    def test_comparison_with_spaces_survives(self):
        raw = "营收 < 100 亿，同比 +12%"
        assert normalize(raw).text == raw


class TestInvisibleCharacters:
    def test_zero_width_removed(self):
        cleaned = normalize("正\u200b文\u200c内容\ufeff。")
        assert cleaned.text == "正文内容。"
        assert "zero_width" in cleaned.flags

    def test_replacement_char_removed(self):
        cleaned = normalize("乱码\ufffd字符")
        assert cleaned.text == "乱码字符"
        assert "replacement" in cleaned.flags


class TestBoilerplate:
    def test_cta_tail_cut(self):
        cleaned = normalize("AI泡沫已经在破了。点击关注公众号「腾讯技术工程」了解更多")
        assert cleaned.text == "AI泡沫已经在破了。"
        assert "boilerplate" in cleaned.flags

    def test_cta_tail_long_press(self):
        assert clean_boilerplate("深度分析长按识别二维码关注我们") == "深度分析"

    def test_footer_line_dropped(self):
        raw = "正文最后一段。\n\n*本文如有侵权，请及时联系我们删除。"
        assert normalize(raw).text == "正文最后一段。"

    def test_copyright_line_dropped(self):
        raw = "正文。\n本文转载目的在于知识分享，版权归原作者和原刊所有。如有侵权，请及时联系我们删除。"
        assert normalize(raw).text == "正文。"

    def test_byline_dropped(self):
        raw = "正文第一段。\n编辑｜张雨忻\n正文第二段。"
        assert normalize(raw).text == "正文第一段。\n正文第二段。"

    @pytest.mark.parametrize(
        "line",
        [
            "支持在东方枢纽国际商务合作区等区域为非居民个人提供金融服务。",
            "乘客在车站扫描二维码后，可直接使用智能手机购买一次票。",
            "研究大模型生成内容的版权归属、内容合规性及风险控制机制。",
            "答：详细说明请点击阅读原文，查看「FAQ - 接入大模型」。",
            "屡屡查词不如一次背词，背完便能无障碍阅读原文。",
        ],
    )
    def test_real_sentences_survive(self, line):
        assert normalize(line).text == line


class TestIdempotency:
    NASTY = (
        "\ufeff---\ntitle: t\n---\n"
        "&amp;ldquo;开头&amp;rdquo; 正文第一段。\n\n"
        "<p>第二段带着 <strong>加粗</strong> 和零宽\u200b字符。</p>\n\n"
        "本文来源：某某公众号\n\n"
        "第三段。点击关注公众号「x」了解更多"
    )

    def test_second_pass_is_a_no_op(self):
        once = normalize(self.NASTY).text
        assert normalize(once).text == once

    def test_flags_do_not_repeat(self):
        once = normalize(self.NASTY)
        assert normalize(once.text).flags == ()


class TestPlainTextKept:
    def test_mid_text_dashes_kept(self):
        raw = "第一部分\n\n---\n\n第二部分"
        assert normalize(raw).text == raw

    def test_blank_lines_collapsed(self):
        assert normalize("甲\n\n\n\n乙").text == "甲\n\n乙"
