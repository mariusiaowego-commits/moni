# -*- coding: utf-8 -*-
"""生成 Obsidian 逐页预览文档（v20）：一个文档内嵌全部 15 页渲染 PNG。

用法: python3 make_preview_doc_v20.py <pptx> <预览图目录> <输出 md>
图片用 Obsidian embed 语法 ![[...]]，路径相对 vault 根（tqob）。
"""
import sys
from pptx import Presentation

PPTX = sys.argv[1]
IMGDIR = sys.argv[2]          # 相对 vault 根，如 55-workstuff/.../决赛/PPT/v20/预览
OUT = sys.argv[3]

VAULT_REL = "55-workstuff/AI技术应用与创新实践劳动竞赛/决赛/PPT"
VERSION = "v20"
SCRIPT_REL = "26-终稿-v20-15页讲稿.md"

# 每页一句话：这一页在整场汇报里承担什么（不是复述标题）
ROLE = {
    1: "开场交代为什么要做同业追踪：团队职责、盯什么、为什么不能漏；下半页是以前人工做的两条路和它们的代价。",
    2: "给出整场的工作流：五步转一圈，转完一轮回到采集端改口径；下方深色条是跑这条流水线的 moni 工作台六个模块。",
    3: "同一套流程的第二种画法：主线、分支、回流各在哪，用来补上「哪些步骤会回到前面重做」。",
    4: "同一套流程的第三种画法：一张表说清每步谁做、产出什么、发现不对改哪里，给评委一个可对照的参照系。",
    5: "第一张产品实拍：模型先按方向和评分筛一遍，人在列表里挑重点标星，标星的才进下一段。",
    6: "第一环节：十五路信源定时接入、每个渠道按自己的关键词过滤、本机去掉推广尾巴和乱码，这层不调模型。",
    7: "第二环节：三把尺子各管一件事——质量分本地算、通用价值分模型打分、方向分换问法不换权重。",
    8: "第三环节：先给一句总判断，再挂五条带出处的摘要，机构动作数字对不上就打回重做或标待核实。",
    9: "深度阅读是核验环节的分支：单篇可以在阅读器里追问，答案带编号能跳回原文。",
    10: "第四环节：把看完的文章拆成五个字段的案例，能按机构回查、能横向比，核过的才进简报。",
    11: "第五环节：同一批内容出 8 种格式，按场合分发；已交付 105 份，开头带「优先关注」三条。",
    12: "安全机制与现场演示：敏感信息出网前在本机遮蔽，模型只见编码，四栏当场能看，账本可复算。",
    13: "成果与收益：规模数字、十二倍提速、八个业务方向持续输出。",
    14: "可推广：换掉信源和关键词，同一套流水线直接复用给消保合规等行内团队。",
    15: "收尾立意：机器守质量下限，人定采集方向，两个圈各转各的，中间每天来回对一次。",
}

DEMO = {5: 10, 9: 12, 10: 10, 12: 25}

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
out.append("> 源文件：`决赛汇报-%s.pptx`（同目录）　渲染图：`%s/L-01…L-15.png`"
           % (VERSION, IMGDIR))
out.append("")

speech_chars = sum(len(s.notes_slide.notes_text_frame.text.strip()) for s in prs.slides)
speech_sec = speech_chars / 200.0 * 60
total_demo = sum(DEMO.values())
total_sec = speech_sec + total_demo
out.append("口播 %d 字（约 %d:%02d）+ 现场演示 %d 秒 = 约 %d:%02d，按 200 字/分钟。"
           % (speech_chars, int(speech_sec) // 60, int(speech_sec) % 60,
              total_demo, int(total_sec) // 60, int(total_sec) % 60))
out.append("")
out.append("> P02 / P02-b / P02-c 是同一套工作流的三种画法，**上台只用其中一页**。")
out.append("")
out.append("---")
out.append("")

for i, s in enumerate(prs.slides, 1):
    texts = []
    for sh in s.shapes:
        if sh.has_text_frame and sh.text_frame.text.strip():
            texts.append((sh.top / 914400.0, sh.text_frame.text.strip()))
    texts.sort(key=lambda x: x[0])
    kicker = ""
    title = ""
    for top, t in texts:
        if top < 0.5 and "   " in t:
            parts = t.split("   ")
            if len(parts) == 2:
                kicker = parts[1]
                continue
        if 0.7 < top < 0.9 and len(t) > 8:
            title = t
            break
    if not title:
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
out.append("相关：[[%s/v20/决赛汇报-%s.pptx]]　[[%s/v20/%s]]"
           % (VAULT_REL, VERSION, VAULT_REL, SCRIPT_REL))
out.append("")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("wrote", OUT)
