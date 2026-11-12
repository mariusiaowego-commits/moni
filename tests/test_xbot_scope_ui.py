"""sprint-35 — X-bot 精选 UI 契约测试 (静态断言 + 少量 DOM 行为)。

覆盖 done-criteria AC-10 / AC-9 的 UI 部分。

口径: 实施方**不自称 UI 评审通过** —— 视觉判定由 agy / warden 做
(vision_analyze 看截图), 这里只钉死**结构与行为契约**。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / "moni" / "web" / "static" / "index.html"
APPJS = REPO / "moni" / "web" / "static" / "js" / "app.js"
CSS = REPO / "moni" / "web" / "static" / "css" / "main.css"


#: X-bot 专属区块在 index.html 里的起止标记。
_XBOT_BLOCK_START = "v1.0.70 (sprint-35): X-bot 精选专属区块"
_XBOT_BLOCK_END = '<div class="modal-footer">'


def _xbot_block(index_html: str) -> str:
    """取出 X-bot 专属区块的 HTML (含内层 template)。"""
    start = index_html.index(_XBOT_BLOCK_START)
    end = index_html.index(_XBOT_BLOCK_END, start)
    return index_html[start:end]


@pytest.fixture(scope="module")
def index_html() -> str:
    return INDEX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return APPJS.read_text(encoding="utf-8")


def _xbot_css_block() -> str:
    """X-bot 专属区块的 CSS 声明部分 (**去掉注释** + **不越界到后面的规则**)。

    注释里会解释"为什么不能用 --bg-secondary" —— 那段文字本身不该被
    当成声明来断言, 所以先把 /* */ 注释剥掉;
    区块在 ``.form-row label small.muted`` 之前结束, 截到那里为止
    (否则会把整个文件后面的既有样式都算进来)。
    """
    css = CSS.read_text(encoding="utf-8")
    marker = "v1.0.71 (sprint-35): X-bot 精选专属区块"
    idx = css.index(marker)
    # 标记在注释**里面** → 往前找注释起点 "/\*", 否则剥不掉注释
    start = css.rindex("/*", 0, idx)
    end = css.index(".form-row label small.muted", idx)
    return re.sub(r"/\*.*?\*/", "", css[start:end], flags=re.S)



# ── AC-10.1: X-bot 专属区块存在, 且只在 xbot_feed 时渲染 ─────────────────

def test_xbot_block_is_conditional_on_source_name(index_html: str):
    assert "fetchConfigData.source_name === 'xbot_feed'" in index_html


def test_xbot_block_has_scope_and_threshold_sections(index_html: str):
    assert "信源范围 (scope)" in index_html
    assert "入库门槛 (thresholds)" in index_html


def test_xbot_block_has_four_number_inputs(index_html: str):
    """4 个 type=number 门槛输入框 (2 topic × 2 字段)。

    源码里是 ``x-for="topic in ['wechat','xcircle']"`` 包着 2 个 number
    输入框 → 渲染出 4 个。
    """
    block = _xbot_block(index_html)
    assert "x-for=\"topic in ['wechat', 'xcircle']\"" in block
    assert block.count('type="number"') == 2
    # 每个输入框都带 min="0" / step="1"
    assert block.count('min="0"') >= 2
    assert block.count('step="1"') >= 2
    # 两个字段各一个输入框
    assert "min_followers" in block and "min_likes" in block


def test_threshold_inputs_use_typed_accessors_not_raw_json(index_html: str):
    """门槛走专用读写函数, 不是 raw JSON textarea (AC-10.2)。"""
    block = _xbot_block(index_html)
    assert "fetchConfigThresholdValue(topic, 'min_followers')" in block
    assert "fetchConfigThresholdValue(topic, 'min_likes')" in block
    assert "setFetchConfigThresholdValue" in block
    # 专属区块里没有 raw JSON 编辑器
    assert "json_object" not in block
    assert "setFetchConfigTextValue" not in block


def test_scope_has_three_blocks_with_editable_fields(index_html: str):
    block = _xbot_block(index_html)
    assert "xcircle 固定账号" in block
    assert "wechat 关键词正则" in block
    assert "wechat 每日查询词" in block
    # 三块都可编辑
    assert "setFetchConfigScopeList('xcircle_accounts'" in block
    assert "setFetchConfigScopeText('wechat_regex'" in block
    assert "setFetchConfigScopeList('wechat_queries'" in block


def test_upstream_not_changed_label_is_present(index_html: str):
    """必须写清楚"不会改变 X-bot 的搜索范围" + 串联语义 (PLAN §7)。"""
    block = _xbot_block(index_html)
    assert "不会改变 X-bot 的搜索范围" in block
    assert "第二道串联闸门" in block
    assert "0 = 不设限" in block


def test_no_emoji_in_xbot_block(index_html: str):
    """dad 明确禁 emoji 标识。"""
    block = _xbot_block(index_html)
    emoji = [c for c in block if ord(c) > 0x1F000]
    assert not emoji, f"emoji found in xbot block: {emoji}"


# ── AC-10.3: 其它信源的弹窗渲染不变 ─────────────────────────────────────

def test_generic_key_loop_excludes_xbot_only_keys(app_js: str):
    """xbot 的 thresholds / scope 不进通用键循环 (其它信源走原路径)。"""
    src = app_js[app_js.index("fetchConfigSortedKeys() {"):]
    src = src[:src.index("\n    },")]
    assert "xbot_feed" in src
    assert "k !== 'thresholds' && k !== 'scope'" in src


def test_sorted_keys_for_other_sources_is_unchanged(app_js: str):
    """其它信源 → keys = Object.keys(merged) (一字未改)。"""
    src = app_js[app_js.index("fetchConfigSortedKeys() {"):]
    src = src[:src.index("\n    },")]
    assert "const keys = isXbot" in src
    assert ": Object.keys(merged);" in src


# ── AC-9: 抓取结果面板出现「门槛过滤」段 ────────────────────────────────

def test_fetch_result_panel_has_threshold_section(app_js: str):
    assert "门槛过滤" in app_js
    assert "threshold_dropped" in app_js
    assert "threshold_dropped_by" in app_js
    assert "threshold_missing_by" in app_js


def test_threshold_section_has_no_emoji(app_js: str):
    src = app_js[app_js.index("门槛过滤"):]
    src = src[:src.index("\n      return lines;")]
    emoji = [c for c in src if ord(c) > 0x1F000]
    assert not emoji, f"emoji found in threshold section: {emoji}"


# ── 保存链路复用现有的 configSaveFetchConfig ─────────────────────────────

def test_save_path_is_existing_config_save(app_js: str):
    assert "configSaveFetchConfig()" in app_js
    src = app_js[app_js.index("configSaveFetchConfig() {"):]
    src = src[:src.index("\n    },")]
    # PUT 的是 merged 全量 (含 thresholds / scope)
    assert "JSON.stringify(this.fetchConfigData.merged)" in src


# ── CSS ─────────────────────────────────────────────────────────────────

def test_xbot_styles_exist():
    css = CSS.read_text(encoding="utf-8")
    assert ".fetch-config-xbot" in css
    assert ".fetch-config-threshold-row" in css
    assert ".fetch-config-scope-input" in css
    assert ".badge-source" in css


# ── P1-5: 深色模式可读性（主题变量 + 特异度）───────────────────────────

def test_css_uses_theme_vars_not_undefined_bg_secondary():
    """P1-5: `--bg-secondary` 在 moni 里**从未定义** → 回落近白, 深色下白底浅色字。

    新增的 X-bot 区块必须只用**已定义**的主题变量。
    """
    block = _xbot_css_block()
    assert "--bg-secondary" not in block
    assert "var(--card)" in block
    assert "var(--foreground)" in block
    assert "var(--border)" in block
    assert "var(--muted)" in block
    assert "var(--muted-fg)" in block


def test_css_has_no_hardcoded_colors_in_xbot_block():
    """P1-5: 区块里不留硬编码色值 (深色模式下对比度约 1.35:1)。"""
    block = _xbot_css_block()
    hardcoded = re.findall(r"#[0-9a-fA-F]{3,8}\b", block)
    assert not hardcoded, f"hardcoded colors in xbot css block: {hardcoded}"
    rgba = re.findall(r"rgba\(\s*0\s*,\s*0\s*,\s*0", block)
    assert not rgba, f"hardcoded rgba(0,0,0,...) border in xbot css block"


def test_scope_input_styles_override_global_input_selectors():
    """P1-5: 特异度 —— 通用 `input[type=text]` (0,1,1) 会压过 (0,1,0)。

    所以 scope 的输入框选择器必须**同时**带上足够特异度, 让 textarea 与
    text input 表现一致 (不再白底/深底交错)。
    """
    block = _xbot_css_block()
    assert ".fetch-config-xbot .fetch-config-scope-input" in block
    assert ".fetch-config-xbot .fetch-config-threshold-field input" in block


def test_scope_textarea_is_resizable_vertical():
    """P1-5 (agy): textarea 加 resize: vertical。"""
    assert "resize: vertical" in _xbot_css_block()


# ── P2-1: 移动端 <480px 门槛行阶梯错位 ──────────────────────────────────

def test_mobile_media_query_stacks_threshold_topic():
    """P2-1 (agy): 小屏下 topic 占满整行, 两个数字框并排 → 不阶梯错位。"""
    css = CSS.read_text(encoding="utf-8")
    assert "@media (max-width: 480px)" in css
    mq = css.split("@media (max-width: 480px)", 1)[1][:600]
    assert ".fetch-config-threshold-topic" in mq
    assert "width: 100%" in mq
    assert "min-width: 0" in mq


# ── P1-1: scope_source 三态的 UI 徽标映射 ───────────────────────────────

def test_scope_source_label_maps_all_three_states(app_js: str):
    """P1-1: 徽标要能显示三态 (上游导出 / moni 侧副本 / moni 侧兜底)。"""
    src = app_js[app_js.index("fetchConfigScopeSourceLabel() {"):]
    src = src[:src.index("\n    },")]
    assert "upstream" in src
    assert "moni_stored" in src
    assert "moni_fallback" in src
