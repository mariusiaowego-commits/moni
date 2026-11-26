# -*- coding: utf-8 -*-
"""moni 决赛汇报 PPT v19 构建脚本（15 页，浅色，台上字号下限，左轨 1/4）"""
import math
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.oxml.ns import qn

BASE = "/Users/mt16/.codex/worktrees/32b5/moni/.ppt-work"
ASSETS = BASE + "/assets"
V16 = BASE + "/assets/v16"
OUT = BASE + "/决赛汇报-v19.pptx"

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

TOTAL = 15
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


# ---------- P02 ----------
def p02(prs):
    s = new_slide(prs)
    header(s, "02", "工作流", "五步转成一个圈，转完一轮回到采集端", 2)
    nodes = [
        ("内容处理", "18 路信源定时入库，本机先过滤"),
        ("价值评估", "本地算质量分，模型打价值分"),
        ("检验核验", "优质文章逐条核对原文"),
        ("提取案例", "核对过的存成结构化案例"),
        ("分享简报", "按方向打包发给团队"),
    ]
    n = len(nodes)
    w, gap = 2.12, 0.28
    x0 = M
    top = 1.66
    for i, (t, d) in enumerate(nodes):
        x = x0 + i * (w + gap)
        hot = i == n - 1
        card(s, x, top, w, 1.24, fill=WHITE)
        shape(s, MSO_SHAPE.RECTANGLE, x, top, w, 0.05, fill=GOLD if hot else BLUE)
        numdot(s, x + 0.18, top + 0.20, i + 1, fill=LIGHT, color=BLUE2, d=0.28, size=10)
        tb(s, x + 0.54, top + 0.18, w - 0.70, 0.32, t, 14, True, INK)
        tb(s, x + 0.18, top + 0.62, w - 0.36, 0.56, d, 11.5, False, MUTED, spacing=1.25, exact=True)
        if i < n - 1:
            shape(s, MSO_SHAPE.RIGHT_ARROW, x + w + 0.03, top + 0.52, 0.20, 0.22, fill=LINE)

    line_y = 3.66
    x_first = x0 + w / 2
    x_last = x0 + (n - 1) * (w + gap) + w / 2
    shape(s, MSO_SHAPE.RECTANGLE, x_last - 0.006, top + 1.54, 0.012, line_y - top - 1.54, fill=BLUE)
    shape(s, MSO_SHAPE.RECTANGLE, x_first, line_y, x_last - x_first, 0.012, fill=BLUE)
    shape(s, MSO_SHAPE.RECTANGLE, x_first - 0.006, top + 1.54, 0.012, line_y - top - 1.54, fill=BLUE)
    shape(s, MSO_SHAPE.UP_ARROW, x_first - 0.09, top + 1.24, 0.18, 0.18, fill=BLUE)
    chip = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 5.48, 3.26, 2.80, 0.34,
                 fill=WHITE, line=BLUE, lw=1.0, radius=0.05)
    fill_text(chip, "决策回流 · 改信源和关键词", 11.5, True, BLUE, PP_ALIGN.CENTER,
              MSO_ANCHOR.MIDDLE)

    moni = card(s, 5.40, 4.02, 2.60, 0.72, fill=BLUE, line=None)
    fill_text(moni, "moni 工作台", 14, True, WHITE, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
    tb(s, 4.60, 4.86, 4.20, 0.30, "抓取、打分、存案例、出简报都在这一层", 12, False, MUTED,
       PP_ALIGN.CENTER)

    bar = card(s, M, 5.62, 11.73, 0.96, fill=LIGHT, line=None)
    tb(s, 1.04, 5.74, 11.2, 0.34, "前三步取材，第四步存案例，第五步发出去，再回头改采集口径。", 13.5, True, BLUE)
    tb(s, 1.04, 6.14, 11.2, 0.32,
       "每一步都留了配置入口：想加公众号改信源清单，觉得某类文章抓偏了改关键词，不用动代码就能重跑。",
       12, False, MUTED, spacing=1.3)
    footer(s)


# ---------- P03 ----------
def p03(prs):
    s = new_slide(prs)
    header(s, "03", "工作台", "一屏总览全局动态，高频操作直达手边", 3)
    ix, iy, iw, ih = M, 1.60, 7.55, 4.72
    pic(s, V16 + "/desk.png", ix, iy, w=iw)
    shape(s, MSO_SHAPE.RECTANGLE, ix, iy, iw, ih, fill=None, line=LINE, lw=1.0)

    marks = [
        (0.905, 0.175, "按方向分类", "银行、AI×金融、微信生态各有各的文章，一屏分开"),
        (0.135, 0.272, "标星区", "标星 48 篇，只有标过的才送进简报"),
        (0.470, 0.318, "命中标签", "入库那一刻就打上标签，前端能直接筛"),
        (0.855, 0.360, "原文时间", "原文什么时候发的、同步什么时候拿到的，分开记"),
    ]
    ex, ew = 8.62, 3.91
    row_h = 1.12
    for i, (fx, fy, t, d) in enumerate(marks):
        px = ix + fx * iw
        py = iy + fy * ih
        ey = 1.66 + i * row_h
        connector(s, px + 0.13, py + 0.09, px + 0.62, py + 0.09, color=RGBColor(0xB7, 0xCC, 0xE0), lw=1.0)
        badge(s, px - 0.02, py - 0.02, i + 1, d=0.26, fill=BLUE, size=9)
        badge(s, ex, ey + 0.02, i + 1, d=0.26, fill=BLUE, size=9)
        tb(s, ex + 0.36, ey - 0.02, ew - 0.36, 0.30, t, 13, True, INK)
        tb(s, ex + 0.36, ey + 0.30, ew - 0.36, 0.66, d, 12, False, MUTED, spacing=1.3)
        if i < len(marks) - 1:
            hline(s, ex, ey + row_h - 0.10, ew)

    bar = card(s, M, 6.48, 11.73, 0.34, fill=LIGHT, line=None)
    tb(s, 1.04, 6.53, 11.2, 0.26,
       "截图取自 moni 生产环境 8900 的文章查阅页；图中编号对应右侧说明。原文、案例、简报都存在本机磁盘，数据不出终端。",
       10.5, False, BLUE)
    demo_tag(s, 10.43, 1.22, "▶ 现场演示 10 秒", 2.10)
    footer(s)


# ---------- P04 ----------
def p04(prs):
    s = new_slide(prs)
    header(s, "04", "环节一 · 内容处理", "多路信源自动接入，先清洗过滤再进模型", 4)
    rail(s, 1)
    cols = [
        ("信源接入", V16 + "/src.png", [
            "18 路信源，15 路保持活跃",
            "821 个发布方，定时自动拉取",
            "某个公众号断更或失效，后台直接停用",
        ]),
        ("规则过滤", V16 + "/rules.png", [
            "黑名单按关键词挡掉无关文章",
            "所有渠道共用一套关键词规则，不重不漏",
            "命中业务方向的才放行入库",
        ]),
        ("去噪清洗", V16 + "/clean.png", [
            "公众号推广尾巴、网页乱码符号本地去掉",
            "空正文与短稿单独标出来",
            "这一层不调模型，几千篇几秒跑完",
        ]),
    ]
    cw = 2.72
    for i, (t, img, items) in enumerate(cols):
        x = BODY_X + i * (cw + 0.17)
        tb(s, x, 1.62, cw, 0.30, t, 15, True, BLUE)
        hline(s, x, 1.98, cw)
        box_h = 1.20 if i == 2 else 1.62
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
       "省的是模型调用费，更重要的是送进模型的每篇正文都是干净的，后面的打分和核查才有意义。",
       12, False, MUTED)
    footer(s)


# ---------- P05 ----------
def p05(prs):
    s = new_slide(prs)
    header(s, "05", "环节二 · 价值评估", "两套评分分开算，先找原文证据再打分", 5)
    rail(s, 2)
    tags = [
        ("质量分 0–5", "本地规则算\n不看模型"),
        ("业务价值分 0–100", "十维加权\n先取证再打分"),
        ("方向分", "换问法\n不换权重"),
    ]
    for i, (n, d) in enumerate(tags):
        x = BODY_X + i * 2.88
        main = i == 1
        card(s, x, 1.62, 2.70, 1.00, fill=WHITE, line=LINE)
        if main:
            shape(s, MSO_SHAPE.RECTANGLE, x, 1.62, 2.70, 0.05, fill=BLUE)
        badge(s, x + 0.14, 1.76, i + 1, d=0.26, size=10)
        tb(s, x + 0.48, 1.74, 2.10, 0.30, n, 13, True, INK)
        tb(s, x + 0.14, 2.10, 2.42, 0.48, d, 12, False, MUTED, spacing=1.2)

    pic(s, ASSETS + "/brief-card.png", BODY_X, 2.82, w=3.40)
    for i, fy in enumerate([0.343, 0.600, 0.825]):
        badge(s, BODY_X + 3.05, 2.82 + 3.40 * fy - 0.15, i + 1, d=0.28, size=10)
    tb(s, BODY_X + 0.02, 6.44, 3.60, 0.30, "简报里每条结论都带着它的分和出处", 10.5, False, MUTED, exact=True)

    tx = BODY_X + 3.78
    tw = BODY_W - 3.78
    tb(s, tx, 2.72, tw, 0.32, "打分过程每一步都可查", 15, True, BLUE)
    rules = [
        ("本地与模型分离", "质量分纯本地算，价值分才调模型"),
        ("先取证后打分", "模型先摘原文依据，再给维度分"),
        ("总分由程序算", "模型只评单项，总分按权重由程序算"),
        ("审出问题就重打", "另一套检查发现前后矛盾就重打，每篇最多五轮"),
        ("只在出简报时跑", "平时不空耗算力"),
        ("低分只往后排", "低分不删，留着随时回头查证"),
    ]
    for i, (t, d) in enumerate(rules):
        y = 3.14 + i * 0.58
        numdot(s, tx, y + 0.04, i + 1, d=0.26, size=9)
        tb(s, tx + 0.36, y - 0.02, 1.55, 0.30, t, 12.5, True, INK)
        tb(s, tx + 1.91, y - 0.02, 2.81, 0.56, d, 12, False, MUTED, spacing=1.25)
    footer(s)


# ---------- P06 ----------
def p06(prs):
    s = new_slide(prs)
    header(s, "06", "环节三 · 检验核验", "优质文章置顶核验，机构数字逐条过关", 6)
    rail(s, 3)
    pic(s, ASSETS + "/brief-card.png", BODY_X, 1.66, w=3.30)
    shape(s, MSO_SHAPE.RECTANGLE, BODY_X, 1.66, 3.30, 3.42, fill=None, line=LINE, lw=1.0)
    for i, fy in enumerate([0.343, 0.600, 0.825]):
        badge(s, BODY_X + 2.93, 1.66 + 3.30 * fy - 0.15, i + 1, d=0.28, size=10)
    tb(s, BODY_X, 5.16, 3.40, 0.44, "每条摘要先判断，后附原文出处；核对不过打回重做。",
       10.5, False, MUTED, spacing=1.25, exact=True)

    tx = BODY_X + 3.57
    tw = BODY_W - 3.57
    items = [
        ("置顶才进核验", "人工把好文章标星置顶，系统才开始提炼要点并核对"),
        ("只盯三样东西", "机构主体、具体动作、关键数字，这三项错了会带偏判断"),
        ("摘要先结论后原文", "输出五条核心摘要，每条先给判断，紧跟原文出处"),
        ("不通过就打回", "机构或数字对不上原文，直接拦截并要求重新生成"),
    ]
    for i, (t, d) in enumerate(items):
        y = 1.70 + i * 0.92
        numdot(s, tx, y + 0.06, i + 1, d=0.28, size=10)
        tb(s, tx + 0.40, y, tw - 0.40, 0.30, t, 13, True, INK)
        tb(s, tx + 0.40, y + 0.34, tw - 0.40, 0.52, d, 10, False, MUTED, spacing=1.25)
        if i < 3:
            hline(s, tx, y + 0.84, tw)

    card(s, BODY_X, 5.70, BODY_W, 1.10, fill=LIGHT, line=None)
    tb(s, BODY_X + 0.22, 5.82, BODY_W - 0.44, 0.30, "为什么非要核这一遍", 13, True, BLUE)
    tb(s, BODY_X + 0.22, 6.14, BODY_W - 0.44, 0.62,
       "大模型常把同业名称记混，或者把原文里的数字算错。这类错在阅读时看不出来，一旦进了汇报材料，"
       "就变成我们对外的说法。入库的每条案例都要先过这一遍，四百三十二条案例才站得住。",
       12, False, INK, spacing=1.3)
    footer(s)


# ---------- P07 ----------
def p07(prs):
    s = new_slide(prs)
    header(s, "07", "环节三 · 深度阅读", "单篇深读直接提问，结论打包带进简报", 7)
    rail(s, 3, branch=True, branch_at=3)
    pic(s, ASSETS + "/rd-ask.png", BODY_X, 1.66, w=3.70)
    shape(s, MSO_SHAPE.RECTANGLE, BODY_X, 1.66, 3.70, 1.58, fill=None, line=LINE, lw=1.0)
    pic(s, ASSETS + "/rd-cite.png", BODY_X, 3.44, h=2.42)
    shape(s, MSO_SHAPE.RECTANGLE, BODY_X, 3.44, 2.60, 2.42, fill=None, line=LINE, lw=1.0)
    tb(s, BODY_X, 6.00, 3.70, 0.30, "上：对原文提问　下：结论带编号",
       10.5, False, MUTED, spacing=1.2, exact=True)

    tx = BODY_X + 4.06
    tw = BODY_W - 4.06
    feats = [
        ("针对单篇直接对话", "在文章页直接做摘要、事实核查或数据复核，不用把正文再贴一遍"),
        ("问出来的结论跟着文章走", "把问出来的结论存进文章档案，选进简报时按需一起带出去"),
        ("结论带编号可溯源", "每条回答都带引用编号，点一下跳回原文对应段落"),
    ]
    for i, (t, d) in enumerate(feats):
        y = 1.70 + i * 0.82
        numdot(s, tx, y + 0.04, i + 1, d=0.26, size=10)
        tb(s, tx + 0.36, y - 0.02, tw - 0.36, 0.30, t, 13, True, INK)
        tb(s, tx + 0.36, y + 0.30, tw - 0.36, 0.54, d, 12, False, MUTED, spacing=1.25)

    card(s, tx, 4.34, tw, 2.06, fill=LIGHT, line=None)
    tb(s, tx + 0.20, 4.46, tw - 0.40, 0.30, "六个预设问题，点一下就开始", 13, True, BLUE)
    qs = [
        "这次改版动了哪个产品流程或收费规则",
        "增长数据的统计口径和时间跨度是什么",
        "面向哪类客户，对客门槛和权益成本多少",
        "按我们行的系统和合规条件，最大的卡点在哪",
        "只借鉴一两个小动作，最快能在哪个渠道试跑",
        "这是一次性活动还是长期机制，值得跟进多久",
    ]
    for i, q in enumerate(qs):
        x = tx + 0.20
        y = 4.76 + i * 0.32
        sh = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, tw - 0.40, 0.30, fill=WHITE,
                   line=LINE, radius=0.03)
        fill_text(sh, q, 10.5, False, INK, PP_ALIGN.LEFT, MSO_ANCHOR.MIDDLE,
                  spacing=1.0)
        sh.text_frame.margin_left = 60960
        sh.text_frame.margin_right = 45720
        sh.text_frame.margin_top = 0
        sh.text_frame.margin_bottom = 0
    demo_tag(s, 10.43, 1.22, "▶ 现场演示 15 秒", 2.10)
    footer(s)


# ---------- P08 ----------
def p08(prs):
    s = new_slide(prs)
    header(s, "08", "环节四 · 提取案例", "看完的文章，拆成能横向比的案例", 8)
    rail(s, 4)
    pic(s, V16 + "/cases.png", BODY_X, 1.66, w=4.55)
    shape(s, MSO_SHAPE.RECTANGLE, BODY_X, 1.66, 4.55, 3.53, fill=None, line=LINE, lw=1.0)
    tb(s, BODY_X, 5.32, 4.55, 0.52, "案例库页截图（2026-11-25，生产环境）：编号、主体、动作、时间和对手方都在一屏",
       10.5, False, MUTED, spacing=1.25, exact=True)

    tx = 8.83
    tw = 12.53 - tx
    tb(s, tx, 1.66, tw, 0.32, "一条案例包含五项信息", 14, True, BLUE)
    fields = ["编号 MN-AI-…", "主体：哪一家机构", "动作：做了什么", "时间与对手方", "证据原文"]
    for i, f in enumerate(fields):
        y = 2.06 + i * 0.40
        shape(s, MSO_SHAPE.RECTANGLE, tx, y + 0.06, 0.05, 0.24, fill=BLUE)
        tb(s, tx + 0.18, y, tw - 0.18, 0.30, f, 12, False, INK)
    hline(s, tx, 4.06, tw)
    tb(s, tx, 4.18, tw, 0.32, "存成结构化案例的好处", 14, True, BLUE)
    uses = [
        ("横向比", "同类动作摆到一张表里，谁先上、谁做得深看得出来"),
        ("按机构回查", "想知道某家银行做过什么，按银行名称筛一下就有"),
    ]
    for i, (t, d) in enumerate(uses):
        x = tx + i * 1.90
        card(s, x, 4.56, 1.78, 1.34)
        numdot(s, x + 0.16, 4.70, i + 1, d=0.24, size=9)
        tb(s, x + 0.46, 4.68, 1.16, 0.28, t, 12.5, True, INK)
        tb(s, x + 0.16, 5.02, 1.50, 0.84, d, 11.5, False, MUTED, spacing=1.3, exact=True)
    band = card(s, tx, 6.36, tw, 0.44, fill=LIGHT, line=None)
    tb(s, tx + 0.16, 6.44, tw - 0.32, 0.30, "每条都绑着原文段落，可点回报道现场", 12,
       True, BLUE)
    demo_tag(s, 10.43, 1.22, "▶ 现场演示 10 秒", 2.10)
    footer(s)


# ---------- P09 ----------
def p09(prs):
    s = new_slide(prs)
    header(s, "09", "环节五 · 分享简报", "同一批内容，出 8 种格式，按场合分发", 9)
    rail(s, 5)
    pic(s, V16 + "/brief.png", BODY_X, 1.66, w=5.10)
    shape(s, MSO_SHAPE.RECTANGLE, BODY_X, 1.66, 5.10, 4.26, fill=None, line=LINE, lw=1.0)
    tb(s, BODY_X, 6.02, 5.10, 0.58, "实际交付的简报成品：标题、分值、来源、日期、摘要和待核实项同在一屏",
       10.5, False, MUTED, spacing=1.25)

    tx = BODY_X + 5.36
    tw = BODY_W - 5.36
    tb(s, tx, 1.66, tw, 0.30, "什么场合用什么格式", 13, True, BLUE)
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
        y = 2.02 + i * 0.36
        tb(s, tx, y, 1.58, 0.28, a, 12, False, MUTED)
        tb(s, tx + 1.60, y, tw - 1.60, 0.28, b, 12, True, INK)
        hline(s, tx, y + 0.30, tw)

    card(s, tx, 4.86, tw, 1.16, fill=LIGHT, line=None)
    tb(s, tx + 0.20, 4.96, tw - 0.40, 0.28, "已交付", 12, False, BLUE)
    tb(s, tx + 0.20, 5.22, 1.50, 0.54, "105 份", 26, True, BLUE)
    tb(s, tx + 1.80, 5.36, 1.30, 0.28, "成品简报", 11.5, False, BLUE)
    tb(s, tx, 6.16, tw, 0.56, "早上跑完，日会直接投；会后发链接，点标题还能回到原文。",
       12, False, MUTED, spacing=1.3)
    footer(s)


# ---------- P10 ----------
def p10(prs):
    s = new_slide(prs)
    header(s, "10", "出站防护", "敏感信息不出内网，模型只见编码不见真名", 10)
    chip = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 9.86, 1.20, 2.67, 0.34,
                 fill=GOLDL, line=GOLD, radius=0.05)
    fill_text(chip, "已上线 · 开关默认关", 11.5, True, GOLD_D, PP_ALIGN.CENTER,
              MSO_ANCHOR.MIDDLE)
    tb(s, M, 1.56, 8.60, 0.28, "moni 调用外部模型的所有通道，都在本地发出前统一拦住处理。", 12.5, False, MUTED)

    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 0.80, 1.94, 5.86, 1.24, fill=LIGHT, line=None, radius=0.06)
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 6.74, 1.94, 1.84, 1.24, fill=GOLDL, line=GOLD, radius=0.06)
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 8.66, 1.94, 3.87, 1.24, fill=LIGHT, line=None, radius=0.06)
    tb(s, 0.96, 1.98, 2.00, 0.26, "本机 · 出站前", 11.5, True, BLUE2)
    tb(s, 6.90, 1.98, 1.20, 0.26, "外部", 11.5, True, GOLD_D)
    tb(s, 8.82, 1.98, 2.20, 0.26, "本机 · 回站后", 11.5, True, BLUE2)

    boxes = [
        ("原文", "正文、机构、结论", False),
        ("出站遮蔽", "敏感项换成编码", False),
        ("本机对照表", "编码对应原值", False),
        ("大模型", "只看得到编码", True),
        ("本地还原", "回包换回原值", False),
        ("用户", "看到的是明文", False),
    ]
    band = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 0.80, 2.24, 11.73, 0.66,
                 fill=LIGHT, line=None, radius=0.05)
    for i, (n, d, hot) in enumerate(boxes):
        x = 0.80 + i * 1.99
        if hot:
            sh = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, 2.26, 1.78, 0.62,
                       fill=BLUE, line=None, radius=0.05)
            fill_text(sh, n, 12, True, WHITE, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
            tb(s, x, 2.94, 1.78, 0.26, d, 10.5, False, GOLD_D, PP_ALIGN.CENTER, exact=True)
        else:
            sh = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, 2.26, 1.78, 0.62,
                       fill=WHITE, line=None, radius=0.05)
            fill_text(sh, n, 12, True, INK, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
            tb(s, x, 2.94, 1.78, 0.26, d, 10.5, False, MUTED, PP_ALIGN.CENTER, exact=True)
        if i < 5:
            cross = i in (2, 3)
            shape(s, MSO_SHAPE.RIGHT_ARROW, x + 1.80, 2.47, 0.17, 0.20,
                  fill=GOLD if cross else LINE)

    facts = [
        ("同一机构同一编码", "同一家机构在请求里恒定同一个代号，模型不会看乱。"),
        ("联网核查按策略放行", "需要公网查证时保留公开机构名，否则公网搜不到结果。"),
        ("遮不干净就拦住", "出网前再扫一遍，仍有明文就拦下，模型一次都不调用，账本记一条。"),
        ("异常时先放行", "脱敏程序万一报错先放行，保证业务不断；要求严格时可切成报错即停"),
    ]
    for i, (n, d) in enumerate(facts):
        x = M + (i % 2) * 5.98
        y = 3.40 + (i // 2) * 0.88
        card(s, x, y, 5.75, 0.86)
        tb(s, x + 0.20, y + 0.10, 2.30, 0.28, n, 13, True, BLUE)
        tb(s, x + 0.20, y + 0.46, 5.35, 0.32, d, 12, False, MUTED, spacing=1.2)

    card(s, M, 5.24, 3.83, 1.60)
    tb(s, 1.02, 5.34, 3.40, 0.58, "≤ 2.5 ms", 32, True, BLUE)
    tb(s, 1.02, 5.98, 3.40, 0.78, "处理一篇六千字中文研报，遮蔽加还原耗时不超过 2.5 毫秒", 12,
       False, MUTED, spacing=1.3)

    card(s, 4.75, 5.24, 3.83, 1.60)
    tb(s, 4.97, 5.34, 3.40, 0.58, "0", 32, True, BLUE)
    tb(s, 4.97, 5.98, 3.40, 0.78, "真实模型端到端跑完一趟，出站明文命中数", 12, False,
       MUTED, spacing=1.3)

    card(s, 8.70, 5.24, 3.83, 1.60)
    tb(s, 8.92, 5.34, 3.40, 0.58, "0 行", 32, True, BLUE)
    tb(s, 8.92, 5.98, 3.40, 0.78, "开关默认关，生产库写入 0 行；配置与实验室已并到一页。",
       12, False, MUTED, spacing=1.3)
    footer(s)


# ---------- P11 ----------
def p11(prs):
    s = new_slide(prs)
    header(s, "11", "演练台", "同一个页面里，从开关到验证一步不用跳", 11)
    tb(s, M, 1.58, 9.30, 0.28,
       "配置和实验室已经并进「配置管理 → 脱敏」一个页签，往下滚就是四栏对比。", 12.5, False, MUTED)
    demo_tag(s, 10.43, 1.22, "▶ 现场演示 25 秒", 2.10)

    cols = [
        ("①", "原文",
         "招商银行与兴业银行同日发布公告：客服电话 13800138000，邮箱 analyst@bankcorp.cn。"
         "请只复述联系方式。",
         "这是我们库里的一篇真文章"),
        ("②", "发给模型的内容",
         "{{ORG_jcwwdnrn}} 与 {{ORG_lq4cr2iu}} 同日发布公告：客服电话 {{PHONE_pbn2bfug}}，"
         "邮箱 {{EMAIL_colbtckw}}。",
         "模型只看得到编码"),
        ("③", "模型回包（原始）",
         "{{ORG_jcwwdnrn}} 与 {{ORG_lq4cr2iu}} 同日发布公告：客服电话 {{PHONE_pbn2bfug}}，"
         "邮箱 {{EMAIL_colbtckw}}。",
         "回话里也只有编码"),
        ("④", "本地还原后",
         "招商银行 与 兴业银行 同日发布公告：客服电话 13800138000，邮箱 analyst@bankcorp.cn。",
         "用户看到的还是明文"),
    ]
    for i, (n, t, body, say) in enumerate(cols):
        x = M + i * 2.97
        card(s, x, 1.90, 2.81, 2.24)
        badge(s, x + 0.14, 2.04, n, d=0.26, size=11)
        tb(s, x + 0.46, 2.02, 2.24, 0.30, t, 12, True, INK)
        tb(s, x + 0.14, 2.40, 2.53, 1.22, body, 10.5, False, INK, spacing=1.2)
        tb(s, x + 0.14, 3.68, 2.53, 0.32, say, 11.5, True, BLUE)

    metrics = [("5", "遮蔽处数 · 136 µs"), ("5", "还原处数 · 98 µs"), ("0", "未还原"),
               ("0", "出站泄漏命中"), ("0", "残留占位符"), ("8775 ms", "模型耗时")]
    for i, (n, lab) in enumerate(metrics):
        x = M + i * 1.83
        card(s, x, 4.14, 1.69, 0.86, fill=LIGHT, line=None)
        tb(s, x, 4.22, 1.69, 0.42, n, 20 if i < 5 else 17, True, BLUE, PP_ALIGN.CENTER)
        tb(s, x, 4.66, 1.69, 0.26, lab, 10.5, False, MUTED, PP_ALIGN.CENTER)

    tb(s, M, 5.06, 5.15, 0.24, "生产环境 8900 实拍，同一页签内的三个按钮", 10.5, False, MUTED, exact=True)
    pic(s, ASSETS + "/desens-inline-btn.png", M, 5.34, w=5.15)
    tb(s, M, 5.94, 5.15, 0.52, "上方是总开关和三级规则，往下滚就是四栏对比，中间不用跳页。",
       11.5, False, MUTED, spacing=1.25, exact=True)
    tb(s, 6.15, 5.06, 6.38, 0.28, "这是 moni 生产环境 8900 上的页面，不是示意图，模型是真实模型。", 12, False, MUTED)
    tb(s, 6.15, 5.38, 6.38, 0.54, "现场提示：点「模型实测」等模型回话，依次划过第二第三栏，再指第四栏和两个零。",
       12.5, True, BLUE, spacing=1.3)
    tb(s, 6.15, 5.96, 6.38, 0.54, "实验室与日常业务共用同一套防护逻辑：遮不干净时出网前就拦，模型一次都不调用。",
       12.5, True, BLUE, spacing=1.3)
    tb(s, 6.15, 6.42, 6.38, 0.44, "地址 127.0.0.1:8900/config?tab=desens；账本分生产与实验室两行口径，示例号码为虚构值。",
       10.5, False, GOLD_D, spacing=1.25, exact=True)
    footer(s)


# ---------- P12 ----------
def p12(prs):
    s = new_slide(prs)
    header(s, "12", "可证伪", "三项指标每次调用都记账，数字能现场复算", 12)
    tb(s, M, 1.58, 11.73, 0.28, "遮蔽有没有漏，直接查审计账本；账本只记编码，不留原文。", 12.5, False, MUTED)

    card(s, M, 1.96, 6.05, 4.04)
    tb(s, 1.02, 2.10, 5.60, 0.32, "和通用密钥型脱敏的区别", 14.5, True, BLUE)
    tb(s, 2.20, 2.52, 2.05, 0.26, "通用密钥型（网关层）", 11, True, MUTED)
    tb(s, 4.42, 2.52, 2.28, 0.26, "moni（业务进程内）", 11, True, BLUE)
    shape(s, MSO_SHAPE.RECTANGLE, 4.30, 2.46, 0.012, 2.94, fill=LINE)
    cmp_rows = [
        ("遮什么", "密钥、密码、邮箱、证件号", "以上，外加同业机构全简称、内部结论、关注名单"),
        ("在哪处理", "模型网关统一过一道", "发出前在业务程序里处理，知道任务类型与文章"),
        ("能不能复算", "有遮蔽条数，无还原成功率", "还原成功率、泄漏命中、残留占位符"),
    ]
    for i, (d, a, b) in enumerate(cmp_rows):
        y = 2.92 + i * 0.90
        tb(s, 1.02, y - 0.02, 1.10, 0.28, d, 11.5, True, GOLD_D)
        tb(s, 2.20, y - 0.04, 2.05, 0.82, a, 11, False, MUTED, spacing=1.3)
        tb(s, 4.42, y - 0.04, 2.28, 0.82, b, 11, False, INK, spacing=1.3)
        if i < 2:
            hline(s, 1.02, y + 0.80, 5.62)
    tb(s, 1.02, 5.56, 5.62, 0.34, "同业判断属于业务口径，按密钥规则匹配不到，只能在业务进程内补。", 11.5, False, BLUE)

    card(s, 7.03, 1.96, 5.50, 4.04)
    tb(s, 7.25, 2.10, 5.06, 0.30, "三项可复算指标", 14.5, True, BLUE)
    metrics = [
        ("0", "出站泄漏命中", "对实际发出的载荷扫一遍敏感串，命中数必须恒为 0"),
        ("0", "写库残留占位符", "占位符不许落进摘要、任务结果和阅读器消息"),
        ("可算", "精确还原成功率", "还原数 ÷（还原数 + 未还原数）"),
    ]
    for i, (n, t, d) in enumerate(metrics):
        y = 2.54 + i * 0.86
        tb(s, 7.25, y, 1.26, 0.42, n, 21, True, BLUE)
        tb(s, 8.58, y, 3.70, 0.30, t, 12.5, True, INK)
        tb(s, 8.58, y + 0.32, 3.82, 0.54, d, 11.5, False, MUTED, spacing=1.25)
    sql = card(s, 7.25, 5.08, 5.06, 0.86, fill=LIGHT, line=None)
    tb(s, 7.40, 5.14, 4.80, 0.76,
       "SELECT sum(masked_total), sum(restored_total),\n"
       "  sum(unrestored_count), sum(leak_scan_hits),\n"
       "  sum(residual_placeholders) FROM desensitize_events;",
       10.5, False, BLUE, spacing=1.25)

    bar = card(s, M, 6.14, 11.73, 0.62, fill=LIGHT, line=None)
    tb(s, 1.04, 6.26, 11.2, 0.30, "口径来自审计表 desensitize_events。实验室已上线，这条 SQL 现场就能跑。",
       12, False, BLUE)
    footer(s)


# ---------- P13 ----------
def p13(prs):
    s = new_slide(prs)
    header(s, "13", "成果与收益", "五篇实测提速十二倍，八个业务方向持续输出", 13)
    stats = [("15", "路活跃信源", "公众号·媒体·官方"),
             ("6,727", "篇入库", "自动抓取清洗"),
             ("432", "条案例", "其中 358 条已确认"),
             ("356", "家机构", "主体去重")]
    for i, (n, lab, sub) in enumerate(stats):
        x = M + i * 2.96
        card(s, x, 1.62, 2.85, 1.00)
        shape(s, MSO_SHAPE.RECTANGLE, x, 1.62, 2.85, 0.04, fill=BLUE)
        tb(s, x + 0.18, 1.66, 2.49, 0.52, n, 30, True, BLUE)
        tb(s, x + 0.18, 2.16, 2.49, 0.28, lab, 12, True, INK)
        tb(s, x + 0.18, 2.38, 2.49, 0.20, sub, 10.5, False, MUTED, exact=True)

    tb(s, M, 2.86, 6.02, 0.28, "同批 5 篇真实文章 · 2026-08-10 实测", 12.5, True, BLUE)
    tb(s, M, 3.18, 5.70, 0.52,
       "取当日入库 5 篇真实文章，人工组读完摘要点，系统组跑同批打分与核验。",
       11.5, False, MUTED, spacing=1.25, exact=True)

    tb(s, M, 3.86, 0.90, 0.28, "人工", 12, False, MUTED)
    tb(s, 1.76, 3.86, 1.10, 0.28, "31.6 分钟", 12, True, INK, PP_ALIGN.RIGHT)
    shape(s, MSO_SHAPE.RECTANGLE, 3.00, 3.90, 3.60, 0.26, fill=LINE)

    tb(s, M, 4.44, 0.90, 0.28, "系统", 12, False, MUTED)
    tb(s, 1.76, 4.44, 1.10, 0.28, "2.5 分钟", 12, True, BLUE, PP_ALIGN.RIGHT)
    shape(s, MSO_SHAPE.RECTANGLE, 3.00, 4.48, 3.60 * 2.5 / 31.6, 0.26, fill=BLUE)

    tb(s, M, 5.02, 0.90, 0.30, "倍数", 12, False, MUTED)
    tb(s, 1.76, 4.96, 1.10, 0.36, "12 倍", 20, True, BLUE, PP_ALIGN.RIGHT)
    tb(s, 3.00, 5.08, 3.40, 0.24, "31.6 ÷ 2.5", 10.5, False, MUTED, exact=True)

    tb(s, M, 5.70, 6.02, 0.30, "同样五篇，从半小时压到两分半，人只做判断。", 12.5, True, BLUE)

    vline(s, 7.12, 2.72, 3.90)
    tb(s, 7.42, 2.72, 5.11, 0.26, "已交付的简报在盯这些方向", 12.5, True, BLUE)
    tb(s, 7.42, 3.04, 1.50, 0.24, "方向", 12, False, MUTED)
    tb(s, 8.98, 3.04, 0.62, 0.24, "次数", 12, False, MUTED, PP_ALIGN.RIGHT)
    tb(s, 9.68, 3.04, 2.85, 0.24, "覆盖内容", 12, False, MUTED)
    topics = [
        ("小微普惠", 65, "授信贴息·线上获客"),
        ("企业微信", 50, "加粉·标签·话术"),
        ("数字人民币", 36, "硬钱包·满减·商户"),
        ("银行零售", 26, "分层·代发·财富"),
        ("App", 26, "改版·弹窗·交互"),
        ("信用卡", 24, "新户礼·分期·联名"),
        ("智能客服", 10, "外呼·坐席辅助"),
        ("视频号", 9, "直播·投流·转化"),
    ]
    for i, (a, n, d) in enumerate(topics):
        y = 3.36 + i * 0.33
        tb(s, 7.42, y - 0.02, 1.50, 0.28, a, 12, True, INK)
        tb(s, 8.98, y, 0.62, 0.26, str(n), 12, True, BLUE2, PP_ALIGN.RIGHT)
        tb(s, 9.68, y, 2.85, 0.26, d, 10.5, False, MUTED, exact=True)
        hline(s, 7.42, y + 0.28, 5.11)
    tb(s, 7.42, 5.98, 4.75, 0.26, "次数为 26 份已交付简报中该方向的命中行数", 10.5, False, MUTED, exact=True)

    bar = card(s, M, 6.30, 11.73, 0.44, fill=LIGHT, line=None)
    tb(s, 1.04, 6.38, 11.2, 0.28,
       "15 路信源每周 10 人 20 人时，换成每天 31.6 到 2.5 分钟的机器活。",
       12.5, True, BLUE)
    footer(s)


# ---------- P14 ----------
def p14(prs):
    s = new_slide(prs)
    header(s, "14", "不止同业", "同一套情报流水线，直接复用给行内其它团队", 14)
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
        chip = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, rx + 0.20, y + 0.16, 0.76, 0.34,
                     fill=GOLD_D if i == 2 else BLUE, line=None, radius=0.04)
        fill_text(chip, tag, 12, True, WHITE, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
        tb(s, rx + 1.08, y + 0.14, rw - 1.28, 0.32, t, 14, True, INK)
        tb(s, rx + 1.08, y + 0.50, rw - 1.28, 0.46, d, 12, False, MUTED, spacing=1.25)

    bar = card(s, M, 6.28, 11.73, 0.50, fill=LIGHT, line=None)
    tb(s, 1.04, 6.36, 11.2, 0.32,
       "工作台和后台流水线无需重写，换一套信源清单和关键词规则就能在消保合规条线跑起来。",
       12.5, True, BLUE)
    footer(s)


# ---------- P15 ----------
def p15(prs):
    s = new_slide(prs)
    header(s, "15", "人机协作", "机器守住质量下限，人的判断力决定采集方向", 15)
    tb(s, M, 1.56, 11.73, 0.30, "机器和人各自按节奏循环，中间靠每天的简报和反馈对接。", 12.5, False, MUTED)

    cx1, cy1, r1 = 3.05, 3.96, 1.30
    shape(s, MSO_SHAPE.OVAL, cx1 - r1, cy1 - r1, r1 * 2, r1 * 2, fill=None, line=BLUE, lw=1.5)
    tb(s, cx1 - 1.33, 1.90, 2.66, 0.32, "机器这一圈", 16, True, BLUE, PP_ALIGN.CENTER)
    mrows = ["改规则或提示词", "用固定考卷跑一遍", "指标不降才换线上", "旧版本留档可回退"]
    for i, r in enumerate(mrows):
        a = -90 + i * 90
        bx = cx1 + r1 * math.cos(math.radians(a)) - 0.95
        by = cy1 + r1 * math.sin(math.radians(a)) - 0.17
        sh = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, bx, by, 1.90, 0.46, fill=WHITE, line=BLUE, lw=1.2, radius=0.05)
        fill_text(sh, r, 11.5, True, INK, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
    tb(s, cx1 - 1.30, 5.94, 3.30, 0.52, "数据清洗、打分、脱敏、定时任务都走这一圈", 11.5, False, MUTED, spacing=1.25, exact=True)

    cx2, cy2, r2 = 10.00, 3.96, 1.30
    shape(s, MSO_SHAPE.OVAL, cx2 - r2, cy2 - r2, r2 * 2, r2 * 2, fill=LIGHT, line=BLUE2, lw=1.5)
    tb(s, cx2 - 1.33, 1.90, 2.66, 0.32, "人这一圈", 16, True, INK, PP_ALIGN.CENTER)
    hrows = ["早上看当天简报", "在会上拿案例做判断", "看反馈哪些判断对了", "回头补关键词再取一批"]
    for i, r in enumerate(hrows):
        a = -90 + i * 90
        bx = cx2 + r2 * math.cos(math.radians(a)) - 0.95
        by = cy2 + r2 * math.sin(math.radians(a)) - 0.17
        sh = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, bx, by, 1.90, 0.46, fill=WHITE, line=BLUE2, lw=1.2, radius=0.05)
        fill_text(sh, r, 11.5, True, INK, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
    tb(s, cx2 - 1.55, 5.94, 3.80, 0.30, "判断留在人这一侧，系统照着反馈调下一轮采集口径", 11.5, False, MUTED, spacing=1.25, exact=True)

    mid_x = 5.35
    shape(s, MSO_SHAPE.LEFT_RIGHT_ARROW, mid_x, 2.82, 2.36, 0.26, fill=BLUE2)
    tb(s, mid_x, 3.20, 2.36, 0.30, "简报来回对一次", 11.5, True, BLUE, PP_ALIGN.CENTER)
    pairs = [("机器交出", "打分后的候选和核对过的案例"),
             ("人交回", "哪些进了简报、哪些没用上")]
    for i, (t, d) in enumerate(pairs):
        y = 3.70 + i * 0.62
        tb(s, mid_x, y - 0.02, 2.36, 0.28, t, 11.5, True, MUTED, PP_ALIGN.CENTER)
        tb(s, mid_x - 0.16, y + 0.28, 2.36, 0.30, d, 11, False, INK, PP_ALIGN.CENTER, exact=True)

    bar = card(s, M, 6.30, 11.73, 0.44, fill=LIGHT, line=None)
    tb(s, 1.04, 6.38, 11.2, 0.28,
       "机器负责把脏活做稳，人负责把方向定准；两个圈各转各的，转得久了口径就对上了。",
       12, False, BLUE)
    footer(s)


NOTES = [
    "我们做行内新媒体渠道建设，同业动态和监管口径都得跟。以前两条路：各自刷公众号拼周报，十人每周耗二十小时；或者领导临时丢来截图，大家熬夜凑材料。都是被动应付，派任务才动手。",
    "moni 把它做成一个循环：定时抓来文章，过滤后交模型打分，核对过的存成案例，打包成简报发出去。最后看哪些被采纳、哪些词常误抓，回头调信源和关键词。",
    "这是每天打开的工作台。上面按业务方向分类，每篇文章抓进来就打好标签，原文时间和抓取时间分开记，标星的才送进简报。",
    "内容处理分三步：十八路信源定时抓；黑名单按关键词挡掉无关文章；本地规则剔掉尾巴和乱码。整层不调模型，几千篇几秒跑完。",
    "评分拆两套。本地规则先算零到五分质量分；出简报时模型再打零到一百分价值分。模型先摘原文证据再打分，只评单个维度，总分由程序按十个权重算。",
    "模型摘要写得再顺也不能直接用，它常记混机构名、算错数字。好文章置顶后进核验，五条摘要每条先给判断再附原文出处，对不上就打回重做。",
    "有疑问可以直接问这一篇。打开文章跟模型对话，做摘要、核查或数据复核，答案带编号能跳回原文。问出来的结论能存成附注跟着文章走，进简报时按需带出。",
    "看完的文章拆成案例：机构、动作、时间、合作方和原文出处，全部带编号。按银行筛一下，同类动作横着摆，每条都能点回原文。",
    "同一批内容可以出八种格式：群里发信息图，开会用报告图或网页，正式汇报用 PDF，存档用 Markdown，目前已经交付一百零五份。",
    "敏感信息不出内网。出站请求都在本机发出前先遮蔽，模型只看得到编码，回包再本地还原。漏遮就在出网前拦下，模型一次不调。",
    "脱敏实验室已经并进配置页签，配置和验证在同一页。贴一段文本点模型实测，四栏同时出结果，第三栏是真实模型回包，全程只有编码。",
    "每次调用都记账：出站明文命中零、写库残留零、还原成功率，一条查询当场算清。网关只认固定词表，这里每步都能现场验。",
    "目前十五路活跃信源、六千七百二十七篇入库、四百三十二条案例覆盖三百五十六家机构。八月十号拿五篇真实文章对比，人工三十一点六分钟，系统两分半。",
    "这套东西不只跟同业。采集、打分、案例、简报不挑题材，换信源和关键词就能给别的条线用，比如消保和合规团队用来盯监管通报和同业罚单，提前预警。",
    "机器管清洗、打分、脱敏和定时任务，改规则先跑固定考卷，指标不降才换线上。人早上看简报、会上做判断、回头补关键词，靠同一批简报对齐。",
]


def main():
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    for fn in (p01, p02, p03, p04, p05, p06, p07, p08, p09,
               p10, p11, p12, p13, p14, p15):
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
