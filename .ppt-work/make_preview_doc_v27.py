# -*- coding: utf-8 -*-
"""生成 v27 逐页预览 markdown"""
import os
BASE = "/Users/mt16/.codex/worktrees/32b5/moni/.ppt-work"
PNG = "/private/tmp/user-v27"
titles = [
    "P01 背景 · 新渠道同业动向、监管政策、行业趋势需要定期跟踪",
    "P02 需求 · 一个能支撑从信息获取到简报分享闭环的工作台",
    "P03 内容预处理 · 信源自动入库，提前过滤",
    "P04 价值评估 · 先打分排优先级，再提炼和核实",
    "P05 事实核查 · 借助 AI 提取摘要、联网事实核查",
    "P06 深度阅读 · 对一篇内容原文详细追问",
    "P07 提取案例 · 看完的文章，拆成能横向比的案例",
    "P08 分享简报 · 多格式分享，灵活支持不同场景",
    "P09 隐私保障 · 敏感信息先脱敏，再交给大模型",
    "P10 成果与收益 · AI 辅助与人工跟踪对比实测",
    "P11 总结 · AI 自动处理+高质量交付，人专注判断+高效决策",
]
lines = ["---", "type: draft", "contest: 决赛汇报PPT", "version: v27-11页",
         "date: 2026-11-28", "---", "", "# 决赛汇报 v27 逐页预览", ""]
for i, t in enumerate(titles, 1):
    p = os.path.join(PNG, "L-%02d.png" % i)
    lines.append("## %s" % t)
    lines.append("")
    lines.append("![[%s]]" % p)
    lines.append("")
open(BASE + "/53-逐页预览-v27.md", "w", encoding="utf-8").write("\n".join(lines))
print("done")
