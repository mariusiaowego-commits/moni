## Output format: Rich Text (structured Markdown)

Render the analyzed articles as structured Markdown designed for 
pasting into rich text editors (WeChat public account editor, Notion, 
Feishu, Lark, Yuque, etc.).

### Document structure

```
# 行业追踪 — YYYY-MM-DD
*N 篇文章 · from moni*

---

## 文章标题

`tag1` `tag2`  |  来源名称 · YYYY-MM-DD

> **摘要** *(AI 生成)*
> 
> 3-5 句摘要内容，直接进入主题，不绕弯。

**事实核查**
- ✅ 支持 — "原文声称 XXX" — 核实结果与简要说明
- ⚠️ 待验证 — "原文声称 YYY" — 无法确认的原因
- ❌ 存疑 — "原文声称 ZZZ" — 矛盾或错误在哪里

🔗 [阅读原文](https://original-url)

---

## 第二篇文章标题

...
```

### Rules
- **Abstract badge**: 
  - `*(原文)*` — publisher-provided abstract
  - `*(DB)*` — already summarized in moni
  - `*(AI 生成)*` — freshly generated — most important to label honestly
- **Fact check**: omit the entire "事实核查" block if no checkable claims exist.
- **Tags**: inline code format `` `tag` `` — renders as colored label in most editors.
- **Link**: always use `🔗 [阅读原文](url)` format. One link per article.
- **Separator**: `---` between articles.
- **Line width**: keep under 120 chars per line. Rich text editors often have narrow 
  editing panes.
- **Tone**: factual, no hype. No "重磅" "炸裂" "必读" — let the content speak.
- **No HTML wrapping**: pure Markdown. The target editor handles rendering.
