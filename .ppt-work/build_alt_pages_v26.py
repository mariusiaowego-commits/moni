# -*- coding: utf-8 -*-
"""备用页：通用业务价值分 · 十维量表（数据直接取自 moni/scoring/rubric.py）。

量表内容不是手写文案，而是从代码里的 Dimension 定义生成：维度名、权重、判定问题、
10 分与 0 分锚点都来自 rubric.py，避免与实现漂移。
"""
import importlib.util
import os
import sys

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn

BASE = "/Users/mt16/.codex/worktrees/32b5/moni/.ppt-work"
RUBRIC_SRC = "/Users/mt16/dev/moni/moni/scoring/rubric.py"
OUT = BASE + "/备用页-业务价值分十维量表.pptx"

FONT = "Heiti SC"
BG = RGBColor(0xF7, 0xF8, 0xFA)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
BLUE = RGBColor(0x00, 0x4B, 0x8D)
LIGHT = RGBColor(0xEA, 0xF1, 0xF8)
LINE = RGBColor(0xD9, 0xE1, 0xEB)
INK = RGBColor(0x12, 0x20, 0x2F)
MUTED = RGBColor(0x5C, 0x6B, 0x7F)
GOLD = RGBColor(0xA9, 0x76, 0x1F)


def _ea(run, name):
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = rPr.makeelement(qn(tag), {})
            rPr.append(el)
        el.set("typeface", name)


def fill_text(sh, text, size=14, bold=False, color=INK,
              align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.0):
    tf = sh.text_frame
    tf.word_wrap = True
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    for i, line in enumerate(text.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        r = p.add_run()
        r.text = line
        f = r.font
        f.name = FONT
        f.size = Pt(size)
        f.bold = bold
        f.color.rgb = color
        _ea(r, FONT)


def tb(s, x, y, w, h, text, size=14, bold=False, color=INK,
       align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.0):
    sh = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    fill_text(sh, text, size, bold, color, align, anchor, spacing)
    return sh


def shape(s, kind, x, y, w, h, fill=None, line=None, lw=1.0, radius=None):
    sh = s.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    if radius is not None:
        try:
            sh.adjustments[0] = radius
        except Exception:
            pass
    if fill is None:
        sh.fill.background()
    else:
        sh.fill.solid()
        sh.fill.fore_color.rgb = fill
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line
        sh.line.width = Pt(lw)
    sh.shadow.inherit = False
    return sh


def card(s, x, y, w, h, fill=WHITE, line=LINE, lw=1.0, radius=0.06):
    return shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, fill=fill,
                 line=line, lw=lw, radius=radius)


def hline(s, x, y, w, color=LINE, lw=0.012):
    return shape(s, MSO_SHAPE.RECTANGLE, x, y, w, lw, fill=color)


# ---- 量表数据：直接从 rubric.py 读 ----
spec = importlib.util.spec_from_file_location("_rubric", RUBRIC_SRC)
rub = importlib.util.module_from_spec(spec)
sys.modules["_rubric"] = rub
spec.loader.exec_module(rub)
DIMS = rub.DIMENSIONS

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
s = prs.slides.add_slide(prs.slide_layouts[6])
shape(s, MSO_SHAPE.RECTANGLE, 0, 0, 13.333, 7.5, fill=BG)

tb(s, 0.80, 0.46, 7.4, 0.30, "备用页   通用业务价值分 · 十维量表", 12.5, True, MUTED)
tb(s, 0.80, 0.74, 11.0, 0.60, "十维固定权重合计 100，每维按四档锚点判定", 28, True, BLUE)
shape(s, MSO_SHAPE.RECTANGLE, 0.80, 1.40, 0.86, 0.045, fill=BLUE)
tb(s, 0.80, 1.50, 11.73, 0.46,
   "量表编号 %s。每维 0–10 整数分，并须引用不少于 12 字的正文连续原句；"
   "无有效引文的维度封顶 3 分。\n总分由程序按固定权重计算，模型不提供总分。" % rub.RUBRIC_ID,
   12, False, MUTED, spacing=1.25)

COLS = [("维度", 1.35), ("权重", 0.62), ("判定问题", 4.60), ("10 分的样子", 4.90)]
X0 = 0.80
HY = 2.02
RH = 0.42
xs = []
acc = X0
for _, w in COLS:
    xs.append(acc)
    acc += w

shape(s, MSO_SHAPE.RECTANGLE, X0, HY, acc - X0, 0.32, fill=BLUE)
for (title, w), x in zip(COLS, xs):
    tb(s, x + 0.10, HY + 0.07, w - 0.20, 0.24, title, 11.5, True, WHITE)

for i, d in enumerate(DIMS):
    y = HY + 0.32 + i * RH
    shape(s, MSO_SHAPE.RECTANGLE, X0, y, acc - X0, RH,
          fill=WHITE if i % 2 == 0 else LIGHT)
    tb(s, xs[0] + 0.10, y + 0.12, COLS[0][1] - 0.20, 0.24, d.label, 11, True, INK)
    tb(s, xs[1] + 0.10, y + 0.12, COLS[1][1] - 0.20, 0.24, str(d.weight), 11, True, GOLD)
    tb(s, xs[2] + 0.10, y + 0.06, COLS[2][1] - 0.20, 0.34, d.question, 10, False, INK,
       spacing=1.15)
    tb(s, xs[3] + 0.10, y + 0.06, COLS[3][1] - 0.20, 0.34, d.anchors[3], 10, False, MUTED,
       spacing=1.15)
    hline(s, X0, y + RH - 0.008, acc - X0)

ny = HY + 0.32 + len(DIMS) * RH + 0.08
tb(s, X0, ny, 11.7, 0.26,
   "来源：moni/scoring/rubric.py（维度、权重、判定问题与锚点均取自代码定义）。",
   10.5, False, MUTED)

hline(s, 0.80, 6.94, 11.73)
tb(s, 0.80, 7.04, 9.0, 0.28, "moni · 新媒体渠道同业跟踪调研 AI 工作台", 10.5, False, MUTED)

prs.save(OUT)
print("wrote", OUT, "rows:", len(DIMS))
