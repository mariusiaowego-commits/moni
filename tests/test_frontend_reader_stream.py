"""v1.0.102 AI 对话流式渲染 —— 前端静态不变量。

根因：``.ai-structured-list > li { animation: ai-list-in 0.3s ease both; }``
在流式期间被每 12ms 的整段重绘反复重建，入场动画永远停在起始帧，
列表项一直半透明（录屏里那批"发白"的卡片）。

钉住的失败模式：

1. 流式期间整段重绘 —— settled（已定稿）必须与 tail（在写）分开渲染。
2. 流式期间列表入场动画没关 —— 卡片一直半透明。
3. 打字循环没切分 settled/tail —— 分屏渲染形同虚设。
4. 自动滚动无条件吸底 —— 用户上滑阅读时被拽回。
5. settled / tail 是后加的属性 —— Alpine 3 不响应，渲染永远不更新。
6. 流结束时没整段定稿 —— tail 里留着半截 markdown。
7. 代码围栏没成对就切分 —— settled 里留下半个 ```，整段变代码块。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "moni" / "web" / "static" / "index.html"
MAIN_CSS = ROOT / "moni" / "web" / "static" / "css" / "main.css"
READER_JS = ROOT / "moni" / "web" / "static" / "js" / "reader.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_stream_body_splits_settled_and_tail():
    """失败模式 1：不得再整段 x-html="readerRenderMarkdown(m.content)"。"""
    html = _read(INDEX_HTML)
    assert 'x-html="readerRenderMarkdown(m.content)"' not in html, (
        "assistant body must not re-render the whole markdown on every token"
    )
    assert "ai-stream-settled" in html and "ai-stream-tail" in html
    assert 'x-html="readerRenderMarkdown(m.settled)"' in html
    assert 'x-html="readerRenderMarkdown(m.tail)"' in html


def test_streaming_disables_list_entry_animation():
    """失败模式 2：流式期间列表入场动画必须关掉。"""
    css = _read(MAIN_CSS)
    assert ".ai-msg-body.is-streaming .ai-structured-list > li { animation: none; }" in css, (
        "the list entry animation must be off while streaming, otherwise the "
        "cards stay half-transparent (ai-list-in restarts on every re-render)"
    )


def test_typing_loop_splits_settled_and_tail():
    """失败模式 3：打字循环必须调用切分。"""
    js = _read(READER_JS)
    m = re.search(r"readerPushDelta\(msg, text\)\s*\{(.*?)\n    \}", js, re.S)
    assert m, "readerPushDelta missing"
    body = m.group(1)
    assert "readerSplitStream(msg)" in body
    assert "readerFollowStream()" in body


def test_auto_scroll_only_follows_when_pinned():
    """失败模式 4：上滑阅读时不能被流式输出拽回底部。"""
    js = _read(READER_JS)
    assert "readerAiPinned" in js, "readerAiPinned state missing"
    assert "readerTrackAiScroll" in js, "no scroll tracking"
    m = re.search(r"readerFollowStream\(\)\s*\{(.*?)\n    \}", js, re.S)
    assert m, "readerFollowStream missing"
    assert "readerAiPinned" in m.group(1), (
        "streaming scroll must be gated on the pinned flag"
    )
    html = _read(INDEX_HTML)
    assert '@scroll="readerTrackAiScroll($event)"' in html


def test_settled_and_tail_exist_from_the_start():
    """失败模式 5：Alpine 3 不响应后加属性，字段必须一开始就存在。"""
    js = _read(READER_JS)
    m = re.search(r"readerInitMessage\(m\)\s*\{(.*?)\n    \}", js, re.S)
    assert m, "readerInitMessage missing"
    assert "settled" in m.group(1) and "tail" in m.group(1)
    assert "readerInitMessage" in js, "loaded messages must go through readerInitMessage"
    pushes = re.findall(r"this\.readerMessages\.push\(\{(.*?)\n        \}\);", js, re.S)
    assistant = [p for p in pushes if "role: 'assistant'" in p]
    assert assistant, "assistant message push not found"
    body = assistant[0]
    assert "role: 'assistant'" in body
    assert "settled" in body and "tail" in body


def test_stream_end_finalises_whole_message():
    """失败模式 6：结束时必须整段定稿。"""
    js = _read(READER_JS)
    idx = js.find("addEventListener('done'")
    assert idx > 0, "done handler missing"
    body = js[idx : idx + 900]
    assert "msg.settled = String(msg.content || '')" in body
    assert "msg.tail = ''" in body


def test_split_never_breaks_a_code_fence():
    """失败模式 7：围栏不成对时不能切分。"""
    js = _read(READER_JS)
    m = re.search(r"readerSplitStream\(msg\)\s*\{(.*?)\n    \}", js, re.S)
    assert m, "readerSplitStream missing"
    body = m.group(1)
    assert "```" in body, "the split must count code fences"
    assert "% 2 === 0" in body, "the split must only cut where fences are balanced"
