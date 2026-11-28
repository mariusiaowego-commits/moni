# -*- coding: utf-8 -*-
"""校验 v10：越界、禁词、每页字数、讲稿时长"""
import sys
from pptx import Presentation
from pptx.util import Emu

PATH = sys.argv[1] if len(sys.argv) > 1 else "/Users/mt16/.codex/worktrees/32b5/moni/.ppt-work/决赛汇报-v15.pptx"
BANNED = ["赋能", "抓手", "闭环", "打造", "底座", "兜底", "固化", "契机",
          "护城河", "飞轮", "全链路", "而是", "真正", "打通"]
VENDORS = ["Google", "Gemini", "Hermes", "hermes", "magpie", "阿里", "通义", "千问", "OpenAI"]

prs = Presentation(PATH)
W = prs.slide_width / 914400.0
H = prs.slide_height / 914400.0
oob = []
speech = 0
print("canvas %.3f x %.3f  slides %d" % (W, H, len(prs.slides._sldIdLst)))
for i, s in enumerate(prs.slides, 1):
    texts = []
    for sh in s.shapes:
        if sh.left is None:
            continue
        l, t = sh.left / 914400.0, sh.top / 914400.0
        r, b = l + sh.width / 914400.0, t + sh.height / 914400.0
        if l < -0.01 or t < -0.01 or r > W + 0.02 or b > H + 0.02:
            oob.append("P%02d %s L%.2f T%.2f R%.2f B%.2f" % (i, sh.shape_type, l, t, r, b))
        if sh.has_text_frame and sh.text_frame.text.strip():
            texts.append(sh.text_frame.text)
    body = "".join(texts)
    note = s.notes_slide.notes_text_frame.text.strip()
    n = len(note)
    speech += n
    hits = [w for w in BANNED if w in body]
    vh = [w for w in VENDORS if w in body]
    print("P%02d  shapes=%2d  onslide=%3d  notes=%3d (%4.1fs)  %s%s"
          % (i, len(s.shapes), len(body), n, n / 200.0 * 60,
             ("BANNED:" + ",".join(hits) + " ") if hits else "",
             ("VENDOR:" + ",".join(vh)) if vh else ""))
print("out-of-bounds: %d" % len(oob))
for x in oob:
    print("  ", x)
print("speech total %d chars = %.1fs (%.2f min) at 200 chars/min" % (speech, speech / 200.0 * 60, speech / 200.0))
