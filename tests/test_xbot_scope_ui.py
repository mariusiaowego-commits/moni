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
_XBOT_BLOCK_START = "v1.0.71 (sprint-35): X-bot 精选专属区块"
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
    """xbot 的 thresholds / scope / scope_fence_enabled 不进通用键循环 (其它信源走原路径)。"""
    src = app_js[app_js.index("fetchConfigSortedKeys() {"):]
    src = src[:src.index("\n    },")]
    assert "xbot_feed" in src
    # t119u (pV P1-2): 排除集合写成 xbotOwnKeys 数组（多一个 scope_fence_enabled）
    assert "xbotOwnKeys" in src
    assert "'thresholds'" in src and "'scope'" in src
    assert "'scope_fence_enabled'" in src


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


# ── sprint-55 (v1.0.131): scope 是入库准入 → UI/契约必须如实 ──────────────

def test_scope_copy_matches_business_tag_gate(index_html: str):
    """t119u 文案契约：scope 默认只展示、业务 tag 才是唯一放行通道。"""
    block = _xbot_block(index_html)
    # 旧谎言必须彻底消失
    assert "不参与入库闸门" not in block
    assert "仅用于记录与展示" not in block
    assert "与领域关键词表是 <strong>OR</strong>" not in block
    assert "命中即准入" not in block
    # 新语义必须写明
    assert "入库由业务 tag 决定" in block
    assert "只作上游范围参考、不参与准入" in block
    assert "一条都不收（不会全放通）" in block
    # 围栏开关必须存在且默认语义写清
    assert "scope_fence_enabled" in block
    assert "围栏" in block
    # 仍然要保留上游边界与串联语义（P0 断言不能被顺手删）
    assert "不会改变 X-bot 的搜索范围" in block
    assert "第二道串联闸门" in block


def test_scope_status_line_reflects_fence_vs_display(index_html: str,
                                                    app_js: str):
    """状态行由后端 scope_mode / fence 字段驱动，不靠猜。"""
    block = _xbot_block(index_html)
    assert "fetchConfigScopeStatus()" in block

    src = app_js[app_js.index("fetchConfigScopeStatus() {"):]
    src = src[:src.index("\n    },")]
    assert "scope_fence_enabled" in src
    assert "scope_admission_active" in src
    assert "scope_stored" in src
    assert "当前只展示" in src
    assert "围栏" in src
    # 旧口径不许回潮
    assert "scope 命中即准入" not in src


def test_wechat_queries_is_marked_display_only(index_html: str):
    block = _xbot_block(index_html)
    assert "仅展示，不参与 moni 准入" in block


def test_scope_setters_mark_dirty(app_js: str):
    """改过 scope 面板 → dirty=true（未存储时唯一允许写回 scope 的条件）。"""
    for fn in ("setFetchConfigScopeList(key, text) {",
               "setFetchConfigScopeText(key, text) {"):
        src = app_js[app_js.index(fn):]
        src = src[:src.index("\n    },")]
        assert "fetchConfigScopeDirty = true" in src, fn


def test_save_strips_unstored_scope(app_js: str):
    """未显式存储且没动过 scope 面板 → 保存时不许把兜底 scope 写进库。

    这是 pV 交叉审的 P0: 「只改门槛保存一次」不得静默激活准入。

    断言强度（吃过一次亏: 只查标识符存在 → `if (false && …)` 变异仍是绿）:
    ① 守卫条件里必须**没有**短路常量（`false`/`true` 混进条件 = 守卫可能恒假）；
    ② 三个操作数都在条件里；
    ③ `delete …merged.scope` 必须落在守卫体内、且在 `fetch(` 之前。
    """
    src = app_js[app_js.index("configSaveFetchConfig() {"):]
    src = src[:src.index("\n    },")]

    guard_at = src.index("if (")
    cond_end = src.index("{", guard_at)
    cond = src[guard_at:cond_end]
    assert "false" not in cond, f"守卫条件里混进了短路常量: {cond!r}"
    assert "source_name === 'xbot_feed'" in cond
    assert "!this.fetchConfigData.scope_stored" in cond
    assert "!this.fetchConfigScopeDirty" in cond

    body_end = src.index("fetch(`", cond_end)
    body = src[cond_end:body_end]
    assert "delete this.fetchConfigData.merged.scope" in body, (
        "删除兜底 scope 的语句不在守卫体内 —— 「只改门槛保存」会静默激活准入"
    )
    # 仍然 PUT merged 全量（其它键的行为不变）
    assert "JSON.stringify(this.fetchConfigData.merged)" in src


def test_fetch_result_panel_has_business_tag_section(app_js: str):
    """漏斗: 后端 result 字段必须在前端渲染出来（否则用户只看到「抓了 N 条」）。"""
    src = app_js[app_js.index("formatFetchResult() {"):]
    src = src[:src.index("\n    },")]
    assert "业务 tag 准入" in src
    assert "business_tag_dropped" in src
    assert "url_deduped" in src
    assert "reply_dropped" in src
    assert "retweet_dropped" in src
    assert "scope_fence_enabled" in src
    assert "scope_fence_dropped" in src
    # 旧口径（scope 是准入通道）不许回潮
    assert "范围准入" not in src
    assert "scope 救回" not in src


# ── t114u 收尾: 「恢复默认展示 / 删除显式 scope」UI 路径（pV P1-2）────────

def test_restore_scope_button_only_when_stored(index_html: str):
    """按钮只在**已显式存储**时出现（未存储时展示兜底，没有可恢复的东西）。"""
    block = _xbot_block(index_html)
    assert "configRestoreScopeDefault()" in block
    at = block.index("configRestoreScopeDefault()")
    tag_start = block.rindex("<button", 0, at)
    tag = block[tag_start:at]
    assert "fetchConfigData.scope_stored" in tag, (
        "恢复按钮必须由 scope_stored 控制显隐 —— 否则未存储时点它会写出一份空/兜底 scope"
    )


def test_restore_scope_copy_states_it_stops_admission(index_html: str):
    """文案必须说清两件事：① 会停用 scope 准入；② 兜底值不会被写入准入。"""
    block = _xbot_block(index_html)
    at = block.index("configRestoreScopeDefault()")
    window = block[max(0, at - 1200):at + 200]
    assert "停用" in window, "没写明会停用 scope 准入"
    assert "兜底" in window
    assert "不会" in window


def test_restore_scope_sends_null_and_never_fallback(app_js: str):
    """恢复默认必须发 ``scope: null``（删键语义），绝不能把兜底副本当 scope 发出去。

    断言强度（按本仓教训）: 切方法体 → 剥注释 → 只认 ``scope: null`` 这个**语句**，
    并显式禁止方法体里出现兜底/展示字段的名字（出现 = 有可能把兜底写进准入）。
    """
    src = app_js[app_js.index("configRestoreScopeDefault() {"):]
    src = src[:src.index("\n    },")]
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    code = "\n".join(
        line for line in code.splitlines() if not line.lstrip().startswith("//")
    )
    assert "scope: null" in code, "恢复默认没有发 null 哨兵"
    for banned in ("xcircle_accounts", "wechat_regex", "wechat_queries",
                   "fetchConfigScopeList(", "fetchConfigScopeText("):
        assert banned not in code, (
            f"方法体里出现了 {banned!r} —— 恢复默认有可能把兜底值写进准入"
        )
    assert "fetch_config" in code and "PUT" in code
    assert "scope_stored" in code, "恢复后要按后端回包刷新 stored 状态"


# ── 文档契约: 旧 plan 的 §2 / AC-12 必须标注被取代（保留原文）────────────

_OLD_PLAN = REPO / "docs" / "plans" / "2026-11-12_xbot-scope-thresholds.md"


def test_old_plan_marks_scope_section_superseded():
    text = _OLD_PLAN.read_text(encoding="utf-8")
    # ① 原文必须**保留**（不得篡改历史决策）
    assert "### 2. scope 是 moni 侧副本，**不参与入库闸门**（关键决策）" in text
    assert "| AC-12 | scope 可编辑可保存；**改 scope 前后入库 URL 集合相同**" in text
    # ② 文首必须有取代标注，且指向新契约（sprint-55 / v1.0.131）
    head = text[: text.index("## 背景")]
    assert "已被取代" in head
    assert "sprint-55" in head and "v1.0.131" in head
    assert "2026-11-25" in head
    # ③ §2 与 AC-12 各自附近都要有就地标注（只改文首会让读者直接读到旧结论）
    at_section = text.index("### 2. scope 是 moni 侧副本")
    assert "已被取代" in text[at_section:at_section + 400]
    at_ac = text.index("| AC-12 |")
    assert "已被取代" in text[at_ac:at_ac + 600]


# ══ pV P0-2 / P1-2 ═════════════════════════════════════════════════════

def test_restore_scope_default_also_turns_off_fence(index_html: str, app_js: str):
    """pV P0-2：「恢复默认展示」必须同时停用围栏（前端 + 文案一致）。"""
    src = app_js[app_js.index("async configRestoreScopeDefault() {"):]
    src = src[:src.index("\n    },")]
    assert "scope: null" in src
    assert "scope_fence_enabled: false" in src, (
        "恢复默认展示必须同时把 scope_fence_enabled 置 false，"
        "否则会留下「围栏开着 + scope 空」= 整源 0 入库的陷阱"
    )

    block = _xbot_block(index_html)
    assert "等于停用围栏" not in block, (
        "旧文案「等于停用围栏」与真实行为矛盾（只删 scope 会拦到 0），必须改写"
    )
    assert "关闭「scope 围栏」" in block


def test_fetch_config_sorted_keys_excludes_scope_fence_enabled(app_js: str):
    """pV P1-2：围栏开关有专属区块，不能再被通用键循环渲染一次。"""
    src = app_js[app_js.index("fetchConfigSortedKeys() {"):]
    src = src[:src.index("\n    },")]
    assert "'scope_fence_enabled'" in src
    assert "xbotOwnKeys" in src
