# -*- coding: utf-8 -*-
"""moni 决赛汇报 PPT v16 构建脚本（15 页，浅色，大字号，左轨 1/4）"""
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
OUT = BASE + "/决赛汇报-v16.pptx"

FONT = "Heiti SC"
BG = RGBColor(0xF7, 0xF8, 0xFA)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
BLUE = RGBColor(0x00, 0x4B, 0x8D)
BLUE2 = RGBColor(0x11, 0x63, 0xA8)
LIGHT = RGBColor(0xEA, 0xF1, 0xF8)
GOLD = RGBColor(0xA9, 0x76, 0x1F)
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


def fill_text(sh, text, size=14, bold=False, color=INK,
              align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.0, after=0):
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
       align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.0, after=0):
    sh = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    return fill_text(sh, text, size, bold, color, align, anchor, spacing, after)


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
    fill_text(sh, str(n), size, True, color, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
    return sh


def numdot(s, x, y, n, fill=LIGHT, color=BLUE2, d=0.30, size=11):
    sh = shape(s, MSO_SHAPE.OVAL, x, y, d, d, fill=fill)
    fill_text(sh, str(n), size, True, color, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
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
    tb(s, M, 0.46, 7.4, 0.28, "%s   %s" % (num, kicker), 11.5, True, MUTED)
    tb(s, M, 0.74, 10.6, 0.60, title, 28, True, BLUE)
    shape(s, MSO_SHAPE.RECTANGLE, M, 1.40, 0.86, 0.045, fill=BLUE)
    tb(s, 10.90, 0.49, 1.63, 0.28, "%02d/%d" % (pageno, TOTAL), 11, True, MUTED, PP_ALIGN.RIGHT)


def footer(s, text="moni · 新媒体渠道同业跟踪调研 AI 工作台"):
    hline(s, M, 6.94, 11.73)
    tb(s, M, 7.04, 9.0, 0.26, text, 9.5, False, MUTED)


def demo_tag(s, x, y, text, w=2.10):
    sh = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, 0.34, fill=GOLDL, line=GOLD, lw=1.0, radius=0.05)
    fill_text(sh, text, 11, True, GOLD, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)


STEPS = ["内容处理", "价值评估", "提炼核验", "提取案例", "分享简报"]
SUBS = ["15 路信源，本机过滤", "0–100，低分不删", "结论带出处，逐条对原文",
        "五字段，带编号", "七种格式，按场景发"]


def rail(s, active, branch=False, branch_at=3):
    x, top = RAIL_X, 1.86
    gap = 0.88 if branch else 1.02
    if isinstance(active, int):
        active = (active,)
    tag = str(active[0]) if len(active) == 1 else "%d-%d" % (active[0], active[-1])
    tb(s, x, 1.56, RAIL_TW + 0.08, 0.22, "流程 · %s/5" % tag, 9, True, MUTED)

    ys = []
    y = top
    for i in range(1, 6):
        ys.append(y)
        y += gap + (0.46 if branch and i == branch_at else 0.0)

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
        fill_text(dot, str(i), 8, True, numc, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
        tb(s, x + 0.34, yy - 0.02, RAIL_TW, 0.24, STEPS[i - 1], 10.5, name_b, name_c)
        tb(s, x + 0.34, yy + 0.21, RAIL_TW, 0.22, SUBS[i - 1], 8, False, sub_c)
        if branch and i == branch_at:
            by = yy + 0.44
            shape(s, MSO_SHAPE.RECTANGLE, cx, by + 0.15, 0.32, 0.012, fill=GOLD)
            bx = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x + 0.34, by, RAIL_TW - 0.10, 0.32,
                       fill=GOLDL, line=GOLD, radius=0.03)
            fill_text(bx, "深度阅读 · 分支", 8.5, True, GOLD, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)


# ---------- P01 ----------
def p01(prs):
    s = new_slide(prs)
    header(s, "01", "为什么做", "同业情报靠人工盯盘，被动响应且难沉淀", 1)
    top = [
        ("我们的职责", "帮业务条线把行外动态变成手头的参考依据"),
        ("要盯什么", "同业的新产品、营销活动、监管口径和渠道打法"),
        ("为什么必须盯", "同业动作快、渠道散，漏掉一次可能慢半拍"),
    ]
    for i, (lab, val) in enumerate(top):
        x = M + i * 4.02
        card(s, x, 1.60, 3.82, 1.34)
        shape(s, MSO_SHAPE.RECTANGLE, x, 1.60, 0.05, 1.34, fill=BLUE)
        tb(s, x + 0.22, 1.74, 3.4, 0.24, lab, 11, True, MUTED)
        tb(s, x + 0.22, 2.04, 3.42, 0.80, val, 13, True, INK, spacing=1.25)

    hline(s, M, 3.16, 11.73)

    lanes = [
        ("路径 A", "日常盯盘", [
            ("刷", "各自关注几十个公众号"),
            ("挑", "凭经验筛出有用的"),
            ("摘", "手动摘机构和数字"),
            ("拼", "拼成周报发到群里"),
            ("沉", "群文件沉底就结束"),
        ]),
        ("路径 B", "临时交办", [
            ("接", "领导或分行丢来友行截图"),
            ("搜", "临时分头全网找同业做法"),
            ("凑", "熬夜拼一份应急对照"),
            ("讲", "汇报完就散场"),
        ]),
    ]
    for li, (tag, name, steps) in enumerate(lanes):
        x = M + li * 3.72
        w = 3.52
        card(s, x, 3.34, w, 3.36, fill=WHITE)
        tb(s, x + 0.20, 3.46, 1.20, 0.26, tag, 11, True, BLUE)
        tb(s, x + 0.20, 3.72, w - 0.40, 0.30, name, 15, True, INK)
        hline(s, x + 0.20, 4.08, w - 0.40)
        for i, (n, d) in enumerate(steps):
            y = 4.18 + i * 0.48
            numdot(s, x + 0.20, y + 0.03, i + 1, d=0.24, size=9)
            tb(s, x + 0.52, y, 0.42, 0.26, n, 11.5, True, BLUE2)
            tb(s, x + 0.96, y + 0.02, w - 1.16, 0.26, d, 9.5, False, MUTED)

    x = M + 2 * 3.72
    w = 11.73 - 2 * 3.72
    card(s, x, 3.34, w, 3.36, fill=LIGHT, line=None)
    tb(s, x + 0.22, 3.46, w - 0.44, 0.30, "两条线跑下来的问题", 14, True, BLUE)
    pains = [
        ("等上面派任务", "上面交办才动手找材料，平时没有常态盯盘的办法"),
        ("每周约二十人时", "十人团队各花两小时，大半时间耗在筛水文上"),
        ("口径容易打架", "人工摘录凭个人经验，同一家机构的动作前后记不一致"),
        ("材料发完就沉底", "群聊和本地文档没法复用，下次遇到还得从头找"),
    ]
    for i, (t, d) in enumerate(pains):
        y = 3.86 + i * 0.68
        tb(s, x + 0.22, y, w - 0.44, 0.26, t, 12, True, INK)
        tb(s, x + 0.22, y + 0.26, w - 0.44, 0.30, d, 9.5, False, MUTED)
    footer(s)


# ---------- P02 ----------
def p02(prs):
    s = new_slide(prs)
    header(s, "02", "工作流", "六步转成一个圈，转完一轮回到采集端", 2)
    nodes = [
        ("自动化采集", "18 路信源定时入库"),
        ("解析与价值评估", "本地过滤后交模型"),
        ("同业案例沉淀", "核对后存成结构化案例"),
        ("简报分发", "按方向打包发给团队"),
        ("辅助决策", "立项营销拿它当参考"),
        ("校准采集策略", "采纳情况回改信源词"),
    ]
    n = len(nodes)
    w, gap = 1.78, 0.19
    x0 = M + 0.06
    top = 1.66
    for i, (t, d) in enumerate(nodes):
        x = x0 + i * (w + gap)
        hot = i == 5
        card(s, x, top, w, 1.10, fill=WHITE)
        shape(s, MSO_SHAPE.RECTANGLE, x, top, w, 0.045, fill=GOLD if hot else BLUE)
        tb(s, x + 0.16, top + 0.22, w - 0.32, 0.56, t, 12.5, True, GOLD if hot else INK, spacing=1.15)
        tb(s, x + 0.16, top + 1.20, w - 0.28, 0.54, d, 9.5, False, MUTED, spacing=1.2)
        if i < n - 1:
            shape(s, MSO_SHAPE.RIGHT_ARROW, x + w + 0.02, top + 0.45, 0.15, 0.18, fill=LINE)

    line_y = 3.66
    x_first = x0 + w / 2
    x_last = x0 + (n - 1) * (w + gap) + w / 2
    shape(s, MSO_SHAPE.RECTANGLE, x_last - 0.006, top + 1.16, 0.012, line_y - top - 1.16, fill=BLUE)
    shape(s, MSO_SHAPE.RECTANGLE, x_first, line_y, x_last - x_first, 0.012, fill=BLUE)
    shape(s, MSO_SHAPE.RECTANGLE, x_first - 0.006, top + 1.16, 0.012, line_y - top - 1.16, fill=BLUE)
    shape(s, MSO_SHAPE.UP_ARROW, x_first - 0.08, top + 1.02, 0.16, 0.18, fill=BLUE)
    chip = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 6.02, line_y - 0.16, 1.72, 0.32,
                 fill=WHITE, line=BLUE, lw=1.0, radius=0.05)
    fill_text(chip, "校准回流", 10, True, BLUE, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)

    moni = card(s, 5.66, 4.00, 2.06, 0.86, fill=BLUE, line=None)
    fill_text(moni, "moni 工作台", 14, True, WHITE, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
    tb(s, 4.90, 4.94, 3.58, 0.28, "采、评、存、发都在这一层", 9.5, False, MUTED, PP_ALIGN.CENTER)

    bar = card(s, M, 5.42, 11.73, 1.24, fill=LIGHT, line=None)
    tb(s, 1.04, 5.58, 11.2, 0.32, "前三步取材，中间两步判断，最后一步回头改采集口径。", 13, True, BLUE)
    tb(s, 1.04, 5.98, 11.2, 0.60,
       "圈里的每一步都有落点：采集有信源清单，评估有打分尺，案例有字段，简报有模板；哪一步做得不好，改配置就能重跑，不必推倒重来。",
       10.5, False, MUTED, spacing=1.25)
    footer(s)


# ---------- P03 ----------
def p03(prs):
    s = new_slide(prs)
    header(s, "03", "工作台", "一屏总览全局动态，高频操作直达手边", 3)
    ix, iy, iw, ih = M, 1.60, 7.55, 4.72
    pic(s, V16 + "/desk.png", ix, iy, w=iw)
    shape(s, MSO_SHAPE.RECTANGLE, ix, iy, iw, ih, fill=None, line=LINE, lw=1.0)

    marks = [
        (0.905, 0.175, "顶部分桶", "按业务方向分桶，银行、AI×金融、微信生态各有各的文章"),
        (0.135, 0.272, "收藏区", "Pin 过 48 篇，只有收藏过的才进简报池"),
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
        tb(s, ex + 0.36, ey, ew - 0.36, 0.28, t, 12.5, True, INK)
        tb(s, ex + 0.36, ey + 0.30, ew - 0.36, 0.62, d, 10, False, MUTED, spacing=1.25)
        if i < len(marks) - 1:
            hline(s, ex, ey + row_h - 0.10, ew)

    bar = card(s, M, 6.48, 11.73, 0.34, fill=LIGHT, line=None)
    tb(s, 1.04, 6.53, 11.2, 0.26,
       "截图取自 moni 生产环境 8900 的文章查阅页；图中编号对应右侧说明。原文、案例、简报都存在本机磁盘，数据不出终端。",
       9.5, False, BLUE)
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
            "接口失效可在后台直接停用",
        ]),
        ("规则过滤", V16 + "/rules.png", [
            "黑名单按命中关键词挡掉无关",
            "主抓取、黑白名单、外部订阅共用一套匹配",
            "命中业务方向的才放行入库",
        ]),
        ("去噪清洗", V16 + "/clean.png", [
            "公众号尾巴、HTML 实体、零宽字符本地剔除",
            "空正文与短稿单独标出来",
            "这一层不调模型，几千篇几秒跑完",
        ]),
    ]
    cw = 2.72
    for i, (t, img, items) in enumerate(cols):
        x = BODY_X + i * (cw + 0.17)
        tb(s, x, 1.62, cw, 0.30, t, 15, True, BLUE)
        hline(s, x, 1.98, cw)
        p = pic(s, img, x, 2.08, h=1.62)
        pw = p.width / 914400.0
        if pw > cw:
            p.left = Inches(x + (cw - pw) / 2)
        shape(s, MSO_SHAPE.RECTANGLE, x, 2.08, cw, 1.62, fill=None, line=LINE, lw=1.0)
        for j, it in enumerate(items):
            y = 3.88 + j * 0.66
            numdot(s, x, y + 0.03, j + 1, d=0.24, size=9)
            tb(s, x + 0.32, y, cw - 0.32, 0.58, it, 10, False, INK, spacing=1.25)

    card(s, BODY_X, 5.92, BODY_W, 0.86, fill=LIGHT, line=None)
    tb(s, BODY_X + 0.22, 6.04, BODY_W - 0.44, 0.30, "这一层全部在本机跑，不调模型", 12.5, True, BLUE)
    tb(s, BODY_X + 0.22, 6.38, BODY_W - 0.44, 0.30,
       "省的是模型调用费，更重要的是送进模型的每篇正文都是干净的，后面的打分和核查才有意义。",
       10.5, False, MUTED)
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
        card(s, x, 1.62, 2.70, 0.96)
        badge(s, x + 0.14, 1.76, i + 1, d=0.26, size=10)
        tb(s, x + 0.48, 1.74, 2.10, 0.26, n, 12, True, INK)
        tb(s, x + 0.14, 2.08, 2.42, 0.44, d, 9.5, False, MUTED, spacing=1.15)

    pic(s, ASSETS + "/brief-card.png", BODY_X, 2.78, w=3.55)
    for i, fy in enumerate([0.343, 0.600, 0.825]):
        badge(s, BODY_X + 3.20, 2.78 + 3.55 * fy - 0.15, i + 1, d=0.28, size=10)
    tb(s, BODY_X + 0.02, 6.50, 3.60, 0.28, "简报里每条结论都带着它的分和出处", 9.5, False, MUTED)

    tx = BODY_X + 3.78
    tw = BODY_W - 3.78
    tb(s, tx, 2.72, tw, 0.30, "打分是可复核的流水线", 14, True, BLUE)
    rules = [
        ("本地与模型分离", "质量分纯本地算，价值分才调模型"),
        ("先取证后打分", "模型必须先摘原文依据，再给维度分"),
        ("总分由代码算", "模型只评单维，加权总分代码算，防它乱加减"),
        ("审出问题就重打", "独立审计发现矛盾发回重做，一篇最多五次调用"),
        ("只在出简报时跑", "平时不空耗算力"),
        ("低分只往后排", "低分不删，留着随时回头查证"),
    ]
    for i, (t, d) in enumerate(rules):
        y = 3.14 + i * 0.58
        numdot(s, tx, y + 0.04, i + 1, d=0.26, size=9)
        tb(s, tx + 0.36, y, 1.70, 0.26, t, 11.5, True, INK)
        tb(s, tx + 2.10, y + 0.02, tw - 2.10, 0.48, d, 10, False, MUTED, spacing=1.2)
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
    tb(s, BODY_X, 5.20, 3.30, 0.70, "每条摘要前面是判断，后面是原文出处；核对不过的那条会被打回重做。",
       10, False, MUTED, spacing=1.25)

    tx = BODY_X + 3.57
    tw = BODY_W - 3.57
    items = [
        ("置顶才进核验", "人工挑出优质文章置顶，系统才启动结构化提炼与核对"),
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

    card(s, BODY_X, 5.16, BODY_W, 1.62, fill=LIGHT, line=None)
    tb(s, BODY_X + 0.22, 5.30, BODY_W - 0.44, 0.30, "为什么非要核这一遍", 13, True, BLUE)
    tb(s, BODY_X + 0.22, 5.66, BODY_W - 0.44, 0.94,
       "大模型常把同业名称记混，或者把原文里的数字算错。这类错在阅读时看不出来，一旦进了汇报材料，"
       "就会变成我们对外的说法。所以入库的每条案例都要先过这一遍，存进库的四百三十二条案例才站得住。",
       11, False, INK, spacing=1.35)
    footer(s)


# ---------- P07 ----------
def p07(prs):
    s = new_slide(prs)
    header(s, "07", "环节三 · 深度阅读", "单篇深读直接提问，结论打包带进简报", 7)
    rail(s, 3, branch=True, branch_at=3)
    pic(s, ASSETS + "/rd-ask.png", BODY_X, 1.66, w=3.70)
    shape(s, MSO_SHAPE.RECTANGLE, BODY_X, 1.66, 3.70, 1.58, fill=None, line=LINE, lw=1.0)
    pic(s, ASSETS + "/rd-cite.png", BODY_X, 3.44, h=2.58)
    shape(s, MSO_SHAPE.RECTANGLE, BODY_X, 3.44, 2.78, 2.58, fill=None, line=LINE, lw=1.0)
    tb(s, BODY_X, 6.12, 3.70, 0.48, "上：对着原文直接问　下：结论带编号，点编号跳回原文", 9.5, False, MUTED, spacing=1.2)

    tx = BODY_X + 4.06
    tw = BODY_W - 4.06
    feats = [
        ("针对单篇直接对话", "在文章页直接做摘要、事实核查或数据复核，不用把正文再喂一遍"),
        ("问出来的结论跟着文章走", "打包成这篇文章的上下文，后面选进简报时按需一起带出去"),
        ("结论带编号可溯源", "每条回答都带引用编号，点一下跳回原文对应段落"),
    ]
    for i, (t, d) in enumerate(feats):
        y = 1.66 + i * 0.86
        numdot(s, tx, y + 0.04, i + 1, d=0.26, size=10)
        tb(s, tx + 0.36, y, tw - 0.36, 0.28, t, 12.5, True, INK)
        tb(s, tx + 0.36, y + 0.30, tw - 0.36, 0.48, d, 10, False, MUTED, spacing=1.2)

    card(s, tx, 4.34, tw, 2.28, fill=LIGHT, line=None)
    tb(s, tx + 0.20, 4.46, tw - 0.40, 0.28, "六个预设问题，点一下就开始", 12, True, BLUE)
    qs = [
        "这次改版动了哪个产品流程或收费规则",
        "增长数据的统计口径和时间跨度是什么",
        "面向哪类客户，对客门槛和权益成本多少",
        "按我们行的系统和合规条件，最大的卡点在哪",
        "只借鉴一两个小动作，最快能在哪个渠道试跑",
        "这是一次性活动还是长期机制，值得跟进多久",
    ]
    for i, q in enumerate(qs):
        x = tx + 0.20 + (i % 2) * 2.12
        y = 4.82 + (i // 2) * 0.56
        sh = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, 1.98, 0.50, fill=WHITE, line=LINE, radius=0.05)
        fill_text(sh, q, 8.5, False, INK, PP_ALIGN.LEFT, MSO_ANCHOR.MIDDLE, spacing=1.05)
        sh.text_frame.margin_left = 60960
        sh.text_frame.margin_right = 45720
    demo_tag(s, 10.43, 1.22, "▶ 现场演示 15 秒", 2.10)
    footer(s)


# ---------- P08 ----------
def p08(prs):
    s = new_slide(prs)
    header(s, "08", "环节四 · 提取案例", "看完的文章，拆成能横向比的案例", 8)
    rail(s, 4)
    pic(s, V16 + "/cases.png", BODY_X, 1.66, w=4.55)
    shape(s, MSO_SHAPE.RECTANGLE, BODY_X, 1.66, 4.55, 3.53, fill=None, line=LINE, lw=1.0)
    tb(s, BODY_X, 5.30, 4.55, 0.48, "案例库页截图（2026-11-25，生产环境）：编号、主体、动作、时间和对手方都在一屏",
       9, False, MUTED, spacing=1.2)

    tx = BODY_X + 4.80
    tw = BODY_W - 4.80
    tb(s, tx, 1.66, tw, 0.30, "案例长什么样", 13.5, True, BLUE)
    fields = ["编号 MN-AI-…", "主体：哪一家机构", "动作：做了什么", "时间与对手方", "证据原文"]
    for i, f in enumerate(fields):
        y = 2.06 + i * 0.44
        shape(s, MSO_SHAPE.RECTANGLE, tx, y + 0.06, 0.05, 0.24, fill=BLUE)
        tb(s, tx + 0.18, y + 0.02, tw - 0.18, 0.28, f, 11, False, INK)
    hline(s, tx, 4.36, tw)
    tb(s, tx, 4.50, tw, 0.30, "这样存有什么用处", 13.5, True, BLUE)
    uses = [
        ("横向比", "同类动作摆到一张表里，谁先上、谁做得深看得出来"),
        ("按机构回查", "想知道某家银行做过什么，按主体筛一下就有"),
        ("带出处", "每条绑着原文段落，点开能回到报道现场"),
    ]
    for i, (t, d) in enumerate(uses):
        y = 4.90 + i * 0.66
        numdot(s, tx, y + 0.03, i + 1, d=0.24, size=9)
        tb(s, tx + 0.32, y, 1.30, 0.26, t, 11, True, INK)
        tb(s, tx + 1.66, y + 0.02, tw - 1.66, 0.52, d, 9.5, False, MUTED, spacing=1.2)
    demo_tag(s, 10.43, 1.22, "▶ 现场演示 10 秒", 2.10)
    footer(s)


# ---------- P09 ----------
def p09(prs):
    s = new_slide(prs)
    header(s, "09", "环节五 · 分享简报", "同一批内容，出七种格式，按场合分发", 9)
    rail(s, 5)
    pic(s, V16 + "/brief.png", BODY_X, 1.66, w=5.10)
    shape(s, MSO_SHAPE.RECTANGLE, BODY_X, 1.66, 5.10, 4.26, fill=None, line=LINE, lw=1.0)
    tb(s, BODY_X, 6.02, 5.10, 0.44, "实际交付的简报成品：标题、分值、来源、日期、摘要和待核实项同在一屏",
       9, False, MUTED, spacing=1.2)

    tx = BODY_X + 5.36
    tw = BODY_W - 5.36
    tb(s, tx, 1.66, tw, 0.28, "什么场合用什么格式", 12.5, True, BLUE)
    rows = [
        ("微信群里同步", "信息图 PNG"),
        ("日会周会投屏", "报告图 PNG"),
        ("当面演示", "单文件 HTML"),
        ("正式汇报", "PDF"),
        ("知识库存档", "Markdown"),
    ]
    for i, (a, b) in enumerate(rows):
        y = 2.06 + i * 0.50
        tb(s, tx, y, 1.70, 0.28, a, 10, False, MUTED)
        tb(s, tx + 1.74, y, 1.40, 0.28, b, 11, True, INK)
        hline(s, tx, y + 0.38, tw)

    card(s, tx, 4.72, tw, 1.14, fill=LIGHT, line=None)
    tb(s, tx + 0.20, 4.84, tw - 0.40, 0.26, "已交付", 10, False, BLUE)
    tb(s, tx + 0.20, 5.06, 1.50, 0.52, "105 份", 24, True, BLUE)
    tb(s, tx + 1.76, 5.18, 1.40, 0.26, "成品简报", 9.5, False, BLUE)
    tb(s, tx, 6.02, tw, 0.56, "早上跑完，日会直接投；会后发链接，点标题还能回到原文。",
       10, False, MUTED, spacing=1.25)
    footer(s)


# ---------- P10 ----------
def p10(prs):
    s = new_slide(prs)
    header(s, "10", "出站防护", "敏感信息不出内网，模型只见编码不见真名", 10)
    chip = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 9.86, 1.20, 2.67, 0.34,
                 fill=GOLDL, line=GOLD, radius=0.05)
    fill_text(chip, "内核已上线 · 开关默认关", 10, True, GOLD, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
    tb(s, M, 1.56, 8.60, 0.26, "moni 调外部模型的 5 条通道，全部收口在进程内、发出之前。", 12, False, MUTED)

    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 0.72, 1.94, 5.88, 1.24, fill=LIGHT, line=None, radius=0.06)
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 6.72, 1.94, 1.88, 1.24, fill=GOLDL, line=GOLD, radius=0.06)
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 8.68, 1.94, 3.92, 1.24, fill=LIGHT, line=None, radius=0.06)
    tb(s, 0.88, 1.98, 2.00, 0.22, "本机 · 出站前", 9, True, BLUE2)
    tb(s, 6.88, 1.98, 1.20, 0.22, "外部", 9, True, GOLD)
    tb(s, 8.84, 1.98, 2.20, 0.22, "本机 · 回站后", 9, True, BLUE2)

    boxes = [
        ("原文", "正文、机构、结论", False),
        ("出站遮蔽", "敏感项换成编码", False),
        ("本机对照表", "编码对应原值", False),
        ("大模型", "只看得到编码", True),
        ("本地还原", "回包换回原值", False),
        ("用户", "看到的是明文", False),
    ]
    for i, (n, d, hot) in enumerate(boxes):
        x = 0.80 + i * 2.00
        if hot:
            sh = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, 2.24, 1.72, 0.62,
                       fill=BLUE, line=None, radius=0.05)
            fill_text(sh, n, 11.5, True, WHITE, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
            tb(s, x, 2.90, 1.72, 0.22, d, 8.5, False, GOLD, PP_ALIGN.CENTER)
        else:
            sh = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, 2.24, 1.72, 0.62,
                       fill=WHITE, line=LINE, radius=0.05)
            fill_text(sh, n, 11.5, True, INK, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
            tb(s, x, 2.90, 1.72, 0.22, d, 8.5, False, MUTED, PP_ALIGN.CENTER)
        if i < 5:
            cross = i in (2, 3)
            shape(s, MSO_SHAPE.RIGHT_ARROW, x + 1.76, 2.45, 0.20, 0.20,
                  fill=GOLD if cross else LINE)

    facts = [
        ("同一机构同一编码", "同一家机构在请求里恒定同一个代号，模型不会看乱。"),
        ("联网核查按策略放行", "需要公网查证时保留公开机构名，否则公网搜不到结果。"),
        ("遮不干净就拦住", "出网前再扫一遍，仍有明文就拦下，模型一次都不调用，账本记一条。"),
        ("异常时先放行", "脱敏器自己报错时先放行，不让分析停摆；需要时可以切到失败即停。"),
    ]
    for i, (n, d) in enumerate(facts):
        x = M + i * 2.97
        card(s, x, 3.36, 2.82, 1.50)
        tb(s, x + 0.18, 3.50, 2.46, 0.28, n, 12, True, BLUE)
        tb(s, x + 0.18, 3.84, 2.46, 0.92, d, 9.5, False, MUTED, spacing=1.25)

    card(s, M, 5.04, 3.90, 1.74)
    tb(s, 1.04, 5.16, 3.42, 0.56, "≤ 2.5 ms", 30, True, BLUE)
    tb(s, 1.04, 5.80, 3.42, 0.72, "六千字中文研报，遮蔽加还原的 P95（CPU 时间口径）", 9.5, False, MUTED, spacing=1.25)

    card(s, 4.86, 5.04, 3.40, 1.74)
    tb(s, 5.10, 5.16, 2.92, 0.56, "0", 30, True, BLUE)
    tb(s, 5.10, 5.80, 2.92, 0.72, "真实模型端到端跑完一趟，出站明文命中数", 9.5, False, MUTED, spacing=1.25)

    card(s, 8.42, 5.04, 4.11, 1.74)
    tb(s, 8.66, 5.16, 3.63, 0.30, "开关默认关，生产库当前 0 行", 13, True, BLUE)
    tb(s, 8.66, 5.52, 3.63, 0.96, "页面上的数字来自 sprint-68 实测。演练台已上线生产，本页只讲内核。",
       10, False, MUTED, spacing=1.3)
    footer(s)


# ---------- P11 ----------
def p11(prs):
    s = new_slide(prs)
    header(s, "11", "演练台", "四栏并排，模型从头到尾只见过编码", 11)
    tb(s, M, 1.58, 9.30, 0.26, "演练台已上线生产（v1.0.128）：贴一段文本，点一次真跑，四个栏同时出结果。", 12, False, MUTED)
    demo_tag(s, 10.43, 1.22, "▶ 现场演示 25 秒", 2.10)

    cols = [
        ("①", "原文",
         "招商银行与兴业银行同日发布公告：招商银行客服电话 13800138000，联系人邮箱 analyst@bankcorp.cn。"
         "请只复述上述联系方式，不要展开分析。",
         "这是我们库里的一篇真文章"),
        ("②", "发给模型的内容",
         "{{ORG_jcwwdnrn}}与{{ORG_lq4cr2iu}}同日发布公告：{{ORG_jcwwdnrn}}客服电话 {{PHONE_pbn2bfug}}，"
         "联系人邮箱 {{EMAIL_colbtckw}}。请只复述上述联系方式，不要展开分析。",
         "模型只看得到编码"),
        ("③", "模型回包（原始）",
         "{{ORG_jcwwdnrn}} 与 {{ORG_lq4cr2iu}} 同日发布公告：{{ORG_jcwwdnrn}}客服电话 "
         "{{PHONE_pbn2bfug}}，联系人邮箱 {{EMAIL_colbtckw}}。",
         "回话里也只有编码"),
        ("④", "本地还原后",
         "招商银行 与 兴业银行 同日发布公告：招商银行客服电话 13800138000，联系人邮箱 analyst@bankcorp.cn。",
         "用户看到的还是明文"),
    ]
    for i, (n, t, body, say) in enumerate(cols):
        x = M + i * 2.97
        card(s, x, 1.94, 2.81, 2.05)
        badge(s, x + 0.14, 2.08, n, d=0.26, size=10)
        tb(s, x + 0.46, 2.08, 2.24, 0.26, t, 11.5, True, INK)
        tb(s, x + 0.14, 2.44, 2.53, 1.06, body, 9, False, INK, spacing=1.15)
        tb(s, x + 0.14, 3.54, 2.53, 0.32, say, 9.5, True, GOLD)

    metrics = [("5", "遮蔽处数 · 136 µs"), ("5", "还原处数 · 98 µs"), ("0", "未还原"),
               ("0", "出站泄漏命中"), ("0", "残留占位符"), ("8775 ms", "模型耗时")]
    for i, (n, lab) in enumerate(metrics):
        x = M + i * 1.83
        card(s, x, 4.14, 1.69, 0.86, fill=LIGHT, line=None)
        tb(s, x, 4.24, 1.69, 0.40, n, 18 if i < 5 else 15, True, BLUE, PP_ALIGN.CENTER)
        tb(s, x, 4.68, 1.69, 0.24, lab, 9, False, MUTED, PP_ALIGN.CENTER)

    pic(s, ASSETS + "/lab-cols.png", M, 5.16, w=5.15)
    tb(s, 6.15, 5.10, 6.38, 0.26, "这是 moni 生产环境 8900 上跑的演练台，不是示意图，模型是真实模型。", 11, False, MUTED)
    tb(s, 6.15, 5.40, 6.38, 0.52, "现场：点一次真跑，等模型回话，依次划过第二第三栏，再指第四栏和两个零。",
       11.5, True, BLUE, spacing=1.25)
    tb(s, 6.15, 5.94, 6.38, 0.52, "这页走的是生产同一条内核：遮不干净时出网前就拦，模型一次都不调用。",
       11.5, True, BLUE, spacing=1.25)
    tb(s, 6.15, 6.48, 6.38, 0.36, "地址 127.0.0.1:8900/static/desens-lab.html；账本分生产与实验室两行口径，示例号码与邮箱为虚构值。",
       9.5, False, GOLD, spacing=1.25)
    footer(s)


# ---------- P12 ----------
def p12(prs):
    s = new_slide(prs)
    header(s, "12", "可证伪", "三项指标每次调用都记账，数字能现场复算", 12)
    tb(s, M, 1.58, 11.73, 0.26, "遮蔽做得好不好，不靠说法，靠账本。账本里只存编码，不存原值。", 12, False, MUTED)

    card(s, M, 1.96, 6.05, 4.04)
    tb(s, 1.02, 2.10, 5.60, 0.30, "和通用密钥型脱敏的区别", 14.5, True, BLUE)
    tb(s, 2.20, 2.52, 2.05, 0.24, "通用密钥型（网关层）", 9.5, True, MUTED)
    tb(s, 4.42, 2.52, 2.28, 0.24, "moni（业务进程内）", 9.5, True, BLUE)
    shape(s, MSO_SHAPE.RECTANGLE, 4.30, 2.46, 0.012, 2.94, fill=LINE)
    cmp_rows = [
        ("遮什么", "密钥、密码、邮箱、证件号", "以上，外加同业机构全简称、内部结论、关注名单"),
        ("在哪处理", "模型网关统一过一道", "模型调用前最后一跳，知道任务类型与文章"),
        ("能不能复算", "有遮蔽条数，没有还原成功率", "还原成功率、泄漏命中、残留占位符"),
    ]
    for i, (d, a, b) in enumerate(cmp_rows):
        y = 2.92 + i * 0.90
        tb(s, 1.02, y, 1.10, 0.26, d, 10, True, GOLD)
        tb(s, 2.20, y - 0.02, 2.05, 0.78, a, 9.5, False, MUTED, spacing=1.25)
        tb(s, 4.42, y - 0.02, 2.28, 0.78, b, 9.5, False, INK, spacing=1.25)
        if i < 2:
            hline(s, 1.02, y + 0.80, 5.62)
    tb(s, 1.02, 5.56, 5.62, 0.32, "同业判断属于业务口径，按密钥规则匹配不到，只能在业务进程内补。", 10.5, False, BLUE)

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
        tb(s, 8.58, y + 0.02, 3.70, 0.28, t, 11.5, True, INK)
        tb(s, 8.58, y + 0.32, 3.70, 0.48, d, 9.5, False, MUTED, spacing=1.2)
    sql = card(s, 7.25, 5.20, 5.06, 0.60, fill=LIGHT, line=None)
    tb(s, 7.40, 5.28, 4.80, 0.46,
       "SELECT sum(masked_total), sum(restored_total), sum(unrestored_count),\n"
       "  sum(leak_scan_hits), sum(residual_placeholders) FROM desensitize_events;",
       8.5, False, BLUE, spacing=1.2)

    bar = card(s, M, 6.14, 11.73, 0.62, fill=LIGHT, line=None)
    tb(s, 1.04, 6.26, 11.2, 0.30, "口径来自审计表 desensitize_events。演练台已就绪，这条 SQL 现场就能跑。",
       11, False, BLUE)
    footer(s)


# ---------- P13 ----------
def p13(prs):
    s = new_slide(prs)
    header(s, "13", "成果与收益", "五篇实测提速十二倍，八个业务方向持续输出", 13)
    stats = [("15", "路信源", "涵盖公众号、行业媒体、官方平台等多渠道"),
             ("6,727", "篇入库同业信息", "自动抓取清洗入库，不靠手工翻找"),
             ("432", "条同业优质案例", "经核验沉淀，其中 358 条已确认"),
             ("356", "家机构", "案例主体去重后的覆盖范围")]
    for i, (n, lab, sub) in enumerate(stats):
        x = M + (i % 2) * 3.10
        y = 1.62 + (i // 2) * 1.50
        card(s, x, y, 2.92, 1.34)
        tb(s, x + 0.20, y + 0.08, 2.52, 0.52, n, 29, True, BLUE)
        tb(s, x + 0.20, y + 0.62, 2.52, 0.26, lab, 11, True, INK)
        tb(s, x + 0.20, y + 0.90, 2.52, 0.36, sub, 8.5, False, MUTED, spacing=1.15)

    tb(s, M, 4.64, 6.02, 0.26, "同批 5 篇真实文章 · 2026-08-10 实测", 11, True, BLUE)
    card(s, M, 4.94, 6.02, 0.74, fill=LIGHT, line=None)
    tb(s, 1.00, 5.02, 5.62, 0.60,
       "实验条件：取当日入库的 5 篇真实同业文章。人工组逐篇读完并摘出要点、机构与数字；"
       "系统组跑完同一批的打分、摘要与核验。同一批文章、同一台机器、同一口径。",
       9, False, INK, spacing=1.25)
    tb(s, M, 5.84, 1.00, 0.28, "人工", 11, False, MUTED)
    shape(s, MSO_SHAPE.RECTANGLE, 1.62, 5.82, 4.30, 0.32, fill=LINE)
    tb(s, 6.06, 5.86, 1.00, 0.26, "31.6 分钟", 10.5, True, INK)
    tb(s, M, 6.26, 1.00, 0.28, "系统", 11, False, MUTED)
    shape(s, MSO_SHAPE.RECTANGLE, 1.62, 6.24, 4.30 * 2.5 / 31.6, 0.32, fill=BLUE)
    tb(s, 2.10, 6.28, 1.00, 0.26, "2.5 分钟", 10.5, True, BLUE)
    tb(s, M, 6.66, 6.02, 0.26, "同样五篇，从半小时压到两分半，人只做判断。", 10, False, MUTED)

    vline(s, 7.18, 1.62, 5.10)
    tb(s, 7.48, 1.58, 5.05, 0.26, "已交付的简报在盯这些方向", 12.5, True, BLUE)
    topics = [
        ("小微普惠", 65, "小微授信、贴息政策、线上获客"),
        ("企业微信私域", 50, "企微加粉、客户标签与话术"),
        ("数字人民币", 36, "数币硬钱包、消费满减、商户拓展"),
        ("银行零售业务", 26, "零售客群分层、代发工资、财富转化"),
        ("App", 26, "手机银行改版、弹窗与交互"),
        ("信用卡经营", 24, "新户礼、分期费率、联名卡权益"),
        ("AI 客服", 10, "智能外呼、大模型坐席辅助"),
        ("视频号", 9, "直播获客、短视频投流与转化"),
    ]
    for i, (a, n, d) in enumerate(topics):
        y = 2.02 + i * 0.58
        tb(s, 7.48, y, 1.60, 0.26, a, 10.5, True, INK)
        tb(s, 9.10, y + 0.01, 0.46, 0.24, str(n), 9.5, True, BLUE2, PP_ALIGN.RIGHT)
        tb(s, 9.66, y + 0.01, 2.87, 0.24, d, 9, False, MUTED)
        hline(s, 7.48, y + 0.40, 5.05)
    tb(s, 7.48, 6.72, 5.05, 0.24, "括号内为 26 份已交付简报中该方向的命中行数", 8.5, False, MUTED)
    footer(s)


# ---------- P14 ----------
def p14(prs):
    s = new_slide(prs)
    header(s, "14", "不止同业", "同一套情报流水线，直接复用给行内其它团队", 14)
    tb(s, M, 1.58, 11.73, 0.28,
       "采集、价值评估、案例沉淀、简报分发这几件事本身与主题无关。换个信源清单和关键词，就能服务别的条线。",
       12, False, MUTED)
    cands = [
        ("对公客户经理", "跑客户前先看一眼，线索自己找上门",
         ["盯产业政策与重点企业扩产、拿地、发债", "晨会前推送分行业简报，见客户就有现成话题", "成交案例反过来校准关键词"]),
        ("消保与合规团队", "同业踩过的坑，提前发到全行桌面",
         ["抓监管通报与同业罚单，按风险点归类", "历史案例入库，新产品上线前查重对照", "人工标记误报漏报，收紧监测口径"]),
        ("零售产品经理", "竞品怎么改版，周报自动帮你理好",
         ["跟友行官号与评测号，剔除节日海报", "按业务模块存成改版卡片，评审随时调取", "每周五自动汇总成周报"]),
    ]
    cw = 3.72
    for i, (who, title, items) in enumerate(cands):
        x = M + i * (cw + 0.17)
        card(s, x, 2.02, cw, 4.16)
        shape(s, MSO_SHAPE.RECTANGLE, x, 2.02, cw, 0.05, fill=BLUE)
        tb(s, x + 0.24, 2.22, cw - 0.48, 0.26, who, 11, True, MUTED)
        tb(s, x + 0.24, 2.52, cw - 0.48, 0.86, title, 15, True, BLUE, spacing=1.2)
        hline(s, x + 0.24, 3.48, cw - 0.48)
        for j, it in enumerate(items):
            y = 3.64 + j * 0.78
            numdot(s, x + 0.24, y + 0.04, j + 1, d=0.24, size=9)
            tb(s, x + 0.56, y, cw - 0.80, 0.66, it, 10, False, INK, spacing=1.25)

    bar = card(s, M, 6.34, 11.73, 0.44, fill=LIGHT, line=None)
    tb(s, 1.04, 6.42, 11.2, 0.28,
       "三个方向哪个先落地，等评委和业务同事一起定；工作台本身不用改。",
       10.5, False, BLUE)
    footer(s)


# ---------- P15 ----------
def p15(prs):
    s = new_slide(prs)
    header(s, "15", "人机协作", "机器守住质量下限，人的判断力决定采集方向", 15)
    tb(s, M, 1.56, 11.73, 0.28, "两个圈各转各的，中间靠同一批简报和评测结果对齐。", 12, False, MUTED)

    cx1, cy1, r1 = 2.70, 4.16, 1.36
    shape(s, MSO_SHAPE.OVAL, cx1 - r1, cy1 - r1, r1 * 2, r1 * 2, fill=None, line=BLUE, lw=1.5)
    tb(s, 1.62, 1.90, 2.66, 0.30, "机器这一圈", 15, True, BLUE, PP_ALIGN.CENTER)
    mrows = ["改规则或提示词", "用固定考卷跑一遍", "指标不降才换线上", "旧版本留档可回退"]
    for i, r in enumerate(mrows):
        a = -90 + i * 90
        bx = cx1 + r1 * math.cos(math.radians(a)) - 0.95
        by = cy1 + r1 * math.sin(math.radians(a)) - 0.17
        sh = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, bx, by, 1.90, 0.44, fill=WHITE, line=BLUE, lw=1.2, radius=0.05)
        fill_text(sh, r, 10.5, True, INK, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
    tb(s, 1.10, 5.80, 3.70, 0.52, "数据清洗、业务价值打分、脱敏、定时任务都走这一圈", 9.5, False, MUTED, spacing=1.2)

    cx2, cy2, r2 = 10.63, 4.16, 1.36
    shape(s, MSO_SHAPE.OVAL, cx2 - r2, cy2 - r2, r2 * 2, r2 * 2, fill=None, line=MUTED, lw=1.5)
    tb(s, 9.05, 1.90, 2.66, 0.30, "人这一圈", 15, True, INK, PP_ALIGN.CENTER)
    hrows = ["早上看当天简报", "在会上拿案例做判断", "看反馈哪些判断对了", "回头补关键词再取下一批"]
    for i, r in enumerate(hrows):
        a = -90 + i * 90
        bx = cx2 + r2 * math.cos(math.radians(a)) - 0.95
        by = cy2 + r2 * math.sin(math.radians(a)) - 0.17
        sh = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, bx, by, 1.90, 0.44, fill=WHITE, line=MUTED, lw=1.2, radius=0.05)
        fill_text(sh, r, 10.5, True, INK, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
    tb(s, 8.55, 5.80, 3.70, 0.52, "判断留在人这一侧，系统照着反馈调下一轮采集口径", 9.5, False, MUTED, spacing=1.2)

    mid_x = 5.30
    shape(s, MSO_SHAPE.LEFT_RIGHT_ARROW, mid_x, 3.02, 2.80, 0.26, fill=BLUE2)
    tb(s, mid_x, 3.40, 2.80, 0.28, "简报来回对一次", 10.5, True, BLUE, PP_ALIGN.CENTER)
    pairs = [("机器交出", "打分后的候选和核对过的案例"),
             ("人交回", "哪些进了简报、哪些没用上")]
    for i, (t, d) in enumerate(pairs):
        y = 3.86 + i * 0.72
        tb(s, mid_x, y, 2.80, 0.24, t, 10, True, MUTED, PP_ALIGN.CENTER)
        tb(s, mid_x, y + 0.24, 2.80, 0.40, d, 9.5, False, INK, PP_ALIGN.CENTER, spacing=1.2)

    bar = card(s, M, 6.34, 11.73, 0.44, fill=LIGHT, line=None)
    tb(s, 1.04, 6.42, 11.2, 0.28,
       "机器负责把脏活做稳，人负责把方向定准；两个圈各转各的，转得久了口径就对上了。",
       10.5, False, BLUE)
    footer(s)


NOTES = [
    "我们做行内新媒体渠道建设，同业动态和监管口径都得跟。以前两条路：各刷公众号、摘数字拼周报，十人团队每周约二十人时；领导临时丢来截图，大家熬夜凑。都停在被动处理，上面派任务才动手。",
    "moni 把它做成一个圈：信源定时抓来，过滤后交模型打分，核对过的存成案例，打包成简报发出去。最后一步校准，哪些被采纳、哪些词常误抓，就改信源和关键词。",
    "这是每天打开的工作台。上面按业务方向分桶，每行入库打好标签，原文时间和同步时间分开记，Pin 过的才进简报池。",
    "内容处理分三步：十八路信源定时抓取；黑名单按关键词挡掉无关；本地规则剔掉公众号尾巴和乱码。整层不调模型，几千篇几秒跑完。",
    "评分拆两套。本地规则算零到五分质量分；进简报时模型再打零到一百分价值分。模型先摘原文证据才打分，只评单维，总分由代码按十个固定权重算。",
    "模型摘要写得再顺也不能直接用，它常记混机构名、算错数字。优质文章置顶后进核验，五条摘要每条先给判断再附原文出处，对不上就打回重做。",
    "有疑问可以直接问这一篇。打开文章跟模型对话，做摘要、核查、数据复核，答案带编号能跳回原文。问出来的结论能打包成上下文跟着文章走，进简报时按需带出。",
    "看完的文章拆成案例：主体、动作、时间、对手方、证据原文，带编号。按机构筛一下，同类动作横着摆，每条都能点回原文。",
    "同一批内容出七种格式：群里信息图，开会报告图或网页，正式汇报 PDF，库存 Markdown，已交付一百零五份。",
    "敏感信息不出内网。五条出站通道收口在业务进程内，敏感字段先换编码，模型只看得到编码，回包再本地还原。漏遮就在出网前拦下，模型一次不调。",
    "演练台已上线生产。贴一段文本，点一次真跑，四栏同时出结果，第三栏是真实模型回包，全程只有编码。",
    "账本每次调用都记：出站明文命中零、写库残留零、还原成功率，一条 SQL 当场复算。网关只认固定词表，这里每步都能现场验。",
    "现在十五路信源、六千七百二十七篇入库、四百三十二条案例覆盖三百五十六家机构。八月十号拿当天五篇真实文章对比，人工三十一点六分钟，系统两分半。",
    "这套东西不只跟同业。采集、评估、案例、简报与主题无关，换信源和关键词就能给别的条线用：对公客户经理盯产业政策，消保团队盯罚单，零售产品经理盯竞品改版。",
    "机器这一圈：清洗、价值打分、脱敏、定时任务，改规则先拿考卷跑一遍，指标不降才换线上。人这一圈：早上看简报、会上拿案例判断、回头补关键词。两个圈靠同一批简报对齐。",
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
