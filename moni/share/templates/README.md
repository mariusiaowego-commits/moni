# 版式模板说明

本目录存放各格式的版式配置和模板。

## PDF 日报 (`pdf-style.yaml`)

修改 `pdf-style.yaml` 即可调整 PDF 外观，无需改代码。

### 常见修改

**改颜色**：
```yaml
colors:
  primary: "#1a365d"  # 改成你想要的颜色
  accent: "#d97706"
```

**改字号**：
```yaml
article:
  title_font_size: 20  # 文章标题字号
  body_font_size: 12   # 正文字号
```

**改间距**：
```yaml
page:
  margin_top: 3cm  # 页面边距

header:
  padding_top: 40  # 标题区内边距
```

**改 Tag 样式**：
```yaml
tag:
  max_display: 8     # 最多显示 8 个 tag
  radius: 5          # 更大圆角
  bg_color: "#e0e7ff" # 改成蓝色背景
```

### 高级修改

如果需要改布局结构（如添加页眉页脚、修改文章卡片样式），需要改 `scripts/generate_pdf.py`。

## 信息图 PNG

信息图 PNG 的版式由 skill 控制（baoyu-infographic / kami / ljg-card）。

如需自定义：
1. 使用 `direct` approach
2. 修改 `moni/share/prompts/format-info-graphic-png.md` 中的 prompt

## 其他格式

各格式的 prompt 在 `moni/share/prompts/` 目录：

| 格式 | Prompt 文件 | 说明 |
|------|-------------|------|
| PDF 日报 | `format-pdf.md` | LLM 输出 Markdown，worker 转 PDF |
| 信息图 PNG | `format-info-graphic-png.md` | LLM 调用 image_generate |
| 报告 PNG | `format-report-png.md` | LLM 调用 image_generate |
| 富媒体文本 | `format-rich-text.md` | LLM 输出 Markdown |
| HTML 网页 | `format-html.md` | LLM 输出 HTML |
| Markdown | `format-markdown.md` | LLM 输出 Markdown |

修改 prompt 即可改变 LLM 输出的内容结构。

## 工作流

1. **改颜色/字号/间距** → 修改 `pdf-style.yaml`
2. **改内容结构** → 修改对应的 `format-*.md` prompt
3. **改布局/添加新元素** → 修改 `scripts/generate_pdf.py`
4. **改信息图风格** → 切换 skill 或修改 prompt
