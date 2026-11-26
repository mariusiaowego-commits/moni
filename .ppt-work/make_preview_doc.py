# -*- coding: utf-8 -*-
"""生成 Obsidian 逐页预览文档：15 页 PPT 渲染图 + 页标题 + 口播。

用法: python3 make_preview_doc.py <pptx> <预览图目录> <输出 md>
图片用 Obsidian embed 语法 ![[...]]，路径相对 vault 根（tqob）。
"""
import sys
from pptx import Presentation

PPTX = sys.argv[1]
IMGDIR = sys.argv[2]          # 相对 vault 根的路径，如 55-workstuff/.../PPT/预览-v18
OUT = sys.argv[3]

VAULT_REL = "55-workstuff/AI技术应用与创新实践劳动竞赛/决赛/PPT"
VERSION = sys.argv[4] if len(sys.argv) > 4 else "v18"
SCRIPT_REL = sys.argv[5] if len(sys.argv) > 5 else "23-终稿-v18-15页讲稿.md"

# 每页一句话：这一页在整场汇报里承担什么（不是复述标题）
ROLE = {
    1: "交代为什么必须有同业追踪：职责、盯什么、为什么不能漏；下半页是以前人工做的两条路和它们的代价。",
    2: "给出整场的工作流：五步转一圈，转完一轮回到采集端改口径；moni 工作台是跑这条流水线的那一层。",
    3: "第一张产品实拍，让评委看到这是个能用的工作台，不是一个示意图。",
    4: "第一环节：信源自动接入、规则过滤、去噪清洗，三步都在本机跑，不调模型。",
    5: "第二环节：两套评分分开算，模型必须先摘出原文证据再打分，总分由程序算。",
    6: "第三环节：好文章置顶后逐条核验，机构、动作、数字三项错了直接打回。",
    7: "深度阅读是第三环节的分支：单篇可以直接提问，答案带编号能跳回原文。",
    8: "第四环节：把看完的文章拆成结构化案例，能按机构回查、能横向比。",
    9: "第五环节：同一批内容出 8 种格式，按场合分发；已交付 105 份。",
    10: "安全机制：敏感信息出网前在本机遮蔽，模型只见编码不见真名。",
    11: "现场演示页：同一个页面里从开关到验证一步不跳，四栏并排，模型回话里也只有编码。",
    12: "把「我说安全」变成「当场可复算」：三项指标 + 一条现场就能跑的 SQL。",
    13: "成果与收益：规模、提速十二倍、八个业务方向持续输出。",
    14: "可推广：换掉信源和关键词，同一套流水线直接复用给消保与合规团队。",
    15: "收尾立意：机器守质量下限，人决定采集方向，两个圈各转各的。",
}

DEMO = {3: 10, 4: 8, 7: 12, 11: 25}

prs = Presentation(PPTX)

out = []
out.append("---")
out.append("type: draft")
out.append("contest: 决赛汇报PPT")
out.append("version: %s-逐页预览" % VERSION)
out.append("duration_min: 6")
out.append("date: 2026-11-26")
out.append("---")
out.append("")
out.append("# 决赛汇报 %s · 逐页预览（15 页）" % VERSION)
out.append("")
out.append("> 源文件：`决赛汇报-%s.pptx`（Obsidian 同目录）　渲染：`%s/L-01…15.png`"
           % (VERSION, IMGDIR))
out.append("")

total_sec = 0.0
total_demo = 0
for i, s in enumerate(prs.slides, 1):
    note = s.notes_slide.notes_text_frame.text.strip()
    sec = len(note) / 200.0 * 60
    d = DEMO.get(i, 0)
    total_sec += sec
    total_demo += d
    total_sec += d

out.append("口播 %d 字（约 %d:%02d）+ 现场演示 %d 秒 = 约 %d:%02d，按 200 字/分钟。"
           % (sum(len(s.notes_slide.notes_text_frame.text.strip()) for s in prs.slides),
              int(sum(len(s.notes_slide.notes_text_frame.text.strip()) for s in prs.slides) / 200.0 * 60) // 60,
              int(sum(len(s.notes_slide.notes_text_frame.text.strip()) for s in prs.slides) / 200.0 * 60) % 60,
              total_demo, int(total_sec) // 60, int(total_sec) % 60))
out.append("")
out.append("---")
out.append("")

for i, s in enumerate(prs.slides, 1):
    # 页眉第一行 "NN  kicker"，第二行大标题
    texts = []
    for sh in s.shapes:
        if sh.has_text_frame and sh.text_frame.text.strip():
            texts.append((sh.top / 914400.0, sh.text_frame.text.strip()))
    texts.sort(key=lambda x: x[0])
    kicker = ""
    title = ""
    for top, t in texts:
        if top < 0.5 and "　" not in t and "  " in t:
            parts = t.split("   ")
            if len(parts) == 2:
                kicker = parts[1]
                continue
        if 0.7 < top < 0.9 and len(t) > 8:
            title = t
            break
    if not title:
        # 兜底：用标题字号最大的那个文本框
        best = None
        for sh in s.shapes:
            if not sh.has_text_frame:
                continue
            sizes = [r.font.size.pt for p in sh.text_frame.paragraphs for r in p.runs if r.font.size]
            if not sizes:
                continue
            if best is None or max(sizes) > best[0]:
                best = (max(sizes), sh.text_frame.text.strip())
        title = best[1] if best else ""

    note = s.notes_slide.notes_text_frame.text.strip()
    sec = len(note) / 200.0 * 60
    d = DEMO.get(i, 0)
    tag = "（含现场演示 %d 秒）" % d if d else ""

    out.append("## P%02d · %s" % (i, kicker if kicker else title))
    out.append("")
    out.append("**%s**" % title)
    out.append("")
    out.append("![[%s/L-%02d.png]]" % (IMGDIR, i))
    out.append("")
    out.append("- **这一页干什么**：%s" % ROLE.get(i, ""))
    out.append("- **口播**：%d 字，约 %d 秒%s" % (len(note), round(sec), tag))
    out.append("")
    out.append("> %s" % note)
    out.append("")

out.append("---")
out.append("")
out.append("相关：[[%s/决赛汇报-%s.pptx]]　[[%s/%s]]"
           % (VAULT_REL, VERSION, VAULT_REL, SCRIPT_REL))
out.append("")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("wrote", OUT)

