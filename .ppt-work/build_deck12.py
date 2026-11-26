# -*- coding: utf-8 -*-
"""moni 决赛汇报 PPT v21 构建脚本（13 页，浅色，台上字号下限，左轨 1/4）

v21 = v20 去掉 P02-b（mermaid 分支图）与 P02-c（表格）两页备选样式，
主流程只保留 P02 五步链条图；两页作为备用素材另存。"""
import math
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.oxml.ns import qn

BASE = "/Users/mt16/.codex/worktrees/32b5/moni/.ppt-work"
ASSETS = BASE + "/assets"
V16 = BASE + "/assets/v16"
OUT = BASE + "/决赛汇报-v21.pptx"

FONT = "Heiti SC"
BG = RGBColor(0xF7, 0xF8, 0xFA)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
BLUE = RGBColor(0x00, 0x4B, 0x8D)
BLUE2 = RGBColor(0x11, 0x63, 0xA8)
LIGHT = RGBColor(0xEA, 0xF1, 0xF8)
GOLD = RGBColor(0xA9, 0x76, 0x1F)
GOLD_D = RGBColor(0x76, 0x51, 0x11)
GOLDL = RGBColor(0xF6, 0xEE, 0xDF)
LINE = RGBColor(0xD9, 0xE1, 0xEB)
INK = RGBColor(0x12, 0x20, 0x2F)
MUTED = RGBColor(0x5C, 0x6B, 0x7F)

TOTAL = 13
M = 0.80
RAIL_X = 0.80
RAIL_TW = 2.32
BODY_X = 4.03
BODY_W = 8.50


def _ea(run, name):
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = rPr.makeelement(qn(tag), {})
            rPr.append(el)
        el.set("typeface", name)


SIZE_FLOOR = {8.0: 9.0, 8.5: 10.5, 9.0: 10.5, 9.5: 12.0, 10.0: 12.0,
               10.5: 12.0, 11.0: 12.0, 11.5: 12.5}


def bump(size):
    """v17：把过小的字号抬到台上可读的下限，exact=True 的调用除外。"""
    return SIZE_FLOOR.get(round(size, 1), size)


def fill_text(sh, text, size=14, bold=False, color=INK,
              align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.0, after=0,
              exact=False):
    if not exact:
        size = bump(size)
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
        p.space_after = Pt(after)
        r = p.add_run()
        r.text = line
        f = r.font
        f.name = FONT
        f.size = Pt(size)
        f.bold = bold
        f.color.rgb = color
        _ea(r, FONT)
    return sh


def tb(s, x, y, w, h, text, size=14, bold=False, color=INK,
       align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.0, after=0,
       exact=False):
    sh = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    return fill_text(sh, text, size, bold, color, align, anchor, spacing, after,
                     exact=exact)


def shape(s, kind, x, y, w, h, fill=None, line=None, lw=1.0, dash=None, radius=None):
    sh = s.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    sh.shadow.inherit = False
    style = sh._element.find(qn("p:style"))
    if style is not None:
        sh._element.remove(style)
    if radius is not None and kind == MSO_SHAPE.ROUNDED_RECTANGLE:
        sh.adjustments[0] = min(0.5, radius / max(0.01, min(w, h)))
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
        if dash:
            sh.line.dash_style = dash
    return sh


def card(s, x, y, w, h, fill=WHITE, line=LINE, lw=1.0, radius=0.06):
    return shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, fill=fill, line=line, lw=lw, radius=radius)


def badge(s, x, y, n, d=0.32, fill=BLUE, color=WHITE, size=11):
    sh = shape(s, MSO_SHAPE.OVAL, x, y, d, d, fill=fill)
    fill_text(sh, str(n), size, True, color, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE,
              exact=True)
    return sh


def numdot(s, x, y, n, fill=LIGHT, color=BLUE2, d=0.30, size=11):
    sh = shape(s, MSO_SHAPE.OVAL, x, y, d, d, fill=fill)
    fill_text(sh, str(n), size, True, color, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE,
              exact=True)
    return sh


def hline(s, x, y, w, color=LINE, lw=0.012):
    return shape(s, MSO_SHAPE.RECTANGLE, x, y, w, lw, fill=color)


def vline(s, x, y, h, color=LINE, lw=0.012):
    return shape(s, MSO_SHAPE.RECTANGLE, x, y, lw, h, fill=color)


def connector(s, x1, y1, x2, y2, color=BLUE, lw=0.9, dash=None):
    cn = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    cn.line.color.rgb = color
    cn.line.width = Pt(lw)
    if dash:
        cn.line.dash_style = dash
    return cn


def pic(s, path, x, y, w=None, h=None):
    kw = {}
    if w:
        kw["width"] = Inches(w)
    if h:
        kw["height"] = Inches(h)
    return s.shapes.add_picture(path, Inches(x), Inches(y), **kw)


def new_slide(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    shape(s, MSO_SHAPE.RECTANGLE, 0, 0, 13.333, 7.5, fill=BG)
    return s


def header(s, num, kicker, title, pageno):
    tb(s, M, 0.46, 7.4, 0.30, "%s   %s" % (num, kicker), 12.5, True, MUTED)
    tb(s, M, 0.74, 10.6, 0.60, title, 28, True, BLUE)
    shape(s, MSO_SHAPE.RECTANGLE, M, 1.40, 0.86, 0.045, fill=BLUE)
    tb(s, 10.90, 0.49, 1.63, 0.30, "%02d/%d" % (pageno, TOTAL), 11.5, True, MUTED,
       PP_ALIGN.RIGHT, exact=True)


def footer(s, text="moni · 新媒体渠道同业跟踪调研 AI 工作台"):
    hline(s, M, 6.94, 11.73)
    tb(s, M, 7.04, 9.0, 0.28, text, 10.5, False, MUTED, exact=True)


def demo_tag(s, x, y, text, w=2.10):
    sh = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, 0.34, fill=GOLDL, line=GOLD, lw=1.0, radius=0.05)
    fill_text(sh, text, 11.5, True, GOLD, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)


STEPS = ["内容处理", "价值评估", "检验核验", "提取案例", "分享简报"]
SUBS = ["15 路信源，本机过滤", "0–100，低分不删", "结论带出处，逐条对原文",
        "五字段，带编号", "8 种格式，按场景发"]


def rail(s, active, branch=False, branch_at=3):
    x, top = RAIL_X, 1.86
    gap = 1.02
    if isinstance(active, int):
        active = (active,)
    tag = str(active[0]) if len(active) == 1 else "%d-%d" % (active[0], active[-1])
    label = "流程 · %s/5" % tag
    if branch:
        label = "流程 · %s·分支/5" % tag
    tb(s, x, 1.56, RAIL_TW + 0.08, 0.22, label, 9, True, MUTED, exact=True)

    ys = [top + i * gap for i in range(6)]

    cx = x + 0.11
    shape(s, MSO_SHAPE.RECTANGLE, cx - 0.006, top + 0.11, 0.012, ys[-1] - top, fill=LINE)

    done_before = min(active) - 1
    for i in range(1, 6):
        yy = ys[i - 1]
        if i in active:
            fillc, numc = BLUE, WHITE
            name_c, name_b = INK, True
            sub_c = GOLD
        elif i <= done_before:
            fillc, numc = LIGHT, BLUE2
            name_c, name_b = BLUE2, False
            sub_c = MUTED
        else:
            fillc, numc = WHITE, MUTED
            name_c, name_b = MUTED, False
            sub_c = MUTED
        dot = shape(s, MSO_SHAPE.OVAL, x, yy, 0.22, 0.22, fill=fillc,
                    line=None if (i in active or i <= done_before) else LINE)
        fill_text(dot, str(i), 9, True, numc, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE,
                  exact=True)
        tb(s, x + 0.34, yy - 0.04, RAIL_TW, 0.28, STEPS[i - 1], 12, name_b, name_c)
        tb(s, x + 0.34, yy + 0.24, RAIL_TW, 0.24, SUBS[i - 1], 10.5, False, sub_c)
        if branch and i == branch_at:
            by = yy + 0.56
            shape(s, MSO_SHAPE.RECTANGLE, cx, by + 0.16, 0.32, 0.012, fill=GOLD)
            bx = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x + 0.34, by, RAIL_TW - 0.10, 0.34,
                       fill=GOLDL, line=GOLD, radius=0.03)
            fill_text(bx, "深度阅读 · 分支", 10.5, True, GOLD, PP_ALIGN.CENTER,
                      MSO_ANCHOR.MIDDLE)




DASH = {"dash": MSO_LINE_DASH_STYLE.DASH, None: None}


def arrow(s, x1, y1, x2, y2, color=BLUE2, lw=1.2, dash="dash"):
    cn = connector(s, x1, y1, x2, y2, color=color, lw=lw, dash=DASH.get(dash))
    ln = cn._element.spPr.find(qn("a:ln"))
    if ln is not None:
        tail = ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"})
        ln.append(tail)
    return cn


def chip(s, x, y, w, h, text, size=11.5, fill=WHITE, line=BLUE, color=BLUE, bold=True):
    sh = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, fill=fill, line=line, lw=1.0, radius=0.04)
    fill_text(sh, text, size, bold, color, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE, exact=True)
    return sh


# ---------- P01 ----------
def p01(prs):
    s = new_slide(prs)
    header(s, "01", "为什么做", "同业情报靠人工盯盘，被动响应且难沉淀", 1)
    top = [
        ("我们的职责", "把同业动作整理成业务可用的参考"),
        ("要盯什么", "同业的新产品、营销活动、监管口径和渠道打法"),
        ("为什么必须盯", "同业动作快、渠道散，漏掉一次可能慢半拍"),
    ]
    widths = [3.84, 3.84, 3.84]
    x = M
    for i, (lab, val) in enumerate(top):
        w = widths[i]
        card(s, x, 1.60, w, 1.40)
        tb(s, x + 0.20, 1.74, w - 0.40, 0.28, lab, 12, True, MUTED)
        tb(s, x + 0.20, 2.06, 3.44, 0.84, val, 14, True, INK, spacing=1.3)
        x += w + 0.105

    hline(s, M, 3.16, 11.73)

    lanes = [
        ("路径 A", "日常盯盘", [
            ("刷", "各自关注几十个公众号"),
            ("挑", "凭经验筛出有用的"),
            ("摘", "手动摘机构和数字"),
            ("拼", "拼成周报发到群里"),
            ("沉", "发完群文件就没人再翻"),
        ]),
        ("路径 B", "临时交办", [
            ("接", "领导或分行丢来友行截图"),
            ("搜", "临时分头全网找同业做法"),
            ("凑", "熬夜拼一份应急对照"),
            ("讲", "讲完就各自散了"),
        ]),
    ]
    for li, (tag, name, steps) in enumerate(lanes):
        x = M + li * 3.85
        w = 3.70
        card(s, x, 3.34, w, 3.36, fill=WHITE)
        tb(s, x + 0.20, 3.46, 1.20, 0.26, tag, 12, True, BLUE)
        tb(s, x + 0.20, 3.72, w - 0.40, 0.30, name, 15, True, INK)
        hline(s, x + 0.20, 4.08, w - 0.40)
        step = 0.50 if len(steps) == 5 else 0.62
        for i, (n, d) in enumerate(steps):
            y = 4.20 + i * step
            numdot(s, x + 0.20, y + 0.03, i + 1, d=0.24, size=9)
            tb(s, x + 0.52, y - 0.02, 0.42, 0.28, n, 12, True, BLUE2)
            tb(s, x + 0.96, y, w - 1.16, 0.30, d, 12, False, MUTED)

    x = 8.50
    w = 4.03
    card(s, x, 3.34, w, 3.36, fill=LIGHT, line=None)
    tb(s, x + 0.22, 3.46, w - 0.44, 0.30, "两条线跑下来的问题", 14, True, BLUE)
    pains = [
        ("等上面派任务", "上面交办才动手，平时没有常态盯盘的办法"),
        ("每周约二十人时", "十人团队各花两小时，大半时间耗在筛水文上"),
        ("口径容易打架", "人工摘录凭经验，同一家银行前后记不一致"),
        ("材料发完就沉底", "群聊和本地文档没法复用，下次遇到还得从头找"),
    ]
    for i, (t, d) in enumerate(pains):
        y = 3.92 + i * 0.72
        tb(s, x + 0.22, y - 0.02, w - 0.44, 0.30, t, 13, True, INK)
        tb(s, x + 0.22, y + 0.28, w - 0.44, 0.32, d, 12, False, MUTED)
    footer(s)


# ---------- P02 工作流（样式一） ----------
def p02(prs):
    s = new_slide(prs)
    header(s, "02", "工作流", "五步走一圈，每一步都能回头改口径", 2)
    nodes = [
        ("内容处理", "15 路信源，本机过滤"),
        ("价值评估", "0–100，低分不删"),
        ("检验核验", "结论带出处，逐条对原文"),
        ("提取案例", "五字段，带编号"),
        ("分享简报", "8 种格式，按场景发"),
    ]
    n = len(nodes)
    w, gap = 2.12, 0.28
    x0 = M
    top = 1.62
    for i, (t, d) in enumerate(nodes):
        x = x0 + i * (w + gap)
        hot = i == n - 1
        card(s, x, top, w, 1.16, fill=WHITE)
        shape(s, MSO_SHAPE.RECTANGLE, x, top, w, 0.05, fill=GOLD if hot else BLUE)
        numdot(s, x + 0.18, top + 0.18, i + 1, fill=LIGHT, color=BLUE2, d=0.28, size=10)
        tb(s, x + 0.54, top + 0.16, w - 0.70, 0.32, t, 14, True, INK)
        tb(s, x + 0.18, top + 0.58, w - 0.36, 0.52, d, 11.5, False, MUTED, spacing=1.25, exact=True)
        if i < n - 1:
            shape(s, MSO_SHAPE.RIGHT_ARROW, x + w + 0.03, top + 0.48, 0.20, 0.22, fill=LINE)

    centers = [x0 + i * (w + gap) + w / 2 for i in range(n)]
    # 复盘带
    band_y = 3.52
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 1.10, band_y, 6.10, 0.40, fill=WHITE,
          line=LINE, lw=1.0, radius=0.04)
    tb(s, 1.26, band_y + 0.06, 5.80, 0.28, "复盘：哪一步口径不对，就回哪一步改", 12, True, MUTED,
       PP_ALIGN.CENTER, exact=True)
    backs = [
        (0, "改信源与标签"),
        (1, "改问法与权重"),
        (2, "重新核查"),
    ]
    for idx, label in backs:
        cx = centers[idx]
        arrow(s, cx, band_y - 0.04, cx, top + 1.22, color=BLUE2, lw=1.1)
        tb(s, cx - 0.95, band_y - 0.44, 1.90, 0.28, label, 11, True, BLUE2, PP_ALIGN.CENTER, exact=True)
    # 从分享简报回到复盘带
    arrow(s, centers[-1], top + 1.22, centers[-1], band_y + 0.60, color=BLUE2, lw=1.1)
    shape(s, MSO_SHAPE.RECTANGLE, 7.20, band_y + 0.594, centers[-1] - 7.20, 0.012, fill=BLUE2)
    arrow(s, 7.20, band_y + 0.60, 7.20, band_y + 0.42, color=BLUE2, lw=1.1)
    tb(s, 7.30, band_y + 0.66, 4.60, 0.28, "每天简报发出去，团队当天就会给回反馈", 11, False, MUTED, exact=True)

    # moni 工作台（底座）
    plat_y = 4.42
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, M, plat_y, 11.73, 1.36, fill=BLUE, line=None, radius=0.06)
    tb(s, 1.00, plat_y + 0.14, 5.00, 0.30, "moni 工作台 · 采集到出简报都在这一层", 13, True, WHITE, exact=True)
    mods = [
        ("采集", "15 路信源定时入库"),
        ("过滤", "渠道关键词 + 去噪"),
        ("打分", "质量分 + 价值分"),
        ("核查", "摘要 + 事实核查"),
        ("存案例", "主体 动作 时间 原文"),
        ("出简报", "8 种格式按场景"),
    ]
    mw, mg = 1.83, 0.10
    for i, (t, d) in enumerate(mods):
        x = 1.00 + i * (mw + mg)
        sh = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, plat_y + 0.54, mw, 0.66, fill=WHITE,
                   line=None, radius=0.04)
        tb(s, x, plat_y + 0.60, mw, 0.26, t, 12, True, BLUE, PP_ALIGN.CENTER, exact=True)
        tb(s, x + 0.08, plat_y + 0.88, mw - 0.16, 0.26, d, 10.5, False, MUTED, PP_ALIGN.CENTER, exact=True)

    bar = card(s, M, 6.00, 11.73, 0.80, fill=LIGHT, line=None)
    tb(s, 1.04, 6.10, 11.2, 0.30, "前三步取材，第四步存案例，第五步发出去，再回头改采集口径。", 13, True, BLUE)
    tb(s, 1.04, 6.44, 11.2, 0.28,
       "每一步都留了配置入口：想加公众号改信源清单，觉得某类文章抓偏了改关键词，不用动代码就能重跑。",
       12, False, MUTED, spacing=1.25, exact=True)
    footer(s)


# ---------- P02-b 工作流（样式二：mermaid）· v21 备用页，不进正片 ----------
def p02b(prs):
    s = new_slide(prs)
    header(s, "02-b", "工作流 · 备选样式", "同一套流程换一种画法：主线、分支和回流各在哪", 3)
    from PIL import Image as _Im
    p = ASSETS + "/wf-mermaid.png"
    with _Im.open(p) as im:
        iw, ih = im.size
    dw = 8.72
    dh = dw * ih / iw
    dx = (13.333 - dw) / 2
    pic(s, p, dx, 1.54, w=dw)
    shape(s, MSO_SHAPE.RECTANGLE, dx, 1.54, dw, dh, fill=None, line=LINE, lw=1.0)

    y = 5.36
    backs = [
        ("改信源与标签", "回内容处理", "某类文章抓偏了，先调标签和信源清单"),
        ("改问法与权重", "回价值评估", "打分问法不对，换问法，权重不动"),
        ("重新核查", "回检验核验", "结论对不上原文，重做一遍或转深度阅读"),
    ]
    for i, (t, tgt, d) in enumerate(backs):
        x = M + i * (3.84 + 0.105)
        card(s, x, y, 3.84, 0.94)
        shape(s, MSO_SHAPE.RECTANGLE, x, y, 3.84, 0.05, fill=BLUE)
        tb(s, x + 0.20, y + 0.14, 1.90, 0.28, t, 13, True, INK)
        chip(s, x + 2.16, y + 0.12, 1.48, 0.30, tgt, 11, LIGHT, BLUE, BLUE)
        tb(s, x + 0.20, y + 0.50, 3.44, 0.36, d, 11, False, MUTED, spacing=1.25, exact=True)

    bar = card(s, M, 6.44, 11.73, 0.38, fill=LIGHT, line=None)
    tb(s, 1.04, 6.50, 11.2, 0.28,
       "采集、过滤、打分、核查、存案例、出简报，这六件事都在 moni 工作台上跑。",
       12.5, True, BLUE)
    footer(s)


# ---------- P02-c 工作流（样式三：一张表）· v21 备用页，不进正片 ----------
def p02c(prs):
    s = new_slide(prs)
    header(s, "02-c", "工作流 · 备选样式", "用一张表说清：每步谁做、产出什么、发现不对改哪里", 4)
    stages = [
        ("内容处理", "定时抓取、关键词过滤、去噪清洗", "干净正文入池", "改信源与标签"),
        ("价值评估", "质量分本地算，价值分模型打分", "排序后的候选", "改问法与权重"),
        ("检验核验", "摘要梳理、事实核查、一句话结论", "可汇报的结论", "重新核查 / 深度阅读"),
        ("提取案例", "主体、动作、时间、对手方、原文", "可回查的案例", "补字段 / 改口径"),
        ("分享简报", "按场合打包成 8 种格式", "发出去的简报", "改简报结构"),
    ]
    x0 = M
    cw, gap = 2.24, 0.10
    heads = ["环节", "这一步做什么", "产出什么", "发现不对时改哪里"]
    rows = [
        ("我们做", None), None, None, None,
    ]
    # 表头
    tb(s, x0, 1.62, 4.0, 0.28, "工作流 · 五步", 12, True, MUTED, exact=True)
    hdr_y = 1.94
    for i, (t, d, out, back) in enumerate(stages):
        x = x0 + i * (cw + gap)
        card(s, x, hdr_y, cw, 4.30, fill=WHITE)
        shape(s, MSO_SHAPE.RECTANGLE, x, hdr_y, cw, 0.05, fill=BLUE if i < 4 else GOLD)
        numdot(s, x + 0.16, hdr_y + 0.18, i + 1, fill=LIGHT, color=BLUE2, d=0.28, size=10)
        tb(s, x + 0.52, hdr_y + 0.16, cw - 0.66, 0.30, t, 13, True, INK)
        hline(s, x + 0.16, hdr_y + 0.58, cw - 0.32)
        tb(s, x + 0.16, hdr_y + 0.66, cw - 0.32, 0.24, "做什么", 10.5, True, MUTED, exact=True)
        tb(s, x + 0.16, hdr_y + 0.92, cw - 0.32, 1.00, d, 12, False, INK, spacing=1.3, exact=True)
        hline(s, x + 0.16, hdr_y + 1.96, cw - 0.32)
        tb(s, x + 0.16, hdr_y + 2.04, cw - 0.32, 0.24, "产出什么", 10.5, True, MUTED, exact=True)
        tb(s, x + 0.16, hdr_y + 2.30, cw - 0.32, 0.52, out, 12, True, BLUE, spacing=1.3, exact=True)
        hline(s, x + 0.16, hdr_y + 2.88, cw - 0.32)
        tb(s, x + 0.16, hdr_y + 2.96, cw - 0.32, 0.24, "发现不对时改哪里", 10.5, True, MUTED, exact=True)
        tb(s, x + 0.16, hdr_y + 3.22, cw - 0.32, 0.86, back, 12, False, INK, spacing=1.3, exact=True)

    bar = card(s, M, 6.42, 11.73, 0.44, fill=LIGHT, line=None)
    tb(s, 1.04, 6.50, 11.2, 0.28,
       "五个环节共用同一套工作台：采集、过滤、打分、核查、存案例、出简报都在 moni 里跑。",
       12, True, BLUE)
    footer(s)


def arrow2(s, x1, y1, x2, y2, color=BLUE2, lw=1.1, dash="dash"):
    return arrow(s, x1, y1, x2, y2, color=color, lw=lw, dash=dash)


# ---------- P03 ----------
def p03(prs):
    s = new_slide(prs)
    header(s, "03", "工作台", "模型先筛一遍，人再挑一遍：标星的才进下一段", 3)
    from PIL import Image as _Im
    p = ASSETS + "/p03-pick-clean.png"
    with _Im.open(p) as im:
        iw, ih = im.size
    iw_, ih_ = 7.55, 7.55 * ih / iw
    pic(s, p, M, 1.62, w=iw_)
    shape(s, MSO_SHAPE.RECTANGLE, M, 1.62, iw_, ih_, fill=None, line=LINE, lw=1.0)
    tb(s, M, 1.62 + ih_ + 0.06, iw_, 0.28,
       "生产环境 8900 的文章查阅页，2026-11-26 实拍", 10.5, False, MUTED, exact=True)

    marks = [
        ("按方向分桶", "顶部这一排是方向汇总，银行、AI×金融、微信生态一屏看全，点一个就按这条线筛"),
        ("两个时间", "原文发布时间和系统同步时间分开记，消息新旧一眼分清"),
        ("命中标签", "每篇入库就打好自己的方向标签，回查和横向比都靠这一列"),
        ("标星 Pin", "人工挑出要细看的点一下，标星的 48 篇才进核验池"),
    ]
    # 截图上的编号标记，与右栏注解一一对应。
    # 位置避开界面文字：① 放在标签行末端空白，② 放在两个时间列之间，
    # ③ 放在命中标签列左侧，④ 放在「Pin ( 48 )」分组标题右侧。
    for i, (tx, ty) in enumerate([(7.38, 1.99), (5.40, 2.90), (3.45, 2.90), (1.45, 2.59)]):
        badge(s, tx, ty, i + 1, d=0.28, size=10)
    ex, ew = 8.62, 3.91
    for i, (t, d) in enumerate(marks):
        y = 1.66 + i * 0.94
        numdot(s, ex, y + 0.02, i + 1, fill=BLUE, color=WHITE, d=0.28, size=10)
        tb(s, ex + 0.40, y, ew - 0.40, 0.28, t, 13, True, INK)
        tb(s, ex + 0.40, y + 0.30, ew - 0.40, 0.56, d, 11.5, False, MUTED, spacing=1.25)
        if i < len(marks) - 1:
            hline(s, ex, y + 0.84, ew)

    bar = card(s, M, 5.52, 11.73, 1.22, fill=LIGHT, line=None)
    tb(s, 1.04, 5.64, 11.2, 0.30, "这一段是人工介入的地方：模型把值得看的挑出来，人只做挑和标星。",
       13, True, BLUE)
    tb(s, 1.04, 5.98, 11.2, 0.62,
       "筛的时候看两样：方向标签选条线，质量分看正文全不全、有没有数字。"
       "标星之后进入摘要与事实核查，没标星的留在库里随时回查——"
       "整个过程在同一页面完成，不用把正文复制到别的工具。",
       12, False, MUTED, spacing=1.3)
    demo_tag(s, 10.43, 1.22, "▶ 现场演示 10 秒", 2.10)
    footer(s)


# ---------- P04 ----------
def p04(prs):
    s = new_slide(prs)
    header(s, "04", "环节一 · 内容处理", "多路信源自动接入，本地清洗过滤后再进模型", 4)
    rail(s, 1)
    cols = [
        ("信源接入", ASSETS + "/p04-src.png", [
            "15 路活跃信源在线运行",
            "覆盖 821 个发布方，定时自动拉取更新",
            "信源异常或停更时，可在后台单独停用",
        ]),
        ("规则过滤", ASSETS + "/p04-rules.png", [
            "黑白名单按关键词过滤，先挡掉无关内容",
            "每个渠道可单独配置关键词，适配各自特点",
            "命中对应业务方向的文章才放行入库",
        ]),
        ("去噪清洗", ASSETS + "/p04-clean.png", [
            "本地规则自动剔除文末推广与网页乱码",
            "自动识别空正文与短稿，避免无效文本入池",
            "清洗规则全在本地运行，不占用模型算力",
        ]),
    ]
    cw = 2.72
    for i, (t, img, items) in enumerate(cols):
        x = BODY_X + i * (cw + 0.17)
        tb(s, x, 1.62, cw, 0.30, t, 15, True, BLUE)
        hline(s, x, 1.98, cw)
        box_h = 1.62
        from PIL import Image as _Im
        with _Im.open(img) as _im:
            iw, ih = _im.size
        box_w = cw
        scale = min(box_w / iw, box_h / ih)
        dw, dh = iw * scale, ih * scale
        pic(s, img, x + (box_w - dw) / 2, 2.08 + (box_h - dh) / 2, w=dw)
        shape(s, MSO_SHAPE.RECTANGLE, x, 2.08, cw, box_h, fill=None, line=LINE, lw=1.0)
        for j, it in enumerate(items):
            y = 3.88 + j * 0.66
            numdot(s, x, y + 0.03, j + 1, d=0.24, size=9)
            tb(s, x + 0.32, y - 0.02, cw - 0.32, 0.62, it, 12, False, INK, spacing=1.3)

    card(s, BODY_X, 5.92, BODY_W, 0.86, fill=LIGHT, line=None)
    tb(s, BODY_X + 0.22, 6.04, BODY_W - 0.44, 0.30, "这一层全部在本机跑，不调模型", 12.5, True, BLUE)
    tb(s, BODY_X + 0.22, 6.38, BODY_W - 0.44, 0.30,
       "接入、过滤与清洗都在本地完成，只把干净正文送进后面的打分和核查。",
       12, False, MUTED)
    footer(s)


# ---------- P05 ----------
def p05(prs):
    s = new_slide(prs)
    header(s, "05", "环节二 · 价值评估", "三把尺子分开算，各管一件事", 5)
    rail(s, 2)
    cards = [
        ("质量分", "0–5.0", "本地规则算，不调模型", [
            ("怎么算", "看正文多少字、数字几处、分段几段、有无发布方与发布时间，底分 2.5，四项全满 5.0"),
            ("前端怎么显示", "文章标星后列表上直接显示分值"),
            ("作用", "先把正文不全的挡掉"),
        ]),
        ("通用业务价值分", "0–100", "十维固定权重，模型打分", [
            ("怎么算", "模型先摘正文原句再逐维打分；引不到原句的一维封顶 3 分，总分由程序按权重算，审计不过重打"),
            ("前端怎么显示", "出简报时自动跑，展示总分与十维明细"),
            ("作用", "用同一把尺子把文章排高低"),
        ]),
        ("业务方向分", "0–100", "同一套权重，换一套问法", [
            ("怎么算", "先定方向与读者视角，模型反问澄清后把十问改写成该方向的问法，权重不动"),
            ("前端怎么显示", "按选定方向显示分值与排序"),
            ("作用", "同一篇文章在不同条线各算各的"),
        ]),
    ]
    cw = 2.70
    for i, (name, rng, sub, rows) in enumerate(cards):
        x = BODY_X + i * (cw + 0.20)
        card(s, x, 1.62, cw, 3.98)
        shape(s, MSO_SHAPE.RECTANGLE, x, 1.62, cw, 0.05, fill=BLUE)
        tb(s, x + 0.18, 1.76, cw - 0.36, 0.30, name, 13.5, True, INK)
        chip(s, x + cw - 1.10, 1.74, 0.92, 0.30, rng, 11.5, LIGHT, BLUE, BLUE)
        tb(s, x + 0.18, 2.12, cw - 0.36, 0.28, sub, 11.5, False, MUTED, exact=True)
        hline(s, x + 0.18, 2.46, cw - 0.36)
        y = 2.56
        for j, (k, v) in enumerate(rows):
            tb(s, x + 0.18, y, cw - 0.36, 0.24, k, 10.5, True, BLUE2, exact=True)
            vh = 0.92 if j == 0 else 0.56
            tb(s, x + 0.18, y + 0.24, cw - 0.36, vh, v, 11.5, False, INK, spacing=1.3, exact=True)
            y += 0.30 + vh + 0.12
            if j < len(rows) - 1:
                hline(s, x + 0.18, y - 0.08, cw - 0.36)

    card(s, BODY_X, 5.76, BODY_W, 0.96, fill=LIGHT, line=None)
    tb(s, BODY_X + 0.22, 5.88, BODY_W - 0.44, 0.30, "为什么要三把尺子", 12.5, True, BLUE)
    tb(s, BODY_X + 0.22, 6.22, BODY_W - 0.44, 0.34,
       "质量分只管文章写没写全，价值分管值不值得看，方向分管对我们这条线有没有用；三者不互相替代。",
       12, False, MUTED)
    footer(s)


# ---------- P06 ----------
def p06(prs):
    s = new_slide(prs)
    header(s, "06", "环节三 · 检验核验", "先给结论，再挂摘要，最后核到原文", 6)
    rail(s, 3)
    pic(s, ASSETS + "/p06-card.png", BODY_X, 1.62, w=3.62)
    shape(s, MSO_SHAPE.RECTANGLE, BODY_X, 1.62, 3.62, 3.37, fill=None, line=LINE, lw=1.0)
    for i, fy in enumerate([0.585, 0.755, 0.845]):
        badge(s, BODY_X + 3.30, 1.62 + 3.37 * fy - 0.14, i + 1, d=0.28, size=10)
    tb(s, BODY_X, 5.06, 3.80, 0.44, "简报里每条结论都带着它的分和出处，核查过的标绿。",
       10.5, False, MUTED, spacing=1.25, exact=True)

    tx = BODY_X + 3.90
    tw = BODY_W - 3.90
    items = [
        ("先筛后核", "自动筛分之后由人工标星，确认进入核验池"),
        ("一句总判断 + 五条摘要", "整篇先给一句总判断，下面挂五条摘要，条条附原文出处"),
        ("机构和数字对不上就重做", "系统先核机构名与关键数字，对不上自动打回模型重做"),
        ("仍不过标待核实", "重做仍对不上就标待核实，交人工最终把关，不混进结论"),
    ]
    for i, (t, d) in enumerate(items):
        y = 1.70 + i * 0.92
        numdot(s, tx, y + 0.06, i + 1, d=0.28, size=10)
        tb(s, tx + 0.40, y, tw - 0.40, 0.30, t, 13, True, INK)
        tb(s, tx + 0.40, y + 0.34, tw - 0.40, 0.52, d, 11.5, False, MUTED, spacing=1.25)
        if i < 3:
            hline(s, tx, y + 0.84, tw)

    card(s, BODY_X, 5.70, BODY_W, 1.10, fill=LIGHT, line=None)
    tb(s, BODY_X + 0.22, 5.82, BODY_W - 0.44, 0.30, "这一遍是为了后面的简报", 13, True, BLUE)
    tb(s, BODY_X + 0.22, 6.14, BODY_W - 0.44, 0.62,
       "简报要的是能直接拿去讲的结论：一句判断、五条要点、机构动作和数字都对得上原文。"
       "模型总结再顺也可能记错机构或数字，先核一遍，后面才敢直接用。",
       12, False, INK, spacing=1.3)
    footer(s)


# ---------- P07 ----------
def p07(prs):
    s = new_slide(prs)
    header(s, "07", "环节三 · 深度阅读", "某篇拿不准，就在这篇里问到底", 7)
    rail(s, 3, branch=True, branch_at=3)
    from PIL import Image as _Im
    p = ASSETS + "/p07-reader.png"
    with _Im.open(p) as im:
        iw, ih = im.size
    iw_ = 4.55
    ih_ = iw_ * ih / iw
    pic(s, p, BODY_X, 1.62, w=iw_)
    shape(s, MSO_SHAPE.RECTANGLE, BODY_X, 1.62, iw_, ih_, fill=None, line=LINE, lw=1.0)
    # 引用编号区（产品里点编号可跳回原文段落）
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 7.16, 3.62, 1.32, 0.62, fill=None,
          line=GOLD, lw=1.4, radius=0.03)
    tb(s, 6.30, 3.66, 0.82, 0.26, "引用编号", 10.5, True, GOLD_D, PP_ALIGN.RIGHT, exact=True)
    tb(s, BODY_X, 1.62 + ih_ + 0.06, iw_, 0.28,
       "阅读器实拍：左侧正文，右侧与这篇文章对话；回答末尾列出引用编号",
       10.5, False, MUTED, exact=True)

    tx = BODY_X + 4.86
    tw = BODY_W - 4.86
    feats = [
        ("只依据这一篇回答", "回答只基于原文，事实、推断、未知分开标，不会顺手补别的文章"),
        ("每条结论带编号", "答案末尾列出引用编号，点一下跳回原文对应段落"),
        ("问出来的结论跟着文章走", "结论存进文章档案，选进简报时按需一起带出去"),
    ]
    for i, (t, d) in enumerate(feats):
        y = 1.70 + i * 0.86
        numdot(s, tx, y + 0.04, i + 1, d=0.26, size=10)
        tb(s, tx + 0.36, y - 0.02, tw - 0.36, 0.30, t, 13, True, INK)
        tb(s, tx + 0.36, y + 0.30, tw - 0.36, 0.54, d, 12, False, MUTED, spacing=1.25)

    tb(s, tx, 4.36, tw, 0.28, "提问入口是产品里现成的五个", 13, True, BLUE)
    tb(s, tx, 4.68, tw, 0.30, "点一下就照这个方向问，不是为演示编的：", 11.5, False, MUTED, exact=True)
    qs = ["总结要点", "结构拆解", "事实清单", "待验证", "反方观点"]
    qcol = tw / 3
    qw = qcol - 0.09
    for i, q in enumerate(qs):
        chip(s, tx + (i % 3) * qcol, 5.06 + (i // 3) * 0.46, qw, 0.36, q, 11.5,
             LIGHT, BLUE, BLUE)

    card(s, tx, 6.02, tw, 0.78, fill=LIGHT, line=None)
    tb(s, tx + 0.20, 6.12, tw - 0.40, 0.56,
       "深度阅读是核验环节的分支：某篇对不上，就在这里问，问完再决定要不要进简报。",
       11.5, True, BLUE, spacing=1.3)
    demo_tag(s, 10.43, 1.22, "▶ 现场演示 12 秒", 2.10)
    footer(s)


# ---------- P08 ----------
def p08(prs):
    s = new_slide(prs)
    header(s, "08", "环节四 · 提取案例", "看完的文章，拆成能横向比的案例", 8)
    rail(s, 4)
    from PIL import Image as _Im
    p = ASSETS + "/p08-cases.png"
    with _Im.open(p) as im:
        iw, ih = im.size
    iw_ = BODY_W
    ih_ = iw_ * ih / iw
    pic(s, p, BODY_X, 1.62, w=iw_)
    shape(s, MSO_SHAPE.RECTANGLE, BODY_X, 1.62, iw_, ih_, fill=None, line=LINE, lw=1.0)
    tb(s, BODY_X, 1.62 + ih_ + 0.06, iw_, 0.30,
       "案例库实拍（2026-11-26，生产环境）：每条案例都带主体、动作、时间、对手方和原文出处",
       10.5, False, MUTED, exact=True)

    top = 1.62 + ih_ + 0.28
    lw_ = 4.16
    tb(s, BODY_X, top, lw_, 0.28, "一条案例拆成五个字段", 13, True, BLUE)
    fields = ["主体：哪家机构", "动作：做了什么", "时间与对手方", "证据原文", "唯一编号"]
    for i, f in enumerate(fields):
        y = top + 0.36 + (i // 3) * 0.36
        x = BODY_X + (i % 3) * 1.38
        shape(s, MSO_SHAPE.RECTANGLE, x, y + 0.06, 0.05, 0.20, fill=BLUE)
        tb(s, x + 0.18, y, 1.20, 0.28, f, 11.5, False, INK, exact=True)

    tx = BODY_X + 4.42
    tw = BODY_W - 4.42
    tb(s, tx, top, tw, 0.28, "这样存下来才有两个用处", 13, True, BLUE)
    uses = [
        ("横向比：同类动作摆到一张表里，谁先上看得出来"),
        ("带出处可追问：每条点一下就回到原文"),
        ("核过再进简报：已确认、待核实、已否决分开标"),
    ]
    for i, t in enumerate(uses):
        y = top + 0.36 + i * 0.32
        numdot(s, tx, y + 0.02, i + 1, fill=BLUE, color=WHITE, d=0.26, size=10)
        tb(s, tx + 0.36, y - 0.02, tw - 0.36, 0.28, t, 12, False, INK, exact=True)

    card(s, BODY_X, 6.46, BODY_W, 0.40, fill=LIGHT, line=None)
    tb(s, BODY_X + 0.20, 6.52, BODY_W - 0.40, 0.30,
       "432 条案例沉在库里，358 条已确认；随时按机构筛，也能直接分享给团队。",
       12, True, BLUE, exact=True)
    demo_tag(s, 10.43, 1.22, "▶ 现场演示 10 秒", 2.10)
    footer(s)


# ---------- P09 ----------
def p09(prs):
    s = new_slide(prs)
    header(s, "09", "环节五 · 分享简报", "同一批内容，出 8 种格式，按场合分发", 9)
    rail(s, 5)
    from PIL import Image as _Im
    p1 = ASSETS + "/p09-verified.png"
    with _Im.open(p1) as im:
        iw, ih = im.size
    iw_ = 5.27
    ih_ = iw_ * ih / iw
    pic(s, p1, BODY_X, 1.62, w=iw_)
    shape(s, MSO_SHAPE.RECTANGLE, BODY_X, 1.62, iw_, ih_, fill=None, line=LINE, lw=1.0)
    tb(s, BODY_X, 1.62 + ih_ + 0.06, iw_, 0.30,
       "已交付简报实拍（局部放大）：核过的结论标「已核实」，并给出可查的出处",
       10.5, False, MUTED, exact=True)

    p2 = ASSETS + "/p09-top.png"
    with _Im.open(p2) as im:
        iw2, ih2 = im.size
    iw2_ = 5.00
    ih2_ = iw2_ * ih2 / iw2
    y2 = 1.62 + ih_ + 0.42
    pic(s, p2, BODY_X, y2, w=iw2_)
    shape(s, MSO_SHAPE.RECTANGLE, BODY_X, y2, iw2_, ih2_, fill=None, line=LINE, lw=1.0)
    tb(s, BODY_X, y2 + ih2_ + 0.06, iw2_, 0.28,
       "简报开头先给「优先关注」：每条带业务价值分和一句话结论", 10.5, False, MUTED, exact=True)

    tx = BODY_X + 5.52
    tw = BODY_W - 5.52
    tb(s, tx, 1.62, tw, 0.30, "什么场合用什么格式", 13.5, True, BLUE)
    rows = [
        ("微信群里同步", "信息图 PNG"),
        ("日会周会投屏", "报告图 PNG"),
        ("当面演示", "单文件 HTML"),
        ("正式汇报", "PDF"),
        ("知识库存档", "Markdown"),
        ("贴进邮件正文", "纯文本 TXT"),
        ("知识库全文", "富文本 MD"),
        ("单个案例对外", "案例简报 HTML"),
    ]
    for i, (a, b) in enumerate(rows):
        y = 2.00 + i * 0.36
        tb(s, tx, y, 1.46, 0.28, a, 11.5, False, MUTED, exact=True)
        tb(s, tx + 1.50, y, tw - 1.50, 0.28, b, 11.5, True, INK, exact=True)
        hline(s, tx, y + 0.28, tw)

    card(s, tx, 4.96, tw, 0.74, fill=LIGHT, line=None)
    tb(s, tx + 0.18, 5.04, 1.26, 0.44, "105 份", 24, True, BLUE)
    tb(s, tx + 1.52, 5.14, tw - 1.70, 0.28, "已交付简报", 11.5, False, BLUE, exact=True)

    card(s, tx, 5.84, tw, 1.00, fill=WHITE, line=LINE)
    tb(s, tx + 0.18, 5.94, tw - 0.36, 0.86,
       "生成时自动注入 AI 摘要、事实核查与业务价值评分；早上跑完，日会直接投。",
       11.5, False, INK, spacing=1.3)
    footer(s)


# ---------- P10 脱敏（机制 + 演练台 + 可证伪 合并） ----------
def p10(prs):
    s = new_slide(prs)
    header(s, "10", "出站防护", "敏感信息不出内网，模型只见编码且当场可复算", 10)
    tb(s, M, 1.58, 9.00, 0.28,
       "moni 调外部模型的五条通道，都在本地发出前统一处理。已上线，总开关默认关。",
       12.5, False, MUTED)

    boxes = [
        ("原文", "正文 机构 结论"),
        ("出站遮蔽", "敏感项换编码"),
        ("本机对照表", "编码对应原值"),
        ("大模型", "只看得到编码"),
        ("本地还原", "回包换回原值"),
        ("用户", "看到的是明文"),
    ]
    bw = 1.90
    for i, (n, d) in enumerate(boxes):
        x = M + i * 1.98
        hot = i == 3
        sh = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, 1.96, bw, 0.62,
                   fill=BLUE if hot else WHITE, line=None, radius=0.05)
        fill_text(sh, n, 12, True, WHITE if hot else INK, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
        tb(s, x, 2.62, bw, 0.24, d, 10.5, False, GOLD_D if hot else MUTED, PP_ALIGN.CENTER, exact=True)
        if i < 5:
            shape(s, MSO_SHAPE.RIGHT_ARROW, x + bw + 0.02, 2.16, 0.14, 0.18,
                  fill=GOLD if i in (2, 3) else LINE)

    cols = [
        ("①", "原文", "招行与兴业同日发公告，客服电话 13800138000，邮箱 analyst@bankcorp.cn。", "库里的一篇真文章"),
        ("②", "发给模型的内容", "{{ORG_jcwwdnrn}} 与 {{ORG_lq4cr2iu}} 同日发公告，客服电话 {{PHONE_pbn2bfug}}。", "模型只看得到编码"),
        ("③", "模型回包（原始）", "{{ORG_jcwwdnrn}} 与 {{ORG_lq4cr2iu}} 同日发公告，客服电话 {{PHONE_pbn2bfug}}。", "回话里也只有编码"),
        ("④", "本地还原后", "招商银行 与 兴业银行 同日发公告，客服电话 13800138000，邮箱 analyst@bankcorp.cn。", "用户看到的还是明文"),
    ]
    for i, (n, t, body, say) in enumerate(cols):
        x = BODY_X + i * 2.13
        card(s, x, 3.06, 1.98, 2.06)
        badge(s, x + 0.12, 3.16, n, d=0.26, size=11)
        tb(s, x + 0.44, 3.14, 1.44, 0.28, t, 11.5, True, INK, exact=True)
        tb(s, x + 0.12, 3.50, 1.74, 1.10, body, 10, False, INK, spacing=1.2, exact=True)
        tb(s, x + 0.12, 4.66, 1.74, 0.30, say, 11, True, BLUE, exact=True)

    metrics = [("5", "遮蔽处数"), ("5", "还原处数"), ("0", "未还原"),
               ("0", "出站泄漏命中"), ("0", "残留占位符"), ("8775 ms", "模型耗时")]
    for i, (n, lab) in enumerate(metrics):
        x = BODY_X + i * 1.44
        card(s, x, 5.28, 1.34, 0.72, fill=LIGHT, line=None)
        tb(s, x, 5.32, 1.34, 0.36, n, 18 if i < 5 else 15, True, BLUE, PP_ALIGN.CENTER, exact=True)
        tb(s, x, 5.68, 1.34, 0.24, lab, 10.5, False, MUTED, PP_ALIGN.CENTER, exact=True)

    tb(s, BODY_X, 6.14, 4.40, 0.28, "生产 8900 实拍，同一页签内从开关到验证", 10.5, False, MUTED, exact=True)
    tb(s, BODY_X + 4.66, 6.14, 3.84, 0.28, "地址 127.0.0.1:8900/config?tab=desens", 10.5, False, GOLD_D, exact=True)
    tb(s, BODY_X, 6.46, 8.50, 0.34,
       "现场：点「模型实测」等回包，划过第二第三栏，再指第四栏和两个零。",
       12, True, BLUE, exact=True)
    demo_tag(s, 10.43, 1.22, "▶ 现场演示 25 秒", 2.10)
    footer(s)


# ---------- P11 成果与收益 ----------
def p11(prs):
    s = new_slide(prs)
    header(s, "11", "成果与收益", "同批 5 篇真实文章，机器把耗时压到十二分之一", 11)

    card(s, M, 1.62, 5.40, 3.86, line=INK, lw=1.25)
    shape(s, MSO_SHAPE.RECTANGLE, M, 1.62, 5.40, 0.05, fill=BLUE)
    tb(s, 1.04, 1.80, 2.60, 0.80, "12 倍", 44, True, BLUE, exact=True)
    tb(s, 1.04, 2.58, 4.90, 0.30, "同批 5 篇真实文章，人工 31.6 分钟，系统 2.5 分钟", 12.5, True, INK, exact=True)
    tb(s, 1.04, 2.90, 4.90, 0.28, "2026-08-10 实测，一格 = 2.5 分钟", 11.5, False, MUTED, exact=True)

    sq = 0.38
    gap = 0.05
    card(s, 0.98, 3.26, 5.20, 0.54, fill=LIGHT, line=None)
    for i in range(12):
        x = 1.04 + i * (sq + gap)
        shape(s, MSO_SHAPE.RECTANGLE, x, 3.34, sq, sq, fill=BLUE)
    tb(s, 1.04, 3.86, 5.00, 0.28, "人工读完并整理要点：31.6 分钟", 12, True, INK, exact=True)
    card(s, 0.98, 4.20, 5.20, 0.54, fill=LIGHT, line=None)
    shape(s, MSO_SHAPE.RECTANGLE, 1.04, 4.28, sq, sq, fill=BLUE)
    for i in range(1, 12):
        shape(s, MSO_SHAPE.RECTANGLE, 1.04 + i * (sq + gap), 4.28, sq, sq, fill=None, line=LINE, lw=1.0)
    tb(s, 1.04, 4.80, 5.00, 0.28, "系统跑同批打分与核验：2.5 分钟", 12, True, BLUE, exact=True)
    tb(s, 1.04, 5.14, 4.90, 0.28, "省下来的时间，用来读结论而不是抄正文。", 11.5, False, MUTED, exact=True)

    rx = 6.44
    rw = 12.53 - rx
    card(s, rx, 1.62, rw, 3.86)
    shape(s, MSO_SHAPE.RECTANGLE, rx, 1.62, rw, 0.05, fill=BLUE)
    tb(s, rx + 0.22, 1.80, rw - 0.44, 0.30, "数字口径", 13.5, True, BLUE)
    rows = [
        ("活跃信源", "15 路"),
        ("入库文章", "6,727 篇"),
        ("同业案例", "432 条（358 已确认）"),
        ("覆盖机构", "356 家"),
        ("高参考案例", "137 条"),
        ("已交付简报", "105 份"),
    ]
    for i, (k, v) in enumerate(rows):
        y = 2.20 + i * 0.52
        tb(s, rx + 0.22, y, 2.40, 0.30, k, 12, False, MUTED, exact=True)
        tb(s, rx + 2.70, y - 0.04, rw - 2.92, 0.34, v, 13, True, INK, PP_ALIGN.RIGHT, exact=True)
        hline(s, rx + 0.22, y + 0.36, rw - 0.44)

    tb(s, M, 5.66, 11.73, 0.28, "已交付的简报在盯这些方向", 12.5, True, BLUE)
    dirs = ["小微普惠", "企业微信", "数字人民币", "银行零售", "小程序", "信用卡", "智能客服", "视频号"]
    for i, d in enumerate(dirs):
        chip(s, M + i * 1.48, 6.02, 1.36, 0.38, d, 12, LIGHT, BLUE, BLUE)

    bar = card(s, M, 6.54, 11.73, 0.30, fill=LIGHT, line=None)
    tb(s, 1.04, 6.56, 11.2, 0.26, "十人每周约 20 人时的人工盯盘，换成每天一份可直接上会的简报。",
       12, True, BLUE, exact=True)
    footer(s)


# ---------- P12 可推广（原 P14） ----------
def p12(prs):
    s = new_slide(prs)
    header(s, "12", "不止同业", "同一套情报流水线，直接复用给行内其它团队", 12)
    tb(s, M, 1.58, 11.73, 0.30,
       "同一套采集、打分与案例沉淀机制，换掉信源和关键词，就能直接服务消保与合规团队。",
       12.5, False, MUTED)

    tb(s, M, 2.02, 5.10, 0.30, "换个场景先落地", 12, True, MUTED)
    tb(s, M, 2.34, 5.10, 0.94, "同业踩过的坑，提前发到全行桌面", 21, True, BLUE,
       spacing=1.25)
    plays = [
        ("自动抓监管通报与同业罚单",
         "订阅各地金融监管官号和行业媒体，按代销、催收、信息保护、收费等风险点归类"),
        ("历史踩雷案例沉淀成库",
         "每张罚单拆出机构、违规事由、处罚金额和原文出处，上线前可查重对照"),
        ("误报漏报人工直接校准",
         "合规人员在后台勾选误抓漏抓的词条，监测口径随检查重点同步微调"),
    ]
    for i, (t, d) in enumerate(plays):
        y = 3.44 + i * 0.98
        numdot(s, M, y + 0.04, i + 1, d=0.26, size=10)
        tb(s, M + 0.38, y - 0.02, 4.72, 0.32, t, 13.5, True, INK)
        tb(s, M + 0.38, y + 0.34, 4.72, 0.62, d, 12, False, MUTED, spacing=1.3)

    rx = 6.30
    rw = BODY_X + BODY_W - rx
    tb(s, rx, 2.02, rw, 0.30, "换成消保合规条线，要改什么", 12, True, MUTED)
    swaps = [
        ("换", "信源清单", "地方监管官号、同业处罚公告、行业媒体"),
        ("换", "关键词规则", "代销、催收、信息保护、收费等风险点"),
        ("不换", "工作台与案例字段", "机构、动作、时间、处罚依据、原文出处照旧"),
    ]
    for i, (tag, t, d) in enumerate(swaps):
        y = 2.38 + i * 1.24
        h = 1.20 if i == 2 else 1.06
        card(s, rx, y, rw, h, fill=LIGHT if i == 2 else WHITE,
             line=None if i == 2 else LINE)
        chip(s, rx + 0.20, y + 0.16, 0.76, 0.34, tag, 12, GOLD_D if i == 2 else BLUE, None,
             WHITE)
        tb(s, rx + 1.08, y + 0.14, rw - 1.28, 0.32, t, 14, True, INK)
        tb(s, rx + 1.08, y + 0.50, rw - 1.28, 0.46, d, 12, False, MUTED, spacing=1.25)

    bar = card(s, M, 6.28, 11.73, 0.50, fill=LIGHT, line=None)
    tb(s, 1.04, 6.36, 11.2, 0.32,
       "工作台和后台流水线无需重写，换一套信源清单和关键词规则就能在消保合规条线跑起来。",
       12.5, True, BLUE)
    footer(s)


# ---------- P13 人机协作 ----------
def p13(prs):
    s = new_slide(prs)
    header(s, "13", "人机协作", "机器守住质量下限，人的判断力决定采集方向", 13)
    tb(s, M, 1.56, 11.73, 0.30,
       "两个圈各转各的；机器交出核过的候选，人回一条判断，中间每天来回对一次。",
       12.5, False, MUTED)

    R = 0.95
    OFF = 1.18
    NR = 0.19
    CY = 3.72

    def ring(cx, title, sub, labels, main, border):
        # 参考环形图：外圈虚线、内圈实线、编号节点、环上前进箭头
        shape(s, MSO_SHAPE.OVAL, cx - R - 0.40, CY - R - 0.40, 2 * (R + 0.40), 2 * (R + 0.40),
              fill=None, line=LINE, lw=1.0, dash=MSO_LINE_DASH_STYLE.DASH)
        shape(s, MSO_SHAPE.OVAL, cx - R, CY - R, 2 * R, 2 * R, fill=None, line=border, lw=1.4)
        inner = shape(s, MSO_SHAPE.OVAL, cx - 0.72, CY - 0.72, 1.44, 1.44, fill=main, line=None)
        fill_text(inner, "", 12, True, WHITE, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
        tb(s, cx - 0.72, CY - 0.24, 1.44, 0.30, title, 15, True, WHITE, PP_ALIGN.CENTER, exact=True)
        tb(s, cx - 0.72, CY + 0.06, 1.44, 0.26, sub, 10.5, False, WHITE, PP_ALIGN.CENTER, exact=True)

        pts = [(cx, CY - OFF), (cx + OFF, CY), (cx, CY + OFF), (cx - OFF, CY)]
        for i, (nx, ny) in enumerate(pts):
            d = shape(s, MSO_SHAPE.OVAL, nx - NR, ny - NR, 2 * NR, 2 * NR,
                      fill=WHITE, line=border, lw=1.3)
            fill_text(d, str(i + 1), 11, True, border, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE,
                      exact=True)
        # 环上前进箭头
        for a in (-90, 0, 90, 180):
            ang = math.radians(a + 46)
            x1 = cx + R * math.cos(ang)
            y1 = CY + R * math.sin(ang)
            ang2 = math.radians(a + 82)
            x2 = cx + (R - 0.15) * math.cos(ang2)
            y2 = CY + (R - 0.15) * math.sin(ang2)
            arrow2(s, x1, y1, x2, y2, color=border, lw=1.0, dash=None)

        # 标签：上 / 右 / 下 / 左，各贴外侧，互不打架
        tb(s, cx - 0.98, CY - OFF - 0.56, 1.96, 0.28, labels[0], 12, True, INK,
           PP_ALIGN.CENTER, exact=True)
        tb(s, cx + OFF + 0.23, CY - 0.14, 1.00, 0.28, labels[1], 12, True, INK, exact=True)
        tb(s, cx - 0.98, CY + OFF + 0.20, 1.96, 0.28, labels[2], 12, True, INK,
           PP_ALIGN.CENTER, exact=True)
        tb(s, cx - OFF - 1.18, CY - 0.14, 0.98, 0.28, labels[3], 12, True, INK,
           PP_ALIGN.RIGHT, exact=True)

    MX, HX = 3.30, 10.05
    ring(MX, "机器这一圈", "自进化",
         ["改规则", "跑考卷", "才换线上", "留档可回退"], BLUE, BLUE)
    ring(HX, "人这一圈", "判断力",
         ["看简报", "做判断", "验效果", "补关键词"], BLUE2, BLUE2)

    tb(s, MX - 1.20, 5.52, 2.40, 0.28, "清洗 打分 核查 脱敏 定时任务", 11, False, MUTED,
       PP_ALIGN.CENTER, exact=True)
    tb(s, HX - 1.20, 5.52, 2.40, 0.28, "早上看简报 会上做判断 回头补口径", 11, False, MUTED,
       PP_ALIGN.CENTER, exact=True)

    # 两个环之间的双向交互
    cx0, cx1 = 5.78, 7.62
    tb(s, cx0, 2.82, cx1 - cx0, 0.28, "机器交出：核过的候选", 11, False, MUTED,
       PP_ALIGN.CENTER, exact=True)
    shape(s, MSO_SHAPE.RIGHT_ARROW, cx0, 3.10, cx1 - cx0, 0.22, fill=BLUE)
    tb(s, cx0, 3.42, cx1 - cx0, 0.30, "每天来回对一次", 12.5, True, BLUE,
       PP_ALIGN.CENTER, exact=True)
    shape(s, MSO_SHAPE.LEFT_ARROW, cx0, 3.78, cx1 - cx0, 0.22, fill=BLUE2)
    tb(s, cx0, 4.08, cx1 - cx0, 0.28, "人交回：哪些进了简报", 11, False, MUTED,
       PP_ALIGN.CENTER, exact=True)

    card(s, M, 6.10, 11.73, 0.68, fill=LIGHT, line=None)
    tb(s, 1.04, 6.20, 11.2, 0.48,
       "回到第一页的问题：十人每周约 20 小时的人工盯盘，现在由 105 份同业跟踪简报接着，"
       "判断留给业务的人。", 12.5, True, BLUE, spacing=1.3, exact=True)
    footer(s)


NOTES = [
    "我们做行内新媒体渠道建设，同业动作、监管口径和渠道打法都得跟。以前两条路：各自刷公众号拼周报，十人每周约二十小时；或者临时交办，大家熬夜凑材料。",
    "这套流程分五步：采集过滤、打分、核查、存案例、出简报。走完一轮回头看：信源抓偏了改标签，方向问错了改问法，核查不过重做一遍。底下这一层是 moni 工作台，六个模块各司其职。",
    "模型先按方向和评分筛一遍，人只在列表里挑重点标星。标星的四十八篇才会进后面的核验池；没标星的留在库里，随时可以按方向回查。",
    "内容处理三步：十五路信源定时抓；每个渠道按自己的关键词过滤；本地去掉推广尾巴和乱码。这一层不调模型，也不占用模型算力。",
    "三个分：质量分本地算，零到五分；通用价值分十维加权，模型先引原文再打分，总分由程序算；方向分换问法不换权重。三把尺子分开用，谁也不替谁。",
    "标星的文章进核验池：先给一句总判断，再挂五条带出处的摘要；机构和数字对不上就打回重做，仍不过就标待核实，交人工最终把关。",
    "某篇拿不准就在阅读器里问：答案只依据这一篇，事实、推断、未知分开标，每条结论带编号，点一下跳回原文对应的段落。",
    "看完的文章拆成案例：主体、动作、时间、对手方、原文出处，一条一个编号。按机构筛一下，同类动作横着比，核过的才算数。",
    "同一批内容出八种格式：群里信息图、会里报告图、正式汇报 PDF。核查过的标已核实，要核的标待核实；早上跑完，日会直接投。",
    "敏感信息不出内网：发出前本地遮蔽，模型只见编码，回包再还原，四栏当场能看。账本记三项指标，出站泄漏、残留、未还原都是零。",
    "数字：十五路信源、六千七百二十七篇入库、四百三十二条案例、三百五十六家机构、已交付一百零五份简报。同批五篇文章，人工三十一点六分钟，系统两分半。",
    "这套东西不挑题材：换信源和关键词，就能给消保合规团队盯监管通报和同业罚单；工作台和案例字段照旧，后台不用重写。",
    "机器守质量下限，人定采集方向，两个圈各转各的，中间每天来回对一次。回到最初的问题：十人每周二十小时的盯盘，现在有一百零五份同业跟踪简报接着，判断留给业务的人。",
]


def main():
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    for fn in (p01, p02, p03, p04, p05, p06, p07, p08, p09,
               p10, p11, p12, p13):
        fn(prs)
    for i, s in enumerate(prs.slides):
        s.notes_slide.notes_text_frame.text = NOTES[i]
    prs.save(OUT)

    bad = []
    for i, s in enumerate(prs.slides, 1):
        for sh in s.shapes:
            if sh.left is None:
                continue
            r = (sh.left + sh.width) / 914400.0
            b = (sh.top + sh.height) / 914400.0
            l = sh.left / 914400.0
            t = sh.top / 914400.0
            if l < -0.01 or t < -0.01 or r > 13.35 or b > 7.52:
                bad.append("slide%d %s L%.2f T%.2f R%.2f B%.2f" % (i, sh.shape_type, l, t, r, b))
    print("slides:", len(prs.slides._sldIdLst))
    print("out of bounds:", len(bad))
    for x in bad:
        print("  ", x)


main()
