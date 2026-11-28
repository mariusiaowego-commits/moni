# -*- coding: utf-8 -*-
"""从 pptx 的 notes 生成 v26 讲稿 markdown（14 页）"""
from pptx import Presentation

BASE = "/Users/mt16/.codex/worktrees/32b5/moni/.ppt-work"
prs = Presentation(BASE + "/决赛汇报-v26.pptx")

TITLES = [
    "为什么做 · 人工盯盘的代价",
    "工作流 · 五步一圈",
    "工作台 · 模型先筛、人再挑",
    "内容处理 · 十五路信源本地过滤",
    "价值评估 · 三套评分口径与判定链路",
    "检验核验 · 先结论后摘要",
    "深度阅读（分支）· 单篇问答",
    "提取案例 · 五个字段",
    "分享简报 · 八种格式",
    "隐私保障 · 脱敏机制",
    "成果与收益 · 十二倍与八个方向",
    "可推广 · 复用给行内其它团队",
    "人机协作 · 两个圈",
    "收尾 · 材料省下来之后",
]

# 现场演示页：P03 工作台 10s / P07 深度阅读 12s / P08 案例库 10s / P10 脱敏 25s
DEMO = {3: 10, 7: 12, 8: 10, 10: 25}

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


def mmss(v):
    total = int(round(v))
    return "%d:%02d" % (total // 60, total % 60)


out = []
out.append("---")
out.append("type: draft")
out.append("contest: 决赛汇报PPT")
out.append("version: v26-14页")
out.append("duration_min: 6")
out.append("date: 2026-11-27")
out.append("---")
out.append("")
out.append("# 决赛汇报 v26 讲稿（14 页）")
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
out.append("- 数字口径（2026-11-26 直接查 `data/news.db` 只读复核）：入库文章 6,727 篇；同业案例 432 条（confirmed 358 / pending 73 / rejected 1）；案例主体去重后覆盖 356 家机构（subject_raw 去重 353）；高参考 137 条；信源 18 路 / 活跃 15 路；文章发布方去重 821 个；分享任务 126 个、done 105 个。")
out.append("- 「十二倍」= 同批 5 篇实测 31.6 分钟 ÷ 2.5 分钟（2026-08-10 一次同口径对比），不代表常态吞吐。")
out.append("- 通用业务价值分（量表 `bv_rubric_v1`，0–100）：实现见 `moni/scoring/rubric.py`（十维与权重、判定锚点）与 `moni/scoring/bv_pipeline.py`（判定链路）。")
out.append("- 十维与权重（合计 100）：规则约束 14 / 同业动作 12 / 产品规则 10 / 系统实现 8 / 客户杠杆 10 / 资金路径 10 / 下行损失 10 / 适用对象 8 / 决策窗口 8 / 落地门槛 10。每维给 0–10 整数分，配 0 / 3 / 6 / 10 四档锚点。")
out.append("- 判定链路：取证（引文须为正文子串且不少于 12 字，有效引文最多 8 条，不足 3 条补证一次；长文按不超过 3000 字的窗口取样，最多 4 窗）→ 十维打分（0–10 整数 + 引文编号 + 理由）→ 校验（分数非整数、维度缺失、引文无效一律退回；得分大于 0 但未绑定有效引文的维度封顶 3 分；理由含禁用词或『不是 X 而是 Y』句式即退回）→ 独立审计（另一角色只依据十维打分与引文复核，未通过按意见重新评分）→ 总分由程序计算 `round(Σ(维度分 ÷ 10 × 权重))`，钳制 0–100，模型不提供总分。")
out.append("- 门槛与预算：正文少于 200 字不打分（insufficient）；单篇预算 300 秒、单次调用 180 秒；结果状态为通过（ready）或待复核（needs_review）。")
out.append("- 触发时机：生成简报时自动运行（`include_business_value` 默认开启），没有独立按钮。生产库中通用业务价值分仅少量文章有分，不写成『已给全部文章打分』；业务方向分线上当前只有 1 个方向（微信生态）。")
out.append("- 备用页：`备用页-业务价值分十维量表.pptx`，十维、权重、判定问题与 10 分锚点均直接取自 `moni/scoring/rubric.py`，用于评委追问评分细节时翻出。")
out.append("- 模型打分：三个分——质量分 `pin_heuristic_v3` 0–5 一位小数（本地规则，不调模型）；通用业务价值分 `bv_rubric_v1` 十维加权 0–100（模型打分，只在生成简报时自动跑）；业务方向分（换问法不换权重，线上当前 1 个方向「微信生态」）。总分由代码 `round(Σ(score/10×weight))` 算，模型永远不给总分。")
out.append("- 十维权重（合计 100）：规则约束 14 / 同业动作 12 / 产品规则 10 / 系统实现 8 / 客户杠杆 10 / 资金路径 10 / 下行损失 10 / 适用对象 8 / 决策窗口 8 / 落地门槛 10。")
out.append("- 脱敏：内核已上线（PR #294 / v1.0.127），演练台已上线生产（PR #305 / v1.0.128）。自 v1.0.138 起配置与实验室合并到主站「配置管理 → 脱敏」同一页签，旧链接自动重定向；按钮文案为「载入示例 / 本地预览 / 模型实测」。")
out.append("- P10 脱敏页指标口径：出站泄漏命中 `sum(leak_scan_hits)` 恒 0；残留占位符 `sum(residual_placeholders)` 恒 0；精确还原成功率 = `sum(restored_total) ÷ (sum(restored_total) + sum(unrestored_count))`。2026-11-25 实测：遮蔽 5 / 还原 5 / 未还原 0 / 出站泄漏命中 0 / 残留占位符 0 / 遮蔽 136 µs / 还原 98 µs / 模型 8775 ms。")
out.append("- P03 截图里的实时总数（库里在持续增长）与正文固定口径不一致，已在素材上抹掉该数字，只保留方向标签桶；全片只出现 6,727 这一个入库篇数口径。")
out.append("- 截图全部来自 moni 自己的生产环境，不使用任何第三方产品素材。")
out.append("- 全片不出现模型厂商名；脱敏对照用「通用密钥型脱敏」作中性表述。")
out.append("")
out.append("## 备用页（不在正片，评委追问时翻）")
out.append("")
out.append("- 备选 A「同一套流程换一种画法：主线、分支和回流各在哪」：mermaid 分支与回流图，看点是哪些步骤会回到前面重做。")
out.append("- 备选 B「用一张表说清：每步谁做、产出什么、发现不对改哪里」：五列表格，看点是每步的责任与返工入口。")
out.append("- 两页成片在同目录 `备用页/`，只有评委说「流程再讲一遍」时才翻。")
out.append("")
out.append("## 演示节奏（现场）")
out.append("")
out.append("- P03 工作台：10 秒，停在文章列表页，指出方向标签桶、原文时间与系统同步时间、命中标签列、Pin 标星。")
out.append("- P07 深度阅读：12 秒，打开详情页发一条追问，指出结论编号可跳回原文。")
out.append("- P08 案例库：10 秒，展示两条已核实案例卡片，指「核验通过 / 驳回」两个状态。")
out.append("- P10 脱敏：25 秒，进「配置管理 → 脱敏」，点「模型实测」等 8–15 秒，依次划四栏，指两个零。")
out.append("- 保底：网络抖动时切静态图；其余页为静态页无风险。")
out.append("")

path = BASE + "/49-终稿-v26-14页讲稿.md"
with open(path, "w") as f:
    f.write("\n".join(out) + "\n")
print("wrote", path)
print("speech %d chars, demo %ds, total %s" % (speech, demo_total, mmss(t)))
