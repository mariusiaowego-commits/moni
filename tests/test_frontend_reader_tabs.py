"""v1.0.102 阅读器页签化 —— 前端静态不变量。

钉住的失败模式（每条都是这轮真实改坏过 / 审计挑出来的问题）：

1. 窄屏点「对话」后再点「摘要 / 正文 / 事实核查」，内容没切回来，
   AI 面板继续盖在正文上（页签高亮与实际显示不一致）。
2. 窄屏切到「对话」时 tablist 里出现两个 aria-selected=true。
3. 摘要 / 核查结论退回 x-text，markdown 又不渲染了。
4. 事实核查页签漏掉人话结论 fact_check_summary。
5. 长核查报告没有折叠入口，整篇铺开。
6. RuntimeError 原文又出现在 toast 主文案里。
7. 迟到的核查结果把用户正在看的另一篇盖掉。
8. 打开一篇有联网核查的文章，证据链不加载（恒为 0 条）。
9. 窄屏 .ai-pane 退回 position:absolute，对话框盖在正文上。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "moni" / "web" / "static" / "index.html"
MAIN_CSS = ROOT / "moni" / "web" / "static" / "css" / "main.css"
READER_JS = ROOT / "moni" / "web" / "static" / "js" / "reader.js"
APP_JS = ROOT / "moni" / "web" / "static" / "js" / "app.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _css_block(css: str, selector: str) -> str:
    """取包含 selector 的第一个 @media 块的正文。"""
    for match in re.finditer(r"@media[^{]*\{", css):
        start = match.end()
        depth = 1
        pos = start
        while pos < len(css) and depth:
            if css[pos] == "{":
                depth += 1
            elif css[pos] == "}":
                depth -= 1
            pos += 1
        body = css[start : pos - 1]
        if selector in body:
            return body
    raise AssertionError(f"no @media block containing {selector!r}")


def test_narrow_tab_switch_resets_fullscreen_ai_pane():
    """失败模式 1：切内容页签必须把窄屏的整屏对话切回来。"""
    js = _read(READER_JS)
    m = re.search(r"readerSwitchContentTab\(tab\)\s*\{(.*?)\n    \}", js, re.S)
    assert m, "readerSwitchContentTab missing"
    assert "readerMobileTab = 'read'" in m.group(1), (
        "readerSwitchContentTab must reset readerMobileTab to 'read' — "
        "otherwise the AI pane keeps covering the content in narrow mode"
    )


def test_tablist_has_single_selected_tab_in_narrow_ai_mode():
    """失败模式 2：切到「对话」时内容页签不能也算选中。"""
    html = _read(INDEX_HTML)
    assert "readerAiFocused" in html, "readerAiFocused not wired into the tablist"
    m = re.search(r'<div class="reader-tabs".*?</div>', html, re.S)
    assert m, "reader tablist not found"
    selected = re.findall(r':aria-selected="([^"]+)"', m.group(0))
    assert len(selected) == 4, f"expected 4 reader tabs, got {len(selected)}"
    for expr in selected:
        assert "!readerAiFocused" in expr or "readerAiFocused" in expr, (
            f"aria-selected={expr!r} does not account for the narrow AI pane"
        )


def test_summary_and_fact_conclusion_render_markdown():
    """失败模式 3：markdown 渲染不许退回 x-text。"""
    html = _read(INDEX_HTML)
    assert 'x-text="articleDetail?.summary"' not in html
    assert 'x-text="articleDetail?.fact_check_conclusion"' not in html
    assert "readerRenderMarkdownPlain(articleDetail?.summary || '')" in html
    assert "readerRenderMarkdownPlain(readerFactPreviewText)" in html
    assert "readerRenderMarkdownPlain(readerFactRestText)" in html


def test_fact_tab_shows_human_conclusion_and_fold():
    """失败模式 4 / 5：人话结论要渲染，长报告要有折叠入口。"""
    html = _read(INDEX_HTML)
    assert 'x-text="articleDetail?.fact_check_summary"' in html, (
        "fact tab must show the human-readable conclusion (fact_check_summary)"
    )
    assert "展开逐条核查" in html, "long fact-check report has no fold entry"


def test_fact_badge_distinguishes_not_checked():
    """未核查的文章不能显示成「快速核查 · 未联网」。"""
    html = _read(INDEX_HTML)
    assert "未核查" in html, "badge must have a not-checked state"


def test_llm_error_text_maps_breaker_to_human_wording():
    """失败模式 6：RuntimeError 原文不得进 toast 主文案。"""
    js = _read(APP_JS)
    assert "_llmErrorText" in js, "no _llmErrorText mapper"
    assert "breaker OPEN" in js, "breaker case is not mapped"
    for raw in (
        "'联网核查失败: ' +",
        '"联网核查失败: " +',
        "'事实核查失败: ' +",
    ):
        assert raw not in js, f"raw backend error still concatenated into the headline: {raw}"


def test_late_job_result_cannot_overwrite_other_article():
    """失败模式 7：迟到结果要按 articleId 守卫。"""
    js = _read(APP_JS)
    assert "readerRefreshDetail" in js
    m = re.search(r"readerRefreshDetail\(articleId, detail\)\s*\{(.*?)\n    \}", js, re.S)
    assert m, "readerRefreshDetail missing"
    assert "articleDetail.id !== articleId" in m.group(1), (
        "late job result must not replace the article the user is reading"
    )


def test_opening_article_loads_evidence_chain():
    """失败模式 8：打开有联网核查的文章必须拉证据链。"""
    js = _read(READER_JS)
    m = re.search(r"showArticleDetail\(article\)\s*\{(.*?)\n    \}", js, re.S)
    assert m, "showArticleDetail missing"
    body = m.group(1)
    assert "loadArticleEvidence" in body, "evidence chain is never loaded on open"
    assert "has_web_fact_check" in body


def test_narrow_mode_ai_pane_is_not_an_overlay():
    """失败模式 9：窄屏 AI 面板不能再是绝对定位盖在正文上。"""
    css = _read(MAIN_CSS)
    block = _css_block(css, ".reader-split")
    ai_rule = re.search(r"\.ai-pane\s*\{(.*?)\}", block, re.S)
    assert ai_rule, "no .ai-pane rule inside the narrow media block"
    assert "position: absolute" not in ai_rule.group(1), (
        "narrow .ai-pane must not be position:absolute — it overlays the article"
    )
    assert ".reader-split.mobile-read .ai-pane" in block, "no narrow AI-pane hide rule"
    assert ".reader-split.mobile-ai .reader-pane" in block, "no narrow article-pane hide rule"
    assert "transition" in block, "narrow pane switch must animate"
