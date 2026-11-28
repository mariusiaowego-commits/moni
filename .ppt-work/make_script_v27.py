# -*- coding: utf-8 -*-
"""从 pptx 的 notes 生成 v27 讲稿 markdown（11 页）"""
from pptx import Presentation

BASE = "/Users/mt16/.codex/worktrees/32b5/moni/.ppt-work"
prs = Presentation(BASE + "/决赛汇报-v27.pptx")

TITLES = [
    "背景 · 为什么需要定期跟踪同业",
    "需求 · 从信息获取到简报分享闭环",
    "内容预处理 · 信源自动入库、提前过滤",
    "价值评估 · 先打分排优先级，再提炼和核实",
    "事实核查 · 提取摘要、联网核查",
    "深度阅读（分支）· 单篇原文追问",
    "提取案例 · 拆成能横向比的案例",
    "分享简报 · 8 种格式按场景分发",
    "隐私保障 · 敏感信息先脱敏，再交给大模型",
    "成果与收益 · 12 倍实测与跟进口径",
    "总结 · 机器自动处理，人专注判断",
]
DEMO = {6: 12}

def mmss(v):
    total = int(round(v))
    return "%d:%02d" % (total // 60, total % 60)

rows = []
t = 0.0
speech = 0
demo_total = 0
for i, s in enumerate(prs.slides, 1):
    note = s.notes_slide.notes_text_frame.text.strip()
    n = len(note)
    speech += n
    sec = n / 200.0 * 60
    d = DEMO.get(i, 0)
    demo_total += d
    rows.append((i, TITLES[i - 1], n, sec, d, t, t + sec + d))
    t += sec + d

out = []
out.append("---")
out.append("type: draft")
out.append("contest: 决赛汇报PPT")
out.append("version: v27-11页")
out.append("duration_min: 6")
out.append("date: 2026-11-28")
out.append("---")
out.append("")
out.append("# 决赛汇报 v27 讲稿（11 页）")
out.append("")
out.append("口播 %d 字，约 %s；现场演示 %d 秒；合计 %s，按 200 字/分钟。"
           % (speech, mmss(speech / 200.0 * 60), demo_total, mmss(t)))
out.append("")
out.append("| 页 | 内容 | 字数 | 口播 | 演示 | 起止 |")
out.append("|---|---|---|---|---|---|")
for i, name, n, sec, d, a, b in rows:
    out.append("| %d | %s | %d | %ds | %s | %s–%s |"
               % (i, name, n, round(sec), ("%ds" % d) if d else "—", mmss(a), mmss(b)))
out.append("")
for i, name, n, sec, d, a, b in rows:
    out.append("## P%02d %s" % (i, name))
    out.append("")
    out.append(prs.slides[i - 1].notes_slide.notes_text_frame.text.strip())
    out.append("")
    if d:
        out.append("> [演示] %d 秒" % d)
        out.append("")

out.append("## 口径备忘")
out.append("")
out.append("- 数字口径：活跃信源 15 路；入库文章 6,727 篇；同业案例 432 条；覆盖机构 356 家；高参考案例 137 条；已交付简报 105 份。")
out.append("- 「12 倍」= 同批 5 篇实测 31.6 分钟 ÷ 2.5 分钟（2026-08-10 一次同口径对比），不代表常态吞吐。")
out.append("- 脱敏口径：出站泄漏命中与残留占位符恒 0；现场演示四栏使用真实模型，最长等约 15 秒，兜底用已截图静态图。")
out.append("- P02 标题保留用户手改原文「闭环」一词（项目需求语义），扫描器禁词不适用。")
out.append("- 全片 11 页，不出现厂商名；截图全部来自 moni 自己环境。")
out.append("")
open(BASE + "/52-终稿-v27-11页讲稿.md", "w", encoding="utf-8").write("\n".join(out))
print("done", speech)
