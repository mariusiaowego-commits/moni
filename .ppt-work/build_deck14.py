# -*- coding: utf-8 -*-
"""moni 决赛汇报 PPT v22 精简版（13 页）

V21 第 6 条：每页只留 1–3 个重点，字更少，突出主题。
文案取自 agy 的精简文案表（20261127-124941-v22-copy），口播合计 704 字。
"""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn

BASE = "/Users/mt16/.codex/worktrees/32b5/moni/.ppt-work"
OUT = BASE + "/决赛汇报-v22-精简版.pptx"

FONT = "Heiti SC"
BG = RGBColor(0xF7, 0xF8, 0xFA)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
BLUE = RGBColor(0x00, 0x4B, 0x8D)
BLUE2 = RGBColor(0x11, 0x63, 0xA8)
LIGHT = RGBColor(0xEA, 0xF1, 0xF8)
GOLD = RGBColor(0xA9, 0x76, 0x1F)
LINE = RGBColor(0xD9, 0xE1, 0xEB)
INK = RGBColor(0x12, 0x20, 0x2F)
MUTED = RGBColor(0x5C, 0x6B, 0x7F)

TOTAL = 13
M = 0.90
CW = 11.53


def _ea(run, name):
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = rPr.makeelement(qn(tag), {})
            rPr.append(el)
        el.set("typeface", name)


def tb(s, x, y, w, h, text, size=14, bold=False, color=INK,
       align=PP_ALIGN.LEFT, spacing=1.0, after=0):
    box = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    for i, line in enumerate(text.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        p.space_after = Pt(after)
        r = p.add_run()
        r.text = line
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.color.rgb = color
        r.font.name = FONT
        _ea(r, FONT)
    return box


def shape(s, kind, x, y, w, h, fill=None, line=None, lw=1.0):
    sh = s.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    sh.shadow.inherit = False
    style = sh._element.find(qn("p:style"))
    if style is not None:
        sh._element.remove(style)
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


def new_slide(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    shape(s, MSO_SHAPE.RECTANGLE, 0, 0, 13.333, 7.5, fill=BG)
    return s


def header(s, num, kicker, pageno):
    tb(s, M, 0.62, 6.0, 0.30, "%s   %s" % (num, kicker), 13, True, MUTED)
    shape(s, MSO_SHAPE.RECTANGLE, M, 1.02, 0.90, 0.05, fill=BLUE)
    tb(s, 10.90, 0.64, 1.53, 0.30, "%02d/%d" % (pageno, TOTAL), 12, True, MUTED,
       PP_ALIGN.RIGHT)


def stats(s, items, y=2.30, h=1.36):
    """一排大字指标，最多 3 个。"""
    n = len(items)
    gap = 0.44
    w = (CW - gap * (n - 1)) / n
    for i, (v, lab) in enumerate(items):
        x = M + i * (w + gap)
        if i:
            shape(s, MSO_SHAPE.RECTANGLE, x - gap / 2 - 0.006, y + 0.18, 0.012,
                  h - 0.36, fill=LINE)
        size = 66 if len(v) <= 4 else (52 if len(v) <= 6 else 40)
        tb(s, x, y, w, 1.16, v, size, True, BLUE)
        tb(s, x, y + 1.14, w, 0.32, lab, 14, False, MUTED)


def bullets(s, items, y=4.26):
    for i, t in enumerate(items):
        yy = y + i * 0.66
        shape(s, MSO_SHAPE.RECTANGLE, M, yy + 0.12, 0.10, 0.10, fill=BLUE)
        tb(s, M + 0.30, yy, CW - 0.30, 0.52, t, 20, False, INK)


def takeaway(s, text):
    shape(s, MSO_SHAPE.RECTANGLE, M, 6.04, CW, 0.58, fill=LIGHT)
    tb(s, M + 0.26, 6.15, CW - 0.52, 0.36, text, 16, True, BLUE)


def footer(s, text="moni · 新媒体渠道同业跟踪调研 AI 工作台"):
    shape(s, MSO_SHAPE.RECTANGLE, M, 6.88, CW, 0.012, fill=LINE)
    tb(s, M, 6.98, 9.0, 0.28, text, 11, False, MUTED)


PAGES = [
    ("01", "为什么做", "人工盯盘，费时且留不住",
     [("20", "人时/周，十人团队估算"), ("2", "条路全靠人工")],
     ["十人每周约 20 小时刷公众号、拼周报",
      "临时交办靠熬夜凑材料，发完群里就沉底"]),
    ("02", "工作流", "五步成一圈，每步都能回头改",
     [("5", "步回到同一条流水线")],
     ["采集、打分、核查、存案例、出简报",
      "抓偏了改标签，问偏了改问法，不用重写代码"]),
    ("03", "工作台", "机器先初筛，人只挑重点",
     [("6,727", "篇入库"), ("48", "篇标星进核验")],
     ["按业务方向分栏，原文与入库时间分开记",
      "标星（Pin）之后才进核验池，其余留在库里"]),
    ("04", "内容处理", "15 路信源，本地清洗过滤",
     [("15", "路活跃信源"), ("821", "个发布方")],
     ["按渠道配关键词，黑白名单先挡一轮",
      "本地去推广尾巴和乱码，这一层不调模型"]),
    ("05", "价值评估", "三把尺子分开算，各管一件事",
     [("0–5", "质量分，本地算"), ("0–100", "通用分，十维加权"), ("0–100", "方向分，换问法")],
     ["模型先引原句再打分，引不到最多 3 分",
      "总分由代码按权重算，低分不删，排到后面"]),
    ("06", "检验核验", "先给结论，再附原文",
     [("5", "条摘要，每条带出处"), ("0", "条无出处结论进简报")],
     ["机构和数字对不上就打回重做",
      "仍不过就标待核实，交人工把关"]),
    ("07", "深度阅读", "单篇拿不准，就问到底",
     [("1", "篇为准，不串别的文章")],
     ["答案只依据这一篇，事实与推断分开标",
      "结论带编号，点一下跳回原文段落"]),
    ("08", "提取案例", "长文拆成五字段，按机构横向比",
     [("432", "条案例"), ("358", "条已确认"), ("356", "家机构")],
     ["主体、动作、时间、对手方、原文出处",
      "一条一个编号，核过的才算数"]),
    ("09", "分享简报", "同一批内容，按场合出 8 种格式",
     [("8", "种格式"), ("105", "份已交付")],
     ["群里发信息图，开会用报告图或 PDF",
      "早上跑完，日会直接投"]),
    ("10", "出站防护", "敏感词本地换编码，漏遮直接拦",
     [("5/5", "遮蔽/还原"), ("0", "出站泄漏命中"), ("0", "残留占位符")],
     ["出网前换编码，模型全程不见真名",
      "回包在本地还原，用户看到的仍是明文"]),
    ("11", "成果与收益", "同批 5 篇，2.5 分钟",
     [("12 倍", "31.6 分钟 → 2.5 分钟")],
     ["同批 5 篇真实文章，同一套打分与核验",
      "省下的时间用来读结论、做判断"]),
    ("12", "不止同业", "换信源和词，就能给消保用",
     [("2", "处要改：信源清单、关键词"), ("0", "行代码要改")],
     ["自动抓监管通报与同业罚单",
      "工作台与案例字段照旧"]),
    ("13", "人机协作", "机器守质量，人定方向",
     [("105", "份简报接住 20 人时/周")],
     ["机器改规则先跑考卷，达标才上线",
      "人每天看简报、做判断、补口径"]),
]


TAKE = {'01': '材料留在群里，下次还得从头找', '02': '同一套流程，改配置就能重跑', '03': '人只做判断，不做搬运', '04': '干净正文才进模型', '05': '三把尺子互不替代', '06': '能指回原文的结论才敢用', '07': '问出来的结论进档案', '08': '案例能横向比，才叫资产', '09': '早上跑完，日会就投', '10': '遮不干净就拦下，不出网', '11': '机器省时间，人做判断', '12': '换的是配置，不是系统', '13': '105 份简报接着每周 20 人时'}


def main():
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    for num, kicker, title, st, bl in PAGES:
        s = new_slide(prs)
        header(s, num, kicker, int(num))
        tb(s, M, 1.20, CW, 0.78, title, 34, True, BLUE)
        stats(s, st)
        bullets(s, bl)
        takeaway(s, TAKE[num])
        footer(s)
    prs.save(OUT)

    bad = []
    for i, s in enumerate(prs.slides, 1):
        for sh in s.shapes:
            if sh.left is None:
                continue
            l = sh.left / 914400.0
            t = sh.top / 914400.0
            r = (sh.left + sh.width) / 914400.0
            b = (sh.top + sh.height) / 914400.0
            if l < -0.01 or t < -0.01 or r > 13.35 or b > 7.52:
                bad.append("slide%d L%.2f T%.2f R%.2f B%.2f" % (i, l, t, r, b))
    print("slides:", len(prs.slides._sldIdLst), "out of bounds:", len(bad))
    for x in bad:
        print("  ", x)


main()
