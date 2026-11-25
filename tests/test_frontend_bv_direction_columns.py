"""tests/test_frontend_bv_direction_columns.py — 业务方向分列表的列契约。

锁住 2026-11-24 UI 审计（P0-1 / P1-1 / P1-2）修掉的三件事：

1. **P0-1 操作列被裁掉**：Pin 列表表头有 10 列，``<colgroup>`` 只写了 9 个
   ``<col>``，``table-layout: fixed`` 下最后一列没有宽度，表格总宽超出容器
   1198px，而 ``.table-container`` 是 ``overflow: hidden`` ——「查看」按钮落在
   容器右边之外，既看不见也点不到。断言表头列数与 ``<col>`` 数一致。
2. **列宽预算**：固定列宽之和不得超过内容区宽度，否则又是一样被裁。
3. **P1-1 / P1-2 tooltip**：十维明细要带权重与理由（总分是加权的），
   并且要交给 floating.js 浮层，否则会被表格的 ``overflow: hidden`` 裁掉。

口径：直接解析 index.html / css / js 的真实写法，不 mock、不抄实现。
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "moni" / "web" / "static"
INDEX_HTML = STATIC / "index.html"
MAIN_CSS = STATIC / "css" / "main.css"
APP_JS = STATIC / "js" / "app.js"
FLOATING_JS = STATIC / "js" / "floating.js"

#: 内容区宽度。1280 宽窗口下的实测值：1280 − 垂直滚动条 17 − .content 的
#: 左右内边距 40×2 = 1183。这是最窄的常见笔记本宽度，也是列宽预算的真正上限；
#: 再宽的窗口里 .content 被 max-width 1280px 卡住，内容区只会更宽。
CONTENT_WIDTH = 1183


def _articles_table() -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    start = html.index('<table class="articles-table"')
    end = html.index("</table>", start)
    return html[start:end]


def _cols() -> list[tuple[int, str]]:
    """(width, x-show) per ``<col>`` in the articles table."""
    table = _articles_table()
    cols: list[tuple[int, str]] = []
    for raw in re.findall(r"<col\b[^>]*>", table):
        width = re.search(r'width:\s*(\d+)px', raw)
        show = re.search(r'x-show="([^"]+)"', raw)
        cols.append((int(width.group(1)) if width else 0, show.group(1) if show else ""))
    return cols


def _headers() -> list[str]:
    table = _articles_table()
    head = table[: table.index("</thead>")]
    return re.findall(r"<th\b[^>]*>(.*?)</th>", head, re.S)


def test_header_count_matches_colgroup():
    """多一列少一个 <col>，最后一列就会被挤出容器裁掉。"""
    headers = _headers()
    cols = _cols()
    assert headers, "表头没解析到，index.html 结构变了"
    assert len(headers) == len(cols), (
        f"表头 {len(headers)} 列，colgroup 只有 {len(cols)} 个 <col>："
        "table-layout: fixed 下最后几列没有宽度定义，会被 overflow:hidden 裁掉"
    )


def test_pinned_column_widths_fit_the_content_area():
    """Pin 视图可见列宽之和不得超过内容区宽度。"""
    pinned = [(w, s) for w, s in _cols() if "articlesSubTab === 'all'" not in s]
    total = sum(w for w, _ in pinned)
    assert total <= CONTENT_WIDTH, (
        f"Pin 视图列宽合计 {total}px > 内容区 {CONTENT_WIDTH}px，超出部分会被裁掉"
    )


def test_quality_column_keeps_the_width_the_css_declares():
    """质量分列由 .quality-col-head 定宽 72px，colgroup 不能再写成 140px 覆盖它。"""
    css = MAIN_CSS.read_text(encoding="utf-8")
    declared = re.search(r"\.quality-col-head\s*\{[^}]*width:\s*(\d+)px", css)
    assert declared, ".quality-col-head 没有宽度声明"
    quality = [
        w
        for w, show in _cols()
        if "pinned" in show and w == int(declared.group(1))
    ]
    assert quality, (
        f"colgroup 里没有 {declared.group(1)}px 的 pinned 列，"
        "与 .quality-col-head 的宽度不一致"
    )


def test_direction_tooltip_is_floated_so_the_table_cannot_clip_it():
    """方向分 chip 的 tooltip 必须走 floating.js，否则被表格 overflow 裁掉。"""
    floating = FLOATING_JS.read_text(encoding="utf-8")
    assert "['.bv-chip-wrap', '.tip']" in floating, (
        "floating.js 没接管 .bv-chip-wrap，方向分 tooltip 会被 .table-container 裁掉"
    )
    css = MAIN_CSS.read_text(encoding="utf-8")
    assert re.search(r"#moni-float\s+\.tip\s*\{[^}]*white-space:\s*pre-wrap", css), (
        "浮层里的 tooltip 要保留换行，否则十维明细会挤成一段"
    )


def test_dimension_lines_carry_weight_and_reason():
    """十维明细要显示权重与理由：总分是加权的，理由是判分依据。"""
    app = APP_JS.read_text(encoding="utf-8")
    helper = re.search(r"function dimensionLine\(d\)\s*\{(.*?)\n\}", app, re.S)
    assert helper, "app.js 里没有 dimensionLine"
    body = helper.group(1)
    assert "d.weight" in body, "维度行没有权重"
    assert "d.reason" in body, "维度行没有理由"
    assert body.count("dimensionLine(d)") == 0
    assert app.count("dimensionLine(d)") >= 2, (
        "通用分与方向分两个 tooltip 都要用 dimensionLine"
    )


def test_scoring_message_is_readable():
    """打分提示是唯一的进度与成本说明，对比度 1.79:1 等于看不见。"""
    css = MAIN_CSS.read_text(encoding="utf-8")
    rule = re.search(r"\.bv-dir-msg\s*\{([^}]*)\}", css)
    assert rule, ".bv-dir-msg 没有样式"
    color = re.search(r"color:\s*([^;]+);", rule.group(1))
    assert color, ".bv-dir-msg 没有指定颜色"
    assert "var(--accent)" not in color.group(1), (
        "提示文案不能��� --accent：实测对比度只有 1.79:1，读不出来"
    )


def test_empty_business_value_cell_offers_a_score_button():
    """没有通用分时，格子给一个能点的「打分」按钮，而不是看不出含义的「—」。

    v1.0.130 起通用分可以按需打，空态必须能解释自己并给出下一步动作。
    """
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert ':title="generalBvButtonTip(a)"' in html, "空态单元格没有 title 提示"
    assert 'x-show="!generalBvChip(a) && !generalBvBusy(a)"' in html, (
        "空态没有「打分」按钮的显示条件"
    )
    assert "打分中…" in html, "打分中没有进度文案"
    app = APP_JS.read_text(encoding="utf-8")
    assert "scoreGeneralBv(a, force" in app, "app.js 里没有单篇打分方法"
    assert "/business-value" in app, "app.js 没有调用打分接口"


def test_score_column_headers_explain_themselves():
    """列头要能说明这一列是什么，hover 列头就能看到。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    head = _articles_table()
    head = head[: head.index("</thead>")]
    for label in ("质量分", "业务价值分"):
        cell = re.search(rf"<th\b[^>]*>{label}</th>", head)
        assert cell, f"表头 {label} 没找到"
        assert "title=" in cell.group(0), f"{label} 列头没有 title 说明"


def test_ops_column_stays_visible_when_the_table_scrolls():
    """操作列要够宽，并且钉在右侧：窄窗口横向滚动时按钮不能被裁掉。"""
    ops = _cols()[-1]
    # 148px 是实测内容宽 140px 加 8px 余量。
    assert ops[0] >= 148, f"操作列只有 {ops[0]}px，装不下查看/删除"
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'class="ops-col"' in html
    assert "articles-table-pinned" in html
    css = MAIN_CSS.read_text(encoding="utf-8")
    sticky = re.search(r"\.articles-table \.ops-col\s*\{([^}]*)\}", css)
    assert sticky, "操作列没有固定样式"
    body = sticky.group(1)
    assert "position: sticky" in body and "right: 0" in body
    assert re.search(r"\.articles-table-pinned\s*\{[^}]*min-width:\s*1176px", css)


def test_unselected_direction_scoring_asks_before_the_batch():
    """没勾选就打当前页，必须先确认篇数和耗时。"""
    app = APP_JS.read_text(encoding="utf-8")
    fn = re.search(r"async startBvDirectionScoring\(\) \{(.*?)\n    pollBvScores", app, re.S)
    assert fn, "startBvDirectionScoring 不是 async 或找不到函数体"
    body = fn.group(1)
    assert "pinnedSelectedIds" in body
    assert "批量方向打分" in body
    assert "moniModal.confirm" in body
    assert "连续打" in body


def test_direction_brief_editor_is_large_enough_to_write():
    """方向说明不是一行备注，输入框要能写下 200 字并显示计数。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    area = re.search(r'<textarea class="direction-brief"[^>]*>', html)
    assert area, "方向说明输入框没有加高"
    assert 'rows="8"' in area.group(0)
    assert " / 200" in html
    css = MAIN_CSS.read_text(encoding="utf-8")
    assert re.search(r"\.direction-brief\s*\{[^}]*min-height:\s*168px", css)
    assert "modal-content-direction" in html



def test_direction_editor_can_draft_guidance_and_names_the_model():
    """碎片想法生成草稿，研究提示词单独保存，并写明当前用的模型。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    idea = re.search(r'<textarea class="direction-idea"[^>]*>', html)
    guidance = re.search(r'<textarea class="direction-guidance"[^>]*>', html)
    assert idea and 'rows="4"' in idea.group(0) and 'maxlength="2000"' in idea.group(0)
    assert guidance and 'rows="8"' in guidance.group(0) and 'maxlength="800"' in guidance.group(0)
    assert " / 800" in html
    assert "生成草稿" in html
    assert "保存并立即研究" in html
    assert "directionModelHint()" in html

    app = APP_JS.read_text(encoding="utf-8")
    assert "/api/directions/draft" in app
    save = re.search(
        r"async configSaveDirection\(andResearch = false\) \{(.*?)\n    async generateDirectionDraft",
        app,
        re.S,
    )
    assert save, "configSaveDirection 没有带上研究开关"
    assert "research_guidance: nextGuidance" in save.group(1)
    assert "directionResearch(saved)" in save.group(1)
    assert "directionModelHint()" in app
    assert "hermes profile" in app

    css = MAIN_CSS.read_text(encoding="utf-8")
    assert re.search(r"\.direction-guidance\s*\{[^}]*min-height:\s*168px", css)
    assert re.search(r"\.direction-idea\s*\{[^}]*min-height:\s*96px", css)
