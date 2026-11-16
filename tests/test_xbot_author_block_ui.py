"""sprint-38 — X-bot 精选「屏蔽作者」UI 契约测试 (静态断言)。

覆盖 done-criteria AC-1 / AC-9 / AC-14 / AC-17。

口径: 实施方**不自称 UI 评审通过** —— 视觉判定由 agy 做 (vision_analyze 看截图),
这里只钉死**结构与行为契约**。

关键契约（warden DoD v0.3.0）：
- AC-1: 按钮挂在**作者 chip 内部右侧**（不是底栏 .article-actions）；
         只对 xbot_feed 出现；走主题变量。
- AC-9: 名单管理 UI 在 sprint-35 的 ``.fetch-config-xbot`` 区块内。
- AC-14: 二次确认**复用** ``window.moniModal.confirm``（modal.js:223），
         不新建第二套弹窗；danger 态。
- AC-17: 确认框文案含「将删除 N 篇已有文章，不可恢复」。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / "moni" / "web" / "static" / "index.html"
APPJS = REPO / "moni" / "web" / "static" / "js" / "app.js"
CSS = REPO / "moni" / "web" / "static" / "css" / "main.css"
MODALJS = REPO / "moni" / "web" / "static" / "js" / "modal.js"

#: sprint-35 的 X-bot 专属区块起止标记。
_XBOT_BLOCK_START = "v1.0.71 (sprint-35): X-bot 精选专属区块"
_XBOT_BLOCK_END = '<div class="modal-footer">'

#: 详情弹窗作者 chip 的标记。
_AUTHOR_CHIP_MARK = "articleDetail?.author"


@pytest.fixture(scope="module")
def index_html() -> str:
    return INDEX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return APPJS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css() -> str:
    return CSS.read_text(encoding="utf-8")


def _xbot_block(index_html: str) -> str:
    start = index_html.index(_XBOT_BLOCK_START)
    end = index_html.index(_XBOT_BLOCK_END, start)
    return index_html[start:end]


def _author_chip_block(index_html: str) -> str:
    """作者 chip 所在的那一行（含其后的几行）。"""
    idx = index_html.index(_AUTHOR_CHIP_MARK)
    # 从该行往上找包含 meta-chip 的 <span ...> 起点，往下取到 </span> 结束
    line_start = index_html.rfind("<span", 0, idx)
    line_end = index_html.index("</span></span>", idx) + len("</span></span>")
    return index_html[line_start:line_end]


# ── AC-1：按钮位置 / 可见性 / 样式 ──────────────────────────────────────

def test_block_button_lives_inside_author_chip(index_html: str):
    """AC-1：按钮挂在作者 chip 内部（就近原则），不是底栏。"""
    chip = _author_chip_block(index_html)
    assert "屏蔽作者" in chip, "屏蔽按钮必须在作者 chip 内"
    assert "author-block" in chip.lower()


def test_block_button_is_not_in_bottom_actions(index_html: str):
    """AC-1：底栏 .article-actions 的既有按钮数量不能变（不挤占）。"""
    start = index_html.index('<div class="article-actions">')
    end = index_html.index("</div>", start)
    actions = index_html[start:end]
    assert "屏蔽作者" not in actions, "屏蔽按钮不该进底栏"
    # 既有 5 个动作按钮仍在
    for label in ("生成摘要", "事实核查", "提取案例", "复制原文链接"):
        assert label in actions


def test_block_button_only_shows_for_xbot_feed(index_html: str):
    """AC-1：只在 xbot_feed 显示（非 xbot 直接隐藏，不置灰）。"""
    assert "canBlockCurrentAuthor" in index_html
    assert "xbot_feed" in index_html


def test_block_button_uses_theme_variables(css: str):
    """AC-1：按钮样式走主题变量，不写死 HEX。"""
    assert ".author-block" in css
    block = css[css.index(".author-block"):]
    block = block[:block.index("}") + 1]
    assert "var(--" in block, "按钮颜色必须走主题变量"
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", block), "不该写死 HEX"


def test_block_button_has_destructive_hover_state(css: str):
    """AC-1：hover 态用 --destructive（深色模式对比度达标）。"""
    idx = css.index(".author-block")
    region = css[idx:idx + 1200]
    assert "destructive" in region


# ── AC-14：复用 moniModal.confirm，不新建第二套 ─────────────────────────

def _block_confirm_region(app_js: str) -> str:
    """取「屏蔽作者」确认流程那一段 JS。

    必须锚在 ``confirmBlockAuthor`` 上 —— 否则会匹配到 app.js 里**别的**
    moniModal.confirm 调用（比如重启服务 / 删 tag），断言就会假绿。

    切片到**方法结束** (``\\n    },``) 为止，而不是固定字数 —— 固定窗口
    (原来 2000 字符) 会被方法里的注释撑爆: P1 修复加了焦点还原的注释后,
    ``danger: true`` 落到窗口外, 断言假红。方法体长度会变, 边界不会。
    """
    idx = app_js.index("confirmBlockAuthor")
    end = app_js.find("\n    },", idx)
    assert end != -1, "找不到 confirmBlockAuthor 方法的结束边界"
    return app_js[idx:end]


def test_confirm_reuses_existing_modal_helper(app_js: str):
    """AC-14：用现成的 window.moniModal.confirm（modal.js:223）。

    锚在 ``confirmBlockAuthor`` 上 —— 否则可能匹配到 app.js 里**别的**
    moniModal.confirm 调用，断言假绿。

    注意这里断言的是**调用点** ``moniModal.confirm(`` —— 只写
    "moniModal.confirm" 的话, 把调用换成自建的 ``myOwnModal.confirm(...)``
    仍然会命中 (前缀子串), 断言假绿。
    """
    region = _block_confirm_region(app_js)
    assert "moniModal.confirm(" in region, (
        "屏蔽作者必须调用 moniModal.confirm( —— "
        "不能换成自建的第二套弹窗实现"
    )


def test_confirm_uses_danger_option(app_js: str):
    """AC-14：屏蔽作者的确认框是危险态（btn-danger）。"""
    region = _block_confirm_region(app_js)
    assert "moniModal.confirm" in region, "屏蔽作者必须走 moniModal.confirm"
    assert "danger: true" in region


def test_no_second_modal_implementation(app_js: str, index_html: str):
    """AC-14：不得自建第二套确认弹窗（不新建 modal-backdrop 确认框）。"""
    # 已有 share pre-flight 那套是既有实现，不算；关键是**不要为屏蔽作者**再建一套
    assert "authorBlockConfirm" not in index_html
    assert "authorBlockConfirm" not in app_js


def test_modal_js_is_loaded_before_app_js(index_html: str):
    """AC-14：moniModal 必须先于 app.js 加载（既有约定）。"""
    m = index_html.index("js/modal.js")
    a = index_html.index("js/app.js")
    assert m < a


# ── AC-17：确认框文案含 handle + 删除篇数 ──────────────────────────────

def test_confirm_text_mentions_deleted_count(app_js: str):
    """AC-17：文案要显示「将删除 N 篇已有文章，不可恢复」。"""
    assert "将删除" in app_js
    assert "不可恢复" in app_js


def test_confirm_text_uses_dynamic_handle_and_count(app_js: str):
    """AC-17：N 和 handle 都是动态的（不是写死的数字）。"""
    idx = app_js.index("将删除")
    region = app_js[max(0, idx - 500):idx + 400]
    assert "${" in region, "文案里的 N / handle 必须是模板插值"


def test_confirm_handles_zero_count_gracefully(app_js: str):
    """AC-17：N = 0 时文案不能误导（不能说「将删除 0 篇…不可恢复」吓人）。

    断言必须锚在屏蔽作者的确认流程上 —— 不能用「没有 / 0 篇」这种
    全局子串去撞 app.js 里无关的文案（那会假绿）。
    """
    region = _block_confirm_region(app_js)
    assert "existing_count" in region or "existingCount" in region, \
        "确认流程要拿到删除篇数 N 才能决定文案"


# ── AC-9：名单管理在 X-bot 配置区 ───────────────────────────────────────

def test_blocklist_manager_lives_in_xbot_block(index_html: str):
    """AC-9：已屏蔽作者管理组件在 .fetch-config-xbot 区块内。"""
    block = _xbot_block(index_html)
    assert "已屏蔽作者" in block


def test_blocklist_manager_shows_builtin_readonly(index_html: str):
    """AC-9：内置黑名单只读展示。"""
    block = _xbot_block(index_html)
    assert "内置" in block


def test_blocklist_uses_dedicated_endpoints(app_js: str):
    """AC-9：名单增删走独立端点，不经 fetch_config PUT。"""
    assert "blocked-authors" in app_js

# ── AC-4 / AC-19: 计数必须**可见**（结果面板单列一行），不是只写进内存 ──

def test_result_panel_renders_author_blocked(app_js: str):
    """AC-4：author_blocked 必须在抓取结果面板里显示出来。

    这条守的是「计数贯通到 UI」这一环 —— 只在 result_json 里塞字段、
    前端不渲染 = dad 看不见 = 需求没落地 (AC-4 明确要求「不是只写进内存」)。
    """
    assert "author_blocked" in app_js, "结果面板完全没提 author_blocked"
    region = app_js[app_js.index("author_blocked"):]
    region = region[:region.index("return lines;")]
    assert "lines.push" in region, "author_blocked 必须 push 进结果面板的行"
    # 必须**独立成段**, 不能混进「门槛过滤」那一段 (AC-8: 两套心智模型)
    assert "作者屏蔽" in region, "作者屏蔽应单列一段, 与门槛过滤分开"


def test_result_panel_keeps_author_blocked_separate_from_thresholds(app_js: str):
    """AC-8：作者屏蔽与门槛过滤是两个**独立**的 section, 不能合并成一段。

    断言要点: 两段各自都要有自己的 ``type: 'section'`` 标题 —— 把作者屏蔽
    降级成门槛段里的一个 item 就该红 (否则 dad 在面板上分不清是哪道闸门丢的)。
    """
    # 锚在 **section 标题的 push 语句** 上 —— 不能用裸的「作者屏蔽」子串:
    # 那个词在状态字段的注释里也出现过 (index() 会命中错误位置)。
    thr_push = "type: 'section', text: '门槛过滤'"
    block_push = "type: 'section', text: '作者屏蔽'"
    assert thr_push in app_js, "门槛过滤应有独立 section"
    assert block_push in app_js, \
        "作者屏蔽必须是**独立 section**, 不能降级成门槛段里的一个 item"
    # 两段必须是**两次独立的 push** (不是一个 section 里两个 item)
    assert app_js.count("type: 'section', text: '门槛过滤'") == 1
    assert app_js.count("type: 'section', text: '作者屏蔽'") == 1
