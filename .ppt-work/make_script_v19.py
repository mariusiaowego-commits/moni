# -*- coding: utf-8 -*-
"""从 pptx 的 notes 生成 v19 讲稿 markdown（含时长核算）"""
from pptx import Presentation

BASE = "/Users/mt16/.codex/worktrees/32b5/moni/.ppt-work"
prs = Presentation(BASE + "/决赛汇报-v19.pptx")

TITLES = ["为什么做 · 两条路径", "工作流 · 五步一圈", "工作台 · 真实界面", "内容处理",
          "价值评估", "检验核验", "深度阅读（分支）", "提取案例", "分享简报",
          "出站防护 · 机制", "演练台 · 四栏", "出站防护 · 可证伪", "成果与收益",
          "不止同业 · 消保合规", "人机协作"]
DEMO = {3: 10, 4: 8, 7: 12, 11: 25}

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
out.append("version: v19-15页")
out.append("duration_min: 6")
out.append("date: 2026-11-26")
out.append("---")
out.append("")
out.append("# 决赛汇报 v19 讲稿（15 页）")
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
out.append("- P13 右侧八个方向的括号数字 = 26 份已交付简报 PDF 文本层的行命中计数（pdftotext 逐行 grep），是方向性参考而非篇数。已核实：零售 26、客户经营 9、渠道建设 0；跨��支付、消费金融、养老金融、理财命中 0 或太弱，已按用户要求删除。")
out.append("- 模型打分：三个分——质量分 `pin_heuristic_v3` 0–5 一位小数（本地规则，不调模型）；通用业务价值分 `bv_rubric_v1` 十维加权 0–100（模型打分，只在生成简报时自动跑）；业务方向分（换问法不换权重，线上当前 1 个方向「微信生态」）。总分由代码 `round(Σ(score/10×weight))` 算，模型永远不给总分。")
out.append("- 十维权重（合计 100）：规则约束 14 / 同业动作 12 / 产品规则 10 / 系统实现 8 / 客户杠杆 10 / 资金路径 10 / 下行损失 10 / 适用对象 8 / 决策窗口 8 / 落地门槛 10。")
out.append("- P10/P11/P12 脱敏：内核已上线（PR #294 / v1.0.127），演练台已上线生产（PR #305 / v1.0.128），但脱敏总开关默认关，生产库 `desensitize_events` 当前 0 行，页面已如实标注「内核已上线 · 开关默认关」。")
out.append("- P11 四栏为 2026-11-25 真实模型调用原文：遮蔽 5 / 还原 5 / 未还原 0 / 出站泄漏命中 0 / 残留占位符 0 / 遮蔽 136 µs / 还原 98 µs / 模型 8775 ms。示例手机号与邮箱为演练台测试样本的虚构值。")
out.append("- P11 三项指标口径：出站泄漏命中 `sum(leak_scan_hits)` 恒 0；残留占位符 `sum(residual_placeholders)` 恒 0；精确还原成功率 = `sum(restored_total) ÷ (sum(restored_total) + sum(unrestored_count))`。")
out.append("- P11 截图来自 moni 自己的演练台真实调用，不使用任何第三方产品素材。")
out.append("- 全片不出现模型厂商名；P12 左栏用「通用密钥型脱敏」作中性对照。")
out.append("")
out.append("## 待处理的生产问题（影响现场演示，需授权后修）")
out.append("")
out.append("- **生产 8900 的 `/api/cases` 返回 500**，案例库页面因此显示「暂无案例」。证据：`/private/tmp/moni-8900-v1.0.52.log` 末尾 `sqlite3.OperationalError: no such column: c.case_action_source`，抛在 `moni/cases/repository.py:678 list_cases`。")
out.append("- 成因：`moni/db/migrate.py` 的 `_CURRENT_VERSION = 68`，而生产库 `schema_version` 最大值是 **61**，缺 062–068 共 7 个迁移；`cases.case_action_source` 由 `moni/db/migrations/062_case_metrics.sql` 引入。")
out.append("- 影响：决赛若现场演示「案例库」页会直接失败。修复动作是执行生产库迁移（`moni db init` / migrate 到 68），属于生产状态变更，**需当轮明确授权后执行**。")
out.append("- P08 用的是 2026-11-25 生产环境 8900 的案例库截图（`docs/moni-screenshots-verify-moni-20261125/page_cases.png`），当时案例数为 432，与今天库里的 432 一致；页面图注已标注截图日期。")
out.append("- 截图来源说明：P03/P04 用 2026-11-26 当天在生产 8900 抓的工作台、信源、关键词过滤、数据清洗界面；P08 用 11-25 归档；P09 用 `data/artifacts` 里的实际交付简报成品；P11 用 moni 自己的演练台实拍。")
out.append("")
out.append("## 演示节奏（现场）")
out.append("")
out.append("- P03 工作台：10 秒，停在文章列表页，指出分桶、标签、Pin。")
out.append("- P04 内容处理：8 秒，快切信源管理 / 关键词过滤 / 数据清洗三张图。")
out.append("- P07 深度阅读：12 秒，打开详情页发一条追问，指出结论编号可跳回原文。")
out.append("- P11 演练台：25 秒，点「真跑一次」等 8–15 秒，依次划②③栏，指④栏和两个零。兜底图 `/private/tmp/desens-lab-dev/03-run-1600.png`。")
out.append("- 兜底：网络抖动时，演练台直接切静态图；其余页为静态页无风险。")
out.append("")

path = BASE + "/24-终稿-v19-15页讲稿.md"
with open(path, "w") as f:
    f.write("\n".join(out) + "\n")
print("wrote", path)
print("speech %d chars, demo %ds, total %s" % (speech, demo_total, mmss(t)))
