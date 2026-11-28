# -*- coding: utf-8 -*-
"""moni 决赛汇报 PPT v26 构建脚本（14 页）

v26 相对 v25 的改动：
1. P05「价值评估」整页重做：文案正式化（去除口语），并补上业务价值分的判定链路
   （取证 → 十维打分 → 校验 → 独立审计 → 程序算总分）与三条硬规则。
2. 其余 13 页与 v25 逐字一致。
"""
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
OUT = BASE + "/决赛汇报-v27.pptx"

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
RAILBG = RGBColor(0xEF, 0xF3, 0xF8)
RAILLINE = RGBColor(0xE3, 0xEA, 0xF2)
SOFT = RGBColor(0xEF, 0xF4, 0xFA)
INK = RGBColor(0x12, 0x20, 0x2F)
MUTED = RGBColor(0x5C, 0x6B, 0x7F)

TOTAL = 11
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
    if kicker:
        tb(s, M, 0.46, 7.4, 0.30, "%s  %s" % (num, kicker), 12.5, True, MUTED)
    else:
        tb(s, M, 0.46, 1.60, 0.30, num, 12.5, True, MUTED)
    tb(s, M, 0.74, 10.80, 0.60, title, 28, True, BLUE)
    shape(s, MSO_SHAPE.RECTANGLE, M, 1.40, 0.86, 0.045, fill=BLUE)
    tb(s, 10.90, 0.49, 1.63, 0.30, "%02d/%d" % (pageno, TOTAL), 11.5, True, MUTED,
       PP_ALIGN.RIGHT, exact=True)


def footer(s, text="新媒体渠道同业跟踪调研 AI 工作台"):
    hline(s, M, 6.94, 11.73)
    tb(s, M, 7.04, 9.0, 0.28, text, 10.5, False, MUTED, exact=True)


def demo_tag(s, x, y, text, w=2.10):
    sh = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, 0.34, fill=GOLDL, line=GOLD, lw=1.0, radius=0.05)
    fill_text(sh, text, 11.5, True, GOLD, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)


STEPS = ["内容处理", "价值评估", "事实核查", "提取案例", "分享简报"]
SUBS = ["15 路信源，本机过滤", "0–100，低分归档", "结论带出处，逐条核对原文",
        "五字段，带编号", "8 种格式，按场景分享"]


def rail(s, active, branch=False, branch_at=3):
    x, top = RAIL_X, 2.00
    gap = 0.88
    if isinstance(active, int):
        active = (active,)
    tag = str(active[0]) if len(active) == 1 else "%d-%d" % (active[0], active[-1])
    label = "流程 · %s/5" % tag
    if branch:
        label = "流程 · %s 分支 5/5" % tag
    # v22：左轨浅底面板，把「导航」和右侧正文分开
    # v23：面板顶边对齐正文顶部 1.62，节点纵向收紧到 0.88 步距
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x - 0.20, 1.62, RAIL_TW + 0.40, 5.02,
          fill=RAILBG, line=RAILLINE, lw=0.75, radius=0.10)
    tb(s, x, 1.70, RAIL_TW + 0.08, 0.22, label, 9, True, MUTED, exact=True)

    ys = [top + i * gap for i in range(6)]

    cx = x + 0.11
    shape(s, MSO_SHAPE.RECTANGLE, cx - 0.006, 2.04, 0.012,
          ys[4] + 0.20 - 2.04, fill=LINE)

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
            # v23：分支胶囊落在节点下方、下一节点上方，不再横向压住节点文字
            shape(s, MSO_SHAPE.RECTANGLE, x + 0.11, yy + 0.50, 0.012, 0.10,
                  fill=GOLD)
            bx = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x + 0.34, yy + 0.52,
                       RAIL_TW - 0.34, 0.26, fill=GOLDL, line=GOLD, radius=0.03)
            fill_text(bx, "↓ 深度阅读 · 分支", 9.5, True, GOLD, PP_ALIGN.CENTER,
                      MSO_ANCHOR.MIDDLE, exact=True)




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


def arrow2(s, x1, y1, x2, y2, color=BLUE2, lw=1.1, dash="dash"):
    return arrow(s, x1, y1, x2, y2, color=color, lw=lw, dash=dash)


# ---------- P01 背景 ----------
def p01(prs):
    s = new_slide(prs)
    header(s, "01", "", "背景：新渠道同业动向、监管政策、行业趋势需要定期跟踪", 1)
    top = [
        ("我们的职责", "行内新媒体渠道的建设与运营"),
        ("要盯什么", "同业技术应用、监管政策、行业实践"),
        ("为什么必须盯", "渠道玩法高频迭代，慢一步就跟不上同业"),
    ]
    x = M
    for i, (lab, val) in enumerate(top):
        w = 3.84
        card(s, x, 1.60, w, 1.40)
        tb(s, x + 0.20, 1.74, w - 0.40, 0.28, lab, 12, True, MUTED)
        tb(s, x + 0.20, 2.06, 3.44, 0.84, val, 14, True, INK, spacing=1.3)
        x += w + 0.105
    hline(s, M, 3.16, 11.73)

    lanes = [
        ("路径 A", "日常跟踪同业", [
            "各自订阅几十个公众号",
            "凭经验挑出有用的",
            "手工摘录机构和数字",
            "拼成周报发到群里",
            "发完群文件就没人再翻",
        ]),
        ("路径 B", "临时交办任务", [
            "领导或分行丢来同业截图",
            "临时分头全网找同业做法",
            "熬夜拼一份应急对照",
            "讲完就各自散了",
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
        for i, d in enumerate(steps):
            y = 4.20 + i * step
            numdot(s, x + 0.20, y + 0.03, i + 1, d=0.24, size=9)
            tb(s, x + 0.56, y - 0.01, w - 0.76, 0.30, d, 12.5, False, INK,
               exact=True)

    x = 8.50
    w = 4.03
    card(s, x, 3.34, w, 3.36, fill=LIGHT, line=None)
    tb(s, x + 0.22, 3.46, w - 0.44, 0.30, "存在问题", 14, True, BLUE)
    pains = [
        ("等派任务", "有任务交办才动手，缺乏常态化跟踪机制"),
        ("每周约20人时", "十人团队各花两小时，时间耗在信息筛选和比对上"),
        ("口径容易打架", "人工摘录凭经验，同一个案例前后记不一致"),
        ("同业案例无沉淀", "群聊分享文件和文档材料没有归档，下次遇到还得从头找"),
    ]
    for i, (t, d) in enumerate(pains):
        y = 3.94 + i * 0.74
        tb(s, x + 0.22, y - 0.02, w - 0.44, 0.26, t, 13, True, INK)
        tb(s, x + 0.22, y + 0.26, w - 0.44, 0.44, d, 11, False, MUTED,
           exact=True)
    footer(s)


# ---------- P02 需求：五步 + 回流 + 工作台底座 ----------
def p02(prs):
    s = new_slide(prs)
    header(s, "02", "", "需求：一个能支撑从信息获取到简报分享闭环的工作台", 2)
    nodes = [
        ("内容处理", "15 路信源，本机过滤"),
        ("价值评估", "0–100，保留高价值原始信息"),
        ("事实核查", "结论带出处，逐条核对原文"),
        ("提取案例", "五字段，带编号"),
        ("分享简报", "8 种格式，按场景分享"),
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
    band_y = 3.30
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 1.10, band_y, 6.10, 0.40, fill=WHITE,
          line=LINE, lw=1.0, radius=0.04)
    tb(s, 1.26, band_y + 0.06, 5.80, 0.28, "复盘同业追踪简报质量", 12, True, MUTED,
       PP_ALIGN.CENTER, exact=True)
    backs = [
        (0, "改信源与标签"),
        (1, "改标准与权重"),
        (2, "调整核查目标"),
    ]
    for idx, label in backs:
        cx = centers[idx]
        arrow(s, cx, band_y - 0.04, cx, top + 1.22, color=BLUE2, lw=1.1)
        tb(s, cx - 0.95, band_y - 0.44, 1.90, 0.28, label, 11, True, BLUE2, PP_ALIGN.CENTER, exact=True)
    arrow(s, centers[-1], top + 1.22, centers[-1], band_y + 0.60, color=BLUE2, lw=1.1)
    shape(s, MSO_SHAPE.RECTANGLE, 7.20, band_y + 0.594, centers[-1] - 7.20, 0.012, fill=BLUE2)
    arrow(s, 7.20, band_y + 0.60, 7.20, band_y + 0.42, color=BLUE2, lw=1.1)
    tb(s, 7.30, band_y + 0.66, 4.60, 0.28, "定时简报分享，团队提供反馈", 11, False, MUTED, exact=True)

    plat_y = 4.16
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, M, plat_y, 11.73, 1.36, fill=BLUE, line=None, radius=0.06)
    tb(s, M + 0.30, plat_y + 0.14, 9.60, 0.30, "新媒体同业跟踪工作台 · 采集-筛选-摘要-核查-简报", 13, True, WHITE, exact=True)
    mods = [
        ("采集", "15 路信源定时入库"),
        ("过滤", "渠道关键词 + 去噪"),
        ("打分", "质量分 + 价值分"),
        ("核查", "摘要 + 事实核查"),
        ("存案例", "主体 动作 时间 原文"),
        ("出简报", "8 种格式投场景"),
    ]
    mw, mg = 1.83, 0.10
    for i, (t, d) in enumerate(mods):
        x = 1.00 + i * (mw + mg)
        sh = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, plat_y + 0.54, mw, 0.66, fill=WHITE,
                   line=None, radius=0.04)
        tb(s, x, plat_y + 0.60, mw, 0.26, t, 12, True, BLUE, PP_ALIGN.CENTER, exact=True)
        tb(s, x + 0.08, plat_y + 0.88, mw - 0.16, 0.26, d, 10.5, False, MUTED, PP_ALIGN.CENTER, exact=True)
    footer(s)


# ---------- P03 内容预处理 ----------
def p03(prs):
    s = new_slide(prs)
    header(s, "03", "环节一 · 内容处理", "内容预处理：信源自动入库，提前过滤", 3)
    rail(s, 1)
    cols = [
        ("信源接入", ASSETS + "/p04-src.png", [
            "15 类活跃信源",
            "覆盖 821 个发布方，定时自动更新",
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
            "清洗规则全在本地运行，减少非必要模型开销",
        ]),
    ]
    cw = 2.72
    from PIL import Image as _Im
    for i, (t, img, items) in enumerate(cols):
        x = BODY_X + i * (cw + 0.17)
        tb(s, x, 1.62, cw, 0.30, t, 15, True, BLUE)
        hline(s, x, 1.98, cw)
        box_h = 1.62
        with _Im.open(img) as _im:
            iw, ih = _im.size
        scale = min(cw / iw, box_h / ih)
        dw, dh = iw * scale, ih * scale
        pic(s, img, x + (cw - dw) / 2, 2.08 + (box_h - dh) / 2, w=dw)
        shape(s, MSO_SHAPE.RECTANGLE, x, 2.08, cw, box_h, fill=None, line=LINE, lw=1.0)
        for j, it in enumerate(items):
            y = 3.88 + j * 0.66
            numdot(s, x, y + 0.03, j + 1, d=0.24, size=9)
            tb(s, x + 0.32, y - 0.02, cw - 0.32, 0.62, it, 12, False, INK, spacing=1.3)

    card(s, BODY_X, 5.92, BODY_W, 0.86, fill=LIGHT, line=None)
    tb(s, BODY_X + 0.22, 6.04, BODY_W - 0.44, 0.30, "内容处理本地自动化处理", 12.5, True, BLUE)
    tb(s, BODY_X + 0.22, 6.38, BODY_W - 0.44, 0.30,
       "接入、过滤与清洗都在本地完成，只把正文送进后面的打分和核查。",
       12, False, MUTED)
    footer(s)


# ---------- P04 价值评估 ----------
def p04(prs):
    s = new_slide(prs)
    header(s, "04", "环节二 · 价值评估", "排序提炼：先打分排优先级，再提炼和核实", 4)
    rail(s, 2)
    cards = [
        ("质量分", "0–5.0", "本地规则算，不调模型",
         "看正文多少字、数字几处、分段几段、有无发布方与发布时间等",
         "先把残缺、异常内容剔除，减少大模型不必要开销"),
        ("通用业务价值分", "0–100", "十维固定权重，模型打分",
         "先摘原文依据，再按十项业务标尺打分；引原句最高 3 分，程序算总分，复核不过重做",
         "按通用规则对文章排序，识别高价值信息"),
        ("业务方向分", "0–100", "同一套权重，换一套问法",
         "自定的业务方向与读者视角，模型多次澄清后给出特定业务价值评分",
         "按不同业务视角评估同一篇信息的参考价值"),
    ]
    cw = 2.70
    for i, (name, rng, kj, desc, use) in enumerate(cards):
        x = BODY_X + i * (cw + 0.20)
        card(s, x, 1.62, cw, 2.70)
        shape(s, MSO_SHAPE.RECTANGLE, x, 1.62, cw, 0.05, fill=BLUE)
        tb(s, x + 0.18, 1.76, cw - 1.34, 0.30, name, 13.5, True, INK)
        chip(s, x + cw - 1.10, 1.74, 0.92, 0.30, rng, 11.5, LIGHT, BLUE, BLUE)
        tb(s, x + 0.18, 2.14, cw - 0.36, 0.26, kj, 11.5, True, BLUE2, exact=True)
        tb(s, x + 0.18, 2.46, cw - 0.36, 0.80, desc, 11.5, False, INK, spacing=1.32, exact=True)
        hline(s, x + 0.18, 3.34, cw - 0.36)
        tb(s, x + 0.18, 3.42, cw - 0.36, 0.24, "作用", 10.5, True, BLUE2, exact=True)
        tb(s, x + 0.18, 3.68, cw - 0.36, 0.52, use, 11.5, False, INK, spacing=1.32, exact=True)

    card(s, BODY_X, 4.46, BODY_W, 2.24, fill=LIGHT, line=None)
    tb(s, BODY_X + 0.22, 4.62, BODY_W - 0.44, 0.30, "为什么要三套分数", 13, True, BLUE, exact=True)
    tb(s, BODY_X + 0.22, 5.00, BODY_W - 0.44, 0.90,
       "质量分负责剔除残缺、异常内容，价值分负责把控内容质量，"
       "方向分评价对“所在业务条线”有没有用。",
       12.5, False, INK, spacing=1.4)
    footer(s)


# ---------- P05 事实核查 ----------
def p05(prs):
    s = new_slide(prs)
    header(s, "05", "环节三 · 事实核查", "借助AI提取摘要、联网事实核查", 5)
    rail(s, 3)
    from PIL import Image as _Im
    p = ASSETS + "/p06-card.png"
    with _Im.open(p) as im:
        iw, ih = im.size
    iw_ = 4.00
    ih_ = iw_ * ih / iw
    pic(s, p, BODY_X, 1.62, w=iw_)
    shape(s, MSO_SHAPE.RECTANGLE, BODY_X, 1.62, iw_, ih_, fill=None, line=LINE, lw=1.0)
    tb(s, BODY_X, 1.62 + ih_ + 0.06, iw_, 0.28,
       "简报卡实拍：一句话判断 + 摘要 + 逐条事实核查",
       10.5, False, MUTED, exact=True)

    tx = BODY_X + iw_ + 0.30
    tw = BODY_W - iw_ - 0.30
    items = [
        ("分数标注", "标注价值评分，识别高相关度信息"),
        ("一句话总结 + 五条摘要", "整篇先给一句总判断，下面挂多条摘要，附原文出处"),
        ("核对文中出现的机构名称、数字等信息", "系统先核对机构名与关键数字，支持多次打回模型重审"),
        ("标注待核实信息", "大模型进行事实核查无结果的段落，单独列出，交给人工最终把关"),
    ]
    for i, (t, d) in enumerate(items):
        y = 1.70 + i * 0.98
        numdot(s, tx, y + 0.06, i + 1, d=0.28, size=10)
        tb(s, tx + 0.40, y, tw - 0.40, 0.30, t, 13, True, INK)
        tb(s, tx + 0.40, y + 0.34, tw - 0.40, 0.55, d, 12, False, MUTED, spacing=1.28)
        if i < 3:
            hline(s, tx, y + 0.88, tw)
    footer(s)


# ---------- P06 深度阅读（分支） ----------
def p06(prs):
    s = new_slide(prs)
    header(s, "06", "环节三 · 深度阅读", "深度阅读：对一篇内容原文详细追问", 6)
    rail(s, 3, branch=True, branch_at=3)
    from PIL import Image as _Im
    p = ASSETS + "/p07-reader.png"
    with _Im.open(p) as im:
        iw, ih = im.size
    iw_ = 4.55
    ih_ = iw_ * ih / iw
    pic(s, p, BODY_X, 1.62, w=iw_)
    shape(s, MSO_SHAPE.RECTANGLE, BODY_X, 1.62, iw_, ih_, fill=None, line=LINE, lw=1.0)
    tb(s, BODY_X, 1.62 + ih_ + 0.06, iw_, 0.28,
       "阅读器实拍：左侧正文，右侧与这篇文章对话；回答末尾列出引用编号",
       10.5, False, MUTED, exact=True)

    tx = BODY_X + iw_ + 0.31
    tw = BODY_W - iw_ - 0.31
    feats = [
        ("只依据这一篇回答", "回答只基于原文，事实、推断、未知分开标，不会顺手补别的文章"),
        ("每条结论带编号", "答案末尾列出引用编号，点一下跳回原文对应段落"),
        ("问出来的结论跟着文章走", "结论存进文章档案，选进简报时按需一起带出去"),
    ]
    for i, (t, d) in enumerate(feats):
        y = 1.70 + i * 0.88
        numdot(s, tx, y + 0.04, i + 1, d=0.26, size=10)
        tb(s, tx + 0.36, y - 0.02, tw - 0.36, 0.30, t, 13, True, INK)
        tb(s, tx + 0.36, y + 0.30, tw - 0.36, 0.54, d, 12, False, MUTED, spacing=1.25)

    tb(s, tx, 4.44, tw, 0.28, "预设提示词，支持自定义保存提示词模板", 13, True, BLUE)
    qs = ["总结要点", "结构拆解", "事实清单", "待验证", "反方观点"]
    qcol = tw / 3
    qw = qcol - 0.09
    for i, q in enumerate(qs):
        chip(s, tx + (i % 3) * qcol, 4.84 + (i // 3) * 0.46, qw, 0.36, q, 11.5,
             LIGHT, BLUE, BLUE)
    card(s, tx, 5.86, tw, 0.88, fill=LIGHT, line=None)
    tb(s, tx + 0.20, 5.98, tw - 0.40, 0.64,
       "深度阅读是核验环节的分支：某篇对不上，就在这里问，问完再决定要不要进简报。",
       11.5, True, BLUE, spacing=1.3)
    demo_tag(s, 10.43, 1.22, "▶ 现场演示 12 秒", 2.10)
    footer(s)


# ---------- P07 提取案例 ----------
def p07(prs):
    s = new_slide(prs)
    header(s, "07", "环节四 · 提取案例", "看完的文章，拆成能横向比的案例", 7)
    rail(s, 4)
    from PIL import Image as _Im

    p1 = ASSETS + "/p08-first.png"
    with _Im.open(p1) as im:
        iw, ih = im.size
    iw_ = 5.20
    ih_ = iw_ * ih / iw
    pic(s, p1, BODY_X, 1.62, w=iw_)
    shape(s, MSO_SHAPE.RECTANGLE, BODY_X, 1.62, iw_, ih_, fill=None, line=LINE, lw=1.0)
    tb(s, BODY_X, 1.62 + ih_ + 0.08, iw_, 0.30, "案例库样式：统计 + 搜索筛选 + 卡片网格",
       10.5, False, MUTED, exact=True)

    fy0 = 1.62 + ih_ + 0.41
    tb(s, BODY_X, fy0, iw_, 0.28, "一条案例提取六类信息", 11, True, BLUE, exact=True)
    fields = ["主体", "动作", "时间", "对手方", "原文出处", "核验状态"]
    for i, f in enumerate(fields):
        fx = BODY_X + (i % 3) * 1.79
        fy = fy0 + 0.32 + (i // 3) * 0.34
        fd = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, fx, fy, 1.62, 0.30,
                   fill=LIGHT, line=None, radius=0.10)
        fill_text(fd, f, 10, False, BLUE2, PP_ALIGN.CENTER,
                  MSO_ANCHOR.MIDDLE, exact=True)

    RX = BODY_X + 5.62
    RW = BODY_W - 5.62
    p2 = ASSETS + "/p08-card3.png"
    with _Im.open(p2) as im:
        cw, ch = im.size
    cw_ = 2.60
    ch_ = cw_ * ch / cw
    card(s, RX, 1.62, cw_ + 0.24, ch_ + 0.18, fill=WHITE, line=LINE, lw=0.75,
         radius=0.06)
    pic(s, p2, RX + 0.12, 1.70, w=cw_)
    shape(s, MSO_SHAPE.RECTANGLE, RX + 0.12, 1.70, cw_, ch_, fill=None, line=LINE,
          lw=0.75)
    tb(s, RX + 0.12, 1.70 + ch_ + 0.06, cw_, 0.40, "卡片样式：标题 · 评价 · 核验状态",
       10, False, MUTED, exact=True)

    y0 = 1.70 + ch_ + 0.54
    tb(s, RX + 0.04, y0, RW - 0.08, 0.26, "案例库使用场景", 12.5, True, BLUE)
    uses = [
        ("知识库管理",
         "按机构、场景和状态搜索筛选，随时回查历史案例，不再沉在群文件里。"),
        ("团队辅助决策",
         "核过的案例库带原文出处与关键数据，可直接分享给团队，或一键进晨报上日会。"),
    ]
    for i, (t, d) in enumerate(uses):
        y = y0 + 0.30 + i * 0.82
        card(s, RX + 0.04, y, RW - 0.10, 0.76, fill=LIGHT, line=None, radius=0.06)
        badge(s, RX + 0.20, y + 0.10, i + 1, d=0.26, fill=BLUE, color=WHITE,
              size=10)
        tb(s, RX + 0.56, y + 0.06, RW - 0.72, 0.24, t, 11, True, INK, exact=True)
        tb(s, RX + 0.22, y + 0.30, RW - 0.38, 0.40, d, 10, False, MUTED,
           spacing=1.15, exact=True)
    footer(s)


# ---------- P08 分享简报 ----------
def p08(prs):
    s = new_slide(prs)
    header(s, "08", "简报分享", "多格式分享，灵活支持不同场景", 8)
    rail(s, 5)

    CX, CW = BODY_X, 5.55
    card(s, CX, 1.62, CW, 5.28, fill=WHITE, line=LINE, lw=0.75, radius=0.06)
    shape(s, MSO_SHAPE.RECTANGLE, CX, 1.62, CW, 0.05, fill=BLUE)
    tb(s, CX + 0.24, 1.78, CW - 0.48, 0.30, "8 种格式，按场景分享", 13.5, True, BLUE)

    fmt = [
        ("信息图 PNG", "群发", "高价值视觉长图，群里直发"),
        ("报告图 PNG", "投屏", "一图概览，日会周会投屏"),
        ("单文件 HTML", "演示", "单页可交互，当面演示"),
        ("PDF", "呈送", "A4 标准版式，正式汇报"),
        ("Markdown", "归档", "纯文本规范，知识库导入"),
        ("纯文本 TXT", "邮件", "字符紧凑，贴进邮件正文"),
        ("富文本 MD", "交流", "带格式高亮，即时交流"),
        ("案例简报 HTML", "对外", "单条案例，可直接对外"),
    ]
    iw_, ih_ = 2.47, 1.05
    for i, (name, tag, desc) in enumerate(fmt):
        col = i // 4
        row = i % 4
        x = CX + 0.24 + col * (iw_ + 0.14)
        y = 2.20 + row * (ih_ + 0.08)
        bd = shape(s, MSO_SHAPE.OVAL, x, y + 0.04, 0.28, 0.28,
                   fill=RGBColor(0x91, 0x42, 0x1F))
        fill_text(bd, str(i + 1), 9.5, True, WHITE, PP_ALIGN.CENTER,
                  MSO_ANCHOR.MIDDLE, exact=True)
        tb(s, x + 0.40, y + 0.04, 1.90, 0.26, name, 11.5, True, INK, exact=True)
        tg = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x + 0.40, y + 0.32, 0.62, 0.24,
                   fill=RGBColor(0xED, 0xF0, 0xF4), line=None, radius=0.5)
        fill_text(tg, tag, 9.5, False, MUTED, PP_ALIGN.CENTER,
                  MSO_ANCHOR.MIDDLE, exact=True)
        tb(s, x + 0.02, y + 0.60, iw_ - 0.06, 0.46, desc, 10, False, MUTED,
           spacing=1.1, exact=True)

    MX, MW = 9.80, 2.60
    card(s, MX, 1.62, MW, 5.28, fill=WHITE, line=LINE, lw=0.75, radius=0.08)
    from PIL import Image as _Im
    p = "/private/tmp/user-v27/brief-top.png"
    with _Im.open(p) as im:
        mw, mh = im.size
    pw = 2.30
    ph = pw * mh / mw
    px = MX + (MW - pw) / 2
    pic(s, p, px, 1.78, w=pw)
    shape(s, MSO_SHAPE.RECTANGLE, px, 1.78, pw, ph, fill=None, line=LINE, lw=0.75)
    tb(s, MX + 0.12, 1.78 + ph + 0.06, MW - 0.24, 0.28, "行业追踪报告 HTML 成品实拍",
       10, False, MUTED, exact=True)
    tb(s, MX + 0.12, 1.78 + ph + 0.32, MW - 0.24, 0.26, "宽版 · 可交互 · 来自 moni",
       10, False, GOLD_D, exact=True)
    footer(s)


# ---------- P09 隐私保障 ----------
def p09(prs):
    s = new_slide(prs)
    header(s, "09", "隐私保障", "敏感信息先脱敏，再交给大模型", 9)

    GRAY_EDGE = RGBColor(0xC6, 0xD3, 0xE3)
    SOFTCARD = RGBColor(0xF7, 0xF9, 0xFC)

    PY = 1.72
    lcx, lcy = 1.30, 2.90
    circ = shape(s, MSO_SHAPE.OVAL, lcx - 0.43, lcy - 0.43, 0.86, 0.86,
                 fill=WHITE, line=INK, lw=1.2)
    fill_text(circ, "原", 15, True, INK, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE,
              exact=True)
    tb(s, lcx - 0.70, lcy + 0.50, 1.40, 0.28, "原文", 13, True, INK,
       PP_ALIGN.CENTER, exact=True)
    tb(s, lcx - 0.70, lcy + 0.78, 1.40, 0.24, "库里的文章", 9.5, False, MUTED,
       PP_ALIGN.CENTER, exact=True)
    arrow2(s, 1.86, 2.90, 2.20, 2.90, color=INK, lw=1.0, dash=None)

    PX, PW = 2.20, 4.10
    card(s, PX, PY, PW, 2.36, fill=WHITE, line=LINE, lw=1.0, radius=0.06)
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, PX, PY, PW, 0.42, fill=BLUE, radius=0.06)
    shape(s, MSO_SHAPE.RECTANGLE, PX, PY + 0.28, PW, 0.14, fill=BLUE)
    tb(s, PX + 0.18, PY + 0.08, 1.60, 0.26, "A 脱敏信息", 13, True, WHITE, exact=True)
    tb(s, PX + PW - 2.06, PY + 0.10, 1.88, 0.24, "本机执行 · 出网前完成",
       9.5, False, WHITE, PP_ALIGN.RIGHT, exact=True)
    fields = [
        ("机构专名", "{{ORG_jcwwdnrn}}"),
        ("联系方式", "{{PHONE_pbn2bfug}}"),
        ("内部判断", "{{JUDGE_xxx}}"),
    ]
    for i, (k, v) in enumerate(fields):
        y = PY + 0.54 + i * 0.52
        card(s, PX + 0.16, y, PW - 0.32, 0.44, fill=SOFTCARD, line=LINE, lw=0.75,
             radius=0.04)
        tb(s, PX + 0.30, y + 0.10, 1.10, 0.24, k, 11.5, True, INK, exact=True)
        tb(s, PX + 1.44, y + 0.11, PW - 1.76, 0.24, v, 9.5, False, GOLD_D,
           PP_ALIGN.RIGHT, exact=True)

    CX, CW = 6.90, 3.55
    stages = [
        ("原文 → 编码", "A 出站遮蔽", "敏感项换成占位编码", "本机", False),
        ("只见编码", "B 外部大模型", "回话里也没有明文", "外部", True),
        ("编码 → 明文", "C 本地还原", "用户看到的还是明文", "本机", False),
    ]
    ys = [1.90, 2.68, 3.46]
    connector(s, 6.34, 2.90, 6.86, 2.90, color=INK, lw=1.0)
    vline(s, 6.88, ys[0] + 0.34, ys[2] + 0.34 - (ys[0] + 0.34), color=INK, lw=0.010)
    for i, (cond, name, cap, tag, hot) in enumerate(stages):
        y = ys[i]
        card(s, CX, y, CW, 0.72, fill=RGBColor(0xFF, 0xFD, 0xF7) if hot else WHITE,
             line=GOLD if hot else LINE, lw=1.4 if hot else 1.0, radius=0.05)
        ic = shape(s, MSO_SHAPE.OVAL, CX + 0.16, y + 0.18, 0.36, 0.36,
                   fill=WHITE, line=GOLD if hot else GRAY_EDGE, lw=1.0)
        fill_text(ic, ["遮", "模", "还"][i], 11, True, GOLD_D if hot else BLUE2,
                  PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE, exact=True)
        tb(s, CX + 0.64, y + 0.07, 2.10, 0.22, cond, 9, False, GOLD_D, exact=True)
        tb(s, CX + 0.64, y + 0.26, 2.10, 0.26, name, 13, True, INK, exact=True)
        tb(s, CX + 0.64, y + 0.50, 2.30, 0.20, cap, 9.5, False, MUTED, exact=True)
        tg = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, CX + CW - 0.70, y + 0.10, 0.56,
                   0.22, fill=GOLDL if hot else SOFTCARD, line=None, radius=0.10)
        fill_text(tg, tag, 9, True, GOLD_D if hot else MUTED, PP_ALIGN.CENTER,
                  MSO_ANCHOR.MIDDLE, exact=True)
        arrow2(s, 6.88, y + 0.36, CX - 0.02, y + 0.36, color=INK, lw=1.0, dash=None)

    rcx, rcy = 11.85, 3.04
    circ2 = shape(s, MSO_SHAPE.OVAL, rcx - 0.43, rcy - 0.43, 0.86, 0.86,
                  fill=WHITE, line=INK, lw=1.2)
    fill_text(circ2, "用", 15, True, INK, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE,
              exact=True)
    tb(s, rcx - 0.70, rcy + 0.50, 1.40, 0.28, "用户", 13, True, INK,
       PP_ALIGN.CENTER, exact=True)
    tb(s, rcx - 0.70, rcy + 0.78, 1.40, 0.24, "看到的是明文", 9.5, False, MUTED,
       PP_ALIGN.CENTER, exact=True)
    connector(s, CX + CW, ys[2] + 0.36, 11.02, ys[2] + 0.36, color=INK, lw=1.0)
    vline(s, 11.05, rcy, ys[2] + 0.36 - rcy, color=INK, lw=0.010)
    arrow2(s, 11.05, rcy, rcx - 0.46, rcy, color=INK, lw=1.0, dash=None)

    tb(s, M, 4.44, 5.60, 0.28, "演示：", 12.5, True, BLUE, exact=True)
    cols = [
        ("① 原文", "招商银行与兴业银行同日发公告，客服电话 13800138000，邮箱 analyst@bankcorp.cn。",
         "库里的一篇文章"),
        ("② 发给模型的内容", "{{ORG_jcwwdnrn}} 与 {{ORG_lq4cr2iu}} 同日发公告，客服电话 {{PHONE_pbn2bfug}}。",
         "模型只看到编码"),
        ("③ 模型回包（原始）", "{{ORG_jcwwdnrn}} 与 {{ORG_lq4cr2iu}} 同日发公告，客服电话 {{PHONE_pbn2bfug}}。",
         "回话里也只有编码"),
        ("④ 本地还原后", "招商银行与兴业银行同日发公告，客服电话 13800138000，邮箱 analyst@bankcorp.cn。",
         "用户看到的还是明文"),
    ]
    colw, gap = 2.85, 0.11
    for i, (t, body, cap) in enumerate(cols):
        x = M + i * (colw + gap)
        hb = shape(s, MSO_SHAPE.RECTANGLE, x, 4.78, colw, 0.28, fill=LIGHT)
        tb(s, x, 4.82, colw, 0.22, t, 10.5, True, BLUE2, PP_ALIGN.CENTER, exact=True)
        card(s, x, 5.06, colw, 1.20, fill=WHITE, line=LINE, lw=0.75, radius=0.05)
        tb(s, x + 0.12, 5.16, colw - 0.24, 1.00, body, 9.5, False, INK,
           spacing=1.25, exact=True)
        tb(s, x, 6.30, colw, 0.22, cap, 9, False, MUTED, PP_ALIGN.CENTER, exact=True)
    footer(s)


# ---------- P10 成果与收益 ----------
def p10(prs):
    s = new_slide(prs)
    header(s, "10", "成果与收益", "AI辅助与人工跟踪对比实测", 10)

    pw = 3.723
    pgap = 0.28
    xs = [M + i * (pw + pgap) for i in range(3)]
    top, ph = 1.62, 4.90

    for x, t in zip(xs, ["实测对比", "数字口径", "已交付简报包含跟踪方向："]):
        card(s, x, top, pw, ph)
        shape(s, MSO_SHAPE.RECTANGLE, x, top, pw, 0.05, fill=BLUE)
        tb(s, x + 0.24, top + 0.20, pw - 0.48, 0.30, t, 13.5, True, BLUE)

    x0 = xs[0]
    tb(s, x0 + 0.24, 2.14, pw - 0.48, 0.70,
       "实测同批 5 篇同业信息从跟踪、分析到分享，提升",
       12.5, True, INK, spacing=1.3)
    tb(s, x0 + 0.24, 2.86, pw - 0.48, 0.80, "12 倍", 44, True, BLUE, exact=True)
    tb(s, x0 + 0.24, 3.56, pw - 0.48, 0.28, "2026-08-10 实测", 11, False, MUTED,
       exact=True)
    sq, gp = 0.228, 0.045
    card(s, x0 + 0.18, 3.96, pw - 0.36, 0.42, fill=LIGHT, line=None)
    for i in range(12):
        shape(s, MSO_SHAPE.RECTANGLE, x0 + 0.24 + i * (sq + gp), 4.04, sq, sq,
              fill=GOLD)
    tb(s, x0 + 0.24, 4.46, pw - 0.48, 0.28, "人工读完并整理要点耗时：31.6 分钟",
       11.5, True, INK, exact=True)
    card(s, x0 + 0.18, 4.78, pw - 0.36, 0.42, fill=LIGHT, line=None)
    shape(s, MSO_SHAPE.RECTANGLE, x0 + 0.24, 4.86, sq, sq, fill=GOLD)
    for i in range(1, 12):
        shape(s, MSO_SHAPE.RECTANGLE, x0 + 0.24 + i * (sq + gp), 4.86, sq, sq,
              fill=None, line=LINE, lw=1.0)
    tb(s, x0 + 0.24, 5.28, pw - 0.48, 0.28, "系统跑同批打分与核验耗时：2.5 分钟",
       11.5, True, BLUE, exact=True)
    tb(s, x0 + 0.24, 5.60, pw - 0.48, 0.52, "省下来的时间用于业务判断与决策。",
       11, False, MUTED, spacing=1.25, exact=True)

    x1 = xs[1]
    rows = [
        ("活跃信源", "15 路"),
        ("入库文章", "6,727 篇"),
        ("同业案例", "432 条"),
        ("覆盖机构", "356 家"),
        ("高参考案例", "137 条"),
        ("已交付简报", "105 份"),
    ]
    for i, (k, v) in enumerate(rows):
        y = 2.30 + i * 0.56
        tb(s, x1 + 0.24, y, 1.90, 0.30, k, 12, False, MUTED, exact=True)
        tb(s, x1 + 2.00, y - 0.02, pw - 2.24, 0.34, v, 13, True, INK,
           PP_ALIGN.RIGHT, exact=True)
        hline(s, x1 + 0.24, y + 0.40, pw - 0.48)
    shape(s, MSO_SHAPE.RECTANGLE, x1 + pw - 0.14, 5.66, 0.22, 0.012, fill=GOLD)
    arrow2(s, x1 + pw + 0.02, 5.68, xs[2] - 0.02, 5.68, color=GOLD, lw=1.2,
           dash=None)

    x2 = xs[2]
    dirs = [
        ("养老", "养老特色场景案例"),
        ("企业微信", "企微获客链路新打法"),
        ("数字人民币", "数币钱包接入实例"),
        ("银行零售", "零售 AUM 增长"),
        ("微信小程序", "小程序创新场景"),
        ("信用卡", "信用卡 AI 客服"),
        ("智能客服", "智能客服场景案例"),
        ("微信服务号", "服务号行业数据"),
    ]
    chw = (pw - 0.48 - 0.16) / 2
    for i, (d, c) in enumerate(dirs):
        cx = x2 + 0.24 + (i % 2) * (chw + 0.16)
        cy = 2.26 + (i // 2) * 0.60
        chip(s, cx, cy, chw, 0.32, d, 11, WHITE, RGBColor(0xBF, 0xCB, 0xDC),
             MUTED, False)
        tb(s, cx + 0.02, cy + 0.34, chw - 0.02, 0.26, c, 9, False, MUTED,
           spacing=1.0, exact=True)
    footer(s)


# ---------- P11 总结（双圆图） ----------
def p11(prs):
    s = new_slide(prs)
    header(s, "11", "总结", "AI自动处理+高质量交付，人专注判断+高效决策", 11)

    EDGE = RGBColor(0xC6, 0xD3, 0xE3)

    def hub(cx, cy, ring, label, sub, mark=None, text=None):
        d = 1.34
        shape(s, MSO_SHAPE.OVAL, cx - d / 2, cy - d / 2, d, d, fill=ring)
        if mark:
            pic(s, ASSETS + "/brand/" + mark, cx - 0.30, cy - 0.46, w=0.60)
        else:
            tb(s, cx - 0.60, cy - 0.19, 1.20, 0.38, text, 18, True, WHITE,
               PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE, exact=True)
        tb(s, cx - 0.62, cy + 0.26, 1.24, 0.24, sub, 9, False, WHITE,
           PP_ALIGN.CENTER, exact=True)
        tb(s, cx - 1.05, cy + d / 2 + 0.12, 2.10, 0.30, label, 14, True,
           ring, PP_ALIGN.CENTER, exact=True)

    def satellites(cx, cy, ring, items, rx=1.62, ry=1.40):
        n = len(items)
        for i, lab in enumerate(items):
            ang = 20 + i * (360.0 / n)
            nx = cx + rx * math.cos(math.radians(ang))
            ny = cy + ry * math.sin(math.radians(ang))
            connector(s, cx, cy, nx, ny, color=EDGE, lw=0.7)
            w = max(0.46, 0.115 * len(lab) + 0.20)
            h = 0.34
            node = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, nx - w / 2, ny - h / 2,
                         w, h, fill=WHITE, line=ring, lw=1.0, radius=0.10)
            fill_text(node, lab, 9, True, INK, PP_ALIGN.CENTER,
                      MSO_ANCHOR.MIDDLE, exact=True)

    AX, AY = 3.50, 3.90
    BX, BY = 9.85, 3.90
    satellites(AX, AY, BLUE, [
        "多路信源抓取", "三种价值打分", "事实核查", "深度阅读\n追问原文",
        "案例库", "多格式分享简报", "数据脱敏",
    ])
    satellites(BX, BY, BLUE2, [
        "周报", "业务决策", "同业对标", "方向校准", "采纳反馈",
    ], rx=1.45, ry=1.30)
    hub(AX, AY, BLUE, "同业追踪工作台", "高效交付 / 数据脱敏", mark="moni-mark-white.png")
    hub(BX, BY, BLUE2, "业务", "专注判断 / 高效决策", text="业务")

    connector(s, AX + 0.72, AY - 0.30, BX - 0.72, AY - 0.30, color=BLUE2, lw=4.5)
    arrow2(s, AX + 0.72, AY - 0.30, BX - 0.72, AY - 0.30, color=BLUE2, lw=4.5,
           dash=None)
    connector(s, BX - 0.72, AY + 0.30, AX + 0.72, AY + 0.30, color=BLUE2, lw=4.5)
    arrow2(s, BX - 0.72, AY + 0.30, AX + 0.72, AY + 0.30, color=BLUE2, lw=4.5,
           dash=None)
    footer(s)


NOTES = [
    "我们负责行内新媒体渠道建设，同业动作、监管口径和渠道打法都得跟。以前各自订阅几十个公众号拼周报，十人每周约二十小时；或等领导临时交办，熬夜凑材料。信息分散、口径不一、成果也没沉淀。",
    "工作台支撑从信息获取到简报分享的闭环：采集过滤、打分、核查、存案例、出简报五步。走完一轮会复盘：信源抓偏改标签，标准不对改权重，核查目标变了就调整；简报发出去，团队当天反馈。底部就是 moni 工作台，六个模块各司其职。",
    "内容处理分三步：十五类活跃信源定时自动入库；每个渠道按关键词过滤，黑白名单先挡掉无关内容；本地清洗去掉文末推广和网页乱码，空稿与短稿不入池。这一层不调模型，只把干净正文送进后面的打分和核查。",
    "打分排序分三套：质量分本地算，看正文结构全不全；通用业务价值分按十维固定权重，模型先摘原文依据再打分，程序算总分；业务方向分换一套问法，评估同一篇信息对所在业务条线有没有用。低分不删，归档可查。",
    "进入核查的文章先给一句总判断，下面挂多条带出处的摘要；分数标注帮识别高相关度信息。系统重点核对文中出现的机构名称和关键数字，对不上就打回模型重审；仍无法核实的内容标注为待核实，交给人工最终把关。",
    "某篇内容拿不准，就进阅读器详细追问：回答只依据这一篇原文，事实、推断、未知分开标注；每条结论带编号，点一下跳回对应段落；问出来的结论存进文章档案，选进简报时一起带出去。提问入口是现成模板，也支持自定义。",
    "看完的文章拆成能横向比的案例：主体、动作、时间、对手方、原文出处、核验状态六个字段。案例库支持按机构、场景和状态搜索筛选，随时回查历史做法；核过的案例带出处与关键数据，可直接分享给团队或一键进简报。",
    "同一批内容按场景出八种格式：群里直发信息图、日会周会投屏报告图、当面演示单文件 HTML、正式汇报 PDF，以及 Markdown、纯文本、富文本和对外案例简报。右侧是行业追踪报告 HTML 成品，单文件可交互。",
    "外部大模型参与核查、案例提取和对比，业务口径与名单会随提示词发出去，所以出网前先脱敏：敏感项换成占位编码，模型只见过编码，回话里也没有明文，本地还原后用户看到的还是明文。现场四栏演示：原文、发给模型的内容、模型回包、本地还原后。",
    "实测同批五篇同业信息，人工从读到整理要三十一点六分钟，系统跑同批打分与核验只要两分半，提升十二倍。当前：十五路活跃信源、六千七百二十七篇入库、四百三十二条同业案例、覆盖三百五十六家机构、一百三十七条高参考案例、已交付一百零五份简报。",
    "AI 自动处理与高质量交付，人专注判断与高效决策。工作台负责信源抓取、打分、核查、深读、案例库、多格式简报和脱敏；业务侧把成果用于周报、决策、同业对标、方向校准和采纳反馈。机器替人跑腿，判断留给人。",
]


def main():
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    for fn in (p01, p02, p03, p04, p05, p06, p07, p08, p09,
               p10, p11):
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
