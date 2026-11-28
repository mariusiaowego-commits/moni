# -*- coding: utf-8 -*-
"""文字溢出检测：估算每个文本框的换行后高度，报告超出框高的情况。
用法: python3 fit_check.py <pptx> [容差pt]
"""
import math
import sys
from pptx import Presentation

PATH = sys.argv[1] if len(sys.argv) > 1 else \
    "/Users/mt16/.codex/worktrees/32b5/moni/.ppt-work/决赛汇报-v16.pptx"
TOL_PT = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5


def cw(ch, size):
    o = ord(ch)
    if o > 0x2E80:          # CJK / 全角
        return size * 1.0
    if ch == ' ':
        return size * 0.30
    if ch in '.,:;!|/\\()[]{}%+-':
        return size * 0.36
    return size * 0.56


def frame_need(tf, width_in):
    total = 0.0
    for p in tf.paragraphs:
        runs = [r for r in p.runs if r.text]
        if not runs:
            continue
        size = max((r.font.size.pt for r in runs if r.font.size), default=14.0)
        text = "".join(r.text for r in runs)
        ls = p.line_spacing if isinstance(p.line_spacing, float) else 1.0
        tw = sum(cw(c, size) for c in text) / 72.0
        lines = max(1, math.ceil(tw / max(0.2, width_in) - 1e-6))
        total += lines * size * 1.22 * ls / 72.0
    return total


prs = Presentation(PATH)
bad = []
for i, s in enumerate(prs.slides, 1):
    for sh in s.shapes:
        if not sh.has_text_frame or not sh.text_frame.text.strip():
            continue
        tf = sh.text_frame
        ml = (tf.margin_left or 0) / 914400.0
        mr = (tf.margin_right or 0) / 914400.0
        mt = (tf.margin_top or 0) / 914400.0
        mb = (tf.margin_bottom or 0) / 914400.0
        w = sh.width / 914400.0 - ml - mr
        h = sh.height / 914400.0 - mt - mb
        need = frame_need(tf, w)
        over = need - h
        if over * 72 > TOL_PT:
            sizes = sorted({r.font.size.pt for p in tf.paragraphs for r in p.runs if r.font.size})
            bad.append((i, over * 72, "%.1fpt" % sizes[0] if sizes else "?",
                        "%.2f>%.2f" % (need, h), tf.text.replace("\n", "⏎")[:42]))
print("overflow count: %d" % len(bad))
for i, o, sz, dims, t in bad:
    print("P%02d  +%.1fpt  size=%s  need%s  %s" % (i, o, sz, dims, t))
