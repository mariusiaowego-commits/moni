## Output format: 同业简报 → PDF（Markdown 中介）

系统会把你的 **Markdown 全文** 转成 PDF。请输出完整 Markdown（与 brief markdown 结构相同），不要只写元叙述。

### 结构（同 brief markdown）
```
# 同业简报 — YYYY-MM-DD
*from moni by TQ · N 篇文章 · 按业务价值排序*

## 优先关注
1. …

## 1. 标题
**来源**: 发布方（采集通道） · **日期**: YYYY-MM-DD
**质量/注意力分**: XX/100
**业务价值**: XX/100 — …
**摘要**: …
**事实核查**: … 【已核实】/【待核实】/【存疑】（禁止英文标签）
[阅读原文 →](url)
```

### 规则
- 来源优先 **发布方**，通道放括号
- 日期必须有（缺失写「日期不详」）
- 质量分必须有数字
- 文末：`*Curated by moni — a TQ project.*`
- 全文输出到 stdout
